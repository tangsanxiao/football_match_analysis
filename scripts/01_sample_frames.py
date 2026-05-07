#!/usr/bin/env python3
"""Extract evenly spaced sample frames for visual quality checks."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import cv2
import yaml
from tqdm import tqdm


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def resolve_path(path: Union[str, Path]) -> Path:
    path = Path(path)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def load_config(config_path: Path) -> Dict[str, Any]:
    with config_path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_probe(config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    probe_path = resolve_path(config["match"]["interim_dir"]) / "video_probe.json"
    if not probe_path.exists():
        return None
    with probe_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def video_info_from_probe(probe: Optional[Dict[str, Any]]) -> Tuple[int, float]:
    if not probe:
        return 0, 0.0
    opencv = probe.get("opencv", {})
    return int(opencv.get("frame_count") or 0), float(opencv.get("fps") or 0.0)


def frame_indices(frame_count: int, count: int) -> List[int]:
    if frame_count <= 0:
        raise ValueError("Cannot sample frames because frame_count is unknown.")
    if count <= 0:
        raise ValueError("sample_frame_count must be positive.")
    if count == 1:
        return [0]

    last = max(frame_count - 1, 0)
    return sorted({round(i * last / (count - 1)) for i in range(count)})


def seek_frame(cap: cv2.VideoCapture, target_idx: int, search_radius: int = 12) -> Tuple[bool, Optional[Any], int]:
    candidates = [target_idx]
    for offset in range(1, search_radius + 1):
        candidates.extend([max(0, target_idx - offset), target_idx + offset])

    for idx in candidates:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if ok and frame is not None:
            actual_idx = int(cap.get(cv2.CAP_PROP_POS_FRAMES) or idx) - 1
            return True, frame, max(actual_idx, 0)
    return False, None, target_idx


def extract_samples(config: Dict[str, Any], count_override: Optional[int]) -> Path:
    match = config["match"]
    sampling = config.get("sampling", {})
    video_path = resolve_path(match["video_path"])
    output_dir = resolve_path(match["interim_dir"]) / "sample_frames"
    output_dir.mkdir(parents=True, exist_ok=True)

    probe = load_probe(config)
    probed_frame_count, probed_fps = video_info_from_probe(probe)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")

    frame_count = probed_frame_count or int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fps = probed_fps or float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    count = count_override or int(sampling.get("sample_frame_count") or 36)
    jpg_quality = int(sampling.get("jpg_quality") or 92)
    indices = frame_indices(frame_count, count)

    manifest_path = output_dir / "sample_frames.csv"
    rows: List[Dict[str, Any]] = []

    for sample_no, target_idx in enumerate(tqdm(indices, desc="sampling frames"), start=1):
        ok, frame, actual_idx = seek_frame(cap, target_idx)
        timestamp_sec = actual_idx / fps if fps > 0 else None
        filename = f"sample_{sample_no:03d}_frame_{actual_idx:08d}.jpg"
        frame_path = output_dir / filename

        if ok and frame is not None:
            cv2.imwrite(str(frame_path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), jpg_quality])

        rows.append(
            {
                "sample_no": sample_no,
                "target_frame": target_idx,
                "actual_frame": actual_idx,
                "timestamp_sec": round(timestamp_sec, 3) if timestamp_sec is not None else "",
                "timestamp": format_timestamp(timestamp_sec),
                "saved": bool(ok),
                "path": str(frame_path if ok else ""),
            }
        )

    cap.release()

    with manifest_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"sample_dir: {output_dir}")
    print(f"manifest: {manifest_path}")
    print(f"saved_frames: {sum(1 for row in rows if row['saved'])}/{len(rows)}")
    return manifest_path


def format_timestamp(seconds: Optional[float]) -> str:
    if seconds is None:
        return ""
    total = int(round(seconds))
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        default="configs/match_red_mvp.yaml",
        help="Path to the match config YAML.",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="Override the number of sample frames.",
    )
    args = parser.parse_args()

    config_path = resolve_path(args.config)
    config = load_config(config_path)
    extract_samples(config, args.count)


if __name__ == "__main__":
    main()
