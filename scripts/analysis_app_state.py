"""Match summaries and workflow state for the analysis workspace."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from analysis_app_config import (
        DEFAULT_FIELD_LENGTH,
        DEFAULT_FIELD_WIDTH,
        PROJECT_ROOT,
        ball_review_paths,
        load_yaml,
        point_counts,
        project_relative,
        resolve_path,
        review_paths,
    )
    from analysis_app_jobs import latest_job_for_match
except ModuleNotFoundError:
    from scripts.analysis_app_config import (
        DEFAULT_FIELD_LENGTH,
        DEFAULT_FIELD_WIDTH,
        PROJECT_ROOT,
        ball_review_paths,
        load_yaml,
        point_counts,
        project_relative,
        resolve_path,
        review_paths,
    )
    from scripts.analysis_app_jobs import latest_job_for_match

try:
    from analysis_metrics import selected_metric_definitions
    from analysis_report_runs import latest_report_record
except ModuleNotFoundError:
    from scripts.analysis_metrics import selected_metric_definitions
    from scripts.analysis_report_runs import latest_report_record


WORKFLOW_STEPS = [
    {"key": "setup", "label": "设置"},
    {"key": "calibration", "label": "标定"},
    {"key": "recognition", "label": "识别"},
    {"key": "review", "label": "校验"},
    {"key": "report", "label": "报告"},
]


def review_progress_from_items(items: List[Dict[str, Any]], correction_items: Dict[str, Any]) -> Tuple[int, int]:
    done_statuses = {"confirmed", "corrected", "ignored", "marked", "invisible", "out_of_play", "skipped"}
    done = 0
    for item in items:
        review_id = str(item.get("review_id") or "")
        human = correction_items.get(review_id) or item.get("human_review") or {}
        if human.get("status") in done_statuses:
            done += 1
    return done, max(0, len(items) - done)


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
    done_count, pending_count = review_progress_from_items(items, corrections.get("items") or {})
    if submitted:
        done_count = len(items)
        pending_count = 0
    return {
        "status": status,
        "ready": bool(manifest),
        "submitted": submitted,
        "item_count": len(items),
        "done_count": done_count,
        "pending_count": pending_count,
        "manifest_path": project_relative(paths["manifest"]),
        "corrections_path": project_relative(paths["corrections"]),
    }


def ball_review_summary(match_id: str) -> Dict[str, Any]:
    paths = ball_review_paths(match_id)
    manifest = load_yaml(paths["manifest"])
    corrections = load_yaml(paths["corrections"])
    status = corrections.get("status") or manifest.get("status") or ("pending" if manifest else "not_ready")
    items = manifest.get("items") or []
    correction_items = corrections.get("items") or {}
    reviewed = 0
    for item in items:
        review_id = str(item.get("review_id") or "")
        human = correction_items.get(review_id) or item.get("human_review") or {}
        if human.get("status") in {"marked", "invisible", "out_of_play", "skipped"}:
            reviewed += 1
    submitted = status == "submitted"
    if submitted:
        reviewed = len(items)
    return {
        "status": status,
        "ready": bool(manifest),
        "submitted": submitted,
        "item_count": len(items),
        "reviewed_count": reviewed,
        "pending_count": max(0, len(items) - reviewed),
        "points_csv": project_relative(paths["points_csv"]),
    }


def match_summary(config_path: Path) -> Dict[str, Any]:
    config = load_yaml(config_path)
    match = config.get("match", {})
    match_id = str(match.get("id") or config_path.parents[1].name)
    project_dir = resolve_path(match.get("project_dir", config_path.parents[1]))
    points_path = resolve_path(config.get("calibration", {}).get("points_path", project_dir / "config" / "calibration_points.yaml"))
    latest_report = latest_report_record(config)
    analyze_team = str(config.get("teams", {}).get("analyze_team", ""))
    team = config.get("teams", {}).get(analyze_team, {})
    marked, enabled, calibration_submitted = point_counts(points_path)
    report_ready = bool(latest_report)
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
    ball_review = ball_review_summary(match_id)

    if report_ready and human_review["ready"] and ball_review["ready"] and not ball_review["submitted"]:
        status = f"报告完成，可补球点 {ball_review['pending_count']}/{ball_review['item_count']}"
    elif report_ready and not human_review["ready"]:
        status = "报告完成（旧流程）"
    elif report_ready:
        status = "报告完成"
    elif not match_info_confirmed:
        status = "待确认信息"
    elif not calibration_submitted:
        status = f"待提交标定 {marked}/{enabled}"
    elif human_review["ready"] and not human_review["submitted"]:
        status = f"待人工校验 {human_review['pending_count']}/{human_review['item_count']}"
    elif human_review["submitted"]:
        status = "待生成最终报告"
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
        "ball_review_ready": ball_review["ready"],
        "ball_review_submitted": ball_review["submitted"],
        "ball_review_item_count": ball_review["item_count"],
        "report_ready": report_ready,
        "report_md": latest_report.get("report_md", ""),
        "report_html": latest_report.get("report_html", ""),
        "report_dir": latest_report.get("report_dir", ""),
        "report_source": latest_report.get("source", ""),
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


def workflow_step_status(summary: Dict[str, Any], latest_job: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    report_ready = bool(summary.get("report_ready"))
    setup_done = bool(summary.get("match_info_confirmed")) or report_ready
    calibration_done = bool(summary.get("calibration_submitted")) or report_ready
    recognition_done = bool(summary.get("human_review_ready") or report_ready)
    review_done = bool(summary.get("human_review_submitted") or report_ready)
    report_done = report_ready
    running_job_name = latest_job.get("name") if latest_job and latest_job.get("status") == "running" else ""

    states: Dict[str, str] = {
        "setup": "done" if setup_done else "active",
        "calibration": "done" if calibration_done else ("active" if setup_done else "blocked"),
        "recognition": "done" if recognition_done else ("active" if calibration_done else "blocked"),
        "review": "done" if review_done else ("active" if summary.get("human_review_ready") else "blocked"),
        "report": "done" if report_done else ("active" if summary.get("human_review_submitted") else "blocked"),
    }
    if running_job_name == "prepare_calibration":
        states["calibration"] = "running"
    elif running_job_name == "prepare_human_review":
        states["recognition"] = "running"
    elif running_job_name == "prepare_ball_review":
        states["review"] = "running"
    elif running_job_name == "final_report":
        states["report"] = "running"

    return [{**step, "status": states[step["key"]]} for step in WORKFLOW_STEPS]


def next_action_for(summary: Dict[str, Any], latest_job: Optional[Dict[str, Any]]) -> Dict[str, str]:
    if latest_job and latest_job.get("status") == "running":
        name = str(latest_job.get("name") or "")
        step_by_job = {
            "prepare_calibration": "calibration",
            "prepare_human_review": "recognition",
            "prepare_ball_review": "review",
            "final_report": "report",
        }
        label_by_job = {
            "prepare_calibration": "等待标定帧生成",
            "prepare_human_review": "等待系统识别与校验包生成",
            "prepare_ball_review": "等待球点校验包生成",
            "final_report": "等待报告生成",
        }
        return {"step": step_by_job.get(name, "recognition"), "label": label_by_job.get(name, "后台任务运行中")}
    if not summary.get("match_info_confirmed"):
        return {"step": "setup", "label": "补全比赛与球队信息"}
    if not summary.get("calibration_submitted") and not summary.get("report_ready"):
        return {"step": "calibration", "label": "完成场地标定"}
    if summary.get("report_ready"):
        if summary.get("ball_review_ready") and not summary.get("ball_review_submitted"):
            return {"step": "review", "label": "可选：补充球点标注后重算"}
        return {"step": "report", "label": "查看最终报告"}
    if not summary.get("human_review_ready"):
        return {"step": "recognition", "label": "运行识别并生成校验包"}
    if not summary.get("human_review_submitted"):
        return {"step": "review", "label": "确认球员身份校验"}
    return {"step": "report", "label": "等待或重新生成报告"}


def match_state(match_id: str) -> Dict[str, Any]:
    config_path = PROJECT_ROOT / "matches" / match_id / "config" / "match.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"Match config not found: {match_id}")
    config = load_yaml(config_path)
    summary = match_summary(config_path)
    latest_job = latest_job_for_match(match_id)
    human_review = human_review_summary(match_id)
    ball_review = ball_review_summary(match_id)
    steps = workflow_step_status(summary, latest_job)
    done_count = sum(1 for step in steps if step["status"] == "done")
    next_action = next_action_for(summary, latest_job)
    analyze_team = str(config.get("teams", {}).get("analyze_team", ""))
    team = config.get("teams", {}).get(analyze_team, {})
    selected_metric_keys = config.get("analysis", {}).get("target_metrics") or config.get("analysis", {}).get("target_events") or []
    selected_metrics = selected_metric_definitions(selected_metric_keys)
    return {
        "match_id": match_id,
        "summary": summary,
        "steps": steps,
        "progress_percent": int(round(done_count / max(1, len(steps)) * 100)),
        "next_action": next_action,
        "latest_job": latest_job,
        "tasks": {
            "calibration": {
                "marked": summary.get("calibration_marked", 0),
                "enabled": summary.get("calibration_enabled", 0),
                "submitted": summary.get("calibration_submitted", False),
            },
            "identity_review": human_review,
            "ball_review": ball_review,
        },
        "report": {
            "ready": summary.get("report_ready", False),
            "html": summary.get("report_html", ""),
            "markdown": summary.get("report_md", ""),
        },
        "team": {
            "key": analyze_team,
            "name": team.get("display_name", ""),
            "players": team.get("players") or [],
        },
        "metrics": selected_metrics,
    }
