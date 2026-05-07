#!/usr/bin/env python3
"""Generate MVP movement/tactical report from labeled red-team tracks."""

from __future__ import annotations

import argparse
import html
import json
import math
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

import numpy as np
import pandas as pd
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
METRIC_STATUS = {
    "shot": ("射门", "候选片段", "当前依赖足球小目标检测，只输出可能射门时刻，暂不计入正式评分。"),
    "pass": ("传球", "待增强", "足球连续轨迹覆盖不足，暂不输出正式传球次数。"),
    "pass_success": ("传球成功率", "待增强", "需要稳定球权链路；当前报告会说明该指标未达到正式统计置信度。"),
    "steal": ("抢断", "候选片段", "可结合近距离压迫和球权转换做候选，但当前不作为正式抢断统计。"),
    "1v1_attack_defense": ("1v1 攻防", "待增强", "需要更稳定的持球人/防守人关系识别；当前以压迫距离和对抗距离作为前置代理。"),
    "positional_discipline": ("站位纪律", "已输出代理指标", "通过角色区域占比、平均位置和进攻/防守区域参与度衡量。"),
    "pressing_intensity": ("压迫强度", "已输出代理指标", "通过进攻半场内接近最近对手的帧占比和关键片段输出。"),
    "off_ball_movement": ("无球跑动", "已输出代理指标", "通过跑动距离、高速跑、进攻三区进入和关键跑动片段输出。"),
    "defensive_off_ball_movement": ("防守无球跑动", "部分输出", "当前以后卫/回收区域、最近对手距离和移动强度为代理，仍需球权链路增强。"),
    "space_creation": ("创造空间", "部分输出", "当前以进攻三区、宽度/纵深跑动和接应空间代理衡量，暂不做正式创造空间次数。"),
    "observed_coverage": ("观察覆盖率", "已输出辅助指标", "计算方式为该球员被自动识别并绑定成功的去重帧数 / 本次采样处理总帧数 × 100%；它反映可评价样本量，不等同真实上场时间。"),
}

ROLE_LABELS = {
    "goalkeeper": "门将",
    "defender": "后卫",
    "right_forward": "右前锋",
    "center_forward": "中锋",
    "left_forward": "左前锋",
}

CONFIDENCE_LABELS = {
    "high": "高",
    "medium": "中",
    "provisional": "待校正",
    "none": "无",
}


