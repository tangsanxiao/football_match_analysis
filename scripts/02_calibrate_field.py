#!/usr/bin/env python3
"""Compute a pitch homography from manually entered calibration points."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple, Union

import cv2
import numpy as np
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def resolve_path(path: Union[str, Path]) -> Path:
    path = Path(path)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def load_yaml(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def valid_points(points_yaml: Dict[str, Any]) -> List[Dict[str, Any]]:
    points = []
    for point in points_yaml.get("points", []):
        if point.get("enabled") is False:
            continue
        if point.get("use_for_homography") is False:
            continue
        image_xy = point.get("image_xy")
        field_xy = point.get("field_xy")
        if image_xy is None:
            continue
        if not isinstance(image_xy, list) or len(image_xy) != 2:
            raise ValueError(f"Invalid image_xy for point {point.get('name')}: {image_xy}")
        if not isinstance(field_xy, list) or len(field_xy) != 2:
            raise ValueError(f"Invalid field_xy for point {point.get('name')}: {field_xy}")
        points.append(point)
    return points


def compute_homography(points: List[Dict[str, Any]]) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
    if len(points) < 4:
        raise ValueError("At least 4 calibration points with image_xy are required.")

    image_pts = np.array([point["image_xy"] for point in points], dtype=np.float32)
    field_pts = np.array([point["field_xy"] for point in points], dtype=np.float32)

    homography, mask = cv2.findHomography(image_pts, field_pts, method=0)
    if homography is None:
        raise RuntimeError("cv2.findHomography failed.")

    projected = cv2.perspectiveTransform(image_pts.reshape(-1, 1, 2), homography).reshape(-1, 2)
    errors = np.linalg.norm(projected - field_pts, axis=1)

    point_results = []
    for point, projected_xy, error_m, used in zip(points, projected, errors, mask.ravel().tolist()):
        point_results.append(
            {
                "name": point["name"],
                "image_xy": [float(point["image_xy"][0]), float(point["image_xy"][1])],
                "field_xy": [float(point["field_xy"][0]), float(point["field_xy"][1])],
                "projected_field_xy": [float(projected_xy[0]), float(projected_xy[1])],
                "error_m": float(error_m),
                "used": bool(used),
            }
        )

    return homography, point_results


def draw_overlay(image_path: Path, point_results: List[Dict[str, Any]], output_path: Path) -> None:
    image = cv2.imread(str(image_path))
    if image is None:
        raise RuntimeError(f"Could not read calibration image: {image_path}")

    for point in point_results:
        x, y = point["image_xy"]
        error = point["error_m"]
        label = f"{point['name']} {error:.2f}m"
        center = (int(round(x)), int(round(y)))
        cv2.circle(image, center, 10, (0, 255, 255), -1)
        cv2.circle(image, center, 16, (0, 0, 0), 2)
        cv2.putText(
            image,
            label,
            (center[0] + 12, center[1] - 12),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 0, 0),
            4,
            cv2.LINE_AA,
        )
        cv2.putText(
            image,
            label,
            (center[0] + 12, center[1] - 12),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), image, [int(cv2.IMWRITE_JPEG_QUALITY), 92])


def save_calibration(
    config: Dict[str, Any],
    points_path: Path,
    image_path: Path,
    homography: np.ndarray,
    point_results: List[Dict[str, Any]],
) -> Path:
    interim_dir = resolve_path(config["match"]["interim_dir"])
    output_dir = interim_dir / "calibration"
    output_dir.mkdir(parents=True, exist_ok=True)
    calibration_path = output_dir / "calibration.json"
    overlay_path = output_dir / "calibration_overlay.jpg"

    errors = [point["error_m"] for point in point_results]
    payload = {
        "match_id": config["match"]["id"],
        "points_path": str(points_path),
        "image_path": str(image_path),
        "homography_image_to_field": homography.tolist(),
        "homography_field_to_image": np.linalg.inv(homography).tolist(),
        "point_count": len(point_results),
        "mean_error_m": float(np.mean(errors)),
        "max_error_m": float(np.max(errors)),
        "points": point_results,
        "quality_note": quality_note(float(np.mean(errors)), float(np.max(errors))),
    }

    with calibration_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)

    draw_overlay(image_path, point_results, overlay_path)
    return calibration_path


def quality_note(mean_error_m: float, max_error_m: float) -> str:
    if mean_error_m <= 0.5 and max_error_m <= 1.0:
        return "good_for_mvp"
    if mean_error_m <= 1.0 and max_error_m <= 2.0:
        return "usable_but_report_spatial_metrics_as_medium_confidence"
    return "high_error_review_points_or_add_lens_distortion_correction"


def print_summary(calibration_path: Path) -> None:
    with calibration_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    print(f"calibration_json: {calibration_path}")
    print(f"point_count: {payload['point_count']}")
    print(f"mean_error_m: {payload['mean_error_m']:.3f}")
    print(f"max_error_m: {payload['max_error_m']:.3f}")
    print(f"quality_note: {payload['quality_note']}")
    print(f"overlay: {calibration_path.parent / 'calibration_overlay.jpg'}")


def default_points_path(config: Dict[str, Any]) -> Path:
    configured = config.get("calibration", {}).get("points_path")
    if configured:
        return resolve_path(configured)
    return resolve_path("configs/calibration_points_red_mvp.yaml")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/match_red_mvp.yaml")
    parser.add_argument("--points", default=None)
    args = parser.parse_args()

    config_path = resolve_path(args.config)
    config = load_yaml(config_path)
    points_path = resolve_path(args.points) if args.points else default_points_path(config)
    points_yaml = load_yaml(points_path)
    image_path = resolve_path(points_yaml["calibration"]["image_path"])
    points = valid_points(points_yaml)
    if len(points) < 4:
        print("Calibration points are not ready yet.")
        print(f"Fill at least 4 image_xy entries in: {points_path}")
        print(f"Reference image: {image_path}")
        raise SystemExit(2)
    homography, point_results = compute_homography(points)
    calibration_path = save_calibration(config, points_path, image_path, homography, point_results)
    print_summary(calibration_path)


if __name__ == "__main__":
    main()
