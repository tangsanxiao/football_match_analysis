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
  00_..15_*.py             Pipeline steps, numbered in execution order
  analysis_app_*.py        Library modules reused by 12_serve_analysis_app.py
  analysis_detection_filters.py / analysis_metrics.py / analysis_report_runs.py
  create_point_picker.py / serve_point_picker.py    Calibration UI
  analysis_app_workspace.html       Frontend (vanilla HTML/JS, ~1.7K lines)
tests/test_analysis_core.py         Only test file (unittest)
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
- Match config example → `configs/match_red_mvp.yaml`
- Calibration points example → `configs/calibration_points_red_mvp.yaml`
