#!/usr/bin/env python3
"""Prepare a system-prelabeled human review package before final reporting."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union

import cv2
import yaml


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


def full_detections_dir(config: Dict[str, Any], start_sec: float, duration_sec: float, sample_fps: float) -> Path:
    return resolve_path(config["match"]["interim_dir"]) / "detections" / output_slug(start_sec, duration_sec, sample_fps)


def analyze_team(config: Dict[str, Any]) -> Dict[str, Any]:
    team_key = str(config.get("teams", {}).get("analyze_team", ""))
    return config.get("teams", {}).get(team_key, {})


def roster_by_id(config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {str(player["player_id"]): player for player in analyze_team(config).get("players", [])}


def read_csv(path: Path) -> List[Dict[str, str]]:
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


def timestamp_label(timestamp_sec: float) -> str:
    seconds = max(0, int(round(timestamp_sec)))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def ensure_full_detection_assets(
    config_path: Path,
    config: Dict[str, Any],
    detections_dir: Path,
    duration_sec: float,
    sample_fps: float,
    device: Optional[str],
    force: bool,
) -> None:
    if not (resolve_path(config["match"]["interim_dir"]) / "video_probe.json").exists():
        run_command([sys.executable, "scripts/00_probe_video.py", "--config", project_relative(config_path)])

    calibration_json = resolve_path(config["match"]["interim_dir"]) / "calibration" / "calibration.json"
    if force or not calibration_json.exists():
        points_path = resolve_path(config.get("calibration", {}).get("points_path", ""))
        run_command(
            [
                sys.executable,
                "scripts/02_calibrate_field.py",
                "--config",
                project_relative(config_path),
                "--points",
                project_relative(points_path),
            ]
        )

    summary_path = detections_dir / "summary.json"
    if force or not summary_path.exists() or not (detections_dir / "tracks_in_play.csv").exists():
        detect_cmd = [
            sys.executable,
            "scripts/03_detect_track.py",
            "--config",
            project_relative(config_path),
            "--start-sec",
            "0",
            "--duration-sec",
            str(duration_sec),
            "--sample-fps",
            str(sample_fps),
            "--max-frames",
            "0",
            "--save-annotated-every",
            "60",
        ]
        if device:
            detect_cmd.extend(["--device", device])
        run_command(detect_cmd)

    if force or not (detections_dir / "tracklets.csv").exists():
        run_command(
            [
                sys.executable,
                "scripts/04_summarize_tracklets.py",
                "--config",
                project_relative(config_path),
                "--detections-dir",
                project_relative(detections_dir),
                "--min-frames",
                "8",
                "--top-n",
                "80",
            ]
        )

    if force or not (detections_dir / "tracklet_color_candidates.csv").exists():
        run_command(
            [
                sys.executable,
                "scripts/05_classify_tracklets.py",
                "--config",
                project_relative(config_path),
                "--detections-dir",
                project_relative(detections_dir),
                "--samples-per-track",
                "5",
                "--top-n",
                "80",
            ]
        )

    if force or not (detections_dir / "tracklet_identity_assignments.csv").exists():
        run_command(
            [
                sys.executable,
                "scripts/06_assign_identities.py",
                "--config",
                project_relative(config_path),
                "--detections-dir",
                project_relative(detections_dir),
                "--right-forward-y",
                "high",
            ]
        )


def write_identity_review_template(
    config: Dict[str, Any],
    assignments: Iterable[Dict[str, str]],
    detections_dir: Path,
    review_path: Path,
    status: str,
) -> None:
    players = roster_by_id(config)
    grouped: Dict[str, List[int]] = {}
    confidences: Dict[str, List[float]] = {}
    for row in assignments:
        player_id = str(row.get("assigned_player_id") or "")
        if not player_id or player_id not in players:
            continue
        grouped.setdefault(player_id, []).append(safe_int(row.get("track_id")))
        confidences.setdefault(player_id, []).append(safe_float(row.get("identity_confidence"), 0.5))

    payload: Dict[str, Any] = {
        "review_required": status != "submitted",
        "human_review": {
            "status": status,
            "source_detections_dir": project_relative(detections_dir),
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        },
        "instructions": [
            "系统已给出球员预标注；人工只需要确认或修正错误 track_id。",
            "提交人工校验后，最终报告会使用这里的绑定结果。",
        ],
        "assignments": {},
    }
    for player_id, track_ids in grouped.items():
        player = players[player_id]
        confidence_values = confidences.get(player_id) or [0.5]
        payload["assignments"][player_id] = {
            "name": player.get("name"),
            "number": player.get("number"),
            "role": player.get("role"),
            "confidence": round(sum(confidence_values) / len(confidence_values), 3),
            "reason": "system prelabel accepted unless corrected in human review UI",
            "track_ids": sorted(set(track_ids)),
        }
    write_yaml(review_path, payload)


def extract_review_frame(
    cap: cv2.VideoCapture,
    row: Dict[str, str],
    output_path: Path,
    label: str,
    max_width: int = 1600,
) -> bool:
    frame_idx = safe_int(row.get("sample_frame"))
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
    ok, frame = cap.read()
    if not ok or frame is None:
        return False

    x1 = safe_int(row.get("sample_x1"))
    y1 = safe_int(row.get("sample_y1"))
    x2 = safe_int(row.get("sample_x2"))
    y2 = safe_int(row.get("sample_y2"))
    h, w = frame.shape[:2]
    x1 = max(0, min(w - 1, x1))
    x2 = max(0, min(w - 1, x2))
    y1 = max(0, min(h - 1, y1))
    y2 = max(0, min(h - 1, y2))
    if x2 > x1 and y2 > y1:
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 255), 4)
        cv2.rectangle(frame, (x1, max(0, y1 - 36)), (min(w - 1, x1 + 520), y1), (20, 90, 75), -1)
        cv2.putText(frame, label[:64], (x1 + 8, max(24, y1 - 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.82, (255, 255, 255), 2, cv2.LINE_AA)

    if w > max_width:
        scale = max_width / float(w)
        frame = cv2.resize(frame, (max_width, int(h * scale)))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    return bool(cv2.imwrite(str(output_path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90]))


def create_review_package(config: Dict[str, Any], detections_dir: Path, max_items: int) -> Path:
    assignments_path = detections_dir / "tracklet_identity_assignments.csv"
    if not assignments_path.exists():
        raise FileNotFoundError(f"Missing identity assignments: {assignments_path}")
    assignments = read_csv(assignments_path)
    if not assignments:
        raise RuntimeError("No identity assignments are available for human review.")

    review_root = resolve_path(config["match"].get("review_dir", "review")) / "human_review"
    frames_dir = review_root / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    video_path = resolve_path(config["match"]["video_path"])
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")

    sorted_rows = sorted(
        assignments,
        key=lambda row: (
            safe_float(row.get("identity_confidence"), 1.0),
            -safe_int(row.get("frames")),
            safe_float(row.get("first_ts")),
        ),
    )
    selected = sorted_rows[:max_items]
    items: List[Dict[str, Any]] = []
    csv_rows: List[Dict[str, Any]] = []
    for index, row in enumerate(selected, start=1):
        review_id = f"identity_{index:04d}"
        confidence = safe_float(row.get("identity_confidence"), 0.0)
        timestamp_sec = safe_float(row.get("sample_timestamp"), safe_float(row.get("first_ts"), 0.0))
        image_path = frames_dir / f"{review_id}.jpg"
        player_name = str(row.get("assigned_name") or "")
        number = str(row.get("assigned_number") or "")
        track_id = safe_int(row.get("track_id"))
        label = f"system:{player_name} #{number} track={track_id} conf={confidence:.2f}"
        frame_written = extract_review_frame(cap, row, image_path, label)
        relative_image = project_relative(image_path) if frame_written else ""
        reason = "low_identity_confidence" if confidence < 0.65 else "identity_confirmation"
        item = {
            "review_id": review_id,
            "type": "identity",
            "reason": reason,
            "timestamp_sec": round(timestamp_sec, 3),
            "timestamp": timestamp_label(timestamp_sec),
            "frame_idx": safe_int(row.get("sample_frame")),
            "frame_image": relative_image,
            "system_labels": {
                "player": {
                    "predicted_id": str(row.get("assigned_player_id") or ""),
                    "name": player_name,
                    "number": row.get("assigned_number"),
                    "role": row.get("assigned_role"),
                    "track_id": track_id,
                    "confidence": round(confidence, 3),
                    "bbox_xyxy": [
                        safe_int(row.get("sample_x1")),
                        safe_int(row.get("sample_y1")),
                        safe_int(row.get("sample_x2")),
                        safe_int(row.get("sample_y2")),
                    ],
                },
                "ball": {"visible": None, "confidence": None, "center_xy": None},
                "event": {"type": "身份确认", "confidence": "低" if confidence < 0.65 else "中", "include_in_report": True},
            },
            "human_review": {
                "status": "pending",
                "player_id": str(row.get("assigned_player_id") or ""),
                "ball_center_xy": None,
                "event_valid": None,
                "report_action": "keep",
                "note": "",
            },
        }
        items.append(item)
        csv_rows.append(
            {
                "review_id": review_id,
                "type": "identity",
                "reason": reason,
                "timestamp": item["timestamp"],
                "track_id": track_id,
                "predicted_player_id": row.get("assigned_player_id") or "",
                "predicted_name": player_name,
                "confidence": round(confidence, 3),
                "frame_image": relative_image,
            }
        )
    cap.release()

    manifest = {
        "status": "pending",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "match_id": config["match"].get("id"),
        "source_detections_dir": project_relative(detections_dir),
        "total_identity_tracklets": len(assignments),
        "review_item_count": len(items),
        "contact_sheet": project_relative(detections_dir / "tracklet_contact_sheet.jpg"),
        "candidate_contact_sheet": project_relative(detections_dir / "analyze_candidate_contact_sheet.jpg"),
        "items": items,
    }
    manifest_path = review_root / "review_manifest.yaml"
    write_yaml(manifest_path, manifest)

    items_csv = review_root / "review_items.csv"
    with items_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "review_id",
                "type",
                "reason",
                "timestamp",
                "track_id",
                "predicted_player_id",
                "predicted_name",
                "confidence",
                "frame_image",
            ],
        )
        writer.writeheader()
        writer.writerows(csv_rows)

    identity_review_path = resolve_path(config["match"].get("review_dir", "review")) / "identity_review.yaml"
    write_identity_review_template(config, assignments, detections_dir, identity_review_path, "pending")
    return manifest_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--device", default=None)
    parser.add_argument("--start-sec", type=float, default=0.0)
    parser.add_argument("--duration-sec", type=float, default=None)
    parser.add_argument("--sample-fps", type=float, default=None)
    parser.add_argument("--max-items", type=int, default=None)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    config_path = resolve_path(args.config)
    config = load_yaml(config_path)
    if not config:
        raise FileNotFoundError(config_path)

    if not (resolve_path(config["match"]["interim_dir"]) / "video_probe.json").exists():
        run_command([sys.executable, "scripts/00_probe_video.py", "--config", project_relative(config_path)])
        config = load_yaml(config_path)

    duration_sec = args.duration_sec
    if duration_sec is None:
        duration_sec = duration_from_probe(config)
        if duration_sec <= 0:
            duration_sec = 580.0
    sample_fps = args.sample_fps or float(config.get("detection", {}).get("sample_fps", 2.0))
    detections_dir = full_detections_dir(config, args.start_sec, duration_sec, sample_fps)
    ensure_full_detection_assets(config_path, config, detections_dir, duration_sec, sample_fps, args.device, args.force)

    max_items = args.max_items
    if max_items is None:
        max_items = int(config.get("review", {}).get("human_review", {}).get("max_review_items", 60))
    manifest_path = create_review_package(config, detections_dir, max_items=max_items)
    print("\nHuman review package is ready.")
    print(f"review_manifest: {manifest_path}")
    print(f"review_url: http://127.0.0.1:8765/")


if __name__ == "__main__":
    main()
