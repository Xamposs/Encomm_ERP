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


class SuppliersView(BaseView):
    """Supplier registry: search, add, delete, automated order lists, export."""

    def __init__(self, parent, db_service, config: dict, **kwargs):
        kwargs.setdefault('fg_color', 'transparent')
        super().__init__(parent, db_service, config, **kwargs)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # ── Search Bar ──
        self.supp_search_bar = ctk.CTkFrame(self, fg_color="transparent")
        self.supp_search_bar.grid(row=0, column=0, sticky="ew", pady=(0, 15))
        self.supp_search_bar.grid_columnconfigure(0, weight=1)

        self.supp_search_entry = ctk.CTkEntry(
            self.supp_search_bar, placeholder_text="🔍 Αναζήτηση προμηθευτή...")
        self.supp_search_entry.grid(row=0, column=0, padx=(0, 10), sticky="ew")
        self.supp_search_entry.bind("<KeyRelease>", lambda e: self.refresh())

        self.supp_refresh_btn = ctk.CTkButton(
            self.supp_search_bar, text="🔄 Ανανέωση", width=100,
            fg_color=("gray80", "gray30"), hover_color=("gray70", "gray40"),
            command=self.refresh)
        self.supp_refresh_btn.grid(row=0, column=1)

        # ── Form ──
        self.supp_form_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.supp_form_frame.grid(row=1, column=0, sticky="ew", pady=(0, 15))

        ctk.CTkLabel(self.supp_form_frame, text="Όνομα:",
            font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, padx=(0, 3), sticky="w")
        self.supp_name_entry = ctk.CTkEntry(self.supp_form_frame, width=180)
        self.supp_name_entry.grid(row=0, column=1, padx=3)

        ctk.CTkLabel(self.supp_form_frame, text="Τηλ:",
            font=ctk.CTkFont(weight="bold")).grid(row=0, column=2, padx=3, sticky="w")
        self.supp_phone_entry = ctk.CTkEntry(self.supp_form_frame, width=140)
        self.supp_phone_entry.grid(row=0, column=3, padx=3)

        ctk.CTkLabel(self.supp_form_frame, text="Email:",
            font=ctk.CTkFont(weight="bold")).grid(row=0, column=4, padx=3, sticky="w")
        self.supp_email_entry = ctk.CTkEntry(self.supp_form_frame, width=180)
        self.supp_email_entry.grid(row=0, column=5, padx=3)

        ctk.CTkLabel(self.supp_form_frame, text="Διεύθυνση:",
            font=ctk.CTkFont(weight="bold")).grid(row=0, column=6, padx=3, sticky="w")
        self.supp_address_entry = ctk.CTkEntry(self.supp_form_frame, width=180)
        self.supp_address_entry.grid(row=0, column=7, padx=3)

        self.supp_save_btn = ctk.CTkButton(
            self.supp_form_frame, text="💾 Αποθήκευση",
            fg_color=("#2ecc71", "#27ae60"), hover_color=("#27ae60", "#1e8449"),
            text_color=("#FFFFFF", "#FFFFFF"), font=ctk.CTkFont(weight="bold"),
            command=self.save_supplier)
        self.supp_save_btn.grid(row=0, column=8, padx=(10, 3))

        self.supp_delete_btn = ctk.CTkButton(
            self.supp_form_frame, text="❌ Διαγραφή",
            fg_color=("#E74C3C", "#C0392B"), hover_color=("#C0392B", "#A93226"),
            text_color=("#FFFFFF", "#FFFFFF"), font=ctk.CTkFont(weight="bold"),
            command=self.delete_supplier)
        self.supp_delete_btn.grid(row=0, column=9, padx=3)

        # ── Treeview ──
        self.supp_table_container = ctk.CTkFrame(self)
        self.supp_table_container.grid(row=2, column=0, sticky="nsew")
        self.supp_table_container.grid_columnconfigure(0, weight=1)
        self.supp_table_container.grid_rowconfigure(0, weight=1)

        self.supp_scrollbar = ttk.Scrollbar(self.supp_table_container, orient="vertical")
        self.supp_scrollbar.grid(row=0, column=1, sticky="ns")

        self.supp_tree = ttk.Treeview(
            self.supp_table_container,
            columns=("id", "name", "phone", "email", "address"),
            show="headings", height=15,
            yscrollcommand=self.supp_scrollbar.set, selectmode="browse")
        self.supp_tree.grid(row=0, column=0, sticky="nsew")
        self.supp_scrollbar.config(command=self.supp_tree.yview)

        self.supp_tree.heading("id", text="ID")
        self.supp_tree.column("id", width=50, anchor="center")
        self.supp_tree.heading("name", text="Όνομα")
        self.supp_tree.column("name", width=250, anchor="w")
        self.supp_tree.heading("phone", text="Τηλ")
        self.supp_tree.column("phone", width=120, anchor="w")
        self.supp_tree.heading("email", text="Email")
        self.supp_tree.column("email", width=200, anchor="w")
        self.supp_tree.heading("address", text="Διεύθυνση")
        self.supp_tree.column("address", width=200, anchor="w")
        self.supp_tree.configure(style="Treeview")

        self.supp_tree.bind("<Double-1>", self._on_tree_select)

        # ── Order Button & Export ──
        self.supp_bottom_bar = ctk.CTkFrame(self, fg_color="transparent")
        self.supp_bottom_bar.grid(row=3, column=0, sticky="ew", pady=(15, 0))
        self.supp_bottom_bar.grid_columnconfigure(3, weight=1)

        self.supp_order_btn = ctk.CTkButton(
            self.supp_bottom_bar, text="📋 Αυτόματη Λίστα Παραγγελίας",
            fg_color="#8E44AD", hover_color="#7D3C98",
            font=ctk.CTkFont(weight="bold"), command=self.generate_automated_orders)
        self.supp_order_btn.grid(row=0, column=0, padx=(0, 15))

        self.supp_export_filter = ctk.CTkEntry(
            self.supp_bottom_bar, width=140, placeholder_text="Φίλτρο (π.χ. Φαρμακαποθήκη)")
        self.supp_export_filter.grid(row=0, column=1, padx=(0, 8))

        self.supp_export_format = ctk.CTkOptionMenu(
            self.supp_bottom_bar, values=["Excel (.csv)", "PDF (.txt style)"], width=130)
        self.supp_export_format.grid(row=0, column=2, padx=(0, 8))

        self.supp_export_btn = ctk.CTkButton(
            self.supp_bottom_bar, text="📤 Εξαγωγή",
            fg_color="#2980B9", hover_color="#1F618D",
            font=ctk.CTkFont(weight="bold"), command=self.export_suppliers)
        self.supp_export_btn.grid(row=0, column=3, sticky="e")

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------
    def _on_tree_select(self, event):
        sel = self.supp_tree.selection()
        if not sel:
            return
        values = self.supp_tree.item(sel[0])["values"]
        self.supp_name_entry.delete(0, "end")
        self.supp_name_entry.insert(0, values[1])
        self.supp_phone_entry.delete(0, "end")
        self.supp_phone_entry.insert(0, values[2] if len(values) > 2 else "")
        self.supp_email_entry.delete(0, "end")
        self.supp_email_entry.insert(0, values[3] if len(values) > 3 else "")
        self.supp_address_entry.delete(0, "end")
        self.supp_address_entry.insert(0, values[4] if len(values) > 4 else "")

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    def save_supplier(self):
        name = self.supp_name_entry.get().strip()
        phone = self.supp_phone_entry.get().strip()
        email = self.supp_email_entry.get().strip()
        address = self.supp_address_entry.get().strip()
        if not name:
            messagebox.showwarning("Προειδοποίηση", "Το όνομα είναι υποχρεωτικό.")
            return
        if self.db_service.add_supplier(name, phone, email, address):
            self.supp_name_entry.delete(0, "end")
            self.supp_phone_entry.delete(0, "end")
            self.supp_email_entry.delete(0, "end")
            self.supp_address_entry.delete(0, "end")
            self.refresh()
            messagebox.showinfo("Επιτυχία", f"Ο προμηθευτής '{name}' αποθηκεύτηκε.")
        else:
            messagebox.showerror("Σφάλμα", "Αποτυχία αποθήκευσης (πιθανό διπλότυπο όνομα).")

    def delete_supplier(self):
        sel = self.supp_tree.selection()
        if not sel:
            messagebox.showwarning("Προειδοποίηση", "Παρακαλώ επιλέξτε έναν προμηθευτή από τη λίστα.")
            return
        supplier_id = self.supp_tree.item(sel[0])["values"][0]
        name = self.supp_tree.item(sel[0])["values"][1]
        if not messagebox.askyesno("Επιβεβαίωση Διαγραφής",
            f"Είστε βέβαιοι ότι θέλετε να διαγράψετε τον προμηθευτή '{name}';", icon="warning"):
            return
        if self.db_service.delete_supplier(int(supplier_id)):
            self.supp_name_entry.delete(0, "end")
            self.supp_phone_entry.delete(0, "end")
            self.supp_email_entry.delete(0, "end")
            self.supp_address_entry.delete(0, "end")
            self.refresh()
            messagebox.showinfo("Επιτυχία", f"Ο προμηθευτής '{name}' διαγράφηκε επιτυχώς.")

    # ------------------------------------------------------------------
    # Automated Order List
    # ------------------------------------------------------------------
    def generate_automated_orders(self):
        def _write():
            try:
                grouped = self.db_service.get_low_stock_by_supplier()
                if not grouped:
                    self.after(0, lambda: messagebox.showinfo("Ενημέρωση",
                        "Δεν βρέθηκαν προϊόντα με χαμηλό στοκ που να αντιστοιχούν σε προμηθευτή."))
                    return
                ts = datetime.now().strftime("%Y%m%d")
                for sid, items in grouped.items():
                    sname = items[0]["supplier_name"].replace(" ", "_")
                    dest = os.path.join(os.path.expanduser("~"), "Desktop", f"Order_{sname}_{ts}.csv")
                    lines = ["Barcode,Όνομα Προϊόντος,Τρέχον Στοκ,Προτεινόμενη Ποσότητα Παραγγελίας"]
                    for it in items:
                        suggested = max(50, (10 - it["stock"]) * 5) if it["stock"] < 10 else 50
                        lines.append(f'{BaseView._csv_cell(it["barcode"])},{BaseView._csv_cell(it["name"])},{it["stock"]},{suggested}')
                    with open(dest, "w", encoding="utf-8-sig") as f:
                        f.write("\n".join(lines))
                self.after(0, lambda: messagebox.showinfo("Έτοιμες Παραγγελίες",
                    "Οι λίστες ανεφοδιασμού δημιουργήθηκαν στο Desktop ομαδοποιημένες ανά προμηθευτή!"))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Σφάλμα", str(e)))
        threading.Thread(target=_write, daemon=True).start()

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def export_suppliers(self):
        filter_text = self.supp_export_filter.get().strip().lower()
        fmt = self.supp_export_format.get()
        is_csv = "csv" in fmt.lower()

        rows = self.db_service.get_all_suppliers()
        if filter_text:
            rows = [r for r in rows if
                    filter_text in r["name"].lower() or
                    filter_text in r.get("email", "").lower()]
        data = [[r["id"], r["name"], r.get("phone", ""), r.get("email", ""),
                 r.get("address", "")] for r in rows]
        self._run_export(
            "Suppliers_Export",
            ["ID", "Όνομα", "Τηλέφωνο", "Email", "Διεύθυνση"],
            data, is_csv,
            txt_title="ΜΗΤΡΩΟ ΠΡΟΜΗΘΕΥΤΩΝ",
            txt_row_fmt="• {1:<30} | Τηλ: {2:<12} | Email: {3:<25}",
        )

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        if not self.winfo_ismapped():
            return

        def bg_fetch():
            try:
                rows = self.db_service.get_all_suppliers()
                self.after(0, self._safe_update_ui, rows)
            except Exception:
                logging.exception("Suppliers fetch failed")
                self.after(0, lambda: self._safe_update_ui([]))

        threading.Thread(target=bg_fetch, daemon=True).start()

    def _safe_update_ui(self, rows: List[Dict]):
        if not hasattr(self, 'supp_tree') or self.supp_tree is None:
            return
        self.supp_tree.delete(*self.supp_tree.get_children())
        for r in rows:
            self.supp_tree.insert("", "end", values=(
                r["id"], r["name"], r.get("phone", ""),
                r.get("email", ""), r.get("address", "")))
