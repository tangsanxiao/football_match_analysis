#!/usr/bin/env python3
"""Merge multiple match-segment reports into a single combined report.

Use case: a football match is split into halves (or any number of segments) and
each segment is analyzed as its own match project. This script aggregates the
per-player metrics, reference candidates, key timestamps, and labeled tracks
from those segments into one combined report at the project level.

Each input segment must already have a finalized report (i.e. the standard
CSVs and report.md/report.html exist under reports/runs/<run>/).

Example:
    python scripts/16_merge_match_segments.py \
        --match 中青赛_2026_05_10_h1 --label 上半场 \
        --match 中青赛_2026_05_10_h2 --label 下半场 \
        --name "中青赛 2026-05-10" \
        --output 中青赛_2026_05_10
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import pandas as pd
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Path / IO helpers
# ---------------------------------------------------------------------------

def resolve_path(path: Union[str, Path]) -> Path:
    p = Path(path).expanduser()
    if p.is_absolute():
        return p
    return PROJECT_ROOT / p


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


def find_match_dir(match_id_or_path: str) -> Path:
    """Resolve --match argument: either a match_id under matches/ or an explicit path."""
    p = Path(match_id_or_path)
    if p.is_absolute() and p.exists():
        return p.resolve()
    if p.exists():
        return p.resolve()
    candidate = PROJECT_ROOT / "matches" / match_id_or_path
    if candidate.exists():
        return candidate.resolve()
    raise FileNotFoundError(
        f"Could not resolve match: {match_id_or_path!r} "
        f"(looked under matches/ and as an explicit path)"
    )


def load_match_config(match_dir: Path) -> Dict[str, Any]:
    for path in (match_dir / "config" / "match.yaml", match_dir / "match.yaml"):
        if path.exists():
            return load_yaml(path)
    return {}


def find_latest_report(match_dir: Path) -> Path:
    """Find the latest finalized report directory for a match.

    Order:
        1. reports/latest_report.yaml -> report_dir
        2. reports/runs/<latest dir with report.md>
        3. reports/<any dir with report.md>
    """
    reports_dir = match_dir / "reports"

    latest_yaml = reports_dir / "latest_report.yaml"
    latest = load_yaml(latest_yaml)
    if latest and latest.get("report_dir"):
        report_dir = resolve_path(latest["report_dir"])
        if (report_dir / "report.md").exists():
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

    raise FileNotFoundError(f"No finalized report found under {reports_dir}")


def segment_duration_sec(match_dir: Path, config: Dict[str, Any], report_dir: Path) -> float:
    """Best-effort segment duration in seconds.

    Tries video_probe.json, then tracks_red_labeled.csv max timestamp, then
    key_timestamps.csv max timestamp.
    """
    interim = config.get("match", {}).get("interim_dir")
    if interim:
        probe = resolve_path(interim) / "video_probe.json"
        if probe.exists():
            try:
                with probe.open("r", encoding="utf-8") as handle:
                    data = json.load(handle)
                duration = float(data.get("opencv", {}).get("duration_sec") or 0)
                if duration > 0:
                    return duration
            except Exception:
                pass

    for csv_name in ("tracks_red_labeled.csv", "key_timestamps.csv"):
        csv_path = report_dir / csv_name
        if csv_path.exists():
            try:
                df = pd.read_csv(csv_path)
                if "timestamp_sec" in df.columns and len(df):
                    return float(pd.to_numeric(df["timestamp_sec"], errors="coerce").max())
            except Exception:
                pass

    return 0.0


# ---------------------------------------------------------------------------
# Merge logic
# ---------------------------------------------------------------------------

PLAYER_SUM_COLS = ["observed_frames", "observed_seconds", "distance_m", "high_speed_distance_m"]
PLAYER_WEIGHTED_AVG_COLS = [
    "observed_coverage_pct",
    "high_speed_pct",
    "pressing_pct",
    "duel_proximity_pct",
    "attacking_half_pct",
    "final_third_pct",
    "role_zone_pct",
    "avg_nearest_opponent_m",
    "avg_x_m",
    "avg_y_m",
    "rating",
]

# Most-conservative-wins ordering. Higher rank = more conservative / lower trust.
CONFIDENCE_RANK = {
    "high": 0, "高": 0,
    "medium": 1, "中": 1,
    "low": 2, "低": 2,
    "candidate": 3, "候选": 3,
    "provisional": 4, "待校正": 4,
    "sample insufficient": 5, "样本不足": 5,
    "none": 6, "无": 6,
    "": 99,
}


def confidence_min(values: List[Any]) -> str:
    """Return the most conservative (worst trust) confidence label among inputs."""
    if not values:
        return ""
    cleaned = [str(v).strip() for v in values if v is not None and str(v).strip()]
    if not cleaned:
        return ""
    cleaned.sort(key=lambda v: CONFIDENCE_RANK.get(v.lower(), 50))
    return cleaned[-1]


def merge_player_metrics(dfs: List[pd.DataFrame]) -> pd.DataFrame:
    """Aggregate per-player metrics across segments.

    Strategy:
      - Identity columns (name/number/role): take first non-empty value
      - Sum columns: sum
      - Weighted-average columns: weight by observed_frames
      - distance_per_min: recomputed from total distance / total observed time
      - confidence: most conservative across segments
    """
    if not dfs:
        return pd.DataFrame()

    combined = pd.concat(dfs, ignore_index=True, sort=False)
    if "player_id" not in combined.columns:
        raise ValueError("player_metrics.csv must have a player_id column")

    base_cols = list(dfs[0].columns)
    out_rows: List[Dict[str, Any]] = []

    for player_id, group in combined.groupby("player_id", sort=False):
        row: Dict[str, Any] = {"player_id": player_id}

        for col in ("name", "number", "role"):
            if col in group.columns:
                vals = [v for v in group[col].tolist() if pd.notna(v) and str(v).strip()]
                row[col] = vals[0] if vals else ""

        weights = pd.to_numeric(
            group.get("observed_frames", pd.Series([0] * len(group))),
            errors="coerce",
        ).fillna(0)
        total_weight = float(weights.sum())

        for col in PLAYER_SUM_COLS:
            if col in group.columns:
                vals = pd.to_numeric(group[col], errors="coerce").fillna(0)
                row[col] = round(float(vals.sum()), 2)

        for col in PLAYER_WEIGHTED_AVG_COLS:
            if col in group.columns:
                vals = pd.to_numeric(group[col], errors="coerce").fillna(0)
                if total_weight > 0:
                    row[col] = round(float((vals * weights).sum() / total_weight), 2)
                else:
                    row[col] = 0.0

        observed_seconds = float(row.get("observed_seconds") or 0)
        distance_m = float(row.get("distance_m") or 0)
        if observed_seconds > 0:
            row["distance_per_min"] = round(distance_m / observed_seconds * 60.0, 2)
        else:
            row["distance_per_min"] = 0.0

        if "confidence" in group.columns:
            row["confidence"] = confidence_min(group["confidence"].tolist())

        out_rows.append(row)

    out_df = pd.DataFrame(out_rows)
    ordered = [c for c in base_cols if c in out_df.columns]
    extras = [c for c in out_df.columns if c not in ordered]
    return out_df[ordered + extras]


def merge_reference_metrics(
    dfs: List[pd.DataFrame],
    weights_by_name: Dict[str, float],
) -> pd.DataFrame:
    """Aggregate the per-player technical-candidate reference metrics.

    Counts → sum; percentages → weighted average by observed_frames if known
    else simple average. `传球成功率参考` is intentionally not recomputed
    (would require ball-event re-derivation) and is replaced with a notice.
    """
    if not dfs:
        return pd.DataFrame()

    combined = pd.concat(dfs, ignore_index=True, sort=False)
    name_col = "球员"
    if name_col not in combined.columns:
        return combined

    sum_cols = ["射门候选", "传球候选", "抢断候选", "1v1攻防候选"]
    pct_cols = [
        "站位纪律%", "压迫强度%",
        "无球跑动指数", "防守无球跑动指数", "创造空间指数",
    ]

    out_rows: List[Dict[str, Any]] = []
    for name, group in combined.groupby(name_col, sort=False):
        row: Dict[str, Any] = {name_col: name}

        if "号码" in group.columns:
            vals = [v for v in group["号码"].tolist() if pd.notna(v) and str(v).strip()]
            row["号码"] = vals[0] if vals else ""

        for col in sum_cols:
            if col in group.columns:
                row[col] = int(pd.to_numeric(group[col], errors="coerce").fillna(0).sum())

        weight = float(weights_by_name.get(str(name), 0.0))
        for col in pct_cols:
            if col in group.columns:
                vals = pd.to_numeric(group[col], errors="coerce").fillna(0)
                row[col] = round(float(vals.mean()), 2)
                # Note: per-row observed_frames isn't recorded in reference_metrics.csv,
                # so true weighted averaging would require joining player_metrics.
                # We document this in the report.

        if "传球成功率参考" in group.columns:
            row["传球成功率参考"] = "（合并报告未重算）"

        if "置信度" in group.columns:
            confs = group["置信度"].dropna().tolist()
            row["置信度"] = confidence_min(confs) if confs else ""

        if "球点过滤提示" in group.columns:
            unique_notes = list(dict.fromkeys(
                str(v).strip() for v in group["球点过滤提示"].tolist() if pd.notna(v) and str(v).strip()
            ))
            row["球点过滤提示"] = " ｜ ".join(unique_notes)

        out_rows.append(row)

    base_cols = list(dfs[0].columns)
    out_df = pd.DataFrame(out_rows)
    ordered = [c for c in base_cols if c in out_df.columns]
    extras = [c for c in out_df.columns if c not in ordered]
    return out_df[ordered + extras]


def merge_key_timestamps(
    items: List[Tuple[Optional[pd.DataFrame], float, str]],
) -> pd.DataFrame:
    """Concatenate event rows from each segment with timestamp offset applied."""
    parts = []
    for df, offset, label in items:
        if df is None or len(df) == 0:
            continue
        d = df.copy()
        if "timestamp_sec" in d.columns:
            d["timestamp_sec"] = (
                pd.to_numeric(d["timestamp_sec"], errors="coerce").fillna(0) + offset
            )
            d["timestamp"] = d["timestamp_sec"].apply(
                lambda s: f"{int(round(s)) // 60:02d}:{int(round(s)) % 60:02d}"
            )
        d.insert(0, "segment", label)
        parts.append(d)
    if not parts:
        return pd.DataFrame()
    out = pd.concat(parts, ignore_index=True, sort=False)
    if "timestamp_sec" in out.columns:
        out = out.sort_values("timestamp_sec", kind="stable").reset_index(drop=True)
    return out


def merge_tracks(
    items: List[Tuple[Optional[pd.DataFrame], float, str]],
) -> pd.DataFrame:
    """Concatenate frame-level tracks across segments with timestamp offset."""
    parts = []
    for df, offset, label in items:
        if df is None or len(df) == 0:
            continue
        d = df.copy()
        if "timestamp_sec" in d.columns:
            d["timestamp_sec"] = (
                pd.to_numeric(d["timestamp_sec"], errors="coerce").fillna(0) + offset
            )
        d.insert(0, "segment", label)
        parts.append(d)
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True, sort=False)


# ---------------------------------------------------------------------------
# Markdown / HTML rendering (no tabulate dependency)
# ---------------------------------------------------------------------------

def df_to_markdown(df: pd.DataFrame, max_rows: Optional[int] = None) -> str:
    if df is None or df.empty:
        return "_(空)_"
    view = df.head(max_rows) if max_rows else df
    cols = list(view.columns)
    header = "| " + " | ".join(str(c) for c in cols) + " |"
    sep = "| " + " | ".join("---" for _ in cols) + " |"
    body_lines = []
    for _, row in view.iterrows():
        cells = []
        for c in cols:
            val = row[c]
            if pd.isna(val):
                cells.append("")
            else:
                text = str(val).replace("|", r"\|").replace("\n", " ")
                cells.append(text)
        body_lines.append("| " + " | ".join(cells) + " |")
    note = ""
    if max_rows is not None and len(df) > max_rows:
        note = f"\n\n_仅显示前 {max_rows} 行，共 {len(df)} 行；完整数据见 CSV。_"
    return "\n".join([header, sep, *body_lines]) + note


def render_markdown(
    combined_name: str,
    segments: List[Dict[str, Any]],
    player_df: pd.DataFrame,
    ref_df: pd.DataFrame,
    events_df: pd.DataFrame,
    total_duration_sec: float,
) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines: List[str] = [
        f"# {combined_name} · 合并报告",
        "",
        f"_由 `scripts/16_merge_match_segments.py` 于 {now} 生成。_",
        "",
        f"**全场总时长**：约 {total_duration_sec / 60:.1f} 分钟（合计 {total_duration_sec:.0f} 秒）。",
        "",
        "## 来源段",
        "",
        "| # | 标签 | 比赛项目 | 时长(秒) | 偏移(秒) | 报告路径 |",
        "| ---: | --- | --- | ---: | ---: | --- |",
    ]
    for seg in segments:
        lines.append(
            f"| {seg['index'] + 1} | {seg['label']} | {seg['match_id']} "
            f"| {seg['duration_sec']:.0f} | {seg['offset_sec']:.0f} | `{seg['report_dir']}` |"
        )

    lines += ["", "## 球员合并指标", ""]
    lines.append(df_to_markdown(player_df))

    if not ref_df.empty:
        lines += [
            "",
            "## 重点指标候选（合并）",
            "",
            "> 候选数为各段累加；百分比类指标为各段算术平均（粗略）。",
            "> 由于 `reference_metrics.csv` 不含逐段观察帧权重，合并百分比未做加权——",
            "> 若需精确，请直接基于合并后的 `player_metrics.csv` 与重新统计的事件重算。",
            "",
        ]
        lines.append(df_to_markdown(ref_df))

    if not events_df.empty:
        lines += ["", "## 关键时间线（全场）", ""]
        lines.append(df_to_markdown(events_df, max_rows=80))

    lines += [
        "",
        "## 注意事项",
        "",
        "- 本报告由多个独立分析段合并而成，**段间跟踪 ID 不连续**。球员级聚合按 `player_id` 合并，正常生效。",
        "- 所有时间戳已加上各段相对偏移，在全场时间轴上连续。",
        "- 球点过滤提示与传球成功率为各段独立计算，合并视图未重算，仅作参考。",
        "- 合并报告的置信度取各段中**最保守**的一档。",
        "",
    ]
    return "\n".join(lines)


def render_html(md: str, title: str) -> str:
    body = html.escape(md)
    return f"""<!DOCTYPE html>
