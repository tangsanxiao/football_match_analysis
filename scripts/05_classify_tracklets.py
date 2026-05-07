#!/usr/bin/env python3
"""Classify person tracklets by dominant clothing color and analyze-team candidacy."""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Dict, Iterable, List, Tuple, Union

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


def read_frame(video_path: Path, frame_idx: int) -> np.ndarray:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")
    try:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(frame_idx))
        ok, frame = cap.read()
        if not ok or frame is None:
            raise RuntimeError(f"Could not read frame: {frame_idx}")
        return frame
    finally:
        cap.release()


def torso_crop(frame: np.ndarray, row: pd.Series) -> np.ndarray:
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = [float(row[key]) for key in ("x1", "y1", "x2", "y2")]
    bw = max(1.0, x2 - x1)
    bh = max(1.0, y2 - y1)
    # Use a tight central torso box to reduce grass/background and leg/boot bias.
    cx1 = int(round(x1 + 0.18 * bw))
    cx2 = int(round(x1 + 0.82 * bw))
    cy1 = int(round(y1 + 0.16 * bh))
    cy2 = int(round(y1 + 0.62 * bh))
    cx1 = max(0, min(w - 1, cx1))
    cx2 = max(cx1 + 1, min(w, cx2))
    cy1 = max(0, min(h - 1, cy1))
    cy2 = max(cy1 + 1, min(h, cy2))
    return frame[cy1:cy2, cx1:cx2].copy()


def color_fractions(crop: np.ndarray) -> Dict[str, float]:
    if crop.size == 0:
        return empty_fractions()

    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    h = hsv[:, :, 0]
    s = hsv[:, :, 1]
    v = hsv[:, :, 2]
    total = float(h.size)
    if total <= 0:
        return empty_fractions()

    masks = {
        # Red shirts wrap around HSV hue 0/179. Purple goalkeeper kits in this
        # clip sit mostly around 130-169, so keep that separate from red.
        "pink_red": ((h <= 8) | (h >= 170)) & (s >= 45) & (v >= 70),
        "purple": (h >= 130) & (h < 170) & (s >= 45) & (v >= 55),
        "orange": (h > 8) & (h <= 22) & (s >= 55) & (v >= 70),
        "yellow": (h > 22) & (h <= 38) & (s >= 45) & (v >= 85),
        "green": (h > 38) & (h <= 85) & (s >= 45) & (v >= 60),
        "blue": (h > 85) & (h <= 135) & (s >= 35) & (v >= 45),
        "white": (s <= 38) & (v >= 145),
        "dark": v <= 70,
    }
    return {name: round(float(mask.sum()) / total, 5) for name, mask in masks.items()}


def empty_fractions() -> Dict[str, float]:
    return {name: 0.0 for name in ["pink_red", "purple", "orange", "yellow", "green", "blue", "white", "dark"]}


def aggregate_fractions(items: List[Dict[str, float]]) -> Dict[str, float]:
    if not items:
        return empty_fractions()
    keys = items[0].keys()
    return {key: round(float(np.mean([item[key] for item in items])), 5) for key in keys}


def primary_color(fractions: Dict[str, float]) -> str:
    # Prefer team-signal colors over background-like green when they are present.
    priority = ["pink_red", "yellow", "orange", "purple", "blue", "white", "dark", "green"]
    meaningful = {key: fractions.get(key, 0.0) for key in priority}
    color, value = max(meaningful.items(), key=lambda item: item[1])
    if value < 0.08:
        return "unknown"
    if color == "green" and (fractions.get("pink_red", 0) > 0.05 or fractions.get("yellow", 0) > 0.05):
        if fractions.get("pink_red", 0) >= fractions.get("yellow", 0):
            return "pink_red"
        return "yellow"
    return color


def analyze_team_kit(config: Dict) -> Dict[str, List[str]]:
    team_key = str(config.get("teams", {}).get("analyze_team", "red"))
    team = config.get("teams", {}).get(team_key, {})
    kit = team.get("kit", {})
    return {
        "field_colors": list(kit.get("field_colors") or ["pink_red"]),
        "goalkeeper_colors": list(kit.get("goalkeeper_colors") or ["yellow"]),
    }


def analyze_team_candidate(row: pd.Series, kit: Dict[str, List[str]]) -> Tuple[str, float, str]:
    color = row["primary_color"]
    mean_x = float(row["mean_x"])
    mean_y = float(row["mean_y"])
    frames = int(row["frames"])
    frame_score = min(1.0, frames / 30.0)
    field_colors = set(kit["field_colors"])
    goalkeeper_colors = set(kit["goalkeeper_colors"])

    if color in goalkeeper_colors and mean_x <= 3.5 and 7.0 <= mean_y <= 14.0:
        return "analyze_goalkeeper_candidate", round(0.75 + 0.25 * frame_score, 3), f"{color} goalkeeper kit near own goal"

    if color in field_colors and -0.5 <= mean_y <= 20.8:
        edge_penalty = 0.2 if mean_y > 20.2 else 0.0
        return "analyze_field_candidate", round(max(0.35, 0.72 + 0.2 * frame_score - edge_penalty), 3), f"{color} field-player kit"

    if color == "yellow" and mean_y >= 18.8:
        return "referee_or_sideline_candidate", round(0.25 + 0.25 * frame_score, 3), "yellow but near touchline"

    if color == "purple":
        return "opponent_goalkeeper_or_other", round(0.1 + 0.2 * frame_score, 3), "purple kit"

    if mean_y > 20.8:
        return "sideline_candidate", round(0.2 + 0.2 * frame_score, 3), "outside or very near touchline"

    return "other_or_opponent", round(0.1 + 0.2 * frame_score, 3), f"primary_color={color}"


