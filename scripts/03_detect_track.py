#!/usr/bin/env python3
"""Run YOLO detection/tracking on a short match segment."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

import cv2
import numpy as np
import yaml
from tqdm import tqdm

try:
    from analysis_detection_filters import filter_static_ball_false_positives
except ModuleNotFoundError:
    from scripts.analysis_detection_filters import filter_static_ball_false_positives


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def resolve_path(path: Union[str, Path]) -> Path:
    path = Path(path)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def load_yaml(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def optional_calibration(config: Dict[str, Any]) -> Optional[np.ndarray]:
    calibration_path = resolve_path(config["match"]["interim_dir"]) / "calibration" / "calibration.json"
    if not calibration_path.exists():
        return None
    calibration = load_json(calibration_path)
    return np.array(calibration["homography_image_to_field"], dtype=np.float64)


def image_to_field(homography: Optional[np.ndarray], point: Tuple[float, float]) -> Tuple[Optional[float], Optional[float]]:
    if homography is None:
        return None, None
    source = np.array([[point]], dtype=np.float64)
    mapped = cv2.perspectiveTransform(source, homography)[0, 0]
    return float(mapped[0]), float(mapped[1])


def inside_play_area(
    config: Dict[str, Any],
    class_id: int,
    field_x: Optional[float],
    field_y: Optional[float],
) -> Optional[bool]:
    if field_x is None or field_y is None:
        return None

    field = config.get("field", {})
    detection = config.get("detection", {})
    field_filter = detection.get("field_filter", {})
    length_m = float(field.get("length_m", 40.0))
    width_m = float(field.get("width_m", 20.0))
    if not field_filter.get("enabled", True):
        margin = max(
            float(field_filter.get("person_margin_m", 0.0)),
            float(field_filter.get("ball_margin_m", 0.0)),
        )
    elif class_id == 0:
        margin = float(field_filter.get("person_margin_m", 1.5))
    else:
        margin = float(field_filter.get("ball_margin_m", 0.2))

    return -margin <= field_x <= length_m + margin and -margin <= field_y <= width_m + margin


def frame_indices(start_frame: int, end_frame: int, stride: int, max_frames: Optional[int]) -> List[int]:
    indices = list(range(start_frame, end_frame + 1, stride))
    if max_frames is not None and max_frames > 0:
        indices = indices[:max_frames]
    return indices


def class_filter(value: Optional[Sequence[int]]) -> Optional[List[int]]:
    if value is None:
        return None
    return [int(item) for item in value]


def bbox_anchor(class_id: int, xyxy: Sequence[float]) -> Tuple[str, Tuple[float, float]]:
    x1, y1, x2, y2 = [float(value) for value in xyxy]
    if class_id == 0:
        return "bottom_center", ((x1 + x2) / 2.0, y2)
    return "center", ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def format_timestamp(seconds: float) -> str:
    total = int(round(seconds))
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def output_slug(start_sec: float, duration_sec: float, sample_fps: float) -> str:
    return f"segment_{int(round(start_sec)):04d}_{int(round(duration_sec)):04d}_{sample_fps:g}fps".replace(".", "p")


def get_video_info(config: Dict[str, Any]) -> Tuple[float, int]:
    probe_path = resolve_path(config["match"]["interim_dir"]) / "video_probe.json"
    if probe_path.exists():
        probe = load_json(probe_path)
        opencv = probe.get("opencv", {})
        fps = float(opencv.get("fps") or 0.0)
        frame_count = int(opencv.get("frame_count") or 0)
        if fps > 0 and frame_count > 0:
            return fps, frame_count

    video_path = resolve_path(config["match"]["video_path"])
    cap = cv2.VideoCapture(str(video_path))
    try:
        if not cap.isOpened():
            raise RuntimeError(f"Could not open video: {video_path}")
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    finally:
        cap.release()
    return fps, frame_count


def extract_rows(
    result: Any,
    frame_idx: int,
    timestamp_sec: float,
    homography: Optional[np.ndarray],
    config: Dict[str, Any],
) -> List[Dict[str, Any]]:
    names = result.names
    boxes = result.boxes
    if boxes is None or len(boxes) == 0:
        return []

    xyxy = boxes.xyxy.cpu().numpy()
    conf = boxes.conf.cpu().numpy()
    cls = boxes.cls.cpu().numpy().astype(int)
    ids = boxes.id.cpu().numpy().astype(int) if boxes.id is not None else [None] * len(xyxy)

    rows: List[Dict[str, Any]] = []
    for det_no, (box, score, class_id, track_id) in enumerate(zip(xyxy, conf, cls, ids), start=1):
        anchor_name, anchor = bbox_anchor(int(class_id), box)
        field_x, field_y = image_to_field(homography, anchor)
        in_play_area = inside_play_area(config, int(class_id), field_x, field_y)
        x1, y1, x2, y2 = [float(value) for value in box]
        rows.append(
            {
                "frame_idx": frame_idx,
                "timestamp_sec": round(timestamp_sec, 3),
                "timestamp": format_timestamp(timestamp_sec),
                "det_no": det_no,
                "track_id": "" if track_id is None else int(track_id),
                "class_id": int(class_id),
                "class_name": names.get(int(class_id), str(class_id)) if isinstance(names, dict) else str(class_id),
                "conf": round(float(score), 5),
                "x1": round(x1, 2),
                "y1": round(y1, 2),
                "x2": round(x2, 2),
                "y2": round(y2, 2),
                "cx": round((x1 + x2) / 2.0, 2),
                "cy": round((y1 + y2) / 2.0, 2),
                "anchor": anchor_name,
                "anchor_x": round(anchor[0], 2),
                "anchor_y": round(anchor[1], 2),
                "field_x_m": "" if field_x is None else round(field_x, 3),
                "field_y_m": "" if field_y is None else round(field_y, 3),
                "inside_play_area": "" if in_play_area is None else bool(in_play_area),
            }
        )
    return rows


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def summarize(rows: List[Dict[str, Any]], processed_frames: int, args: argparse.Namespace) -> Dict[str, Any]:
    by_class = Counter(row["class_name"] for row in rows)
    frames_by_class: Dict[str, set] = defaultdict(set)
    track_ids_by_class: Dict[str, set] = defaultdict(set)
    in_play_rows = [row for row in rows if row.get("inside_play_area") is True]
    for row in rows:
        frames_by_class[row["class_name"]].add(row["frame_idx"])
        if row["track_id"] != "":
            track_ids_by_class[row["class_name"]].add(row["track_id"])
    in_play_by_class = Counter(row["class_name"] for row in in_play_rows)
    in_play_frames_by_class: Dict[str, set] = defaultdict(set)
    for row in in_play_rows:
        in_play_frames_by_class[row["class_name"]].add(row["frame_idx"])

    return {
        "segment": {
            "start_sec": args.start_sec,
            "duration_sec": args.duration_sec,
            "sample_fps": args.sample_fps,
            "processed_frames": processed_frames,
        },
        "model": args.model,
        "tracker": args.tracker,
        "imgsz": args.imgsz,
        "conf": args.conf,
        "iou": args.iou,
        "class_counts": dict(by_class),
        "in_play_class_counts": dict(in_play_by_class),
        "frame_coverage_by_class": {
            class_name: round(len(frames) / processed_frames, 4) if processed_frames else 0.0
            for class_name, frames in frames_by_class.items()
        },
        "in_play_frame_coverage_by_class": {
            class_name: round(len(frames) / processed_frames, 4) if processed_frames else 0.0
            for class_name, frames in in_play_frames_by_class.items()
        },
        "track_count_by_class": {
            class_name: len(track_ids) for class_name, track_ids in track_ids_by_class.items()
        },
    }


def run_detection(config: Dict[str, Any], args: argparse.Namespace) -> Path:
    try:
        from ultralytics import YOLO
    except ModuleNotFoundError as exc:
        raise SystemExit("Missing ultralytics. Install with: .venv/bin/python -m pip install -r requirements-detect.txt") from exc

    video_path = resolve_path(args.video or config["match"]["video_path"])
    output_root = resolve_path(config["match"]["interim_dir"]) / "detections" / output_slug(
        args.start_sec, args.duration_sec, args.sample_fps
    )
    annotated_dir = output_root / "annotated_frames"
    output_root.mkdir(parents=True, exist_ok=True)
    annotated_dir.mkdir(parents=True, exist_ok=True)

    video_fps, frame_count = get_video_info(config)
    if video_fps <= 0:
        raise RuntimeError("Video FPS is unknown.")

    start_frame = int(round(args.start_sec * video_fps))
    end_frame = min(frame_count - 1, int(round((args.start_sec + args.duration_sec) * video_fps)))
    stride = max(1, int(round(video_fps / args.sample_fps)))
    indices = frame_indices(start_frame, end_frame, stride, args.max_frames)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")

    model = YOLO(args.model)
    homography = optional_calibration(config)
    rows: List[Dict[str, Any]] = []
    processed_frames = 0

    try:
        for sample_no, frame_idx in enumerate(tqdm(indices, desc="detecting/tracking"), start=1):
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue

            timestamp_sec = frame_idx / video_fps
            results = model.track(
                frame,
                persist=True,
                tracker=args.tracker,
                imgsz=args.imgsz,
                conf=args.conf,
                iou=args.iou,
                classes=class_filter(args.classes),
                device=args.device,
                verbose=False,
            )
            if not results:
                continue
            result = results[0]
            rows.extend(extract_rows(result, frame_idx, timestamp_sec, homography, config))

            if args.save_annotated_every > 0 and sample_no % args.save_annotated_every == 0:
                annotated = result.plot()
                annotated_name = f"frame_{frame_idx:08d}_{format_timestamp(timestamp_sec).replace(':', '')}.jpg"
                cv2.imwrite(str(annotated_dir / annotated_name), annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 90])

            processed_frames += 1
    finally:
        cap.release()

    rows, filter_summary = filter_static_ball_false_positives(rows, config)

    tracks_csv = output_root / "tracks.csv"
    tracks_in_play_csv = output_root / "tracks_in_play.csv"
    summary_json = output_root / "summary.json"
    write_csv(tracks_csv, rows)
    write_csv(tracks_in_play_csv, [row for row in rows if row.get("inside_play_area") is True])
    summary = summarize(rows, processed_frames, args)
    summary.update(
        {
            "video_path": str(video_path),
            "output_dir": str(output_root),
            "tracks_csv": str(tracks_csv),
            "tracks_in_play_csv": str(tracks_in_play_csv),
            "annotated_dir": str(annotated_dir),
            "filters": {
                "static_ball_false_positive": filter_summary,
            },
        }
    )
    with summary_json.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, ensure_ascii=False)

    print(f"output_dir: {output_root}")
    print(f"tracks_csv: {tracks_csv}")
    print(f"tracks_in_play_csv: {tracks_in_play_csv}")
    print(f"summary_json: {summary_json}")
    print(f"processed_frames: {processed_frames}")
    print(f"detections: {len(rows)}")
    print(f"class_counts: {summary['class_counts']}")
    print(f"in_play_class_counts: {summary['in_play_class_counts']}")
    print(f"frame_coverage_by_class: {summary['frame_coverage_by_class']}")
    print(f"in_play_frame_coverage_by_class: {summary['in_play_frame_coverage_by_class']}")
    print(f"track_count_by_class: {summary['track_count_by_class']}")
    if filter_summary.get("removed_rows"):
        print(f"static_ball_false_positive_removed_rows: {filter_summary['removed_rows']}")
    return output_root


def parser_with_config_defaults(config: Dict[str, Any]) -> argparse.ArgumentParser:
    detection = config.get("detection", {})
    smoke = detection.get("smoke_test", {})
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/match_red_mvp.yaml")
    parser.add_argument("--video", default=None)
    parser.add_argument("--model", default=detection.get("model", "yolo11n.pt"))
    parser.add_argument("--tracker", default=detection.get("tracker", "bytetrack.yaml"))
    parser.add_argument("--classes", nargs="*", type=int, default=detection.get("classes", [0, 32]))
    parser.add_argument("--conf", type=float, default=float(detection.get("conf", 0.18)))
    parser.add_argument("--iou", type=float, default=float(detection.get("iou", 0.5)))
    parser.add_argument("--imgsz", type=int, default=int(detection.get("imgsz", 1280)))
    parser.add_argument("--sample-fps", type=float, default=float(detection.get("sample_fps", 2.0)))
    parser.add_argument("--start-sec", type=float, default=float(smoke.get("start_sec", 120.0)))
    parser.add_argument("--duration-sec", type=float, default=float(smoke.get("duration_sec", 10.0)))
    parser.add_argument("--max-frames", type=int, default=smoke.get("max_frames", None))
    parser.add_argument("--device", default=None, help="Ultralytics device, e.g. cpu, mps, 0.")
    parser.add_argument("--save-annotated-every", type=int, default=1)
    return parser


def main() -> None:
    # allow_abbrev=False prevents argparse from treating --conf as a prefix
    # match for --config (silently swapping conf's value into config's slot).
    pre_parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    pre_parser.add_argument("--config", default="configs/match_red_mvp.yaml")
    pre_args, _ = pre_parser.parse_known_args()
    config_path = resolve_path(pre_args.config)
    config = load_yaml(config_path)

    parser = parser_with_config_defaults(config)
    args = parser.parse_args()
    run_detection(config, args)


if __name__ == "__main__":
    main()
