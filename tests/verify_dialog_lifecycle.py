"""Dialog lifecycle assertions — run standalone, not through pytest (Qt crash)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtWidgets import QApplication
from qt_app.dialogs.product_import_preview_dialog import (
    ProductImportPreviewDialog, _InspectResult,
)
app = QApplication.instance() or QApplication(sys.argv)

d = ProductImportPreviewDialog()
failures = 0

def check(label, cond):
    global failures
    if not cond:
        print(f"FAIL: {label}", file=sys.stderr)
        failures += 1

# 1. Busy lifecycle
check("idle default", not d.is_busy())
d._thread = object()
check("busy with ref", d.is_busy())
d._start_worker(object(), lambda x: None)
check("refuses when busy", d._worker is None)
d._thread = None
check("idle after clear", not d.is_busy())

# 2. Stale reject — token
d._inspect_token = 5
d._current_file_path = "/f"
result = _InspectResult(ok=True, token=3, file_path="/f", sheets=(), headers=())
count_before = d._sheet_combo.count()
d._on_inspect_done(result)
check("stale token not applied", d._sheet_combo.count() == count_before)

# 3. Stale reject — wrong path
d._inspect_token = 7
d._current_file_path = "/real"
d._sheet_combo.clear(); d._sheet_combo.addItems(["Real"])
result = _InspectResult(ok=True, token=7, file_path="/other", sheets=("Bad",), headers=())
d._on_inspect_done(result)
check("stale path not applied", d._sheet_combo.itemText(0) == "Real")

# 4. hide_results clears rows
d._sample_table.setRowCount(3)
d._error_table.setRowCount(5)
d._hide_results()
check("sample cleared", d._sample_table.rowCount() == 0)
check("errors cleared", d._error_table.rowCount() == 0)

# 5. Preview button restored
d._file_path = "f"
d._sheet_combo.addItem("S1"); d._sheet_combo.setCurrentIndex(0)
d._closing = False
d._on_thread_done()
check("preview btn enabled", d._preview_btn.isEnabled())
check("cancel hidden", not d._cancel_btn.isVisible())

if failures:
    print(f"\n{failures} assertion(s) FAILED", file=sys.stderr)
    sys.exit(1)
print("ALL DIALOG ASSERTIONS PASSED")