<html lang=\"zh-CN\">
<head>
<meta charset=\"utf-8\">
<title>{html.escape(title)}</title>
<style>
body {{
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif;
  max-width: 1280px; margin: 24px auto; padding: 0 16px;
  color: #1b241f; background: #f5f7f4; line-height: 1.55;
}}
pre {{
  white-space: pre-wrap; word-break: break-word; background: #ffffff;
  border: 1px solid #d8ded8; border-radius: 8px; padding: 16px;
  font-size: 13px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}}
</style>
</head>
<body>
<pre>{body}</pre>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def safe_read_csv(path: Path) -> Optional[pd.DataFrame]:
    if not path.exists():
        return None
    try:
        return pd.read_csv(path)
    except Exception as exc:
        print(f"  warn: failed to read {path}: {exc}", file=sys.stderr)
        return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Merge multiple match-segment reports into one combined report.",
    )
    parser.add_argument(
        "--match", action="append", required=True, dest="matches",
        help="Match id under matches/ or an explicit path. Repeat for each segment, in order.",
    )
    parser.add_argument(
        "--label", action="append", default=None, dest="labels",
        help="Optional label per segment (e.g. '上半场','下半场'). Must equal --match count if given.",
    )
    parser.add_argument(
        "--name", default="",
        help="Display name for the combined match. Defaults to ' + '.join(match_ids).",
    )
    parser.add_argument(
        "--output", default="",
        help="Output dir name under reports/combined/. Defaults to a timestamped slug.",
    )
    args = parser.parse_args()

    if len(args.matches) < 1:
        parser.error("Provide at least one --match. (Use 1 to copy a single segment, 2+ to merge.)")
    if args.labels is not None and len(args.labels) != len(args.matches):
        parser.error("--label count must equal --match count")

    labels = list(args.labels) if args.labels else [
        f"段{i + 1}" if len(args.matches) > 1 else "全场"
        for i in range(len(args.matches))
    ]

    # Resolve segments
    match_dirs = [find_match_dir(m) for m in args.matches]
    report_dirs = [find_latest_report(d) for d in match_dirs]
    configs = [load_match_config(d) for d in match_dirs]
    durations = [
        segment_duration_sec(d, c, r)
        for d, c, r in zip(match_dirs, configs, report_dirs)
    ]

    offsets = [0.0]
    for dur in durations[:-1]:
        offsets.append(offsets[-1] + dur)

    segments: List[Dict[str, Any]] = []
    for i, (md_, rd, dur, off, lbl) in enumerate(
        zip(match_dirs, report_dirs, durations, offsets, labels),
    ):
        segments.append({
            "index": i,
            "match_id": md_.name,
            "match_dir": project_relative(md_),
            "report_dir": project_relative(rd),
            "duration_sec": float(dur),
            "offset_sec": float(off),
            "label": lbl,
        })

    print("Loaded segments:")
    for seg in segments:
        print(
            f"  [{seg['index'] + 1}] {seg['label']}: "
            f"{seg['match_id']}  dur={seg['duration_sec']:.0f}s offset={seg['offset_sec']:.0f}s",
        )

    player_dfs = [safe_read_csv(rd / "player_metrics.csv") for rd in report_dirs]
    ref_dfs = [safe_read_csv(rd / "reference_metrics.csv") for rd in report_dirs]
    events_dfs = [safe_read_csv(rd / "key_timestamps.csv") for rd in report_dirs]
    track_dfs = [safe_read_csv(rd / "tracks_red_labeled.csv") for rd in report_dirs]

    player_clean = [df for df in player_dfs if df is not None and len(df)]
    if not player_clean:
        print("error: no segment has a usable player_metrics.csv", file=sys.stderr)
        return 1

    merged_players = merge_player_metrics(player_clean)

    weights_by_name: Dict[str, float] = {}
    if "name" in merged_players.columns and "observed_frames" in merged_players.columns:
        for _, row in merged_players.iterrows():
            weights_by_name[str(row["name"])] = float(row["observed_frames"] or 0)

    ref_clean = [df for df in ref_dfs if df is not None and len(df)]
    merged_refs = merge_reference_metrics(ref_clean, weights_by_name)

    events_items: List[Tuple[Optional[pd.DataFrame], float, str]] = list(
        zip(events_dfs, offsets, labels)
    )
    merged_events = merge_key_timestamps(events_items)

    track_items: List[Tuple[Optional[pd.DataFrame], float, str]] = list(
        zip(track_dfs, offsets, labels)
    )
    merged_tracks = merge_tracks(track_items)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_slug = args.output.strip() or f"combined_{timestamp}"
    out_dir = PROJECT_ROOT / "reports" / "combined" / output_slug / timestamp
    out_dir.mkdir(parents=True, exist_ok=True)

    merged_players.to_csv(out_dir / "player_metrics.csv", index=False)
    if not merged_refs.empty:
        merged_refs.to_csv(out_dir / "reference_metrics.csv", index=False)
    if not merged_events.empty:
        merged_events.to_csv(out_dir / "key_timestamps.csv", index=False)
    if not merged_tracks.empty:
        merged_tracks.to_csv(out_dir / "tracks_red_labeled.csv", index=False)

    combined_name = args.name.strip() or " + ".join(seg["match_id"] for seg in segments)
    total_duration = float(sum(durations))

    manifest = {
        "combined_name": combined_name,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "total_duration_sec": total_duration,
        "segments": segments,
        "outputs": {
            "report_md": "report.md",
            "report_html": "report.html",
            "player_metrics": "player_metrics.csv",
            "reference_metrics": "reference_metrics.csv" if not merged_refs.empty else None,
            "key_timestamps": "key_timestamps.csv" if not merged_events.empty else None,
            "tracks_red_labeled": "tracks_red_labeled.csv" if not merged_tracks.empty else None,
        },
    }
    with (out_dir / "manifest.yaml").open("w", encoding="utf-8") as handle:
        yaml.safe_dump(manifest, handle, allow_unicode=True, sort_keys=False)

    md = render_markdown(
        combined_name, segments, merged_players, merged_refs,
        merged_events, total_duration,
    )
    (out_dir / "report.md").write_text(md, encoding="utf-8")
    (out_dir / "report.html").write_text(
        render_html(md, f"{combined_name} · 合并报告"),
        encoding="utf-8",
    )

    print()
    print(f"✅ Combined report written to: {project_relative(out_dir)}")
    print(f"   Segments: {len(segments)}  Total: {total_duration:.0f}s ({total_duration / 60:.1f} min)")
    print(f"   - report.md  /  report.html")
    print(f"   - player_metrics.csv ({len(merged_players)} players)")
    if not merged_refs.empty:
        print(f"   - reference_metrics.csv ({len(merged_refs)} rows)")
    if not merged_events.empty:
        print(f"   - key_timestamps.csv ({len(merged_events)} events)")
    if not merged_tracks.empty:
        print(f"   - tracks_red_labeled.csv ({len(merged_tracks)} frames)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
