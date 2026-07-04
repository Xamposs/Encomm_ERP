import tkinter as tk
import os
import time
import logging
import threading
from tkinter import messagebox
from tkinter import ttk
import customtkinter
from datetime import datetime, date
from typing import Dict, Any, List, Tuple

from core.domain_models import Product, Invoice
from core.business_rules import (
    is_low_stock,
    is_expired,
    is_near_expiry,
    calculate_vat,
    calculate_invoice_totals,
    get_days_until_expiry,
)
from infrastructure.database_service import DatabaseService
from infrastructure.license_service import generate_hwid, verify_local_license, generate_license_key
from infrastructure.ai_service import AIService
from core.intent_factory import IntentFactory
from core.undo_stack import ActionHistory

from presentation.views import (
    DashboardView, InventoryView, POSView, SettingsView, AIView,
    CustomersView, SuppliersView, InvoiceHistoryView, StockMovementsView,
)

# Configure CustomTkinter behavior
customtkinter.set_appearance_mode("Dark")
customtkinter.set_default_color_theme("blue")


# ---------------------------------------------------------------------------
# Theme-aware colour helpers (module-level — used by sidebar + ttk style)
# ---------------------------------------------------------------------------
def _is_dark_mode() -> bool:
    return customtkinter.get_appearance_mode() == "Dark"


def _zebra_row(index: int) -> tuple:
    if index % 2 == 0:
        return ("#F0F2F5", "#16191E")
    return ("#E0E3E8", "#22252C")


def _header_bg() -> tuple:
    return ("gray75", "gray20")


def _header_fg() -> tuple:
    return ("gray30", "gray80")


def _csv_cell(val) -> str:
    s = str(val)
    if "," in s or '"' in s or "\n" in s:
        s = '"' + s.replace('"', '""') + '"'
    return s


def _nav_hover() -> tuple:
    return ("gray80", "gray25")


def _nav_text() -> tuple:
    return ("gray40", "gray70")


def _nav_active_bg() -> tuple:
    return ("#D0DAFF", "#252b36")


def _nav_active_text() -> tuple:
    return ("#1D4ED8", "#3B82F6")


def _stat_border_default() -> tuple:
    return ("#C8CCD4", "#2b303c")


def _body_text() -> tuple:
    return ("gray20", "gray90")


def _ttk_bg() -> str:
    return "#242424" if _is_dark_mode() else "#f0f0f0"


def _ttk_fg() -> str:
    return "#ffffff" if _is_dark_mode() else "#000000"


def _ttk_selected_bg() -> str:
    return "#3a3a3a" if _is_dark_mode() else "#d0d7ff"


def _subtle_text() -> tuple:
    return ("gray55", "gray50")


def _card_title_text() -> tuple:
    return ("gray45", "gray60")


