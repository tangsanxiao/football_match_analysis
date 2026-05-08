"""Shared detection filters for common football false positives."""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Tuple


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


def field_size(config: Dict[str, Any]) -> Tuple[float, float]:
    field = config.get("field", {})
    return float(field.get("length_m", 40.0)), float(field.get("width_m", 20.0))


def ball_static_filter_config(config: Dict[str, Any]) -> Dict[str, Any]:
    return (
        config.get("detection", {})
        .get("ball_filter", {})
        .get("static_false_positive", {})
    )


def ball_group_key(row: Dict[str, Any]) -> Tuple[str, Any]:
    track_id = row.get("track_id")
    if track_id not in {"", None} and str(track_id).lower() != "nan":
        return ("track", safe_int(track_id))
    return (
        "field_cluster",
        round(safe_float(row.get("field_x_m")) / 0.4),
        round(safe_float(row.get("field_y_m")) / 0.4),
    )


def static_ball_group_reason(group: List[Dict[str, Any]], config: Dict[str, Any]) -> str:
    cfg = ball_static_filter_config(config)
    min_frames = int(cfg.get("min_frames", 4))
    min_duration_s = float(cfg.get("min_duration_s", 1.5))
    max_span_m = float(cfg.get("max_span_m", 0.45))
    if len(group) < min_frames:
        return ""
    xs = [safe_float(row.get("field_x_m"), math.nan) for row in group]
    ys = [safe_float(row.get("field_y_m"), math.nan) for row in group]
    ts = [safe_float(row.get("timestamp_sec"), math.nan) for row in group]
    xs = [value for value in xs if not math.isnan(value)]
    ys = [value for value in ys if not math.isnan(value)]
    ts = [value for value in ts if not math.isnan(value)]
    if not xs or not ys or not ts:
        return ""
    span_m = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
    duration_s = max(ts) - min(ts)
    if span_m <= max_span_m and duration_s >= min_duration_s:
        return f"static ball-like mark: {len(group)} frames, span {span_m:.2f}m, duration {duration_s:.1f}s"
    return ""


def filter_static_ball_false_positives(
    rows: Iterable[Dict[str, Any]],
    config: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    cfg = ball_static_filter_config(config)
    if not cfg.get("enabled", True):
        rows_list = list(rows)
        return rows_list, {"enabled": False, "removed_rows": 0, "removed_groups": []}

    rows_list = list(rows)
    groups: Dict[Tuple[str, Any], List[Dict[str, Any]]] = {}
    for row in rows_list:
        if row.get("class_name") != "sports ball":
            continue
        if row.get("inside_play_area") is not True and row.get("inside_play_area") != "True":
            continue
        groups.setdefault(ball_group_key(row), []).append(row)

    remove_keys: Dict[Tuple[str, Any], str] = {}
    for key, group in groups.items():
        reason = static_ball_group_reason(group, config)
        if reason:
            remove_keys[key] = reason

    if not remove_keys:
        return rows_list, {"enabled": True, "removed_rows": 0, "removed_groups": []}

    kept: List[Dict[str, Any]] = []
    removed_rows = 0
    for row in rows_list:
        if row.get("class_name") == "sports ball" and ball_group_key(row) in remove_keys:
            removed_rows += 1
            continue
        kept.append(row)

    removed_groups = [
        {"group_key": str(key), "reason": reason, "rows": len(groups.get(key, []))}
        for key, reason in remove_keys.items()
    ]
    return kept, {"enabled": True, "removed_rows": removed_rows, "removed_groups": removed_groups}


def opponent_goalkeeper_filter_config(config: Dict[str, Any]) -> Dict[str, Any]:
    return (
        config.get("detection", {})
        .get("identity_filter", {})
        .get("opponent_goalkeeper", {})
    )


def analyze_team_kit(config: Dict[str, Any]) -> Dict[str, List[str]]:
    team_key = str(config.get("teams", {}).get("analyze_team", "red"))
    team = config.get("teams", {}).get(team_key, {})
    kit = team.get("kit", {})
    return {
        "field_colors": list(kit.get("field_colors") or ["pink_red"]),
        "goalkeeper_colors": list(kit.get("goalkeeper_colors") or ["yellow"]),
    }


def is_likely_opponent_goalkeeper(row: Any, config: Dict[str, Any], primary_color: str = "") -> Tuple[bool, str]:
    cfg = opponent_goalkeeper_filter_config(config)
    if not cfg.get("enabled", True):
        return False, ""
    length_m, width_m = field_size(config)
    zone_m = float(cfg.get("opponent_goal_zone_m", 4.5))
    goal_y_margin_m = float(cfg.get("goal_y_margin_m", 4.2))
    min_frames = int(cfg.get("min_frames", 6))
    max_span_x_m = float(cfg.get("max_span_x_m", 4.0))
    max_span_y_m = float(cfg.get("max_span_y_m", 7.0))
    strict_stationary_span_x_m = float(cfg.get("strict_stationary_span_x_m", 1.8))
    strict_stationary_span_y_m = float(cfg.get("strict_stationary_span_y_m", 3.0))

    get = row.get if isinstance(row, dict) else lambda key, default=None: getattr(row, key, default)
    mean_x = safe_float(get("mean_x"))
    mean_y = safe_float(get("mean_y"))
    frames = safe_int(get("frames"))
    span_x = safe_float(get("span_x_m"), safe_float(get("max_x")) - safe_float(get("min_x")))
    span_y = safe_float(get("span_y_m"), safe_float(get("max_y")) - safe_float(get("min_y")))
    color = primary_color or str(get("primary_color", ""))

    near_opponent_goal = mean_x >= length_m - zone_m and abs(mean_y - width_m / 2.0) <= goal_y_margin_m
    compact = span_x <= max_span_x_m and span_y <= max_span_y_m
    very_stationary = span_x <= strict_stationary_span_x_m and span_y <= strict_stationary_span_y_m
    if not (near_opponent_goal and frames >= min_frames and compact):
        return False, ""

    kit = analyze_team_kit(config)
    field_colors = set(kit["field_colors"])
    goalkeeper_colors = set(kit["goalkeeper_colors"])
    color_hint = color in goalkeeper_colors or (color and color not in field_colors)
    if color_hint or very_stationary:
        return True, f"near opponent goal with goalkeeper-like track: color={color or 'unknown'}, span=({span_x:.1f},{span_y:.1f})m"
    return False, ""
