#!/usr/bin/env python3
"""Serve the football analysis workspace UI."""

from __future__ import annotations

import argparse
import csv
import json
import mimetypes
import re
import subprocess
import sys
import time
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import parse_qs, urlparse

import yaml

try:
    from create_point_picker import build_html, load_yaml as load_yaml_file, point_payload, resolve_path as picker_resolve_path
    from serve_point_picker import submit_labeling, update_point_image_xy
except ModuleNotFoundError:
    from scripts.create_point_picker import build_html, load_yaml as load_yaml_file, point_payload, resolve_path as picker_resolve_path
    from scripts.serve_point_picker import submit_labeling, update_point_image_xy


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv"}
ROLE_CHOICES = ["goalkeeper", "defender", "right_forward", "center_forward", "left_forward", "substitute"]
DEFAULT_FIELD_LENGTH = 40.0
DEFAULT_FIELD_WIDTH = 20.0
DEFAULT_SAMPLE_FPS = 2.0
DEFAULT_METRICS = [
    "shot",
    "pass",
    "steal",
    "positional_discipline",
    "pressing_intensity",
    "off_ball_movement",
    "1v1_attack_defense",
    "defensive_off_ball_movement",
    "space_creation",
    "pass_success",
    "observed_coverage",
]
METRIC_DEFINITIONS = [
    {
        "key": "shot",
        "label": "射门",
        "description": "识别红队最后触球后球向球门或门区高速移动的候选片段；当前受足球检测覆盖率影响，先作为候选统计。",
    },
    {
        "key": "pass",
        "label": "传球",
        "description": "尝试基于球权从一名本队球员转移到另一名本队球员的轨迹链路统计传球候选。",
    },
    {
        "key": "pass_success",
        "label": "传球成功率",
        "description": "在可观察球权链路内估算成功传球占比；球检测不足时会降级为低置信候选。",
    },
    {
        "key": "steal",
        "label": "抢断",
        "description": "识别对手控球后，本队球员近距离施压并导致球权转换的候选事件。",
    },
    {
        "key": "1v1_attack_defense",
        "label": "1v1 攻防",
        "description": "统计持球人与最近防守人形成近距离对抗的次数、方向和成功倾向，是后续重点增强指标。",
    },
    {
        "key": "positional_discipline",
        "label": "站位纪律",
        "description": "评估球员是否稳定出现在角色对应区域，以及攻防转换中是否保持合理纵深和宽度。",
    },
    {
        "key": "pressing_intensity",
        "label": "压迫强度",
        "description": "统计球员在进攻半场或对手附近进入压迫距离的占比，并结合移动方向做压迫候选。",
    },
    {
        "key": "off_ball_movement",
        "label": "无球跑动",
        "description": "评估非持球阶段的跑动距离、进入进攻三区、拉开宽度和接应路线等代理指标。",
    },
    {
        "key": "defensive_off_ball_movement",
        "label": "防守无球跑动",
        "description": "关注回收、补位、封堵中路和协防距离变化，用于区分只站位和主动防守移动。",
    },
    {
        "key": "space_creation",
        "label": "创造空间",
        "description": "观察无球跑动是否拉开防线、创造接应角度或带走防守人；第一版以空间代理指标输出。",
    },
    {
        "key": "observed_coverage",
        "label": "观察覆盖率",
        "description": "该球员被自动识别并绑定成功的去重帧数 / 本次采样处理总帧数 × 100%。它反映本场可评价样本量，不等同真实上场时间；低覆盖率提示遮挡、远景或身份绑定需要人工校验。",
    },
]


JOBS: Dict[str, Dict[str, Any]] = {}


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


def human_review_summary(match_id: str) -> Dict[str, Any]:
    paths = review_paths(match_id)
    manifest = load_yaml(paths["manifest"])
    corrections = load_yaml(paths["corrections"])
    identity_review = load_yaml(paths["identity_review"])
    status = (
        corrections.get("status")
        or manifest.get("status")
        or identity_review.get("human_review", {}).get("status")
        or ("pending" if manifest else "not_ready")
    )
    items = manifest.get("items") or []
    submitted = status == "submitted"
    return {
        "status": status,
        "ready": bool(manifest),
        "submitted": submitted,
        "item_count": len(items),
        "pending_count": sum(1 for item in items if (item.get("human_review") or {}).get("status") == "pending"),
        "manifest_path": project_relative(paths["manifest"]),
        "corrections_path": project_relative(paths["corrections"]),
    }


def match_summary(config_path: Path) -> Dict[str, Any]:
    config = load_yaml(config_path)
    match = config.get("match", {})
    match_id = str(match.get("id") or config_path.parents[1].name)
    project_dir = resolve_path(match.get("project_dir", config_path.parents[1]))
    points_path = resolve_path(config.get("calibration", {}).get("points_path", project_dir / "config" / "calibration_points.yaml"))
    report_dir = resolve_path(match.get("output_dir", project_dir / "reports")) / "mvp_initial"
    analyze_team = str(config.get("teams", {}).get("analyze_team", ""))
    team = config.get("teams", {}).get(analyze_team, {})
    marked, enabled, calibration_submitted = point_counts(points_path)
    report_ready = (report_dir / "report.md").exists() and (report_dir / "report.html").exists()
    review = config.get("review", {})
    players = team.get("players") or []
    metrics = config.get("analysis", {}).get("target_metrics") or config.get("analysis", {}).get("target_events") or []
    kit = team.get("kit", {})
    has_minimum_info = bool(team.get("display_name") and players and metrics and kit.get("field_colors") and kit.get("goalkeeper_colors"))
    match_info_confirmed = bool(
        review.get("match_info", {}).get("confirmed")
        or review.get("match_info", {}).get("status") == "confirmed"
        or has_minimum_info
    )
    human_review = human_review_summary(match_id)
    if human_review["ready"] and not human_review["submitted"]:
        status = f"待人工校验 {human_review['pending_count']}/{human_review['item_count']}"
    elif human_review["submitted"] and not report_ready:
        status = "待生成最终报告"
    elif report_ready:
        status = "报告完成"
    elif not match_info_confirmed:
        status = "待确认信息"
    elif not calibration_submitted:
        status = f"待提交标定 {marked}/{enabled}"
    else:
        status = "待分析"
    latest_job = latest_job_for_match(match_id)
    if latest_job and latest_job.get("status") == "running":
        status = "后台运行中"
    elif latest_job and latest_job.get("status") == "failed":
        status = "后台任务失败"
    return {
        "match_id": match_id,
        "name": match.get("name", match_id),
        "team_name": team.get("display_name", analyze_team),
        "video_path": match.get("video_path", ""),
        "config_path": project_relative(config_path),
        "status": status,
        "match_info_confirmed": match_info_confirmed,
        "calibration_marked": marked,
        "calibration_enabled": enabled,
        "calibration_submitted": calibration_submitted,
        "human_review_ready": human_review["ready"],
        "human_review_submitted": human_review["submitted"],
        "human_review_item_count": human_review["item_count"],
        "report_ready": report_ready,
        "report_md": project_relative(report_dir / "report.md"),
        "report_html": project_relative(report_dir / "report.html"),
        "updated_at": datetime.fromtimestamp(config_path.stat().st_mtime).isoformat(timespec="seconds"),
    }


def list_matches() -> List[Dict[str, Any]]:
    matches_dir = PROJECT_ROOT / "matches"
    if not matches_dir.exists():
        return []
    rows = []
    for config_path in sorted(matches_dir.glob("*/config/match.yaml"), key=lambda p: p.stat().st_mtime, reverse=True):
        rows.append(match_summary(config_path))
    return rows


