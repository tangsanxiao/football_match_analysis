# CLAUDE.md

This file is the project context for Claude Code (and any other AI agent that needs to
land in this repo cold). Keep it concise — every line is loaded into every session.

For UX/UI decisions, **`design.md` at the repo root is the source of truth**. This file
covers code structure, commands, and conventions only.

---

## Product

Local-first analysis workbench for **5-a-side amateur football matches** filmed from a
single fixed camera. One match video → player ratings, movement/tactical review, key
timestamps, low-to-medium confidence technical candidates (pass / shot / 1v1 / pressure /
space creation) → HTML + Markdown + CSV report.

Core principles (per `design.md`):
- Trust over automation — manual confirmation is acceptable when it raises confidence.
- Honest uncertainty — every metric is labelled High / Medium / Low / Candidate.
- One match = one project with one visible path: setup → calibration → recognition →
  human review → report → rerun.

UI copy is **Chinese** by default. Code, identifiers, file paths, and commit messages
are **English**.

---

## Run commands

Python 3.9 venv lives at `.venv/`. Activate or call `.venv/bin/python` directly.

```bash
# Full MVP pipeline for a configured match (00 → 07, via subprocess fan-out)
.venv/bin/python scripts/10_run_match_mvp.py --config configs/match_red_mvp.yaml

# Workspace web app (stdlib http.server — NOT Flask). Serves the workspace UI.
.venv/bin/python scripts/12_serve_analysis_app.py            # default port

# Per-step pipeline (run individually for debugging)
.venv/bin/python scripts/00_probe_video.py    --config configs/match_red_mvp.yaml
.venv/bin/python scripts/02_calibrate_field.py --config configs/match_red_mvp.yaml
# ... etc, scripts are numbered in pipeline order

# Tests (unittest, single file)
.venv/bin/python -m unittest tests.test_analysis_core
```

Detection requires `yolo11n.pt` (5.6 MB) at the repo root. It is **gitignored**
(`*.pt`); ultralytics will download it on first run, or copy it manually.

---

## Repo layout

```
configs/                   YAML for matches and calibration points
data/                      Generated interim outputs (gitignored)
docs/football-video-analysis-mvp/   Process notes per work phase
matches/<match_id>/        Per-match isolated project (config tracked, media gitignored)
reports/                   Pilot report outputs (gitignored)
scripts/
  00_..17_*.py             Pipeline steps, numbered in execution order
  analysis_app_*.py        Library modules reused by 12_serve_analysis_app.py
  analysis_detection_filters.py / analysis_metrics.py / analysis_report_runs.py
  analysis_eval.py         Pure logic for the Layer A / Layer B eval harness
  create_point_picker.py / serve_point_picker.py    Calibration UI
  analysis_app_workspace.html       Frontend (vanilla HTML/JS, ~1.7K lines)
reports/combined/<slug>/<ts>/      Combined reports from 16_merge_match_segments
evals/                              Evaluation harness — see "Eval harness" below
tests/test_analysis_core.py         Test suite (unittest)
videos/raw/                Source footage (gitignored)
design.md                  UX/UI source of truth — read before any UI change
```

Anything inside `stitch_pro_match_analytics/` is **gitignored UI/PRD experiments**,
not part of the live codebase. Don't import from it. Don't put new work there.

---

## How the pipeline fits together

1. `00_probe_video` → `01_sample_frames` — get video metadata + sample frames.
2. `02_calibrate_field` — turn pixel-coordinate calibration points into a homography.
   The picker UI lives in `create_point_picker.py` + `serve_point_picker.py`.
3. `03_detect_track` — YOLO11n detection + ByteTrack on segments defined in match YAML.
4. `04_summarize_tracklets` → `05_classify_tracklets` → `06_assign_identities` —
   build per-track summaries, classify by team color, bind to roster.
5. `07_generate_report` — combine identity + ball annotations into the HTML/MD/CSV
   report. Confidence labels are derived per `analysis_metrics.py`.
6. `10_run_match_mvp` runs 00–07 end to end via `subprocess`.
7. `08_new_match_project` / `11_verify_match_project` scaffold and validate per-match
   project directories under `matches/`.
8. `12_serve_analysis_app` is the workspace web server. It uses
   `analysis_app_state.py` for the normalized match-state model, `analysis_app_jobs.py`
   to spawn pipeline jobs as subprocesses, and `analysis_app_config.py` for shared
   config/IO helpers.
9. `13_prepare_human_review` / `14_finalize_human_review` / `15_prepare_ball_review`
   produce the human-review packages and finalize after manual annotations.
10. `16_merge_match_segments` aggregates finalized reports from N match projects
    into one combined report (use case: upper-half + lower-half of one game). See
    "Multi-segment matches" below.
