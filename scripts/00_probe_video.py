#!/usr/bin/env python3
"""Probe basic video metadata for the football analysis MVP."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional, Union

import cv2
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def resolve_path(path: Union[str, Path]) -> Path:
    path = Path(path)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def load_config(config_path: Path) -> Dict[str, Any]:
    with config_path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def run_ffprobe(video_path: Path) -> Optional[Dict[str, Any]]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None

    command = [
        ffprobe,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(video_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return {"error": result.stderr.strip()}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {"error": f"Could not parse ffprobe JSON: {exc}"}


def probe_with_opencv(video_path: Path) -> Dict[str, Any]:
    cap = cv2.VideoCapture(str(video_path))
    opened = cap.isOpened()
    metadata: Dict[str, Any] = {"opened": opened}

    if opened:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        duration_sec = frame_count / fps if fps > 0 else None

        metadata.update(
            {
                "width": width,
                "height": height,
                "fps": fps,
                "frame_count": frame_count,
                "duration_sec": duration_sec,
            }
        )

        ok, frame = cap.read()
        metadata["first_frame_readable"] = bool(ok and frame is not None)
        if ok and frame is not None:
            metadata["first_frame_shape"] = list(frame.shape)

    cap.release()
    return metadata


def build_probe(config: Dict[str, Any], config_path: Path) -> Dict[str, Any]:
    match = config["match"]
    video_path = resolve_path(match["video_path"])
    if not video_path.exists():
        raise FileNotFoundError(f"Video file does not exist: {video_path}")

    stat = video_path.stat()
    ffprobe_data = run_ffprobe(video_path)
    opencv_data = probe_with_opencv(video_path)

    return {
        "match_id": match["id"],
        "config_path": str(config_path),
        "video_path": str(video_path),
        "file_size_bytes": stat.st_size,
        "file_size_gb": round(stat.st_size / (1024**3), 3),
        "opencv": opencv_data,
        "ffprobe": ffprobe_data,
    }


def write_probe(config: Dict[str, Any], probe: Dict[str, Any]) -> Path:
    interim_dir = resolve_path(config["match"]["interim_dir"])
    interim_dir.mkdir(parents=True, exist_ok=True)
    output_path = interim_dir / "video_probe.json"
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(probe, handle, indent=2, ensure_ascii=False)
    return output_path


def print_summary(probe: Dict[str, Any], output_path: Path) -> None:
    opencv = probe["opencv"]
    print(f"match_id: {probe['match_id']}")
    print(f"video_path: {probe['video_path']}")
    print(f"file_size_gb: {probe['file_size_gb']}")
    print(f"opencv_opened: {opencv.get('opened')}")
    print(f"resolution: {opencv.get('width')}x{opencv.get('height')}")
    print(f"fps: {opencv.get('fps')}")
    print(f"frame_count: {opencv.get('frame_count')}")
    print(f"duration_sec: {opencv.get('duration_sec')}")
    print(f"first_frame_readable: {opencv.get('first_frame_readable')}")
    print(f"probe_json: {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        default="configs/match_red_mvp.yaml",
        help="Path to the match config YAML.",
    )
    args = parser.parse_args()

    config_path = resolve_path(args.config)
    config = load_config(config_path)
    probe = build_probe(config, config_path)
    output_path = write_probe(config, probe)
    print_summary(probe, output_path)


if __name__ == "__main__":
    main()
