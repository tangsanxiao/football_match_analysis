"""Pure helpers for the evaluation harness.

Layer A — match-agnostic sanity / schema / range checks. Runs on any
finalized match without external data.

Layer B — per-match accuracy metrics computed against a "gold" segment under
``evals/gold/<match_id>/`` (see ``evals/gold_schema_v1.yaml``).

This module has *no* CLI; it's pure logic, testable. The CLI driver lives in
``scripts/17_eval_against_gold.py``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

import pandas as pd
import yaml


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

SEVERITY_FAIL = "fail"
SEVERITY_WARN = "warn"
SEVERITY_INFO = "info"


@dataclass
class Finding:
    """A single check result. Aggregated into a LayerAResult."""

    severity: str  # fail | warn | info
    code: str
    message: str
    context: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "context": self.context or {},
        }


@dataclass
class LayerAResult:
    match_id: str
    findings: List[Finding] = field(default_factory=list)

    def add(self, severity: str, code: str, message: str, **context: Any) -> None:
        self.findings.append(Finding(severity, code, message, context))

    @property
    def fail_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == SEVERITY_FAIL)

    @property
    def warn_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == SEVERITY_WARN)

    @property
    def info_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == SEVERITY_INFO)

    @property
    def passed(self) -> bool:
        return self.fail_count == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "match_id": self.match_id,
            "passed": self.passed,
            "summary": {
                "fail": self.fail_count,
                "warn": self.warn_count,
                "info": self.info_count,
            },
            "findings": [f.to_dict() for f in self.findings],
        }


@dataclass
class Metric:
    """One numerical accuracy result. value=None means we couldn't compute it."""

    name: str
    value: Optional[float]
    numerator: int
    denominator: int
    unit: str = ""  # %, m, count
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "unit": self.unit,
            "note": self.note,
        }


@dataclass
class LayerBResult:
    match_id: str
    gold_window: Tuple[float, float] = (0.0, 0.0)
    metrics: List[Metric] = field(default_factory=list)
    per_event_metrics: Dict[str, Dict[str, Metric]] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "match_id": self.match_id,
            "gold_window": list(self.gold_window),
            "metrics": [m.to_dict() for m in self.metrics],
            "per_event_metrics": {
                etype: {k: v.to_dict() for k, v in mset.items()}
                for etype, mset in self.per_event_metrics.items()
            },
            "notes": list(self.notes),
        }


@dataclass
class GoldBundle:
    """Loaded gold artifacts for one match."""

    match_id: str
    gold_dir: Path
    manifest: Dict[str, Any]
    tracks: Optional[pd.DataFrame] = None
    ball_points: Optional[pd.DataFrame] = None
    events: Optional[pd.DataFrame] = None

    @property
    def window(self) -> Tuple[float, float]:
        w = self.manifest.get("window") or {}
        return float(w.get("start_sec", 0)), float(w.get("end_sec", 0))


# ---------------------------------------------------------------------------
# Layer A — match-agnostic sanity checks
# ---------------------------------------------------------------------------

PLAYER_METRICS_REQUIRED = [
    "player_id", "name", "role",
    "observed_frames", "observed_seconds", "observed_coverage_pct",
    "distance_m", "distance_per_min",
    "rating", "confidence",
]
KEY_TIMESTAMPS_REQUIRED = [
    "timestamp_sec", "player_id", "event_type", "confidence",
]
LEGAL_CONFIDENCE = {
    "high", "medium", "low", "candidate", "provisional",
    "sample insufficient", "none",
    "高", "中", "低", "候选", "待校正", "样本不足", "无", "",
}

VALUE_RANGES = {
    "observed_coverage_pct": (0.0, 100.0),
    "rating": (0.0, 10.0),
    "distance_per_min": (0.0, 500.0),
}


def _check_csv_columns(
    result: LayerAResult,
    df: Optional[pd.DataFrame],
    csv_name: str,
    required: Iterable[str],
) -> bool:
    """Return True if df is present and has all required columns."""
    if df is None:
        result.add(SEVERITY_FAIL, "csv_missing",
                   f"{csv_name} is missing or unreadable",
                   csv=csv_name)
        return False
    missing = [c for c in required if c not in df.columns]
    if missing:
        result.add(SEVERITY_FAIL, "csv_columns_missing",
                   f"{csv_name} is missing columns: {missing}",
                   csv=csv_name, missing=missing)
        return False
    return True


