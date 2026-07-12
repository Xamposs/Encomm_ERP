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


class CustomersView(BaseView):
    """Customer registry: search, add, delete, export."""

    def __init__(self, parent, db_service, config: dict, **kwargs):
        kwargs.setdefault('fg_color', 'transparent')
        super().__init__(parent, db_service, config, **kwargs)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # ── Search Bar ──
        self.cust_search_bar = ctk.CTkFrame(self, fg_color="transparent")
        self.cust_search_bar.grid(row=0, column=0, sticky="ew", pady=(0, 15))
        self.cust_search_bar.grid_columnconfigure(0, weight=1)

        self.cust_search_entry = ctk.CTkEntry(
            self.cust_search_bar, placeholder_text="🔍 Αναζήτηση πελάτη (όνομα, ΑΜΚΑ, τηλέφωνο)...")
        self.cust_search_entry.grid(row=0, column=0, padx=(0, 10), sticky="ew")
        self.cust_search_entry.bind("<KeyRelease>", lambda e: self._on_search_changed())

        self.cust_refresh_btn = ctk.CTkButton(
            self.cust_search_bar, text="🔄 Ανανέωση", width=100,
            fg_color=("gray80", "gray30"), hover_color=("gray70", "gray40"),
            command=self.refresh)
        self.cust_refresh_btn.grid(row=0, column=1)

        # ── Form ──
        self.cust_form_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.cust_form_frame.grid(row=1, column=0, sticky="ew", pady=(0, 15))
        self.cust_form_frame.grid_columnconfigure((1, 3, 5), weight=1)

        ctk.CTkLabel(self.cust_form_frame, text="Όνομα:",
            font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, padx=(0, 5), sticky="w")
        self.cust_name_entry = ctk.CTkEntry(self.cust_form_frame, width=220)
        self.cust_name_entry.grid(row=0, column=1, padx=(0, 10), sticky="ew")

        ctk.CTkLabel(self.cust_form_frame, text="ΑΜΚΑ:",
            font=ctk.CTkFont(weight="bold")).grid(row=0, column=2, padx=(0, 5), sticky="w")
        self.cust_amka_entry = ctk.CTkEntry(self.cust_form_frame, width=150)
        self.cust_amka_entry.grid(row=0, column=3, padx=(0, 10), sticky="ew")

        ctk.CTkLabel(self.cust_form_frame, text="Τηλέφωνο:",
            font=ctk.CTkFont(weight="bold")).grid(row=0, column=4, padx=(0, 5), sticky="w")
        self.cust_phone_entry = ctk.CTkEntry(self.cust_form_frame, width=150)
        self.cust_phone_entry.grid(row=0, column=5, padx=(0, 10), sticky="ew")

        self.cust_save_btn = ctk.CTkButton(
            self.cust_form_frame, text="💾 Αποθήκευση",
            fg_color=("#2ecc71", "#27ae60"), hover_color=("#27ae60", "#1e8449"),
            text_color=("#FFFFFF", "#FFFFFF"), font=ctk.CTkFont(weight="bold"),
            command=self.save_customer)
        self.cust_save_btn.grid(row=0, column=6, padx=(0, 5))

        self.cust_delete_btn = ctk.CTkButton(
            self.cust_form_frame, text="❌ Διαγραφή",
            fg_color=("#E74C3C", "#C0392B"), hover_color=("#C0392B", "#A93226"),
            text_color=("#FFFFFF", "#FFFFFF"), font=ctk.CTkFont(weight="bold"),
            command=self.delete_customer)
        self.cust_delete_btn.grid(row=0, column=7)

        # ── Treeview ──
        self.cust_table_container = ctk.CTkFrame(self)
        self.cust_table_container.grid(row=2, column=0, sticky="nsew")
        self.cust_table_container.grid_columnconfigure(0, weight=1)
        self.cust_table_container.grid_rowconfigure(0, weight=1)

        self.cust_scrollbar = ttk.Scrollbar(self.cust_table_container, orient="vertical")
        self.cust_scrollbar.grid(row=0, column=1, sticky="ns")

        self.cust_tree = ttk.Treeview(
            self.cust_table_container,
            columns=("id", "name", "amka", "phone"),
            show="headings", height=15,
            yscrollcommand=self.cust_scrollbar.set, selectmode="browse")
        self.cust_tree.grid(row=0, column=0, sticky="nsew")
        self.cust_scrollbar.config(command=self.cust_tree.yview)

        self.cust_tree.heading("id", text="ID")
        self.cust_tree.column("id", width=50, anchor="center")
        self.cust_tree.heading("name", text="Όνομα")
        self.cust_tree.column("name", width=300, anchor="w")
        self.cust_tree.heading("amka", text="ΑΜΚΑ")
        self.cust_tree.column("amka", width=150, anchor="w")
        self.cust_tree.heading("phone", text="Τηλέφωνο")
        self.cust_tree.column("phone", width=150, anchor="w")
        self.cust_tree.configure(style="Treeview")

        self.cust_tree.bind("<Double-1>", self._on_tree_select)

        # ── Export Bar ──
        self.cust_export_bar = ctk.CTkFrame(self, fg_color="transparent")
        self.cust_export_bar.grid(row=3, column=0, sticky="ew", pady=(15, 0))
        self.cust_export_filter = ctk.CTkEntry(
            self.cust_export_bar, width=160, placeholder_text="Φίλτρο (π.χ. Γεώργιος)")
        self.cust_export_filter.pack(side="left", padx=(0, 8))
        self.cust_export_format = ctk.CTkOptionMenu(
            self.cust_export_bar, values=["Excel (.csv)", "PDF (.txt style)"], width=140)
        self.cust_export_format.pack(side="left", padx=(0, 8))
        self.cust_export_btn = ctk.CTkButton(
            self.cust_export_bar, text="📤 Εξαγωγή",
            fg_color="#2980B9", hover_color="#1F618D",
            font=ctk.CTkFont(weight="bold"), command=self.export_customers)
        self.cust_export_btn.pack(side="left")

        self._search_timer = None

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------
    def _on_search_changed(self):
        if not self.winfo_ismapped():
            return
        if self._search_timer is not None:
            self.after_cancel(self._search_timer)
        self._search_timer = self.after(300, self.refresh)

    def _on_tree_select(self, event):
        sel = self.cust_tree.selection()
        if not sel:
            return
        values = self.cust_tree.item(sel[0])["values"]
        self.cust_name_entry.delete(0, "end")
        self.cust_name_entry.insert(0, values[1])
        self.cust_amka_entry.delete(0, "end")
        self.cust_amka_entry.insert(0, values[2])
        self.cust_phone_entry.delete(0, "end")
        self.cust_phone_entry.insert(0, values[3])
        self._selected_customer_id = values[0]

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    def save_customer(self):
        name = self.cust_name_entry.get().strip()
        amka = self.cust_amka_entry.get().strip()
        phone = self.cust_phone_entry.get().strip()
        if not name:
            messagebox.showwarning("Προειδοποίηση", "Το όνομα είναι υποχρεωτικό.")
            return
        if self.db_service.add_customer(name, amka, phone):
            self.cust_name_entry.delete(0, "end")
            self.cust_amka_entry.delete(0, "end")
            self.cust_phone_entry.delete(0, "end")
            self._selected_customer_id = None
            self.refresh()
            messagebox.showinfo("Επιτυχία", f"Ο πελάτης '{name}' αποθηκεύτηκε.")
        else:
            messagebox.showerror("Σφάλμα", "Αποτυχία αποθήκευσης (πιθανή διπλότυπη ΑΜΚΑ).")

    def delete_customer(self):
        sel = self.cust_tree.selection()
        if not sel:
            messagebox.showwarning("Προειδοποίηση", "Παρακαλώ επιλέξτε έναν πελάτη από τη λίστα.")
            return
        customer_id = self.cust_tree.item(sel[0])["values"][0]
        name = self.cust_tree.item(sel[0])["values"][1]
        if not messagebox.askyesno("Επιβεβαίωση Διαγραφής",
            f"Είστε βέβαιοι ότι θέλετε να διαγράψετε τον πελάτη '{name}';", icon="warning"):
            return
        if self.db_service.delete_customer(int(customer_id)):
            self.cust_name_entry.delete(0, "end")
            self.cust_amka_entry.delete(0, "end")
            self.cust_phone_entry.delete(0, "end")
            self._selected_customer_id = None
            self.refresh()
            messagebox.showinfo("Επιτυχία", f"Ο πελάτης '{name}' διαγράφηκε επιτυχώς.")

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def export_customers(self):
        filter_text = self.cust_export_filter.get().strip().lower()
        fmt = self.cust_export_format.get()
        is_csv = "csv" in fmt.lower()

        rows = self.db_service.get_all_customers()
        if filter_text:
            rows = [r for r in rows if
                    filter_text in r["name"].lower() or
                    filter_text in r.get("amka", "").lower() or
                    filter_text in r.get("phone", "").lower()]
        data = [[r["id"], r["name"], r.get("amka", ""), r.get("phone", "")]
                for r in rows]
        self._run_export(
            "Customers_Export",
            ["ID", "Όνομα", "ΑΜΚΑ", "Τηλέφωνο"],
            data, is_csv,
            txt_title="ΜΗΤΡΩΟ ΠΕΛΑΤΩΝ",
            txt_row_fmt="• {1:<30} | ΑΜΚΑ: {2:<15} | Τηλ: {3}",
        )

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        if not self.winfo_ismapped():
            return
        query = self.cust_search_entry.get().strip() if hasattr(self, 'cust_search_entry') else ""

        def bg_fetch():
            try:
                rows = self.db_service.search_customers(query) if query else self.db_service.get_all_customers()
                self.after(0, self._safe_update_ui, rows)
            except Exception:
                logging.exception("Customers fetch failed")
                self.after(0, lambda: self._safe_update_ui([]))

        threading.Thread(target=bg_fetch, daemon=True).start()

    def _safe_update_ui(self, rows: List[Dict]):
        if not hasattr(self, 'cust_tree') or self.cust_tree is None:
            return
        self.cust_tree.delete(*self.cust_tree.get_children())
        for r in rows:
            self.cust_tree.insert("", "end", values=(
                r["id"], r["name"], r.get("amka", ""), r.get("phone", "")))
