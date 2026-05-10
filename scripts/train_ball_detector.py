#!/usr/bin/env python3
"""Fine-tune a single-class YOLO ball detector on a prepared dataset.

The dataset comes from ``scripts/prepare_ball_training_data.py``. Training
runs ultralytics out of the box; the only project-specific bit is the
default config tuned for tiny datasets (heavy aug, small batch, patience).

Output: ``runs/detect/<run_name>/weights/best.pt``.

Usage:
    python scripts/train_ball_detector.py \
        --data evals/training/ball_v1/data.yaml \
        --base yolo11n.pt \
        --run-name ball_v1 \
        --epochs 80 --imgsz 1280 --batch 8 --device mps
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True,
                        help="Path to data.yaml (e.g. evals/training/ball_v1/data.yaml)")
    parser.add_argument("--base", default="yolo11n.pt",
                        help="Base weights to fine-tune from (default: yolo11n.pt)")
    parser.add_argument("--run-name", default="ball_v1",
                        help="Sub-directory under runs/detect/")
    parser.add_argument("--epochs", type=int, default=80)
    parser.add_argument("--imgsz", type=int, default=1280)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--device", default="mps", help="cpu / mps / 0 / etc.")
    parser.add_argument("--patience", type=int, default=20)
    parser.add_argument("--lr0", type=float, default=0.01)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--project", default=str(PROJECT_ROOT / "runs" / "detect"),
                        help="Where to write run outputs (default: runs/detect/)")
    args = parser.parse_args()

    try:
        from ultralytics import YOLO
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "Missing ultralytics. Install with: "
            ".venv/bin/python -m pip install -r requirements-detect.txt"
        ) from exc

    data_path = Path(args.data)
    if not data_path.is_absolute():
        data_path = PROJECT_ROOT / data_path
    if not data_path.exists():
        raise SystemExit(f"data.yaml not found: {data_path}")

    print(f"base weights:  {args.base}")
    print(f"data.yaml:     {data_path}")
    print(f"run dir:       {args.project}/{args.run_name}")
    print(f"epochs={args.epochs} imgsz={args.imgsz} batch={args.batch} device={args.device}")
    print()

    model = YOLO(args.base)
    results = model.train(
        data=str(data_path),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        patience=args.patience,
        lr0=args.lr0,
        workers=args.workers,
        project=args.project,
        name=args.run_name,
        exist_ok=True,
        # tiny-dataset hygiene: more aug, no mosaic late, conservative LR
        mosaic=1.0,
        close_mosaic=10,  # disable mosaic in last 10 epochs
        hsv_h=0.015, hsv_s=0.7, hsv_v=0.4,
        translate=0.1, scale=0.5, fliplr=0.5,
        degrees=0.0, shear=0.0, perspective=0.0,
        # keep these labels; we want to match anything ball-shaped at small scale
        single_cls=True,
        verbose=True,
        plots=True,
    )

    weights_dir = Path(args.project) / args.run_name / "weights"
    best = weights_dir / "best.pt"
    print()
    if best.exists():
        print(f"✅ best weights: {best.resolve()}")
        try:
            print(f"   relative:     {best.resolve().relative_to(PROJECT_ROOT)}")
        except ValueError:
            pass
    else:
        print(f"⚠️  best.pt not found under {weights_dir}; check training logs above")
    return 0


if __name__ == "__main__":
    sys.exit(main())