def check_smoke(report_dir: Path) -> List[Finding]:
    """Verify the report directory exists and contains the standard files."""
    findings: List[Finding] = []
    must_exist = ["report.md", "report.html",
                  "player_metrics.csv", "key_timestamps.csv"]
    if not report_dir.exists():
        findings.append(Finding(SEVERITY_FAIL, "report_dir_missing",
                                f"Report directory does not exist: {report_dir}",
                                {"report_dir": str(report_dir)}))
        return findings
    for name in must_exist:
        if not (report_dir / name).exists():
            findings.append(Finding(SEVERITY_FAIL, "report_file_missing",
                                    f"Required file missing: {name}",
                                    {"file": name}))
    return findings


def check_value_ranges(player_df: pd.DataFrame) -> List[Finding]:
    findings: List[Finding] = []
    if player_df is None or player_df.empty:
        return findings
    for col, (lo, hi) in VALUE_RANGES.items():
        if col not in player_df.columns:
            continue
        vals = pd.to_numeric(player_df[col], errors="coerce")
        bad_lo = vals[vals < lo].dropna()
        bad_hi = vals[vals > hi].dropna()
        if len(bad_lo):
            findings.append(Finding(
                SEVERITY_FAIL, "value_below_range",
                f"{col} has {len(bad_lo)} value(s) below {lo}",
                {"column": col, "min_observed": float(bad_lo.min()), "lo": lo},
            ))
        if len(bad_hi):
            findings.append(Finding(
                SEVERITY_FAIL, "value_above_range",
                f"{col} has {len(bad_hi)} value(s) above {hi}",
                {"column": col, "max_observed": float(bad_hi.max()), "hi": hi},
            ))
    if "observed_seconds" in player_df.columns:
        zeros = pd.to_numeric(player_df["observed_seconds"], errors="coerce").fillna(0)
        if (zeros <= 0).any():
            n = int((zeros <= 0).sum())
            findings.append(Finding(
                SEVERITY_WARN, "zero_observed_seconds",
                f"{n} player row(s) have observed_seconds <= 0",
                {"column": "observed_seconds", "zero_rows": n},
            ))
    return findings


def check_confidence_labels(player_df: pd.DataFrame) -> List[Finding]:
    findings: List[Finding] = []
    if player_df is None or "confidence" not in player_df.columns:
        return findings
    vals = player_df["confidence"].astype(str).str.strip()
    illegal = sorted({v for v in vals.unique() if v.lower() not in LEGAL_CONFIDENCE and v not in LEGAL_CONFIDENCE})
    if illegal:
        findings.append(Finding(
            SEVERITY_FAIL, "illegal_confidence_label",
            f"player_metrics.csv has illegal confidence label(s): {illegal}",
            {"illegal": illegal},
        ))
    return findings


def check_cross_table_consistency(
    player_df: Optional[pd.DataFrame],
    events_df: Optional[pd.DataFrame],
) -> List[Finding]:
    findings: List[Finding] = []
    if player_df is None or events_df is None:
        return findings
    if "player_id" not in player_df.columns or "player_id" not in events_df.columns:
        return findings
    player_ids = set(player_df["player_id"].astype(str))
    event_player_ids = set(events_df["player_id"].astype(str)) - {"", "nan"}
    orphans = sorted(event_player_ids - player_ids)
    if orphans:
        findings.append(Finding(
            SEVERITY_WARN, "event_player_unknown",
            f"key_timestamps.csv references {len(orphans)} player_id(s) "
            f"not present in player_metrics.csv",
            {"unknown_player_ids": orphans},
        ))
    return findings


def run_layer_a(
    match_id: str,
    report_dir: Path,
    csvs: Optional[Mapping[str, Optional[pd.DataFrame]]] = None,
) -> LayerAResult:
    """Run the full Layer A check suite for one match.

    csvs (optional) is a mapping of csv-name -> DataFrame, useful for tests.
    If not provided, CSVs are read from report_dir.
    """
    result = LayerAResult(match_id=match_id)

    smoke = check_smoke(report_dir)
    result.findings.extend(smoke)
    if any(f.severity == SEVERITY_FAIL for f in smoke):
        return result  # no point continuing if files are missing

    if csvs is None:
        csvs = {
            "player_metrics.csv": _safe_read_csv(report_dir / "player_metrics.csv"),
            "key_timestamps.csv": _safe_read_csv(report_dir / "key_timestamps.csv"),
        }

    player_df = csvs.get("player_metrics.csv")
    events_df = csvs.get("key_timestamps.csv")

    if _check_csv_columns(result, player_df, "player_metrics.csv", PLAYER_METRICS_REQUIRED):
        result.findings.extend(check_value_ranges(player_df))
        result.findings.extend(check_confidence_labels(player_df))
        if player_df is not None:
            result.add(SEVERITY_INFO, "player_count",
                       f"player_metrics.csv has {len(player_df)} player rows",
                       count=len(player_df))

    if events_df is not None and len(events_df):
        if _check_csv_columns(result, events_df, "key_timestamps.csv", KEY_TIMESTAMPS_REQUIRED):
            result.findings.extend(check_cross_table_consistency(player_df, events_df))
            result.add(SEVERITY_INFO, "event_count",
                       f"key_timestamps.csv has {len(events_df)} events",
                       count=len(events_df))
    else:
        result.add(SEVERITY_WARN, "no_events",
                   "key_timestamps.csv is empty or unreadable")

    return result


