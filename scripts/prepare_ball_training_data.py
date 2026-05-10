#!/usr/bin/env python3
"""Build a YOLO-format training dataset for a custom ball detector.

Source: existing matches' ``review/ball_review/ball_review_points.csv``,
where ``source=human`` rows are real ground truth.

Status mapping:
    status=marked    + ball_visible=True   → positive (bbox at source_image_x/y)
    status=invisible + ball_visible=False  → negative (no annotation, empty label)
    other rows                              → skipped

Output:
    evals/training/ball_v1/
      data.yaml
      images/{train,val}/<frame_id>.jpg
      labels/{train,val}/<frame_id>.txt
      manifest.yaml

Usage:
    python scripts/prepare_ball_training_data.py \
        --match 中青赛_1_20260506_213657 \
        --output-name ball_v1 \
        --bbox-size 24 \
        --val-split 0.2 \
        --seed 42
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import pandas as pd
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TRAINING_ROOT = PROJECT_ROOT / "evals" / "training"


def resolve_path(path) -> Path:
    p = Path(path).expanduser()
    return p if p.is_absolute() else PROJECT_ROOT / p


def load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as h:
        return yaml.safe_load(h) or {}


def find_match_dir(match_id_or_path: str) -> Path:
    p = Path(match_id_or_path)
    if p.exists():
        return p.resolve()
    candidate = PROJECT_ROOT / "matches" / match_id_or_path
    if candidate.exists():
        return candidate.resolve()
    raise FileNotFoundError(f"Could not resolve match: {match_id_or_path!r}")


def gather_samples(
    match_dir: Path,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Return (rows, video_info). Each row: {frame_idx, status, x_px, y_px}."""
    config = load_yaml(match_dir / "config" / "match.yaml")
    interim = match_dir / "data" / "interim"
    video_path_str = config.get("match", {}).get("video_path")
    if not video_path_str:
        raise ValueError(f"{match_dir.name}: match.video_path not in config")
    video_path = resolve_path(video_path_str)
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    probe_path = interim / "video_probe.json"
    if not probe_path.exists():
        raise FileNotFoundError(f"video_probe.json missing at {probe_path}")
    probe = json.loads(probe_path.read_text(encoding="utf-8"))
    width = int(probe["opencv"]["width"])
    height = int(probe["opencv"]["height"])
    fps = float(probe["opencv"]["fps"])

    review_csv = match_dir / "review" / "ball_review" / "ball_review_points.csv"
    if not review_csv.exists():
        raise FileNotFoundError(f"Ball review file missing: {review_csv}")

    df = pd.read_csv(review_csv)
    df = df[df["source"].astype(str).str.lower() == "human"]
    rows: List[Dict[str, Any]] = []
    for _, r in df.iterrows():
        status = str(r.get("status", "")).strip().lower()
        if status == "marked" and r.get("ball_visible") is True:
            rows.append({
                "match_id": match_dir.name,
                "frame_idx": int(r["frame_idx"]),
                "timestamp_sec": float(r["timestamp_sec"]),
                "status": "positive",
                "x_px": float(r["source_image_x"]),
                "y_px": float(r["source_image_y"]),
            })
        elif status == "invisible":
            rows.append({
                "match_id": match_dir.name,
                "frame_idx": int(r["frame_idx"]),
                "timestamp_sec": float(r["timestamp_sec"]),
                "status": "negative",
                "x_px": None,
                "y_px": None,
            })
    info = {"video_path": str(video_path), "width": width, "height": height, "fps": fps}
    return rows, info


def write_label(
    label_path: Path,
    sample: Dict[str, Any],
    img_w: int,
    img_h: int,
    bbox_size: int,
) -> None:
    """Write YOLO-format label. Empty file = negative example."""
    if sample["status"] == "negative":
        label_path.write_text("", encoding="utf-8")
        return
    cx = float(sample["x_px"]) / img_w
    cy = float(sample["y_px"]) / img_h
    w = float(bbox_size) / img_w
    h = float(bbox_size) / img_h
    cx = max(0.0, min(1.0, cx))
    cy = max(0.0, min(1.0, cy))
    label_path.write_text(f"0 {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}\n", encoding="utf-8")


