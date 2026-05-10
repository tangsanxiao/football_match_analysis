# Evaluation harness — gold segments

Status: **bootstrap, schema v1.**

This directory is the project's "evaluation底座". It lets every code change
that touches detection, tracking, identity binding, or report generation be
**measured**, not guessed. Without it, "改进了检测" is faith-based.

The harness has two layers:

| Layer | What it checks | Needs gold? | Runs when |
|---|---|---|---|
| **A · Sanity** | Files exist, schemas match, value ranges plausible, output stable across runs | No | Any finalized match |
| **B · Accuracy** | Per-player detection recall, identity binding accuracy, ball-point miss rate, event precision/recall | Yes (per match) | When `evals/gold/<match_id>/` exists |

Run the harness with:

```bash
python scripts/17_eval_against_gold.py --match <match_id>             # Layer A only
python scripts/17_eval_against_gold.py --match <match_id> --gold auto # Layer B if gold exists
python scripts/17_eval_against_gold.py --match A --match B            # aggregate over multiple matches
```

---

## Why the system, not the data, is the deliverable

Each match's footage is different (lighting, opponent, field). One golden
truth dataset cannot certify "the model is good for all future matches". So
this harness treats gold segments as **examples**, not **certificates**:

- Layer A applies to **any match** out of the box.
- Layer B can run against **partial gold** — even 30 seconds is useful.
- You build a library of gold segments over time. Each new model version is
  evaluated against **all** segments to detect regressions and assess
  generalization.

> **Investment per new match: ~15 minutes** to label 30s of gold lets the harness give you Layer B numbers for that match. You don't need to label entire matches.

---

## Adding a new gold segment

1. Pick a slice that matters (typically: a 30-second to 5-minute window with active play, multiple players, ball involvement).
2. Create the directory:

   ```
   evals/gold/<match_id>/
   ├── manifest.yaml       # schema below
   ├── tracks.csv          # optional, see schema
   ├── ball_points.csv     # optional, see schema
   └── events.csv          # optional, see schema
   ```

3. Fill in `manifest.yaml` declaring **which** labels are present (you don't have to provide all three).
4. Use the calibrated field coordinates from the match's project (open the workspace, click positions, read out the field-coords). Future iterations will provide a dedicated UI; for now manual entry is fine.
5. Run:

   ```bash
   python scripts/17_eval_against_gold.py --match <match_id> --gold auto
   ```

   The eval report goes to `evals/runs/<eval_id>/`.

See `gold_schema_v1.yaml` for the exact column / field contract.

---

## What Layer A actually checks

For each match input:

- **Smoke**: report directory exists, contains `report.md`, `report.html`, and the standard CSVs.
- **Schema**: `player_metrics.csv` has all expected columns. `key_timestamps.csv` columns intact. `confidence` values from the legal set.
- **Value ranges**:
  - `observed_coverage_pct ∈ [0, 100]`
  - `distance_per_min ∈ [0, 500]` (m/min — humans don't sustain >500)
  - `rating ∈ [0, 10]`
  - `observed_seconds > 0` for every player row
- **Cross-table consistency**: every `key_timestamps.csv` `player_id` appears in `player_metrics.csv`.

Failures are categorized **fail** (must fix) or **warn** (suspicious, look at it). The harness exits non-zero on any **fail**.

---

## What Layer B computes

Given gold + system output for the same match, the harness reports:

| Metric | What it measures | Interpretation |
|---|---|---|
| `player_detection_recall` | % of gold (player, frame) pairs the system saw | Higher = better player tracking density |
| `identity_binding_accuracy` | Of gold detections, % bound to the right player_id | Higher = identity step is reliable |
| `ball_recall` | % of gold in-play ball points the system also has | Higher = fewer "ball not seen" gaps |
| `ball_position_error_m` | Median field-distance between system and gold ball points | Lower = positioning accurate |
| `event_recall_by_type` | Per event_type: % of gold events the system flagged | Per metric quality view |
| `event_precision_by_type` | Per event_type: % of system events that match gold | Catches false positives |

Match tolerances (e.g. "system point within 1.5m and 1s of gold counts as a hit") live in `gold_schema_v1.yaml` under `match_tolerances`. Override per-run via CLI flags if needed.

### `--ball-source` matters for honesty

Ball-related metrics need a careful choice of system output:

- **`--ball-source raw`** (default) — pre-human-review YOLO ball detections from `data/interim/detections/<segment>/tracks.csv` filtered to `class_name == "sports ball"`. **This is the meaningful comparison**: it measures the model alone, before manual correction.
- **`--ball-source reviewed`** — post-human-review consolidated points from `matches/<id>/review/ball_review/ball_review_points.csv`. **Circular** if the gold was extracted from this same file (recall will appear 100%); use only to measure things like "did human review add new positive points" once the project tracks that.

---

## Current baseline (2026-05-10)

First real gold landed: `evals/gold/中青赛_1_20260506_213657/` (window 78–138s, 30 in-play ball points).

Layer B with default tolerances and `--ball-source raw`:

| Metric | Value | Note |
|---|---:|---|
| `ball_recall` | **0.0%** (0/30) | YOLO11n produces 103 ball detections in this window, all clustered at one static field-mark (~2.7m, ~9.0m). None match the actual ball. |
| `ball_position_error_m` | — | No matches → no error to compute. |

Interpretation: the project's manual ball-review workflow is empirically **the only thing** producing usable ball positions on this footage. Even loose tolerances (5m / 3s) yield 0/30 — YOLO11n's ball head is not viable for 5-a-side amateur footage as-is.

Improvements that should move this number up:
- Switch to YOLO11s/m (more capacity for small-object detection).
- Apply `analysis_detection_filters.filter_static_ball_false_positives` before the gold comparison (currently the `raw` path doesn't filter; consider adding `--ball-source filtered`).
- Train a small-object specialized ball detector and ensemble.
- Add optical-flow ball interpolation between detections.

Each of those is now testable: run the eval before, change one thing, run again. The number is the merge gate.

---

## Layout

```
evals/
├── README.md                # this file
├── gold_schema_v1.yaml      # schema contract for gold/<match>/ contents
├── gold/
│   └── <match_id>/          # one directory per labeled match (tracked)
│       ├── manifest.yaml
│       ├── tracks.csv
│       ├── ball_points.csv
│       └── events.csv
└── runs/                    # generated eval reports (gitignored — runs/ rule)
    └── <eval_id>/
        ├── report.md
        ├── report.html
        ├── layer_a.yaml
        └── layer_b.yaml     # only present when gold was used
```

---

## Operational rule

Whenever you change anything in the detection / tracking / report pipeline,
run:

```bash
python scripts/17_eval_against_gold.py --match <every-match-with-gold>
```

before merging. If Layer A fails, fix it. If Layer B regresses on any match
without an explanation in the commit message, don't merge.

This is the discipline that turns "I think this is better" into "here are
the numbers".
