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

One single scrollable **Dense ERP Dashboard** packing ~90–110 widgets across
four labelled regions. Every widget is visible simultaneously — the scrollbar
handles windows too small to show everything.

| Region | Widgets |
|--------|---------|
| 📊 Στατιστικά (Dashboard) | 4 stat cards + 8-row activity table |
| 📦 Αποθήκη (Inventory) | search bar + 2 combos + add button + 14-row table + paginator |
| 🧾 Ταμείο / Πωλήσεις (POS) | 6-row cart table + 15 product buttons in a grid |
| ⚙️ Ρυθμίσεις (Settings) | 7 form controls (spinboxes, combo, checkbox, password field) |

The sidebar displays all 9 Greek navigation buttons (display-only, not
functional — they exist only as widget-density contributors to the resize
test, not as a working navigation system).

## What to evaluate

1. **Visibility** — while dragging any edge or corner for 10+ seconds, all
   content must remain visible. No white/blank panels, no flickering, no
   delayed content restoration.

2. **Event-loop responsiveness** — the UI pulse-rate counter in the status bar
   (bottom-right) should stay ≥50 Hz during continuous resize.

3. **After resize** — after releasing the mouse, scrolling the dense dashboard
   should feel smooth. The pulse-rate counter continues updating.

## Metrics collected

- **UI pulse rate** (Hz) — rolling 60-sample window from a 16 ms QTimer.
  This is NOT rendered-frame FPS. It measures *event-loop responsiveness*:
  how regularly Qt dispatches a high-frequency timer while the window is
  being resized.
- **Minimum pulse rate** ever observed (floor)
- **Maximum inter-tick gap** (ms) — worst-case event-loop stall
- **Resize event count** — total number of resize events received

## Acceptance criteria

| Metric | Pass |
|--------|:----:|
| Content visible during resize | ✅ Must |
| No white flash or blank panels | ✅ Must |
| UI pulse rate ≥50 Hz during resize | ✅ Must |
| UI pulse rate ≥55 Hz at rest | ✅ Must |
| Dense dashboard scrollable after resize | ✅ Must |

If all criteria pass, Qt/PySide6 is a viable alternative for the
presentation layer. The next step would be a partial migration plan
(starting with one view at a time).

## Notes

- This prototype is **read-only** — it loads no real database and writes
  nothing to disk.
- It does not touch any existing ENCOMM ERP source file (`main.py`,
  `core/`, `infrastructure/`, `presentation/`, `tests/`).
- Sidebar navigation buttons are display-only widget density; they do not
  switch views.
- Remove the prototype directory (`prototypes/`) once the technology
  decision is made — it has no ongoing value.