# ---------------------------------------------------------------------------
# Gold loading
# ---------------------------------------------------------------------------

def load_gold(match_id: str, gold_root: Path) -> Optional[GoldBundle]:
    """Load gold/<match_id>/ if it exists. Returns None if no gold available."""
    gold_dir = gold_root / match_id
    manifest_path = gold_dir / "manifest.yaml"
    if not manifest_path.exists():
        return None
    with manifest_path.open("r", encoding="utf-8") as handle:
        manifest = yaml.safe_load(handle) or {}
    if int(manifest.get("schema_version", 0)) != 1:
        raise ValueError(
            f"Unsupported gold schema_version in {manifest_path}: "
            f"{manifest.get('schema_version')}"
        )
    labels = manifest.get("labels") or {}
    bundle = GoldBundle(match_id=match_id, gold_dir=gold_dir, manifest=manifest)
    if labels.get("tracks") and (gold_dir / "tracks.csv").exists():
        bundle.tracks = pd.read_csv(gold_dir / "tracks.csv")
    if labels.get("ball_points") and (gold_dir / "ball_points.csv").exists():
        bundle.ball_points = pd.read_csv(gold_dir / "ball_points.csv")
    if labels.get("events") and (gold_dir / "events.csv").exists():
        bundle.events = pd.read_csv(gold_dir / "events.csv")
    return bundle


# ---------------------------------------------------------------------------
# Layer B — accuracy metrics
# ---------------------------------------------------------------------------

DEFAULT_TOLERANCES = {
    "player_position_m": 3.0,
    "player_timestamp_sec": 0.5,
    "ball_position_m": 1.5,
    "ball_timestamp_sec": 1.0,
    "event_timestamp_sec": 5.0,
    "event_position_m": 5.0,
}


def _ratio_pct(num: int, denom: int) -> Optional[float]:
    if denom <= 0:
        return None
    return round(100.0 * num / denom, 2)


def compute_player_detection_recall(
    gold_tracks: pd.DataFrame,
    system_tracks: pd.DataFrame,
    pos_tol_m: float,
    ts_tol_sec: float,
) -> Tuple[Metric, Metric]:
    """For each gold (player_id, timestamp) row, check if the system has a
    matching track within tolerance.

    Returns (detection_recall, identity_binding_accuracy).

    detection_recall: of gold rows, how many had ANY system track for that
      player at that time within position tolerance.
    identity_binding_accuracy: of gold rows where the system has SOME track
      within position tolerance for SOMEONE, how many of those bound to the
      correct gold player_id.
    """
    if gold_tracks is None or len(gold_tracks) == 0:
        return (
            Metric("player_detection_recall", None, 0, 0, "%",
                   "gold tracks empty or missing"),
            Metric("identity_binding_accuracy", None, 0, 0, "%",
                   "gold tracks empty or missing"),
        )
    if system_tracks is None or len(system_tracks) == 0:
        return (
            Metric("player_detection_recall", 0.0, 0, len(gold_tracks), "%",
                   "system has no tracks"),
            Metric("identity_binding_accuracy", None, 0, 0, "%",
                   "system has no tracks"),
        )

    sys_ts = pd.to_numeric(system_tracks["timestamp_sec"], errors="coerce")
    sys_x = pd.to_numeric(system_tracks.get("field_x_m"), errors="coerce")
    sys_y = pd.to_numeric(system_tracks.get("field_y_m"), errors="coerce")
    sys_pid = system_tracks.get("assigned_player_id")
    if sys_pid is None:
        sys_pid = system_tracks.get("player_id")
    if sys_pid is None:
        return (
            Metric("player_detection_recall", None, 0, 0, "%",
                   "system tracks missing player_id column"),
            Metric("identity_binding_accuracy", None, 0, 0, "%",
                   "system tracks missing player_id column"),
        )
    sys_pid = sys_pid.astype(str)

    detection_hits = 0
    identity_hits = 0
    identity_denom = 0  # frames where system saw any matching position

    for _, row in gold_tracks.iterrows():
        try:
            g_ts = float(row["timestamp_sec"])
            g_pid = str(row["player_id"])
            g_x = float(row["field_x_m"])
            g_y = float(row["field_y_m"])
        except (KeyError, ValueError, TypeError):
            continue

        ts_window = (sys_ts - g_ts).abs() <= ts_tol_sec
        if not ts_window.any():
            continue

        # candidates within timestamp window
        idx = sys_ts[ts_window].index
        cand_x = sys_x.loc[idx]
        cand_y = sys_y.loc[idx]
        cand_pid = sys_pid.loc[idx]

        dist = ((cand_x - g_x).pow(2) + (cand_y - g_y).pow(2)).pow(0.5)
        within = dist <= pos_tol_m
        if not within.any():
            continue

        # detection hit: system saw someone at the right place/time
        same_pid = cand_pid[within] == g_pid
        identity_denom += 1
        if same_pid.any():
            detection_hits += 1
            identity_hits += 1
        # else: detection_hit is 0 here — identity not the right player

    n_gold = len(gold_tracks)
    detection = Metric(
        "player_detection_recall",
        _ratio_pct(detection_hits, n_gold),
        detection_hits, n_gold, "%",
        f"pos_tol={pos_tol_m}m, ts_tol={ts_tol_sec}s",
    )
    identity = Metric(
        "identity_binding_accuracy",
        _ratio_pct(identity_hits, identity_denom) if identity_denom else None,
        identity_hits, identity_denom, "%",
        "denominator = gold rows where system saw someone at the right place/time",
    )
    return detection, identity


