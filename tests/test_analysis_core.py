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

merge_spec = importlib.util.spec_from_file_location(
    "merge_match_segments", PROJECT_ROOT / "scripts" / "16_merge_match_segments.py"
)
merge_match_segments = importlib.util.module_from_spec(merge_spec)
assert merge_spec and merge_spec.loader
merge_spec.loader.exec_module(merge_match_segments)

import analysis_eval  # noqa: E402


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


class TestMergeMatchSegments(unittest.TestCase):
    """Lock in the per-segment merge math for 16_merge_match_segments.py."""

    @staticmethod
    def _player_df(rows):
        import pandas as pd

        cols = [
            "player_id", "name", "number", "role",
            "observed_frames", "observed_seconds", "observed_coverage_pct",
            "distance_m", "distance_per_min",
            "high_speed_distance_m", "high_speed_pct",
            "pressing_pct", "duel_proximity_pct",
            "attacking_half_pct", "final_third_pct", "role_zone_pct",
            "avg_nearest_opponent_m", "avg_x_m", "avg_y_m",
            "rating", "confidence",
        ]
        return pd.DataFrame(rows, columns=cols)

    def test_merge_player_metrics_sums_avgs_and_recomputes_rate(self) -> None:
        seg_a = self._player_df([
            ["p1", "ALPHA", 7, "defender",
             100, 50.0, 50.0, 200.0, 240.0, 20.0, 10.0,
             5.0, 2.0, 30.0, 10.0, 80.0, 6.0, 20.0, 5.0,
             8.0, "high"],
            ["p2", "BETA", 9, "center_forward",
             200, 100.0, 70.0, 500.0, 300.0, 100.0, 20.0,
             8.0, 4.0, 60.0, 25.0, 70.0, 7.0, 30.0, 12.0,
             7.0, "medium"],
        ])
        seg_b = self._player_df([
            ["p1", "ALPHA", 7, "defender",
             300, 150.0, 60.0, 600.0, 240.0, 60.0, 10.0,
             5.0, 2.0, 30.0, 10.0, 90.0, 6.0, 22.0, 5.0,
             6.0, "medium"],
            # p3 only present in segment B
            ["p3", "GAMMA", 11, "left_forward",
             50, 25.0, 30.0, 100.0, 240.0, 0.0, 0.0,
             0.0, 0.0, 50.0, 20.0, 60.0, 8.0, 25.0, 8.0,
             7.5, "low"],
        ])

        merged = merge_match_segments.merge_player_metrics([seg_a, seg_b])

        merged_by_id = {row["player_id"]: row for _, row in merged.iterrows()}

        # ALPHA appears in both segments — sums and weighted averages
        alpha = merged_by_id["p1"]
        self.assertEqual(alpha["observed_frames"], 400.0)
        self.assertEqual(alpha["observed_seconds"], 200.0)
        self.assertAlmostEqual(alpha["distance_m"], 800.0, places=1)
        self.assertAlmostEqual(alpha["high_speed_distance_m"], 80.0, places=1)
        # distance_per_min recomputed: 800 / 200 * 60 = 240
        self.assertAlmostEqual(alpha["distance_per_min"], 240.0, places=1)
        # rating weighted by observed_frames: (8.0*100 + 6.0*300) / 400 = 6.5
        self.assertAlmostEqual(alpha["rating"], 6.5, places=2)
        # confidence: high vs medium → most conservative is medium
        self.assertEqual(alpha["confidence"], "medium")

        # BETA appears only in segment A — passes through
        beta = merged_by_id["p2"]
        self.assertEqual(beta["observed_frames"], 200.0)
        self.assertAlmostEqual(beta["distance_m"], 500.0, places=1)
        self.assertEqual(beta["confidence"], "medium")

        # GAMMA appears only in segment B
        gamma = merged_by_id["p3"]
        self.assertEqual(gamma["observed_frames"], 50.0)
        self.assertAlmostEqual(gamma["distance_m"], 100.0, places=1)
        self.assertEqual(gamma["confidence"], "low")

    def test_confidence_min_picks_worst(self) -> None:
        cm = merge_match_segments.confidence_min
        self.assertEqual(cm(["high", "medium"]), "medium")
        self.assertEqual(cm(["medium", "low"]), "low")
        self.assertEqual(cm(["high", "high"]), "high")
        # Chinese labels mix: 中 ranks same as medium, 低 same as low
        self.assertEqual(cm(["高", "中"]), "中")
        self.assertEqual(cm([None, "", "high"]), "high")
        self.assertEqual(cm([]), "")

    def test_merge_key_timestamps_applies_offset_and_segment_label(self) -> None:
        import pandas as pd

        df_a = pd.DataFrame([
            {"timestamp_sec": 10.0, "timestamp": "00:10", "event_type": "高速跑动"},
            {"timestamp_sec": 100.0, "timestamp": "01:40", "event_type": "压迫候选"},
        ])
        df_b = pd.DataFrame([
            {"timestamp_sec": 5.0, "timestamp": "00:05", "event_type": "高速跑动"},
        ])
        merged = merge_match_segments.merge_key_timestamps([
            (df_a, 0.0, "上半场"),
            (df_b, 600.0, "下半场"),
        ])
        # Three events total, sorted by timestamp ascending
        self.assertEqual(len(merged), 3)
        self.assertListEqual(list(merged["segment"]), ["上半场", "上半场", "下半场"])
        # df_b's first event got +600s offset, becoming 605s
        self.assertAlmostEqual(merged.iloc[2]["timestamp_sec"], 605.0)
        self.assertEqual(merged.iloc[2]["timestamp"], "10:05")


