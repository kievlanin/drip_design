# FREEZE NOTES

## Freeze snapshot

- Date: 2026-04-28
- Branch: `master`
- Commit: `823e485`
- Tag: `freeze-2026-04-28`
- Remote: `origin` ([kievlanin/drip_design](https://github.com/kievlanin/drip_design))

## What is frozen in this snapshot

- Shared shapely-free helper for allowed pipes:
  - `modules/hydraulic_module/allowed_pipes_common.py`
  - `normalize_allowed_pipes_map_common`
  - `pn_sort_tuple_common`
  - `allowed_pipe_candidates_sorted_common`
- Duplication removal:
  - `modules/hydraulic_module/hydraulics_core.py` now reuses shared allowed-pipes helpers
  - `modules/hydraulic_module/trunk_irrigation_schedule_hydro.py` now reuses shared allowed-pipes helpers
- Project docs and memory sync included in freeze:
  - `PROJECT_CONTEXT.md`
  - `PROJECT_STATE.md`
  - `memory-bank/activeContext.md`
  - `memory-bank/productContext.md`
  - `memory-bank/progress.md`
  - `memory-bank/projectbrief.md`
  - `memory-bank/systemPatterns.md`
- Current UI/trunk code state included at freeze point:
  - `main_app/ui/dripcad_legacy.py`
  - `main_app/ui/control_panel_impl.py`
  - `main_app/ui/map_viewer_tk_window.py`

## Restore instructions

### Checkout frozen state

```bash
git fetch --tags
git checkout freeze-2026-04-28
```

### Return to current development branch

```bash
git checkout master
git pull
```

## Notes

- Local `.env` is intentionally **not** part of this freeze and remains local-only.
- Freeze commit message:
  - `chore(release): freeze project snapshot 2026-04-28`