11. `17_eval_against_gold` runs the eval harness (Layer A sanity + optional
    Layer B accuracy vs `evals/gold/<match_id>/`). See "Eval harness" below.

---

## Eval harness (Layer A / Layer B)

`evals/` is the project's measurement底座. Every detection / tracking /
report-pipeline change should be evaluated against it before merging.

```bash
# Layer A only (sanity / schema / range checks). Always works.
python scripts/17_eval_against_gold.py --match <match_id>

# Layer A + Layer B (accuracy metrics) when evals/gold/<match_id>/ exists
python scripts/17_eval_against_gold.py --match <match_id> --gold auto

# Multi-match aggregation (regression suite)
python scripts/17_eval_against_gold.py --match m1 --match m2 --output baseline_v1

# Override match tolerances (e.g. tighter player-position window)
python scripts/17_eval_against_gold.py --match <m> --tolerances player_position_m=2.0
```

Output: `evals/runs/<eval_id>/{report.md, report.html, layer_a.yaml, layer_b.yaml, manifest.yaml}`.

**Layer A** (no gold needed) — runs on any finalized match:
- Smoke: report.md/html + standard CSVs exist.
- Schema: required columns present in `player_metrics.csv` / `key_timestamps.csv`.
- Value ranges: `observed_coverage_pct ∈ [0,100]`, `rating ∈ [0,10]`,
  `distance_per_min ∈ [0,500]`.
- Confidence labels are from the legal set (see `analysis_eval.LEGAL_CONFIDENCE`).
- Cross-table: every event's `player_id` appears in `player_metrics.csv`.

The harness exits non-zero on any **fail**.

**Layer B** (requires `evals/gold/<match_id>/manifest.yaml` per `evals/gold_schema_v1.yaml`):
- `player_detection_recall` — % of gold (player, timestamp) rows the system captured (within `player_position_m` / `player_timestamp_sec`).
- `identity_binding_accuracy` — among gold detections the system saw, % bound to the correct `player_id`.
- `ball_recall` + `ball_position_error_m` — based on `matches/<id>/review/ball_review/ball_review_points.csv`.
- Per-event-type recall + precision (greedy nearest-first match within `event_timestamp_sec` / `event_position_m`).

Tolerances default per `evals/gold_schema_v1.yaml#match_tolerances`. The
algorithms are locked by `TestEvalLayerA` and `TestEvalLayerB`.

**`--ball-source`** chooses what to measure as "system output":
- `raw` — YOLO ball detections, no filter, no human (pure model)
- `filtered` *(default)* — raw + `filter_static_ball_false_positives` (production pipeline before human review)
- `reviewed` — post-human (circular if your gold was extracted from there)

First baseline (2026-05-10) on `中青赛_1_20260506_213657` window 78–138s:
- **YOLO11n: ball_recall = 0/30 (0%)** at default tolerances; both raw and filtered.
- **YOLO11s: ball_recall = 0/30 (0%)** at default; **0/30 even at 10m tolerance**.
  3× more detections than 11n (87.6% vs 42.8% frame coverage) but they
  cluster at sidelines / advertising boards, never on the actual ball.
- Diagnosis: the COCO `sports ball` class is mismatched to 5-a-side
  amateur footage (~5–10 px white blobs). **Bigger COCO-trained YOLO
  ≠ better ball detection** — domain-specific training or specialized
  small-object detector is required. Manual ball review remains the
  only viable ball pipeline today. See `evals/README.md` "Current
  baseline" + "YOLO11s A/B" for full numbers and the still-on-the-table
  directions.

To add a new gold segment, see `evals/README.md`. Investment per match is
~15 minutes (label 30s of footage); the harness will produce partial Layer B
metrics from partial gold.

---

## Multi-segment matches (e.g. 上半场 + 下半场)

A real 5-a-side game is typically two ~20-min halves. **Treat each half as its
own match project, then merge.** Reasons:

- Trackers (ByteTrack / BoT-SORT) reset IDs across the half-time gap anyway —
  pretending it's one continuous video creates fake cross-half tracklets.
- Re-running just one half is cheap; re-running a 40-min concat is not.
- Calibration may differ (camera bumped between halves). Two projects force you
  to handle that explicitly.

Workflow:

```bash
# 1. Scaffold one match project per half
python scripts/08_new_match_project.py --match-id 中青赛_2026_05_10_h1 ...
python scripts/08_new_match_project.py --match-id 中青赛_2026_05_10_h2 ...

# 2. Run the full pipeline on each half independently
python scripts/10_run_match_mvp.py --config matches/中青赛_2026_05_10_h1/config/match.yaml
python scripts/10_run_match_mvp.py --config matches/中青赛_2026_05_10_h2/config/match.yaml
# (calibration / identity binding / ball review can be reused if camera & roster unchanged)

# 3. Merge into one combined report
python scripts/16_merge_match_segments.py \
    --match 中青赛_2026_05_10_h1 --label 上半场 \
    --match 中青赛_2026_05_10_h2 --label 下半场 \
    --name "中青赛 2026-05-10" \
    --output 中青赛_2026_05_10
# → reports/combined/中青赛_2026_05_10/<timestamp>/{report.md, report.html, *.csv, manifest.yaml}
```

