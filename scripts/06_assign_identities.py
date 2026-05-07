#!/usr/bin/env python3
"""Assign analyze-team tracklets to roster players and export labeled tracks."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

import pandas as pd
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


def analyze_team_key(config: Dict[str, Any]) -> str:
    return str(config.get("teams", {}).get("analyze_team", "red"))


def roster_by_role(config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    players = config["teams"][analyze_team_key(config)]["players"]
    return {str(player["role"]): player for player in players}


def roster_by_id(config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    players = config["teams"][analyze_team_key(config)]["players"]
    return {str(player["player_id"]): player for player in players}


def manual_map(path: Optional[Path]) -> Dict[int, Tuple[str, float, str]]:
    if path is None or not path.exists():
        return {}
    data = load_yaml(path) or {}
    out: Dict[int, Tuple[str, float, str]] = {}
    assignments = data.get("assignments", {})
    if isinstance(assignments, list):
        iterable = assignments
    else:
        iterable = [{"player_id": player_id, **payload} for player_id, payload in assignments.items()]
    for item in iterable:
        player_id = str(item["player_id"])
        confidence = float(item.get("confidence", 0.95))
        reason = str(item.get("reason", "manual binding"))
        for track_id in item.get("track_ids", []):
            out[int(track_id)] = (player_id, confidence, reason)
    return out


def role_zone_assignment(
    row: pd.Series,
    role_players: Dict[str, Dict[str, Any]],
    field_length: float,
    right_forward_y: str,
) -> Tuple[Optional[str], float, str]:
    if row["candidate_type"] in {"red_goalkeeper_candidate", "analyze_goalkeeper_candidate"}:
        if "goalkeeper" not in role_players:
            return None, 0.0, "goalkeeper role missing in roster"
        return role_players["goalkeeper"]["player_id"], 0.88, "goalkeeper kit near own goal"

    if row["candidate_type"] not in {"red_field_candidate", "analyze_field_candidate"}:
        return None, 0.0, "not an analyze-team candidate"

    mean_x = float(row["mean_x"])
    mean_y = float(row["mean_y"])
    frames = int(row["frames"])
    length_mid = field_length / 2.0
    confidence = 0.42 + min(0.18, frames / 300.0)

    if mean_x < length_mid - 3.0:
        if "defender" in role_players:
            return role_players["defender"]["player_id"], round(confidence, 3), "role-zone auto: deeper average position"

    right_is_high_y = right_forward_y == "high"
    if (right_is_high_y and mean_y >= 13.0) or ((not right_is_high_y) and mean_y <= 7.0):
        if "right_forward" in role_players:
            return role_players["right_forward"]["player_id"], round(confidence, 3), "role-zone auto: right attacking lane"

    if (right_is_high_y and mean_y <= 7.0) or ((not right_is_high_y) and mean_y >= 13.0):
        if "left_forward" in role_players:
            return role_players["left_forward"]["player_id"], round(confidence, 3), "role-zone auto: left attacking lane"

    if "center_forward" in role_players:
        return role_players["center_forward"]["player_id"], round(confidence, 3), "role-zone auto: central attacking lane"
    return None, 0.0, "no matching role in roster"


def build_assignments(
    config: Dict[str, Any],
    detections_dir: Path,
    manual_bindings: Optional[Path],
    right_forward_y: str,
) -> pd.DataFrame:
    candidates_path = detections_dir / "tracklet_color_candidates.csv"
    if not candidates_path.exists():
        raise FileNotFoundError(f"Missing tracklet_color_candidates.csv: {candidates_path}")

    candidates = pd.read_csv(candidates_path)
    role_players = roster_by_role(config)
    players = roster_by_id(config)
    field_length = float(config.get("field", {}).get("length_m", 40.0))
    manual = manual_map(manual_bindings)

    rows = []
    for _, row in candidates.iterrows():
        track_id = int(row["track_id"])
        method = "auto_role_zone"
        if track_id in manual:
            player_id, confidence, reason = manual[track_id]
            method = "manual"
        else:
            player_id, confidence, reason = role_zone_assignment(row, role_players, field_length, right_forward_y)

        out = row.to_dict()
        out["assigned_player_id"] = player_id or ""
        out["assigned_name"] = players[player_id]["name"] if player_id else ""
        out["assigned_number"] = players[player_id]["number"] if player_id else ""
        out["assigned_role"] = players[player_id]["role"] if player_id else ""
        out["identity_confidence"] = confidence
        out["identity_method"] = method if player_id else ""
        out["identity_reason"] = reason
        rows.append(out)

    assignments = pd.DataFrame(rows)
    assigned = assignments[assignments["assigned_player_id"] != ""].copy()
    return assigned.sort_values(["assigned_player_id", "first_ts", "frames"], ascending=[True, True, False])


def export_labeled_tracks(detections_dir: Path, assignments: pd.DataFrame) -> pd.DataFrame:
    tracks_path = detections_dir / "tracks_in_play.csv"
    if not tracks_path.exists():
        raise FileNotFoundError(f"Missing tracks_in_play.csv: {tracks_path}")

    tracks = pd.read_csv(tracks_path)
    people = tracks[tracks["class_name"] == "person"].copy()
    id_map = assignments[
        [
            "track_id",
            "assigned_player_id",
            "assigned_name",
            "assigned_number",
            "assigned_role",
            "identity_confidence",
            "identity_method",
        ]
    ].copy()
    labeled = people.merge(id_map, on="track_id", how="inner")
    return labeled.sort_values(["timestamp_sec", "assigned_player_id", "track_id"])


def write_binding_yaml(config: Dict[str, Any], assignments: pd.DataFrame, path: Path, right_forward_y: str) -> None:
    players = roster_by_id(config)
    payload: Dict[str, Any] = {
        "method": "auto_role_zone_with_color_candidates",
        "review_required": True,
        "notes": [
            "Goalkeeper identity is high-confidence from yellow kit and own-goal position.",
            "Field-player identities are first-pass role-zone estimates; correct this YAML if jersey-number review disagrees.",
            f"right_forward_y={right_forward_y}",
        ],
        "assignments": {},
    }
    for player_id, group in assignments.groupby("assigned_player_id"):
        payload["assignments"][player_id] = {
            "name": players[player_id]["name"],
            "number": int(players[player_id]["number"]),
            "role": players[player_id]["role"],
            "confidence_mean": round(float(group["identity_confidence"].mean()), 3),
            "track_ids": [int(value) for value in group["track_id"].tolist()],
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, allow_unicode=True, sort_keys=False)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/match_red_mvp.yaml")
    parser.add_argument("--detections-dir", required=True)
    parser.add_argument("--manual-bindings", default=None, help="Optional YAML with track_ids per player_id.")
    parser.add_argument("--right-forward-y", choices=["high", "low"], default="high")
    args = parser.parse_args()

    config = load_yaml(resolve_path(args.config))
    detections_dir = resolve_path(args.detections_dir)
    manual_bindings = resolve_path(args.manual_bindings) if args.manual_bindings else None

    assignments = build_assignments(config, detections_dir, manual_bindings, args.right_forward_y)
    assignments_csv = detections_dir / "tracklet_identity_assignments.csv"
    assignments.to_csv(assignments_csv, index=False)

    labeled = export_labeled_tracks(detections_dir, assignments)
    labeled_csv = detections_dir / "tracks_red_labeled.csv"
    labeled.to_csv(labeled_csv, index=False)

    binding_yaml = detections_dir / "identity_bindings_auto.yaml"
    write_binding_yaml(config, assignments, binding_yaml, args.right_forward_y)

    print(f"assignments_csv: {assignments_csv}")
    print(f"labeled_tracks_csv: {labeled_csv}")
    print(f"binding_yaml: {binding_yaml}")
    print(f"assigned_tracklets: {len(assignments)}")
    if not assignments.empty:
        cols = [
            "track_id",
            "frames",
            "first_ts",
            "last_ts",
            "mean_x",
            "mean_y",
            "assigned_name",
            "assigned_role",
            "identity_confidence",
            "identity_reason",
        ]
        print(assignments[cols].to_string(index=False))


if __name__ == "__main__":
    main()
