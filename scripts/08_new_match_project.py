#!/usr/bin/env python3
"""Create an isolated match project with config, review, data, and report folders."""

from __future__ import annotations

import argparse
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import yaml

try:
    from analysis_metrics import DEFAULT_METRICS
    from analysis_app_config import default_calibration_points
except ModuleNotFoundError:
    from scripts.analysis_metrics import DEFAULT_METRICS
    from scripts.analysis_app_config import default_calibration_points


PROJECT_ROOT = Path(__file__).resolve().parents[1]

ROLE_CHOICES = ["goalkeeper", "defender", "right_forward", "center_forward", "left_forward", "substitute"]
COLOR_ALIASES = {
    "红": "pink_red",
    "红色": "pink_red",
    "粉": "pink_red",
    "粉色": "pink_red",
    "粉红": "pink_red",
    "橙": "orange",
    "橙色": "orange",
    "黄": "yellow",
    "黄色": "yellow",
    "绿": "green",
    "绿色": "green",
    "蓝": "blue",
    "蓝色": "blue",
    "紫": "purple",
    "紫色": "purple",
    "白": "white",
    "白色": "white",
    "黑": "dark",
    "黑色": "dark",
    "深色": "dark",
}


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


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "match"


def default_match_id(video_path: Path) -> str:
    today = datetime.now().strftime("%Y%m%d")
    return slugify(f"{video_path.stem}_{today}")[:64]


def ask(prompt: str, default: Optional[str] = None) -> str:
    suffix = f" [{default}]" if default not in {None, ""} else ""
    value = input(f"{prompt}{suffix}: ").strip()
    if value:
        return value
    return default or ""


