#!/usr/bin/env python3
"""Summarize person tracklets and create a contact sheet for identity binding."""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Dict, List, Tuple, Union

import cv2
import numpy as np
import pandas as pd
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def resolve_path(path: Union[str, Path]) -> Path:
    path = Path(path)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def load_yaml(path: Path) -> Dict:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def summarize_tracklets(detections_dir: Path, min_frames: int) -> pd.DataFrame:
    tracks_path = detections_dir / "tracks_in_play.csv"
    if not tracks_path.exists():
        raise FileNotFoundError(f"Missing tracks_in_play.csv: {tracks_path}")

    df = pd.read_csv(tracks_path)
    people = df[df["class_name"] == "person"].copy()
    if people.empty:
        return pd.DataFrame()

    grouped = people.groupby("track_id")
    summary = grouped.agg(
        frames=("frame_idx", "nunique"),
        first_frame=("frame_idx", "min"),
        last_frame=("frame_idx", "max"),
        first_ts=("timestamp_sec", "min"),
        last_ts=("timestamp_sec", "max"),
        mean_conf=("conf", "mean"),
        max_conf=("conf", "max"),
        mean_x=("field_x_m", "mean"),
        mean_y=("field_y_m", "mean"),
        min_x=("field_x_m", "min"),
        max_x=("field_x_m", "max"),
        min_y=("field_y_m", "min"),
        max_y=("field_y_m", "max"),
    )
    summary = summary.reset_index()

    bbox_rows = []
    for track_id, group in grouped:
        best = group.sort_values("conf", ascending=False).iloc[0]
        bbox_rows.append(
            {
                "track_id": track_id,
                "mean_bbox_w": float((group["x2"] - group["x1"]).mean()),
                "mean_bbox_h": float((group["y2"] - group["y1"]).mean()),
                "sample_frame": int(best["frame_idx"]),
                "sample_timestamp": float(best["timestamp_sec"]),
                "sample_conf": float(best["conf"]),
                "sample_x1": float(best["x1"]),
                "sample_y1": float(best["y1"]),
                "sample_x2": float(best["x2"]),
                "sample_y2": float(best["y2"]),
            }
        )
    bbox_stats = pd.DataFrame(bbox_rows)

    summary = summary.merge(bbox_stats, on="track_id", how="left")
    summary["duration_s"] = summary["last_ts"] - summary["first_ts"]
    summary["span_x_m"] = summary["max_x"] - summary["min_x"]
    summary["span_y_m"] = summary["max_y"] - summary["min_y"]
    summary = summary[summary["frames"] >= min_frames]
    return summary.sort_values(["frames", "duration_s", "mean_conf"], ascending=False)


def crop_frame(video_path: Path, frame_idx: int, box: Tuple[float, float, float, float], pad: int = 24) -> np.ndarray:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")
    try:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(frame_idx))
        ok, frame = cap.read()
        if not ok or frame is None:
            raise RuntimeError(f"Could not read frame {frame_idx}")
    finally:
        cap.release()

    h, w = frame.shape[:2]
    x1, y1, x2, y2 = [int(round(value)) for value in box]
    x1 = max(0, x1 - pad)
    y1 = max(0, y1 - pad)
    x2 = min(w, x2 + pad)
    y2 = min(h, y2 + pad)
    return frame[y1:y2, x1:x2].copy()


def make_tile(crop: np.ndarray, label: str, size: Tuple[int, int]) -> np.ndarray:
    tile_w, tile_h = size
    tile = np.full((tile_h, tile_w, 3), 245, dtype=np.uint8)
    if crop.size:
        h, w = crop.shape[:2]
        scale = min(tile_w / max(w, 1), (tile_h - 48) / max(h, 1))
        resized = cv2.resize(crop, (max(1, int(w * scale)), max(1, int(h * scale))))
        rh, rw = resized.shape[:2]
        x = (tile_w - rw) // 2
        y = 8
        tile[y : y + rh, x : x + rw] = resized

    cv2.rectangle(tile, (0, tile_h - 40), (tile_w, tile_h), (20, 90, 75), -1)
    cv2.putText(tile, label, (8, tile_h - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (255, 255, 255), 1, cv2.LINE_AA)
    return tile


def create_contact_sheet(config: Dict, detections_dir: Path, tracklets: pd.DataFrame, top_n: int) -> Path:
    video_path = resolve_path(config["match"]["video_path"])
    output_path = detections_dir / "tracklet_contact_sheet.jpg"
    if tracklets.empty:
        return output_path

    rows = tracklets.head(top_n)
    tile_size = (260, 260)
    cols = 5
    sheet_rows = math.ceil(len(rows) / cols)
    sheet = np.full((sheet_rows * tile_size[1], cols * tile_size[0], 3), 250, dtype=np.uint8)

    for idx, row in enumerate(rows.itertuples(index=False)):
        crop = crop_frame(
            video_path,
            int(row.sample_frame),
            (row.sample_x1, row.sample_y1, row.sample_x2, row.sample_y2),
        )
        label = f"id {int(row.track_id)}  {int(row.frames)}f  {row.first_ts:.0f}-{row.last_ts:.0f}s"
        tile = make_tile(crop, label, tile_size)
        r, c = divmod(idx, cols)
        y, x = r * tile_size[1], c * tile_size[0]
        sheet[y : y + tile_size[1], x : x + tile_size[0]] = tile

    cv2.imwrite(str(output_path), sheet, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/match_red_mvp.yaml")
    parser.add_argument("--detections-dir", required=True)
    parser.add_argument("--min-frames", type=int, default=8)
    parser.add_argument("--top-n", type=int, default=30)
    args = parser.parse_args()

    config = load_yaml(resolve_path(args.config))
    detections_dir = resolve_path(args.detections_dir)
    tracklets = summarize_tracklets(detections_dir, args.min_frames)
    output_csv = detections_dir / "tracklets.csv"
    tracklets.to_csv(output_csv, index=False)
    contact_sheet = create_contact_sheet(config, detections_dir, tracklets, args.top_n)

    print(f"tracklets_csv: {output_csv}")
    print(f"contact_sheet: {contact_sheet}")
    print(f"tracklets: {len(tracklets)}")
    if not tracklets.empty:
        print(tracklets[["track_id", "frames", "first_ts", "last_ts", "mean_x", "mean_y", "mean_conf"]].head(20).to_string(index=False))


if __name__ == "__main__":
    main()
