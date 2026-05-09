from __future__ import annotations

import sys
import tempfile
import unittest
import importlib.util
from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from analysis_app_config import DEFAULT_METRICS as CONFIG_DEFAULT_METRICS, default_calibration_points, should_count_calibration_point
from analysis_detection_filters import filter_static_ball_false_positives, is_likely_opponent_goalkeeper
from analysis_metrics import DEFAULT_METRICS, metric_definition, metric_status_rows
from analysis_report_runs import latest_report_record, write_latest_report

serve_app_spec = importlib.util.spec_from_file_location("serve_analysis_app", PROJECT_ROOT / "scripts" / "12_serve_analysis_app.py")
serve_analysis_app = importlib.util.module_from_spec(serve_app_spec)
assert serve_app_spec and serve_app_spec.loader
serve_app_spec.loader.exec_module(serve_analysis_app)


def base_config(output_dir: Path | None = None, review_dir: Path | None = None) -> dict:
    return {
        "field": {"length_m": 40.0, "width_m": 20.0},
        "match": {
            "output_dir": str(output_dir or PROJECT_ROOT / "tmp_reports"),
            "review_dir": str(review_dir or PROJECT_ROOT / "tmp_review"),
        },
        "calibration": {},
        "teams": {
            "analyze_team": "red",
            "red": {
                "kit": {
                    "field_colors": ["pink_red"],
                    "goalkeeper_colors": ["yellow"],
                }
            },
        },
        "detection": {
            "ball_filter": {
                "static_false_positive": {
                    "enabled": True,
                    "min_frames": 4,
                    "min_duration_s": 1.5,
                    "max_span_m": 0.45,
                    "marked_static_field": {"min_frames": 2, "max_span_m": 0.8},
                }
            },
            "visible_area_filter": {"enabled": True},
            "identity_filter": {
                "opponent_goalkeeper": {
                    "enabled": True,
                    "opponent_goal_zone_m": 4.5,
                    "goal_y_margin_m": 4.2,
                    "min_frames": 6,
                    "max_span_x_m": 4.0,
                    "max_span_y_m": 7.0,
                    "strict_stationary_span_x_m": 1.8,
                    "strict_stationary_span_y_m": 3.0,
                }
            },
        },
    }


