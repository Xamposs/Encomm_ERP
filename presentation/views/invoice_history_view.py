import customtkinter as ctk
import tkinter as tk
import os
import logging
import threading
from datetime import datetime
from tkinter import messagebox
from tkinter import ttk
from typing import Dict, List, Optional
from .base_view import BaseView


class InvoiceHistoryView(BaseView):
    """Invoice history: search by ID/date range, view details, export."""

    def __init__(self, parent, db_service, config: dict, **kwargs):
        kwargs.setdefault('fg_color', 'transparent')
        super().__init__(parent, db_service, config, **kwargs)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # ── Filter Bar ──
        self.hist_filter_bar = ctk.CTkFrame(self, fg_color="transparent")
        self.hist_filter_bar.grid(row=0, column=0, sticky="ew", pady=(0, 15))
        self.hist_filter_bar.grid_columnconfigure(5, weight=1)

        ctk.CTkLabel(self.hist_filter_bar, text="Αρ. Παραστατικού:",
            font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, padx=(0, 5))
        self.hist_id_entry = ctk.CTkEntry(self.hist_filter_bar, width=150)
        self.hist_id_entry.grid(row=0, column=1, padx=(0, 15))

        ctk.CTkLabel(self.hist_filter_bar, text="Από:",
            font=ctk.CTkFont(weight="bold")).grid(row=0, column=2, padx=(0, 5))
        self.hist_start_entry = ctk.CTkEntry(
            self.hist_filter_bar, width=120, placeholder_text="YYYY-MM-DD")
        self.hist_start_entry.grid(row=0, column=3, padx=(0, 15))

        ctk.CTkLabel(self.hist_filter_bar, text="Έως:",
            font=ctk.CTkFont(weight="bold")).grid(row=0, column=4, padx=(0, 5))
        self.hist_end_entry = ctk.CTkEntry(
            self.hist_filter_bar, width=120, placeholder_text="YYYY-MM-DD")
        self.hist_end_entry.grid(row=0, column=5, padx=(0, 10))

        self.hist_search_btn = ctk.CTkButton(
            self.hist_filter_bar, text="🔍 Αναζήτηση",
            fg_color=("#2ecc71", "#27ae60"), hover_color=("#27ae60", "#1e8449"),
            command=self.refresh)
        self.hist_search_btn.grid(row=0, column=6)

        # ── Treeview ──
        self.hist_table_container = ctk.CTkFrame(self)
        self.hist_table_container.grid(row=1, column=0, sticky="nsew")
        self.hist_table_container.grid_columnconfigure(0, weight=1)
        self.hist_table_container.grid_rowconfigure(0, weight=1)

        self.hist_scrollbar = ttk.Scrollbar(self.hist_table_container, orient="vertical")
        self.hist_scrollbar.grid(row=0, column=1, sticky="ns")

        self.hist_tree = ttk.Treeview(
            self.hist_table_container,
            columns=("id", "date", "subtotal", "vat", "total", "customer"),
            show="headings", height=20,
            yscrollcommand=self.hist_scrollbar.set, selectmode="browse")
        self.hist_tree.grid(row=0, column=0, sticky="nsew")
        self.hist_scrollbar.config(command=self.hist_tree.yview)

        for col, text, w, a in [
            ("id", "Αρ. Παραστατικού", 160, "w"),
            ("date", "Ημερομηνία", 160, "w"),
            ("subtotal", "Υποσύνολο", 100, "e"),
            ("vat", "ΦΠΑ", 80, "e"),
            ("total", "Σύνολο", 100, "e"),
            ("customer", "Πελάτης", 160, "w"),
        ]:
            self.hist_tree.heading(col, text=text)
            self.hist_tree.column(col, width=w, anchor=a)

        self.hist_tree.configure(style="Treeview")
        self.hist_tree.bind("<Double-1>", self._on_invoice_double_click)

        # ── Export Bar ──
        self.hist_export_bar = ctk.CTkFrame(self, fg_color="transparent")
        self.hist_export_bar.grid(row=2, column=0, sticky="ew", pady=(15, 0))

        self.hist_export_start = ctk.CTkEntry(
            self.hist_export_bar, width=120, placeholder_text="Από (YYYY-MM-DD)")
        self.hist_export_start.pack(side="left", padx=(0, 5))
        self.hist_export_end = ctk.CTkEntry(
            self.hist_export_bar, width=120, placeholder_text="Έως (YYYY-MM-DD)")
        self.hist_export_end.pack(side="left", padx=(0, 8))
        self.hist_export_format = ctk.CTkOptionMenu(
            self.hist_export_bar, values=["Excel (.csv)", "PDF (.txt style)"], width=140)
        self.hist_export_format.pack(side="left", padx=(0, 8))
        self.hist_export_btn = ctk.CTkButton(
            self.hist_export_bar, text="📤 Εξαγωγή",
            fg_color="#2980B9", hover_color="#1F618D",
            font=ctk.CTkFont(weight="bold"), command=self.export_invoice_history)
        self.hist_export_btn.pack(side="left")

    # ------------------------------------------------------------------
    # Invoice Detail Popup
    # ------------------------------------------------------------------
    def _on_invoice_double_click(self, event):
        sel = self.hist_tree.selection()
        if not sel:
            return
        inv_id = self.hist_tree.item(sel[0])["values"][0]

        def bg_fetch():
            try:
                items = self.db_service.get_invoice_items(inv_id)
                self.after(0, self._show_invoice_detail_popup, inv_id, items)
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Σφάλμα", str(e)))

        threading.Thread(target=bg_fetch, daemon=True).start()

    def _show_invoice_detail_popup(self, inv_id: str, items: List[Dict]):
        popup = ctk.CTkToplevel(self)
        popup.title(f"Λεπτομέρειες {inv_id}")
        popup.geometry("600x400")
        popup.resizable(False, False)
        popup.transient(self.master)
        popup.grab_set()

        header = ctk.CTkLabel(
            popup, text=f"🧾 Παραστατικό: {inv_id}",
            font=ctk.CTkFont(size=15, weight="bold"))
        header.pack(padx=20, pady=(15, 10), anchor="w")

        tree_frame = ctk.CTkFrame(popup)
        tree_frame.pack(padx=20, pady=(0, 10), fill="both", expand=True)
        tree_frame.grid_columnconfigure(0, weight=1)
        tree_frame.grid_rowconfigure(0, weight=1)

        tree = ttk.Treeview(
            tree_frame,
            columns=("barcode", "name", "qty", "price", "row_total"),
            show="headings", height=12)
        tree.grid(row=0, column=0, sticky="nsew")

        tree.heading("barcode", text="Barcode")
        tree.heading("name", text="Όνομα")
        tree.heading("qty", text="Ποσ.")
        tree.heading("price", text="Τιμή")
        tree.heading("row_total", text="Σύνολο")
        tree.column("barcode", width=100, anchor="w")
        tree.column("name", width=200, anchor="w")
        tree.column("qty", width=60, anchor="e")
        tree.column("price", width=80, anchor="e")
        tree.column("row_total", width=80, anchor="e")

        total = 0.0
        for it in items:
            row_total = it["quantity"] * it["price"]
            total += row_total
            tree.insert("", "end", values=(
                it.get("barcode", ""), it.get("name", ""),
                it["quantity"], f"€{it['price']:.2f}", f"€{row_total:.2f}"))

        ctk.CTkLabel(
            popup, text=f"Γενικό Σύνολο: €{total:.2f}",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color="#34C759").pack(pady=(0, 10))

        ctk.CTkButton(
            popup, text="Κλείσιμο",
            fg_color=("gray80", "gray30"), hover_color=("gray70", "gray40"),
            command=popup.destroy).pack(pady=(0, 15))

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def export_invoice_history(self):
        fmt = self.hist_export_format.get()
        is_csv = "csv" in fmt.lower()
        start_date = self.hist_export_start.get().strip() or None
        end_date = self.hist_export_end.get().strip() or None

        rows = self.db_service.get_all_invoices(
            search_id="", start_date=start_date, end_date=end_date)
        data = [
            [r["id"], r["date"], f'{r["subtotal"]:.2f}', f'{r["vat"]:.2f}',
             f'{r["total"]:.2f}', r.get("customer_name", "")]
            for r in rows
        ]
        self._run_export(
            "InvoiceHistory",
            ["Αρ.Παραστατικού", "Ημερομηνία", "Υποσύνολο", "ΦΠΑ", "Σύνολο", "Πελάτης"],
            data, is_csv,
            txt_title="ΙΣΤΟΡΙΚΟ ΠΑΡΑΣΤΑΤΙΚΩΝ",
            txt_row_fmt="📋 {0}  |  {1}  |  Υποσ: €{2}  |  ΦΠΑ: €{3}  |  Σύνολο: €{4}  |  {5}",
        )

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        if not self.winfo_ismapped():
            return
        search_id = self.hist_id_entry.get().strip() if hasattr(self, 'hist_id_entry') else ""
        start_date = self.hist_start_entry.get().strip() or None
        end_date = self.hist_end_entry.get().strip() or None

        def bg_fetch():
            try:
                rows = self.db_service.get_all_invoices(
                    search_id=search_id, start_date=start_date, end_date=end_date)
                self.after(0, self._safe_update_ui, rows)
            except Exception:
                logging.exception("Invoice history fetch failed")
                self.after(0, lambda: self._safe_update_ui([]))

        threading.Thread(target=bg_fetch, daemon=True).start()

    def _safe_update_ui(self, rows: List[Dict]):
        if not hasattr(self, 'hist_tree') or self.hist_tree is None:
            return
        self.hist_tree.delete(*self.hist_tree.get_children())
        for r in rows:
            self.hist_tree.insert("", "end", values=(
                r["id"], r["date"],
                f"€{r['subtotal']:.2f}", f"€{r['vat']:.2f}",
                f"€{r['total']:.2f}", r.get("customer_name", "")))
