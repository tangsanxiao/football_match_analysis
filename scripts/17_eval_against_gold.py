#!/usr/bin/env python3
"""Evaluation driver — runs Layer A (sanity) and optionally Layer B (accuracy)
against one or more finalized matches.

Layer A is always run. Layer B runs whenever ``evals/gold/<match_id>/``
exists for the given match (or always when ``--gold auto``).

Examples:
    python scripts/17_eval_against_gold.py --match 中青赛_1_20260506_213657
    python scripts/17_eval_against_gold.py --match m1 --match m2 --output baseline_v1
    python scripts/17_eval_against_gold.py --match m1 --tolerances player_position_m=2.0
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import pandas as pd
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from analysis_eval import (  # noqa: E402
    GoldBundle,
    LayerAResult,
    LayerBResult,
    Metric,
    SEVERITY_FAIL,
    SEVERITY_INFO,
    SEVERITY_WARN,
    load_gold,
    run_layer_a,
    run_layer_b,
)


GOLD_ROOT = PROJECT_ROOT / "evals" / "gold"
EVAL_RUNS_ROOT = PROJECT_ROOT / "evals" / "runs"


# ---------------------------------------------------------------------------
# Path helpers (duplicated from 16_merge_match_segments — pending consolidation)
# ---------------------------------------------------------------------------

def resolve_path(path: Union[str, Path]) -> Path:
    p = Path(path).expanduser()
    return p if p.is_absolute() else PROJECT_ROOT / p


def project_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT.resolve()))
    except ValueError:
        return str(path.resolve())


def find_match_dir(match_id_or_path: str) -> Path:
    p = Path(match_id_or_path)
    if p.exists():
        return p.resolve()
    candidate = PROJECT_ROOT / "matches" / match_id_or_path
    if candidate.exists():
        return candidate.resolve()
    raise FileNotFoundError(
        f"Could not resolve match: {match_id_or_path!r} "
        "(looked under matches/ and as an explicit path)"
    )


def find_latest_report(match_dir: Path) -> Optional[Path]:
    reports_dir = match_dir / "reports"
    latest_path = reports_dir / "latest_report.yaml"
    if latest_path.exists():
        with latest_path.open("r", encoding="utf-8") as h:
            data = yaml.safe_load(h) or {}
        report_dir = resolve_path(data.get("report_dir") or "")
        if report_dir.exists() and (report_dir / "report.md").exists():
            return report_dir
    runs_dir = reports_dir / "runs"
    if runs_dir.exists():
        finalized = sorted(
            (d for d in runs_dir.iterdir() if d.is_dir() and (d / "report.md").exists()),
            key=lambda d: (d / "report.md").stat().st_mtime,
            reverse=True,
        )
        if finalized:
            return finalized[0]
    if reports_dir.exists():
        legacy = sorted(
            (d for d in reports_dir.iterdir() if d.is_dir() and (d / "report.md").exists()),
            key=lambda d: (d / "report.md").stat().st_mtime,
            reverse=True,
        )
        if legacy:
            return legacy[0]
    return None


def load_system_ball_points(match_dir: Path, source: str = "raw") -> Optional[pd.DataFrame]:
    """Return system-side ball points for Layer B comparison.

    source="raw":      YOLO's auto-detected ball boxes (pre human review).
                       Loaded from the segment's tracks.csv filtered to
                       class_name == "sports ball". This is the meaningful
                       comparison against human-labeled gold.
    source="reviewed": post human-review consolidated ball points. Useful for
                       measuring the labeling effort, but NOT a measure of the
                       model — the gold typically came from this same file.
    """
    if source == "reviewed":
        path = match_dir / "review" / "ball_review" / "ball_review_points.csv"
        if not path.exists():
            return None
        try:
            df = pd.read_csv(path)
        except Exception:
            return None
        # Normalize to (timestamp_sec, ball_in_play, field_x_m, field_y_m)
        return df

    # source == "raw"
    detections_dir = _resolve_source_detections_dir(match_dir)
    if detections_dir is None:
        return None
    tracks_csv = detections_dir / "tracks.csv"
    if not tracks_csv.exists():
        return None
    try:
        df = pd.read_csv(tracks_csv)
    except Exception:
        return None
    if "class_name" not in df.columns:
        return None
    ball = df[df["class_name"].astype(str).str.lower() == "sports ball"].copy()
    if ball.empty:
        return ball  # empty df, eval will see 0 system points
    # Conform to the columns the eval expects
    ball["ball_in_play"] = ball.get("inside_play_area", True)
    return ball[["timestamp_sec", "field_x_m", "field_y_m", "ball_in_play", "conf"]]


def _resolve_source_detections_dir(match_dir: Path) -> Optional[Path]:
    """Read latest_report.yaml's source_detections_dir; fall back to scanning."""
    latest_yaml = match_dir / "reports" / "latest_report.yaml"
    if latest_yaml.exists():
        with latest_yaml.open("r", encoding="utf-8") as h:
            data = yaml.safe_load(h) or {}
        sdd = data.get("source_detections_dir")
        if sdd:
            cand = resolve_path(sdd)
            if cand.exists():
                return cand
    # Fallback: pick the segment directory with the largest tracks.csv
    detections_root = match_dir / "data" / "interim" / "detections"
    if not detections_root.exists():
        return None
    candidates = [
        d for d in detections_root.iterdir()
        if d.is_dir() and (d / "tracks.csv").exists()
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda d: (d / "tracks.csv").stat().st_size, reverse=True)
    return candidates[0]


