#!/usr/bin/env python3
"""Generate the final report after human review has been submitted."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml

try:
    from analysis_report_runs import report_dir_for_output_name, write_latest_report
except ModuleNotFoundError:
    from scripts.analysis_report_runs import report_dir_for_output_name, write_latest_report


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
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def write_yaml(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, allow_unicode=True, sort_keys=False)


def run_command(command: List[str]) -> None:
    print("+ " + " ".join(command))
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)


def output_slug(start_sec: float, duration_sec: float, sample_fps: float) -> str:
    return f"segment_{int(round(start_sec)):04d}_{int(round(duration_sec)):04d}_{sample_fps:g}fps".replace(".", "p")


def duration_from_probe(config: Dict[str, Any]) -> float:
    probe_path = resolve_path(config["match"]["interim_dir"]) / "video_probe.json"
    if not probe_path.exists():
        return 0.0
    with probe_path.open("r", encoding="utf-8") as handle:
        probe = json.load(handle)
    return float(probe.get("opencv", {}).get("duration_sec") or 0.0)


def fallback_detections_dir(config: Dict[str, Any], sample_fps: float) -> Path:
    duration_sec = duration_from_probe(config)
    if duration_sec <= 0:
        duration_sec = 580.0
    return resolve_path(config["match"]["interim_dir"]) / "detections" / output_slug(0.0, duration_sec, sample_fps)


def identity_review_path(config: Dict[str, Any]) -> Path:
    configured = config.get("review", {}).get("manual_identity_path")
    if configured:
        return resolve_path(configured)
    return resolve_path(config["match"].get("review_dir", "review")) / "identity_review.yaml"


def source_detections_dir(config: Dict[str, Any], review: Dict[str, Any]) -> Path:
    review_meta = review.get("human_review", {})
    configured = review_meta.get("source_detections_dir")
    if configured:
        return resolve_path(configured)
    manifest_path = resolve_path(config["match"].get("review_dir", "review")) / "human_review" / "review_manifest.yaml"
    manifest = load_yaml(manifest_path)
    if manifest.get("source_detections_dir"):
        return resolve_path(manifest["source_detections_dir"])
    sample_fps = float(config.get("detection", {}).get("sample_fps", 2.0))
    return fallback_detections_dir(config, sample_fps)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--output-name", default=None)
    parser.add_argument("--allow-draft", action="store_true")
    parser.add_argument("--right-forward-y", choices=["high", "low"], default="high")
    args = parser.parse_args()

    config_path = resolve_path(args.config)
    config = load_yaml(config_path)
    if not config:
        raise FileNotFoundError(config_path)

    review_path = identity_review_path(config)
    review = load_yaml(review_path)
    if not review:
        raise FileNotFoundError(f"Human review identity file not found: {review_path}")
    status = str(review.get("human_review", {}).get("status") or "")
    if status != "submitted" and not args.allow_draft:
        raise RuntimeError("Human review has not been submitted yet.")

    detections_dir = source_detections_dir(config, review)
    if not (detections_dir / "tracklet_identity_assignments.csv").exists():
        raise FileNotFoundError(f"Missing detection identity assignments: {detections_dir}")

    run_command(
        [
            sys.executable,
            "scripts/06_assign_identities.py",
            "--config",
            project_relative(config_path),
            "--detections-dir",
            project_relative(detections_dir),
            "--manual-bindings",
            project_relative(review_path),
            "--right-forward-y",
            args.right_forward_y,
        ]
    )

    output_dir = report_dir_for_output_name(config, args.output_name, prefix="final")
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

    latest_payload = write_latest_report(
        config,
        output_dir,
        {
            "source": "final_report",
            "source_detections_dir": project_relative(detections_dir),
        },
    )
    status_path = resolve_path(config["match"].get("review_dir", "review")) / "human_review" / "final_report_status.yaml"
    write_yaml(
        status_path,
        {
            "status": "done",
            "finalized_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "source_detections_dir": project_relative(detections_dir),
            "report_dir": project_relative(output_dir),
            "latest_report": latest_payload,
        },
    )
    print("\nReviewed final report is ready.")
    print(f"detections_dir: {detections_dir}")
    print(f"report_dir: {output_dir}")


if __name__ == "__main__":
    main()