def match_payload(match_id: str) -> Dict[str, Any]:
    config_path = PROJECT_ROOT / "matches" / match_id / "config" / "match.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"Match config not found: {match_id}")
    config = load_yaml(config_path)
    match = config.get("match", {})
    analyze_team = str(config.get("teams", {}).get("analyze_team", ""))
    team = config.get("teams", {}).get(analyze_team, {})
    kit = team.get("kit", {})
    return {
        "match_id": match_id,
        "match_name": match.get("name", ""),
        "video_path": match.get("video_path", ""),
        "team_key": analyze_team,
        "team_name": team.get("display_name", ""),
        "team_color": team.get("team_color") or (kit.get("field_colors") or [""])[0],
        "goalkeeper_color": (kit.get("goalkeeper_colors") or [""])[0],
        "field_length": config.get("field", {}).get("length_m", DEFAULT_FIELD_LENGTH),
        "field_width": config.get("field", {}).get("width_m", DEFAULT_FIELD_WIDTH),
        "metrics": config.get("analysis", {}).get("target_metrics", []),
        "players": team.get("players", []),
        "substitutions": team.get("substitutions", []),
        "summary": match_summary(config_path),
    }


def job_status(job: Dict[str, Any]) -> Dict[str, Any]:
    process = job.get("process")
    if process is not None:
        code = process.poll()
        if code is None:
            job["status"] = "running"
        elif code == 0:
            job["status"] = "done"
            job["return_code"] = code
        else:
            job["status"] = "failed"
            job["return_code"] = code
    log_path = Path(job["log_path"])
    tail = ""
    if log_path.exists():
        text = log_path.read_text(encoding="utf-8", errors="replace")
        tail = text[-5000:]
    return {key: value for key, value in job.items() if key != "process"} | {"log_tail": tail}


def all_job_statuses() -> List[Dict[str, Any]]:
    statuses = [job_status(job) for job in JOBS.values()]
    statuses.sort(key=lambda item: item.get("started_at", ""), reverse=True)
    return statuses


def latest_job_for_match(match_id: str) -> Optional[Dict[str, Any]]:
    for item in all_job_statuses():
        if item.get("match_id") == match_id:
            return item
    return None