def sample_rows(track_rows: pd.DataFrame, samples_per_track: int) -> pd.DataFrame:
    if len(track_rows) <= samples_per_track:
        return track_rows
    sorted_rows = track_rows.sort_values("conf", ascending=False).head(max(samples_per_track * 3, samples_per_track))
    return sorted_rows.sort_values("timestamp_sec").iloc[
        np.linspace(0, len(sorted_rows) - 1, samples_per_track).round().astype(int)
    ]


def classify_tracklets(config: Dict, detections_dir: Path, samples_per_track: int) -> pd.DataFrame:
    video_path = resolve_path(config["match"]["video_path"])
    kit = analyze_team_kit(config)
    tracklets = pd.read_csv(detections_dir / "tracklets.csv")
    tracks = pd.read_csv(detections_dir / "tracks_in_play.csv")
    people = tracks[tracks["class_name"] == "person"].copy()

    rows = []
    frame_cache: Dict[int, np.ndarray] = {}
    for tracklet in tracklets.itertuples(index=False):
        group = people[people["track_id"] == tracklet.track_id]
        fractions_by_sample: List[Dict[str, float]] = []
        for _, det in sample_rows(group, samples_per_track).iterrows():
            frame_idx = int(det["frame_idx"])
            if frame_idx not in frame_cache:
                frame_cache[frame_idx] = read_frame(video_path, frame_idx)
            crop = torso_crop(frame_cache[frame_idx], det)
            fractions_by_sample.append(color_fractions(crop))

        fractions = aggregate_fractions(fractions_by_sample)
        row = tracklet._asdict()
        row.update({f"color_{key}": value for key, value in fractions.items()})
        row["primary_color"] = primary_color(fractions)
        rows.append(row)

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    candidate = out.apply(lambda row: analyze_team_candidate(row, kit), axis=1, result_type="expand")
    candidate.columns = ["candidate_type", "candidate_score", "candidate_reason"]
    out = pd.concat([out, candidate], axis=1)
    return out.sort_values(["candidate_score", "frames", "mean_conf"], ascending=False)


def crop_for_contact_sheet(video_path: Path, row: pd.Series, pad: int = 28) -> np.ndarray:
    frame = read_frame(video_path, int(row["sample_frame"]))
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = [int(round(row[key])) for key in ("sample_x1", "sample_y1", "sample_x2", "sample_y2")]
    x1 = max(0, x1 - pad)
    y1 = max(0, y1 - pad)
    x2 = min(w, x2 + pad)
    y2 = min(h, y2 + pad)
    return frame[y1:y2, x1:x2].copy()


def make_tile(crop: np.ndarray, label: str, size: Tuple[int, int]) -> np.ndarray:
    tile_w, tile_h = size
    tile = np.full((tile_h, tile_w, 3), 250, dtype=np.uint8)
    if crop.size:
        h, w = crop.shape[:2]
        scale = min(tile_w / max(w, 1), (tile_h - 52) / max(h, 1))
        resized = cv2.resize(crop, (max(1, int(w * scale)), max(1, int(h * scale))))
        rh, rw = resized.shape[:2]
        x = (tile_w - rw) // 2
        y = 8
        tile[y : y + rh, x : x + rw] = resized

    cv2.rectangle(tile, (0, tile_h - 44), (tile_w, tile_h), (20, 90, 75), -1)
    cv2.putText(tile, label[:52], (7, tile_h - 24), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1, cv2.LINE_AA)
    return tile


def create_contact_sheet(config: Dict, candidates: pd.DataFrame, output_path: Path, top_n: int) -> None:
    if candidates.empty:
        return
    video_path = resolve_path(config["match"]["video_path"])
    rows = candidates.head(top_n)
    tile_size = (280, 280)
    cols = 5
    sheet_rows = math.ceil(len(rows) / cols)
    sheet = np.full((sheet_rows * tile_size[1], cols * tile_size[0], 3), 245, dtype=np.uint8)

    for idx, (_, row) in enumerate(rows.iterrows()):
        crop = crop_for_contact_sheet(video_path, row)
        label = f"id {int(row.track_id)} {row.primary_color} {row.candidate_type} {row.candidate_score:.2f}"
        tile = make_tile(crop, label, tile_size)
        r, c = divmod(idx, cols)
        y, x = r * tile_size[1], c * tile_size[0]
        sheet[y : y + tile_size[1], x : x + tile_size[0]] = tile

    cv2.imwrite(str(output_path), sheet, [int(cv2.IMWRITE_JPEG_QUALITY), 92])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/match_red_mvp.yaml")
    parser.add_argument("--detections-dir", required=True)
    parser.add_argument("--samples-per-track", type=int, default=5)
    parser.add_argument("--top-n", type=int, default=30)
    args = parser.parse_args()

    config = load_yaml(resolve_path(args.config))
    detections_dir = resolve_path(args.detections_dir)
    candidates = classify_tracklets(config, detections_dir, args.samples_per_track)
    output_csv = detections_dir / "tracklet_color_candidates.csv"
    candidates.to_csv(output_csv, index=False)
    contact_sheet = detections_dir / "analyze_candidate_contact_sheet.jpg"
    create_contact_sheet(config, candidates, contact_sheet, args.top_n)

    print(f"candidates_csv: {output_csv}")
    print(f"contact_sheet: {contact_sheet}")
    print(f"candidates: {len(candidates)}")
    if not candidates.empty:
        cols = [
            "track_id",
            "frames",
            "first_ts",
            "last_ts",
            "mean_x",
            "mean_y",
            "primary_color",
            "candidate_type",
            "candidate_score",
            "candidate_reason",
        ]
        print(candidates[cols].head(30).to_string(index=False))


if __name__ == "__main__":
    main()
