from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from analysis_app_config import DEFAULT_METRICS as CONFIG_DEFAULT_METRICS
from analysis_detection_filters import filter_static_ball_false_positives, is_likely_opponent_goalkeeper
from analysis_metrics import DEFAULT_METRICS, metric_definition, metric_status_rows
from analysis_report_runs import latest_report_record, write_latest_report


def base_config(output_dir: Path | None = None, review_dir: Path | None = None) -> dict:
    return {
        "field": {"length_m": 40.0, "width_m": 20.0},
        "match": {
            "output_dir": str(output_dir or PROJECT_ROOT / "tmp_reports"),
            "review_dir": str(review_dir or PROJECT_ROOT / "tmp_review"),
        },
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
                }
            },
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
