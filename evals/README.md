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

- **`--ball-source raw`** — pre-filter, pre-human YOLO detections from `data/interim/detections/<segment>/tracks.csv` filtered to `class_name == "sports ball"`. Measures **the model alone**.
- **`--ball-source filtered`** *(default)* — raw detections passed through `filter_static_ball_false_positives`. Measures **what the production pipeline actually offers the user** before human review (this is what `03_detect_track` and `07_generate_report` use internally).
- **`--ball-source reviewed`** — post-human-review file. **Circular** if your gold was extracted from this same file; use only when measuring "human contribution on top of the model".

---

## Current baseline (2026-05-10)

First real gold: `evals/gold/中青赛_1_20260506_213657/` (window 78–138s, 30 in-play ball points; data extracted from this match's existing human-labeled ball-review file, not AI-fabricated).

Layer B at default tolerances (1.5m / 1s):

| `--ball-source` | `ball_recall` | What survived | Interpretation |
|---|---:|---|---|
| `raw` | **0/30 (0%)** | 103 system "ball" detections, all on a single static field-mark at (~2.7m, ~9.0m) | YOLO is hallucinating one location consistently |
| `filtered` | **0/30 (0%)** | 66 "ball" detections, all clustered at sidelines (y ≈ 22m, outside the 20m-wide field) and marked `inside_play_area=False` | The static-filter correctly removed the 3 worst false-positive groups; remaining noise is off-field and ignored by the eval anyway |
| `reviewed` | 100% (circular — don't use against this gold) | — | — |

Even at loose tolerances (5m / 3s), `raw` and `filtered` both stay at 0/30.

**Diagnosis** — three findings, each measurable:

1. **The bottleneck is the model, not the filter.** `filter_static_ball_false_positives` is doing its job (removed 63 rows in 3 static groups), but the surviving detections are *still* false positives.
2. **The manual ball-review workflow is empirically the only viable ball pipeline today.**
3. **Model size alone does not help** — see the YOLO11s A/B below.

### ball_v1 fine-tune (2026-05-10) — **first non-zero result**

Trained a single-class YOLO from `yolo11n.pt` on 67 positive + 29 negative
frames extracted from this match's existing `ball_review_points.csv`. See
`docs/football-video-analysis-mvp/09-custom-ball-head-v1.md` for the full
experiment.

| Model | Conf | Detections (window) | `ball_recall@1.5m` | `recall@5m` | `recall@10m` | Pos err (m) |
|---|---:|---:|---:|---:|---:|---:|
| YOLO11n COCO | 0.18 | 92 / 66 filtered | 0/30 (0%) | 0/30 | 0/30 | — |
| YOLO11s COCO | 0.18 | 268 / 176 filtered | 0/30 (0%) | 0/30 | 0/30 | — |
| ball_v1 (fine-tuned) | 0.18 | 72 / 61 in_play | 13/30 (43.3%) | 24/30 (80.0%) | 25/30 (83.3%) | 0.98 |
| **ball_v1 (recommended)** | **0.05** | **379 / 365 in_play** | **25/30 (83.3%)** | — | — | **0.86** |

ball_v1 detections cluster at x∈[25.5, 35.6], y∈[0.7, 16.2] — gold lives at
x∈[26.4, 38.6], y∈[-0.4, 8.8]. **Spatially almost overlapping.** This is
the first model checkpoint that detects balls inside the play area in the
right region.

The next experiments to run (each with a new `ab_<label>` eval):

- `ball_v2`: add a second match's ball_review_points to training
- `ball_v1_low_conf`: same weights, inference at `conf=0.05` to trade precision for recall
- `ball_v1_with_persons`: dual-model inference (ball_v1 + yolo11n person) merged

### YOLO11s A/B (2026-05-10)

Same gold, same window, same params (`conf=0.18`, `imgsz=1280`, `sample-fps=2.0`, ByteTrack). Re-ran detection on the 60–160s window with both 11n and 11s into separate detection dirs (`segment_0060_0100_2fps_yolo11n` / `_yolo11s`) and ran the eval with `--detections-dir`.

| Model | Ball detections in window | Frame coverage | Tracks | `ball_recall` (default tol) | `recall@10m` |
|---|---:|---:|---:|---:|---:|
| YOLO11n (2.6M params) | 92 raw → 66 filtered | 42.8% | 3 | 0/30 (0%) | 0/30 (0%) |
| **YOLO11s** (9.4M params) | **268 raw** → 176 filtered | **87.6%** | **7** | **0/30 (0%)** | **0/30 (0%)** |

**11s detects ~3× more "balls" but none of them are the actual ball.** The 176 surviving 11s detections all cluster at (3m, 21m), (6m, 24m), (9m, 21m) — sideline / advertising-board / spectator regions. The actual ball is at x∈[26, 38], y∈[-0.4, 8.8]. Nearest 11s detection to any gold point: 25.8m. Even at 10m tolerance: still 0/30.

**Verdict: bigger COCO-trained YOLO is not the path.** Both nano and small models are detecting non-ball objects (round/white things at the field perimeter) and missing the actual ball. The COCO `sports ball` class was trained on close-up footage of clear balls, not 5–10 pixel white blobs in 5-a-side amateur footage. **Custom training on this domain or a specialized small-object detector is required.**

### Improvement directions still on the table

Now ruled out by data:
- ❌ Upgrade YOLO11n → YOLO11s (this PR's experiment)

Still untested but worth measuring:
- Upgrade to YOLO11m / 11l (probably same outcome — fundamental class-mismatch issue, but cheap to verify)
- **Custom-train a YOLO ball head** on the 84 marked human ball points already in `matches/中青赛_1_20260506_213657/review/ball_review/ball_review_points.csv`. Each labeled frame_idx + position can be back-projected into a YOLO bbox. ~84 positives is small but the class is well-defined.
- Add aggressive `inside_play_area` cropping at the IMAGE level (run YOLO only on the field rectangle, not the whole 4K frame).
- Lower ball-class `conf` to 0.05 AND require the detection to lie within the play polygon. Currently `conf=0.18` global; ball-class survives the first cut but gets dumped by the play-area filter.
- Optical-flow ball interpolation between sparse detections (only useful if at least *some* detections are in the right place, which today's data says they are not).

The number is the merge gate: any "I improved ball detection" PR must show this metric move.

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
