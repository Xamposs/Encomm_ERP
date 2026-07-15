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

from infrastructure.database_service import DatabaseService
from infrastructure.license_service import generate_hwid, verify_local_license, generate_license_key
from infrastructure.ai_service import AIService
from core.intent_factory import IntentFactory
from core.undo_stack import ActionHistory

from presentation.views import (
    DashboardView, InventoryView, POSView, SettingsView, AIView,
    CustomersView, SuppliersView, InvoiceHistoryView, StockMovementsView,
)

# Configure CustomTkinter behavior (theme is set per-instance in MainWindow.__init__)
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

        # Apply persisted theme BEFORE creating any widgets
        theme = config.get("theme", "Dark")
        customtkinter.set_appearance_mode(theme)

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

        # ── View references (set in _init_views; lazily built on demand) ──
        self.dashboard_view = None
        self.inventory_view = None
        self.pos_view = None
        self.settings_view = None
        self.ai_view = None

    # =========================================================================
    # VIEWS — Instantiate all 5 view classes
    # =========================================================================
    def _init_views(self):
        """Create ONLY the dashboard view at startup. All others are lazily
        instantiated on first navigation via _ensure_frame(). This avoids
        ~220 widgets existing simultaneously, which causes severe resize lag
        because Tkinter recalculates geometry for ALL children on every
        resize pixel — not just the visible frame."""
        self.dashboard_view = DashboardView(
            self.main_container, self.db_service, self.config,
            fg_color="transparent")
        self.dashboard_view.import_btn.configure(command=self._on_dashboard_import)
        self.dashboard_view.grid(row=1, column=0, sticky="nsew")
        self.dashboard_view.grid_remove()

        # All other views start as None — created on first access
        self.inventory_view = None
        self.pos_view = None
        self.settings_view = None
        self.ai_view = None
        self.customers_view = None
        self.suppliers_view = None
        self.invoice_history_view = None
        self.stock_movements_view = None

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

    def _on_theme_applied(self, theme: str):
        """Re-apply ttk Treeview styles after a live theme switch."""
        self._apply_global_ttk_style()
        self.config["theme"] = theme

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
        """Process the AI command bar entry via AIService → IntentFactory."""
        text = self.ai_cmd_bar.get().strip()
        if not text:
            return
        self.ai_cmd_bar.delete(0, "end")
        self.ai_status_lbl.configure(text="🔄 Επεξεργασία εντολής...")

        def _bg_process():
            try:
                ai = self._get_ai_service()
                raw_response = ai.send_command_to_llm(text)
                intent = self.intent_factory.parse(raw_response)
                self.after(0, self._dispatch_ai_intent, intent)
            except Exception as exc:
                logging.exception("AI command processing failed")
                self.after(0, lambda: self.ai_status_lbl.configure(
                    text=f"⚠️ Σφάλμα: {exc}"))

        threading.Thread(target=_bg_process, daemon=True).start()

    def _dispatch_ai_intent(self, intent: dict):
        """Dispatch a parsed intent dict to the appropriate navigation/action."""
        if not intent:
            self.ai_status_lbl.configure(text="⚠️ Αδύνατη επεξεργασία εντολής.")
            return

        intent_name = intent.get("intent", "unknown")
        params = intent.get("parameters", {})

        if intent_name == "unknown":
            reason = params.get("reason", "Μη αναγνωρίσιμη εντολή.")
            self.ai_status_lbl.configure(text=f"⚠️ {reason}")
            return

        self.ai_status_lbl.configure(text=f"✅ Εντολή: {intent_name}")

        if intent_name == "search_inventory":
            query = params.get("query", "")
            self._filter_low_stock = False
            self._filter_expiry = False
            self.select_frame("inventory")
            if query and hasattr(self, 'inventory_view') and self.inventory_view:
                self.inventory_view.search_entry.delete(0, "end")
                self.inventory_view.search_entry.insert(0, query)
                self.inventory_view.refresh()
        elif intent_name == "check_low_stock":
            self.select_frame("inventory")
            if hasattr(self, 'inventory_view') and self.inventory_view:
                self.inventory_view.search_entry.delete(0, "end")
                self.inventory_view.inv_page = 0
                # Trigger a low-stock-filtered fetch
                threading.Thread(target=lambda: self.after(100, lambda: self.inventory_view.refresh()),
                                 daemon=True).start()
        elif intent_name == "check_expiry":
            self.select_frame("inventory")
        elif intent_name == "view_dashboard":
            self.select_frame("dashboard")
        elif intent_name == "view_inventory":
            self.select_frame("inventory")
        elif intent_name == "view_pos":
            self.select_frame("invoices")
        elif intent_name == "view_settings":
            self.select_frame("settings")
        elif intent_name == "add_product":
            self.select_frame("inventory")
            if hasattr(self, 'inventory_view') and self.inventory_view:
                self.after(100, lambda: self.inventory_view.open_add_product_dialog())

    def _process_ai_chat_message(self, text: str) -> str:
        """Process a chat message from AIView and return a human-readable reply.

        Routes through AIService → IntentFactory, then returns a Greek
        description of what was understood and any action taken.
        """
        try:
            ai = self._get_ai_service()
            if not ai.is_configured():
                return ("⚠️ Το AI δεν έχει ρυθμιστεί. Πηγαίνετε στις Ρυθμίσεις "
                        "και εισάγετε ένα API Key (π.χ. DeepSeek, OpenAI).")
            raw_response = ai.send_command_to_llm(text)
            intent = self.intent_factory.parse(raw_response)

            intent_name = intent.get("intent", "unknown")
            params = intent.get("parameters", {})

            if intent_name == "unknown":
                reason = params.get("reason", "Δεν κατάλαβα την εντολή.")
                return f"🤔 {reason}"

            # Dispatch the action on the main thread, then return a reply
            self.after(0, self._dispatch_ai_intent, intent)

            replies = {
                "search_inventory": f"🔍 Αναζήτηση για: {params.get('query', '—')}",
                "check_low_stock": "📦 Εμφάνιση προϊόντων με χαμηλό στοκ.",
                "check_expiry": "📅 Εμφάνιση προϊόντων κοντά στη λήξη.",
                "view_dashboard": "📊 Μετάβαση στο Dashboard.",
                "view_inventory": "📦 Μετάβαση στην Αποθήκη.",
                "view_pos": "🧾 Μετάβαση στο Ταμείο.",
                "view_settings": "⚙️ Μετάβαση στις Ρυθμίσεις.",
                "add_product": "➕ Άνοιγμα φόρμας νέου προϊόντος.",
            }
            return replies.get(intent_name, f"✅ Εντολή: {intent_name}")
        except Exception as exc:
            logging.exception("AI chat message processing failed")
            return f"⚠️ Σφάλμα: {exc}"

    # =========================================================================
    # FRAME SWITCHING
    # =========================================================================
    def _ensure_frame(self, name: str):
        """Return the view frame, building it once on first access (lazy).

        Each view is created, gridded, then hidden (grid_remove) on first
        access. This keeps the widget count low at startup — only the
        dashboard exists initially, preventing resize lag."""
        attr = self.frame_attrs.get(name)
        if not attr:
            return None
        frame = getattr(self, attr, None)
        if frame is not None:
            return frame

        # ── Lazy factory: create + grid + hide on first access ──
        common = (self.main_container, self.db_service, self.config)
        kwargs = {"fg_color": "transparent"}

        if name == "dashboard":
            return getattr(self, attr)  # always pre-created

        elif name == "inventory":
            view = InventoryView(*common, on_data_changed=self._on_inventory_data_changed, **kwargs)

        elif name == "invoices":
            view = POSView(*common, **kwargs)

        elif name == "settings":
            view = SettingsView(*common, **kwargs)
            view._on_settings_saved = self._on_settings_saved
            view._on_theme_applied = self._on_theme_applied

        elif name == "ai_assistant":
            view = AIView(*common, on_send_message=self._process_ai_chat_message, **kwargs)

        elif name == "customers":
            view = CustomersView(*common, **kwargs)

        elif name == "suppliers":
            view = SuppliersView(*common, **kwargs)

        elif name == "invoice_history":
            view = InvoiceHistoryView(*common, **kwargs)

        elif name == "stock_movements":
            view = StockMovementsView(*common, **kwargs)

        else:
            return None

        # Store, grid, then hide — ready for frame switching
        setattr(self, attr, view)
        view.grid(row=1, column=0, sticky="nsew")
        view.grid_remove()
        return view

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