class AnalysisCoreTest(unittest.TestCase):
    def test_metric_definitions_are_shared(self) -> None:
        self.assertEqual(CONFIG_DEFAULT_METRICS, DEFAULT_METRICS)
        self.assertEqual(metric_definition("pass")["label"], "传球")
        rows = metric_status_rows(["pass", "observed_coverage"])
        self.assertEqual([row["指标"] for row in rows], ["传球", "观察覆盖率"])

    def test_default_calibration_points_include_optional_marks(self) -> None:
        points = default_calibration_points(40.0, 20.0)["points"]
        by_name = {point["name"]: point for point in points}
        self.assertEqual(by_name["left_penalty_mark"]["kind"], "static_field_mark")
        self.assertFalse(by_name["visible_area_bottom_left"]["use_for_homography"])
        self.assertFalse(by_name["bottom_left_corner"]["required"])
        self.assertTrue(should_count_calibration_point(by_name["left_penalty_mark"]))
        self.assertFalse(should_count_calibration_point(by_name["visible_area_top_left"]))

    def test_review_item_time_key_sorts_chronologically(self) -> None:
        items = [
            {"review_id": "b", "timestamp_sec": 12.0, "frame_idx": 360},
            {"review_id": "a", "timestamp_sec": 8.0, "frame_idx": 240},
            {"review_id": "c", "timestamp_sec": 12.0, "frame_idx": 300},
        ]
        ordered = sorted(items, key=serve_analysis_app.review_item_time_key)
        self.assertEqual([item["review_id"] for item in ordered], ["a", "c", "b"])

    def test_static_ball_filter_removes_stationary_field_mark(self) -> None:
        rows = [
            {
                "class_name": "sports ball",
                "track_id": 7,
                "timestamp_sec": index * 0.5,
                "field_x_m": 6.0 + index * 0.01,
                "field_y_m": 10.0,
                "inside_play_area": True,
            }
            for index in range(5)
        ]
        rows.append({"class_name": "person", "track_id": 3, "inside_play_area": True})
        filtered, summary = filter_static_ball_false_positives(rows, base_config())
        self.assertEqual(summary["removed_rows"], 5)
        self.assertEqual([row["class_name"] for row in filtered], ["person"])

    def test_static_ball_filter_keeps_moving_ball(self) -> None:
        rows = [
            {
                "class_name": "sports ball",
                "track_id": 7,
                "timestamp_sec": index * 0.5,
                "field_x_m": 6.0 + index * 0.4,
                "field_y_m": 10.0,
                "inside_play_area": True,
            }
            for index in range(5)
        ]
        filtered, summary = filter_static_ball_false_positives(rows, base_config())
        self.assertEqual(summary["removed_rows"], 0)
        self.assertEqual(len(filtered), 5)

    def test_marked_static_field_spot_can_filter_short_false_positive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            points_path = Path(tmp) / "calibration_points.yaml"
            points_yaml = default_calibration_points(40.0, 20.0)
            for point in points_yaml["points"]:
                if point["name"] == "left_penalty_mark":
                    point["image_xy"] = [100, 120]
            points_path.write_text(yaml.safe_dump(points_yaml, allow_unicode=True, sort_keys=False), encoding="utf-8")
            config = base_config()
            config["calibration"]["points_path"] = str(points_path)
            rows = [
                {
                    "class_name": "sports ball",
                    "track_id": 7,
                    "timestamp_sec": index * 0.5,
                    "field_x_m": 6.05,
                    "field_y_m": 10.02,
                    "anchor_x": 102,
                    "anchor_y": 121,
                    "inside_play_area": True,
                }
                for index in range(2)
            ]
            filtered, summary = filter_static_ball_false_positives(rows, config)
            self.assertEqual(summary["removed_rows"], 2)
            self.assertEqual(filtered, [])

    def test_visible_area_polygon_filters_outside_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            points_path = Path(tmp) / "calibration_points.yaml"
            points_yaml = default_calibration_points(40.0, 20.0)
            polygon = {
                "visible_area_top_left": [0, 0],
                "visible_area_top_right": [200, 0],
                "visible_area_bottom_right": [200, 200],
                "visible_area_bottom_left": [0, 200],
            }
            for point in points_yaml["points"]:
                if point["name"] in polygon:
                    point["image_xy"] = polygon[point["name"]]
            points_path.write_text(yaml.safe_dump(points_yaml, allow_unicode=True, sort_keys=False), encoding="utf-8")
            config = base_config()
            config["calibration"]["points_path"] = str(points_path)
            rows = [
                {"class_name": "person", "track_id": 1, "anchor_x": 50, "anchor_y": 50, "inside_play_area": True},
                {"class_name": "person", "track_id": 2, "anchor_x": 250, "anchor_y": 50, "inside_play_area": True},
            ]
            filtered, summary = filter_static_ball_false_positives(rows, config)
            self.assertEqual(len(filtered), 1)
            self.assertEqual(filtered[0]["track_id"], 1)
            self.assertEqual(summary["visible_area"]["removed_rows"], 1)

    def test_opponent_goalkeeper_filter_flags_far_goal_keeper(self) -> None:
        row = {
            "mean_x": 37.5,
            "mean_y": 10.1,
            "frames": 12,
            "span_x_m": 1.2,
            "span_y_m": 2.0,
            "primary_color": "yellow",
        }
        flagged, reason = is_likely_opponent_goalkeeper(row, base_config())
        self.assertTrue(flagged)
        self.assertIn("opponent goal", reason)

    def test_opponent_goalkeeper_filter_flags_stationary_far_goal_field_color(self) -> None:
        row = {
            "mean_x": 38.1,
            "mean_y": 11.0,
            "frames": 20,
            "span_x_m": 0.7,
            "span_y_m": 0.6,
            "primary_color": "pink_red",
        }
        flagged, _ = is_likely_opponent_goalkeeper(row, base_config())
        self.assertTrue(flagged)

    def test_latest_report_record_prefers_explicit_latest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_dir = root / "reports"
            review_dir = root / "review"
            legacy = output_dir / "mvp_initial"
            latest = output_dir / "runs" / "final_20260508_120000"
            for folder in [legacy, latest]:
                folder.mkdir(parents=True)
                (folder / "report.md").write_text("# report\n", encoding="utf-8")
                (folder / "report.html").write_text("<html></html>\n", encoding="utf-8")
            config = base_config(output_dir, review_dir)
            write_latest_report(config, latest, {"source": "test"})
            record = latest_report_record(config)
            self.assertEqual(Path(record["report_dir"]).name, "final_20260508_120000")
            latest_yaml = yaml.safe_load((output_dir / "latest_report.yaml").read_text(encoding="utf-8"))
            self.assertEqual(latest_yaml["source"], "test")


if __name__ == "__main__":
    unittest.main()