def parse_tolerance_overrides(items: Optional[List[str]]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    if not items:
        return out
    for raw in items:
        if "=" not in raw:
            raise SystemExit(f"--tolerances expects key=value, got: {raw!r}")
        k, v = raw.split("=", 1)
        out[k.strip()] = float(v.strip())
    return out


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

SEV_BADGE = {
    SEVERITY_FAIL: "❌",
    SEVERITY_WARN: "⚠️",
    SEVERITY_INFO: "ℹ️",
}


def render_layer_a_md(result: LayerAResult) -> List[str]:
    lines = [
        f"### Match: `{result.match_id}`  ·  {'PASS ✅' if result.passed else 'FAIL ❌'}",
        "",
        f"- fail: **{result.fail_count}**, warn: {result.warn_count}, info: {result.info_count}",
        "",
    ]
    if not result.findings:
        lines.append("_(no findings)_")
        return lines
    lines += ["| | code | message |", "| --- | --- | --- |"]
    for f in result.findings:
        msg = f.message.replace("|", r"\|")
        lines.append(f"| {SEV_BADGE.get(f.severity, '?')} | `{f.code}` | {msg} |")
    return lines


def render_layer_b_md(result: LayerBResult) -> List[str]:
    lines = [
        f"### Layer B · `{result.match_id}`",
        "",
        f"Gold window: {result.gold_window[0]:.1f}s → {result.gold_window[1]:.1f}s "
        f"({max(0, result.gold_window[1] - result.gold_window[0]):.1f}s).",
        "",
    ]
    if result.metrics:
        lines += ["| metric | value | num/denom | unit | note |",
                  "| --- | ---: | --- | --- | --- |"]
        for m in result.metrics:
            v = "—" if m.value is None else f"{m.value}"
            lines.append(
                f"| `{m.name}` | {v} | {m.numerator}/{m.denominator} | {m.unit} | {m.note} |"
            )
        lines.append("")
    if result.per_event_metrics:
        lines += ["#### Events", "",
                  "| event_type | recall | precision | gold | system |",
                  "| --- | ---: | ---: | ---: | ---: |"]
        for etype, mset in sorted(result.per_event_metrics.items()):
            r = mset.get("recall")
            p = mset.get("precision")
            r_v = "—" if (not r or r.value is None) else f"{r.value}%"
            p_v = "—" if (not p or p.value is None) else f"{p.value}%"
            gold_n = r.denominator if r else 0
            sys_n = p.denominator if p else 0
            lines.append(f"| `{etype}` | {r_v} | {p_v} | {gold_n} | {sys_n} |")
        lines.append("")
    if result.notes:
        lines += ["**Notes:**"] + [f"- {n}" for n in result.notes] + [""]
    return lines


def render_report_md(
    eval_id: str,
    layer_a_results: List[LayerAResult],
    layer_b_results: List[LayerBResult],
) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    overall_pass = all(r.passed for r in layer_a_results)
    lines: List[str] = [
        f"# Evaluation report · `{eval_id}`",
        "",
        f"_Generated by `scripts/17_eval_against_gold.py` at {now}._",
        "",
        f"**Overall Layer A**: {'✅ all passed' if overall_pass else '❌ has failures'}.",
        "",
        "## Layer A · Sanity",
        "",
    ]
    for r in layer_a_results:
        lines.extend(render_layer_a_md(r))
        lines.append("")
    if layer_b_results:
        lines += ["## Layer B · Accuracy", ""]
        for r in layer_b_results:
            lines.extend(render_layer_b_md(r))
    else:
        lines += [
            "## Layer B · Accuracy",
            "",
            "_No gold segments matched any input match. Drop labeled gold under_ "
            "`evals/gold/<match_id>/` _to get accuracy numbers._",
            "",
        ]
    return "\n".join(lines)


def render_report_html(md: str, title: str) -> str:
    body = html.escape(md)
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="utf-8"><title>{html.escape(title)}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif;
       max-width: 1280px; margin: 24px auto; padding: 0 16px; color: #1b241f; background: #f5f7f4;
       line-height: 1.55; }}
