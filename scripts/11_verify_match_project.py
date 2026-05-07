#!/usr/bin/env python3
"""Verify that an isolated match project has the expected workflow assets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def resolve_path(path: Union[str, Path]) -> Path:
    path = Path(path).expanduser()
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def project_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT.resolve()))
    except ValueError:
        return str(path.resolve())


def load_yaml(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def output_slug(start_sec: float, duration_sec: float, sample_fps: float) -> str:
    return f"segment_{int(round(start_sec)):04d}_{int(round(duration_sec)):04d}_{sample_fps:g}fps".replace(".", "p")


def status_icon(ok: bool, warn: bool = False) -> str:
    if ok:
        return "OK"
    if warn:
        return "WARN"
    return "MISS"


def add_check(checks: List[Tuple[str, bool, bool, str]], name: str, ok: bool, detail: str, warn: bool = False) -> None:
    checks.append((name, ok, warn, detail))


def count_marked_points(points_path: Path) -> Tuple[int, int]:
    if not points_path.exists():
        return 0, 0
    points_yaml = load_yaml(points_path)
    total = 0
    marked = 0
    for point in points_yaml.get("points", []):
        if point.get("enabled") is False:
            continue
        total += 1
        image_xy = point.get("image_xy")
        if isinstance(image_xy, list) and len(image_xy) == 2:
            marked += 1
    return marked, total


def read_probe_duration(interim_dir: Path) -> Optional[float]:
    probe_path = interim_dir / "video_probe.json"
    if not probe_path.exists():
        return None
    with probe_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    duration = payload.get("opencv", {}).get("duration_sec")
    return float(duration) if duration else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, help="Path to matches/<match_id>/config/match.yaml.")
    parser.add_argument("--output-name", default="mvp_initial")
    args = parser.parse_args()

    config_path = resolve_path(args.config)
    config = load_yaml(config_path)
    match = config.get("match", {})
    match_id = str(match.get("id", "unknown"))
    project_dir = resolve_path(match.get("project_dir", config_path.parents[1]))
    interim_dir = resolve_path(match.get("interim_dir", project_dir / "data" / "interim"))
    review_dir = resolve_path(match.get("review_dir", project_dir / "review"))
    output_dir = resolve_path(match.get("output_dir", project_dir / "reports"))
    points_path = resolve_path(config.get("calibration", {}).get("points_path", project_dir / "config" / "calibration_points.yaml"))
    video_path = resolve_path(match.get("video_path", ""))
    analyze_team = str(config.get("teams", {}).get("analyze_team", ""))
    team = config.get("teams", {}).get(analyze_team, {})
    review_cfg = config.get("review", {}).get("initial_recognition", {})
    start_sec = float(review_cfg.get("start_sec", 120.0))
    duration_sec = float(review_cfg.get("duration_sec", 120.0))
    sample_fps = float(review_cfg.get("sample_fps", 2.0))
    recognition_dir = interim_dir / "detections" / output_slug(start_sec, duration_sec, sample_fps)
    report_dir = output_dir / args.output_name

    checks: List[Tuple[str, bool, bool, str]] = []
    add_check(checks, "config", config_path.exists(), project_relative(config_path))
    add_check(checks, "project_dir", project_dir.exists(), project_relative(project_dir))
    add_check(checks, "video", video_path.exists(), project_relative(video_path))
    for label, folder in [
        ("config_dir", project_dir / "config"),
        ("interim_dir", interim_dir),
        ("review_dir", review_dir),
        ("reports_dir", output_dir),
    ]:
        add_check(checks, label, folder.exists(), project_relative(folder))

    players = team.get("players") or []
    substitutions = team.get("substitutions") or []
    metrics = config.get("analysis", {}).get("target_metrics") or []
    field_colors = team.get("kit", {}).get("field_colors") or []
    goalkeeper_colors = team.get("kit", {}).get("goalkeeper_colors") or []
    add_check(checks, "analyze_team", bool(analyze_team and team), analyze_team)
    add_check(checks, "team_colors", bool(field_colors and goalkeeper_colors), f"field={field_colors}, goalkeeper={goalkeeper_colors}")
    add_check(checks, "players", len(players) > 0 and players[0].get("name") != "TODO", f"{len(players)} players: {', '.join(str(p.get('name')) for p in players)}")
    add_check(checks, "substitutions", True, f"{len(substitutions)} records")
    add_check(checks, "target_metrics", bool(metrics), f"{len(metrics)} metrics")
    match_info_review = config.get("review", {}).get("match_info", {})
    match_info_confirmed = bool(match_info_review.get("confirmed") or match_info_review.get("status") == "confirmed")
    add_check(
        checks,
        "match_info_confirmed",
        match_info_confirmed,
        str(match_info_review.get("confirmed_at") or "open /match and confirm"),
        warn=True,
    )

    marked, total = count_marked_points(points_path)
    point_picker = interim_dir / "calibration" / "point_picker.html"
    add_check(checks, "calibration_points_yaml", points_path.exists(), project_relative(points_path))
    add_check(checks, "calibration_points_marked", marked >= 4, f"{marked}/{total} enabled points marked", warn=True)
    add_check(checks, "point_picker_html", point_picker.exists(), project_relative(point_picker))

    probe_duration = read_probe_duration(interim_dir)
    add_check(checks, "video_probe", probe_duration is not None, f"duration={probe_duration}s" if probe_duration else "not generated")
    sample_manifest = interim_dir / "sample_frames" / "sample_frames.csv"
    add_check(checks, "sample_frames", sample_manifest.exists(), project_relative(sample_manifest))

    add_check(checks, "initial_recognition_dir", recognition_dir.exists(), project_relative(recognition_dir), warn=True)
    for filename in ["tracklet_contact_sheet.jpg", "analyze_candidate_contact_sheet.jpg", "identity_bindings_auto.yaml"]:
        path = recognition_dir / filename
        add_check(checks, f"initial_{filename}", path.exists(), project_relative(path), warn=True)
    identity_review = review_dir / "identity_review.yaml"
    add_check(checks, "identity_review_yaml", identity_review.exists(), project_relative(identity_review), warn=True)

    for filename in ["report.md", "report.html", "player_metrics.csv", "key_timestamps.csv"]:
        path = report_dir / filename
        add_check(checks, f"report_{filename}", path.exists(), project_relative(path), warn=True)

    print(f"# Match Project Verification: {match_id}")
    print("")
    for name, ok, warn, detail in checks:
        print(f"{status_icon(ok, warn)}  {name}: {detail}")
    print("")
    hard_missing = [name for name, ok, warn, _ in checks if not ok and not warn]
    soft_missing = [name for name, ok, warn, _ in checks if not ok and warn]
    if hard_missing:
        print("result: FAILED")
        print("missing_required: " + ", ".join(hard_missing))
    elif soft_missing:
        print("result: PARTIAL")
        print("missing_next_steps: " + ", ".join(soft_missing))
    else:
        print("result: COMPLETE")


if __name__ == "__main__":
    main()
