#!/usr/bin/env python3
"""Run the full MVP analysis pipeline for a prepared match project."""

from __future__ import annotations

import argparse
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


def run_command(command: List[str]) -> None:
    print("+ " + " ".join(command))
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)


def output_slug(start_sec: float, duration_sec: float, sample_fps: float) -> str:
    return f"segment_{int(round(start_sec)):04d}_{int(round(duration_sec)):04d}_{sample_fps:g}fps".replace(".", "p")


def duration_from_probe(config: Dict[str, Any]) -> float:
    probe_path = resolve_path(config["match"]["interim_dir"]) / "video_probe.json"
    if not probe_path.exists():
        return 0.0
    import json

    with probe_path.open("r", encoding="utf-8") as handle:
        probe = json.load(handle)
    return float(probe.get("opencv", {}).get("duration_sec") or 0.0)


def manual_bindings_path(config: Dict[str, Any]) -> Optional[Path]:
    configured = config.get("review", {}).get("manual_identity_path")
    if configured:
        path = resolve_path(configured)
        if path.exists():
            return path
    review_dir = config["match"].get("review_dir")
    if review_dir:
        path = resolve_path(review_dir) / "identity_review.yaml"
        if path.exists():
            return path
    return None


def full_detections_dir(config: Dict[str, Any], start_sec: float, duration_sec: float, sample_fps: float) -> Path:
    return resolve_path(config["match"]["interim_dir"]) / "detections" / output_slug(start_sec, duration_sec, sample_fps)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--start-sec", type=float, default=0.0)
    parser.add_argument("--duration-sec", type=float, default=None)
    parser.add_argument("--sample-fps", type=float, default=None)
    parser.add_argument("--device", default=None)
    parser.add_argument("--save-annotated-every", type=int, default=60)
    parser.add_argument("--right-forward-y", choices=["high", "low"], default="high")
    parser.add_argument("--output-name", default="mvp_initial")
    parser.add_argument("--manual-bindings", default=None, help="Optional identity review YAML for this exact full-run track set.")
    parser.add_argument(
        "--use-review-bindings",
        action="store_true",
        help="Use config review.manual_identity_path. Only use this when it was reviewed for the same full-run detections.",
    )
    args = parser.parse_args()

    config_path = resolve_path(args.config)
    config = load_yaml(config_path)
    if not (resolve_path(config["match"]["interim_dir"]) / "video_probe.json").exists():
        run_command([sys.executable, "scripts/00_probe_video.py", "--config", project_relative(config_path)])

    duration_sec = args.duration_sec
    if duration_sec is None:
        duration_sec = duration_from_probe(config)
        if duration_sec <= 0:
            duration_sec = 580.0
    sample_fps = args.sample_fps or float(config.get("detection", {}).get("sample_fps", 2.0))

    detect_cmd = [
        sys.executable,
        "scripts/03_detect_track.py",
        "--config",
        project_relative(config_path),
        "--start-sec",
        str(args.start_sec),
        "--duration-sec",
        str(duration_sec),
        "--sample-fps",
        str(sample_fps),
        "--max-frames",
        "0",
        "--save-annotated-every",
        str(args.save_annotated_every),
    ]
    if args.device:
        detect_cmd.extend(["--device", args.device])
    run_command(detect_cmd)

    detections_dir = full_detections_dir(config, args.start_sec, duration_sec, sample_fps)
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
            "80",
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
            "80",
        ]
    )

    assign_cmd = [
        sys.executable,
        "scripts/06_assign_identities.py",
        "--config",
        project_relative(config_path),
        "--detections-dir",
        project_relative(detections_dir),
        "--right-forward-y",
        args.right_forward_y,
    ]
    if args.manual_bindings:
        manual_path = resolve_path(args.manual_bindings)
    elif args.use_review_bindings:
        manual_path = manual_bindings_path(config)
    else:
        manual_path = None
    if manual_path:
        assign_cmd.extend(["--manual-bindings", project_relative(manual_path)])
    run_command(assign_cmd)

    output_dir = resolve_path(config["match"]["output_dir"]) / args.output_name
    run_command(
        [
            sys.executable,
            "scripts/07_generate_report.py",
            "--config",
            project_relative(config_path),
            "--detections-dir",
            project_relative(detections_dir),
            "--output-dir",
            project_relative(output_dir),
        ]
    )

    print("\nFull MVP report is ready.")
    print(f"detections_dir: {detections_dir}")
    print(f"report_dir: {output_dir}")


if __name__ == "__main__":
    main()