# ============================================================================
# MAIN WINDOW — Shell: sidebar + frame switching only
# ============================================================================
class MainWindow(customtkinter.CTk):
    def __init__(self, db_service: DatabaseService, config: Dict[str, Any]):
        super().__init__()

        self.db_service = db_service
        self.config = config

        self.title("ENCOMM Mini-ERP 🧪")
        self.geometry("1150x730")
        self.minsize(1050, 650)

        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.active_frame = None
        self.current_frame_name = None

        # AI filter flags
        self._filter_low_stock = False
        self._filter_expiry = False
        self.cached_hwid = None

        # Undo/Redo
        self.action_history = ActionHistory()

        self.frame_attrs = {
            "dashboard": "dashboard_view",
            "inventory": "inventory_view",
            "invoices": "pos_view",
            "settings": "settings_view",
            "ai_assistant": "ai_view",
            "customers": "customers_view",
            "invoice_history": "invoice_history_view",
            "suppliers": "suppliers_view",
            "stock_movements": "stock_movements_view",
        }

        self._active_timers = []
        self.protocol("WM_DELETE_WINDOW", self.on_safe_close)

        self.after(0, self._post_init)

        self.configure(takefocus=True)

    @staticmethod
    def _on_background_click(event):
        """Focus out of entry fields when clicking blank content area."""
        try:
            w = event.widget
            if w and "entry" not in w.__class__.__name__.lower():
                w.master.focus_set() if hasattr(w, 'master') else None
        except Exception:
            pass

    def _post_init(self):
        self._apply_global_ttk_style()

        def _fetch_hwid_bg():
            val = generate_hwid()
            self.after(0, lambda: self._set_cached_hwid(val))
        self.after(1000, lambda: threading.Thread(target=_fetch_hwid_bg, daemon=True).start())

        self._init_sidebar()
        self._init_main_panel()
        self._init_views()

        # Bind background click to content area only (NOT globally).
        # Global bind_all causes resize stutter on Windows.
        self.main_container.bind("<Button-1>", self._on_background_click, add="+")

        self.after(50, lambda: self._ensure_frame("dashboard"))
        self.after(100, lambda: self.select_frame("dashboard"))

        self._update_clock()

    def _set_cached_hwid(self, val: str):
        self.cached_hwid = val
        if hasattr(self, 'settings_view') and hasattr(self.settings_view, 'set_hwid_entry'):
            try:
                self.settings_view.set_hwid_entry.configure(state="normal")
                self.settings_view.set_hwid_entry.delete(0, "end")
                self.settings_view.set_hwid_entry.insert(0, val)
                self.settings_view.set_hwid_entry.configure(state="disabled")
            except Exception:
                pass

    def on_safe_close(self):
        if hasattr(self, 'settings_view') and hasattr(self.settings_view, 'set_autobackup_var'):
            if self.settings_view.set_autobackup_var.get():
                try:
                    path = self.db_service.backup_database()
                    logging.info("Auto-backup on close: %s", path)
                except Exception as e:
                    logging.error("Auto-backup on close failed: %s", e)

        logging.info("Safe exit triggered — hard OS-level kill...")
        try:
            if hasattr(self, '_search_timer') and self._search_timer is not None:
                self.after_cancel(self._search_timer)
            for _tid in getattr(self, '_active_timers', []):
                try:
                    self.after_cancel(_tid)
                except Exception:
                    pass
        except Exception:
            pass
        os._exit(0)

    # =========================================================================
    # SIDEBAR
    # =========================================================================
    def _init_sidebar(self):
        self.sidebar_frame = customtkinter.CTkFrame(self, corner_radius=0, width=220)
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
        self.sidebar_frame.grid_rowconfigure(9, weight=1)

        self.brand_label = customtkinter.CTkLabel(
            self.sidebar_frame, text="ENCOMM ERP 🧪",
            font=customtkinter.CTkFont(family="Outfit", size=20, weight="bold"))
        self.brand_label.grid(row=0, column=0, padx=20, pady=(45, 30))

        self.nav_buttons = {}

        nav_items = [
            (1, "dashboard", "📊  Αρχική"),
            (2, "inventory", "📦  Αποθήκη"),
            (3, "suppliers", "🏭  Προμηθευτές"),
            (4, "invoices", "🧾  Ταμείο / Πωλήσεις"),
            (5, "customers", "👥  Πελάτες"),
            (6, "invoice_history", "🔎  Ιστορικό"),
            (7, "stock_movements", "📋  Κινήσεις"),
            (8, "settings", "⚙️  Ρυθμίσεις"),
            (9, "ai_assistant", "🤖  AI Βοηθός"),
        ]
        for row, name, text in nav_items:
            self.nav_buttons[name] = customtkinter.CTkButton(
                self.sidebar_frame, text=text, anchor="w",
                fg_color="transparent", text_color=_nav_text(),
                hover_color=_nav_hover(),
                font=customtkinter.CTkFont(family="Outfit", size=13, weight="normal"),
                command=lambda n=name: self.select_frame(n))
            self.nav_buttons[name].grid(row=row, column=0, padx=20, pady=8, sticky="ew")

        self.version_label = customtkinter.CTkLabel(
            self.sidebar_frame, text="v1.0.0 Stable | ENCOMM Tensor Intelligence",
            font=customtkinter.CTkFont(size=11), text_color=_subtle_text())
        self.version_label.grid(row=9, column=0, padx=20, pady=(120, 15))

    # =========================================================================
    # MAIN PANEL
    # =========================================================================
    def _init_main_panel(self):
        self.main_container = customtkinter.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.main_container.grid(row=0, column=1, sticky="nsew", padx=35, pady=35)
        self.main_container.grid_columnconfigure(0, weight=1)
        self.main_container.grid_rowconfigure(0, weight=0)
        self.main_container.grid_rowconfigure(1, weight=1)

        # HEADER
        self.header_frame = customtkinter.CTkFrame(self.main_container, fg_color="transparent")
        self.header_frame.grid(row=0, column=0, sticky="ew", pady=(0, 25))
        self.header_frame.grid_columnconfigure(0, weight=1)

        self.section_title_label = customtkinter.CTkLabel(
            self.header_frame, text="Αρχική",
            font=customtkinter.CTkFont(family="Outfit", size=26, weight="bold"))
        self.section_title_label.grid(row=0, column=0, sticky="w")

        self.header_right_bar = customtkinter.CTkFrame(self.header_frame, fg_color="transparent")
        self.header_right_bar.grid(row=0, column=1, sticky="e")

        self.clock_label = customtkinter.CTkLabel(
            self.header_right_bar, text="",
            font=customtkinter.CTkFont(family="Courier", size=14), text_color="#34C759")
        self.clock_label.pack(side="left", padx=(0, 12))

        self.undo_btn = customtkinter.CTkButton(
            self.header_right_bar, text="↩", width=36, height=36,
            fg_color="transparent", text_color=_nav_text(),
            hover_color=_nav_hover(), font=customtkinter.CTkFont(size=16),
            command=self._undo_last_action, state="disabled")
        self.undo_btn.pack(side="left", padx=(0, 4))

        self.redo_btn = customtkinter.CTkButton(
            self.header_right_bar, text="↪", width=36, height=36,
            fg_color="transparent", text_color=_nav_text(),
            hover_color=_nav_hover(), font=customtkinter.CTkFont(size=16),
            command=self._redo_last_action, state="disabled")
        self.redo_btn.pack(side="left")

        # AI Command Bar
        self.ai_cmd_bar = customtkinter.CTkEntry(
            self.header_frame,
            placeholder_text="💡 Πείτε στο Encomm AI τι θέλετε να κάνετε... (π.χ. 'Δείξε μου τις ελλείψεις')",
            height=38, font=customtkinter.CTkFont(size=13),
            fg_color=("#E8ECF1", "#1A1D24"), border_color=("#A0B4D0", "#3B5068"), border_width=1)
        self.ai_cmd_bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        self.ai_cmd_bar.bind("<Return>", lambda e: self.process_ai_command())

        self.ai_status_lbl = customtkinter.CTkLabel(
            self.header_frame, text="", font=customtkinter.CTkFont(size=11),
            text_color=_subtle_text())
        self.ai_status_lbl.grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))

        self.ai_service = None
        self._ai_service_lock = threading.Lock()
        self.intent_factory = IntentFactory()

        # ── Lazy frame placeholders ──
        self.dashboard_frame = None
        self.inventory_frame = None
        self.invoices_frame = None
        self.settings_frame = None
        self.ai_assistant_frame = None
        self.customers_frame = None
        self.suppliers_frame = None
        self.invoice_history_frame = None
        self.stock_movements_frame = None

        # ── View references (set in _init_views) ──
        self.dashboard_view = None
        self.inventory_view = None
        self.pos_view = None
        self.settings_view = None
        self.ai_view = None

    # =========================================================================
    # VIEWS — Instantiate all 5 view classes
    # =========================================================================
    def _init_views(self):
        """Create view objects but don't grid them yet — handled by _ensure_frame/frame switching."""
        self.dashboard_view = DashboardView(
            self.main_container, self.db_service, self.config,
            fg_color="transparent")
        # Wire dashboard import button to inventory's import method
        self.dashboard_view.import_btn.configure(command=self._on_dashboard_import)

        self.inventory_view = InventoryView(
            self.main_container, self.db_service, self.config,
            on_data_changed=self._on_inventory_data_changed,
            fg_color="transparent")

        self.pos_view = POSView(
            self.main_container, self.db_service, self.config,
            fg_color="transparent")

        self.settings_view = SettingsView(
            self.main_container, self.db_service, self.config,
            fg_color="transparent")
        self.settings_view._on_settings_saved = self._on_settings_saved

        self.ai_view = AIView(
            self.main_container, self.db_service, self.config,
            fg_color="transparent")

        # ── New refactored Views ──
        self.customers_view = CustomersView(
            self.main_container, self.db_service, self.config,
            fg_color="transparent")

        self.suppliers_view = SuppliersView(
            self.main_container, self.db_service, self.config,
            fg_color="transparent")

        self.invoice_history_view = InvoiceHistoryView(
            self.main_container, self.db_service, self.config,
            fg_color="transparent")

        self.stock_movements_view = StockMovementsView(
            self.main_container, self.db_service, self.config,
            fg_color="transparent")

        # Map old frame attributes to new view objects
        self.dashboard_frame = self.dashboard_view
        self.inventory_frame = self.inventory_view
        self.invoices_frame = self.pos_view
        self.settings_frame = self.settings_view
        self.ai_assistant_frame = self.ai_view

    def _on_dashboard_import(self):
        """Delegate dashboard import button to inventory's import method."""
        if self.inventory_view:
            self.inventory_view.import_supplier_invoice()

    def _on_inventory_data_changed(self):
        """Cross-view refresh: inventory change → dashboard + POS."""
        if self.dashboard_view:
            self.dashboard_view.refresh()
        if self.pos_view:
            self.pos_view.refresh()

    def _on_settings_saved(self):
        """Cross-view refresh: settings saved → dashboard + inventory + POS."""
        if self.dashboard_view:
            self.dashboard_view.refresh()
        if self.inventory_view:
            self.inventory_view.refresh()
        if self.pos_view:
            self.pos_view.refresh()

    # =========================================================================
    # UNDO / REDO
    # =========================================================================
    def _update_undo_redo_buttons(self):
        if hasattr(self, 'undo_btn') and self.undo_btn is not None:
            self.undo_btn.configure(state="normal" if self.action_history.can_undo else "disabled")
        if hasattr(self, 'redo_btn') and self.redo_btn is not None:
            self.redo_btn.configure(state="normal" if self.action_history.can_redo else "disabled")

    def _undo_last_action(self):
        desc = self.action_history.undo()
        if desc is None:
            return
        self._update_undo_redo_buttons()
        messagebox.showinfo("Αναίρεση", f"Η ενέργεια αναιρέθηκε:\n{desc}")

    def _redo_last_action(self):
        desc = self.action_history.redo()
        if desc is None:
            return
        self._update_undo_redo_buttons()
        messagebox.showinfo("Επανάληψη", f"Η ενέργεια επαναλήφθηκε:\n{desc}")

    # =========================================================================
    # AI BACKEND
    # =========================================================================
    def _get_ai_service(self):
        if self.ai_service is None:
            with self._ai_service_lock:
                if self.ai_service is None:
                    self.ai_service = AIService(self.db_service)
        return self.ai_service

    def process_ai_command(self):
        """Process the AI command bar entry via IntentFactory."""
        text = self.ai_cmd_bar.get().strip()
        if not text:
            return
        self.ai_cmd_bar.delete(0, "end")
        try:
            intent = self.intent_factory.parse(text)
        except Exception:
            intent = None

        if intent:
            self.ai_status_lbl.configure(text=f"✅ Εντολή: {intent['label']}")
            if intent.get("action") == "filter_low_stock":
                self._filter_low_stock = True
                self._filter_expiry = False
                self.select_frame("inventory")
            elif intent.get("action") == "filter_expiry":
                self._filter_expiry = True
                self._filter_low_stock = False
                self.select_frame("inventory")
            elif intent.get("action") == "show_dashboard":
                self.select_frame("dashboard")
            elif intent.get("action") == "open_pos":
                self.select_frame("invoices")
        else:
            self.ai_status_lbl.configure(text="⚠️ Μη αναγνωρίσιμη εντολή.")

    # =========================================================================
    # FRAME SWITCHING
    # =========================================================================
    def _ensure_frame(self, name: str):
        """Return the view frame, building it once on first access."""
        attr = self.frame_attrs.get(name)
        if not attr:
            return None
        frame = getattr(self, attr, None)
        if frame is not None:
            return frame

        # Lazy-build on first click (suppliers, customers, invoice_history, stock_movements)
        init_map = {
            "dashboard":       lambda: None,
            "inventory":       lambda: None,
            "invoices":        lambda: None,
            "settings":        lambda: None,
            "ai_assistant":    lambda: None,
            "customers":       self._init_customers_frame,
            "invoice_history": self._init_invoice_history_frame,
            "suppliers":       self._init_suppliers_frame,
            "stock_movements": self._init_stock_movements_frame,
        }
        if name in init_map:
            fn = init_map[name]
            if fn:
                fn()
                frame = getattr(self, attr)
                if frame:
                    frame.grid(row=1, column=0, sticky="nsew")
                    frame.grid_remove()
                    return frame
        return None

    def select_frame(self, name: str):
        if self.current_frame_name == name:
            return

        self.current_frame_name = name

        for btn_name, btn in self.nav_buttons.items():
            if btn_name == name:
                btn.configure(
                    fg_color=_nav_active_bg(), text_color=_nav_active_text(),
                    font=customtkinter.CTkFont(family="Outfit", size=13, weight="bold"))
            else:
                btn.configure(
                    fg_color="transparent", text_color=_nav_text(),
                    font=customtkinter.CTkFont(family="Outfit", size=13, weight="normal"))

        if hasattr(self, "active_frame") and self.active_frame is not None:
            self.active_frame.grid_remove()

        target_frame = self._ensure_frame(name)
        if target_frame is None:
            return

        frame_titles = {
            "dashboard": "Επισκόπηση Συστήματος",
            "inventory": "Διαχείριση Αποθήκης",
            "invoices": "Ταμείο / Πωλήσεις (POS)",
            "settings": "Ρυθμίσεις Συστήματος",
            "ai_assistant": "AI Βοηθός",
            "customers": "Μητρώο Πελατών",
            "invoice_history": "Ιστορικό Παραστατικών",
            "suppliers": "Μητρώο Προμηθευτών",
            "stock_movements": "Κινήσεις Αποθέματος",
        }
        refresh_fns = {
            "dashboard": lambda: self.dashboard_view.refresh() if self.dashboard_view else None,
            "inventory": lambda: self.inventory_view.refresh() if self.inventory_view else None,
            "invoices": lambda: self.pos_view.refresh() if self.pos_view else None,
            "settings": lambda: self.settings_view.load_settings_values() if self.settings_view else None,
            "ai_assistant": None,
            "customers": lambda: self.customers_view.refresh() if self.customers_view else None,
            "invoice_history": lambda: self.invoice_history_view.refresh() if self.invoice_history_view else None,
            "suppliers": lambda: self.suppliers_view.refresh() if self.suppliers_view else None,
            "stock_movements": lambda: self.stock_movements_view.refresh() if self.stock_movements_view else None,
        }

        self.section_title_label.configure(text=frame_titles.get(name, name))
        target_frame.grid(row=1, column=0, sticky="nsew")
        target_frame.tkraise()
        self.active_frame = target_frame

        if refresh_fns.get(name):
            refresh_fns[name]()

    # =========================================================================
    # CLOCK
    # =========================================================================
    def _update_clock(self):
        now_str = datetime.now().strftime("%A, %Y-%m-%d %H:%M:%S")
        self.clock_label.configure(text=f"🕒  {now_str}")
        self.after(1000, self._update_clock)

    # =========================================================================
    # GLOBAL TTK STYLE
    # =========================================================================
    @staticmethod
    def _apply_global_ttk_style():
        _style = ttk.Style()
        _style.theme_use("clam")
        bg = _ttk_bg()
        fg = _ttk_fg()
        sel_bg = _ttk_selected_bg()
        _style.configure("Treeview", background=bg, foreground=fg, fieldbackground=bg,
                          rowheight=30, font=("Segoe UI", 13))
        _style.configure("Treeview.Heading", background=bg, foreground=fg,
                          font=("Segoe UI", 14, "bold"))
        _style.map("Treeview", background=[("selected", sel_bg)],
                    foreground=[("selected", "#ffffff")])

    # =========================================================================
    # LEGACY TAB FRAMES (not yet ported to views — preserve original behaviour)
    # =========================================================================
    def _init_customers_frame(self):
        self.customers_frame = customtkinter.CTkFrame(self.main_container, fg_color="transparent")
        self.customers_frame.grid_columnconfigure(0, weight=1)
        self.customers_frame.grid_rowconfigure(2, weight=1)

        search_bar = customtkinter.CTkFrame(self.customers_frame, fg_color="transparent")
        search_bar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.cust_search_entry = customtkinter.CTkEntry(search_bar, placeholder_text="Αναζήτηση πελάτη...", width=300)
        self.cust_search_entry.pack(side="left", padx=(0, 10))
        self.cust_search_entry.bind("<KeyRelease>", lambda e: self.refresh_customer_list())
        customtkinter.CTkButton(search_bar, text="🔍", width=40,
            command=self.refresh_customer_list).pack(side="left")

        form = customtkinter.CTkFrame(self.customers_frame, fg_color="transparent")
        form.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        customtkinter.CTkLabel(form, text="Όνομα:", font=customtkinter.CTkFont(weight="bold")).grid(row=0, column=0, padx=(0,5))
        self.cust_name_entry = customtkinter.CTkEntry(form, width=200)
        self.cust_name_entry.grid(row=0, column=1, padx=5)
        customtkinter.CTkLabel(form, text="ΑΜΚΑ:", font=customtkinter.CTkFont(weight="bold")).grid(row=0, column=2, padx=5)
        self.cust_amka_entry = customtkinter.CTkEntry(form, width=150)
        self.cust_amka_entry.grid(row=0, column=3, padx=5)
        customtkinter.CTkLabel(form, text="Τηλ:", font=customtkinter.CTkFont(weight="bold")).grid(row=0, column=4, padx=5)
        self.cust_phone_entry = customtkinter.CTkEntry(form, width=150)
        self.cust_phone_entry.grid(row=0, column=5, padx=5)
        customtkinter.CTkButton(form, text="💾 Αποθήκευση", fg_color=("#2ecc71", "#27ae60"),
            hover_color=("#27ae60", "#1e8449"), text_color=("#FFFFFF", "#FFFFFF"),
            command=self.save_customer).grid(row=0, column=6, padx=(10, 0))
        customtkinter.CTkButton(form, text="❌ Διαγραφή Πελάτη", fg_color=("#E74C3C", "#C0392B"),
            hover_color=("#C0392B", "#A93226"), text_color=("#FFFFFF", "#FFFFFF"),
            command=self.delete_customer).grid(row=0, column=7, padx=(10, 0))

        self.cust_tree = ttk.Treeview(self.customers_frame,
            columns=("id", "name", "amka", "phone"), show="headings", height=15)
        self.cust_tree.heading("id", text="ID"); self.cust_tree.column("id", width=50)
        self.cust_tree.heading("name", text="Όνομα"); self.cust_tree.column("name", width=250)
        self.cust_tree.heading("amka", text="ΑΜΚΑ"); self.cust_tree.column("amka", width=120)
        self.cust_tree.heading("phone", text="Τηλέφωνο"); self.cust_tree.column("phone", width=120)
        self.cust_tree.grid(row=2, column=0, sticky="nsew")

    def refresh_customer_list(self):
        if not hasattr(self, 'customers_frame') or self.customers_frame is None:
            return
        query = self.cust_search_entry.get().strip() if hasattr(self, 'cust_search_entry') else ""
        rows = self.db_service.search_customers(query) if query else self.db_service.get_all_customers()
        self.cust_tree.delete(*self.cust_tree.get_children())
        for r in rows:
            self.cust_tree.insert("", "end", values=(r["id"], r["name"], r["amka"], r["phone"]))

    def save_customer(self):
        name = self.cust_name_entry.get().strip()
        amka = self.cust_amka_entry.get().strip()
        phone = self.cust_phone_entry.get().strip()
        if not name:
            messagebox.showwarning("Προειδοποίηση", "Το όνομα είναι υποχρεωτικό.")
            return
        if self.db_service.add_customer(name, amka, phone):
            self.cust_name_entry.delete(0, "end"); self.cust_amka_entry.delete(0, "end"); self.cust_phone_entry.delete(0, "end")
            self.refresh_customer_list()
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
            messagebox.showinfo("Επιτυχία", f"Ο πελάτης '{name}' διαγράφηκε επιτυχώς.")
            self.refresh_customer_list()

    def _init_suppliers_frame(self):
        self.suppliers_frame = customtkinter.CTkFrame(self.main_container, fg_color="transparent")
        self.suppliers_frame.grid_columnconfigure(0, weight=1)
        self.suppliers_frame.grid_rowconfigure(2, weight=1)

        search_bar = customtkinter.CTkFrame(self.suppliers_frame, fg_color="transparent")
        search_bar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.supp_search_entry = customtkinter.CTkEntry(search_bar, placeholder_text="Αναζήτηση προμηθευτή...", width=300)
        self.supp_search_entry.pack(side="left", padx=(0, 10))
        customtkinter.CTkButton(search_bar, text="🔍", width=40,
            command=self.refresh_supplier_list).pack(side="left")

        form = customtkinter.CTkFrame(self.suppliers_frame, fg_color="transparent")
        form.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        customtkinter.CTkLabel(form, text="Όνομα:", font=customtkinter.CTkFont(weight="bold")).grid(row=0, column=0, padx=(0,3))
        self.supp_name_entry = customtkinter.CTkEntry(form, width=180)
        self.supp_name_entry.grid(row=0, column=1, padx=3)
        customtkinter.CTkLabel(form, text="Τηλ:", font=customtkinter.CTkFont(weight="bold")).grid(row=0, column=2, padx=3)
        self.supp_phone_entry = customtkinter.CTkEntry(form, width=140)
        self.supp_phone_entry.grid(row=0, column=3, padx=3)
        customtkinter.CTkLabel(form, text="Email:", font=customtkinter.CTkFont(weight="bold")).grid(row=0, column=4, padx=3)
        self.supp_email_entry = customtkinter.CTkEntry(form, width=180)
        self.supp_email_entry.grid(row=0, column=5, padx=3)
        customtkinter.CTkLabel(form, text="Διεύθυνση:", font=customtkinter.CTkFont(weight="bold")).grid(row=0, column=6, padx=3)
        self.supp_address_entry = customtkinter.CTkEntry(form, width=180)
        self.supp_address_entry.grid(row=0, column=7, padx=3)
        customtkinter.CTkButton(form, text="💾 Αποθήκευση", fg_color=("#2ecc71", "#27ae60"),
            hover_color=("#27ae60", "#1e8449"), text_color=("#FFFFFF", "#FFFFFF"),
            command=self.save_supplier).grid(row=0, column=8, padx=(10, 0))
        customtkinter.CTkButton(form, text="❌ Διαγραφή", fg_color=("#E74C3C", "#C0392B"),
            hover_color=("#C0392B", "#A93226"), text_color=("#FFFFFF", "#FFFFFF"),
            command=self.delete_supplier).grid(row=0, column=9, padx=(10, 0))

        self.supp_order_btn = customtkinter.CTkButton(self.suppliers_frame, text="📋 Αυτόματη Λίστα Παραγγελίας",
            fg_color="#8E44AD", hover_color="#7D3C98", font=customtkinter.CTkFont(weight="bold"),
            command=self.generate_automated_orders)
        self.supp_order_btn.grid(row=3, column=0, pady=(10, 0))

        self.supp_tree = ttk.Treeview(self.suppliers_frame,
            columns=("id", "name", "phone", "email", "address"), show="headings", height=15)
        self.supp_tree.heading("id", text="ID"); self.supp_tree.column("id", width=50)
        self.supp_tree.heading("name", text="Όνομα"); self.supp_tree.column("name", width=250)
        self.supp_tree.heading("phone", text="Τηλ"); self.supp_tree.column("phone", width=120)
        self.supp_tree.heading("email", text="Email"); self.supp_tree.column("email", width=200)
        self.supp_tree.heading("address", text="Διεύθυνση"); self.supp_tree.column("address", width=200)
        self.supp_tree.grid(row=2, column=0, sticky="nsew")

    def refresh_supplier_list(self):
        if not hasattr(self, 'suppliers_frame') or self.suppliers_frame is None:
            return
        rows = self.db_service.get_all_suppliers()
        self.supp_tree.delete(*self.supp_tree.get_children())
        for r in rows:
            self.supp_tree.insert("", "end", values=(r["id"], r["name"], r.get("phone", ""),
                r.get("email", ""), r.get("address", "")))

    def save_supplier(self):
        name = self.supp_name_entry.get().strip()
        phone = self.supp_phone_entry.get().strip()
        email = self.supp_email_entry.get().strip()
        address = self.supp_address_entry.get().strip()
        if not name:
            messagebox.showwarning("Προειδοποίηση", "Το όνομα είναι υποχρεωτικό.")
            return
        if self.db_service.add_supplier(name, phone, email, address):
            self.supp_name_entry.delete(0, "end"); self.supp_phone_entry.delete(0, "end")
            self.supp_email_entry.delete(0, "end"); self.supp_address_entry.delete(0, "end")
            self.refresh_supplier_list()
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
            messagebox.showinfo("Επιτυχία", f"Ο προμηθευτής '{name}' διαγράφηκε επιτυχώς.")
            self.refresh_supplier_list()

    def generate_automated_orders(self):
        import os as _os
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
                    dest = _os.path.join(_os.path.expanduser("~"), "Desktop", f"Order_{sname}_{ts}.csv")
                    lines = ["Barcode,Όνομα Προϊόντος,Τρέχον Στοκ,Προτεινόμενη Ποσότητα Παραγγελίας"]
                    for it in items:
                        suggested = max(50, (10 - it["stock"]) * 5) if it["stock"] < 10 else 50
                        lines.append(f'{it["barcode"]},{it["name"]},{it["stock"]},{suggested}')
                    with open(dest, "w", encoding="utf-8-sig") as f:
                        f.write("\n".join(lines))
                self.after(0, lambda: messagebox.showinfo("Έτοιμες Παραγγελίες",
                    "Οι λίστες ανεφοδιασμού δημιουργήθηκαν στο Desktop ομαδοποιημένες ανά προμηθευτή!"))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Σφάλμα", str(e)))
        threading.Thread(target=_write, daemon=True).start()

    def _init_invoice_history_frame(self):
        self.invoice_history_frame = customtkinter.CTkFrame(self.main_container, fg_color="transparent")
        self.invoice_history_frame.grid_columnconfigure(0, weight=1)
        self.invoice_history_frame.grid_rowconfigure(1, weight=1)

        filter_bar = customtkinter.CTkFrame(self.invoice_history_frame, fg_color="transparent")
        filter_bar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        customtkinter.CTkLabel(filter_bar, text="Αρ. Παρ/κού:", font=customtkinter.CTkFont(weight="bold")).pack(side="left", padx=(0,5))
        self.hist_id_entry = customtkinter.CTkEntry(filter_bar, width=140)
        self.hist_id_entry.pack(side="left", padx=(0, 10))
        customtkinter.CTkLabel(filter_bar, text="Από:", font=customtkinter.CTkFont(weight="bold")).pack(side="left", padx=(0,5))
        self.hist_start_entry = customtkinter.CTkEntry(filter_bar, width=110, placeholder_text="YYYY-MM-DD")
        self.hist_start_entry.pack(side="left", padx=(0, 10))
        customtkinter.CTkLabel(filter_bar, text="Έως:", font=customtkinter.CTkFont(weight="bold")).pack(side="left", padx=(0,5))
        self.hist_end_entry = customtkinter.CTkEntry(filter_bar, width=110, placeholder_text="YYYY-MM-DD")
        self.hist_end_entry.pack(side="left", padx=(0, 10))
        customtkinter.CTkButton(filter_bar, text="🔍 Αναζήτηση", command=self.refresh_invoice_history_list).pack(side="left")

        self.hist_tree = ttk.Treeview(self.invoice_history_frame,
            columns=("id", "date", "subtotal", "vat", "total", "customer"), show="headings", height=20)
        self.hist_tree.heading("id", text="Αρ. Παρ/κού"); self.hist_tree.column("id", width=150)
        self.hist_tree.heading("date", text="Ημερομηνία"); self.hist_tree.column("date", width=160)
        self.hist_tree.heading("subtotal", text="Υποσύνολο"); self.hist_tree.column("subtotal", width=100)
        self.hist_tree.heading("vat", text="ΦΠΑ"); self.hist_tree.column("vat", width=80)
        self.hist_tree.heading("total", text="Σύνολο"); self.hist_tree.column("total", width=100)
        self.hist_tree.heading("customer", text="Πελάτης"); self.hist_tree.column("customer", width=150)
        self.hist_tree.grid(row=1, column=0, sticky="nsew")
        self.hist_tree.bind("<Double-1>", self._on_invoice_double_click)

        # Export bar
        self.hist_export_bar = customtkinter.CTkFrame(self.invoice_history_frame, fg_color="transparent")
        self.hist_export_bar.grid(row=2, column=0, sticky="ew", pady=(10,0))
        self.hist_export_start = customtkinter.CTkEntry(self.hist_export_bar, width=110, placeholder_text="Από (YYYY-MM-DD)")
        self.hist_export_start.pack(side="left", padx=(0,5))
        self.hist_export_end = customtkinter.CTkEntry(self.hist_export_bar, width=110, placeholder_text="Έως (YYYY-MM-DD)")
        self.hist_export_end.pack(side="left", padx=(0,8))
        self.hist_export_format = customtkinter.CTkOptionMenu(self.hist_export_bar,
            values=["Excel (.csv)", "PDF (.txt style)"], width=140)
        self.hist_export_format.pack(side="left", padx=(0,8))
        customtkinter.CTkButton(self.hist_export_bar, text="📤 Εξαγωγή", fg_color="#2980B9",
            hover_color="#1F618D", font=customtkinter.CTkFont(weight="bold"),
            command=self.export_invoice_history).pack(side="left")

    def refresh_invoice_history_list(self):
        if not hasattr(self, 'invoice_history_frame') or self.invoice_history_frame is None:
            return
        search_id = self.hist_id_entry.get().strip() if hasattr(self, 'hist_id_entry') else ""
        start_date = self.hist_start_entry.get().strip() or None
        end_date = self.hist_end_entry.get().strip() or None
        rows = self.db_service.get_all_invoices(search_id=search_id, start_date=start_date, end_date=end_date)
        self.hist_tree.delete(*self.hist_tree.get_children())
        for r in rows:
            self.hist_tree.insert("", "end", values=(r["id"], r["date"],
                f"€{r['subtotal']:.2f}", f"€{r['vat']:.2f}", f"€{r['total']:.2f}", r.get("customer_name", "")))

    def _on_invoice_double_click(self, event):
        sel = self.hist_tree.selection()
        if not sel:
            return
        inv_id = self.hist_tree.item(sel[0])["values"][0]
        items = self.db_service.get_invoice_items(inv_id)
        popup = customtkinter.CTkToplevel(self)
        popup.title(f"Λεπτομέρειες {inv_id}")
        popup.geometry("550x350")
        popup.transient(self)
        tree = ttk.Treeview(popup, columns=("barcode", "name", "qty", "price", "row_total"), show="headings")
        tree.heading("barcode", text="Barcode"); tree.heading("name", text="Όνομα")
        tree.heading("qty", text="Ποσ."); tree.heading("price", text="Τιμή"); tree.heading("row_total", text="Σύνολο")
        tree.column("barcode", width=100); tree.column("name", width=180)
        tree.column("qty", width=60); tree.column("price", width=80); tree.column("row_total", width=80)
        tree.pack(padx=15, pady=15, fill="both", expand=True)
        total = 0.0
        for it in items:
            row_total = it["quantity"] * it["price"]
            total += row_total
            tree.insert("", "end", values=(it["barcode"], it["name"], it["quantity"], f"€{it['price']:.2f}", f"€{row_total:.2f}"))
        customtkinter.CTkLabel(popup, text=f"Γενικό Σύνολο: €{total:.2f}",
            font=customtkinter.CTkFont(size=14, weight="bold")).pack(pady=(0,15))
        customtkinter.CTkButton(popup, text="Κλείσιμο", command=popup.destroy).pack(pady=(0,10))

    def export_invoice_history(self):
        fmt = self.hist_export_format.get()
        is_csv = "csv" in fmt.lower()
        start_date = self.hist_export_start.get().strip() or ""
        end_date = self.hist_export_end.get().strip() or ""

        def _write():
            try:
                rows = self.db_service.get_all_invoices(start_date=start_date or None, end_date=end_date or None)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                if is_csv:
                    dest = os.path.join(os.path.expanduser("~"), "Desktop", f"InvoiceHistory_{ts}.csv")
                    lines = ["Αρ.Παραστατικού,Ημερομηνία,Υποσύνολο,ΦΠΑ,Σύνολο,Πελάτης"]
                    for r in rows:
                        lines.append(f'{_csv_cell(r["id"])},{_csv_cell(r["date"])},{r["subtotal"]:.2f},{r["vat"]:.2f},{r["total"]:.2f},{_csv_cell(r.get("customer_name",""))}')
                    with open(dest, "w", encoding="utf-8-sig") as f:
                        f.write("\n".join(lines))
                else:
                    dest = os.path.join(os.path.expanduser("~"), "Desktop", f"InvoiceHistory_{ts}.txt")
                    lines = ["=" * 55, "  ΙΣΤΟΡΙΚΟ ΠΑΡΑΣΤΑΤΙΚΩΝ", "=" * 55]
                    for r in rows:
                        lines.append(f"{r['id']} | {r['date']} | Υποσ:€{r['subtotal']:.2f} | ΦΠΑ:€{r['vat']:.2f} | Συν:€{r['total']:.2f} | {r.get('customer_name','')}")
                    lines.append("=" * 55)
                    with open(dest, "w", encoding="utf-8") as f:
                        f.write("\n".join(lines))
                self.after(0, lambda: messagebox.showinfo("Επιτυχής Εξαγωγή",
                    f"Το αρχείο αποθηκεύτηκε στην Επιφάνεια Εργασίας!"))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Σφάλμα Εξαγωγής", str(e)))
        threading.Thread(target=_write, daemon=True).start()

    def _init_stock_movements_frame(self):
        self.stock_movements_frame = customtkinter.CTkFrame(self.main_container, fg_color="transparent")
        self.stock_movements_frame.grid_columnconfigure(0, weight=1)
        self.stock_movements_frame.grid_rowconfigure(1, weight=1)

        filter_bar = customtkinter.CTkFrame(self.stock_movements_frame, fg_color="transparent")
        filter_bar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.sm_barcode_entry = customtkinter.CTkEntry(filter_bar, placeholder_text="Barcode...", width=150)
        self.sm_barcode_entry.pack(side="left", padx=(0, 5))
        self.sm_reason_var = tk.StringVar(value="")
        customtkinter.CTkOptionMenu(filter_bar, variable=self.sm_reason_var,
            values=["", "Εισαγωγή", "Πώληση", "Χειροκίνητη Ενημέρωση", "Επαναφορά"], width=150).pack(side="left", padx=5)
        customtkinter.CTkButton(filter_bar, text="🔍 Φίλτρο",
            command=self.refresh_stock_movements).pack(side="left", padx=5)

        self.sm_tree = ttk.Treeview(self.stock_movements_frame,
            columns=("timestamp", "barcode", "name", "old_stock", "new_stock", "diff", "reason", "source"),
            show="headings", height=20)
        for col, text, w in [("timestamp", "Ημερομηνία", 150), ("barcode", "Barcode", 120),
            ("name", "Προϊόν", 200), ("old_stock", "Παλιό", 70), ("new_stock", "Νέο", 70),
            ("diff", "Διαφορά", 70), ("reason", "Αιτία", 120), ("source", "Πηγή", 100)]:
            self.sm_tree.heading(col, text=text); self.sm_tree.column(col, width=w)
        self.sm_tree.grid(row=1, column=0, sticky="nsew")

    def refresh_stock_movements(self):
        if not hasattr(self, 'stock_movements_frame') or self.stock_movements_frame is None:
            return
        barcode = self.sm_barcode_entry.get().strip() or None
        reason = self.sm_reason_var.get().strip() or None
        rows = self.db_service.get_stock_movements(barcode=barcode, reason=reason, limit=200)
        self.sm_tree.delete(*self.sm_tree.get_children())
        for r in rows:
            self.sm_tree.insert("", "end", values=(
                r.get("timestamp", ""), r.get("barcode", ""), r.get("product_name", ""),
                r.get("old_stock", 0), r.get("new_stock", 0),
                r.get("change_amount", 0), r.get("reason", ""), r.get("source", "")))