pre {{ white-space: pre-wrap; word-break: break-word; background: #ffffff;
       border: 1px solid #d8ded8; border-radius: 8px; padding: 16px; font-size: 13px;
       font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
</style></head><body><pre>{body}</pre></body></html>
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def evaluate_match(
    match_id_or_path: str,
    use_gold: bool,
    tolerance_overrides: Dict[str, float],
    ball_source: str = "raw",
) -> Dict[str, Any]:
    match_dir = find_match_dir(match_id_or_path)
    match_id = match_dir.name
    report_dir = find_latest_report(match_dir)

    if report_dir is None:
        # Layer A still runs and will fail with `report_dir_missing`
        layer_a = LayerAResult(match_id=match_id)
        layer_a.add(SEVERITY_FAIL, "report_dir_missing",
                    f"No finalized report found under {match_dir / 'reports'}")
        return {"match_id": match_id, "match_dir": project_relative(match_dir),
                "report_dir": None, "layer_a": layer_a, "layer_b": None}

    layer_a = run_layer_a(match_id=match_id, report_dir=report_dir)

    layer_b: Optional[LayerBResult] = None
    if use_gold:
        try:
            gold = load_gold(match_id, GOLD_ROOT)
        except ValueError as exc:
            layer_a.add(SEVERITY_FAIL, "gold_schema_error", str(exc))
            gold = None
        if gold is not None:
            tracks_path = report_dir / "tracks_red_labeled.csv"
            events_path = report_dir / "key_timestamps.csv"
            sys_tracks = pd.read_csv(tracks_path) if tracks_path.exists() else None
            sys_events = pd.read_csv(events_path) if events_path.exists() else None
            sys_ball = load_system_ball_points(match_dir, source=ball_source)
            layer_b = run_layer_b(
                match_id=match_id,
                gold=gold,
                system_tracks=sys_tracks,
                system_ball=sys_ball,
                system_events=sys_events,
                tolerances=tolerance_overrides,
            )

    return {
        "match_id": match_id,
        "match_dir": project_relative(match_dir),
        "report_dir": project_relative(report_dir),
        "layer_a": layer_a,
        "layer_b": layer_b,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Layer A sanity + optional Layer B accuracy eval over matches."
    )
    parser.add_argument(
        "--match", action="append", required=True, dest="matches",
        help="match_id under matches/ or path to a match dir (repeatable)",
    )
    parser.add_argument(
        "--gold", choices=["auto", "off"], default="auto",
        help="auto = load evals/gold/<match_id>/ if present; off = Layer A only",
    )
    parser.add_argument(
        "--tolerances", action="append", default=None,
        help="override Layer B tolerances, e.g. player_position_m=2.0 (repeatable)",
    )
    parser.add_argument(
        "--ball-source", choices=["raw", "reviewed"], default="raw",
        help="raw = YOLO auto detections (default, the honest model comparison); "
             "reviewed = post human-review consolidated points (circular if your gold "
             "was extracted from the same file).",
    )
    parser.add_argument(
        "--output", default="",
        help="Output dir name under evals/runs/. Defaults to a timestamp.",
    )
    args = parser.parse_args()

    tols = parse_tolerance_overrides(args.tolerances)
    use_gold = args.gold == "auto"

    results: List[Dict[str, Any]] = []
    for raw in args.matches:
        try:
            results.append(evaluate_match(
                raw, use_gold=use_gold, tolerance_overrides=tols,
                ball_source=args.ball_source,
            ))
        except FileNotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    layer_a_list = [r["layer_a"] for r in results]
    layer_b_list = [r["layer_b"] for r in results if r["layer_b"] is not None]

    eval_id = args.output.strip() or datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = EVAL_RUNS_ROOT / eval_id
    out_dir.mkdir(parents=True, exist_ok=True)

    md = render_report_md(eval_id, layer_a_list, layer_b_list)
    (out_dir / "report.md").write_text(md, encoding="utf-8")
    (out_dir / "report.html").write_text(
        render_report_html(md, f"Eval · {eval_id}"), encoding="utf-8"
    )
    with (out_dir / "layer_a.yaml").open("w", encoding="utf-8") as h:
        yaml.safe_dump(
            [r.to_dict() for r in layer_a_list], h, allow_unicode=True, sort_keys=False
        )
    if layer_b_list:
        with (out_dir / "layer_b.yaml").open("w", encoding="utf-8") as h:
            yaml.safe_dump(
                [r.to_dict() for r in layer_b_list], h, allow_unicode=True, sort_keys=False
            )

    # Index into a manifest
    manifest = {
        "eval_id": eval_id,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "tolerances_applied": tols,
        "ball_source": args.ball_source,
        "matches": [
            {
                "match_id": r["match_id"],
                "match_dir": r["match_dir"],
                "report_dir": r["report_dir"],
                "layer_a_passed": r["layer_a"].passed,
                "layer_a_fails": r["layer_a"].fail_count,
                "layer_a_warns": r["layer_a"].warn_count,
                "has_gold": r["layer_b"] is not None,
            }
            for r in results
        ],
    }
    with (out_dir / "manifest.yaml").open("w", encoding="utf-8") as h:
        yaml.safe_dump(manifest, h, allow_unicode=True, sort_keys=False)

    # Console summary
    print(f"Eval written to: {project_relative(out_dir)}")
    print()
    for r in results:
        a = r["layer_a"]
        flag = "PASS" if a.passed else "FAIL"
        ext = " (gold)" if r["layer_b"] is not None else ""
        print(f"  [{flag}] {r['match_id']:40s}  fails={a.fail_count} warns={a.warn_count}{ext}")

    return 0 if all(r["layer_a"].passed for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
