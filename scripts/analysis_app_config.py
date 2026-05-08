"""Shared configuration and filesystem helpers for the analysis app."""

from __future__ import annotations

import csv
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import yaml

try:
    from analysis_metrics import DEFAULT_METRICS, METRIC_DEFINITIONS
except ModuleNotFoundError:
    from scripts.analysis_metrics import DEFAULT_METRICS, METRIC_DEFINITIONS


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv"}
ROLE_CHOICES = ["goalkeeper", "defender", "right_forward", "center_forward", "left_forward", "substitute"]
DEFAULT_FIELD_LENGTH = 40.0
DEFAULT_FIELD_WIDTH = 20.0
DEFAULT_SAMPLE_FPS = 2.0


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


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "item"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def clean_string_list(values: Any) -> List[str]:
    if isinstance(values, str):
        raw = values.split(",")
    elif isinstance(values, list):
        raw = values
    else:
        raw = []
    return [str(item).strip() for item in raw if str(item).strip()]


def coerce_number(value: Any) -> Any:
    text = str(value or "").strip()
    return int(text) if text.isdigit() else text


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


def read_csv_rows(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def default_match_id(match_name: str, video_path: Path) -> str:
    seed = match_name.strip() or video_path.stem or "match"
    return f"{slugify(seed)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"[:80]


def default_calibration_points(field_length: float, field_width: float) -> Dict[str, Any]:
    length = float(field_length)
    width = float(field_width)
    return {
        "calibration": {
            "image_path": "",
            "labeling_status": "draft",
            "labeling_submitted": False,
            "note": "image_path is filled by scripts/09_prepare_match_review.py after sample extraction.",
        },
        "points": [
            {"name": "top_left_corner", "field_xy": [0.0, 0.0], "image_xy": None, "enabled": True},
            {"name": "top_right_corner", "field_xy": [length, 0.0], "image_xy": None, "enabled": True},
            {"name": "bottom_left_corner", "field_xy": [0.0, width], "image_xy": None, "enabled": True},
            {"name": "bottom_right_corner", "field_xy": [length, width], "image_xy": None, "enabled": True},
            {"name": "center_mark", "field_xy": [length / 2.0, width / 2.0], "image_xy": None, "enabled": True},
            {"name": "halfway_top_touchline", "field_xy": [length / 2.0, 0.0], "image_xy": None, "enabled": True},
            {"name": "halfway_bottom_touchline", "field_xy": [length / 2.0, width], "image_xy": None, "enabled": True},
            {"name": "left_goal_center", "field_xy": [0.0, width / 2.0], "image_xy": None, "enabled": True},
            {"name": "right_goal_center", "field_xy": [length, width / 2.0], "image_xy": None, "enabled": True},
        ],
    }


def sync_calibration_points(
    points_path: Path,
    field_length: float,
    field_width: float,
    reset_image: bool = False,
    field_changed: bool = False,
) -> None:
    if not points_path.exists() or reset_image:
        write_yaml(points_path, default_calibration_points(field_length, field_width))
        return

    points_yaml = load_yaml(points_path)
    length = float(field_length)
    width = float(field_width)
    field_xy_by_name = {
        "top_left_corner": [0.0, 0.0],
        "top_right_corner": [length, 0.0],
        "bottom_left_corner": [0.0, width],
        "bottom_right_corner": [length, width],
        "center_mark": [length / 2.0, width / 2.0],
        "halfway_top_touchline": [length / 2.0, 0.0],
        "halfway_bottom_touchline": [length / 2.0, width],
        "left_goal_center": [0.0, width / 2.0],
        "right_goal_center": [length, width / 2.0],
    }
    for point in points_yaml.get("points", []):
        name = point.get("name")
        if name in field_xy_by_name:
            point["field_xy"] = field_xy_by_name[name]
    calibration = points_yaml.setdefault("calibration", {})
    if field_changed and (calibration.get("labeling_submitted") or calibration.get("labeling_status") == "submitted"):
        calibration["labeling_status"] = "draft_after_field_change"
        calibration["labeling_submitted"] = False
        calibration["labeling_last_edited_at"] = now_iso()
    write_yaml(points_path, points_yaml)


def build_config(info: Dict[str, Any], project_dir: Path, video_path_value: str) -> Dict[str, Any]:
    team_key = info["team_key"]
    metrics = info["metrics"]
    return {
        "match": {
            "id": info["match_id"],
            "name": info["match_name"],
            "project_dir": project_relative(project_dir),
            "video_path": video_path_value,
            "output_dir": project_relative(project_dir / "reports"),
            "interim_dir": project_relative(project_dir / "data" / "interim"),
            "review_dir": project_relative(project_dir / "review"),
        },
        "field": {
            "length_m": float(info.get("field_length", DEFAULT_FIELD_LENGTH)),
            "width_m": float(info.get("field_width", DEFAULT_FIELD_WIDTH)),
        },
        "calibration": {
            "points_path": project_relative(project_dir / "config" / "calibration_points.yaml"),
            "picker_port": 8765,
        },
        "sampling": {
            "sample_frame_count": 36,
            "jpg_quality": 92,
        },
        "teams": {
            "analyze_team": team_key,
            team_key: {
                "display_name": info["team_name"],
                "team_color": info["team_color"],
                "kit": {
                    "field_colors": [info["team_color"]],
                    "goalkeeper_colors": [info["goalkeeper_color"]],
                },
                "players": info["players"],
                "substitutions": info.get("substitutions", []),
            },
            "opponent": {
                "display_name": "Opponent",
                "analyze_individuals": False,
            },
        },
        "analysis": {
            "target_events": metrics,
            "target_metrics": metrics,
            "report_formats": ["markdown", "csv", "html"],
        },
        "review": {
            "match_info": {
                "status": "confirmed",
                "confirmed": True,
                "confirmed_at": now_iso(),
                "player_count": len(info["players"]),
                "target_metric_count": len(metrics),
            },
            "initial_recognition": {
                "start_sec": 120.0,
                "duration_sec": 120.0,
                "sample_fps": 2.0,
                "max_frames": 240,
                "top_n": 60,
            },
            "human_review": {
                "enabled": True,
                "gate_before_final_report": True,
                "max_review_items": 60,
                "selection": [
                    "low_identity_confidence",
                    "key_timestamps",
                    "event_candidates",
                    "ball_uncertain_frames",
                    "speed_or_distance_outliers",
                ],
                "mode": "system_prelabels_human_confirms_or_corrects",
            },
            "manual_identity_path": project_relative(project_dir / "review" / "identity_review.yaml"),
        },
        "detection": {
            "model": "yolo11n.pt",
            "tracker": "bytetrack.yaml",
            "classes": [0, 32],
            "conf": 0.18,
            "iou": 0.5,
            "imgsz": 1280,
            "sample_fps": DEFAULT_SAMPLE_FPS,
            "smoke_test": {
                "start_sec": 120.0,
                "duration_sec": 10.0,
                "max_frames": 24,
            },
            "field_filter": {
                "enabled": True,
                "person_margin_m": 1.5,
                "ball_margin_m": 0.2,
            },
            "ball_filter": {
                "static_false_positive": {
                    "enabled": True,
                    "min_frames": 4,
                    "min_duration_s": 1.5,
                    "max_span_m": 0.45,
                },
            },
            "identity_filter": {
                "opponent_goalkeeper": {
                    "enabled": True,
                    "opponent_goal_zone_m": 4.5,
                    "goal_y_margin_m": 4.2,
                    "min_frames": 6,
                    "max_span_x_m": 4.0,
                    "max_span_y_m": 7.0,
                    "strict_stationary_span_x_m": 1.8,
                    "strict_stationary_span_y_m": 3.0,
                },
            },
        },
    }


def normalize_info(payload: Dict[str, Any], existing_match_id: Optional[str] = None) -> Dict[str, Any]:
    video = str(payload.get("video_path") or "").strip()
    if not video:
        raise ValueError("请选择或填写视频路径。")
    video_path = resolve_path(video)
    if not video_path.exists():
        raise ValueError(f"视频不存在: {video_path}")

    match_name = str(payload.get("match_name") or "").strip()
    team_name = str(payload.get("team_name") or "").strip()
    team_color = str(payload.get("team_color") or "").strip()
    goalkeeper_color = str(payload.get("goalkeeper_color") or "").strip()
    if not match_name:
        raise ValueError("比赛名称不能为空。")
    if not team_name:
        raise ValueError("球队名称不能为空。")
    if not team_color:
        raise ValueError("球队主色不能为空。")
    if not goalkeeper_color:
        raise ValueError("门将颜色不能为空。")

    metrics = clean_string_list(payload.get("metrics"))
    if not metrics:
        raise ValueError("至少选择一个分析指标。")

    team_key = slugify(payload.get("team_key") or team_name or team_color)
    players_payload = payload.get("players")
    if not isinstance(players_payload, list):
        players_payload = []
    players: List[Dict[str, Any]] = []
    for index, item in enumerate(players_payload):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        role = str(item.get("role") or ROLE_CHOICES[min(index, len(ROLE_CHOICES) - 2)]).strip()
        player_id = slugify(f"{team_key}_{name}")
        players.append(
            {
                "player_id": player_id,
                "name": name,
                "number": coerce_number(item.get("number", "")),
                "role": role,
                "visual_hint": str(item.get("visual_hint") or "").strip(),
            }
        )
    if not players:
        raise ValueError("至少需要填写一名球员。")

    substitutions: List[Dict[str, Any]] = []
    for item in payload.get("substitutions") or []:
        if not isinstance(item, dict):
            continue
        at = str(item.get("at") or "").strip()
        out_player = str(item.get("out") or "").strip()
        in_player = str(item.get("in") or "").strip()
        note = str(item.get("note") or "").strip()
        if any([at, out_player, in_player, note]):
            substitutions.append({"at": at, "out": out_player, "in": in_player, "note": note})

    return {
        "match_id": existing_match_id or default_match_id(match_name, video_path),
        "match_name": match_name,
        "team_key": team_key,
        "team_name": team_name,
        "team_color": team_color,
        "goalkeeper_color": goalkeeper_color,
        "field_length": float(payload.get("field_length") or DEFAULT_FIELD_LENGTH),
        "field_width": float(payload.get("field_width") or DEFAULT_FIELD_WIDTH),
        "video_path": video_path,
        "players": players,
        "substitutions": substitutions,
        "metrics": metrics,
    }


def create_or_update_match(payload: Dict[str, Any], match_id: Optional[str] = None) -> Tuple[Path, Path]:
    info = normalize_info(payload, existing_match_id=match_id)
    project_dir = PROJECT_ROOT / "matches" / info["match_id"]
    for folder in ["config", "review", "reports", "data/interim", "videos/raw"]:
        (project_dir / folder).mkdir(parents=True, exist_ok=True)

    config_path = project_dir / "config" / "match.yaml"
    points_path = project_dir / "config" / "calibration_points.yaml"
    video_path_value = project_relative(info["video_path"])
    old_config = load_yaml(config_path) if config_path.exists() else {}
    old_video_path = old_config.get("match", {}).get("video_path")
    old_field = old_config.get("field", {})
    old_length = float(old_field.get("length_m") or info["field_length"])
    old_width = float(old_field.get("width_m") or info["field_width"])
    field_changed = old_length != float(info["field_length"]) or old_width != float(info["field_width"])
    config = build_config(info, project_dir, video_path_value)
    if config_path.exists():
        for key in ["sampling", "detection"]:
            if key in old_config and key not in config:
                config[key] = old_config[key]
        if old_config.get("review", {}).get("manual_identity_path"):
            config["review"]["manual_identity_path"] = old_config["review"]["manual_identity_path"]
    write_yaml(config_path, config)
    sync_calibration_points(
        points_path,
        info["field_length"],
        info["field_width"],
        reset_image=bool(old_video_path and old_video_path != video_path_value),
        field_changed=field_changed,
    )
    return project_dir, config_path


def list_videos() -> List[Dict[str, str]]:
    videos: List[Dict[str, str]] = []
    roots = [PROJECT_ROOT / "videos" / "raw"]
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS:
                videos.append({"path": project_relative(path), "name": path.name})
    return videos


def point_counts(points_path: Path) -> Tuple[int, int, bool]:
    points_yaml = load_yaml(points_path)
    marked = 0
    enabled = 0
    for point in points_yaml.get("points", []):
        if point.get("enabled") is False:
            continue
        enabled += 1
        if isinstance(point.get("image_xy"), list) and len(point.get("image_xy")) == 2:
            marked += 1
    calibration = points_yaml.get("calibration", {})
    submitted = bool(calibration.get("labeling_submitted") or calibration.get("labeling_status") == "submitted")
    return marked, enabled, submitted


def review_paths(match_id: str) -> Dict[str, Path]:
    project_dir = PROJECT_ROOT / "matches" / match_id
    review_dir = project_dir / "review"
    human_dir = review_dir / "human_review"
    return {
        "project_dir": project_dir,
        "review_dir": review_dir,
        "human_dir": human_dir,
        "manifest": human_dir / "review_manifest.yaml",
        "corrections": human_dir / "corrections.yaml",
        "identity_review": review_dir / "identity_review.yaml",
        "final_status": human_dir / "final_report_status.yaml",
    }


def ball_review_paths(match_id: str) -> Dict[str, Path]:
    project_dir = PROJECT_ROOT / "matches" / match_id
    review_dir = project_dir / "review"
    ball_dir = review_dir / "ball_review"
    return {
        "project_dir": project_dir,
        "review_dir": review_dir,
        "ball_dir": ball_dir,
        "manifest": ball_dir / "ball_review_manifest.yaml",
        "corrections": ball_dir / "corrections.yaml",
        "points_csv": ball_dir / "ball_review_points.csv",
    }


def roster_options(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    analyze_team = str(config.get("teams", {}).get("analyze_team", ""))
    players = config.get("teams", {}).get(analyze_team, {}).get("players") or []
    return [
        {
            "player_id": str(player.get("player_id") or ""),
            "label": f"{player.get('name', '')} / {player.get('number', '')} / {player.get('role', '')}",
            "name": player.get("name", ""),
            "number": player.get("number", ""),
            "role": player.get("role", ""),
        }
        for player in players
    ]
