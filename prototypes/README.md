# PySide6 Resize Smoothness Prototype

Validates whether **Qt (PySide6)** provides smooth window-resize on Windows
with widget density comparable to the production CustomTkinter ERP.

## Quick start

```powershell
# Install
pip install -r prototypes/requirements-qt-prototype.txt

# Run
python prototypes/qt_resize_prototype.py
```

## What it simulates

| Component | Widget count | Notes |
|-----------|:-----------:|-------|
| Sidebar | 9 nav buttons + brand + version | Fixed 220 px, matches production layout |
| Dashboard page | 4 stat cards + 8-row table | Dense Row 1 |
| Inventory page | search bar + 2 combos + 14-row table + paginator | Closest to real ERP |
| POS page | 6-row cart table + 15 product buttons | Two-panel layout |
| Settings page | 7 form controls in a scrollable group | Form density |

Total: ~90–110 widgets visible at any time, comparable to the real app.

## What to evaluate

1. **Visibility** — while dragging any edge or corner for 10+ seconds, all
   content must remain visible. No white/blank panels, no flickering, no
   delayed content restoration.

2. **Smoothness** — the FPS counter in the status bar (bottom-right) should
   stay ≥40 fps during continuous resize.

3. **Responsiveness after resize** — after releasing the mouse, switching
   between pages (sidebar buttons) should feel instant. The clock and FPS
   counter continue updating.

## Metrics collected

- **FPS** (rolling 60-frame window, sampled at display refresh rate)
- **Minimum FPS** ever observed (floor)
- **Maximum inter-frame gap** (ms) — spikes in resize-event processing
- **Resize event count** — total number of resize events received

## Acceptance criteria

| Metric | Pass |
|--------|:----:|
| Content visible during resize | ✅ Must |
| No white flash or blank panels | ✅ Must |
| FPS stays ≥40 during resize | ✅ Must |
| FPS stays ≥50 at rest | ✅ Must |
| Page switching works after resize | ✅ Must |

If all criteria pass, Qt/PySide6 is a viable alternative for the
presentation layer. The next step would be a partial migration plan
(starting with one view at a time).

## Notes

- This prototype is **read-only** — it loads no real database and writes
  nothing to disk.
- It does not touch any existing ENCOMM ERP source file (`main.py`,
  `core/`, `infrastructure/`, `presentation/`, `tests/`).
- Remove the prototype directory (`prototypes/`) once the technology
  decision is made — it has no ongoing value.