def extract_frame(cap: cv2.VideoCapture, frame_idx: int):
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
    ok, frame = cap.read()
    if not ok or frame is None:
        return None
    return frame


def build_dataset(
    samples: List[Dict[str, Any]],
    info: Dict[str, Any],
    out_dir: Path,
    bbox_size: int,
    val_split: float,
    seed: int,
) -> Dict[str, Any]:
    rng = random.Random(seed)
    rng.shuffle(samples)
    n_val = max(1, int(round(len(samples) * val_split)))
    val_set = set(range(n_val))

    images_root = out_dir / "images"
    labels_root = out_dir / "labels"
    for split in ("train", "val"):
        (images_root / split).mkdir(parents=True, exist_ok=True)
        (labels_root / split).mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(info["video_path"])
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {info['video_path']}")

    n_train_pos = n_train_neg = n_val_pos = n_val_neg = 0
    written: List[Dict[str, Any]] = []
    try:
        for i, s in enumerate(samples):
            split = "val" if i in val_set else "train"
            stem = f"{s['match_id']}__f{s['frame_idx']:08d}"
            img_path = images_root / split / f"{stem}.jpg"
            lbl_path = labels_root / split / f"{stem}.txt"

            if not img_path.exists():
                frame = extract_frame(cap, s["frame_idx"])
                if frame is None:
                    print(f"  warn: failed to read frame {s['frame_idx']}", file=sys.stderr)
                    continue
                cv2.imwrite(str(img_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 92])

            write_label(lbl_path, s, info["width"], info["height"], bbox_size)
            written.append({**s, "split": split, "image": str(img_path.relative_to(out_dir))})

            if split == "train":
                if s["status"] == "positive":
                    n_train_pos += 1
                else:
                    n_train_neg += 1
            else:
                if s["status"] == "positive":
                    n_val_pos += 1
                else:
                    n_val_neg += 1
    finally:
        cap.release()

    return {
        "n_train_pos": n_train_pos, "n_train_neg": n_train_neg,
        "n_val_pos": n_val_pos, "n_val_neg": n_val_neg,
        "rows": written,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--match", action="append", required=True, dest="matches",
                        help="Match id under matches/ or path; repeatable")
    parser.add_argument("--output-name", default="ball_v1",
                        help="Subdirectory under evals/training/")
    parser.add_argument("--bbox-size", type=int, default=24,
                        help="Square bbox edge length in source pixels (default 24)")
    parser.add_argument("--val-split", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    out_dir = TRAINING_ROOT / args.output_name
    out_dir.mkdir(parents=True, exist_ok=True)

    all_samples: List[Dict[str, Any]] = []
    info: Optional[Dict[str, Any]] = None
    per_match_summary: List[Dict[str, Any]] = []
    for raw in args.matches:
        match_dir = find_match_dir(raw)
        rows, m_info = gather_samples(match_dir)
        all_samples.extend(rows)
        per_match_summary.append({
            "match_id": match_dir.name,
            "video_path": m_info["video_path"],
            "video_resolution": [m_info["width"], m_info["height"]],
            "fps": m_info["fps"],
            "n_positive": sum(1 for r in rows if r["status"] == "positive"),
            "n_negative": sum(1 for r in rows if r["status"] == "negative"),
        })
        if info is None:
            info = m_info
        else:
            if (m_info["width"], m_info["height"]) != (info["width"], info["height"]):
                print(
                    f"  warn: resolution mismatch — {raw} is "
                    f"{m_info['width']}x{m_info['height']} vs first match's "
                    f"{info['width']}x{info['height']}. YOLO will resize at training time.",
                    file=sys.stderr,
                )
            # sample-by-sample we'll write labels using each match's own dimensions
            # — but build_dataset uses a single info. Refactor: do per-sample resolution.

    # Refactor: rebuild samples with per-sample width/height from their own match
    enriched: List[Dict[str, Any]] = []
    by_match_info: Dict[str, Dict[str, Any]] = {}
    for raw in args.matches:
        match_dir = find_match_dir(raw)
        if match_dir.name not in by_match_info:
            _, m_info = gather_samples(match_dir)
            by_match_info[match_dir.name] = m_info
    for s in all_samples:
        m_info = by_match_info[s["match_id"]]
        enriched.append({**s, "_video_path": m_info["video_path"],
                         "_width": m_info["width"], "_height": m_info["height"]})

    rng = random.Random(args.seed)
    rng.shuffle(enriched)
    n_val = max(1, int(round(len(enriched) * args.val_split)))
    val_idx = set(range(n_val))

    images_root = out_dir / "images"
    labels_root = out_dir / "labels"
    for split in ("train", "val"):
        (images_root / split).mkdir(parents=True, exist_ok=True)
        (labels_root / split).mkdir(parents=True, exist_ok=True)

    # Open video captures lazily, one per match
    caps: Dict[str, cv2.VideoCapture] = {}

    counts = {"train": {"pos": 0, "neg": 0}, "val": {"pos": 0, "neg": 0}}
    written_rows: List[Dict[str, Any]] = []
    try:
        for i, s in enumerate(enriched):
            split = "val" if i in val_idx else "train"
            stem = f"{s['match_id']}__f{s['frame_idx']:08d}"
            img_path = images_root / split / f"{stem}.jpg"
            lbl_path = labels_root / split / f"{stem}.txt"

            cap = caps.get(s["match_id"])
            if cap is None:
                cap = cv2.VideoCapture(s["_video_path"])
                if not cap.isOpened():
                    raise RuntimeError(f"Could not open: {s['_video_path']}")
                caps[s["match_id"]] = cap

            if not img_path.exists():
                frame = extract_frame(cap, s["frame_idx"])
                if frame is None:
                    print(f"  warn: failed to read frame {s['frame_idx']} in {s['match_id']}",
                          file=sys.stderr)
                    continue
                cv2.imwrite(str(img_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 92])

            write_label(lbl_path, s, s["_width"], s["_height"], args.bbox_size)
            kind = "pos" if s["status"] == "positive" else "neg"
            counts[split][kind] += 1
            written_rows.append({
                "match_id": s["match_id"], "frame_idx": s["frame_idx"],
                "split": split, "kind": kind,
                "image": str(img_path.relative_to(out_dir)),
            })
    finally:
        for cap in caps.values():
            cap.release()

    data_yaml = {
        "path": str(out_dir.resolve()),
        "train": "images/train",
        "val": "images/val",
        "names": {0: "sports ball"},
        "nc": 1,
    }
    (out_dir / "data.yaml").write_text(
        yaml.safe_dump(data_yaml, sort_keys=False, allow_unicode=True), encoding="utf-8",
    )

    manifest = {
        "dataset_name": args.output_name,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "bbox_size_px": args.bbox_size,
        "val_split": args.val_split,
        "seed": args.seed,
        "matches": per_match_summary,
        "counts": {
            "train_positive": counts["train"]["pos"],
            "train_negative": counts["train"]["neg"],
            "val_positive": counts["val"]["pos"],
            "val_negative": counts["val"]["neg"],
            "total_train": counts["train"]["pos"] + counts["train"]["neg"],
            "total_val": counts["val"]["pos"] + counts["val"]["neg"],
        },
    }
    (out_dir / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True), encoding="utf-8",
    )

    print(f"✅ Dataset written to: {out_dir.relative_to(PROJECT_ROOT)}")
    print(f"   train: {counts['train']['pos']} pos + {counts['train']['neg']} neg = "
          f"{counts['train']['pos'] + counts['train']['neg']}")
    print(f"   val:   {counts['val']['pos']} pos + {counts['val']['neg']} neg = "
          f"{counts['val']['pos'] + counts['val']['neg']}")
    print(f"   data.yaml + manifest.yaml written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