Single-segment use is also supported (`--match X` once = produces a normalized
combined-style output for one match). The aggregation rules are:

- `observed_frames` / `observed_seconds` / `distance_m` / `high_speed_distance_m`
  → **sum**
- `*_pct` and rate columns → **weighted average by `observed_frames`**
- `distance_per_min` → **recomputed** from total distance ÷ total observed time
- `key_timestamps.csv` → concatenated with each segment's timestamps offset by
  the cumulative duration of preceding segments; gains a `segment` column
- `tracks_red_labeled.csv` → same offset + segment column
- `confidence` → most conservative across segments

The aggregation logic is locked by tests in `TestMergeMatchSegments`
(`tests/test_analysis_core.py`).

---

## Conventions

- **Numbered scripts are pipeline steps**. Anything reusable goes in
  `analysis_app_*` / `analysis_*` modules. New shared helpers should go there too,
  not as another numbered script.
- **Match state is centralized**. `analysis_app_state.py` builds a single normalized
  match summary; UI and jobs derive status from it. Don't duplicate status logic in
  individual handlers — extend the state model instead. (See `design.md` "Data And
  Status Model".)
- **Confidence labels are productized**. The valid levels are `high / medium / low /
  candidate / sample insufficient`. Don't invent new ones in copy. See
  `analysis_metrics.py`.
- **Internal job names are not user-facing**. Map `prepare_calibration` /
  `prepare_human_review` / `prepare_ball_review` / `final_report` to Chinese labels
  per `design.md` "Job Status Banner".
- **Imports**: numbered scripts and `12_serve_analysis_app.py` use a
  `try / except ModuleNotFoundError` shim to import either as `analysis_app_*` (when
  `scripts/` is on `sys.path`) or as `scripts.analysis_app_*`. Keep the shim if you
  add another script that imports these modules.

---

## Active work — current refactor

The team is mid-refactor toward the workspace model in `design.md`. Recent commits
(annotation zoom, ball review, archived annotations, frame-time sort) are all part of
this. Phase plan:

1. Information architecture — replace 4 tabs with `New Match` + `History` + `Match
   Workspace`.
2. Status model — single `/api/match_state` powering stepper + task cards + next action.
3. Review task center — task cards instead of raw lists.
4. Report workspace — embed report step inside the match workspace.
5. Visual polish.

Before changing the workspace UI, read `design.md` "Implementation Guidance For Current
Codebase" and "Design Checklist For Future AI Agents".

---

## Things to avoid

- Don't commit `*.pt` model files, raw videos, generated `reports/`, or
  `data/interim/` — all gitignored for a reason.
- Don't put new code in `stitch_pro_match_analytics/`. It's archived UI experiments.
- Don't create another top-level navigation tab — favour a workspace step.
- Don't expose internal filenames (`match.yaml`, `tracks_red_labeled.csv`,
  `review_manifest.yaml`) in user-facing copy.
- Don't write to `~/.claude/` for project state. Project-level snapshots, if needed,
  belong in `./AI_CONTEXT.md` (handled by the `project-context-anchor` skill on demand).

---

## Known gaps (good first issues)

- Single test file — no end-to-end pipeline smoke test.
- `load_yaml` / `write_yaml` / `resolve_path` are duplicated across several scripts.
- `scripts/` mixes pipeline steps and library modules; a dedicated package would let us
  drop the `try/except` import shim and the test's `sys.path` hack.
- `stitch_pro_match_analytics/football_match_analysis_prd.md` and
  `Product Requirements Document (PRD) Football Match Analysis Workspace.md` are
  byte-identical duplicates.
- No bootstrap script for first-run dependency + model download.

---

## Pointers

- UX truth → `design.md`
- Process notes per phase → `docs/football-video-analysis-mvp/`
- **Eval harness + YOLO ball baseline (2026-05-10)** →
  `docs/football-video-analysis-mvp/08-eval-harness-and-yolo-ball-baseline.md`
  Read this before touching detection / ball-related code; it records what's
  been ruled out (YOLO11n→11s upgrade, static-filter tuning) and the next
  recommended path (custom-train ball head on existing labeled points).
- Match config example → `configs/match_red_mvp.yaml`
- Calibration points example → `configs/calibration_points_red_mvp.yaml`
