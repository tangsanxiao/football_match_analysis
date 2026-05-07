#!/usr/bin/env python3
"""Prepare calibration and identity-review assets for an isolated match project."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

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
        return yaml.safe_load(handle)


def write_yaml(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, allow_unicode=True, sort_keys=False)


def run_command(command: List[str]) -> None:
    print("+ " + " ".join(command))
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)


def points_path_for(config: Dict[str, Any]) -> Path:
    configured = config.get("calibration", {}).get("points_path")
    if configured:
        return resolve_path(configured)
    match_id = config["match"]["id"]
    legacy = PROJECT_ROOT / "configs" / f"calibration_points_{match_id}.yaml"
    if legacy.exists():
        return legacy
    return resolve_path("configs/calibration_points_red_mvp.yaml")


def has_enough_calibration_points(points_path: Path) -> bool:
    points_yaml = load_yaml(points_path)
    ready = 0
    for point in points_yaml.get("points", []):
        if point.get("enabled") is False:
            continue
        image_xy = point.get("image_xy")
        if isinstance(image_xy, list) and len(image_xy) == 2:
            ready += 1
    return ready >= 4


def is_labeling_submitted(points_path: Path) -> bool:
    points_yaml = load_yaml(points_path)
    calibration = points_yaml.get("calibration", {})
    return bool(calibration.get("labeling_submitted") or calibration.get("labeling_status") == "submitted")


def is_match_info_confirmed(config: Dict[str, Any]) -> bool:
    match_info = config.get("review", {}).get("match_info", {})
    return bool(match_info.get("confirmed") or match_info.get("status") == "confirmed")


def load_sample_manifest(config: Dict[str, Any]) -> List[Dict[str, str]]:
    manifest = resolve_path(config["match"]["interim_dir"]) / "sample_frames" / "sample_frames.csv"
    if not manifest.exists():
        return []
    with manifest.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def choose_calibration_image(config: Dict[str, Any]) -> Optional[Path]:
    rows = [row for row in load_sample_manifest(config) if row.get("saved") in {"True", "true", "1", True}]
    if not rows:
        return None
    middle = rows[len(rows) // 2]
    return resolve_path(middle["path"])


def update_calibration_image(points_path: Path, image_path: Path) -> None:
    points_yaml = load_yaml(points_path)
    points_yaml.setdefault("calibration", {})
    points_yaml["calibration"]["image_path"] = project_relative(image_path)
    write_yaml(points_path, points_yaml)


def output_slug(start_sec: float, duration_sec: float, sample_fps: float) -> str:
    return f"segment_{int(round(start_sec)):04d}_{int(round(duration_sec)):04d}_{sample_fps:g}fps".replace(".", "p")


def initial_recognition_dir(config: Dict[str, Any]) -> Path:
    review = config.get("review", {}).get("initial_recognition", {})
    start_sec = float(review.get("start_sec", 120.0))
    duration_sec = float(review.get("duration_sec", 120.0))
    sample_fps = float(review.get("sample_fps", 2.0))
    return resolve_path(config["match"]["interim_dir"]) / "detections" / output_slug(start_sec, duration_sec, sample_fps)


def run_calibration_step(config_path: Path, config: Dict[str, Any], points_path: Path) -> None:
    run_command([sys.executable, "scripts/00_probe_video.py", "--config", project_relative(config_path)])
    run_command([sys.executable, "scripts/01_sample_frames.py", "--config", project_relative(config_path)])

    image_path = choose_calibration_image(config)
    if image_path is None:
        raise RuntimeError("No sample frame is available for calibration.")
    update_calibration_image(points_path, image_path)

    run_command(
        [
            sys.executable,
            "scripts/create_point_picker.py",
            "--config",
            project_relative(config_path),
            "--points",
            project_relative(points_path),
        ]
    )

    port = int(config.get("calibration", {}).get("picker_port", 8765))
    print("\nCalibration review is ready.")
    print(f"calibration_points: {points_path}")
    print(f"calibration_image: {image_path}")
    print("Run this to auto-save clicked field points:")
    print(
        f"  {sys.executable} scripts/serve_point_picker.py "
        f"--config {project_relative(config_path)} --points {project_relative(points_path)} --port {port}"
    )
    print(f"Then open: http://127.0.0.1:{port}/")


def team_key(config: Dict[str, Any]) -> str:
    return str(config.get("teams", {}).get("analyze_team", "red"))


def write_identity_review_template(config: Dict[str, Any], detections_dir: Path) -> Optional[Path]:
    assignments_path = detections_dir / "identity_bindings_auto.yaml"
    if not assignments_path.exists():
        return None
    auto = load_yaml(assignments_path)
    review_dir = resolve_path(config["match"].get("review_dir", Path(config["match"]["interim_dir"]).parent / "review"))
    review_dir.mkdir(parents=True, exist_ok=True)
    review_path = review_dir / "identity_review.yaml"
    if review_path.exists():
        return review_path

    payload: Dict[str, Any] = {
        "review_required": True,
        "instructions": [
            "Review track_ids against the contact sheet before full-report generation.",
            "Move wrong track_ids to the correct player_id or remove them.",
            "This file can be passed to scripts/06_assign_identities.py with --manual-bindings.",
        ],
        "assignments": {},
    }
    for player_id, item in (auto.get("assignments") or {}).items():
        payload["assignments"][player_id] = {
            "name": item.get("name"),
            "number": item.get("number"),
            "role": item.get("role"),
            "confidence": item.get("confidence_mean", 0.5),
            "reason": "human reviewed or accepted auto binding",
            "track_ids": item.get("track_ids", []),
        }
    write_yaml(review_path, payload)
    return review_path


def write_review_readme(config: Dict[str, Any], detections_dir: Path, review_path: Optional[Path]) -> Path:
    review_dir = resolve_path(config["match"].get("review_dir", Path(config["match"]["interim_dir"]).parent / "review"))
    review_dir.mkdir(parents=True, exist_ok=True)
    readme = review_dir / "README.md"
    lines = [
        "# Match Review Package",
        "",
        "## Calibration",
        "",
        f"- Calibration overlay: `{project_relative(resolve_path(config['match']['interim_dir']) / 'calibration' / 'calibration_overlay.jpg')}`",
        "",
        "## Initial Recognition",
        "",
        f"- Tracklet contact sheet: `{project_relative(detections_dir / 'tracklet_contact_sheet.jpg')}`",
        f"- Candidate contact sheet: `{project_relative(detections_dir / 'analyze_candidate_contact_sheet.jpg')}`",
        f"- Auto identity bindings: `{project_relative(detections_dir / 'identity_bindings_auto.yaml')}`",
    ]
    if review_path:
        lines.append(f"- Human review identity file: `{project_relative(review_path)}`")
    lines.extend(
        [
            "",
            "After reviewing identity bindings, run the full MVP pipeline script or call the detection/report scripts manually.",
        ]
    )
    readme.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return readme


def run_recognition_step(config_path: Path, config: Dict[str, Any], points_path: Path, device: Optional[str]) -> None:
    if not is_match_info_confirmed(config):
        print(
            "Warning: match information has not been confirmed in the browser page. "
            "Open http://127.0.0.1:8765/match and click '确认并写入 match.yaml' when ready."
        )
    if not has_enough_calibration_points(points_path):
        raise SystemExit(
            "Calibration points are not ready. Mark at least 4 enabled points, then re-run with --step recognition."
        )
    if not is_labeling_submitted(points_path):
        print(
            "Warning: calibration points are marked but not submitted. "
            "Open the point picker and click '提交打标' when you want to record human confirmation."
        )

    run_command(
        [
            sys.executable,
            "scripts/02_calibrate_field.py",
            "--config",
            project_relative(config_path),
            "--points",
            project_relative(points_path),
        ]
    )

    review = config.get("review", {}).get("initial_recognition", {})
    start_sec = float(review.get("start_sec", 120.0))
    duration_sec = float(review.get("duration_sec", 120.0))
    sample_fps = float(review.get("sample_fps", 2.0))
    max_frames = int(review.get("max_frames", 240))
    top_n = int(review.get("top_n", 60))

    detect_cmd = [
        sys.executable,
        "scripts/03_detect_track.py",
        "--config",
        project_relative(config_path),
        "--start-sec",
        str(start_sec),
        "--duration-sec",
        str(duration_sec),
        "--sample-fps",
        str(sample_fps),
        "--max-frames",
        str(max_frames),
        "--save-annotated-every",
        "20",
    ]
    if device:
        detect_cmd.extend(["--device", device])
    run_command(detect_cmd)

    detections_dir = initial_recognition_dir(config)
    run_command(
        [
            sys.executable,
            "scripts/04_summarize_tracklets.py",
            "--config",
            project_relative(config_path),
            "--detections-dir",
            project_relative(detections_dir),
            "--min-frames",
            "8",
            "--top-n",
            str(top_n),
        ]
    )
    run_command(
        [
            sys.executable,
            "scripts/05_classify_tracklets.py",
            "--config",
            project_relative(config_path),
            "--detections-dir",
            project_relative(detections_dir),
            "--samples-per-track",
            "5",
            "--top-n",
            str(top_n),
        ]
    )
    run_command(
        [
            sys.executable,
            "scripts/06_assign_identities.py",
            "--config",
            project_relative(config_path),
            "--detections-dir",
            project_relative(detections_dir),
        ]
    )
    review_path = write_identity_review_template(config, detections_dir)
    readme = write_review_readme(config, detections_dir, review_path)

    print("\nInitial recognition review is ready.")
    print(f"detections_dir: {detections_dir}")
    print(f"review_readme: {readme}")
    if review_path:
        print(f"identity_review_yaml: {review_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--step", choices=["calibration", "recognition", "all"], default="all")
    parser.add_argument("--device", default=None, help="Ultralytics device, e.g. mps, cpu, 0.")
    args = parser.parse_args()

    config_path = resolve_path(args.config)
    config = load_yaml(config_path)
    points_path = points_path_for(config)

    if args.step in {"calibration", "all"}:
        run_calibration_step(config_path, config, points_path)

    if args.step in {"recognition", "all"}:
        config = load_yaml(config_path)
        run_recognition_step(config_path, config, points_path, args.device)


if __name__ == "__main__":
    main()
