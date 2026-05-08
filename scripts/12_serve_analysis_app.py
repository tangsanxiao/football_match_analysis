#!/usr/bin/env python3
"""Serve the football analysis workspace UI."""

from __future__ import annotations

import argparse
import csv
import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import parse_qs, urlparse

try:
    from analysis_app_config import (
        DEFAULT_METRICS,
        METRIC_DEFINITIONS,
        PROJECT_ROOT,
        ROLE_CHOICES,
        ball_review_paths,
        create_or_update_match,
        list_videos,
        load_yaml,
        now_iso,
        project_relative,
        read_csv_rows,
        resolve_path,
        review_paths,
        roster_options,
        safe_float,
        safe_int,
        write_yaml,
    )
    from analysis_app_jobs import (
        all_job_statuses,
        get_job_status,
        start_analysis,
        start_final_report,
        start_prepare_ball_review,
        start_prepare_calibration,
        start_prepare_human_review,
    )
    from analysis_app_state import ball_review_summary, human_review_summary, list_matches, match_payload, match_state
    from create_point_picker import build_html, load_yaml as load_yaml_file, point_payload, resolve_path as picker_resolve_path
    from serve_point_picker import submit_labeling, update_point_image_xy
except ModuleNotFoundError:
    from scripts.analysis_app_config import (
        DEFAULT_METRICS,
        METRIC_DEFINITIONS,
        PROJECT_ROOT,
        ROLE_CHOICES,
        ball_review_paths,
        create_or_update_match,
        list_videos,
        load_yaml,
        now_iso,
        project_relative,
        read_csv_rows,
        resolve_path,
        review_paths,
        roster_options,
        safe_float,
        safe_int,
        write_yaml,
    )
    from scripts.analysis_app_jobs import (
        all_job_statuses,
        get_job_status,
        start_analysis,
        start_final_report,
        start_prepare_ball_review,
        start_prepare_calibration,
        start_prepare_human_review,
    )
    from scripts.analysis_app_state import ball_review_summary, human_review_summary, list_matches, match_payload, match_state
    from scripts.create_point_picker import build_html, load_yaml as load_yaml_file, point_payload, resolve_path as picker_resolve_path
    from scripts.serve_point_picker import submit_labeling, update_point_image_xy


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


def ball_review_payload(match_id: str) -> Dict[str, Any]:
    paths = ball_review_paths(match_id)
    manifest = load_yaml(paths["manifest"])
    corrections = load_yaml(paths["corrections"])
    if not manifest:
        return {
            "match_id": match_id,
            "ready": False,
            "status": "not_ready",
            "message": "球位置标注包还没有生成。请先点击“生成球标注包”。",
            "items": [],
        }
    correction_items = corrections.get("items") or {}
    items = manifest.get("items") or []
    for item in items:
        review_id = str(item.get("review_id") or "")
        if review_id in correction_items:
            item["human_review"] = {**(item.get("human_review") or {}), **correction_items[review_id]}
    summary = ball_review_summary(match_id)
    return {
        "match_id": match_id,
        "ready": True,
        "status": summary["status"],
        "submitted": summary["submitted"],
        "item_count": summary["item_count"],
        "reviewed_count": summary["reviewed_count"],
        "pending_count": summary["pending_count"],
        "points_csv": summary["points_csv"],
        "items": items,
    }


def apply_homography(matrix: List[List[float]], x: float, y: float) -> Tuple[Optional[float], Optional[float]]:
    if not matrix or len(matrix) < 3:
        return None, None
    den = matrix[2][0] * x + matrix[2][1] * y + matrix[2][2]
    if abs(den) < 1e-9:
        return None, None
    field_x = (matrix[0][0] * x + matrix[0][1] * y + matrix[0][2]) / den
    field_y = (matrix[1][0] * x + matrix[1][1] * y + matrix[1][2]) / den
    return field_x, field_y


def calibration_homography(config: Dict[str, Any]) -> List[List[float]]:
    calibration_path = resolve_path(config["match"]["interim_dir"]) / "calibration" / "calibration.json"
    if not calibration_path.exists():
        return []
    with calibration_path.open("r", encoding="utf-8") as handle:
        calibration = json.load(handle)
    return calibration.get("homography_image_to_field") or []


def ball_in_field(config: Dict[str, Any], field_x: Optional[float], field_y: Optional[float], margin_m: float = 0.75) -> bool:
    if field_x is None or field_y is None:
        return False
    length = float(config.get("field", {}).get("length_m", 40.0))
    width = float(config.get("field", {}).get("width_m", 20.0))
    return -margin_m <= float(field_x) <= length + margin_m and -margin_m <= float(field_y) <= width + margin_m


