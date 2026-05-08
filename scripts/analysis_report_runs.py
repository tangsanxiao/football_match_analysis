"""Helpers for versioned report output directories."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LEGACY_REPORT_NAME = "mvp_initial"
LATEST_REPORT_FILE = "latest_report.yaml"


def resolve_path(path: Any) -> Path:
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


def report_base_dir(config: Dict[str, Any]) -> Path:
    match = config.get("match", {})
    return resolve_path(match.get("output_dir", "reports"))


def new_report_run_id(prefix: str = "report") -> str:
    return f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def report_dir_for_output_name(config: Dict[str, Any], output_name: Optional[str], prefix: str = "report") -> Path:
    base = report_base_dir(config)
    name = str(output_name or "").strip()
    if not name:
        return base / "runs" / new_report_run_id(prefix)
    path = Path(name)
    if path.is_absolute():
        return path
    return base / path


def report_ready(report_dir: Path) -> bool:
    return (report_dir / "report.md").exists() and (report_dir / "report.html").exists()


def report_record(report_dir: Path, source: str = "unknown") -> Dict[str, Any]:
    return {
        "report_dir": project_relative(report_dir),
        "report_md": project_relative(report_dir / "report.md"),
        "report_html": project_relative(report_dir / "report.html"),
        "source": source,
    }


def write_latest_report(config: Dict[str, Any], report_dir: Path, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = {
        **report_record(report_dir, source="latest_report"),
        "status": "ready",
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    if extra:
        payload.update(extra)
    write_yaml(report_base_dir(config) / LATEST_REPORT_FILE, payload)
    return payload


def latest_report_record(config: Dict[str, Any]) -> Dict[str, Any]:
    base = report_base_dir(config)
    latest = load_yaml(base / LATEST_REPORT_FILE)
    if latest:
        report_dir = resolve_path(latest.get("report_dir", ""))
        if report_ready(report_dir):
            return {
                **latest,
                "report_dir": project_relative(report_dir),
                "report_md": project_relative(report_dir / "report.md"),
                "report_html": project_relative(report_dir / "report.html"),
            }

    review_dir = resolve_path(config.get("match", {}).get("review_dir", "review"))
    final_status = load_yaml(review_dir / "human_review" / "final_report_status.yaml")
    if final_status.get("report_dir"):
        report_dir = resolve_path(final_status["report_dir"])
        if report_ready(report_dir):
            return {**report_record(report_dir, source="final_report_status"), **final_status}

    runs_dir = base / "runs"
    if runs_dir.exists():
        candidates = [path for path in runs_dir.iterdir() if path.is_dir() and report_ready(path)]
        if candidates:
            candidates.sort(key=lambda path: (path / "report.md").stat().st_mtime, reverse=True)
            return report_record(candidates[0], source="runs_latest")

    legacy = base / LEGACY_REPORT_NAME
    if report_ready(legacy):
        return report_record(legacy, source="legacy")
    return {}
