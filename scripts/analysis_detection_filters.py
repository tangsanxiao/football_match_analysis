"""Shared detection filters for common football false positives."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def resolve_path(path: Any) -> Path:
    path = Path(path).expanduser()
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


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


def calibration_points_yaml(config: Dict[str, Any]) -> Dict[str, Any]:
    configured = config.get("calibration", {}).get("points_path")
    if not configured:
        return {}
    return load_yaml(resolve_path(configured))


def static_field_marks(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    points_yaml = calibration_points_yaml(config)
    marks = []
    for point in points_yaml.get("points", []):
        if point.get("kind") != "static_field_mark" and "penalty_mark" not in str(point.get("name", "")):
            continue
        if not isinstance(point.get("image_xy"), list) and not isinstance(point.get("field_xy"), list):
            continue
        marks.append(point)
    return marks


def visible_area_polygon(config: Dict[str, Any]) -> List[Tuple[float, float]]:
    points_yaml = calibration_points_yaml(config)
    polygon: List[Tuple[float, float]] = []
    for point in points_yaml.get("points", []):
        if point.get("kind") != "visible_area":
            continue
        image_xy = point.get("image_xy")
        if isinstance(image_xy, list) and len(image_xy) == 2:
            polygon.append((safe_float(image_xy[0]), safe_float(image_xy[1])))
    return polygon if len(polygon) >= 3 else []


def point_in_polygon(x: float, y: float, polygon: List[Tuple[float, float]]) -> bool:
    inside = False
    j = len(polygon) - 1
    for i, (xi, yi) in enumerate(polygon):
        xj, yj = polygon[j]
        denominator = yj - yi
        if abs(denominator) < 1e-9:
            denominator = 1e-9
        intersects = ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / denominator + xi
        )
        if intersects:
            inside = not inside
        j = i
    return inside


def row_image_anchor(row: Dict[str, Any]) -> Tuple[float, float]:
    if row.get("anchor_x") not in {"", None} and row.get("anchor_y") not in {"", None}:
        return safe_float(row.get("anchor_x")), safe_float(row.get("anchor_y"))
    return safe_float(row.get("cx")), safe_float(row.get("cy"))


def filter_outside_visible_area(
    rows: Iterable[Dict[str, Any]],
    config: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    cfg = config.get("detection", {}).get("visible_area_filter", {})
    if not cfg.get("enabled", True):
        rows_list = list(rows)
        return rows_list, {"enabled": False, "removed_rows": 0, "polygon_points": 0}
    polygon = visible_area_polygon(config)
    rows_list = list(rows)
    if not polygon:
        return rows_list, {"enabled": True, "removed_rows": 0, "polygon_points": 0}
    kept = []
    removed = 0
    for row in rows_list:
        x, y = row_image_anchor(row)
        if point_in_polygon(x, y, polygon):
            kept.append(row)
        else:
            removed += 1
    return kept, {"enabled": True, "removed_rows": removed, "polygon_points": len(polygon)}


def near_static_mark(row: Dict[str, Any], mark: Dict[str, Any], config: Dict[str, Any]) -> bool:
    field_xy = mark.get("field_xy")
    if isinstance(field_xy, list) and len(field_xy) == 2 and row.get("field_x_m") not in {"", None}:
        radius_m = safe_float(mark.get("static_filter_radius_m"), 0.65)
        dx = safe_float(row.get("field_x_m")) - safe_float(field_xy[0])
        dy = safe_float(row.get("field_y_m")) - safe_float(field_xy[1])
        if math.hypot(dx, dy) <= radius_m:
            return True
    image_xy = mark.get("image_xy")
    if isinstance(image_xy, list) and len(image_xy) == 2:
        radius_px = safe_float(mark.get("static_filter_radius_px"), 22.0)
        x, y = row_image_anchor(row)
        if math.hypot(x - safe_float(image_xy[0]), y - safe_float(image_xy[1])) <= radius_px:
            return True
    return False


def static_mark_group_reason(group: List[Dict[str, Any]], config: Dict[str, Any]) -> str:
    cfg = ball_static_filter_config(config).get("marked_static_field", {})
    min_frames = int(cfg.get("min_frames", 2))
    max_span_m = float(cfg.get("max_span_m", 0.8))
    if len(group) < min_frames:
        return ""
    marks = static_field_marks(config)
    if not marks:
        return ""
    if not any(any(near_static_mark(row, mark, config) for mark in marks) for row in group):
        return ""
    xs = [safe_float(row.get("field_x_m"), math.nan) for row in group]
    ys = [safe_float(row.get("field_y_m"), math.nan) for row in group]
    xs = [value for value in xs if not math.isnan(value)]
    ys = [value for value in ys if not math.isnan(value)]
    if xs and ys:
        span_m = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
        if span_m > max_span_m:
            return ""
        return f"marked static field spot: {len(group)} frames, span {span_m:.2f}m"
    return f"marked static field spot: {len(group)} frames"


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
        rows_list, visible_summary = filter_outside_visible_area(rows_list, config)
        return rows_list, {"enabled": False, "removed_rows": 0, "removed_groups": [], "visible_area": visible_summary}

    rows_list, visible_summary = filter_outside_visible_area(rows, config)
    groups: Dict[Tuple[str, Any], List[Dict[str, Any]]] = {}
    for row in rows_list:
        if row.get("class_name") != "sports ball":
            continue
        if row.get("inside_play_area") is not True and row.get("inside_play_area") != "True":
            continue
        groups.setdefault(ball_group_key(row), []).append(row)

    remove_keys: Dict[Tuple[str, Any], str] = {}
    for key, group in groups.items():
        reason = static_mark_group_reason(group, config) or static_ball_group_reason(group, config)
        if reason:
            remove_keys[key] = reason

    if not remove_keys:
        return rows_list, {"enabled": True, "removed_rows": 0, "removed_groups": [], "visible_area": visible_summary}

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
    return kept, {
        "enabled": True,
        "removed_rows": removed_rows,
        "removed_groups": removed_groups,
        "visible_area": visible_summary,
    }


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