def write_ball_review(match_id: str, reviewed_items: List[Dict[str, Any]], submit: bool) -> Dict[str, Any]:
    config_path = PROJECT_ROOT / "matches" / match_id / "config" / "match.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"Match config not found: {match_id}")
    config = load_yaml(config_path)
    paths = ball_review_paths(match_id)
    manifest = load_yaml(paths["manifest"])
    if not manifest:
        raise FileNotFoundError("球位置标注包还没有生成，无法保存。")

    item_by_id = {str(item.get("review_id")): item for item in manifest.get("items") or []}
    homography = calibration_homography(config)
    corrections: Dict[str, Any] = {}
    points_rows: List[Dict[str, Any]] = []

    for payload_item in reviewed_items:
        review_id = str(payload_item.get("review_id") or "")
        if review_id not in item_by_id:
            continue
        item = item_by_id[review_id]
        status = str(payload_item.get("status") or "pending")
        if status not in {"pending", "marked", "invisible", "out_of_play", "skipped"}:
            status = "pending"
        review_xy = payload_item.get("review_image_xy")
        source_xy = None
        field_x = field_y = None
        has_review_xy = isinstance(review_xy, list) and len(review_xy) == 2
        ball_visible = bool(payload_item.get("ball_visible")) and status in {"marked", "out_of_play"} and has_review_xy
        if ball_visible:
            scale_x = float(item.get("review_to_source_scale_x") or 1.0)
            scale_y = float(item.get("review_to_source_scale_y") or 1.0)
            source_x = float(review_xy[0]) * scale_x
            source_y = float(review_xy[1]) * scale_y
            source_xy = [round(source_x, 2), round(source_y, 2)]
            mapped_x, mapped_y = apply_homography(homography, source_x, source_y)
            if mapped_x is not None and mapped_y is not None:
                field_x = round(mapped_x, 3)
                field_y = round(mapped_y, 3)
        ball_in_play = status == "marked" and ball_visible and ball_in_field(config, field_x, field_y)

        correction = {
            "status": status,
            "ball_visible": ball_visible,
            "ball_in_play": ball_in_play,
            "review_image_xy": [round(float(review_xy[0]), 2), round(float(review_xy[1]), 2)] if isinstance(review_xy, list) and len(review_xy) == 2 else None,
            "source_image_xy": source_xy,
            "field_xy": [field_x, field_y] if field_x is not None and field_y is not None else None,
            "note": str(payload_item.get("note") or ""),
        }
        corrections[review_id] = correction
        item["human_review"] = {**(item.get("human_review") or {}), **correction}
        if status in {"marked", "invisible", "out_of_play", "skipped"}:
            points_rows.append(
                {
                    "review_id": review_id,
                    "frame_idx": int(item.get("frame_idx") or 0),
                    "timestamp_sec": float(item.get("timestamp_sec") or 0.0),
                    "timestamp": item.get("timestamp") or "",
                    "ball_visible": ball_visible,
                    "ball_in_play": ball_in_play,
                    "review_image_x": correction["review_image_xy"][0] if correction["review_image_xy"] else "",
                    "review_image_y": correction["review_image_xy"][1] if correction["review_image_xy"] else "",
                    "source_image_x": source_xy[0] if source_xy else "",
                    "source_image_y": source_xy[1] if source_xy else "",
                    "field_x_m": field_x if field_x is not None else "",
                    "field_y_m": field_y if field_y is not None else "",
                    "source": "human",
                    "status": status,
                    "note": correction["note"],
                }
            )

    manifest["status"] = "submitted" if submit else "draft"
    manifest["updated_at"] = now_iso()
    write_yaml(paths["manifest"], manifest)
    write_yaml(
        paths["corrections"],
        {
            "status": "submitted" if submit else "draft",
            "updated_at": now_iso(),
            "submitted_at": now_iso() if submit else None,
            "items": corrections,
        },
    )

    paths["points_csv"].parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "review_id",
        "frame_idx",
        "timestamp_sec",
        "timestamp",
        "ball_visible",
        "ball_in_play",
        "review_image_x",
        "review_image_y",
        "source_image_x",
        "source_image_y",
        "field_x_m",
        "field_y_m",
        "source",
        "status",
        "note",
    ]
    with paths["points_csv"].open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(sorted(points_rows, key=lambda row: float(row["timestamp_sec"])))
    return ball_review_summary(match_id)


def build_workspace_html() -> str:
    payload = json.dumps(
        {"metrics": METRIC_DEFINITIONS, "roles": ROLE_CHOICES, "default_metrics": DEFAULT_METRICS},
        ensure_ascii=False,
    )
    template_path = PROJECT_ROOT / "scripts" / "analysis_app_workspace.html"
    return template_path.read_text(encoding="utf-8").replace("__BOOT__", payload)


class AnalysisAppServer(ThreadingHTTPServer):
    pass


class Handler(BaseHTTPRequestHandler):
    server: AnalysisAppServer

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/":
                self.send_html(build_workspace_html())
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
            if parsed.path == "/api/match_state":
                match_id = self.query_one(parsed, "match_id")
                self.send_json({"ok": True, "state": match_state(match_id)})
                return
            if parsed.path == "/api/human_review":
                match_id = self.query_one(parsed, "match_id")
                self.send_json({"ok": True, "review": human_review_payload(match_id)})
                return
            if parsed.path == "/api/ball_review":
                match_id = self.query_one(parsed, "match_id")
                self.send_json({"ok": True, "ball_review": ball_review_payload(match_id)})
                return
            if parsed.path == "/api/job":
                job_id = self.query_one(parsed, "job_id")
                status = get_job_status(job_id)
                if status is None:
                    raise FileNotFoundError(f"Job not found: {job_id}")
                self.send_json({"ok": True, **status})
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
            if parsed.path == "/api/start_ball_review":
                match_id = str(payload.get("match_id") or "").strip()
                if not match_id:
                    raise ValueError("match_id is required.")
                job = start_prepare_ball_review(match_id)
                self.send_json({"ok": True, "job": job})
                return
            if parsed.path == "/api/save_ball_review":
                match_id = str(payload.get("match_id") or "").strip()
                if not match_id:
                    raise ValueError("match_id is required.")
                items = payload.get("items")
                if not isinstance(items, list):
                    raise ValueError("items must be a list.")
                submit = bool(payload.get("submit"))
                summary = write_ball_review(match_id, items, submit=submit)
                response: Dict[str, Any] = {"ok": True, "ball_review": summary}
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