def parse_csv(value: str) -> List[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def normalize_color(value: str) -> str:
    value = value.strip()
    return COLOR_ALIASES.get(value, value)


def normalize_colors(value: str, default: str) -> List[str]:
    raw = parse_csv(value or default)
    return [normalize_color(item) for item in raw]


def interactive_players(team_key: str) -> List[Dict[str, Any]]:
    print("\n录入重点分析球队球员。代号留空结束。")
    players: List[Dict[str, Any]] = []
    while True:
        code = ask("球员代号/name，例如 HDA")
        if not code:
            break
        number_raw = ask("号码", "")
        role = ask(f"位置 {ROLE_CHOICES}", ROLE_CHOICES[min(len(players), 4)])
        if role not in ROLE_CHOICES:
            print(f"  未识别位置，保留为: {role}")
        visual_hint = ask("重点特征，例如 黄色门将服/绿色球鞋/发型", "")
        player_id = slugify(f"{team_key}_{code}")
        players.append(
            {
                "player_id": player_id,
                "name": code,
                "number": int(number_raw) if number_raw.isdigit() else number_raw,
                "role": role,
                "visual_hint": visual_hint,
            }
        )
    return players


def interactive_substitutions() -> List[Dict[str, Any]]:
    print("\n录入换人信息，可直接回车跳过。")
    substitutions: List[Dict[str, Any]] = []
    while True:
        at = ask("换人时间 mm:ss 或秒数")
        if not at:
            break
        out_player = ask("下场球员代号/player_id", "")
        in_player = ask("上场球员代号/player_id", "")
        note = ask("备注", "")
        substitutions.append({"at": at, "out": out_player, "in": in_player, "note": note})
    return substitutions


def collect_match_info(args: argparse.Namespace, video_path: Path) -> Dict[str, Any]:
    match_id = args.match_id or default_match_id(video_path)
    name = args.name or f"Football Match {match_id}"
    field_length = args.field_length
    field_width = args.field_width
    team_key = args.analyze_team
    team_name = args.team_name or team_key
    team_color = args.team_color or "pink_red"
    field_colors = normalize_colors(args.field_colors or team_color, "pink_red")
    goalkeeper_colors = normalize_colors(args.goalkeeper_colors, "yellow")
    players: List[Dict[str, Any]] = []
    substitutions: List[Dict[str, Any]] = []
    metrics = parse_csv(args.metrics) if args.metrics else DEFAULT_METRICS

    if not args.non_interactive:
        print("创建新比赛项目。直接回车使用默认值。")
        match_id = slugify(ask("比赛项目 ID", match_id))
        name = ask("比赛名称", name)
        field_length = float(ask("球场长度米", str(field_length)))
        field_width = float(ask("球场宽度米", str(field_width)))
        team_key = slugify(ask("重点分析球队 key", team_key))
        team_name = ask("重点分析球队名称", team_name)
        team_color = ask("球队主色/球衣颜色，例如 粉色 或 pink_red", team_color)
        field_colors = normalize_colors(ask("场上球员颜色分类，逗号分隔", ",".join(field_colors)), "pink_red")
        goalkeeper_colors = normalize_colors(ask("门将颜色分类，逗号分隔", ",".join(goalkeeper_colors)), "yellow")
        players = interactive_players(team_key)
        substitutions = interactive_substitutions()
        metrics = parse_csv(ask("重点分析指标，逗号分隔", ",".join(metrics)))

    if not players:
        players = [
            {
                "player_id": f"{team_key}_todo",
                "name": "TODO",
                "number": "",
                "role": "center_forward",
                "visual_hint": "fill before analysis",
            }
        ]

    return {
        "match_id": match_id,
        "name": name,
        "field_length": field_length,
        "field_width": field_width,
        "team_key": team_key,
        "team_name": team_name,
        "team_color": team_color,
        "field_colors": field_colors,
        "goalkeeper_colors": goalkeeper_colors,
        "players": players,
        "substitutions": substitutions,
        "metrics": metrics,
    }


def link_video(source: Path, project_dir: Path, mode: str) -> str:
    if mode == "reference":
        return project_relative(source)

    video_dir = project_dir / "videos" / "raw"
    video_dir.mkdir(parents=True, exist_ok=True)
    target = video_dir / source.name
    if target.exists() or target.is_symlink():
        return project_relative(target)

    if mode == "copy":
        shutil.copy2(source, target)
    else:
        try:
            target.symlink_to(source.resolve())
        except OSError:
            shutil.copy2(source, target)
    return project_relative(target)


def build_config(info: Dict[str, Any], project_dir: Path, video_path_value: str) -> Dict[str, Any]:
    team_key = info["team_key"]
    config_rel = project_relative(project_dir / "config" / "calibration_points.yaml")
    return {
        "match": {
            "id": info["match_id"],
            "name": info["name"],
            "project_dir": project_relative(project_dir),
            "video_path": video_path_value,
            "output_dir": project_relative(project_dir / "reports"),
            "interim_dir": project_relative(project_dir / "data" / "interim"),
            "review_dir": project_relative(project_dir / "review"),
        },
        "field": {
            "length_m": float(info["field_length"]),
            "width_m": float(info["field_width"]),
        },
        "calibration": {
            "points_path": config_rel,
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
                    "field_colors": info["field_colors"],
                    "goalkeeper_colors": info["goalkeeper_colors"],
                },
                "players": info["players"],
                "substitutions": info["substitutions"],
            },
            "opponent": {
                "display_name": "Opponent",
                "analyze_individuals": False,
            },
        },
        "analysis": {
            "target_events": info["metrics"],
            "target_metrics": info["metrics"],
            "report_formats": ["markdown", "csv", "html"],
        },
        "review": {
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
            "sample_fps": 2.0,
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
                    "marked_static_field": {
                        "min_frames": 2,
                        "max_span_m": 0.8,
                    },
                },
            },
            "visible_area_filter": {
                "enabled": True,
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


def write_yaml(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, allow_unicode=True, sort_keys=False)


def write_readme(project_dir: Path, config_path: Path) -> None:
    content = f"""# Match Project

This folder is an isolated football match analysis project.

## Main Files

- Config: `{project_relative(config_path)}`
- Calibration points: `{project_relative(project_dir / "config" / "calibration_points.yaml")}`
- Review folder: `{project_relative(project_dir / "review")}`
- Interim data: `{project_relative(project_dir / "data" / "interim")}`
- Reports: `{project_relative(project_dir / "reports")}`

## Next Commands

```bash
.venv/bin/python scripts/09_prepare_match_review.py --config {project_relative(config_path)} --step calibration
```

After marking calibration points:

```bash
.venv/bin/python scripts/09_prepare_match_review.py --config {project_relative(config_path)} --step recognition --device mps
```
"""
    (project_dir / "README.md").write_text(content, encoding="utf-8")


def create_project(args: argparse.Namespace) -> Tuple[Path, Path]:
    video_path = resolve_path(args.video)
    if not video_path.exists():
        raise FileNotFoundError(f"Video does not exist: {video_path}")

    info = collect_match_info(args, video_path)
    project_dir = PROJECT_ROOT / "matches" / info["match_id"]
    if project_dir.exists() and not args.force:
        raise FileExistsError(f"Match project already exists: {project_dir}. Use --force to overwrite config files.")

    for folder in ["config", "review", "reports", "data/interim", "videos/raw"]:
        (project_dir / folder).mkdir(parents=True, exist_ok=True)

    video_path_value = link_video(video_path, project_dir, args.video_mode)
    config = build_config(info, project_dir, video_path_value)
    config_path = project_dir / "config" / "match.yaml"
    points_path = project_dir / "config" / "calibration_points.yaml"
    write_yaml(config_path, config)
    if not points_path.exists() or args.force:
        write_yaml(points_path, default_calibration_points(info["field_length"], info["field_width"]))
    write_readme(project_dir, config_path)
    return project_dir, config_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True, help="Path to the new match video.")
    parser.add_argument("--match-id", default=None)
    parser.add_argument("--name", default=None)
    parser.add_argument("--analyze-team", default="red")
    parser.add_argument("--team-name", default=None)
    parser.add_argument("--team-color", default=None)
    parser.add_argument("--field-colors", default=None, help="Comma-separated color classes, e.g. pink_red,orange.")
    parser.add_argument("--goalkeeper-colors", default="yellow")
    parser.add_argument("--metrics", default=None, help="Comma-separated target metrics.")
    parser.add_argument("--field-length", type=float, default=40.0)
    parser.add_argument("--field-width", type=float, default=20.0)
    parser.add_argument("--video-mode", choices=["symlink", "copy", "reference"], default="symlink")
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    project_dir, config_path = create_project(args)
    print(f"match_project: {project_dir}")
    print(f"config: {config_path}")
    print("next:")
    print(f"  .venv/bin/python scripts/09_prepare_match_review.py --config {project_relative(config_path)} --step calibration")


if __name__ == "__main__":
    main()
