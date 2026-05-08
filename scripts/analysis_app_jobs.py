"""Background job runner with lightweight persisted status."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from analysis_app_config import PROJECT_ROOT, load_yaml, now_iso, slugify, write_yaml
except ModuleNotFoundError:
    from scripts.analysis_app_config import PROJECT_ROOT, load_yaml, now_iso, slugify, write_yaml


JOBS: Dict[str, Dict[str, Any]] = {}


def job_dir(match_id: str, create: bool = True) -> Path:
    path = PROJECT_ROOT / "matches" / match_id / "review" / "jobs"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def status_path_for(match_id: str, job_id: str) -> Path:
    return job_dir(match_id) / f"{job_id}.status.yaml"


def latest_status_path(match_id: str) -> Path:
    return job_dir(match_id, create=False) / "latest_status.yaml"


def persisted_payload(job: Dict[str, Any], tail: str = "") -> Dict[str, Any]:
    payload = {key: value for key, value in job.items() if key not in {"process", "log_handle"}}
    payload["log_tail"] = tail
    payload["updated_at"] = now_iso()
    return payload


def persist_job_status(status: Dict[str, Any]) -> None:
    match_id = str(status.get("match_id") or "")
    job_id = str(status.get("job_id") or "")
    if not match_id or not job_id:
        return
    write_yaml(status_path_for(match_id, job_id), status)
    write_yaml(latest_status_path(match_id), status)


def read_log_tail(log_path: str, limit: int = 5000) -> str:
    path = Path(log_path)
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[-limit:]


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
        if code is not None and job.get("log_handle"):
            try:
                job["log_handle"].close()
            except OSError:
                pass
            job.pop("log_handle", None)
    tail = read_log_tail(str(job.get("log_path") or ""))
    status = persisted_payload(job, tail)
    persist_job_status(status)
    return status


def persisted_job_statuses() -> List[Dict[str, Any]]:
    matches_dir = PROJECT_ROOT / "matches"
    if not matches_dir.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for path in matches_dir.glob("*/review/jobs/*.status.yaml"):
        payload = load_yaml(path)
        if payload:
            rows.append(payload)
    return rows


def all_job_statuses() -> List[Dict[str, Any]]:
    by_id: Dict[str, Dict[str, Any]] = {}
    for item in persisted_job_statuses():
        job_id = str(item.get("job_id") or "")
        if job_id:
            by_id[job_id] = item
    for item in JOBS.values():
        status = job_status(item)
        by_id[str(status.get("job_id"))] = status
    statuses = list(by_id.values())
    statuses.sort(key=lambda item: item.get("started_at", ""), reverse=True)
    return statuses


def latest_job_for_match(match_id: str) -> Optional[Dict[str, Any]]:
    latest = load_yaml(latest_status_path(match_id))
    memory_statuses = [job_status(job) for job in JOBS.values() if job.get("match_id") == match_id]
    candidates = memory_statuses + ([latest] if latest else [])
    candidates = [item for item in candidates if item]
    candidates.sort(key=lambda item: item.get("started_at", ""), reverse=True)
    return candidates[0] if candidates else None


def get_job_status(job_id: str) -> Optional[Dict[str, Any]]:
    if job_id in JOBS:
        return job_status(JOBS[job_id])
    for item in persisted_job_statuses():
        if item.get("job_id") == job_id:
            return item
    return None


def start_job(match_id: str, name: str, command: List[str]) -> Dict[str, Any]:
    log_dir = job_dir(match_id)
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
        "log_handle": handle,
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
    ]
    return start_job(match_id, "final_report", command)


def start_prepare_ball_review(match_id: str) -> Dict[str, Any]:
    command = [
        sys.executable,
        "scripts/15_prepare_ball_review.py",
        "--config",
        f"matches/{match_id}/config/match.yaml",
    ]
    return start_job(match_id, "prepare_ball_review", command)


def start_analysis(match_id: str, device: str = "mps") -> Dict[str, Any]:
    return start_prepare_human_review(match_id, device)