class TestEvalLayerA(unittest.TestCase):
    """Sanity / schema checks should detect missing files, bad values, wrong labels."""

    @staticmethod
    def _build_report(tmp: Path, *, with_files=True, player_rows=None, event_rows=None):
        import pandas as pd

        report_dir = tmp / "reports" / "runs" / "final_test"
        if with_files:
            report_dir.mkdir(parents=True)
            (report_dir / "report.md").write_text("# r\n", encoding="utf-8")
            (report_dir / "report.html").write_text("<html></html>", encoding="utf-8")
        else:
            report_dir.mkdir(parents=True)
            # leave report files missing intentionally

        # default player rows
        if player_rows is None:
            player_rows = [{
                "player_id": "p1", "name": "ALPHA", "role": "defender",
                "observed_frames": 100, "observed_seconds": 50.0,
                "observed_coverage_pct": 50.0,
                "distance_m": 200.0, "distance_per_min": 240.0,
                "rating": 7.5, "confidence": "high",
            }]
        pd.DataFrame(player_rows).to_csv(report_dir / "player_metrics.csv", index=False)

        if event_rows is None:
            event_rows = [{
                "timestamp_sec": 30.0, "player_id": "p1",
                "event_type": "高速跑动", "confidence": "low",
            }]
        pd.DataFrame(event_rows).to_csv(report_dir / "key_timestamps.csv", index=False)

        return report_dir

    def test_smoke_passes_on_complete_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report_dir = self._build_report(Path(tmp))
            result = analysis_eval.run_layer_a("test_match", report_dir)
            self.assertTrue(result.passed, msg=[f.message for f in result.findings])

    def test_smoke_fails_when_report_files_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            empty_dir = Path(tmp) / "empty_report"
            empty_dir.mkdir()
            result = analysis_eval.run_layer_a("m", empty_dir)
            self.assertFalse(result.passed)
            codes = {f.code for f in result.findings}
            self.assertIn("report_file_missing", codes)

    def test_value_range_violation_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report_dir = self._build_report(
                Path(tmp),
                player_rows=[{
                    "player_id": "p1", "name": "A", "role": "defender",
                    "observed_frames": 10, "observed_seconds": 5.0,
                    "observed_coverage_pct": 50.0,
                    "distance_m": 100.0, "distance_per_min": 800.0,  # > 500
                    "rating": 11.0,  # > 10
                    "confidence": "high",
                }],
            )
            result = analysis_eval.run_layer_a("m", report_dir)
            self.assertFalse(result.passed)
            codes = {f.code for f in result.findings}
            self.assertIn("value_above_range", codes)

    def test_illegal_confidence_label_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report_dir = self._build_report(
                Path(tmp),
                player_rows=[{
                    "player_id": "p1", "name": "A", "role": "defender",
                    "observed_frames": 10, "observed_seconds": 5.0,
                    "observed_coverage_pct": 50.0,
                    "distance_m": 100.0, "distance_per_min": 200.0,
                    "rating": 7.0, "confidence": "totally_made_up",
                }],
            )
            result = analysis_eval.run_layer_a("m", report_dir)
            codes = {f.code for f in result.findings}
            self.assertIn("illegal_confidence_label", codes)

    def test_event_player_unknown_warns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report_dir = self._build_report(
                Path(tmp),
                event_rows=[{
                    "timestamp_sec": 5.0, "player_id": "ghost_player",
                    "event_type": "压迫候选", "confidence": "low",
                }],
            )
            result = analysis_eval.run_layer_a("m", report_dir)
            warn_codes = {f.code for f in result.findings if f.severity == "warn"}
            self.assertIn("event_player_unknown", warn_codes)