def compute_ball_metrics(
    gold_ball: pd.DataFrame,
    system_ball: pd.DataFrame,
    pos_tol_m: float,
    ts_tol_sec: float,
) -> Tuple[Metric, Metric]:
    """Ball recall: of gold in-play ball points, how many did system match?

    Position error: median field distance between matched gold/system points.
    """
    if gold_ball is None or len(gold_ball) == 0:
        return (
            Metric("ball_recall", None, 0, 0, "%", "gold ball empty"),
            Metric("ball_position_error_m", None, 0, 0, "m", "no matched pairs"),
        )

    in_play = gold_ball[gold_ball["status"].astype(str).str.lower() == "in_play"].copy()
    if in_play.empty:
        return (
            Metric("ball_recall", None, 0, 0, "%",
                   "gold has no in_play ball points"),
            Metric("ball_position_error_m", None, 0, 0, "m",
                   "no matched pairs"),
        )

    if system_ball is None or len(system_ball) == 0:
        return (
            Metric("ball_recall", 0.0, 0, len(in_play), "%",
                   "system has no ball points"),
            Metric("ball_position_error_m", None, 0, 0, "m",
                   "no matched pairs"),
        )

    sys_in_play = system_ball.copy()
    if "ball_in_play" in sys_in_play.columns:
        sys_in_play = sys_in_play[
            sys_in_play["ball_in_play"].astype(str).str.lower().isin({"true", "1"})
        ]
    if "field_x_m" in sys_in_play.columns:
        sys_in_play = sys_in_play[sys_in_play["field_x_m"].notna()]

    sys_ts = pd.to_numeric(sys_in_play["timestamp_sec"], errors="coerce")
    sys_x = pd.to_numeric(sys_in_play.get("field_x_m"), errors="coerce")
    sys_y = pd.to_numeric(sys_in_play.get("field_y_m"), errors="coerce")

    matches = 0
    errors: List[float] = []
    for _, row in in_play.iterrows():
        try:
            g_ts = float(row["timestamp_sec"])
            g_x = float(row["field_x_m"])
            g_y = float(row["field_y_m"])
        except (KeyError, ValueError, TypeError):
            continue
        ts_window = (sys_ts - g_ts).abs() <= ts_tol_sec
        if not ts_window.any():
            continue
        idx = sys_ts[ts_window].index
        dx = sys_x.loc[idx] - g_x
        dy = sys_y.loc[idx] - g_y
        dist = (dx.pow(2) + dy.pow(2)).pow(0.5)
        if (dist <= pos_tol_m).any():
            matches += 1
            errors.append(float(dist.min()))

    recall = Metric(
        "ball_recall",
        _ratio_pct(matches, len(in_play)),
        matches, len(in_play), "%",
        f"pos_tol={pos_tol_m}m, ts_tol={ts_tol_sec}s",
    )
    pos_err = Metric(
        "ball_position_error_m",
        round(float(pd.Series(errors).median()), 2) if errors else None,
        len(errors), len(in_play), "m",
        "median nearest-system-to-gold distance among matched pairs",
    )
    return recall, pos_err


