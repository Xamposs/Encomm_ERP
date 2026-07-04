import customtkinter as ctk
import os
import time
import logging
import threading
from tkinter import messagebox
from tkinter import ttk
from datetime import datetime
from typing import Dict, List
from .base_view import BaseView


class DashboardView(BaseView):
    """Dashboard with stat cards, critical alerts Treeview, import action, and export."""

    def __init__(self, parent, db_service, config: dict, **kwargs):
        kwargs.setdefault('fg_color', 'transparent')
        super().__init__(parent, db_service, config, **kwargs)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=1)

        # ── 1. STAT CARDS ROW ──
        self.stats_row = ctk.CTkFrame(self, fg_color="transparent")
        self.stats_row.grid(row=0, column=0, sticky="ew", pady=(0, 25))
        self.stats_row.grid_columnconfigure((0, 1, 2), weight=1, uniform="equal")

        # Card 1: Total Products
        self.card_total = ctk.CTkFrame(self.stats_row, border_width=1, border_color=BaseView._stat_border_default())
        self.card_total.grid(row=0, column=0, padx=(0, 15), pady=5, sticky="nsew")
        self.card_total_title = ctk.CTkLabel(self.card_total, text="Συνολικά Προϊόντα",
            font=ctk.CTkFont(size=12, weight="bold"), text_color=BaseView._card_title_text())
        self.card_total_title.pack(anchor="w", padx=15, pady=(15, 2))
        self.card_total_val = ctk.CTkLabel(self.card_total, text="0", font=ctk.CTkFont(size=32, weight="bold"))
        self.card_total_val.pack(anchor="w", padx=15, pady=(0, 15))

        # Card 2: Low Stock Alerts
        self.card_low_stock = ctk.CTkFrame(self.stats_row, border_width=2, border_color="#FF9500")
        self.card_low_stock.grid(row=0, column=1, padx=15, pady=5, sticky="nsew")
        self.card_low_stock_title = ctk.CTkLabel(self.card_low_stock, text="Ελλείψεις Στοκ",
            font=ctk.CTkFont(size=12, weight="bold"), text_color=BaseView._card_title_text())
        self.card_low_stock_title.pack(anchor="w", padx=15, pady=(15, 2))
        self.card_low_stock_val = ctk.CTkLabel(self.card_low_stock, text="0",
            font=ctk.CTkFont(size=32, weight="bold"), text_color="#FF9500")
        self.card_low_stock_val.pack(anchor="w", padx=15, pady=(0, 15))

        # Card 3: Expiry Alerts
        self.card_expiry = ctk.CTkFrame(self.stats_row, border_width=2, border_color="#FF3B30")
        self.card_expiry.grid(row=0, column=2, padx=(15, 0), pady=5, sticky="nsew")
        self.card_expiry_title = ctk.CTkLabel(self.card_expiry, text="Κοντά στη Λήξη / Ληγμένα",
            font=ctk.CTkFont(size=12, weight="bold"), text_color=BaseView._card_title_text())
        self.card_expiry_title.pack(anchor="w", padx=15, pady=(15, 2))
        self.card_expiry_val = ctk.CTkLabel(self.card_expiry, text="0",
            font=ctk.CTkFont(size=32, weight="bold"), text_color="#FF3B30")
        self.card_expiry_val.pack(anchor="w", padx=15, pady=(0, 15))

        # ── Analytics row 2: revenue, VAT, invoice count ──
        self.analytics_row2 = ctk.CTkFrame(self, fg_color="transparent")
        self.analytics_row2.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 5))

        self.card_revenue = ctk.CTkFrame(self.analytics_row2)
        self.card_revenue.pack(side="left", fill="both", expand=True, padx=(0, 8))
        self.card_revenue_title = ctk.CTkLabel(self.card_revenue, text="Έσοδα Σήμερα",
            font=ctk.CTkFont(size=11), text_color=BaseView._card_title_text())
        self.card_revenue_title.pack(anchor="w", padx=15, pady=(10, 2))
        self.card_revenue_val = ctk.CTkLabel(self.card_revenue, text="€0.00",
            font=ctk.CTkFont(size=22, weight="bold"), text_color="#34C759")
        self.card_revenue_val.pack(anchor="w", padx=15, pady=(0, 10))

        self.card_vat = ctk.CTkFrame(self.analytics_row2)
        self.card_vat.pack(side="left", fill="both", expand=True, padx=4)
        self.card_vat_title = ctk.CTkLabel(self.card_vat, text="ΦΠΑ Σήμερα",
            font=ctk.CTkFont(size=11), text_color=BaseView._card_title_text())
        self.card_vat_title.pack(anchor="w", padx=15, pady=(10, 2))
        self.card_vat_val = ctk.CTkLabel(self.card_vat, text="€0.00",
            font=ctk.CTkFont(size=22, weight="bold"), text_color="#FF9500")
        self.card_vat_val.pack(anchor="w", padx=15, pady=(0, 10))

        self.card_inv_count = ctk.CTkFrame(self.analytics_row2)
        self.card_inv_count.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self.card_inv_count_title = ctk.CTkLabel(self.card_inv_count, text="Παραστατικά",
            font=ctk.CTkFont(size=11), text_color=BaseView._card_title_text())
        self.card_inv_count_title.pack(anchor="w", padx=15, pady=(10, 2))
        self.card_invoice_count_val = ctk.CTkLabel(self.card_inv_count, text="0",
            font=ctk.CTkFont(size=22, weight="bold"))
        self.card_invoice_count_val.pack(anchor="w", padx=15, pady=(0, 10))

        # ── 2. ALERTS SCROLLABLE TABLE & EXCEL IMPORT ──
        self.lower_row = ctk.CTkFrame(self, fg_color="transparent")
        self.lower_row.grid(row=1, column=0, sticky="nsew")
        self.lower_row.grid_columnconfigure(0, weight=3)
        self.lower_row.grid_columnconfigure(1, weight=1)
        self.lower_row.grid_rowconfigure(0, weight=1)

        self.alert_container = ctk.CTkFrame(self.lower_row)
        self.alert_container.grid(row=0, column=0, sticky="nsew", padx=(0, 20), pady=5)
        self.alert_container.grid_columnconfigure(0, weight=1)
        self.alert_container.grid_rowconfigure(2, weight=1)

        self.alert_lbl = ctk.CTkLabel(
            self.alert_container,
            text="⚠️ Κρίσιμα Προϊόντα (Χαμηλό Στοκ ή Κοντά στη Λήξη)",
            font=ctk.CTkFont(size=14, weight="bold"))
        self.alert_lbl.grid(row=0, column=0, padx=15, pady=10, sticky="w")

        self.alert_scrollbar = ttk.Scrollbar(self.alert_container, orient="vertical")
        self.alert_scrollbar.grid(row=2, column=1, sticky="ns", padx=(0, 15), pady=(0, 15))

        self.alert_tree = ttk.Treeview(
            self.alert_container,
            columns=("name", "stock", "expiry", "reason"),
            show="headings", height=12,
            yscrollcommand=self.alert_scrollbar.set, selectmode="browse")
        self.alert_tree.grid(row=2, column=0, sticky="nsew", padx=(15, 0), pady=(0, 15))
        self.alert_scrollbar.config(command=self.alert_tree.yview)

        self.alert_tree.heading("name", text="Όνομα Προϊόντος")
        self.alert_tree.heading("stock", text="Στοκ")
        self.alert_tree.heading("expiry", text="Ημ. Λήξης")
        self.alert_tree.heading("reason", text="Αιτία Προειδοποίησης")
        self.alert_tree.column("name", width=250, anchor="w")
        self.alert_tree.column("stock", width=80, anchor="e")
        self.alert_tree.column("expiry", width=120, anchor="e")
        self.alert_tree.column("reason", width=200, anchor="w")

        self.alert_tree.tag_configure("expired", foreground="#FF3B30")
        self.alert_tree.tag_configure("near_expiry", foreground="#FF9500")
        self.alert_tree.tag_configure("low_stock", foreground="#FF9500")
        self.alert_tree.configure(style="Treeview")

        self.actions_panel = ctk.CTkFrame(self.lower_row)
        self.actions_panel.grid(row=0, column=1, sticky="nsew", pady=5)

        self.act_title = ctk.CTkLabel(self.actions_panel, text="Ενέργειες Προμηθευτή",
            font=ctk.CTkFont(size=14, weight="bold"))
        self.act_title.pack(padx=20, pady=(20, 10), anchor="w")

        self.import_btn = ctk.CTkButton(
            self.actions_panel, text="📥  Εισαγωγή Excel Προμηθευτή",
            font=ctk.CTkFont(weight="bold", size=13),
            fg_color="#10B981", hover_color="#059669",
            height=40)
        self.import_btn.pack(padx=20, pady=(15, 15), fill="x")
        # command set externally by MainWindow

        self.quick_desc = ctk.CTkLabel(
            self.actions_panel,
            text="Αυτόματη ανάλυση τιμολογίων προμηθευτών\nκαι συγχρονισμός επιπέδων στοκ.\nΥποστηριζόμενες μορφές: .xlsx, .csv",
            font=ctk.CTkFont(size=11),
            text_color=BaseView._subtle_text(),
            justify="left", wraplength=180)
        self.quick_desc.pack(padx=20, pady=10, anchor="w")

        # ── Smart Export control bar ──
        self.dash_export_bar = ctk.CTkFrame(self, fg_color="transparent")
        self.dash_export_bar.grid(row=2, column=0, sticky="ew", pady=(20, 0))
        self.dash_export_filter = ctk.CTkEntry(self.dash_export_bar, width=160, placeholder_text="Φίλτρο (π.χ. DEPON)")
        self.dash_export_filter.pack(side="left", padx=(0, 8))
        self.dash_export_limit = ctk.CTkEntry(self.dash_export_bar, width=100, placeholder_text="Ποσότητα (π.χ. 20 ή ALL)")
        self.dash_export_limit.pack(side="left", padx=(0, 8))
        self.dash_export_start = ctk.CTkEntry(self.dash_export_bar, width=110, placeholder_text="Από (YYYY-MM-DD)")
        self.dash_export_start.pack(side="left", padx=(0, 5))
        self.dash_export_end = ctk.CTkEntry(self.dash_export_bar, width=110, placeholder_text="Έως (YYYY-MM-DD)")
        self.dash_export_end.pack(side="left", padx=(0, 8))
        self.dash_export_format = ctk.CTkOptionMenu(self.dash_export_bar, values=["PDF (.txt style)", "Excel (.csv)"], width=140)
        self.dash_export_format.pack(side="left", padx=(0, 8))
        self.dash_export_btn = ctk.CTkButton(self.dash_export_bar, text="📤 Εξαγωγή",
            fg_color="#2980B9", hover_color="#1F618D",
            font=ctk.CTkFont(weight="bold"), command=self.export_dashboard)
        self.dash_export_btn.pack(side="left")

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def export_dashboard(self):
        filter_text = self.dash_export_filter.get().strip().lower()
        limit_str = self.dash_export_limit.get().strip().upper()
        start_date = self.dash_export_start.get().strip() if hasattr(self, 'dash_export_start') else ""
        end_date = self.dash_export_end.get().strip() if hasattr(self, 'dash_export_end') else ""
        fmt = self.dash_export_format.get()
        is_csv = "csv" in fmt.lower()

        def _write():
            try:
                rows = []
                for child in self.alert_tree.get_children():
                    vals = self.alert_tree.item(child)["values"]
                    rows.append({"name": str(vals[0]), "stock": str(vals[1]),
                                 "expiry": str(vals[2]), "reason": str(vals[3])})
                if filter_text:
                    rows = [r for r in rows if filter_text in r["name"].lower()]
                if start_date:
                    rows = [r for r in rows if r["expiry"] >= start_date]
                if end_date:
                    rows = [r for r in rows if r["expiry"] <= end_date]
                try:
                    limit = int(limit_str)
                    rows = rows[:limit]
                except ValueError:
                    pass
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                if is_csv:
                    dest = os.path.join(os.path.expanduser("~"), "Desktop", f"Dashboard_Export_{ts}.csv")
                    lines = ["Όνομα,Στοκ,Ημ.Λήξης,Αιτία"]
                    for r in rows:
                        lines.append(f'{BaseView._csv_cell(r["name"])},{r["stock"]},{BaseView._csv_cell(r["expiry"])},{BaseView._csv_cell(r["reason"])}')
                    with open(dest, "w", encoding="utf-8-sig") as f:
                        f.write("\n".join(lines))
                else:
                    dest = os.path.join(os.path.expanduser("~"), "Desktop", f"Dashboard_Export_{ts}.txt")
                    lines = ["=" * 50, "  ENCOMM DASHBOARD — ΚΡΙΣΙΜΑ ΠΡΟΪΟΝΤΑ", "=" * 50,
                             f"Ημ/νία: {datetime.now().strftime('%d/%m/%Y %H:%M')}",
                             f"Εύρος ημ/νιών: {start_date or '—'} έως {end_date or '—'}", "-" * 50]
                    for r in rows:
                        lines.append(f"{r['name']:<30} | Στοκ: {r['stock']:<6} | Λήξη: {r['expiry']:<12} | {r['reason']}")
                    lines.append("=" * 50)
                    with open(dest, "w", encoding="utf-8") as f:
                        f.write("\n".join(lines))
                self.after(0, lambda: messagebox.showinfo("Επιτυχής Εξαγωγή",
                    "Το φιλτραρισμένο αρχείο βάσει ημερομηνιών αποθηκεύτηκε στην Επιφάνεια Εργασίας!"))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Σφάλμα Εξαγωγής", str(e)))
        threading.Thread(target=_write, daemon=True).start()

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        threshold = int(self.config.get("low_stock_threshold", 10))
        alert_days = int(self.config.get("expiry_alert_days", 30))

        def bg_fetch():
            start_time = time.time()
            try:
                counts = self.db_service.get_dashboard_counts(threshold, alert_days)
                critical_items = self.db_service.get_critical_products_sliced(threshold, alert_days, limit=20)
                analytics = self.db_service.get_dashboard_analytics()
                duration_ms = int((time.time() - start_time) * 1000)
                logging.info(f"Dashboard DB fetch (threaded) in {duration_ms}ms | {len(critical_items)} alerts")
            except Exception:
                logging.exception("Dashboard background fetch failed")
                counts = {"total": 0, "low_stock": 0, "expiry": 0}
                critical_items = []
                analytics = {"revenue_today": 0, "vat_today": 0, "total_revenue": 0, "invoice_count": 0, "top_products": []}
            self.after(0, self._safe_update_ui, counts, critical_items, analytics)

        threading.Thread(target=bg_fetch, daemon=True).start()

    def _safe_update_ui(self, counts: Dict[str, int], critical_items: List, analytics: Dict = None):
        if not hasattr(self, 'alert_tree') or self.alert_tree is None:
            return
        self.card_total_val.configure(text=str(counts["total"]))
        self.card_low_stock_val.configure(text=str(counts["low_stock"]))
        self.card_expiry_val.configure(text=str(counts["expiry"]))

        if analytics and hasattr(self, 'card_revenue_val'):
            self.card_revenue_val.configure(text=f"€{analytics.get('revenue_today', 0):.2f}")
            self.card_vat_val.configure(text=f"€{analytics.get('vat_today', 0):.2f}")
            self.card_invoice_count_val.configure(text=str(analytics.get('invoice_count', 0)))

        self.alert_tree.delete(*self.alert_tree.get_children())
        for p, reason in critical_items:
            if "Ληγμένο" in reason:
                tag = "expired"
            elif "Λήγει" in reason:
                tag = "near_expiry"
            else:
                tag = "low_stock"
            self.alert_tree.insert("", "end", values=(p.name, f"{p.stock} τεμ.", p.expiry_date, reason), tags=(tag,))
