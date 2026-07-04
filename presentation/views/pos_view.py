import customtkinter as ctk
import tkinter as tk
import os
import logging
import threading
import uuid
from datetime import datetime
from tkinter import messagebox
from tkinter import ttk
from typing import List, Tuple
from .base_view import BaseView
from core.domain_models import Product


class POSView(BaseView):
    """Point-of-sale: product selector, cart, checkout, customer history, export."""

    def __init__(self, parent, db_service, config: dict, **kwargs):
        kwargs.setdefault('fg_color', 'transparent')
        super().__init__(parent, db_service, config, **kwargs)
        self.grid_columnconfigure(0, weight=3)
        self.grid_columnconfigure(1, weight=2)
        self.grid_rowconfigure(0, weight=1)

        self.invoice_cart: List[Tuple[Product, int]] = []
        self.cart_rows_tracked = []
        self._pos_search_timer = None
        self._selected_customer_id = None

        # ── LEFT PANEL ──
        self.pos_left_panel = ctk.CTkFrame(self)
        self.pos_left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 20))
        self.pos_left_panel.grid_columnconfigure(0, weight=1)
        self.pos_left_panel.grid_rowconfigure(0, weight=0)
        self.pos_left_panel.grid_rowconfigure(1, weight=0)
        self.pos_left_panel.grid_rowconfigure(2, weight=0)
        self.pos_left_panel.grid_rowconfigure(3, weight=1)

        # Selector frame
        self.pos_selector_frame = ctk.CTkFrame(self.pos_left_panel, fg_color="transparent")
        self.pos_selector_frame.grid(row=0, column=0, sticky="ew", padx=15, pady=15)
        self.pos_selector_frame.grid_columnconfigure(0, weight=2)
        self.pos_selector_frame.grid_columnconfigure(1, weight=1)

        self.pos_prod_lbl = ctk.CTkLabel(self.pos_selector_frame, text="Επιλογή Προϊόντος:",
            font=ctk.CTkFont(weight="bold"))
        self.pos_prod_lbl.grid(row=0, column=0, sticky="w", pady=(0, 5))

        self.pos_prod_menu = ctk.CTkEntry(self.pos_selector_frame,
            placeholder_text="🔍 Σκανάρετε Barcode ή πληκτρολογήστε όνομα...")
        self.pos_prod_menu.grid(row=1, column=0, padx=(0, 10), sticky="ew")
        self.pos_prod_menu.bind("<Return>", lambda e: self.add_item_to_cart())
        self.pos_prod_menu.bind("<KeyRelease>", lambda e: self._pos_search_changed())

        self.pos_qty_lbl = ctk.CTkLabel(self.pos_selector_frame, text="Ποσότητα:",
            font=ctk.CTkFont(weight="bold"))
        self.pos_qty_lbl.grid(row=0, column=1, sticky="w", pady=(0, 5))
        self.pos_qty_entry = ctk.CTkEntry(self.pos_selector_frame)
        self.pos_qty_entry.insert(0, "1")
        self.pos_qty_entry.grid(row=1, column=1, padx=(0, 10), sticky="ew")

        self.add_cart_btn = ctk.CTkButton(self.pos_selector_frame, text="🛒 Προσθήκη",
            font=ctk.CTkFont(weight="bold"), command=self.add_item_to_cart)
        self.add_cart_btn.grid(row=1, column=2, sticky="ew")

        # Live search results
        self.pos_search_results_tree = ttk.Treeview(self.pos_left_panel,
            columns=("barcode", "name", "stock", "price"),
            show="headings", height=5, selectmode="browse")
        self.pos_search_results_tree.grid(row=1, column=0, sticky="ew", padx=15, pady=(0, 10))
        for col, text, w, a in [
            ("barcode", "Barcode", 100, "w"), ("name", "Όνομα", 200, "w"),
            ("stock", "Στοκ", 60, "e"), ("price", "Τιμή", 70, "e")]:
            self.pos_search_results_tree.heading(col, text=text)
            self.pos_search_results_tree.column(col, width=w, anchor=a)
        self.pos_search_results_tree.configure(style="Treeview")

        # Cart header
        self.cart_header = ctk.CTkFrame(self.pos_left_panel, fg_color=BaseView._header_bg(), height=30)
        self.cart_header.grid(row=2, column=0, sticky="ew", padx=15, pady=(5, 5))
        for i in range(5):
            self.cart_header.grid_columnconfigure(i, weight=[2, 1, 1, 1, 1][i])
        _hdr_fg = BaseView._header_fg()
        for i, (text, anchor) in enumerate([
            ("Όνομα Προϊόντος", "w"), ("Τιμή Μονάδας", "e"),
            ("Ποσότητα", "e"), ("Σύνολο", "e"), ("", "e")]):
            ctk.CTkLabel(self.cart_header, text=text,
                font=ctk.CTkFont(size=11, weight="bold"), text_color=_hdr_fg).grid(
                row=0, column=i, padx=(15 if i == 0 else 15, 5 if i == 0 else 15), sticky=anchor)

        # Cart scroll
        self.cart_scroll = ctk.CTkScrollableFrame(self.pos_left_panel, fg_color="transparent")
        self.cart_scroll.grid(row=3, column=0, sticky="nsew", padx=15, pady=(0, 15))
        self.cart_scroll.grid_columnconfigure(0, weight=1)

        # ── RIGHT PANEL ──
        self.pos_right_panel = ctk.CTkFrame(self)
        self.pos_right_panel.grid(row=0, column=1, sticky="nsew")

        self.pos_summary_title = ctk.CTkLabel(self.pos_right_panel,
            text="Σύνοψη Παραστατικού", font=ctk.CTkFont(size=16, weight="bold"))
        self.pos_summary_title.pack(padx=20, pady=20, anchor="w")

        self.sum_items_count = ctk.CTkLabel(self.pos_right_panel, text="Συνολικά Τεμάχια: 0", font=ctk.CTkFont(size=13))
        self.sum_items_count.pack(padx=20, pady=5, anchor="w")
        self.sum_subtotal = ctk.CTkLabel(self.pos_right_panel, text="Υποσύνολο: €0.00", font=ctk.CTkFont(size=13))
        self.sum_subtotal.pack(padx=20, pady=5, anchor="w")

        vat_pct = float(self.config.get("vat_rate", 0.15)) * 100
        self.sum_vat = ctk.CTkLabel(self.pos_right_panel, text=f"ΦΠΑ ({vat_pct:.1f}%): €0.00", font=ctk.CTkFont(size=13))
        self.sum_vat.pack(padx=20, pady=5, anchor="w")
        self.sum_total = ctk.CTkLabel(self.pos_right_panel, text="Γενικό Σύνολο: €0.00",
            font=ctk.CTkFont(size=20, weight="bold"), text_color="#34C759")
        self.sum_total.pack(padx=20, pady=(15, 10), anchor="w")

        # Customer selector
        self.pos_cust_frame = ctk.CTkFrame(self.pos_right_panel, fg_color="transparent")
        self.pos_cust_frame.pack(padx=20, pady=5, fill="x")
        ctk.CTkLabel(self.pos_cust_frame, text="Πελάτης:", font=ctk.CTkFont(weight="bold")).pack(side="left", padx=(0, 8))
        self.pos_customer_var = tk.StringVar(value="Λιανική Πώληση (Κανένας)")
        self.pos_customer_menu = ctk.CTkOptionMenu(self.pos_cust_frame,
            variable=self.pos_customer_var, values=["Λιανική Πώληση (Κανένας)"],
            width=220, command=self._on_pos_customer_selected)
        self.pos_customer_menu.pack(side="left")

        self.checkout_btn = ctk.CTkButton(self.pos_right_panel, text="💳  Ολοκλήρωση Πώλησης",
            font=ctk.CTkFont(weight="bold", size=14), fg_color="#10B981", hover_color="#059669",
            command=self.process_checkout)
        self.checkout_btn.pack(padx=20, pady=10, fill="x")

        self.clear_cart_btn = ctk.CTkButton(self.pos_right_panel, text="🧹 Αδειασμα Καλαθιού",
            fg_color=("gray80", "gray30"), hover_color=("gray70", "gray40"), command=self.clear_cart)
        self.clear_cart_btn.pack(padx=20, pady=5, fill="x")

        # Cart export
        self.pos_export_bar = ctk.CTkFrame(self.pos_right_panel, fg_color="transparent")
        self.pos_export_bar.pack(padx=20, pady=(15, 5), fill="x")
        self.pos_export_filter = ctk.CTkEntry(self.pos_export_bar, width=140, placeholder_text="Φίλτρο (π.χ. DEPON)")
        self.pos_export_filter.pack(side="left", padx=(0, 6))
        self.pos_export_limit = ctk.CTkEntry(self.pos_export_bar, width=90, placeholder_text="Ποσότητα (π.χ. 20 ή ALL)")
        self.pos_export_limit.pack(side="left", padx=(0, 6))
        self.pos_export_format = ctk.CTkOptionMenu(self.pos_export_bar, values=["PDF (.txt style)", "Excel (.csv)"], width=130)
        self.pos_export_format.pack(side="left", padx=(0, 6))
        self.pos_export_btn = ctk.CTkButton(self.pos_export_bar, text="📤 Εξαγωγή",
            fg_color="#2980B9", hover_color="#1F618D", font=ctk.CTkFont(weight="bold"),
            command=self.export_cart)
        self.pos_export_btn.pack(side="left")

        # Sales export
        self.pos_sales_export_bar = ctk.CTkFrame(self, fg_color="transparent")
        self.pos_sales_export_bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self.pos_sales_export_start = ctk.CTkEntry(self.pos_sales_export_bar, width=120, placeholder_text="Από (YYYY-MM-DD)")
        self.pos_sales_export_start.pack(side="left", padx=(0, 5))
        self.pos_sales_export_end = ctk.CTkEntry(self.pos_sales_export_bar, width=120, placeholder_text="Έως (YYYY-MM-DD)")
        self.pos_sales_export_end.pack(side="left", padx=(0, 8))
        self.pos_sales_export_format = ctk.CTkOptionMenu(self.pos_sales_export_bar,
            values=["Excel (.csv)", "PDF (.txt style)"], width=140)
        self.pos_sales_export_format.pack(side="left", padx=(0, 8))
        self.pos_sales_export_btn = ctk.CTkButton(self.pos_sales_export_bar, text="📤 Εξαγωγή Πωλήσεων",
            fg_color="#2980B9", hover_color="#1F618D", font=ctk.CTkFont(weight="bold"),
            command=self.export_pos_sales)
        self.pos_sales_export_btn.pack(side="left")

    # ==================================================================
    # Live Search
    # ==================================================================
    def _pos_search_changed(self):
        if not self.winfo_ismapped():
            return
        if hasattr(self, '_pos_search_timer') and self._pos_search_timer is not None:
            self.after_cancel(self._pos_search_timer)
        self._pos_search_timer = self.after(250, self._pos_live_search)

    def _pos_live_search(self):
        if not self.winfo_ismapped():
            return
        text = self.pos_prod_menu.get().strip()
        if not text:
            self.pos_search_results_tree.delete(*self.pos_search_results_tree.get_children())
            return

        def bg_fetch():
            try:
                results, _ = self.db_service.get_products_paginated(search_query=text, limit=10, offset=0)
                self.after(0, self._safe_update_pos_search_ui, results, text)
            except Exception:
                logging.exception("POS live search failed")
        threading.Thread(target=bg_fetch, daemon=True).start()

    def _safe_update_pos_search_ui(self, results: list, original_text: str):
        if self.pos_prod_menu.get().strip() != original_text:
            return
        self.pos_search_results_tree.delete(*self.pos_search_results_tree.get_children())
        for p in results:
            self.pos_search_results_tree.insert("", "end", values=(p.barcode, p.name, p.stock, f"€{p.price:.2f}"))

    # ==================================================================
    # Add to cart
    # ==================================================================
    def add_item_to_cart(self):
        selection = self.pos_search_results_tree.selection()
        barcode = None
        if selection:
            barcode = str(self.pos_search_results_tree.item(selection[0])["values"][0])
        else:
            text = self.pos_prod_menu.get().strip()
            barcode = text

        if not barcode:
            return

        try:
            qty = int(self.pos_qty_entry.get().strip())
        except ValueError:
            qty = 1

        def bg_add():
            product = self.db_service.get_product(barcode)
            if not product:
                self.after(0, lambda: messagebox.showwarning("Προειδοποίηση",
                    f"Δεν βρέθηκε προϊόν με barcode '{barcode}'."))
                return
            if product.stock < qty:
                self.after(0, lambda: messagebox.showerror("Σφάλμα",
                    f"Ανεπαρκές απόθεμα για '{product.name}'.\nΔιαθέσιμο: {product.stock}, Ζητούμενο: {qty}"))
                return
            self.after(0, self._safe_finalize_add_to_cart, product, qty)

        threading.Thread(target=bg_add, daemon=True).start()

    def _safe_finalize_add_to_cart(self, product: Product, qty: int):
        existing = [(i, (p, q)) for i, (p, q) in enumerate(self.invoice_cart) if p.barcode == product.barcode]
        if existing:
            idx, (_, old_q) = existing[0]
            self.invoice_cart[idx] = (product, old_q + qty)
        else:
            self.invoice_cart.append((product, qty))
        self.pos_prod_menu.delete(0, "end")
        self.pos_qty_entry.delete(0, "end")
        self.pos_qty_entry.insert(0, "1")
        self.refresh_cart_list()

    def refresh_cart_list(self):
        for row in self.cart_rows_tracked:
            try:
                row.destroy()
            except Exception:
                pass
        self.cart_rows_tracked.clear()

        subtotal = 0.0
        total_qty = 0
        for idx, (p, qty) in enumerate(self.invoice_cart):
            subtotal += p.price * qty
            total_qty += qty

            row_frame = ctk.CTkFrame(self.cart_scroll, fg_color=BaseView._zebra_row(idx))
            row_frame.pack(fill="x", pady=1)
            row_frame.grid_columnconfigure(0, weight=2)
            row_frame.grid_columnconfigure(1, weight=1)
            row_frame.grid_columnconfigure(2, weight=1)
            row_frame.grid_columnconfigure(3, weight=1)
            row_frame.grid_columnconfigure(4, weight=1)
            self.cart_rows_tracked.append(row_frame)

            body = BaseView._body_text()
            ctk.CTkLabel(row_frame, text=p.name[:30], font=ctk.CTkFont(size=12), text_color=body,
                anchor="w").grid(row=0, column=0, padx=(10, 5), sticky="w")
            ctk.CTkLabel(row_frame, text=f"€{p.price:.2f}", font=ctk.CTkFont(size=12), text_color=body).grid(row=0, column=1, padx=5)
            ctk.CTkLabel(row_frame, text=str(qty), font=ctk.CTkFont(size=12), text_color=body).grid(row=0, column=2, padx=5)
            ctk.CTkLabel(row_frame, text=f"€{p.price * qty:.2f}", font=ctk.CTkFont(size=12), text_color=body).grid(row=0, column=3, padx=5)

            remove_btn = ctk.CTkButton(row_frame, text="✕", width=28, height=28,
                fg_color=("#E74C3C", "#C0392B"), hover_color=("#C0392B", "#A93226"),
                text_color=("#FFFFFF", "#FFFFFF"), font=ctk.CTkFont(size=12),
                command=lambda i=idx: self._remove_cart_item(i))
            remove_btn.grid(row=0, column=4, padx=5)

        vat_rate = float(self.config.get("vat_rate", 0.15))
        vat = round(subtotal * vat_rate, 2)
        grand = round(subtotal + vat, 2)

        self.sum_items_count.configure(text=f"Συνολικά Τεμάχια: {total_qty}")
        self.sum_subtotal.configure(text=f"Υποσύνολο: €{subtotal:.2f}")
        self.sum_vat.configure(text=f"ΦΠΑ ({vat_rate * 100:.1f}%): €{vat:.2f}")
        self.sum_total.configure(text=f"Γενικό Σύνολο: €{grand:.2f}")

    def _remove_cart_item(self, idx: int):
        if 0 <= idx < len(self.invoice_cart):
            self.invoice_cart.pop(idx)
            self.refresh_cart_list()

    def clear_cart(self):
        for row in self.cart_rows_tracked:
            try:
                row.destroy()
            except Exception:
                pass
        self.cart_rows_tracked.clear()
        self.invoice_cart = []
        self.refresh_cart_list()

    # ==================================================================
    # Checkout
    # ==================================================================
    def process_checkout(self):
        if not self.invoice_cart:
            messagebox.showwarning("Προειδοποίηση", "Το καλάθι είναι άδειο.")
            return

        succeeded: List[Tuple[Product, int]] = []
        failed_items: List[Tuple[str, str]] = []

        conn = None
        try:
            conn = self.db_service._get_connection()
            conn.execute("BEGIN TRANSACTION")

            for p, qty in self.invoice_cart:
                db_p = self.db_service.get_product(p.barcode)
                if not db_p or db_p.stock < qty:
                    failed_items.append((p.name, "Ανεπαρκές απόθεμα"))
                    continue
                conn.execute("UPDATE ProductMaster SET Stock = ? WHERE Barcode = ?",
                    (db_p.stock - qty, p.barcode))
                succeeded.append((p, qty))

            if not failed_items:
                conn.commit()
            else:
                conn.rollback()
                succeeded.clear()
        except Exception:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            succeeded.clear()
        finally:
            if conn:
                conn.close()

        if failed_items:
            msg = "Η πώληση ακυρώθηκε. Προβλήματα:\n\n" + "\n".join(f"• {name}: {reason}" for name, reason in failed_items)
            messagebox.showerror("Σφάλμα Πώλησης", msg)
            return

        # Full success: save invoice
        subtotal = sum(p.price * q for p, q in succeeded)
        vat_rate = float(self.config.get("vat_rate", 0.15))
        vat = round(subtotal * vat_rate, 2)
        grand = round(subtotal + vat, 2)
        inv_id = f"INV-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"

        self.db_service.save_invoice_transaction(inv_id, subtotal, vat, grand,
            succeeded, customer_id=self._selected_customer_id)

        for row in self.cart_rows_tracked:
            try:
                row.destroy()
            except Exception:
                pass
        self.cart_rows_tracked.clear()
        self.invoice_cart = []
        self.refresh_cart_list()

        msg = f"✅ Πώληση ολοκληρώθηκε!\n\nΑρ. Παραστατικού: {inv_id}\nΣύνολο: €{grand:.2f}"
        messagebox.showinfo("Επιτυχής Πώληση", msg)

    # ==================================================================
    # Customer selector
    # ==================================================================
    def _refresh_pos_customer_list(self):
        if not hasattr(self, 'pos_customer_menu'):
            return
        try:
            customers = self.db_service.get_all_customers()
            names = ["Λιανική Πώληση (Κανένας)"] + [f"{c['id']}: {c['name']}" for c in customers]
            current = self.pos_customer_var.get()
            self.pos_customer_menu.configure(values=names)
            if current in names:
                self.pos_customer_var.set(current)
            else:
                self.pos_customer_var.set("Λιανική Πώληση (Κανένας)")
                self._selected_customer_id = None
        except Exception:
            pass

    def _on_pos_customer_selected(self, choice: str):
        if choice.startswith("Λιανική"):
            self._selected_customer_id = None
            if hasattr(self, 'cust_popup') and self.cust_popup.winfo_exists():
                self.cust_popup.destroy()
        else:
            try:
                self._selected_customer_id = int(choice.split(":")[0])
                customer_name = choice.split(": ", 1)[1] if ": " in choice else choice
                self.after(80, lambda: self.show_customer_history_popup(
                    self._selected_customer_id, customer_name))
            except (ValueError, IndexError):
                self._selected_customer_id = None

    def show_customer_history_popup(self, customer_id: int, customer_name: str):
        if hasattr(self, 'cust_popup') and self.cust_popup.winfo_exists():
            self.cust_popup.destroy()

        self.cust_popup = ctk.CTkToplevel(self)
        self.cust_popup.title("👤 Ιστορικό Αγορών")
        self.cust_popup.geometry("450x320")
        self.cust_popup.resizable(False, False)
        self.cust_popup.transient(self.master)  # master is the parent view container
        self.cust_popup.after(100, lambda: self.cust_popup.lift())

        ctk.CTkLabel(self.cust_popup, text=f"Πρόσφατες αγορές: {customer_name}",
            font=ctk.CTkFont(size=13, weight="bold")).pack(pady=15)

        scroll = ctk.CTkScrollableFrame(self.cust_popup, width=410, height=220)
        scroll.pack(padx=20, pady=(0, 15), fill="both", expand=True)

        loading = ctk.CTkLabel(scroll, text="🔄 Ανάκτηση ιστορικού από τη βάση δεδομένων...",
            font=ctk.CTkFont(size=12))
        loading.pack(pady=20)

        def _fetch():
            try:
                rows = self.db_service.get_customer_purchase_history(customer_id)
                self.after(0, lambda: _render(rows))
            except Exception as e:
                self.after(0, lambda: loading.configure(text=f"Σφάλμα: {e}"))

        def _render(rows):
            for w in scroll.winfo_children():
                w.destroy()
            if not rows:
                ctk.CTkLabel(scroll, text="Δεν βρέθηκαν παλαιότερες αγορές για τον συγκεκριμένο πελάτη.",
                    font=ctk.CTkFont(size=11), wraplength=380).pack(pady=20)
                return
            for r in rows:
                line = f"📅 [{r['date']}] - {r['name']} (Τεμ: {r['qty']} - €{r['price']:.2f})"
                ctk.CTkLabel(scroll, text=line, font=ctk.CTkFont(size=11),
                    anchor="w", justify="left").pack(anchor="w", pady=2)
                ctk.CTkFrame(scroll, height=1, fg_color=("gray85", "gray25")).pack(fill="x", pady=1)

        threading.Thread(target=_fetch, daemon=True).start()

    # ==================================================================
    # Export
    # ==================================================================
    def export_cart(self):
        filter_text = self.pos_export_filter.get().strip().lower()
        limit_str = self.pos_export_limit.get().strip().upper()
        fmt = self.pos_export_format.get()
        is_csv = "csv" in fmt.lower()

        def _write():
            try:
                items = [(p, q) for p, q in self.invoice_cart]
                if filter_text:
                    items = [(p, q) for p, q in items if filter_text in p.name.lower() or filter_text in p.barcode.lower()]
                try:
                    limit = int(limit_str)
                    items = items[:limit]
                except ValueError:
                    pass
                subtotal = sum(p.price * q for p, q in items)
                total_qty = sum(q for _, q in items)
                vat_rate = float(self.config.get("vat_rate", 0.15))
                vat = subtotal * vat_rate
                grand = subtotal + vat
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                if is_csv:
                    dest = os.path.join(os.path.expanduser("~"), "Desktop", f"POS_Cart_Export_{ts}.csv")
                    lines = ["Barcode,Όνομα,Ποσότητα,Τιμή Μον.,Σύνολο"]
                    for p, q in items:
                        lines.append(f'{BaseView._csv_cell(p.barcode)},{BaseView._csv_cell(p.name)},{q},{p.price:.2f},{p.price*q:.2f}')
                    lines += ["", f"Υποσύνολο,,{total_qty},,{subtotal:.2f}",
                              f"ΦΠΑ {vat_rate*100:.1f}%,,,,{vat:.2f}", f"ΓΕΝΙΚΟ ΣΥΝΟΛΟ,,,,{grand:.2f}"]
                    with open(dest, "w", encoding="utf-8-sig") as f:
                        f.write("\n".join(lines))
                else:
                    dest = os.path.join(os.path.expanduser("~"), "Desktop", f"POS_Προσφορά_{ts}.txt")
                    lines = ["=" * 55, "  ENCOMM — ΠΡΟΣΦΟΡΑ / PROFORMA", "=" * 55,
                             f"Ημ/νία: {datetime.now().strftime('%d/%m/%Y %H:%M')}",
                             f"Είδη: {len(items)}  |  Τεμάχια: {total_qty}", "-" * 55]
                    lines.append(f"{'Barcode':<14} {'Όνομα':<22} {'Ποσ.':<6} {'Τιμή':<8} {'Σύνολο':<10}")
                    lines.append("-" * 55)
                    for p, q in items:
                        lines.append(f"{p.barcode:<14} {p.name[:22]:<22} {q:<6} €{p.price:<7.2f} €{p.price*q:<9.2f}")
                    lines.append("-" * 55)
                    lines.extend([f"{'Υποσύνολο:':<42} €{subtotal:.2f}",
                                  f"ΦΠΑ ({vat_rate*100:.1f}%):".ljust(42) + f" €{vat:.2f}",
                                  f"{'ΓΕΝΙΚΟ ΣΥΝΟΛΟ:':<42} €{grand:.2f}", "=" * 55])
                    with open(dest, "w", encoding="utf-8") as f:
                        f.write("\n".join(lines))
                self.after(0, lambda: messagebox.showinfo("Επιτυχής Εξαγωγή",
                    f"Το αρχείο αποθηκεύτηκε στην Επιφάνεια Εργασίας!"))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Σφάλμα Εξαγωγής", str(e)))
        threading.Thread(target=_write, daemon=True).start()

    def export_pos_sales(self):
        start_date = self.pos_sales_export_start.get().strip() if hasattr(self, 'pos_sales_export_start') else ""
        end_date = self.pos_sales_export_end.get().strip() if hasattr(self, 'pos_sales_export_end') else ""
        fmt = self.pos_sales_export_format.get()
        is_csv = "csv" in fmt.lower()

        def _write():
            try:
                invoices = self.db_service.get_all_invoices(
                    search_id="", start_date=start_date or None, end_date=end_date or None)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                if is_csv:
                    dest = os.path.join(os.path.expanduser("~"), "Desktop", f"POS_Sales_{ts}.csv")
                    lines = ["Αρ.Παραστατικού,Ημερομηνία,Υποσύνολο,ΦΠΑ,Γενικό Σύνολο,Πελάτης"]
                    for inv in invoices:
                        lines.append(f'{BaseView._csv_cell(inv["id"])},{BaseView._csv_cell(inv["date"])},{inv["subtotal"]:.2f},{inv["vat"]:.2f},{inv["total"]:.2f},{BaseView._csv_cell(inv["customer_name"])}')
                    with open(dest, "w", encoding="utf-8-sig") as f:
                        f.write("\n".join(lines))
                else:
                    dest = os.path.join(os.path.expanduser("~"), "Desktop", f"POS_Sales_{ts}.txt")
                    lines = ["=" * 65, "  ENCOMM — ΑΝΑΦΟΡΑ ΠΩΛΗΣΕΩΝ", "=" * 65,
                             f"Ημ/νία: {datetime.now().strftime('%d/%m/%Y %H:%M')}",
                             f"Παραστατικά: {len(invoices)}", "=" * 65]
                    total_revenue = 0.0
                    for inv in invoices:
                        total_revenue += inv["total"]
                        lines.append(f"\n📋 {inv['id']}  |  {inv['date']}  |  Πελάτης: {inv['customer_name'] or 'Λιανική'}")
                        lines.append(f"   Υποσύνολο: €{inv['subtotal']:.2f}  |  ΦΠΑ: €{inv['vat']:.2f}  |  Σύνολο: €{inv['total']:.2f}")
                        items = self.db_service.get_invoice_items(inv["id"])
                        if items:
                            for it in items:
                                qty = it.get("quantity", 0)
                                price = it.get("price", 0.0)
                                lines.append(f"   - {it.get('name', '')[:35]:<35} x{qty:<4} €{qty*price:.2f}")
                    lines += [f"\n{'=' * 65}", f"  ΣΥΝΟΛΟ ΕΣΟΔΩΝ: €{total_revenue:.2f}", f"{'=' * 65}"]
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
        if hasattr(self, 'pos_prod_menu'):
            try:
                self.pos_prod_menu.delete(0, "end")
            except Exception:
                pass
        self._refresh_pos_customer_list()
        self.refresh_cart_list()
