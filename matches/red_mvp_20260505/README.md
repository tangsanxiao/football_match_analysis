# Match Project

This folder is an isolated football match analysis project.

## Main Files

- Config: `matches/red_mvp_20260505/config/match.yaml`
- Calibration points: `matches/red_mvp_20260505/config/calibration_points.yaml`
- Review folder: `matches/red_mvp_20260505/review`
- Interim data: `matches/red_mvp_20260505/data/interim`
- Reports: `matches/red_mvp_20260505/reports`

## Next Commands

```bash
.venv/bin/python scripts/09_prepare_match_review.py --config matches/red_mvp_20260505/config/match.yaml --step calibration
```

After marking calibration points:

```bash
.venv/bin/python scripts/09_prepare_match_review.py --config matches/red_mvp_20260505/config/match.yaml --step recognition --device mps
```
