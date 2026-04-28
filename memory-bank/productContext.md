# Product context

## Problem

Irrigation designers need a single tool to draw fields and pipe layouts, run credible hydraulics (laterals, submains, trunk/schedule), visualize relief and maps, and export for field/office use.

## Users and workflow

- Typical flow: define blocks → direction of rows / auto laterals → submain → run hydraulics → review results, maps, and BOM.
- Map tab: project zone, tiles, layers, drawing modes synced with canvas; trunk trace on a dedicated sub-tab (`map_left_draw_widgets.py` — Drawing / Trunk notebook). Trunk pickets are explicit: route clicks can define polyline geometry without creating bend nodes, and pickets can be inserted from pressure graphs.
- **Block tab:** heavy emitter-flow / mask / isoline visualization is routed to the tab panel (not the main canvas) to avoid UI freezes on large fields.
- Separate tools menu launches standalone calculators (`lateral_field_calculator.py`, `submain_telescope_calculator.py`) in subprocesses.

## UX principles (high level)

- Maximize workspace; collapsible panels; RMB closes contours / ends drafts; snap toggle; hydraulic audit feedback (laterals, emitters, trunk pressure/velocity).
- Windows: prefer silent custom dialogs (`silent_messagebox`) instead of system message boxes (no beep).
- Progress UI merges frequent updates so the bar does not stutter on large projects.

## Locale

- UI, tooltips, and reports: **Ukrainian**.

## Documentation anchors (maintenance)

- Canonical technical snapshots: [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md) §7 and session notes in [PROJECT_STATE.md](../PROJECT_STATE.md). Last full doc alignment recorded: **2026-04-27** (user-requested sync with memory-bank).