class TestEvalLayerB(unittest.TestCase):
    """Accuracy metric math against synthetic gold + system data."""

    @staticmethod
    def _gold_tracks(rows):
        import pandas as pd

        return pd.DataFrame(rows, columns=["timestamp_sec", "player_id", "field_x_m", "field_y_m"])

    @staticmethod
    def _system_tracks(rows):
        import pandas as pd

        return pd.DataFrame(
            rows,
            columns=["timestamp_sec", "assigned_player_id", "field_x_m", "field_y_m"],
        )

    def test_player_detection_perfect_recall(self) -> None:
        gold = self._gold_tracks([
            (10.0, "p1", 20.0, 10.0),
            (10.5, "p2", 25.0, 5.0),
        ])
        system = self._system_tracks([
            (10.0, "p1", 20.1, 10.1),
            (10.5, "p2", 25.05, 5.05),
        ])
        det, ident = analysis_eval.compute_player_detection_recall(
            gold, system, pos_tol_m=1.0, ts_tol_sec=0.5,
        )
        self.assertEqual(det.value, 100.0)
        self.assertEqual(ident.value, 100.0)

    def test_player_detection_offset_too_far(self) -> None:
        gold = self._gold_tracks([(10.0, "p1", 20.0, 10.0)])
        # system labeled this player 10 meters away — outside tolerance
        system = self._system_tracks([(10.0, "p1", 30.0, 10.0)])
        det, ident = analysis_eval.compute_player_detection_recall(
            gold, system, pos_tol_m=1.0, ts_tol_sec=0.5,
        )
        self.assertEqual(det.value, 0.0)
        self.assertIsNone(ident.value)  # denominator was 0

    def test_identity_binding_swap(self) -> None:
        # Gold says p1 is here. System has someone here, but bound to p2.
        gold = self._gold_tracks([(10.0, "p1", 20.0, 10.0)])
        system = self._system_tracks([(10.0, "p2", 20.0, 10.0)])
        det, ident = analysis_eval.compute_player_detection_recall(
            gold, system, pos_tol_m=1.0, ts_tol_sec=0.5,
        )
        # Detection: gold rows that had a system track for the *same* player_id within tol.
        self.assertEqual(det.value, 0.0)
        # Identity denominator: 1 (system saw someone there). Hits: 0 (wrong identity).
        self.assertEqual(ident.numerator, 0)
        self.assertEqual(ident.denominator, 1)
        self.assertEqual(ident.value, 0.0)

    def test_event_metrics_perfect_match(self) -> None:
        import pandas as pd

        gold = pd.DataFrame([
            {"timestamp_sec": 100.0, "event_type": "pass",
             "field_x_m": 20.0, "field_y_m": 10.0},
        ])
        system = pd.DataFrame([
            {"timestamp_sec": 101.0, "event_type": "pass",
             "field_x_m": 20.5, "field_y_m": 10.5},
        ])
        out = analysis_eval.compute_event_metrics(
            gold, system, ts_tol_sec=5.0, pos_tol_m=5.0,
        )
        self.assertIn("pass", out)
        self.assertEqual(out["pass"]["recall"].value, 100.0)
        self.assertEqual(out["pass"]["precision"].value, 100.0)

    def test_event_metrics_extra_system_event_lowers_precision(self) -> None:
        import pandas as pd

        gold = pd.DataFrame([
            {"timestamp_sec": 100.0, "event_type": "pass"},
        ])
        system = pd.DataFrame([
            {"timestamp_sec": 100.5, "event_type": "pass"},  # match
            {"timestamp_sec": 200.0, "event_type": "pass"},  # spurious
            {"timestamp_sec": 300.0, "event_type": "pass"},  # spurious
        ])
        out = analysis_eval.compute_event_metrics(
            gold, system, ts_tol_sec=2.0, pos_tol_m=5.0,
        )
        self.assertEqual(out["pass"]["recall"].value, 100.0)
        # 1 match out of 3 system events
        self.assertAlmostEqual(out["pass"]["precision"].value, 33.33, places=1)

    def test_ball_metrics_recall_and_position_error(self) -> None:
        import pandas as pd

        gold = pd.DataFrame([
            {"timestamp_sec": 5.0, "status": "in_play", "field_x_m": 10.0, "field_y_m": 5.0},
            {"timestamp_sec": 6.0, "status": "in_play", "field_x_m": 12.0, "field_y_m": 5.0},
            {"timestamp_sec": 7.0, "status": "invisible", "field_x_m": None, "field_y_m": None},
        ])
        system = pd.DataFrame([
            {"timestamp_sec": 5.0, "ball_in_play": True,
             "field_x_m": 10.5, "field_y_m": 5.0},
            # second gold point not matched by system
            {"timestamp_sec": 6.0, "ball_in_play": False,
             "field_x_m": None, "field_y_m": None},
        ])
        recall, pos_err = analysis_eval.compute_ball_metrics(
            gold, system, pos_tol_m=1.0, ts_tol_sec=0.5,
        )
        # Two in_play gold points, one matched
        self.assertEqual(recall.numerator, 1)
        self.assertEqual(recall.denominator, 2)
        self.assertEqual(recall.value, 50.0)
        self.assertAlmostEqual(pos_err.value, 0.5, places=2)


if __name__ == "__main__":
    unittest.main()