def compute_event_metrics(
    gold_events: pd.DataFrame,
    system_events: pd.DataFrame,
    ts_tol_sec: float,
    pos_tol_m: float,
) -> Dict[str, Dict[str, Metric]]:
    """Per event_type: precision and recall on a (timestamp, position) match.

    Match definition: a gold event matches a system event if they share an
    event_type and lie within ts_tol_sec and pos_tol_m. Each gold event can
    match at most one system event and vice versa (greedy nearest-first).
    """
    out: Dict[str, Dict[str, Metric]] = {}
    if gold_events is None or len(gold_events) == 0:
        return out
    if system_events is None:
        system_events = pd.DataFrame(columns=["timestamp_sec", "event_type"])

    gold_types = {str(t) for t in gold_events.get("event_type", []).dropna().unique()}
    sys_types = {str(t) for t in system_events.get("event_type", []).dropna().unique()}

    for etype in sorted(gold_types | sys_types):
        gold_e = gold_events[gold_events["event_type"].astype(str) == etype].reset_index(drop=True)
        sys_e = system_events[system_events["event_type"].astype(str) == etype].reset_index(drop=True)

        used_sys = set()
        matched = 0
        for _, g in gold_e.iterrows():
            best_idx = -1
            best_score = math.inf
            for j, s in sys_e.iterrows():
                if j in used_sys:
                    continue
                try:
                    dt = abs(float(s["timestamp_sec"]) - float(g["timestamp_sec"]))
                except (ValueError, TypeError):
                    continue
                if dt > ts_tol_sec:
                    continue
                # position is optional; if both have it, factor in
                dpos = 0.0
                if all(c in g.index for c in ("field_x_m", "field_y_m")) and \
                   all(c in s.index for c in ("field_x_m", "field_y_m")):
                    try:
                        dpos = math.hypot(
                            float(s["field_x_m"]) - float(g["field_x_m"]),
                            float(s["field_y_m"]) - float(g["field_y_m"]),
                        )
                        if dpos > pos_tol_m:
                            continue
                    except (ValueError, TypeError):
                        pass
                score = dt + dpos / 5.0  # 1m ~ 0.2s of weight
                if score < best_score:
                    best_score = score
                    best_idx = j
            if best_idx >= 0:
                used_sys.add(best_idx)
                matched += 1

        out[etype] = {
            "recall": Metric(
                "event_recall",
                _ratio_pct(matched, len(gold_e)) if len(gold_e) else None,
                matched, len(gold_e), "%",
                f"event_type={etype}",
            ),
            "precision": Metric(
                "event_precision",
                _ratio_pct(matched, len(sys_e)) if len(sys_e) else None,
                matched, len(sys_e), "%",
                f"event_type={etype}",
            ),
        }
    return out


def run_layer_b(
    match_id: str,
    gold: GoldBundle,
    system_tracks: Optional[pd.DataFrame],
    system_ball: Optional[pd.DataFrame],
    system_events: Optional[pd.DataFrame],
    tolerances: Optional[Mapping[str, float]] = None,
) -> LayerBResult:
    tols = dict(DEFAULT_TOLERANCES)
    if tolerances:
        tols.update({k: float(v) for k, v in tolerances.items()})

    result = LayerBResult(match_id=match_id, gold_window=gold.window)

    if gold.tracks is not None and system_tracks is not None:
        det, ident = compute_player_detection_recall(
            gold.tracks, system_tracks,
            pos_tol_m=tols["player_position_m"],
            ts_tol_sec=tols["player_timestamp_sec"],
        )
        result.metrics.append(det)
        result.metrics.append(ident)
    elif gold.tracks is not None:
        result.notes.append("system tracks unavailable; skipping player metrics")

    if gold.ball_points is not None:
        recall, pos_err = compute_ball_metrics(
            gold.ball_points, system_ball if system_ball is not None else pd.DataFrame(),
            pos_tol_m=tols["ball_position_m"],
            ts_tol_sec=tols["ball_timestamp_sec"],
        )
        result.metrics.append(recall)
        result.metrics.append(pos_err)

    if gold.events is not None and system_events is not None:
        result.per_event_metrics = compute_event_metrics(
            gold.events, system_events,
            ts_tol_sec=tols["event_timestamp_sec"],
            pos_tol_m=tols["event_position_m"],
        )
    elif gold.events is not None:
        result.notes.append("system events unavailable; skipping event metrics")

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe_read_csv(path: Path) -> Optional[pd.DataFrame]:
    if not path.exists():
        return None
    try:
        return pd.read_csv(path)
    except Exception:
        return None