def read_csv_rows(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


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


def human_review_payload(match_id: str) -> Dict[str, Any]:
    config_path = PROJECT_ROOT / "matches" / match_id / "config" / "match.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"Match config not found: {match_id}")
    config = load_yaml(config_path)
    paths = review_paths(match_id)
    manifest = load_yaml(paths["manifest"])
    corrections = load_yaml(paths["corrections"])
    if not manifest:
        return {
            "match_id": match_id,
            "ready": False,
            "status": "not_ready",
            "message": "人工校验包还没有生成。请先完成标定，或在历史分析里点击“生成校验”。",
            "players": roster_options(config),
            "items": [],
        }

    correction_items = corrections.get("items") or {}
    items = manifest.get("items") or []
    for item in items:
        review_id = str(item.get("review_id") or "")
        if review_id in correction_items:
            item["human_review"] = {**(item.get("human_review") or {}), **correction_items[review_id]}
    summary = human_review_summary(match_id)
    return {
        "match_id": match_id,
        "ready": True,
        "status": summary["status"],
        "submitted": summary["submitted"],
        "item_count": summary["item_count"],
        "pending_count": summary["pending_count"],
        "players": roster_options(config),
        "contact_sheet": manifest.get("contact_sheet", ""),
        "candidate_contact_sheet": manifest.get("candidate_contact_sheet", ""),
        "source_detections_dir": manifest.get("source_detections_dir", ""),
        "items": items,
    }


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


def write_human_review(match_id: str, reviewed_items: List[Dict[str, Any]], submit: bool) -> Dict[str, Any]:
    config_path = PROJECT_ROOT / "matches" / match_id / "config" / "match.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"Match config not found: {match_id}")
    config = load_yaml(config_path)
    paths = review_paths(match_id)
    manifest = load_yaml(paths["manifest"])
    if not manifest:
        raise FileNotFoundError("人工校验包还没有生成，无法提交。")

    item_by_id = {str(item.get("review_id")): item for item in manifest.get("items") or []}
    corrections: Dict[str, Any] = {}
    for item in reviewed_items:
        review_id = str(item.get("review_id") or "")
        if review_id not in item_by_id:
            continue
        status = str(item.get("status") or "confirmed")
        player_id = str(item.get("player_id") or "")
        report_action = str(item.get("report_action") or ("remove" if status == "ignored" else "keep"))
        correction = {
            "status": status,
            "player_id": player_id,
            "report_action": report_action,
            "note": str(item.get("note") or ""),
        }
        corrections[review_id] = correction
        item_by_id[review_id]["human_review"] = {**(item_by_id[review_id].get("human_review") or {}), **correction}

    manifest["status"] = "submitted" if submit else "draft"
    manifest["updated_at"] = now_iso()
    write_yaml(paths["manifest"], manifest)

    corrections_payload = {
        "status": "submitted" if submit else "draft",
        "updated_at": now_iso(),
        "submitted_at": now_iso() if submit else None,
        "items": corrections,
    }
    write_yaml(paths["corrections"], corrections_payload)

    source_dir = resolve_path(manifest.get("source_detections_dir", ""))
    assignments_rows = read_csv_rows(source_dir / "tracklet_identity_assignments.csv")
    track_to_player: Dict[int, str] = {}
    track_confidence: Dict[int, float] = {}
    for row in assignments_rows:
        track_id = safe_int(row.get("track_id"))
        track_to_player[track_id] = str(row.get("assigned_player_id") or "")
        track_confidence[track_id] = safe_float(row.get("identity_confidence"), 0.5)

    for review_id, correction in corrections.items():
        system_player = (item_by_id[review_id].get("system_labels") or {}).get("player") or {}
        track_id = safe_int(system_player.get("track_id"))
        if correction["status"] == "ignored" or correction["report_action"] == "remove":
            track_to_player[track_id] = ""
        else:
            track_to_player[track_id] = str(correction.get("player_id") or system_player.get("predicted_id") or "")
            track_confidence[track_id] = 0.97 if correction["status"] == "corrected" else max(track_confidence.get(track_id, 0.5), 0.9)

    players = {item["player_id"]: item for item in roster_options(config)}
    grouped: Dict[str, List[int]] = {}
    confidence_by_player: Dict[str, List[float]] = {}
    for track_id, player_id in track_to_player.items():
        if not player_id or player_id not in players:
            continue
        grouped.setdefault(player_id, []).append(track_id)
        confidence_by_player.setdefault(player_id, []).append(track_confidence.get(track_id, 0.5))

    identity_payload: Dict[str, Any] = {
        "review_required": not submit,
        "human_review": {
            "status": "submitted" if submit else "draft",
            "source_detections_dir": project_relative(source_dir),
            "updated_at": now_iso(),
            "submitted_at": now_iso() if submit else None,
            "review_item_count": len(item_by_id),
            "corrected_item_count": sum(1 for item in corrections.values() if item["status"] == "corrected"),
            "ignored_item_count": sum(1 for item in corrections.values() if item["status"] == "ignored"),
        },
        "instructions": [
            "由 Home / 人工校验 Tab 写入。",
            "最终报告会使用这些 track_id 到球员的绑定。",
        ],
        "assignments": {},
    }
    for player_id, track_ids in grouped.items():
        player = players[player_id]
        confidence_values = confidence_by_player.get(player_id) or [0.5]
        identity_payload["assignments"][player_id] = {
            "name": player.get("name"),
            "number": player.get("number"),
            "role": player.get("role"),
            "confidence": round(sum(confidence_values) / len(confidence_values), 3),
            "reason": "human review submitted" if submit else "human review draft",
            "track_ids": sorted(set(track_ids)),
        }
    write_yaml(paths["identity_review"], identity_payload)
    return human_review_summary(match_id)


def start_job(match_id: str, name: str, command: List[str]) -> Dict[str, Any]:
    project_dir = PROJECT_ROOT / "matches" / match_id
    log_dir = project_dir / "review" / "jobs"
    log_dir.mkdir(parents=True, exist_ok=True)
    job_id = f"{slugify(name)}_{int(time.time())}"
    log_path = log_dir / f"{job_id}.log"
    handle = log_path.open("w", encoding="utf-8")
    handle.write("+ " + " ".join(command) + "\n")
    handle.flush()
    process = subprocess.Popen(command, cwd=PROJECT_ROOT, stdout=handle, stderr=subprocess.STDOUT, text=True)
    job = {
        "job_id": job_id,
        "match_id": match_id,
        "name": name,
        "status": "running",
        "command": command,
        "log_path": str(log_path),
        "started_at": now_iso(),
        "process": process,
    }
    JOBS[job_id] = job
    return job_status(job)


def start_prepare_calibration(match_id: str) -> Dict[str, Any]:
    return start_job(
        match_id,
        "prepare_calibration",
        [
            sys.executable,
            "scripts/09_prepare_match_review.py",
            "--config",
            f"matches/{match_id}/config/match.yaml",
            "--step",
            "calibration",
        ],
    )


def start_prepare_human_review(match_id: str, device: str = "mps") -> Dict[str, Any]:
    command = [
        sys.executable,
        "scripts/13_prepare_human_review.py",
        "--config",
        f"matches/{match_id}/config/match.yaml",
        "--device",
        device,
    ]
    return start_job(match_id, "prepare_human_review", command)


def start_final_report(match_id: str) -> Dict[str, Any]:
    command = [
        sys.executable,
        "scripts/14_finalize_human_review.py",
        "--config",
        f"matches/{match_id}/config/match.yaml",
        "--output-name",
        "mvp_initial",
    ]
    return start_job(match_id, "final_report", command)


def start_analysis(match_id: str, device: str = "mps") -> Dict[str, Any]:
    return start_prepare_human_review(match_id, device)


def build_home_html() -> str:
    payload = json.dumps({"metrics": METRIC_DEFINITIONS, "roles": ROLE_CHOICES, "default_metrics": DEFAULT_METRICS}, ensure_ascii=False)
    template = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>五人制足球分析工作台</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7f4;
      --panel: #fff;
      --ink: #17211b;
      --muted: #66736b;
      --accent: #0f7b63;
      --line: #d8ded8;
      --warn: #9b5a00;
      --danger: #982b16;
    }
    * { box-sizing: border-box; }
    body { margin: 0; background: var(--bg); color: var(--ink); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    main { max-width: 1220px; margin: 0 auto; padding: 16px; }
    header { display: flex; align-items: center; justify-content: space-between; gap: 14px; margin-bottom: 12px; }
    h1 { margin: 0; font-size: 20px; letter-spacing: 0; }
    h2 { margin: 0; padding: 11px 12px; font-size: 15px; border-bottom: 1px solid var(--line); }
    .tabs { display: flex; gap: 8px; }
    .tab { border: 1px solid var(--line); background: #fff; border-radius: 6px; min-height: 36px; padding: 0 12px; cursor: pointer; font-weight: 700; }
    .tab.active { background: var(--accent); color: #fff; border-color: var(--accent); }
    section.panel, .panel { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; margin-bottom: 12px; overflow: visible; }
    .content { padding: 12px; }
    .grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
    label { display: grid; gap: 5px; color: var(--muted); font-size: 12px; }
    input, select, textarea, button { font: inherit; }
    input, select, textarea { width: 100%; min-height: 34px; border: 1px solid var(--line); border-radius: 6px; background: #fff; color: var(--ink); padding: 6px 8px; }
    textarea { min-height: 70px; resize: vertical; line-height: 1.45; }
    button { min-height: 34px; border: 1px solid var(--line); border-radius: 6px; background: #fff; color: var(--ink); cursor: pointer; font-weight: 700; padding: 0 12px; }
    button.primary { background: var(--accent); color: #fff; border-color: var(--accent); }
    button.danger { color: var(--danger); }
    button:disabled { cursor: not-allowed; opacity: .45; }
    .actions { display: flex; justify-content: flex-end; gap: 8px; padding: 12px; border-top: 1px solid var(--line); }
    .wide { grid-column: span 2; }
    .full { grid-column: 1 / -1; }
    .metrics { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
    .metric { position: relative; display: flex; align-items: center; gap: 8px; border: 1px solid var(--line); border-radius: 6px; padding: 8px; background: #fbfcfa; color: var(--ink); min-height: 44px; }
    .metric input { width: auto; min-height: auto; }
    .info { display: inline-flex; align-items: center; justify-content: center; width: 18px; height: 18px; border: 1px solid var(--line); border-radius: 50%; color: var(--muted); font-size: 12px; }
    .metric-tip { display: none; position: absolute; left: 8px; right: 8px; top: calc(100% + 6px); z-index: 10; padding: 9px 10px; border: 1px solid var(--line); border-radius: 6px; background: #17211b; color: #fff; font-size: 12px; line-height: 1.45; box-shadow: 0 8px 24px rgba(0,0,0,.18); }
    .metric:hover .metric-tip, .metric:focus-within .metric-tip { display: block; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { border-bottom: 1px solid var(--line); padding: 7px; text-align: left; vertical-align: top; }
    th { background: #f9faf7; color: var(--muted); font-weight: 700; }
    .table-wrap { overflow: auto; }
    .hidden { display: none; }
    .status { color: var(--muted); font-size: 13px; }
    .saved { color: var(--accent); font-weight: 800; }
    .failed { color: var(--warn); font-weight: 800; }
    .history-row { display: grid; grid-template-columns: 1.5fr 1fr 1fr 1.2fr auto; gap: 10px; align-items: center; border-bottom: 1px solid var(--line); padding: 10px 12px; }
    .history-row:last-child { border-bottom: 0; }
    .row-actions { display: flex; gap: 6px; justify-content: flex-end; flex-wrap: wrap; }
    a { color: var(--accent); text-decoration: none; font-weight: 700; }
    code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
    pre { white-space: pre-wrap; max-height: 240px; overflow: auto; background: #20251f; color: #eef3ea; padding: 10px; border-radius: 6px; }
    .calibration-frame { width: 100%; height: 72vh; border: 0; display: block; background: #20251f; }
    .inline-status { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
    .review-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; padding: 12px; }
    .review-card { border: 1px solid var(--line); border-radius: 8px; overflow: hidden; background: #fff; }
    .review-image-button { width: 100%; min-height: 0; padding: 0; border: 0; border-radius: 0; display: block; background: #20251f; cursor: zoom-in; }
    .review-card img { width: 100%; display: block; background: #20251f; aspect-ratio: 16 / 9; object-fit: contain; }
    .review-card .body { display: grid; gap: 8px; padding: 10px; }
    .review-meta { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; color: var(--muted); font-size: 12px; }
    .pill { display: inline-flex; align-items: center; min-height: 22px; padding: 0 7px; border: 1px solid var(--line); border-radius: 999px; background: #f9faf7; color: var(--muted); font-size: 12px; font-weight: 700; }
    .sheet-links { display: flex; gap: 12px; flex-wrap: wrap; }
    .empty-state { padding: 18px 12px; color: var(--muted); }
    .review-field-title { color: var(--muted); font-size: 12px; }
    .radio-row { display: flex; gap: 8px; flex-wrap: wrap; }
    .radio-choice { display: inline-flex; align-items: center; gap: 6px; min-height: 34px; border: 1px solid var(--line); border-radius: 6px; padding: 0 10px; background: #fbfcfa; color: var(--ink); cursor: pointer; font-weight: 700; }
    .radio-choice input { width: auto; min-height: auto; }
    .radio-choice:has(input:checked) { border-color: var(--accent); background: #eaf5f1; color: var(--accent); }
    body.modal-open { overflow: hidden; }
    .image-modal { position: fixed; inset: 0; z-index: 1000; display: none; grid-template-rows: auto 1fr; background: rgba(12, 17, 14, .94); color: #fff; }
    .image-modal.open { display: grid; }
    .image-modal-toolbar { min-height: 52px; display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 8px 12px; border-bottom: 1px solid rgba(255,255,255,.16); background: rgba(12, 17, 14, .92); }
    .modal-title { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 800; }
    .modal-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .modal-actions button { border-color: rgba(255,255,255,.22); background: rgba(255,255,255,.08); color: #fff; min-width: 42px; }
    .zoom-label { min-width: 56px; text-align: center; color: rgba(255,255,255,.78); font-weight: 800; }
    .image-modal-stage { min-width: 0; min-height: 0; display: grid; place-items: center; overflow: hidden; touch-action: none; cursor: grab; }
    .image-modal-stage.dragging { cursor: grabbing; }
    .modal-image { max-width: 96vw; max-height: calc(100vh - 76px); transform-origin: center center; will-change: transform; user-select: none; -webkit-user-drag: none; box-shadow: 0 18px 60px rgba(0,0,0,.45); }
    .feedback-modal { position: fixed; inset: 0; z-index: 1100; display: none; place-items: center; padding: 20px; background: rgba(12, 17, 14, .45); }
    .feedback-modal.open { display: grid; }
    .feedback-card { width: min(560px, 100%); background: #fff; border: 1px solid var(--line); border-radius: 8px; box-shadow: 0 18px 60px rgba(0,0,0,.24); overflow: hidden; }
    .feedback-card h2 { border-bottom: 1px solid var(--line); }
    .feedback-body { padding: 14px; display: grid; gap: 10px; color: var(--ink); line-height: 1.5; }
    .feedback-body ul { margin: 0; padding-left: 20px; }
    @media (max-width: 980px) { .grid, .metrics { grid-template-columns: 1fr 1fr; } .history-row { grid-template-columns: 1fr; } }
    @media (max-width: 980px) { .review-grid { grid-template-columns: 1fr; } }
    @media (max-width: 640px) { .grid, .metrics { grid-template-columns: 1fr; } .wide { grid-column: auto; } header { align-items: flex-start; flex-direction: column; } }
  </style>
</head>
<body>
<main>
  <header>
    <h1>五人制足球分析工作台</h1>
    <div class="tabs">
      <button class="tab active" data-tab="new">新增比赛分析</button>
      <button class="tab" data-tab="calibration">场地标定与运行</button>
      <button class="tab" data-tab="review">人工校验</button>
      <button class="tab" data-tab="history">查看历史分析</button>
    </div>
  </header>

  <section id="jobPanel" class="panel hidden">
    <h2>后台任务状态</h2>
    <div class="content">
      <div id="jobStatus" class="status"></div>
      <pre id="jobLog"></pre>
    </div>
  </section>

  <div id="newTab">
    <section class="panel">
      <h2>比赛与球队基础信息</h2>
      <div class="content grid">
        <label>比赛名称<input id="matchName" placeholder="2026-05-06 红队训练赛"></label>
        <label>球队名称<input id="teamName" placeholder="红队"></label>
        <label>球队主色<input id="teamColor" placeholder="pink_red"></label>
        <label>门将颜色<input id="goalkeeperColor" placeholder="yellow"></label>
        <label class="wide">选择视频<select id="videoSelect"></select></label>
        <label class="wide">或指定视频路径<input id="videoPath" placeholder="videos/raw/example.mp4"></label>
      </div>
    </section>

    <section class="panel">
      <h2>分析指标</h2>
      <div class="content">
        <div id="metrics" class="metrics"></div>
      </div>
    </section>

    <section class="panel">
      <h2>人员与特征</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>球员 ID</th><th>代号/名称</th><th>号码</th><th>位置</th><th>重点特征</th><th></th></tr></thead>
          <tbody id="playersBody"></tbody>
        </table>
      </div>
      <div class="actions"><button id="addPlayer">添加球员</button></div>
    </section>

    <section class="panel">
      <h2>场地标定</h2>
      <div class="content grid">
        <label>球场长度米<input id="fieldLength" type="number" step="0.1" value="40"></label>
        <label>球场宽度米<input id="fieldWidth" type="number" step="0.1" value="20"></label>
        <label class="wide">标定状态<input id="calibrationStatus" disabled value="未创建项目"></label>
        <div class="full status" id="workflowStatus">填写完整信息后，确认写入 match.yaml，并进入场地标定。</div>
      </div>
      <div class="actions">
        <button id="resetForm">清空</button>
        <button id="openCalibration" disabled>打开场地标定</button>
        <button id="submitMatch" class="primary">确认并写入 match.yaml，准备标定</button>
      </div>
    </section>

  </div>

  <div id="calibrationTab" class="hidden">
    <section class="panel">
      <h2>场地标定与运行</h2>
      <div class="content">
        <div class="inline-status">
          <div class="status" id="calibrationTabStatus">请先在新增页创建比赛，或在历史页选择一个比赛进行标定。</div>
          <div class="row-actions">
            <button id="refreshCalibration">刷新标定页</button>
            <button id="backToHistory">查看历史</button>
          </div>
        </div>
      </div>
      <iframe id="calibrationFrame" class="calibration-frame" title="场地标定"></iframe>
    </section>
  </div>

  <div id="reviewTab" class="hidden">
    <section class="panel">
      <h2>人工校验</h2>
      <div class="content">
        <div class="inline-status">
          <div class="status" id="reviewStatus">请先完成场地标定并生成校验包，或在历史分析里选择一个比赛。</div>
          <div class="row-actions">
            <button id="refreshReview">刷新校验</button>
            <button id="confirmAllReview">全部按系统确认</button>
            <button id="saveReview">保存草稿</button>
            <button id="submitReview" class="primary">提交人工校验并生成最终报告</button>
          </div>
        </div>
        <div id="reviewSheets" class="sheet-links"></div>
      </div>
      <div id="reviewItems" class="review-grid"></div>
    </section>
  </div>

  <div id="historyTab" class="hidden">
    <section class="panel">
      <h2>历史分析</h2>
      <div id="historyRows"></div>
    </section>
  </div>
</main>

<div id="imageModal" class="image-modal hidden" aria-hidden="true">
  <div class="image-modal-toolbar">
    <div id="modalTitle" class="modal-title"></div>
    <div class="modal-actions">
      <button id="modalZoomOut">-</button>
      <span id="modalZoomLabel" class="zoom-label">100%</span>
      <button id="modalZoomIn">+</button>
      <button id="modalReset">重置</button>
      <button id="modalClose">关闭</button>
    </div>
  </div>
  <div id="modalStage" class="image-modal-stage">
    <img id="modalImage" class="modal-image" alt="">
  </div>
</div>

<div id="feedbackModal" class="feedback-modal hidden" aria-hidden="true">
  <div class="feedback-card">
    <h2 id="feedbackTitle">状态反馈</h2>
    <div id="feedbackBody" class="feedback-body"></div>
    <div class="actions">
      <button id="feedbackHistory">查看历史分析</button>
      <button id="feedbackOk" class="primary">知道了</button>
    </div>
  </div>
</div>

<script>
const BOOT = __BOOT__;
let editingMatchId = null;
let currentMatchId = null;
let currentJobId = null;
let players = [];
let reviewItems = [];
let reviewPlayers = [];
let jobPollTimer = null;
let modalScale = 1;
let modalX = 0;
let modalY = 0;
let modalDragging = false;
let modalDragStartX = 0;
let modalDragStartY = 0;
let modalOriginX = 0;
let modalOriginY = 0;

function $(id) { return document.getElementById(id); }
function slug(value) {
  return String(value || '').trim().toLowerCase()
    .replace(/[^a-z0-9\u4e00-\u9fff]+/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_+|_+$/g, '') || 'item';
}
function teamKey() { return slug($('teamName').value || $('teamColor').value || 'team'); }
function generatedPlayerId(player) { return `${teamKey()}_${slug(player.name || 'player')}`; }
function setWorkflow(message, cls='status') { $('workflowStatus').textContent = message; $('workflowStatus').className = cls; }
function splitList(value) { return String(value || '').split(',').map(x => x.trim()).filter(Boolean); }
function escapeHtml(value) {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function showTab(name) {
  document.querySelectorAll('.tab').forEach(btn => btn.classList.toggle('active', btn.dataset.tab === name));
  $('newTab').classList.toggle('hidden', name !== 'new');
  $('calibrationTab').classList.toggle('hidden', name !== 'calibration');
  $('reviewTab').classList.toggle('hidden', name !== 'review');
  $('historyTab').classList.toggle('hidden', name !== 'history');
  if (name === 'review' && currentMatchId) loadHumanReview(currentMatchId);
  if (name === 'history') loadHistory();
}

function renderJob(result) {
  $('jobPanel').classList.remove('hidden');
  $('jobStatus').textContent = `${result.match_id || ''} / ${result.name}: ${result.status}`;
  $('jobLog').textContent = result.log_tail || '';
}

async function pollAllJobs() {
  try {
    const result = await api('/api/jobs');
    const active = result.jobs.find(job => job.status === 'running') || result.jobs[0];
    if (active) {
      renderJob(active);
      if (active.status === 'done' || active.status === 'failed') loadHistory();
    }
  } catch (error) {
    console.warn(error);
  } finally {
    jobPollTimer = setTimeout(pollAllJobs, 3500);
  }
}

function clampZoom(value) {
  return Math.max(0.5, Math.min(8, value));
}

function updateModalTransform() {
  $('modalImage').style.transform = `translate(${modalX}px, ${modalY}px) scale(${modalScale})`;
  $('modalZoomLabel').textContent = `${Math.round(modalScale * 100)}%`;
}

function resetModalView() {
  modalScale = 1;
  modalX = 0;
  modalY = 0;
  updateModalTransform();
}

function zoomModal(multiplier, event=null) {
  const previous = modalScale;
  const next = clampZoom(previous * multiplier);
  if (next === previous) return;
  if (event) {
    const rect = $('modalStage').getBoundingClientRect();
    const centerX = event.clientX - rect.left - rect.width / 2;
    const centerY = event.clientY - rect.top - rect.height / 2;
    const ratio = next / previous;
    modalX = centerX - (centerX - modalX) * ratio;
    modalY = centerY - (centerY - modalY) * ratio;
  }
  modalScale = next;
  updateModalTransform();
}

function openImageModal(src, title='') {
  $('modalImage').src = src;
  $('modalTitle').textContent = title;
  $('imageModal').classList.add('open');
  $('imageModal').classList.remove('hidden');
  $('imageModal').setAttribute('aria-hidden', 'false');
  document.body.classList.add('modal-open');
  resetModalView();
}

function closeImageModal() {
  $('imageModal').classList.remove('open');
  $('imageModal').classList.add('hidden');
  $('imageModal').setAttribute('aria-hidden', 'true');
  $('modalImage').src = '';
  document.body.classList.remove('modal-open');
  modalDragging = false;
}

function showFeedback(title, lines) {
  $('feedbackTitle').textContent = title;
  const bodyLines = Array.isArray(lines) ? lines : [String(lines || '')];
  $('feedbackBody').innerHTML = `<ul>${bodyLines.map(line => `<li>${escapeHtml(line)}</li>`).join('')}</ul>`;
  $('feedbackModal').classList.add('open');
  $('feedbackModal').classList.remove('hidden');
  $('feedbackModal').setAttribute('aria-hidden', 'false');
}

function closeFeedback() {
  $('feedbackModal').classList.remove('open');
  $('feedbackModal').classList.add('hidden');
  $('feedbackModal').setAttribute('aria-hidden', 'true');
}

function showCalibration(matchId) {
  currentMatchId = matchId;
  $('calibrationFrame').src = `/calibration?match_id=${encodeURIComponent(matchId)}`;
  $('calibrationTabStatus').textContent = `当前标定项目: ${matchId}。提交标定后会先生成系统预标注的人工校验包，进度会显示在上方任务状态里。`;
  showTab('calibration');
}

function showReview(matchId) {
  currentMatchId = matchId;
  showTab('review');
  loadHumanReview(matchId);
}

function renderMetrics(selected = BOOT.default_metrics) {
  const container = $('metrics');
  container.innerHTML = '';
  BOOT.metrics.forEach(metric => {
    const label = document.createElement('label');
    label.className = 'metric';
    const escaped = metric.description.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    label.innerHTML = `<input type="checkbox" value="${metric.key}"><span>${metric.label}</span><span class="info">?</span><span class="metric-tip">${escaped}</span>`;
    label.querySelector('input').checked = selected.includes(metric.key);
    container.appendChild(label);
  });
}

function roleOptions(value) {
  return BOOT.roles.map(role => `<option value="${role}" ${role === value ? 'selected' : ''}>${role}</option>`).join('');
}

function renderPlayers() {
  const body = $('playersBody');
  body.innerHTML = '';
  players.forEach((player, index) => {
    player.player_id = generatedPlayerId(player);
    const row = document.createElement('tr');
    row.innerHTML = `
      <td><code>${player.player_id}</code></td>
      <td><input data-field="name" data-index="${index}" value="${player.name || ''}" placeholder="HDA"></td>
      <td><input data-field="number" data-index="${index}" value="${player.number || ''}" placeholder="1"></td>
      <td><select data-field="role" data-index="${index}">${roleOptions(player.role || BOOT.roles[Math.min(index, 4)])}</select></td>
      <td><input data-field="visual_hint" data-index="${index}" value="${player.visual_hint || ''}" placeholder="黄色门将服/绿色球鞋"></td>
      <td><button class="danger" data-remove="${index}">删除</button></td>`;
    body.appendChild(row);
  });
  body.querySelectorAll('input, select').forEach(input => {
    input.addEventListener('input', event => {
      const index = Number(event.target.dataset.index);
      const field = event.target.dataset.field;
      players[index][field] = event.target.value;
      if (field === 'name') renderPlayers();
    });
  });
  body.querySelectorAll('[data-remove]').forEach(btn => btn.addEventListener('click', () => {
    players.splice(Number(btn.dataset.remove), 1);
    renderPlayers();
  }));
}

function selectedMetrics() {
  return Array.from(document.querySelectorAll('#metrics input:checked')).map(input => input.value);
}

function selectedVideoPath() {
  return $('videoPath').value.trim() || $('videoSelect').value.trim();
}

function collectPayload() {
  return {
    match_name: $('matchName').value.trim(),
    team_name: $('teamName').value.trim(),
    team_key: teamKey(),
    team_color: $('teamColor').value.trim(),
    goalkeeper_color: $('goalkeeperColor').value.trim(),
    video_path: selectedVideoPath(),
    field_length: Number($('fieldLength').value || 40),
    field_width: Number($('fieldWidth').value || 20),
    metrics: selectedMetrics(),
    players: players.map(player => ({ name: player.name, number: player.number, role: player.role, visual_hint: player.visual_hint })),
    substitutions: []
  };
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok || result.ok === false) throw new Error(result.error || `HTTP ${response.status}`);
  return result;
}

async function loadVideos() {
  const result = await api('/api/videos');
  const select = $('videoSelect');
  select.innerHTML = '<option value="">选择项目内视频</option>';
  result.videos.forEach(video => {
    const option = document.createElement('option');
    option.value = video.path;
    option.textContent = video.name;
    select.appendChild(option);
  });
}

async function submitMatch() {
  try {
    setWorkflow('写入 match.yaml 中...');
    const payload = collectPayload();
    const endpoint = editingMatchId ? `/api/update_match?match_id=${encodeURIComponent(editingMatchId)}` : '/api/create_match';
    const result = await api(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    currentMatchId = result.match_id;
    editingMatchId = result.match_id;
    $('openCalibration').disabled = false;
    $('openCalibration').onclick = () => showCalibration(currentMatchId);
    $('calibrationStatus').value = '已创建项目，正在准备标定帧';
    setWorkflow(`已写入 ${result.config_path}，正在准备场地标定...`, 'saved');
    if (result.job) {
      currentJobId = result.job.job_id;
      $('jobPanel').classList.remove('hidden');
      pollJob();
    }
  } catch (error) {
    setWorkflow(`失败: ${error.message}`, 'failed');
  }
}

async function pollJob() {
  if (!currentJobId) return;
  const result = await api(`/api/job?job_id=${encodeURIComponent(currentJobId)}`);
  $('jobStatus').textContent = `${result.name}: ${result.status}`;
  $('jobLog').textContent = result.log_tail || '';
    if (result.status === 'done') {
      if (result.name === 'prepare_calibration') {
        $('calibrationStatus').value = '标定帧已准备，可打开场地标定';
        setWorkflow('标定帧已准备。已切换到场地标定页，提交打标后会生成校验包。', 'saved');
        if (currentMatchId) showCalibration(currentMatchId);
      } else if (result.name === 'prepare_human_review') {
        setWorkflow('人工校验包已生成。已切换到人工校验页，请确认或修正系统预标注。', 'saved');
        if (currentMatchId) showReview(currentMatchId);
      } else if (result.name === 'final_report') {
        setWorkflow('最终报告已生成。', 'saved');
        showFeedback('最终报告已生成', [
          `当前状态：${result.match_id || currentMatchId || '当前比赛'} 的最终报告已完成。`,
          '接下来：请在“查看历史分析”里打开 HTML 或 MD 报告，重点检查“重点指标参考值（低置信）”和球员评分。',
          '如发现身份仍有问题，可以回到“人工校验”继续修正后再次提交。'
        ]);
        showTab('history');
      }
      loadHistory();
  } else if (result.status === 'failed') {
    setWorkflow('后台任务失败，请查看日志。', 'failed');
  } else {
    setTimeout(pollJob, 2500);
  }
}

function playerOptionsHtml(value) {
  const roster = reviewPlayers.map(player => `<option value="${escapeHtml(player.player_id)}" ${player.player_id === value ? 'selected' : ''}>${escapeHtml(player.label)}</option>`).join('');
  return `<option value="" ${!value ? 'selected' : ''}>不纳入本队/不确定</option>${roster}`;
}

function statusChoicesHtml(index, value) {
  const options = [
    ['confirmed', '确认正确'],
    ['corrected', '已修正'],
    ['ignored', '忽略该轨迹']
  ];
  return options.map(([key, label]) => `
    <label class="radio-choice">
      <input type="radio" name="review_status_${index}" data-review-field="status" data-index="${index}" value="${key}" ${key === value ? 'checked' : ''}>
      <span>${label}</span>
    </label>`).join('');
}

function renderHumanReview(data) {
  reviewItems = data.items || [];
  reviewPlayers = data.players || [];
  $('reviewStatus').textContent = data.ready
    ? `当前项目: ${data.match_id}，状态: ${data.status}，校验项: ${data.item_count || reviewItems.length}，待处理: ${data.pending_count || 0}`
    : (data.message || '人工校验包还没有生成。');
  $('reviewSheets').innerHTML = '';
  if (data.contact_sheet) {
    $('reviewSheets').innerHTML += `<a href="/asset?path=${encodeURIComponent(data.contact_sheet)}" target="_blank">Tracklet 总览图</a>`;
  }
  if (data.candidate_contact_sheet) {
    $('reviewSheets').innerHTML += `<a href="/asset?path=${encodeURIComponent(data.candidate_contact_sheet)}" target="_blank">颜色候选总览图</a>`;
  }
  const box = $('reviewItems');
  box.innerHTML = '';
  if (!data.ready) {
    box.innerHTML = '<div class="empty-state">还没有可校验内容。完成场地标定后，系统会先抽样关键 tracklet 并给出球员预标注。</div>';
    return;
  }
  if (!reviewItems.length) {
    box.innerHTML = '<div class="empty-state">校验包为空，请重新生成校验包。</div>';
    return;
  }
  reviewItems.forEach((item, index) => {
    const human = item.human_review || {};
    const player = (item.system_labels && item.system_labels.player) || {};
    const status = human.status === 'pending' ? 'confirmed' : (human.status || 'confirmed');
    const playerId = human.player_id || player.predicted_id || '';
    const image = item.frame_image ? `/asset?path=${encodeURIComponent(item.frame_image)}` : '';
    const imageTitle = `${item.timestamp || ''} / track ${player.track_id || ''} / ${player.name || ''} ${player.number || ''}`;
    const card = document.createElement('div');
    card.className = 'review-card';
    card.innerHTML = `
      ${image ? `<button class="review-image-button" data-image-src="${escapeHtml(image)}" data-image-title="${escapeHtml(imageTitle)}"><img src="${escapeHtml(image)}" alt="review frame ${escapeHtml(item.review_id)}"></button>` : '<div class="empty-state">无截图</div>'}
      <div class="body">
        <div class="review-meta">
          <span class="pill">${escapeHtml(item.timestamp || '')}</span>
          <span class="pill">track ${escapeHtml(player.track_id || '')}</span>
          <span class="pill">${escapeHtml(item.reason || '')}</span>
          <span class="pill">置信度 ${escapeHtml(player.confidence || '')}</span>
        </div>
        <div><strong>系统预判：</strong>${escapeHtml(player.name || '')} / ${escapeHtml(player.number || '')} / ${escapeHtml(player.role || '')}</div>
        <div>
          <div class="review-field-title">人工判断</div>
          <div class="radio-row">${statusChoicesHtml(index, status)}</div>
        </div>
        <label>正确球员
          <select data-review-field="player_id" data-index="${index}">${playerOptionsHtml(playerId)}</select>
        </label>
        <label>备注
          <input data-review-field="note" data-index="${index}" value="${escapeHtml(human.note || '')}" placeholder="可选">
        </label>
      </div>`;
    box.appendChild(card);
  });
  box.querySelectorAll('[data-review-field]').forEach(input => {
    input.addEventListener('input', event => {
      const index = Number(event.target.dataset.index);
      const field = event.target.dataset.reviewField;
      reviewItems[index].human_review = reviewItems[index].human_review || {};
      reviewItems[index].human_review[field] = event.target.value;
      if (field === 'player_id') {
        const predicted = reviewItems[index].system_labels.player.predicted_id || '';
        const nextStatus = event.target.value === predicted ? 'confirmed' : 'corrected';
        reviewItems[index].human_review.status = nextStatus;
        const statusRadio = box.querySelector(`input[name="review_status_${index}"][value="${nextStatus}"]`);
        if (statusRadio) statusRadio.checked = true;
      }
    });
  });
  box.querySelectorAll('.review-image-button').forEach(button => {
    button.addEventListener('click', () => openImageModal(button.dataset.imageSrc, button.dataset.imageTitle || ''));
  });
}

async function loadHumanReview(matchId) {
  try {
    const result = await api(`/api/human_review?match_id=${encodeURIComponent(matchId)}`);
    renderHumanReview(result.review);
  } catch (error) {
    $('reviewStatus').textContent = `加载失败: ${error.message}`;
    $('reviewItems').innerHTML = '';
  }
}

function collectReviewItems() {
  return reviewItems.map(item => {
    const human = item.human_review || {};
    const player = (item.system_labels && item.system_labels.player) || {};
    return {
      review_id: item.review_id,
      status: human.status || 'confirmed',
      player_id: human.player_id || player.predicted_id || '',
      report_action: human.report_action || 'keep',
      note: human.note || ''
    };
  });
}

function confirmAllReviewItems() {
  reviewItems.forEach(item => {
    const player = (item.system_labels && item.system_labels.player) || {};
    item.human_review = item.human_review || {};
    item.human_review.status = 'confirmed';
    item.human_review.player_id = player.predicted_id || '';
    item.human_review.report_action = 'keep';
  });
  renderHumanReview({
    ready: true,
    match_id: currentMatchId,
    status: 'draft',
    item_count: reviewItems.length,
    pending_count: 0,
    players: reviewPlayers,
    items: reviewItems
  });
}

async function saveHumanReview(submit=false) {
  if (!currentMatchId) {
    alert('请先选择比赛。');
    return;
  }
  try {
    const result = await api('/api/save_human_review', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ match_id: currentMatchId, submit, items: collectReviewItems() })
    });
    $('reviewStatus').textContent = submit ? '人工校验已提交，最终报告生成中。' : '人工校验草稿已保存。';
    if (result.job) {
      currentJobId = result.job.job_id;
      renderJob(result.job);
      showFeedback('人工校验已提交', [
        `当前状态：${currentMatchId} 的人工校验结果已保存。`,
        '后台任务：正在根据人工校验结果重新绑定球员轨迹并生成最终 Markdown / HTML / CSV 报告。',
        '接下来：可以留在当前页查看后台任务状态；任务完成后，到“查看历史分析”打开最新报告。'
      ]);
      pollJob();
    } else {
      if (submit) {
        showFeedback('人工校验已提交', [
          `当前状态：${currentMatchId} 的人工校验结果已保存。`,
          '接下来：如果未自动生成报告，请在历史分析里重新提交或查看后台任务。'
        ]);
      }
      loadHumanReview(currentMatchId);
    }
    loadHistory();
  } catch (error) {
    $('reviewStatus').textContent = `保存失败: ${error.message}`;
  }
}

async function loadHistory() {
  const result = await api('/api/matches');
  const box = $('historyRows');
  box.innerHTML = '';
  if (!result.matches.length) {
    box.innerHTML = '<div class="content status">暂无历史比赛项目。</div>';
    return;
  }
  result.matches.forEach(item => {
    const row = document.createElement('div');
    row.className = 'history-row';
    const report = item.report_ready ? `<a href="/report?path=${encodeURIComponent(item.report_html)}" target="_blank">HTML</a> · <a href="/report?path=${encodeURIComponent(item.report_md)}" target="_blank">MD</a>` : '未生成';
    row.innerHTML = `
      <div><strong>${item.name}</strong><br><span class="status"><code>${item.match_id}</code></span></div>
      <div>${item.team_name || ''}</div>
      <div>${item.status}</div>
      <div>${report}</div>
      <div class="row-actions">
        <button data-edit="${item.match_id}">调整设置</button>
        <button data-cal="${item.match_id}">场地标定</button>
        <button data-review="${item.match_id}">人工校验</button>
        <button class="primary" data-run="${item.match_id}">生成校验</button>
      </div>`;
    box.appendChild(row);
  });
  box.querySelectorAll('[data-edit]').forEach(btn => btn.addEventListener('click', () => loadMatchForEdit(btn.dataset.edit)));
  box.querySelectorAll('[data-cal]').forEach(btn => btn.addEventListener('click', () => showCalibration(btn.dataset.cal)));
  box.querySelectorAll('[data-review]').forEach(btn => btn.addEventListener('click', () => showReview(btn.dataset.review)));
  box.querySelectorAll('[data-run]').forEach(btn => btn.addEventListener('click', () => runAnalysis(btn.dataset.run)));
}

async function loadMatchForEdit(matchId) {
  const result = await api(`/api/match?match_id=${encodeURIComponent(matchId)}`);
  const data = result.match;
  editingMatchId = matchId;
  currentMatchId = matchId;
  $('matchName').value = data.match_name || '';
  $('teamName').value = data.team_name || '';
  $('teamColor').value = data.team_color || '';
  $('goalkeeperColor').value = data.goalkeeper_color || '';
  $('videoPath').value = data.video_path || '';
  $('fieldLength').value = data.field_length || 40;
  $('fieldWidth').value = data.field_width || 20;
  players = (data.players || []).map(player => ({ name: player.name, number: player.number, role: player.role, visual_hint: player.visual_hint }));
  renderPlayers();
  renderMetrics(data.metrics || BOOT.default_metrics);
  $('submitMatch').textContent = '保存设置并准备重新分析';
  $('openCalibration').disabled = false;
  $('openCalibration').onclick = () => showCalibration(matchId);
  showTab('new');
  setWorkflow(`正在调整历史项目 ${matchId}。保存后可重新分析。`, 'saved');
}

async function runAnalysis(matchId) {
  try {
    const result = await api('/api/start_analysis', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ match_id: matchId, device: 'mps' })
    });
    currentJobId = result.job.job_id;
    renderJob(result.job);
    showTab('new');
    setWorkflow(`已启动 ${matchId} 的识别与人工校验包生成。`, 'saved');
    pollJob();
  } catch (error) {
    alert(`启动失败: ${error.message}`);
  }
}

function resetForm() {
  editingMatchId = null;
  currentMatchId = null;
  currentJobId = null;
  $('matchName').value = '';
  $('teamName').value = '';
  $('teamColor').value = 'pink_red';
  $('goalkeeperColor').value = 'yellow';
  $('videoPath').value = '';
  $('fieldLength').value = 40;
  $('fieldWidth').value = 20;
  $('calibrationStatus').value = '未创建项目';
  $('submitMatch').textContent = '确认并写入 match.yaml，准备标定';
  $('openCalibration').disabled = true;
  renderMetrics();
  players = [
    { name: 'HDA', number: 1, role: 'goalkeeper', visual_hint: '黄色门将服' },
    { name: 'LXY', number: 2, role: 'defender', visual_hint: '' },
    { name: 'TYX', number: 18, role: 'right_forward', visual_hint: '绿色球鞋' },
    { name: 'JYN', number: 9, role: 'center_forward', visual_hint: '' },
    { name: 'ZMC', number: 28, role: 'left_forward', visual_hint: '' }
  ];
  renderPlayers();
  setWorkflow('填写完整信息后，确认写入 match.yaml，并进入场地标定。');
}

document.querySelectorAll('.tab').forEach(btn => btn.addEventListener('click', () => showTab(btn.dataset.tab)));
$('addPlayer').addEventListener('click', () => { players.push({ name: '', number: '', role: 'substitute', visual_hint: '' }); renderPlayers(); });
$('submitMatch').addEventListener('click', submitMatch);
$('resetForm').addEventListener('click', resetForm);
$('refreshCalibration').addEventListener('click', () => { if (currentMatchId) showCalibration(currentMatchId); });
$('backToHistory').addEventListener('click', () => showTab('history'));
$('refreshReview').addEventListener('click', () => { if (currentMatchId) loadHumanReview(currentMatchId); });
$('confirmAllReview').addEventListener('click', confirmAllReviewItems);
$('saveReview').addEventListener('click', () => saveHumanReview(false));
$('submitReview').addEventListener('click', () => saveHumanReview(true));
$('modalClose').addEventListener('click', closeImageModal);
$('modalZoomIn').addEventListener('click', () => zoomModal(1.25));
$('modalZoomOut').addEventListener('click', () => zoomModal(0.8));
$('modalReset').addEventListener('click', resetModalView);
$('feedbackOk').addEventListener('click', closeFeedback);
$('feedbackHistory').addEventListener('click', () => { closeFeedback(); showTab('history'); });
$('modalStage').addEventListener('wheel', event => {
  if (!$('imageModal').classList.contains('open')) return;
  event.preventDefault();
  zoomModal(event.deltaY < 0 ? 1.18 : 0.85, event);
}, { passive: false });
$('modalStage').addEventListener('pointerdown', event => {
  if (!$('imageModal').classList.contains('open')) return;
  modalDragging = true;
  modalDragStartX = event.clientX;
  modalDragStartY = event.clientY;
  modalOriginX = modalX;
  modalOriginY = modalY;
  $('modalStage').classList.add('dragging');
  $('modalStage').setPointerCapture(event.pointerId);
  event.preventDefault();
});
$('modalStage').addEventListener('pointermove', event => {
  if (!modalDragging) return;
  modalX = modalOriginX + event.clientX - modalDragStartX;
  modalY = modalOriginY + event.clientY - modalDragStartY;
  updateModalTransform();
});
$('modalStage').addEventListener('pointerup', event => {
  modalDragging = false;
  $('modalStage').classList.remove('dragging');
  try { $('modalStage').releasePointerCapture(event.pointerId); } catch (error) {}
});
$('modalStage').addEventListener('pointercancel', () => {
  modalDragging = false;
  $('modalStage').classList.remove('dragging');
});
$('modalImage').addEventListener('dblclick', event => {
  zoomModal(modalScale < 2 ? 2 / modalScale : 0.5, event);
});
document.addEventListener('keydown', event => {
  if ($('feedbackModal').classList.contains('open') && event.key === 'Escape') {
    closeFeedback();
    return;
  }
  if (!$('imageModal').classList.contains('open')) return;
  if (event.key === 'Escape') closeImageModal();
  if (event.key === '+' || event.key === '=') zoomModal(1.25);
  if (event.key === '-') zoomModal(0.8);
  if (event.key === '0') resetModalView();
});
$('teamName').addEventListener('input', renderPlayers);
$('teamColor').addEventListener('input', renderPlayers);
window.addEventListener('message', event => {
  if (event.data && event.data.type === 'analysis_job_started' && event.data.job) {
    currentJobId = event.data.job.job_id;
    renderJob(event.data.job);
    setWorkflow('已提交场地标定，正在生成系统预标注的人工校验包。', 'saved');
    loadHistory();
    pollJob();
  }
});
renderMetrics();
resetForm();
loadVideos();
loadHistory();
pollAllJobs();
</script>
</body>
</html>"""
    return template.replace("__BOOT__", payload)


class AnalysisAppServer(ThreadingHTTPServer):
    pass


class Handler(BaseHTTPRequestHandler):
    server: AnalysisAppServer

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/":
                self.send_html(build_home_html())
                return
            if parsed.path == "/api/videos":
                self.send_json({"ok": True, "videos": list_videos()})
                return
            if parsed.path == "/api/matches":
                self.send_json({"ok": True, "matches": list_matches()})
                return
            if parsed.path == "/api/match":
                match_id = self.query_one(parsed, "match_id")
                self.send_json({"ok": True, "match": match_payload(match_id)})
                return
            if parsed.path == "/api/human_review":
                match_id = self.query_one(parsed, "match_id")
                self.send_json({"ok": True, "review": human_review_payload(match_id)})
                return
            if parsed.path == "/api/job":
                job_id = self.query_one(parsed, "job_id")
                if job_id not in JOBS:
                    raise FileNotFoundError(f"Job not found: {job_id}")
                self.send_json({"ok": True, **job_status(JOBS[job_id])})
                return
            if parsed.path == "/api/jobs":
                self.send_json({"ok": True, "jobs": all_job_statuses()})
                return
            if parsed.path == "/calibration":
                match_id = self.query_one(parsed, "match_id")
                self.send_html(self.build_calibration_html(match_id))
                return
            if parsed.path == "/report":
                report_path = resolve_path(self.query_one(parsed, "path"))
                self.send_file(report_path)
                return
            if parsed.path == "/asset":
                asset_path = resolve_path(self.query_one(parsed, "path"))
                self.send_file(asset_path)
                return
            self.send_json({"ok": False, "error": "Not found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            payload = self.read_json()
            if parsed.path == "/api/create_match":
                _, config_path = create_or_update_match(payload)
                match_id = config_path.parents[1].name
                job = start_prepare_calibration(match_id)
                self.send_json({"ok": True, "match_id": match_id, "config_path": project_relative(config_path), "job": job})
                return
            if parsed.path == "/api/update_match":
                match_id = self.query_one(parsed, "match_id")
                _, config_path = create_or_update_match(payload, match_id=match_id)
                job = start_prepare_calibration(match_id)
                self.send_json({"ok": True, "match_id": match_id, "config_path": project_relative(config_path), "job": job})
                return
            if parsed.path == "/api/start_analysis":
                match_id = str(payload.get("match_id") or "").strip()
                if not match_id:
                    raise ValueError("match_id is required.")
                job = start_analysis(match_id, str(payload.get("device") or "mps"))
                self.send_json({"ok": True, "job": job})
                return
            if parsed.path == "/api/save_human_review":
                match_id = str(payload.get("match_id") or "").strip()
                if not match_id:
                    raise ValueError("match_id is required.")
                items = payload.get("items")
                if not isinstance(items, list):
                    raise ValueError("items must be a list.")
                submit = bool(payload.get("submit"))
                summary = write_human_review(match_id, items, submit=submit)
                response: Dict[str, Any] = {"ok": True, "review": summary}
                if submit:
                    response["job"] = start_final_report(match_id)
                self.send_json(response)
                return
            if parsed.path == "/api/calibration_point":
                match_id = str(payload.get("match_id") or (parse_qs(parsed.query).get("match_id") or [""])[0]).strip()
                point_name = str(payload.get("name") or "").strip()
                if not match_id or not point_name:
                    raise ValueError("match_id and point name are required.")
                points_path = self.points_path_for(match_id)
                image_xy = payload.get("image_xy")
                xy_tuple: Optional[Tuple[int, int]] = None
                if image_xy is not None:
                    if not isinstance(image_xy, list) or len(image_xy) != 2:
                        raise ValueError("image_xy must be null or [x, y].")
                    xy_tuple = (int(image_xy[0]), int(image_xy[1]))
                update_point_image_xy(points_path, point_name, xy_tuple)
                points_yaml = load_yaml(points_path)
                calibration = points_yaml.setdefault("calibration", {})
                if calibration.get("labeling_submitted") or calibration.get("labeling_status") == "submitted":
                    calibration["labeling_status"] = "draft_after_edit"
                    calibration["labeling_submitted"] = False
                    calibration["labeling_last_edited_at"] = now_iso()
                    write_yaml(points_path, points_yaml)
                self.send_json({"ok": True, "name": point_name, "image_xy": image_xy})
                return
            if parsed.path == "/api/submit_calibration":
                match_id = str(payload.get("match_id") or (parse_qs(parsed.query).get("match_id") or [""])[0]).strip()
                if not match_id:
                    raise ValueError("match_id is required.")
                result = submit_labeling(self.points_path_for(match_id))
                response: Dict[str, Any] = {"ok": True, **result}
                if payload.get("start_analysis", True):
                    response["job"] = start_prepare_human_review(match_id, str(payload.get("device") or "mps"))
                self.send_json(response)
                return
            self.send_json({"ok": False, "error": "Not found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def query_one(self, parsed: Any, key: str) -> str:
        values = parse_qs(parsed.query).get(key)
        if not values or not values[0]:
            raise ValueError(f"Missing query parameter: {key}")
        return values[0]

    def points_path_for(self, match_id: str) -> Path:
        config = load_yaml(PROJECT_ROOT / "matches" / match_id / "config" / "match.yaml")
        if not config:
            raise FileNotFoundError(f"Match not found: {match_id}")
        return resolve_path(config.get("calibration", {}).get("points_path", f"matches/{match_id}/config/calibration_points.yaml"))

    def build_calibration_html(self, match_id: str) -> str:
        config_path = PROJECT_ROOT / "matches" / match_id / "config" / "match.yaml"
        config = load_yaml(config_path)
        points_path = self.points_path_for(match_id)
        points_yaml = load_yaml_file(points_path)
        image_path_value = points_yaml.get("calibration", {}).get("image_path")
        if not image_path_value:
            raise ValueError("标定帧还没准备好，请先在主页提交比赛信息并等待标定帧生成。")
        image_path = picker_resolve_path(image_path_value)
        html = build_html(
            image_path,
            point_payload(points_yaml),
            autosave=True,
            save_endpoint=f"/api/calibration_point?match_id={match_id}",
            submit_endpoint=f"/api/submit_calibration?match_id={match_id}",
            points_label=str(points_path),
            labeling_status=str(points_yaml.get("calibration", {}).get("labeling_status") or ""),
            labeling_submitted_at=str(points_yaml.get("calibration", {}).get("labeling_submitted_at") or ""),
        )
        return (
            html.replace('href="/match"', 'href="/"')
            .replace(">比赛信息<", ">工作台<")
            .replace("提交打标", "提交打标并生成校验包")
            .replace("后台分析已启动", "人工校验包生成已启动")
        )

    def read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8") if length else "{}"
        return json.loads(body or "{}")

    def send_html(self, html: str, status: Union[int, HTTPStatus] = HTTPStatus.OK) -> None:
        payload = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(str(path))
        if path.suffix == ".html":
            content_type = "text/html; charset=utf-8"
        elif path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
            content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        else:
            content_type = "text/plain; charset=utf-8"
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, payload: Dict[str, Any], status: Union[int, HTTPStatus] = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format % args}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    server = AnalysisAppServer((args.host, args.port), Handler)
    print(f"Football analysis app: http://{args.host}:{args.port}/")
    server.serve_forever()


if __name__ == "__main__":
    main()
