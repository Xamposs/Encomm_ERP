import customtkinter as ctk
import tkinter as tk
import os
import logging
import threading
from datetime import datetime
from tkinter import messagebox, filedialog
from tkinter import ttk
from typing import List, Tuple
from .base_view import BaseView
from core.domain_models import Product


class InventoryView(BaseView):
    """Inventory management: search, add/edit/delete, Excel import, movement history, export."""

    def __init__(self, parent, db_service, config: dict, on_data_changed=None, **kwargs):
        kwargs.setdefault('fg_color', 'transparent')
        super().__init__(parent, db_service, config, **kwargs)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._on_data_changed = on_data_changed
        self.inv_page = 0
        self.inv_page_size = 20
        self.inv_show_movements = False
        self._inv_fetching = False
        self._search_timer = None
        self.inv_mov_page = 0

        # ── Toolbar ──
        self.inv_toolbar = ctk.CTkFrame(self, fg_color="transparent")
        self.inv_toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 20))
        self.inv_toolbar.grid_columnconfigure(0, weight=1)

        self.search_entry = ctk.CTkEntry(self.inv_toolbar, placeholder_text="🔍 Αναζήτηση κατά barcode ή όνομα...")
        self.search_entry.grid(row=0, column=0, padx=(0, 15), sticky="ew")
        self.search_entry.bind("<KeyRelease>", lambda e: self._inv_search_changed())

        self.add_prod_btn = ctk.CTkButton(self.inv_toolbar, text="➕  Νέο Προϊόν",
            fg_color="#34C759", hover_color="#289A47",
            font=ctk.CTkFont(weight="bold"), command=self.open_add_product_dialog)
        self.add_prod_btn.grid(row=0, column=1, padx=5)

        self.import_inv_btn = ctk.CTkButton(self.inv_toolbar, text="📥  Εισαγωγή Excel/CSV",
            fg_color=("#2ecc71", "#27ae60"), hover_color=("#27ae60", "#1e8449"),
            font=ctk.CTkFont(weight="bold"), command=self.import_supplier_invoice)
        self.import_inv_btn.grid(row=0, column=2, padx=(5, 0))

        self.inv_movements_btn = ctk.CTkButton(self.inv_toolbar, text="📋 Ιστορικό Κινήσεων",
            fg_color=("#8E44AD", "#7D3C98"), hover_color=("#7D3C98", "#6C3483"),
            font=ctk.CTkFont(weight="bold"), command=self.toggle_movement_history)
        self.inv_movements_btn.grid(row=0, column=3, padx=(5, 0))

        # ── Pagination toolbar ──
        self.inv_pager = ctk.CTkFrame(self, fg_color="transparent")
        self.inv_pager.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        self.inv_pager.grid_columnconfigure(1, weight=1)

        self.inv_page_info = ctk.CTkLabel(self.inv_pager, text="",
            font=ctk.CTkFont(size=12), text_color=BaseView._subtle_text())
        self.inv_page_info.grid(row=0, column=1, sticky="e", padx=10)

        self.inv_prev_btn = ctk.CTkButton(self.inv_pager, text="◀ Προηγ.", width=90, height=28,
            font=ctk.CTkFont(size=12), fg_color=("gray80", "gray30"), hover_color=("gray70", "gray40"),
            command=self._inv_prev_page)
        self.inv_prev_btn.grid(row=0, column=0, padx=(0, 5))

        self.inv_next_btn = ctk.CTkButton(self.inv_pager, text="Επόμ. ▶", width=90, height=28,
            font=ctk.CTkFont(size=12), fg_color=("gray80", "gray30"), hover_color=("gray70", "gray40"),
            command=self._inv_next_page)
        self.inv_next_btn.grid(row=0, column=2, padx=(5, 0))

        # ── Movement History View ──
        self.inv_mov_filter = ctk.CTkEntry(self, placeholder_text="🔍 Φιλτράρισμα κατά Barcode...", width=250)
        self.inv_mov_filter.grid(row=1, column=0, sticky="w", padx=15, pady=(5, 10))
        self.inv_mov_filter.bind("<KeyRelease>", lambda e: self._refresh_movement_history())
        self.inv_mov_filter.grid_remove()

        self.inv_mov_container = ctk.CTkFrame(self)
        self.inv_mov_container.grid(row=2, column=0, sticky="nsew", padx=15, pady=15)
        self.inv_mov_container.grid_columnconfigure(0, weight=1)
        self.inv_mov_container.grid_rowconfigure(0, weight=1)
        self.inv_mov_container.grid_remove()

        self.inv_mov_scrollbar = ttk.Scrollbar(self.inv_mov_container, orient="vertical")
        self.inv_mov_scrollbar.grid(row=0, column=1, sticky="ns")

        self.inv_mov_tree = ttk.Treeview(self.inv_mov_container,
            columns=("timestamp", "barcode", "name", "old_stock", "new_stock", "diff", "reason", "ref"),
            show="headings", height=20, yscrollcommand=self.inv_mov_scrollbar.set, selectmode="browse")
        self.inv_mov_tree.grid(row=0, column=0, sticky="nsew")
        self.inv_mov_scrollbar.config(command=self.inv_mov_tree.yview)

        for col, text, w, a in [
            ("timestamp", "Ημερομηνία", 150, "w"), ("barcode", "Barcode", 120, "w"),
            ("name", "Προϊόν", 200, "w"), ("old_stock", "Παλιό Στοκ", 80, "e"),
            ("new_stock", "Νέο Στοκ", 80, "e"), ("diff", "Διαφορά", 80, "e"),
            ("reason", "Αιτία", 140, "w"), ("ref", "Αναφορά", 100, "w")]:
            self.inv_mov_tree.heading(col, text=text)
            self.inv_mov_tree.column(col, width=w, anchor=a)

        self.inv_mov_pager = ctk.CTkFrame(self, fg_color="transparent")
        self.inv_mov_pager.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        self.inv_mov_pager.grid_columnconfigure(1, weight=1)
        self.inv_mov_pager.grid_remove()
        self.inv_mov_page_info = ctk.CTkLabel(self.inv_mov_pager, text="", font=ctk.CTkFont(size=12), text_color=BaseView._subtle_text())
        self.inv_mov_page_info.grid(row=0, column=1, sticky="e", padx=10)

        # ── Main Table ──
        self.table_container = ctk.CTkFrame(self)
        self.table_container.grid(row=1, column=0, sticky="nsew", padx=15, pady=15)
        self.table_container.grid_columnconfigure(0, weight=1)
        self.table_container.grid_rowconfigure(0, weight=1)

        self.inv_scrollbar = ttk.Scrollbar(self.table_container, orient="vertical")
        self.inv_scrollbar.grid(row=0, column=1, sticky="ns")

        self.inv_tree = ttk.Treeview(self.table_container,
            columns=("barcode", "name", "stock", "expiry", "price"),
            show="headings", height=20, yscrollcommand=self.inv_scrollbar.set, selectmode="browse")
        self.inv_tree.grid(row=0, column=0, sticky="nsew")
        self.inv_scrollbar.config(command=self.inv_tree.yview)

        for col, text, w, a in [
            ("barcode", "Barcode", 120, "w"), ("name", "Όνομα Προϊόντος", 280, "w"),
            ("stock", "Στοκ", 80, "e"), ("expiry", "Ημ. Λήξης", 120, "e"), ("price", "Τιμή", 100, "e")]:
            self.inv_tree.heading(col, text=text)
            self.inv_tree.column(col, width=w, anchor=a)

        self.inv_tree.tag_configure("low_stock", foreground="#FF9500")
        self.inv_tree.tag_configure("expired", foreground="#FF3B30")
        self.inv_tree.tag_configure("near_expiry", foreground="#FF9500")
        self.inv_tree.configure(style="Treeview")
        self.inv_tree.bind("<Double-1>", self._on_tree_double_click)

        # ── Export bar ──
        self.inv_export_bar = ctk.CTkFrame(self, fg_color="transparent")
        self.inv_export_bar.grid(row=3, column=0, sticky="ew", pady=(15, 0))
        self.inv_export_filter = ctk.CTkEntry(self.inv_export_bar, width=160, placeholder_text="Φίλτρο (π.χ. DEPON)")
        self.inv_export_filter.pack(side="left", padx=(0, 8))
        self.inv_export_limit = ctk.CTkEntry(self.inv_export_bar, width=100, placeholder_text="Ποσότητα (π.χ. 20 ή ALL)")
        self.inv_export_limit.pack(side="left", padx=(0, 8))
        self.inv_export_start = ctk.CTkEntry(self.inv_export_bar, width=110, placeholder_text="Από (YYYY-MM-DD)")
        self.inv_export_start.pack(side="left", padx=(0, 5))
        self.inv_export_end = ctk.CTkEntry(self.inv_export_bar, width=110, placeholder_text="Έως (YYYY-MM-DD)")
        self.inv_export_end.pack(side="left", padx=(0, 8))
        self.inv_export_format = ctk.CTkOptionMenu(self.inv_export_bar, values=["PDF (.txt style)", "Excel (.csv)"], width=140)
        self.inv_export_format.pack(side="left", padx=(0, 8))
        self.inv_export_btn = ctk.CTkButton(self.inv_export_bar, text="📤 Εξαγωγή",
            fg_color="#2980B9", hover_color="#1F618D", font=ctk.CTkFont(weight="bold"),
            command=self.export_inventory)
        self.inv_export_btn.pack(side="left")

    # ==================================================================
    # Search / Pagination / Movement History
    # ==================================================================
    def _inv_search_changed(self):
        if not self.winfo_ismapped():
            return
        if hasattr(self, '_search_timer') and self._search_timer is not None:
            self.after_cancel(self._search_timer)
        self.inv_page = 0
        self._search_timer = self.after(300, self.refresh)

    def _inv_next_page(self):
        if not self.winfo_ismapped() or getattr(self, '_inv_fetching', False):
            return
        self.inv_page += 1
        self.refresh()

    def _inv_prev_page(self):
        if not self.winfo_ismapped() or getattr(self, '_inv_fetching', False):
            return
        if self.inv_page > 0:
            self.inv_page -= 1
            self.refresh()

    def toggle_movement_history(self):
        self.inv_show_movements = not self.inv_show_movements
        if self.inv_show_movements:
            self.inv_movements_btn.configure(text="🔄 Πίσω στη Λίστα Προϊόντων")
            self._show_movement_history()
        else:
            self.inv_movements_btn.configure(text="📋 Ιστορικό Κινήσεων")
            self._hide_movement_history()

    def _show_movement_history(self):
        self.search_entry.grid_remove()
        self.add_prod_btn.grid_remove()
        self.import_inv_btn.grid_remove()
        self.table_container.grid_remove()
        self.inv_pager.grid_remove()
        self.inv_export_bar.grid_remove()
        self.inv_mov_filter.grid()
        self.inv_mov_container.grid()
        self.inv_mov_pager.grid()
        self._refresh_movement_history()

    def _hide_movement_history(self):
        self.inv_mov_filter.grid_remove()
        self.inv_mov_container.grid_remove()
        self.inv_mov_pager.grid_remove()
        self.search_entry.grid()
        self.add_prod_btn.grid()
        self.import_inv_btn.grid()
        self.table_container.grid()
        self.inv_pager.grid()
        self.inv_export_bar.grid()

    def _refresh_movement_history(self):
        barcode_filter = self.inv_mov_filter.get().strip() or None
        try:
            rows = self.db_service.get_stock_movements(barcode=barcode_filter, limit=100)
            self.inv_mov_tree.delete(*self.inv_mov_tree.get_children())
            for r in rows:
                self.inv_mov_tree.insert("", "end", values=(
                    r.get("timestamp", ""), r.get("barcode", ""), r.get("product_name", ""),
                    r.get("old_stock", 0), r.get("new_stock", 0),
                    r.get("change_amount", 0), r.get("reason", ""), r.get("source", "")))
        except Exception:
            pass

    # ==================================================================
    # Product CRUD via dialogs
    # ==================================================================
    def _on_tree_double_click(self, event):
        selection = self.inv_tree.selection()
        if not selection:
            return
        item = self.inv_tree.item(selection[0])
        barcode = item["values"][0]
        product = self.db_service.get_product(str(barcode))
        if product:
            self.open_edit_product_dialog(product)

    def open_add_product_dialog(self):
        dialog = ProductFormDialog(self, self.db_service)
        self.wait_window(dialog)
        if dialog.result:
            p = Product(
                barcode=dialog.result["barcode"], name=dialog.result["name"],
                stock=int(dialog.result.get("stock", 0)),
                expiry_date=dialog.result.get("expiry_date", "2099-12-31"),
                price=float(dialog.result.get("price", 0)),
                supplier_id=dialog.result.get("supplier_id"))
            if self.db_service.add_product(p):
                messagebox.showinfo("Επιτυχία", f"Το προϊόν '{p.name}' προστέθηκε.")
                self.refresh()
                if self._on_data_changed:
                    self._on_data_changed()
            else:
                messagebox.showerror("Σφάλμα", "Αποτυχία προσθήκης προϊόντος.")

    def open_edit_product_dialog(self, product: Product):
        dialog = ProductFormDialog(self, self.db_service, product=product)
        self.wait_window(dialog)
        if dialog.result:
            p = Product(
                barcode=dialog.result["barcode"], name=dialog.result["name"],
                stock=int(dialog.result.get("stock", 0)),
                expiry_date=dialog.result.get("expiry_date", "2099-12-31"),
                price=float(dialog.result.get("price", 0)),
                supplier_id=dialog.result.get("supplier_id"))
            if self.db_service.update_product(p):
                self.refresh()
                if self._on_data_changed:
                    self._on_data_changed()

    def delete_product(self, barcode: str, name: str):
        if not messagebox.askyesno("Επιβεβαίωση Διαγραφής",
            f"Είστε βέβαιοι ότι θέλετε να διαγράψετε το προϊόν '{name}';", icon="warning"):
            return
        if self.db_service.delete_product(barcode):
            messagebox.showinfo("Επιτυχία", f"Το προϊόν '{name}' διαγράφηκε.")
            self.refresh()
            if self._on_data_changed:
                self._on_data_changed()

    # ==================================================================
    # Excel Import
    # ==================================================================
    def import_supplier_invoice(self):
        from infrastructure.excel_parser_service import ExcelParserService
        file_path = filedialog.askopenfilename(
            title="Επιλογή Αρχείου Προμηθευτή",
            filetypes=[("Excel & CSV", "*.xlsx *.csv"), ("All", "*.*")])
        if not file_path:
            return

        def bg_import():
            try:
                parser = ExcelParserService()
                products = parser.parse_supplier_file(file_path)
                if not products:
                    self.after(0, lambda: messagebox.showwarning("Προειδοποίηση", "Δεν βρέθηκαν έγκυρα προϊόντα."))
                    return
                self.db_service.bulk_upsert_products(products)
                self.after(0, lambda: [
                    messagebox.showinfo("Επιτυχία", f"Εισήχθησαν {len(products)} προϊόντα!"),
                    self.refresh(),
                    self._on_data_changed() if self._on_data_changed else None
                ])
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Σφάλμα", str(e)))
        threading.Thread(target=bg_import, daemon=True).start()

    # ==================================================================
    # Export
    # ==================================================================
    def export_inventory(self):
        filter_text = self.inv_export_filter.get().strip().lower()
        limit_str = self.inv_export_limit.get().strip().upper()
        start_date = self.inv_export_start.get().strip() if hasattr(self, 'inv_export_start') else ""
        end_date = self.inv_export_end.get().strip() if hasattr(self, 'inv_export_end') else ""
        fmt = self.inv_export_format.get()
        is_csv = "csv" in fmt.lower()

        def _write():
            try:
                products = self.db_service.get_all_products()
                if filter_text:
                    products = [p for p in products if filter_text in p.name.lower() or filter_text in p.barcode.lower()]
                if start_date:
                    products = [p for p in products if p.expiry_date >= start_date]
                if end_date:
                    products = [p for p in products if p.expiry_date <= end_date]
                try:
                    limit = int(limit_str)
                    products = products[:limit]
                except ValueError:
                    pass
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                if is_csv:
                    dest = os.path.join(os.path.expanduser("~"), "Desktop", f"Inventory_Export_{ts}.csv")
                    lines = ["Barcode,Όνομα,Στοκ,Ημ.Λήξης,Τιμή"]
                    for p in products:
                        lines.append(f'{BaseView._csv_cell(p.barcode)},{BaseView._csv_cell(p.name)},{p.stock},{BaseView._csv_cell(p.expiry_date)},{p.price:.2f}')
                    with open(dest, "w", encoding="utf-8-sig") as f:
                        f.write("\n".join(lines))
                else:
                    dest = os.path.join(os.path.expanduser("~"), "Desktop", f"Inventory_Export_{ts}.txt")
                    lines = ["=" * 60, "  ENCOMM INVENTORY — ΕΞΑΓΩΓΗ ΑΠΟΘΗΚΗΣ", "=" * 60,
                             f"Ημ/νία: {datetime.now().strftime('%d/%m/%Y %H:%M')}  |  Προϊόντα: {len(products)}",
                             f"Εύρος ημ/νιών λήξης: {start_date or '—'} έως {end_date or '—'}", "-" * 60]
                    lines.append(f"{'Barcode':<15} {'Όνομα':<30} {'Στοκ':<8} {'Λήξη':<12} {'Τιμή':<10}")
                    lines.append("-" * 60)
                    for p in products:
                        lines.append(f"{p.barcode:<15} {p.name[:30]:<30} {p.stock:<8} {p.expiry_date:<12} €{p.price:.2f}")
                    lines.append("=" * 60)
                    with open(dest, "w", encoding="utf-8") as f:
                        f.write("\n".join(lines))
                self.after(0, lambda: messagebox.showinfo("Επιτυχής Εξαγωγή",
                    "Το φιλτραρισμένο αρχείο βάσει ημερομηνιών αποθηκεύτηκε στην Επιφάνεια Εργασίας!"))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Σφάλμα Εξαγωγής", str(e)))
        threading.Thread(target=_write, daemon=True).start()

    # ==================================================================
    # Refresh
    # ==================================================================
    def refresh(self) -> None:
        if not self.winfo_ismapped():
            return
        self._inv_fetching = True
        search_query = self.search_entry.get().strip() if hasattr(self, 'search_entry') else ""
        threshold = int(self.config.get("low_stock_threshold", 10))
        alert_days = int(self.config.get("expiry_alert_days", 30))

        def bg_fetch():
            try:
                page_products, total_count = self.db_service.get_products_paginated(
                    search_query=search_query, threshold=threshold, alert_days=alert_days,
                    limit=self.inv_page_size, offset=self.inv_page * self.inv_page_size)
                total_pages = max(1, (total_count + self.inv_page_size - 1) // self.inv_page_size)
                self.after(0, self._safe_update_ui, page_products, total_count, total_pages)
            except Exception:
                logging.exception("Inventory paginated fetch failed")
            finally:
                self.after(0, lambda: setattr(self, '_inv_fetching', False))
        threading.Thread(target=bg_fetch, daemon=True).start()

    def _safe_update_ui(self, page_products, total_count, total_pages):
        if not hasattr(self, 'inv_tree') or self.inv_tree is None:
            return
        self.inv_tree.delete(*self.inv_tree.get_children())
        threshold = int(self.config.get("low_stock_threshold", 10))
        alert_days = int(self.config.get("expiry_alert_days", 30))
        today = datetime.now().strftime("%Y-%m-%d")
        for p in page_products:
            tag = ()
            if p.stock <= threshold:
                tag = ("low_stock",)
            if p.expiry_date < today:
                tag = ("expired",)
            elif p.expiry_date <= datetime.now().strftime("%Y-%m-%d") if False else p.expiry_date:
                pass
            self.inv_tree.insert("", "end", values=(p.barcode, p.name, p.stock, p.expiry_date, f"€{p.price:.2f}"), tags=tag)
        self.inv_page_info.configure(text=f"Σελίδα {self.inv_page + 1}/{total_pages}  |  Σύνολο: {total_count}")


# ======================================================================
# Product Form Dialog (extracted from main_window.py)
# ======================================================================
class ProductFormDialog(ctk.CTkToplevel):
    """Modal dialog for adding or editing a product."""

    def __init__(self, parent, db_service, product: Product = None):
        super().__init__(parent)
        self.db_service = db_service
        self.product = product
        self.result = None

        self.title("Επεξεργασία Προϊόντος" if product else "Νέο Προϊόν")
        self.geometry("420x480")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        ctk.CTkLabel(self, text="Barcode:", font=ctk.CTkFont(size=12)).pack(padx=25, pady=(20, 2), anchor="w")
        self.barcode_entry = ctk.CTkEntry(self, width=370)
        self.barcode_entry.pack(padx=25, pady=(0, 10))
        if product:
            self.barcode_entry.insert(0, product.barcode)
            self.barcode_entry.configure(state="disabled")

        ctk.CTkLabel(self, text="Όνομα:", font=ctk.CTkFont(size=12)).pack(padx=25, pady=(0, 2), anchor="w")
        self.name_entry = ctk.CTkEntry(self, width=370)
        self.name_entry.pack(padx=25, pady=(0, 10))
        if product:
            self.name_entry.insert(0, product.name)

        ctk.CTkLabel(self, text="Απόθεμα:", font=ctk.CTkFont(size=12)).pack(padx=25, pady=(0, 2), anchor="w")
        self.stock_entry = ctk.CTkEntry(self, width=370)
        self.stock_entry.pack(padx=25, pady=(0, 10))
        self.stock_entry.insert(0, str(product.stock) if product else "0")

        ctk.CTkLabel(self, text="Ημ. Λήξης (YYYY-MM-DD):", font=ctk.CTkFont(size=12)).pack(padx=25, pady=(0, 2), anchor="w")
        self.expiry_entry = ctk.CTkEntry(self, width=370)
        self.expiry_entry.pack(padx=25, pady=(0, 10))
        self.expiry_entry.insert(0, product.expiry_date if product else "2099-12-31")

        ctk.CTkLabel(self, text="Τιμή (€):", font=ctk.CTkFont(size=12)).pack(padx=25, pady=(0, 2), anchor="w")
        self.price_entry = ctk.CTkEntry(self, width=370)
        self.price_entry.pack(padx=25, pady=(0, 20))
        self.price_entry.insert(0, str(product.price) if product else "0.00")

        # Supplier dropdown
        self.supplier_var = tk.StringVar(value="Κανένας")
        ctk.CTkLabel(self, text="Προμηθευτής:", font=ctk.CTkFont(size=12)).pack(padx=25, pady=(0, 2), anchor="w")
        self.supplier_menu = ctk.CTkOptionMenu(self, variable=self.supplier_var,
            values=["Κανένας"], width=370)
        self.supplier_menu.pack(padx=25, pady=(0, 20))
        self._supplier_map = {}
        try:
            suppliers = parent.db_service.get_all_suppliers()
            names = ["Κανένας"] + [s["name"] for s in suppliers]
            self.supplier_menu.configure(values=names)
            for s in suppliers:
                self._supplier_map[s["name"]] = s["id"]
            if product and getattr(product, 'supplier_id', None):
                for s in suppliers:
                    if s["id"] == product.supplier_id:
                        self.supplier_var.set(s["name"])
                        break
        except Exception:
            pass

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(pady=10)
        ctk.CTkButton(btn_frame, text="Αποθήκευση", fg_color=("#2ecc71", "#27ae60"),
            hover_color=("#27ae60", "#1e8449"), text_color=("#FFFFFF", "#FFFFFF"),
            font=ctk.CTkFont(weight="bold"), command=self.save).pack(side="left", padx=5)
        ctk.CTkButton(btn_frame, text="Ακύρωση", fg_color=("gray80", "gray30"),
            hover_color=("gray70", "gray40"), command=self.destroy).pack(side="left", padx=5)
        if product:
            ctk.CTkButton(btn_frame, text="Διαγραφή", fg_color=("#E74C3C", "#C0392B"),
                hover_color=("#C0392B", "#A93226"), text_color=("#FFFFFF", "#FFFFFF"),
                font=ctk.CTkFont(weight="bold"),
                command=lambda: [self._delete(), self.destroy()]).pack(side="left", padx=5)

    def save(self):
        barcode = self.barcode_entry.get().strip()
        name = self.name_entry.get().strip()
        if not barcode or not name:
            messagebox.showwarning("Προειδοποίηση", "Barcode και Όνομα είναι υποχρεωτικά.", parent=self)
            return
        try:
            stock = int(self.stock_entry.get().strip())
            price = float(self.price_entry.get().strip().replace(",", "."))
        except ValueError:
            messagebox.showwarning("Προειδοποίηση", "Άκυρες τιμές για απόθεμα ή τιμή.", parent=self)
            return
        supplier_name = self.supplier_var.get()
        supplier_id = self._supplier_map.get(supplier_name, None) if supplier_name != "Κανένας" else None
        self.result = {"barcode": barcode, "name": name, "stock": stock,
                       "expiry_date": self.expiry_entry.get().strip(), "price": price,
                       "supplier_id": supplier_id}
        self.destroy()

    def _delete(self):
        if not self.product:
            return
        if messagebox.askyesno("Επιβεβαίωση Διαγραφής",
            f"Είστε βέβαιοι ότι θέλετε να διαγράψετε το προϊόν '{self.product.name}';",
            parent=self, icon="warning"):
            self.db_service.delete_product(self.product.barcode)
