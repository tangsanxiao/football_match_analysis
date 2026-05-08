#!/usr/bin/env python3
"""Prepare consecutive key frames for manual ball-position review."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

import cv2
import yaml

try:
    from analysis_report_runs import latest_report_record
except ModuleNotFoundError:
    from scripts.analysis_report_runs import latest_report_record


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


def read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def format_ts(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    return f"{total // 60:02d}:{total % 60:02d}"


def output_slug(start_sec: float, duration_sec: float, sample_fps: float) -> str:
    return f"segment_{int(round(start_sec)):04d}_{int(round(duration_sec)):04d}_{sample_fps:g}fps".replace(".", "p")


def duration_from_probe(config: Dict[str, Any]) -> float:
    probe_path = resolve_path(config["match"]["interim_dir"]) / "video_probe.json"
    if not probe_path.exists():
        return 580.0
    with probe_path.open("r", encoding="utf-8") as handle:
        probe = json.load(handle)
    return float(probe.get("opencv", {}).get("duration_sec") or 580.0)


def default_detections_dir(config: Dict[str, Any]) -> Path:
    sample_fps = float(config.get("detection", {}).get("sample_fps", 2.0))
    return resolve_path(config["match"]["interim_dir"]) / "detections" / output_slug(0.0, duration_from_probe(config), sample_fps)


def unique_frames(track_rows: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    by_frame: Dict[int, Dict[str, Any]] = {}
    for row in track_rows:
        frame_idx = safe_int(row.get("frame_idx"))
        if frame_idx not in by_frame:
            by_frame[frame_idx] = {
                "frame_idx": frame_idx,
                "timestamp_sec": safe_float(row.get("timestamp_sec")),
                "timestamp": row.get("timestamp") or format_ts(safe_float(row.get("timestamp_sec"))),
            }
    return sorted(by_frame.values(), key=lambda item: item["frame_idx"])


def best_ball_by_frame(track_rows: List[Dict[str, str]]) -> Dict[int, Dict[str, Any]]:
    out: Dict[int, Dict[str, Any]] = {}
    for row in track_rows:
        if row.get("class_name") != "sports ball":
            continue
        frame_idx = safe_int(row.get("frame_idx"))
        conf = safe_float(row.get("conf"))
        existing = out.get(frame_idx)
        if existing is None or conf > safe_float(existing.get("conf")):
            out[frame_idx] = {
                "frame_idx": frame_idx,
                "conf": conf,
                "cx": safe_float(row.get("cx")),
                "cy": safe_float(row.get("cy")),
                "field_x_m": safe_float(row.get("field_x_m")),
                "field_y_m": safe_float(row.get("field_y_m")),
            }
    return out


def event_timestamps(config: Dict[str, Any]) -> List[float]:
    latest = latest_report_record(config)
    if latest.get("report_dir"):
        rows = read_csv(resolve_path(latest["report_dir"]) / "key_timestamps.csv")
    else:
        rows = read_csv(resolve_path(config["match"]["output_dir"]) / "mvp_initial" / "key_timestamps.csv")
    return [safe_float(row.get("timestamp_sec")) for row in rows if row.get("timestamp_sec")]


def nearest_frame_indices(frames: List[Dict[str, Any]], target_ts: float, window_s: float) -> List[int]:
    return [
        int(item["frame_idx"])
        for item in frames
        if abs(float(item["timestamp_sec"]) - target_ts) <= window_s
    ]


def adjacent_frame_indices(frames: List[Dict[str, Any]], frame_idx: int, radius: int) -> List[int]:
    index_by_frame = {int(item["frame_idx"]): pos for pos, item in enumerate(frames)}
    pos = index_by_frame.get(frame_idx)
    if pos is None:
        return []
    start = max(0, pos - radius)
    end = min(len(frames), pos + radius + 1)
    return [int(item["frame_idx"]) for item in frames[start:end]]


def select_review_frames(
    frames: List[Dict[str, Any]],
    ball_by_frame: Dict[int, Dict[str, Any]],
    event_ts: Iterable[float],
    max_frames: int,
    interval_sec: float,
) -> List[Dict[str, Any]]:
    selected: Dict[int, str] = {}

    for frame_idx in sorted(ball_by_frame):
        for nearby in adjacent_frame_indices(frames, frame_idx, radius=2):
            selected.setdefault(nearby, "ball_detection_window")

    for timestamp_sec in event_ts:
        for frame_idx in nearest_frame_indices(frames, timestamp_sec, window_s=1.5):
            selected.setdefault(frame_idx, "key_timestamp_window")

    last_uniform = -1e9
    for item in frames:
        timestamp_sec = float(item["timestamp_sec"])
        if timestamp_sec - last_uniform >= interval_sec:
            selected.setdefault(int(item["frame_idx"]), "uniform_context")
            last_uniform = timestamp_sec

    rows_by_frame = {int(item["frame_idx"]): item for item in frames}
    selected_rows = [
        {**rows_by_frame[frame_idx], "reason": reason}
        for frame_idx, reason in selected.items()
        if frame_idx in rows_by_frame
    ]
    selected_rows.sort(key=lambda item: (float(item["timestamp_sec"]), int(item["frame_idx"])))
    if len(selected_rows) <= max_frames:
        return selected_rows

    important = [item for item in selected_rows if item["reason"] != "uniform_context"]
    uniform = [item for item in selected_rows if item["reason"] == "uniform_context"]
    if len(important) >= max_frames:
        step = max(1, len(important) // max_frames)
        return important[::step][:max_frames]
    remaining = max_frames - len(important)
    step = max(1, len(uniform) // max(1, remaining))
    return sorted((important + uniform[::step][:remaining]), key=lambda item: (float(item["timestamp_sec"]), int(item["frame_idx"])))


def extract_frame_image(
    cap: cv2.VideoCapture,
    frame_idx: int,
    output_path: Path,
    ball: Optional[Dict[str, Any]],
    label: str,
    max_width: int,
) -> Dict[str, Any]:
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
    ok, frame = cap.read()
    if not ok or frame is None:
        raise RuntimeError(f"Could not read frame {frame_idx}")

    source_h, source_w = frame.shape[:2]
    scale = 1.0
    if source_w > max_width:
        scale = max_width / float(source_w)
        frame = cv2.resize(frame, (max_width, int(source_h * scale)))

    if ball:
        cx = int(round(float(ball["cx"]) * scale))
        cy = int(round(float(ball["cy"]) * scale))
        cv2.circle(frame, (cx, cy), 18, (0, 255, 255), 3)
        cv2.circle(frame, (cx, cy), 4, (0, 0, 255), -1)
        cv2.rectangle(frame, (max(0, cx - 110), max(0, cy - 44)), (min(frame.shape[1] - 1, cx + 190), max(22, cy - 8)), (20, 90, 75), -1)
        cv2.putText(frame, label[:38], (max(4, cx - 100), max(18, cy - 18)), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (255, 255, 255), 1, cv2.LINE_AA)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    return {
        "source_width": source_w,
        "source_height": source_h,
        "image_width": int(frame.shape[1]),
        "image_height": int(frame.shape[0]),
        "review_to_source_scale_x": round(source_w / float(frame.shape[1]), 6),
        "review_to_source_scale_y": round(source_h / float(frame.shape[0]), 6),
    }


def create_ball_review(config: Dict[str, Any], detections_dir: Path, max_frames: int, interval_sec: float, max_width: int) -> Path:
    tracks_path = detections_dir / "tracks_in_play.csv"
    if not tracks_path.exists():
        raise FileNotFoundError(f"Missing tracks_in_play.csv: {tracks_path}")
    track_rows = read_csv(tracks_path)
    frames = unique_frames(track_rows)
    if not frames:
        raise RuntimeError("No sampled frames are available for ball review.")
    ball_by_frame = best_ball_by_frame(track_rows)
    selected = select_review_frames(frames, ball_by_frame, event_timestamps(config), max_frames=max_frames, interval_sec=interval_sec)

    review_root = resolve_path(config["match"].get("review_dir", "review")) / "ball_review"
    frames_dir = review_root / "frames"
    video_path = resolve_path(config["match"]["video_path"])
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")

    items: List[Dict[str, Any]] = []
    csv_rows: List[Dict[str, Any]] = []
    for index, item in enumerate(selected, start=1):
        review_id = f"ball_{index:04d}"
        frame_idx = int(item["frame_idx"])
        timestamp_sec = float(item["timestamp_sec"])
        ball = ball_by_frame.get(frame_idx)
        image_path = frames_dir / f"{review_id}.jpg"
        label = f"ball conf={float(ball['conf']):.2f}" if ball else "ball not detected"
        image_meta = extract_frame_image(cap, frame_idx, image_path, ball, label, max_width=max_width)
        system_ball = {
            "visible": bool(ball),
            "confidence": round(float(ball["conf"]), 3) if ball else 0.0,
            "source_image_xy": [round(float(ball["cx"]), 1), round(float(ball["cy"]), 1)] if ball else None,
            "review_image_xy": [
                round(float(ball["cx"]) / image_meta["review_to_source_scale_x"], 1),
                round(float(ball["cy"]) / image_meta["review_to_source_scale_y"], 1),
            ]
            if ball
            else None,
            "field_xy": [round(float(ball["field_x_m"]), 2), round(float(ball["field_y_m"]), 2)] if ball else None,
        }
        review_item = {
            "review_id": review_id,
            "type": "ball_position",
            "reason": item["reason"],
            "frame_idx": frame_idx,
            "timestamp_sec": round(timestamp_sec, 3),
            "timestamp": item.get("timestamp") or format_ts(timestamp_sec),
            "frame_image": project_relative(image_path),
            **image_meta,
            "system_labels": {"ball": system_ball},
            "human_review": {
                "status": "pending",
                "ball_visible": None,
                "review_image_xy": None,
                "source_image_xy": None,
                "note": "",
            },
        }
        items.append(review_item)
        csv_rows.append(
            {
                "review_id": review_id,
                "frame_idx": frame_idx,
                "timestamp_sec": round(timestamp_sec, 3),
                "timestamp": review_item["timestamp"],
                "reason": item["reason"],
                "system_ball_visible": system_ball["visible"],
                "system_ball_confidence": system_ball["confidence"],
                "frame_image": project_relative(image_path),
            }
        )
    cap.release()

    manifest = {
        "status": "pending",
        "match_id": config["match"].get("id"),
        "source_detections_dir": project_relative(detections_dir),
        "review_item_count": len(items),
        "items": items,
    }
    manifest_path = review_root / "ball_review_manifest.yaml"
    write_yaml(manifest_path, manifest)

    items_csv = review_root / "ball_review_items.csv"
    with items_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(csv_rows[0].keys()) if csv_rows else ["review_id"])
        writer.writeheader()
        writer.writerows(csv_rows)
    return manifest_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--detections-dir", default=None)
    parser.add_argument("--max-frames", type=int, default=180)
    parser.add_argument("--interval-sec", type=float, default=4.0)
    parser.add_argument("--max-width", type=int, default=1600)
    args = parser.parse_args()

    config = load_yaml(resolve_path(args.config))
    detections_dir = resolve_path(args.detections_dir) if args.detections_dir else default_detections_dir(config)
    manifest_path = create_ball_review(config, detections_dir, args.max_frames, args.interval_sec, args.max_width)
    print("\nBall review package is ready.")
    print(f"ball_review_manifest: {manifest_path}")
    print(f"review_url: http://127.0.0.1:8765/")


if __name__ == "__main__":
    main()