def resolve_path(path: Union[str, Path]) -> Path:
    path = Path(path)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def load_yaml(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def format_ts(seconds: float) -> str:
    total = int(round(seconds))
    return f"{total // 60:02d}:{total % 60:02d}"


def clip(value: float, low: float = 0.0, high: float = 10.0) -> float:
    return max(low, min(high, value))


def pct(mask: Iterable[bool]) -> float:
    values = list(mask)
    if not values:
        return 0.0
    return 100.0 * sum(bool(item) for item in values) / len(values)


def player_table(config: Dict[str, Any]) -> pd.DataFrame:
    team_key = str(config.get("teams", {}).get("analyze_team", "red"))
    return pd.DataFrame(config["teams"][team_key]["players"])


def analyze_team_label(config: Dict[str, Any]) -> str:
    team_key = str(config.get("teams", {}).get("analyze_team", "red"))
    team = config.get("teams", {}).get(team_key, {})
    return str(team.get("display_name") or team_key)


def dedupe_player_frames(labeled: pd.DataFrame) -> pd.DataFrame:
    labeled = labeled.sort_values(
        ["assigned_player_id", "timestamp_sec", "identity_confidence", "conf"],
        ascending=[True, True, False, False],
    )
    return labeled.drop_duplicates(["assigned_player_id", "frame_idx"], keep="first").copy()


def add_motion_columns(df: pd.DataFrame, max_gap_s: float, max_speed_mps: float) -> pd.DataFrame:
    rows = []
    for player_id, group in df.groupby("assigned_player_id"):
        group = group.sort_values("timestamp_sec").copy()
        group["dt_s"] = group["timestamp_sec"].diff()
        group["dx_m"] = group["field_x_m"].diff()
        group["dy_m"] = group["field_y_m"].diff()
        group["step_distance_m"] = np.sqrt(group["dx_m"] ** 2 + group["dy_m"] ** 2)
        valid = (
            group["dt_s"].notna()
            & (group["dt_s"] > 0)
            & (group["dt_s"] <= max_gap_s)
            & (group["step_distance_m"].notna())
        )
        group["speed_mps"] = 0.0
        group.loc[valid, "speed_mps"] = group.loc[valid, "step_distance_m"] / group.loc[valid, "dt_s"]
        group.loc[group["speed_mps"] > max_speed_mps, "step_distance_m"] = 0.0
        group.loc[group["speed_mps"] > max_speed_mps, "speed_mps"] = 0.0
        rows.append(group)
    if not rows:
        return df
    return pd.concat(rows, ignore_index=True)


def opponent_rows(tracks: pd.DataFrame, assignments: pd.DataFrame, candidates: pd.DataFrame) -> pd.DataFrame:
    red_ids = set(int(value) for value in assignments["track_id"].tolist())
    sideline_types = {"referee_or_sideline_candidate", "sideline_candidate"}
    sideline_ids = set(
        int(value)
        for value in candidates[candidates["candidate_type"].isin(sideline_types)]["track_id"].tolist()
    )
    people = tracks[tracks["class_name"] == "person"].copy()
    people = people[~people["track_id"].isin(red_ids | sideline_ids)]
    people = people[(people["field_y_m"] >= -0.5) & (people["field_y_m"] <= 20.5)]
    return people


def nearest_opponent(red: pd.DataFrame, opponents: pd.DataFrame) -> pd.DataFrame:
    opponent_by_frame = {frame: group for frame, group in opponents.groupby("frame_idx")}
    distances: List[float] = []
    for row in red.itertuples(index=False):
        group = opponent_by_frame.get(row.frame_idx)
        if group is None or group.empty:
            distances.append(math.nan)
            continue
        dx = group["field_x_m"].to_numpy(dtype=float) - float(row.field_x_m)
        dy = group["field_y_m"].to_numpy(dtype=float) - float(row.field_y_m)
        distances.append(float(np.sqrt(dx * dx + dy * dy).min()))
    out = red.copy()
    out["nearest_opponent_m"] = distances
    return out


def role_zone_pct(group: pd.DataFrame, role: str) -> float:
    x = group["field_x_m"]
    y = group["field_y_m"]
    if role == "goalkeeper":
        return pct((x <= 5.5) & (y >= 6.0) & (y <= 14.0))
    if role == "defender":
        return pct(x <= 24.0)
    if role == "right_forward":
        return pct((x >= 15.0) & (y >= 11.5))
    if role == "left_forward":
        return pct((x >= 15.0) & (y <= 8.5))
    if role == "center_forward":
        return pct((x >= 15.0) & (y >= 7.0) & (y <= 13.5))
    return 0.0


def summarize_players(
    config: Dict[str, Any],
    labeled: pd.DataFrame,
    tracks: pd.DataFrame,
    assignments: pd.DataFrame,
    candidates: pd.DataFrame,
    sample_fps: float,
    processed_frames: int,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    red = dedupe_player_frames(labeled)
    red = add_motion_columns(red, max_gap_s=2.0, max_speed_mps=8.0)
    red = nearest_opponent(red, opponent_rows(tracks, assignments, candidates))
    red["in_attacking_half"] = red["field_x_m"] >= float(config["field"]["length_m"]) / 2.0
    red["in_final_third"] = red["field_x_m"] >= float(config["field"]["length_m"]) * 2.0 / 3.0
    red["pressing_frame"] = red["in_attacking_half"] & (red["nearest_opponent_m"] <= 3.5)
    red["duel_proximity_frame"] = red["nearest_opponent_m"] <= 2.2
    red["high_speed_frame"] = red["speed_mps"] >= 3.2

    players = player_table(config).set_index("player_id")
    rows = []
    for player_id, player in players.iterrows():
        group = red[red["assigned_player_id"] == player_id].copy()
        if group.empty:
            rows.append(
                {
                    "player_id": player_id,
                    "name": player["name"],
                    "number": player["number"],
                    "role": player["role"],
                    "observed_frames": 0,
                    "observed_seconds": 0.0,
                    "observed_coverage_pct": 0.0,
                    "distance_m": 0.0,
                    "distance_per_min": 0.0,
                    "high_speed_distance_m": 0.0,
                    "high_speed_pct": 0.0,
                    "pressing_pct": 0.0,
                    "duel_proximity_pct": 0.0,
                    "attacking_half_pct": 0.0,
                    "final_third_pct": 0.0,
                    "role_zone_pct": 0.0,
                    "avg_nearest_opponent_m": math.nan,
                    "avg_x_m": math.nan,
                    "avg_y_m": math.nan,
                    "rating": 0.0,
                    "confidence": "none",
                }
            )
            continue

        observed_frames = int(group["frame_idx"].nunique())
        observed_seconds = observed_frames / sample_fps if sample_fps > 0 else 0.0
        minutes = max(observed_seconds / 60.0, 1e-6)
        distance_m = float(group["step_distance_m"].sum())
        high_speed_distance_m = float(group.loc[group["high_speed_frame"], "step_distance_m"].sum())
        role_pct = role_zone_pct(group, str(player["role"]))
        distance_per_min = distance_m / minutes
        high_speed_pct = pct(group["high_speed_frame"])
        pressing_pct = pct(group["pressing_frame"])
        final_third_pct = pct(group["in_final_third"])
        attacking_half_pct = pct(group["in_attacking_half"])
        coverage_pct = 100.0 * observed_frames / processed_frames if processed_frames else 0.0

        movement_score = clip(distance_per_min / 45.0 * 10.0)
        high_speed_score = clip(high_speed_pct / 18.0 * 10.0)
        pressing_score = clip(pressing_pct / 25.0 * 10.0)
        attack_score = clip(final_third_pct / 40.0 * 10.0)
        discipline_score = clip(role_pct / 75.0 * 10.0)
        availability_score = clip(coverage_pct / 70.0 * 10.0)

        role = str(player["role"])
        if role == "goalkeeper":
            weighted = 0.45 * discipline_score + 0.25 * availability_score + 0.15 * movement_score + 0.15 * clip((100 - attacking_half_pct) / 95 * 10)
        elif role == "defender":
            weighted = 0.30 * discipline_score + 0.25 * pressing_score + 0.20 * movement_score + 0.15 * availability_score + 0.10 * high_speed_score
        else:
            weighted = 0.25 * pressing_score + 0.22 * attack_score + 0.20 * movement_score + 0.18 * discipline_score + 0.15 * high_speed_score
        rating = round(4.5 + 0.45 * weighted, 1)

        confidence_mean = float(group["identity_confidence"].mean())
        if role == "goalkeeper" and confidence_mean >= 0.8:
            confidence = "high"
        elif confidence_mean >= 0.5:
            confidence = "medium"
        else:
            confidence = "provisional"

        rows.append(
            {
                "player_id": player_id,
                "name": player["name"],
                "number": player["number"],
                "role": role,
                "observed_frames": observed_frames,
                "observed_seconds": round(observed_seconds, 1),
                "observed_coverage_pct": round(coverage_pct, 1),
                "distance_m": round(distance_m, 1),
                "distance_per_min": round(distance_per_min, 1),
                "high_speed_distance_m": round(high_speed_distance_m, 1),
                "high_speed_pct": round(high_speed_pct, 1),
                "pressing_pct": round(pressing_pct, 1),
                "duel_proximity_pct": round(pct(group["duel_proximity_frame"]), 1),
                "attacking_half_pct": round(attacking_half_pct, 1),
                "final_third_pct": round(final_third_pct, 1),
                "role_zone_pct": round(role_pct, 1),
                "avg_nearest_opponent_m": round(float(group["nearest_opponent_m"].mean()), 2),
                "avg_x_m": round(float(group["field_x_m"].mean()), 2),
                "avg_y_m": round(float(group["field_y_m"].mean()), 2),
                "rating": rating,
                "confidence": confidence,
            }
        )
    metrics = pd.DataFrame(rows).sort_values("rating", ascending=False)
    return metrics, red


def add_event(events: List[Dict[str, Any]], row: Any, event_type: str, confidence: str, reason: str) -> None:
    events.append(
        {
            "timestamp_sec": round(float(row.timestamp_sec), 1),
            "timestamp": format_ts(float(row.timestamp_sec)),
            "player_id": row.assigned_player_id,
            "name": row.assigned_name,
            "number": int(row.assigned_number),
            "role": row.assigned_role,
            "event_type": event_type,
            "confidence": confidence,
            "reason": reason,
            "field_x_m": round(float(row.field_x_m), 2),
            "field_y_m": round(float(row.field_y_m), 2),
        }
    )


def separated(rows: pd.DataFrame, min_gap_s: float, limit: int) -> List[Any]:
    selected: List[Any] = []
    for row in rows.itertuples(index=False):
        ts = float(row.timestamp_sec)
        if all(abs(ts - float(prev.timestamp_sec)) >= min_gap_s for prev in selected):
            selected.append(row)
        if len(selected) >= limit:
            break
    return selected


def key_events(red: pd.DataFrame) -> pd.DataFrame:
    events: List[Dict[str, Any]] = []
    for _, group in red.groupby("assigned_player_id"):
        press = group[group["pressing_frame"]].sort_values(["nearest_opponent_m", "field_x_m"], ascending=[True, False])
        for row in separated(press, 10.0, 3):
            add_event(events, row, "压迫候选", "中", "进攻半场内，距离最近对手不超过 3.5 米")

        final = group[group["in_final_third"]].sort_values("field_x_m", ascending=False)
        for row in separated(final, 12.0, 2):
            add_event(events, row, "进攻三区跑动", "中", "进入或保持在进攻三区")

        fast = group[group["speed_mps"] >= 3.2].sort_values("speed_mps", ascending=False)
        for row in separated(fast, 12.0, 2):
            add_event(events, row, "高速跑动", "低", f"估算速度 {row.speed_mps:.1f} 米/秒")

    if not events:
        return pd.DataFrame()
    return pd.DataFrame(events).sort_values(["timestamp_sec", "player_id"])


def separated_count(timestamps: Iterable[float], min_gap_s: float) -> int:
    count = 0
    last_ts: Optional[float] = None
    for ts in sorted(float(value) for value in timestamps):
        if last_ts is None or ts - last_ts >= min_gap_s:
            count += 1
            last_ts = ts
    return count


def ball_ownership_candidates(red: pd.DataFrame, tracks: pd.DataFrame, max_distance_m: float = 4.0) -> pd.DataFrame:
    ball = tracks[tracks["class_name"] == "sports ball"].copy()
    required = {"frame_idx", "timestamp_sec", "field_x_m", "field_y_m", "conf"}
    if ball.empty or red.empty or not required.issubset(ball.columns):
        return pd.DataFrame()
    ball = ball.dropna(subset=["field_x_m", "field_y_m"])
    if ball.empty:
        return pd.DataFrame()
    ball = ball.sort_values(["frame_idx", "conf"], ascending=[True, False]).drop_duplicates("frame_idx", keep="first")
    red_by_frame = {frame: group for frame, group in red.groupby("frame_idx")}
    rows: List[Dict[str, Any]] = []
    for row in ball.itertuples(index=False):
        group = red_by_frame.get(row.frame_idx)
        if group is None or group.empty:
            rows.append(
                {
                    "frame_idx": int(row.frame_idx),
                    "timestamp_sec": float(row.timestamp_sec),
                    "ball_x_m": float(row.field_x_m),
                    "ball_y_m": float(row.field_y_m),
                    "owner_player_id": "",
                    "owner_name": "",
                    "distance_to_owner_m": math.nan,
                }
            )
            continue
        dx = group["field_x_m"].to_numpy(dtype=float) - float(row.field_x_m)
        dy = group["field_y_m"].to_numpy(dtype=float) - float(row.field_y_m)
        distances = np.sqrt(dx * dx + dy * dy)
        nearest_pos = int(distances.argmin())
        nearest = group.iloc[nearest_pos]
        distance = float(distances[nearest_pos])
        owner_id = str(nearest["assigned_player_id"]) if distance <= max_distance_m else ""
        rows.append(
            {
                "frame_idx": int(row.frame_idx),
                "timestamp_sec": float(row.timestamp_sec),
                "ball_x_m": float(row.field_x_m),
                "ball_y_m": float(row.field_y_m),
                "owner_player_id": owner_id,
                "owner_name": str(nearest["assigned_name"]) if owner_id else "",
                "distance_to_owner_m": round(distance, 2),
            }
        )
    return pd.DataFrame(rows).sort_values("timestamp_sec")


def pass_reference_counts(ownership: pd.DataFrame, max_gap_s: float = 4.0) -> Tuple[Dict[str, int], Dict[str, int]]:
    attempts: Dict[str, int] = {}
    successes: Dict[str, int] = {}
    if ownership.empty:
        return attempts, successes
    segments: List[Dict[str, Any]] = []
    for row in ownership.sort_values("timestamp_sec").itertuples(index=False):
        owner = str(row.owner_player_id or "")
        timestamp_sec = float(row.timestamp_sec)
        if segments and segments[-1]["owner"] == owner:
            segments[-1]["end_ts"] = timestamp_sec
            continue
        segments.append({"owner": owner, "start_ts": timestamp_sec, "end_ts": timestamp_sec})

    for current, nxt in zip(segments, segments[1:]):
        owner = current["owner"]
        if not owner:
            continue
        if float(nxt["start_ts"]) - float(current["end_ts"]) > max_gap_s:
            continue
        attempts[owner] = attempts.get(owner, 0) + 1
        next_owner = str(nxt["owner"] or "")
        if next_owner and next_owner != owner:
            successes[owner] = successes.get(owner, 0) + 1
    return attempts, successes


def bounded_pct(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 1)


def technical_reference_table(metrics: pd.DataFrame, red: pd.DataFrame, tracks: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
    field_length = float(config.get("field", {}).get("length_m", 40.0))
    ownership = ball_ownership_candidates(red, tracks)
    pass_attempts, pass_successes = pass_reference_counts(ownership)

    shot_counts: Dict[str, int] = {}
    if not ownership.empty:
        shot_zone = ownership[(ownership["owner_player_id"] != "") & (ownership["ball_x_m"] >= field_length * 0.78)]
        for player_id, group in shot_zone.groupby("owner_player_id"):
            shot_counts[str(player_id)] = separated_count(group["timestamp_sec"], 8.0)

    rows = []
    for _, row in metrics.iterrows():
        player_id = str(row["player_id"])
        group = red[red["assigned_player_id"] == player_id]
        steal_mask = group["pressing_frame"] & group["duel_proximity_frame"] if not group.empty else []
        duel_mask = group["duel_proximity_frame"] if not group.empty else []
        steal_candidates = separated_count(group.loc[steal_mask, "timestamp_sec"], 8.0) if not group.empty else 0
        duel_candidates = separated_count(group.loc[duel_mask, "timestamp_sec"], 8.0) if not group.empty else 0
        attempts = pass_attempts.get(player_id, 0)
        successes = pass_successes.get(player_id, 0)
        pass_success_text = f"{round(100.0 * successes / attempts, 1)}%" if attempts else "样本不足"

        movement_norm = min(float(row["distance_per_min"]) / 120.0 * 100.0, 100.0)
        high_speed_norm = min(float(row["high_speed_pct"]) / 6.0 * 100.0, 100.0)
        pressing_norm = min(float(row["pressing_pct"]) / 20.0 * 100.0, 100.0)
        duel_norm = min(float(row["duel_proximity_pct"]) / 15.0 * 100.0, 100.0)
        off_ball_index = bounded_pct(0.40 * movement_norm + 0.25 * high_speed_norm + 0.35 * float(row["final_third_pct"]))
        defensive_off_ball_index = bounded_pct(0.45 * float(row["role_zone_pct"]) + 0.30 * pressing_norm + 0.25 * duel_norm)
        space_creation_index = bounded_pct(
            0.35 * float(row["final_third_pct"])
            + 0.25 * float(row["attacking_half_pct"])
            + 0.20 * movement_norm
            + 0.20 * high_speed_norm
        )

        rows.append(
            {
                "球员": row["name"],
                "号码": row["number"],
                "射门候选": shot_counts.get(player_id, 0),
                "传球候选": attempts,
                "传球成功率参考": pass_success_text,
                "抢断候选": steal_candidates,
                "1v1攻防候选": duel_candidates,
                "站位纪律%": row["role_zone_pct"],
                "压迫强度%": row["pressing_pct"],
                "无球跑动指数": off_ball_index,
                "防守无球跑动指数": defensive_off_ball_index,
                "创造空间指数": space_creation_index,
                "置信度": "低" if attempts or shot_counts.get(player_id, 0) else "低/样本少",
            }
        )
    return pd.DataFrame(rows)


def suggestion(row: pd.Series) -> str:
    role = row["role"]
    if row["observed_frames"] == 0:
        return "本次自动检测未稳定捕捉到该球员，先补身份绑定再评价。"
    if role == "goalkeeper":
        if row["role_zone_pct"] < 80:
            return "门前站位可再稳定，优先练习出击后快速回到中路保护位置。"
        return "门将站位整体稳定，下一步重点补充开球选择和防守转换后的第一传。"
    if role == "defender":
        if row["role_zone_pct"] < 55:
            return "防守保护区离开较多，建议练 2v2 延缓、身后保护和攻转守第一步回收。"
        if row["pressing_pct"] < 8:
            return "防守站位较稳，但主动压迫偏少，建议增加对持球人第一下限制。"
        return "防守覆盖不错，继续提升抢断后向前出球的速度。"
    if row["pressing_pct"] < 10:
        return "前场压迫触发偏少，建议练习失球后 3 秒内的夹抢和封中路路线。"
    if row["final_third_pct"] < 20:
        return "进入终结区域比例偏低，建议增加斜插后点、二点球跟进和门前补位。"
    if row["role_zone_pct"] < 45:
        return "活动范围很大但位置纪律一般，建议明确边/中职责，减少同线重叠。"
    return "跑动和压迫参与度较好，下一步把无球跑动和接应后的第一脚处理连起来。"


def role_label(role: str) -> str:
    return ROLE_LABELS.get(str(role), str(role))


def confidence_label(value: str) -> str:
    return CONFIDENCE_LABELS.get(str(value), str(value))


def localized_score_table(metrics: pd.DataFrame) -> pd.DataFrame:
    table = metrics[
        [
            "name",
            "number",
            "role",
            "rating",
            "confidence",
            "observed_coverage_pct",
            "distance_per_min",
            "pressing_pct",
            "final_third_pct",
            "role_zone_pct",
        ]
    ].copy()
    table["role"] = table["role"].map(role_label)
    table["confidence"] = table["confidence"].map(confidence_label)
    table = table.rename(
        columns={
            "name": "球员",
            "number": "号码",
            "role": "位置",
            "rating": "评分",
            "confidence": "身份置信度",
            "observed_coverage_pct": "观察覆盖率%",
            "distance_per_min": "跑动米/分钟",
            "pressing_pct": "压迫参与%",
            "final_third_pct": "进攻三区%",
            "role_zone_pct": "位置纪律%",
        }
    )
    return table


def localized_auxiliary_table(metrics: pd.DataFrame) -> pd.DataFrame:
    table = metrics[
        [
            "name",
            "number",
            "observed_seconds",
            "distance_m",
            "high_speed_distance_m",
            "high_speed_pct",
            "duel_proximity_pct",
            "attacking_half_pct",
            "avg_nearest_opponent_m",
            "avg_x_m",
            "avg_y_m",
        ]
    ].copy()
    table = table.rename(
        columns={
            "name": "球员",
            "number": "号码",
            "observed_seconds": "观察时长秒",
            "distance_m": "估算总跑动米",
            "high_speed_distance_m": "高速跑距离米",
            "high_speed_pct": "高速跑帧占比%",
            "duel_proximity_pct": "近距离对抗%",
            "attacking_half_pct": "进攻半场%",
            "avg_nearest_opponent_m": "平均最近对手米",
            "avg_x_m": "平均X米",
            "avg_y_m": "平均Y米",
        }
    )
    return table


def markdown_table(df: pd.DataFrame) -> str:
    if df.empty:
        return ""
    display = df.copy()
    headers = [str(col) for col in display.columns]
    rows = []
    for _, row in display.iterrows():
        rows.append([str(row[col]) for col in display.columns])
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows:
        safe = [cell.replace("\n", " ").replace("|", "/") for cell in row]
        lines.append("| " + " | ".join(safe) + " |")
    return "\n".join(lines)


def metric_status_table(config: Dict[str, Any]) -> pd.DataFrame:
    selected = (
        config.get("analysis", {}).get("target_metrics")
        or config.get("analysis", {}).get("target_events")
        or []
    )
    rows = []
    for metric in selected:
        label, status, note = METRIC_STATUS.get(metric, (metric, "已选择", "该指标暂未配置详细说明。"))
        rows.append({"指标": label, "当前报告状态": status, "说明": note})
    return pd.DataFrame(rows)


def markdown_report(
    config: Dict[str, Any],
    metrics: pd.DataFrame,
    reference_metrics: pd.DataFrame,
    events: pd.DataFrame,
    summary: Dict[str, Any],
    output_dir: Path,
) -> str:
    mvp = metrics[metrics["observed_frames"] > 0].sort_values("rating", ascending=False).head(1)
    mvp_text = "暂无"
    if not mvp.empty:
        r = mvp.iloc[0]
        mvp_text = f"{r['name']}（{int(r['number'])}号，评分 {r['rating']}）"

    team_label = analyze_team_label(config)
    lines = [
        f"# {team_label} 五人制比赛 MVP 初版报告",
        "",
        f"- 视频片段：{format_ts(summary['segment']['start_sec'])} - {format_ts(summary['segment']['start_sec'] + summary['segment']['duration_sec'])}",
        f"- 采样：{summary['segment']['sample_fps']} fps，共 {summary['segment']['processed_frames']} 帧",
        f"- MVP：{mvp_text}",
        "- 重要说明：当前报告主要基于人物检测、轨迹、站位和对抗距离。通用 YOLO 对足球覆盖率较低，因此传球、射门、抢断只能给出候选时刻，不能作为正式技术统计。",
        "",
        "## 本次勾选指标覆盖状态",
        "",
        markdown_table(metric_status_table(config)),
        "",
        "## 球员评分",
        "",
        "### 评分规则说明",
        "",
        "- 总分为 10 分制，是“业余五人制球员能力评估”的 MVP 初版代理评分；当前不把传球、射门、抢断计入正式评分，因为足球小目标检测覆盖不足。",
        "- 基础分为 4.5 分，再根据角色加权后的表现分上浮；表现分由跑动参与、压迫参与、进攻三区参与、位置纪律、高速移动和可观察度组成。",
        "- 跑动参与：按每分钟估算跑动距离评分；高速移动：按高速跑帧占比评分；压迫参与：在进攻半场且距离最近对手 3.5 米内的帧占比；进攻三区参与：进入球场前 1/3 区域的帧占比；位置纪律：球员是否稳定出现在其角色对应区域；可观察度：检测到该球员的帧覆盖率。",
        "- 观察覆盖率：该球员被自动识别并绑定成功的去重帧数 / 本次采样处理总帧数 × 100%。它反映本场可评价样本量，不等同真实出勤率；低覆盖率通常意味着遮挡、远景、号码不清或身份绑定需要人工校验。",
        "- 角色权重：门将更看重门前位置纪律和可观察度；后卫更看重位置纪律、压迫和回收覆盖；前锋更看重压迫、进攻三区参与、跑动和位置纪律。",
        "- 身份置信度为“高/中/待校正”。HDA 由黄色门将服和门前位置识别，置信度高；其他场上球员目前主要由角色区域自动绑定，仍建议后续用号码/球鞋做一次校正。",
        "",
        "### 评分表",
        "",
        markdown_table(localized_score_table(metrics)),
        "",
        "### 辅助指标",
        "",
        "- 以下指标用于辅助理解表现和校验数据质量；除已在评分规则中明确提到的代理项外，暂不直接作为最终评分依据。",
        "- 平均X米/平均Y米：把球员检测框底部中心点通过场地标定映射到五人制球场坐标后取平均值。X 轴表示从本方球门方向到进攻方向的纵深位置，0 到 40 米；Y 轴表示从上侧边线到下侧边线的横向位置，0 到 20 米。它们是位置热区的简化参考，受标定误差、遮挡和身份绑定影响。",
        "",
        markdown_table(localized_auxiliary_table(metrics)),
        "",
        "### 重点指标参考值（低置信）",
        "",
        "- 以下指标是低置信参考值，用于先观察趋势，不进入当前正式评分。射门、传球、传球成功率主要依赖足球小目标检测和最近球员归属；如果候选值为 0 且置信度为“低/样本少”，表示当前视频中足球检测样本不足，不能等同于真实比赛没有发生。抢断和 1v1 主要依赖近距离对抗与压迫代理；无球、防守无球和创造空间为 0-100 的代理指数。",
        "",
        markdown_table(reference_metrics),
        "",
        "## 每名球员建议",
        "",
    ]
    for _, row in metrics.sort_values("number").iterrows():
        lines.append(
            f"- {row['name']}（{int(row['number'])}号，{role_label(row['role'])}）：评分 {row['rating']}，"
            f"身份置信度 {confidence_label(row['confidence'])}。{suggestion(row)}"
        )

    lines.extend(["", "## 关键片段时间戳", ""])
    if events.empty:
        lines.append("- 暂无稳定候选。")
    else:
        for _, row in events.head(30).iterrows():
            lines.append(
                f"- {row['timestamp']} {row['name']}（{int(row['number'])}号）："
                f"{row['event_type']}，{row['reason']}。"
            )

    lines.extend(
        [
            "",
            "## 输出文件",
            "",
            f"- `player_metrics.csv`：球员评分与移动/站位指标",
            f"- `reference_metrics.csv`：低置信重点指标参考值",
            f"- `key_timestamps.csv`：关键候选片段",
            f"- `report.html`：可浏览报告",
            f"- 报告目录：`{output_dir}`",
        ]
    )
    return "\n".join(lines) + "\n"


def inline_markdown(value: str) -> str:
    escaped = html.escape(value)
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)


def is_markdown_table_separator(line: str) -> bool:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return False
    cells = [cell.strip() for cell in stripped.strip("|").split("|")]
    return bool(cells) and all(cell and set(cell) <= {"-", ":"} for cell in cells)


def parse_markdown_table_row(line: str) -> List[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def markdown_table_to_html(lines: List[str]) -> str:
    if len(lines) < 2:
        return ""
    headers = parse_markdown_table_row(lines[0])
    rows = [parse_markdown_table_row(line) for line in lines[2:]]
    output = ["<table>", "<thead><tr>"]
    for header in headers:
        output.append(f"<th>{inline_markdown(header)}</th>")
    output.append("</tr></thead>")
    output.append("<tbody>")
    for row in rows:
        output.append("<tr>")
        for index in range(len(headers)):
            cell = row[index] if index < len(row) else ""
            output.append(f"<td>{inline_markdown(cell)}</td>")
        output.append("</tr>")
    output.append("</tbody></table>")
    return "\n".join(output)


def markdown_to_html(markdown_text: str) -> str:
    lines = markdown_text.splitlines()
    output: List[str] = []
    index = 0
    in_list = False

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            output.append("</ul>")
            in_list = False

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            close_list()
            index += 1
            continue

        if stripped.startswith("|") and index + 1 < len(lines) and is_markdown_table_separator(lines[index + 1]):
            close_list()
            table_lines = [stripped, lines[index + 1].strip()]
            index += 2
            while index < len(lines) and lines[index].strip().startswith("|"):
                table_lines.append(lines[index].strip())
                index += 1
            output.append(markdown_table_to_html(table_lines))
            continue

        if stripped.startswith("#"):
            close_list()
            level = min(6, len(stripped) - len(stripped.lstrip("#")))
            title = stripped[level:].strip()
            output.append(f"<h{level}>{inline_markdown(title)}</h{level}>")
            index += 1
            continue

        if stripped.startswith("- "):
            if not in_list:
                output.append("<ul>")
                in_list = True
            output.append(f"<li>{inline_markdown(stripped[2:].strip())}</li>")
            index += 1
            continue

        close_list()
        output.append(f"<p>{inline_markdown(stripped)}</p>")
        index += 1

    close_list()
    return "\n".join(output)


def html_report(markdown_text: str) -> str:
    css = """
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #17201b; line-height: 1.55; }
    h1, h2, h3 { color: #12382d; }
    h1 { margin-bottom: 18px; }
    h2 { margin-top: 30px; }
    table { border-collapse: collapse; width: 100%; margin: 16px 0 28px; }
    th, td { border: 1px solid #d8e0dc; padding: 8px 10px; text-align: left; }
    th { background: #edf5f1; }
    ul { padding-left: 1.25rem; }
    li { margin: 4px 0; }
    code { background: #f3f5f2; border: 1px solid #d8e0dc; border-radius: 4px; padding: 1px 4px; }
    """
    first_heading = next((line.lstrip("#").strip() for line in markdown_text.splitlines() if line.startswith("#")), "五人制比赛报告")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>{html.escape(first_heading)}</title>
  <style>{css}</style>
</head>
<body>
  {markdown_to_html(markdown_text)}
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/match_red_mvp.yaml")
    parser.add_argument("--detections-dir", required=True)
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    config = load_yaml(resolve_path(args.config))
    detections_dir = resolve_path(args.detections_dir)
    output_dir = resolve_path(args.output_dir or config["match"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)

    summary = load_json(detections_dir / "summary.json")
    sample_fps = float(summary["segment"]["sample_fps"])
    processed_frames = int(summary["segment"]["processed_frames"])

    labeled = pd.read_csv(detections_dir / "tracks_red_labeled.csv")
    tracks = pd.read_csv(detections_dir / "tracks_in_play.csv")
    assignments = pd.read_csv(detections_dir / "tracklet_identity_assignments.csv")
    candidates = pd.read_csv(detections_dir / "tracklet_color_candidates.csv")

    metrics, red_rows = summarize_players(config, labeled, tracks, assignments, candidates, sample_fps, processed_frames)
    events = key_events(red_rows)
    reference_metrics = technical_reference_table(metrics, red_rows, tracks, config)

    metrics_path = output_dir / "player_metrics.csv"
    reference_metrics_path = output_dir / "reference_metrics.csv"
    events_path = output_dir / "key_timestamps.csv"
    labeled_path = output_dir / "tracks_red_labeled.csv"
    report_md_path = output_dir / "report.md"
    report_html_path = output_dir / "report.html"

    metrics.to_csv(metrics_path, index=False)
    reference_metrics.to_csv(reference_metrics_path, index=False)
    events.to_csv(events_path, index=False)
    red_rows.to_csv(labeled_path, index=False)
    markdown_text = markdown_report(config, metrics, reference_metrics, events, summary, output_dir)
    report_md_path.write_text(markdown_text, encoding="utf-8")
    report_html_path.write_text(html_report(markdown_text), encoding="utf-8")

    print(f"metrics_csv: {metrics_path}")
    print(f"reference_metrics_csv: {reference_metrics_path}")
    print(f"key_timestamps_csv: {events_path}")
    print(f"labeled_tracks_csv: {labeled_path}")
    print(f"report_md: {report_md_path}")
    print(f"report_html: {report_html_path}")
    print(metrics[["name", "number", "role", "rating", "confidence", "distance_per_min", "pressing_pct", "final_third_pct", "role_zone_pct"]].to_string(index=False))


if __name__ == "__main__":
    main()
