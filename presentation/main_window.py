import tkinter as tk
import os
import time
import logging
import threading
from tkinter import messagebox, filedialog
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
from infrastructure.excel_parser_service import ExcelParserService
from infrastructure.license_service import generate_hwid, verify_local_license, generate_license_key
from infrastructure.ai_service import AIService
from core.intent_factory import IntentFactory
from core.undo_stack import ActionHistory

# Configure CustomTkinter behavior
customtkinter.set_appearance_mode("Dark")
customtkinter.set_default_color_theme("blue")


# ---------------------------------------------------------------------------
# Theme-aware colour helpers
# ---------------------------------------------------------------------------
def _is_dark_mode() -> bool:
    """Return True when CustomTkinter is currently in Dark appearance mode."""
    return customtkinter.get_appearance_mode() == "Dark"


def _zebra_row(index: int) -> tuple:
    """Return alternating zebra-stripe background colour tuple (light, dark)."""
    if index % 2 == 0:
        return ("#F0F2F5", "#16191E")
    return ("#E0E3E8", "#22252C")


def _header_bg() -> tuple:
    return ("gray75", "gray20")


def _csv_cell(val) -> str:
    """Escape a CSV cell: wrap in quotes if it contains comma, quote, or newline."""
    s = str(val)
    if "," in s or '"' in s or "\n" in s:
        s = '"' + s.replace('"', '""') + '"'
    return s


def _header_fg() -> tuple:
    return ("gray30", "gray80")


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
    """Return a plain hex string for ttk background based on current mode."""
    return "#242424" if _is_dark_mode() else "#f0f0f0"


def _ttk_fg() -> str:
    """Return a plain hex string for ttk foreground based on current mode."""
    return "#ffffff" if _is_dark_mode() else "#000000"


def _ttk_selected_bg() -> str:
    """Return a plain hex string for ttk selected-row background."""
    return "#3a3a3a" if _is_dark_mode() else "#d0d7ff"


def _subtle_text() -> tuple:
    return ("gray55", "gray50")


def _card_title_text() -> tuple:
    return ("gray45", "gray60")


class MainWindow(customtkinter.CTk):
    def __init__(self, db_service: DatabaseService, config: Dict[str, Any]):
        super().__init__()

        self.db_service = db_service
        self.config = config

        # Configure window settings (lightweight — no widget creation)
        self.title("ENCOMM Mini-ERP 🧪")
        self.geometry("1150x730")
        self.minsize(1050, 650)

        # Set up grid layout (1x2 - Sidebar on left, Content on right)
        self.grid_columnconfigure(0, weight=0)  # Sidebar fixed width
        self.grid_columnconfigure(1, weight=1)  # Main content expands
        self.grid_rowconfigure(0, weight=1)

        # Track currently visible frame (anti-flicker)
        self.active_frame = None
        self.current_frame_name = None

        # Temporary variables for POS checkout (Invoices view)
        self.invoice_cart: List[Tuple[Product, int]] = []
        self.cart_rows_tracked = []
        self._pos_search_timer = None
        self._search_timer = None
        
        # Threading/Spam Guards
        self._inv_fetching = False

        # AI filter flags (set by IntentFactory)
        self._filter_low_stock = False
        self._filter_expiry = False

        # Cached HWID (fetched in background to avoid 18s startup freeze)
        self.cached_hwid = None

        # Undo/Redo action-history stack (5-level deep)
        self.action_history = ActionHistory()

        # Safe exit protocol (register before UI build so it's always active)
        self._active_timers = []
        self.protocol("WM_DELETE_WINDOW", self.on_safe_close)

        # ── Force empty window to render NOW — prevents white-screen freeze ──
        self.update_idletasks()

        # ── Defer ALL heavy UI construction to the event loop ──
        self.after(0, self._post_init)

        # ── Root-window focus + global click-to-unfocus ──
        self.configure(takefocus=True)
        self.bind_all("<Button-1>", self._on_global_click)

    def _on_global_click(self, event):
        try:
            if "entry" not in str(event.widget).lower():
                self.focus_set()
        except Exception:
            pass

    def _post_init(self):
        """Build the full UI inside the event loop, after the window is visible."""
        # Global ttk style — configure once before any Treeview is created
        self._apply_global_ttk_style()

        # HWID background fetch (deferred 1s to let UI settle)
        def _fetch_hwid_bg():
            from infrastructure.license_service import generate_hwid
            val = generate_hwid()
            self.after(0, lambda: self._set_cached_hwid(val))
        self.after(1000, lambda: threading.Thread(target=_fetch_hwid_bg, daemon=True).start())

        # Create components
        self._init_sidebar()
        self._init_main_panel()

        # ── Build-Once On-Demand: only dashboard at startup, rest lazy ──
        self.after(50, lambda: self._ensure_frame("dashboard"))
        self.after(100, lambda: self.select_frame("dashboard"))

        # Start live clock
        self._update_clock()

    def _set_cached_hwid(self, val: str):
        """Store HWID from background thread and update settings entry if visible."""
        self.cached_hwid = val
        if hasattr(self, 'set_hwid_entry') and self.set_hwid_entry is not None:
            self.set_hwid_entry.configure(state="normal")
            self.set_hwid_entry.delete(0, "end")
            self.set_hwid_entry.insert(0, val)
            self.set_hwid_entry.configure(state="disabled")

    def on_safe_close(self):
        """Auto-backup if enabled, cancel timers, then hard OS-level kill.
        Bypasses self.quit()/self.destroy() to avoid X11 deadlock with background threads."""
        # ── Auto-backup on close (if enabled in settings) ──
        if hasattr(self, 'set_autobackup_var') and self.set_autobackup_var.get():
            try:
                path = self.db_service.backup_database()
                logging.info("Auto-backup on close: %s", path)
            except Exception as e:
                logging.error("Auto-backup on close failed: %s", e)

        logging.info("Safe exit triggered — hard OS-level kill...")
        try:
            if hasattr(self, '_search_timer') and self._search_timer is not None:
                self.after_cancel(self._search_timer)
            if hasattr(self, '_pos_search_timer') and self._pos_search_timer is not None:
                self.after_cancel(self._pos_search_timer)
            if hasattr(self, '_active_timers'):
                for _tid in self._active_timers:
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
        """Build the left navigation sidebar with a flat minimalist style."""
        self.sidebar_frame = customtkinter.CTkFrame(self, corner_radius=0, width=220)
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
        self.sidebar_frame.grid_rowconfigure(9, weight=1)

        # App Brand Header
        self.brand_label = customtkinter.CTkLabel(
            self.sidebar_frame,
            text="ENCOMM ERP 🧪",
            font=customtkinter.CTkFont(family="Outfit", size=20, weight="bold")
        )
        self.brand_label.grid(row=0, column=0, padx=20, pady=(45, 30))

        # Navigation Buttons
        self.nav_buttons = {}

        self.nav_buttons["dashboard"] = customtkinter.CTkButton(
            self.sidebar_frame,
            text="📊  Αρχική",
            anchor="w",
            fg_color="transparent",
            text_color=_nav_text(),
            hover_color=_nav_hover(),
            font=customtkinter.CTkFont(family="Outfit", size=13, weight="normal"),
            command=lambda: self.select_frame("dashboard")
        )
        self.nav_buttons["dashboard"].grid(row=1, column=0, padx=20, pady=8, sticky="ew")

        self.nav_buttons["inventory"] = customtkinter.CTkButton(
            self.sidebar_frame,
            text="📦  Αποθήκη",
            anchor="w",
            fg_color="transparent",
            text_color=_nav_text(),
            hover_color=_nav_hover(),
            font=customtkinter.CTkFont(family="Outfit", size=13, weight="normal"),
            command=lambda: self.select_frame("inventory")
        )
        self.nav_buttons["inventory"].grid(row=2, column=0, padx=20, pady=8, sticky="ew")

        self.nav_buttons["suppliers"] = customtkinter.CTkButton(
            self.sidebar_frame,
            text="🏭  Προμηθευτές",
            anchor="w",
            fg_color="transparent",
            text_color=_nav_text(),
            hover_color=_nav_hover(),
            font=customtkinter.CTkFont(family="Outfit", size=13, weight="normal"),
            command=lambda: self.select_frame("suppliers")
        )
        self.nav_buttons["suppliers"].grid(row=3, column=0, padx=20, pady=8, sticky="ew")

        self.nav_buttons["invoices"] = customtkinter.CTkButton(
            self.sidebar_frame,
            text="🧾  Ταμείο / Πωλήσεις",
            anchor="w",
            fg_color="transparent",
            text_color=_nav_text(),
            hover_color=_nav_hover(),
            font=customtkinter.CTkFont(family="Outfit", size=13, weight="normal"),
            command=lambda: self.select_frame("invoices")
        )
        self.nav_buttons["invoices"].grid(row=4, column=0, padx=20, pady=8, sticky="ew")

        self.nav_buttons["customers"] = customtkinter.CTkButton(
            self.sidebar_frame,
            text="👥  Πελάτες",
            anchor="w",
            fg_color="transparent",
            text_color=_nav_text(),
            hover_color=_nav_hover(),
            font=customtkinter.CTkFont(family="Outfit", size=13, weight="normal"),
            command=lambda: self.select_frame("customers")
        )
        self.nav_buttons["customers"].grid(row=5, column=0, padx=20, pady=8, sticky="ew")

        self.nav_buttons["invoice_history"] = customtkinter.CTkButton(
            self.sidebar_frame,
            text="🔎  Ιστορικό",
            anchor="w",
            fg_color="transparent",
            text_color=_nav_text(),
            hover_color=_nav_hover(),
            font=customtkinter.CTkFont(family="Outfit", size=13, weight="normal"),
            command=lambda: self.select_frame("invoice_history")
        )
        self.nav_buttons["invoice_history"].grid(row=6, column=0, padx=20, pady=8, sticky="ew")

        self.nav_buttons["stock_movements"] = customtkinter.CTkButton(
            self.sidebar_frame,
            text="📋  Κινήσεις",
            anchor="w",
            fg_color="transparent",
            text_color=_nav_text(),
            hover_color=_nav_hover(),
            font=customtkinter.CTkFont(family="Outfit", size=13, weight="normal"),
            command=lambda: self.select_frame("stock_movements")
        )
        self.nav_buttons["stock_movements"].grid(row=7, column=0, padx=20, pady=8, sticky="ew")

        self.nav_buttons["settings"] = customtkinter.CTkButton(
            self.sidebar_frame,
            text="⚙️  Ρυθμίσεις",
            anchor="w",
            fg_color="transparent",
            text_color=_nav_text(),
            hover_color=_nav_hover(),
            font=customtkinter.CTkFont(family="Outfit", size=13, weight="normal"),
            command=lambda: self.select_frame("settings")
        )
        self.nav_buttons["settings"].grid(row=8, column=0, padx=20, pady=8, sticky="ew")

        self.nav_buttons["ai_assistant"] = customtkinter.CTkButton(
            self.sidebar_frame,
            text="🤖  AI Βοηθός",
            anchor="w",
            fg_color="transparent",
            text_color=_nav_text(),
            hover_color=_nav_hover(),
            font=customtkinter.CTkFont(family="Outfit", size=13, weight="normal"),
            command=lambda: self.select_frame("ai_assistant")
        )
        self.nav_buttons["ai_assistant"].grid(row=9, column=0, padx=20, pady=8, sticky="ew")

        # Footer branding — shares weighted row 9 with AI button (spacer pushes both down)
        self.version_label = customtkinter.CTkLabel(
            self.sidebar_frame,
            text="v1.0.0 Stable | ENCOMM Tensor Intelligence",
            font=customtkinter.CTkFont(size=11),
            text_color=_subtle_text()
        )
        self.version_label.grid(row=9, column=0, padx=20, pady=(120, 15))

    # =========================================================================
    # MAIN PANEL
    # =========================================================================
    def _init_main_panel(self):
        """Build the right content area with enhanced margins and breathing room."""
        self.main_container = customtkinter.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.main_container.grid(row=0, column=1, sticky="nsew", padx=35, pady=35)

        self.main_container.grid_columnconfigure(0, weight=1)
        self.main_container.grid_rowconfigure(0, weight=0)
        self.main_container.grid_rowconfigure(1, weight=1)

        # HEADER PANEL
        self.header_frame = customtkinter.CTkFrame(self.main_container, fg_color="transparent")
        self.header_frame.grid(row=0, column=0, sticky="ew", pady=(0, 25))
        self.header_frame.grid_columnconfigure(0, weight=1)

        # Title of current section
        self.section_title_label = customtkinter.CTkLabel(
            self.header_frame,
            text="Αρχική",
            font=customtkinter.CTkFont(family="Outfit", size=26, weight="bold")
        )
        self.section_title_label.grid(row=0, column=0, sticky="w")

        # Live clock + undo/redo button bar (right side of header)
        self.header_right_bar = customtkinter.CTkFrame(self.header_frame, fg_color="transparent")
        self.header_right_bar.grid(row=0, column=1, sticky="e")

        self.clock_label = customtkinter.CTkLabel(
            self.header_right_bar,
            text="",
            font=customtkinter.CTkFont(family="Courier", size=14),
            text_color="#34C759"
        )
        self.clock_label.pack(side="left", padx=(0, 12))

        self.undo_btn = customtkinter.CTkButton(
            self.header_right_bar, text="↩", width=36, height=36,
            fg_color="transparent", text_color=_nav_text(),
            hover_color=_nav_hover(),
            font=customtkinter.CTkFont(size=16),
            command=self._undo_last_action, state="disabled"
        )
        self.undo_btn.pack(side="left", padx=(0, 4))

        self.redo_btn = customtkinter.CTkButton(
            self.header_right_bar, text="↪", width=36, height=36,
            fg_color="transparent", text_color=_nav_text(),
            hover_color=_nav_hover(),
            font=customtkinter.CTkFont(size=16),
            command=self._redo_last_action, state="disabled"
        )
        self.redo_btn.pack(side="left")

        # AI Command Bar
        self.ai_cmd_bar = customtkinter.CTkEntry(
            self.header_frame,
            placeholder_text="💡 Πείτε στο Encomm AI τι θέλετε να κάνετε... (π.χ. 'Δείξε μου τις ελλείψεις')",
            height=38,
            font=customtkinter.CTkFont(size=13),
            fg_color=("#E8ECF1", "#1A1D24"),
            border_color=("#A0B4D0", "#3B5068"),
            border_width=1,
        )
        self.ai_cmd_bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        self.ai_cmd_bar.bind("<Return>", lambda e: self.process_ai_command())

        # AI status label
        self.ai_status_lbl = customtkinter.CTkLabel(
            self.header_frame,
            text="",
            font=customtkinter.CTkFont(size=11),
            text_color=_subtle_text(),
        )
        self.ai_status_lbl.grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))

        # AI backend services (lazy-initialized to avoid blocking startup)
        self.ai_service = None
        self._ai_service_lock = threading.Lock()
        self.intent_factory = IntentFactory()

        # LAZY FRAME INITIALIZATION
        self.dashboard_frame = None
        self.inventory_frame = None
        self.invoices_frame = None
        self.settings_frame = None
        self.ai_assistant_frame = None
        # Dashboard frame created lazily on first select_frame("dashboard")

    # =========================================================================
    # UNDO / REDO
    # =========================================================================
    def _update_undo_redo_buttons(self):
        """Enable or disable the undo/redo header buttons based on stack state."""
        if hasattr(self, 'undo_btn') and self.undo_btn is not None:
            if self.action_history.can_undo:
                self.undo_btn.configure(state="normal")
            else:
                self.undo_btn.configure(state="disabled")
        if hasattr(self, 'redo_btn') and self.redo_btn is not None:
            if self.action_history.can_redo:
                self.redo_btn.configure(state="normal")
            else:
                self.redo_btn.configure(state="disabled")

    def _undo_last_action(self):
        """Execute the most recent undo entry."""
        desc = self.action_history.undo()
        if desc is None:
            return
        self._update_undo_redo_buttons()
        messagebox.showinfo("Αναίρεση", f"Η ενέργεια αναιρέθηκε:\n{desc}")

    def _redo_last_action(self):
        """Re-execute the most recently undone action."""
        desc = self.action_history.redo()
        if desc is None:
            return
        self._update_undo_redo_buttons()
        messagebox.showinfo("Επανάληψη", f"Η ενέργεια επαναλήφθηκε:\n{desc}")

    # =========================================================================
    # AI BACKEND — Thread-safe lazy initialization
    # =========================================================================
    def _get_ai_service(self):
        """Return the AIService singleton, initializing it once on first access."""
        if self.ai_service is None:
            with self._ai_service_lock:
                if self.ai_service is None:  # double-checked locking
                    self.ai_service = AIService(self.db_service)
        return self.ai_service

    # =========================================================================
    # VIEW: AI ASSISTANT (CHAT)
    # =========================================================================
    def _init_ai_assistant_frame(self):
        """Initialize the AI Assistant frame with chat UI."""
        self.ai_assistant_frame = customtkinter.CTkFrame(self.main_container, fg_color="transparent")
        self.ai_assistant_frame.grid_columnconfigure(0, weight=1)
        self.ai_assistant_frame.grid_rowconfigure(0, weight=0)
        self.ai_assistant_frame.grid_rowconfigure(1, weight=1)
        self.ai_assistant_frame.grid_rowconfigure(2, weight=0)

        self.ai_title_label = customtkinter.CTkLabel(
            self.ai_assistant_frame,
            text="Βοηθός AI Φαρμακείου (Encomm AI)",
            font=customtkinter.CTkFont(family="Outfit", size=18, weight="bold")
        )
        self.ai_title_label.grid(row=0, column=0, sticky="w", pady=(0, 15))

        self.ai_chat_log = customtkinter.CTkScrollableFrame(
            self.ai_assistant_frame,
            fg_color="transparent"
        )
        self.ai_chat_log.grid(row=1, column=0, sticky="nsew", pady=(0, 15))
        self.ai_chat_log.grid_columnconfigure(0, weight=1)

        self._append_chat_message("Encomm AI", "Γεια! Είμαι ο Encomm, ο AI βοηθός σου. Πώς μπορώ να σε βοηθήσω; 🤖")

        self.ai_input_frame = customtkinter.CTkFrame(self.ai_assistant_frame, fg_color="transparent")
        self.ai_input_frame.grid(row=2, column=0, sticky="ew")
        self.ai_input_frame.grid_columnconfigure(0, weight=1)

        self.ai_input_entry = customtkinter.CTkEntry(
            self.ai_input_frame,
            placeholder_text="Γράψτε μια εντολή ή ερώτηση...",
            height=40,
            font=customtkinter.CTkFont(size=13)
        )
        self.ai_input_entry.grid(row=0, column=0, padx=(0, 10), sticky="ew")
        self.ai_input_entry.bind("<Return>", lambda e: self.send_ai_message())

        self.ai_send_btn = customtkinter.CTkButton(
            self.ai_input_frame,
            text="🚀 Αποστολή",
            width=120,
            height=40,
            font=customtkinter.CTkFont(weight="bold", size=13),
            fg_color="#10B981",
            hover_color="#059669",
            command=self.send_ai_message
        )
        self.ai_send_btn.grid(row=0, column=1)

    def _append_chat_message(self, sender: str, message: str):
        """Append a styled message bubble to the chat log."""
        is_bot = sender == "Encomm AI"
        bubble_color = ("#E2E8F0", "#1E293B")
        sender_color = "#10B981" if is_bot else ("#2563EB", "#3B82F6")

        bubble = customtkinter.CTkFrame(self.ai_chat_log, fg_color=bubble_color, corner_radius=10)
        bubble.grid(row=len(self.ai_chat_log.winfo_children()), column=0, sticky="ew", pady=4, padx=5)
        bubble.grid_columnconfigure(0, weight=1)

        customtkinter.CTkLabel(
            bubble,
            text=sender,
            font=customtkinter.CTkFont(size=11, weight="bold"),
            text_color=sender_color,
            anchor="w"
        ).grid(row=0, column=0, padx=12, pady=(8, 0), sticky="w")

        customtkinter.CTkLabel(
            bubble,
            text=message,
            font=customtkinter.CTkFont(size=13),
            text_color=_body_text(),
            anchor="w",
            wraplength=600,
            justify="left"
        ).grid(row=1, column=0, padx=12, pady=(2, 8), sticky="w")

    def send_ai_message(self):
        """Send user message to chat, process with LLM in background thread."""
        text = self.ai_input_entry.get().strip()
        if not text:
            return

        self._append_chat_message("Εσείς", text)
        self.ai_input_entry.delete(0, tk.END)

        def bg_chat_process():
            try:
                time.sleep(0.4)
                reply = "Λήψη εντολής επιτυχής. Το backend AI interface είναι έτοιμο για διασύνδεση!"
                self.after(0, lambda: self._append_chat_message("Encomm AI", reply))
            except Exception as e:
                self.after(0, lambda: self._append_chat_message("Encomm AI", f"⚠️ Σφάλμα: {str(e)}"))

        threading.Thread(target=bg_chat_process, daemon=True).start()

    def _update_clock(self):
        """Update live timestamp label every second."""
        now_str = datetime.now().strftime("%A, %Y-%m-%d %H:%M:%S")
        self.clock_label.configure(text=f"🕒  {now_str}")
        self.after(1000, self._update_clock)

    def _ensure_frame(self, name: str):
        """Return the frame, building it once on first access (Build-Once On-Demand)."""
        frame_attr = f"{name}_frame"
        frame = getattr(self, frame_attr, None)
        if frame is not None:
            return frame

        # Lazy-build on first click — no pre-rendering at startup
        init_map = {
            "dashboard":       self._init_dashboard_frame,
            "inventory":       self._init_inventory_frame,
            "invoices":        self._init_invoices_frame,
            "settings":        self._init_settings_frame,
            "ai_assistant":    self._init_ai_assistant_frame,
            "customers":       self._init_customers_frame,
            "invoice_history": self._init_invoice_history_frame,
            "suppliers":       self._init_suppliers_frame,
            "stock_movements": self._init_stock_movements_frame,
        }
        init_map[name]()
        frame = getattr(self, frame_attr)

        # Grid into main_container (all frames share row=1, col=0), then hide
        frame.grid(row=1, column=0, sticky="nsew")
        frame.grid_remove()
        return frame

    def select_frame(self, name: str):
        """Switch active frame — visibility management only (no widget creation)."""
        if self.current_frame_name == name:
            return

        self.current_frame_name = name

        # Update nav button highlight state
        for btn_name, btn in self.nav_buttons.items():
            if btn_name == name:
                btn.configure(
                    fg_color=_nav_active_bg(),
                    text_color=_nav_active_text(),
                    font=customtkinter.CTkFont(family="Outfit", size=13, weight="bold")
                )
            else:
                btn.configure(
                    fg_color="transparent",
                    text_color=_nav_text(),
                    font=customtkinter.CTkFont(family="Outfit", size=13, weight="normal")
                )

        # Hide current frame (grid_remove preserves grid options — no recalculation)
        if hasattr(self, "active_frame") and self.active_frame is not None:
            self.active_frame.grid_remove()

        # Get target frame (built once on first access, gridded, then hidden)
        target_frame = self._ensure_frame(name)
        if target_frame is None:  # race-condition guard — frame not built yet
            return

        frame_titles = {
            "dashboard":       "Επισκόπηση Συστήματος",
            "inventory":       "Διαχείριση Αποθήκης",
            "invoices":        "Ταμείο / Πωλήσεις (POS)",
            "settings":        "Ρυθμίσεις Συστήματος",
            "ai_assistant":    "AI Βοηθός",
            "customers":       "Πελάτες",
            "invoice_history": "Ιστορικό Παραστατικών",
            "suppliers":       "Μητρώο Προμηθευτών",
            "stock_movements": "Κινήσεις Αποθέματος",
        }
        refresh_fns = {
            "dashboard":       self.refresh_dashboard,
            "inventory":       self.refresh_inventory_list,
            "invoices":        self.refresh_invoice_view,
            "settings":        self.load_settings_values,
            "ai_assistant":    None,
            "customers":       self.refresh_customer_list,
            "invoice_history": self.refresh_invoice_history_list,
            "suppliers":       self.refresh_supplier_list,
            "stock_movements": self.refresh_stock_movements,
        }

        self.section_title_label.configure(text=frame_titles[name])
        target_frame.grid()      # restore with preserved grid options (no recalculation)
        target_frame.tkraise()   # ensure top z-order
        self.active_frame = target_frame

        self.update_idletasks()

        if refresh_fns[name]:
            refresh_fns[name]()

    # =========================================================================
    # VIEW: DASHBOARD
    # =========================================================================
    def _init_dashboard_frame(self):
        """Initialize the dashboard frame container."""
        self.dashboard_frame = customtkinter.CTkFrame(self.main_container, fg_color="transparent")
        self.dashboard_frame.grid_columnconfigure(0, weight=1)
        self.dashboard_frame.grid_rowconfigure(0, weight=0)
        self.dashboard_frame.grid_rowconfigure(1, weight=1)

        # 1. STAT CARDS ROW
        self.stats_row = customtkinter.CTkFrame(self.dashboard_frame, fg_color="transparent")
        self.stats_row.grid(row=0, column=0, sticky="ew", pady=(0, 25))
        self.stats_row.grid_columnconfigure((0, 1, 2), weight=1, uniform="equal")

        # Card 1: Total Products
        self.card_total = customtkinter.CTkFrame(self.stats_row, border_width=1, border_color=_stat_border_default())
        self.card_total.grid(row=0, column=0, padx=(0, 15), pady=5, sticky="nsew")
        self.card_total_title = customtkinter.CTkLabel(self.card_total, text="Συνολικά Προϊόντα", font=customtkinter.CTkFont(size=12, weight="bold"), text_color=_card_title_text())
        self.card_total_title.pack(anchor="w", padx=15, pady=(15, 2))
        self.card_total_val = customtkinter.CTkLabel(self.card_total, text="0", font=customtkinter.CTkFont(size=32, weight="bold"))
        self.card_total_val.pack(anchor="w", padx=15, pady=(0, 15))

        # Card 2: Low Stock Alerts
        self.card_low_stock = customtkinter.CTkFrame(self.stats_row, border_width=2, border_color="#FF9500")
        self.card_low_stock.grid(row=0, column=1, padx=15, pady=5, sticky="nsew")
        self.card_low_stock_title = customtkinter.CTkLabel(self.card_low_stock, text="Ελλείψεις Στοκ", font=customtkinter.CTkFont(size=12, weight="bold"), text_color=_card_title_text())
        self.card_low_stock_title.pack(anchor="w", padx=15, pady=(15, 2))
        self.card_low_stock_val = customtkinter.CTkLabel(self.card_low_stock, text="0", font=customtkinter.CTkFont(size=32, weight="bold"), text_color="#FF9500")
        self.card_low_stock_val.pack(anchor="w", padx=15, pady=(0, 15))

        # Card 3: Expiry Alerts
        self.card_expiry = customtkinter.CTkFrame(self.stats_row, border_width=2, border_color="#FF3B30")
        self.card_expiry.grid(row=0, column=2, padx=(15, 0), pady=5, sticky="nsew")
        self.card_expiry_title = customtkinter.CTkLabel(self.card_expiry, text="Κοντά στη Λήξη / Ληγμένα", font=customtkinter.CTkFont(size=12, weight="bold"), text_color=_card_title_text())
        self.card_expiry_title.pack(anchor="w", padx=15, pady=(15, 2))
        self.card_expiry_val = customtkinter.CTkLabel(self.card_expiry, text="0", font=customtkinter.CTkFont(size=32, weight="bold"), text_color="#FF3B30")
        self.card_expiry_val.pack(anchor="w", padx=15, pady=(0, 15))

        # ── Analytics row 2: revenue, VAT, invoice count ──
        self.analytics_row2 = customtkinter.CTkFrame(self.dashboard_frame, fg_color="transparent")
        self.analytics_row2.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 5))

        self.card_revenue = customtkinter.CTkFrame(self.analytics_row2)
        self.card_revenue.pack(side="left", fill="both", expand=True, padx=(0, 8))
        self.card_revenue_title = customtkinter.CTkLabel(self.card_revenue, text="Έσοδα Σήμερα", font=customtkinter.CTkFont(size=11), text_color=_card_title_text())
        self.card_revenue_title.pack(anchor="w", padx=15, pady=(10, 2))
        self.card_revenue_val = customtkinter.CTkLabel(self.card_revenue, text="€0.00", font=customtkinter.CTkFont(size=22, weight="bold"), text_color="#34C759")
        self.card_revenue_val.pack(anchor="w", padx=15, pady=(0, 10))

        self.card_vat = customtkinter.CTkFrame(self.analytics_row2)
        self.card_vat.pack(side="left", fill="both", expand=True, padx=4)
        self.card_vat_title = customtkinter.CTkLabel(self.card_vat, text="ΦΠΑ Σήμερα", font=customtkinter.CTkFont(size=11), text_color=_card_title_text())
        self.card_vat_title.pack(anchor="w", padx=15, pady=(10, 2))
        self.card_vat_val = customtkinter.CTkLabel(self.card_vat, text="€0.00", font=customtkinter.CTkFont(size=22, weight="bold"), text_color="#FF9500")
        self.card_vat_val.pack(anchor="w", padx=15, pady=(0, 10))

        self.card_inv_count = customtkinter.CTkFrame(self.analytics_row2)
        self.card_inv_count.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self.card_inv_count_title = customtkinter.CTkLabel(self.card_inv_count, text="Παραστατικά", font=customtkinter.CTkFont(size=11), text_color=_card_title_text())
        self.card_inv_count_title.pack(anchor="w", padx=15, pady=(10, 2))
        self.card_invoice_count_val = customtkinter.CTkLabel(self.card_inv_count, text="0", font=customtkinter.CTkFont(size=22, weight="bold"))
        self.card_invoice_count_val.pack(anchor="w", padx=15, pady=(0, 10))

        # 2. ALERTS SCROLLABLE TABLE & EXCEL IMPORT
        self.lower_row = customtkinter.CTkFrame(self.dashboard_frame, fg_color="transparent")
        self.lower_row.grid(row=1, column=0, sticky="nsew")
        self.lower_row.grid_columnconfigure(0, weight=3)
        self.lower_row.grid_columnconfigure(1, weight=1)
        self.lower_row.grid_rowconfigure(0, weight=1)

        self.alert_container = customtkinter.CTkFrame(self.lower_row)
        self.alert_container.grid(row=0, column=0, sticky="nsew", padx=(0, 20), pady=5)
        self.alert_container.grid_columnconfigure(0, weight=1)
        self.alert_container.grid_rowconfigure(2, weight=1)

        self.alert_lbl = customtkinter.CTkLabel(
            self.alert_container,
            text="⚠️ Κρίσιμα Προϊόντα (Χαμηλό Στοκ ή Κοντά στη Λήξη)",
            font=customtkinter.CTkFont(size=14, weight="bold")
        )
        self.alert_lbl.grid(row=0, column=0, padx=15, pady=10, sticky="w")

        self.alert_scrollbar = ttk.Scrollbar(self.alert_container, orient="vertical")
        self.alert_scrollbar.grid(row=2, column=1, sticky="ns", padx=(0, 15), pady=(0, 15))

        self.alert_tree = ttk.Treeview(
            self.alert_container,
            columns=("name", "stock", "expiry", "reason"),
            show="headings",
            height=12,
            yscrollcommand=self.alert_scrollbar.set,
            selectmode="browse",
        )
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

        self.actions_panel = customtkinter.CTkFrame(self.lower_row)
        self.actions_panel.grid(row=0, column=1, sticky="nsew", pady=5)

        self.act_title = customtkinter.CTkLabel(self.actions_panel, text="Ενέργειες Προμηθευτή", font=customtkinter.CTkFont(size=14, weight="bold"))
        self.act_title.pack(padx=20, pady=(20, 10), anchor="w")

        self.import_btn = customtkinter.CTkButton(
            self.actions_panel,
            text="📥  Εισαγωγή Excel Προμηθευτή",
            font=customtkinter.CTkFont(weight="bold", size=13),
            fg_color="#10B981",
            hover_color="#059669",
            command=self.import_supplier_invoice,
            height=40
        )
        self.import_btn.pack(padx=20, pady=(15, 15), fill="x")

        self.quick_desc = customtkinter.CTkLabel(
            self.actions_panel,
            text="Αυτόματη ανάλυση τιμολογίων προμηθευτών\nκαι συγχρονισμός επιπέδων στοκ.\nΥποστηριζόμενες μορφές: .xlsx, .csv",
            font=customtkinter.CTkFont(size=11),
            text_color=_subtle_text(),
            justify="left",
            wraplength=180
        )
        self.quick_desc.pack(padx=20, pady=10, anchor="w")

        # ── Smart Export control bar ──
        self.dash_export_bar = customtkinter.CTkFrame(self.dashboard_frame, fg_color="transparent")
        self.dash_export_bar.grid(row=2, column=0, sticky="ew", pady=(20, 0))
        self.dash_export_filter = customtkinter.CTkEntry(self.dash_export_bar, width=160, placeholder_text="Φίλτρο (π.χ. DEPON)")
        self.dash_export_filter.pack(side="left", padx=(0, 8))
        self.dash_export_limit = customtkinter.CTkEntry(self.dash_export_bar, width=100, placeholder_text="Ποσότητα (π.χ. 20 ή ALL)")
        self.dash_export_limit.pack(side="left", padx=(0, 8))
        self.dash_export_start = customtkinter.CTkEntry(self.dash_export_bar, width=110, placeholder_text="Από (YYYY-MM-DD)")
        self.dash_export_start.pack(side="left", padx=(0, 5))
        self.dash_export_end = customtkinter.CTkEntry(self.dash_export_bar, width=110, placeholder_text="Έως (YYYY-MM-DD)")
        self.dash_export_end.pack(side="left", padx=(0, 8))
        self.dash_export_format = customtkinter.CTkOptionMenu(self.dash_export_bar, values=["PDF (.txt style)", "Excel (.csv)"], width=140)
        self.dash_export_format.pack(side="left", padx=(0, 8))
        self.dash_export_btn = customtkinter.CTkButton(self.dash_export_bar, text="📤 Εξαγωγή", fg_color="#2980B9", hover_color="#1F618D",
            font=customtkinter.CTkFont(weight="bold"), command=self.export_dashboard)
        self.dash_export_btn.pack(side="left")

    def export_dashboard(self):
        """Export dashboard alert items to Desktop in a background thread (with date range filter)."""
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
                    rows.append({"name": str(vals[0]), "stock": str(vals[1]), "expiry": str(vals[2]), "reason": str(vals[3])})
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
                        lines.append(f'{_csv_cell(r["name"])},{r["stock"]},{_csv_cell(r["expiry"])},{_csv_cell(r["reason"])}')
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

    def refresh_dashboard(self):
        """Fetch statistics from DB via native SQL in a background thread."""
        if not hasattr(self, 'dashboard_frame') or self.dashboard_frame is None:
            return
        threshold = int(self.config["low_stock_threshold"])
        alert_days = int(self.config["expiry_alert_days"])

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

            self.after(0, self._safe_update_dashboard_ui, counts, critical_items, analytics)

        threading.Thread(target=bg_fetch, daemon=True).start()

    def _safe_update_dashboard_ui(self, counts: Dict[str, int], critical_items: List, analytics: Dict = None):
        """Populate dashboard alert Treeview and analytics safely."""
        if not hasattr(self, 'alert_tree') or self.alert_tree is None:
            return
        self.card_total_val.configure(text=str(counts["total"]))
        self.card_low_stock_val.configure(text=str(counts["low_stock"]))
        self.card_expiry_val.configure(text=str(counts["expiry"]))

        # Update analytics cards if present
        if analytics and hasattr(self, 'card_revenue_val'):
            self.card_revenue_val.configure(text=f'€{analytics.get("revenue_today", 0):.2f}')
            self.card_vat_val.configure(text=f'€{analytics.get("vat_today", 0):.2f}')
            self.card_invoice_count_val.configure(text=str(analytics.get("invoice_count", 0)))

        self.alert_tree.delete(*self.alert_tree.get_children())

        for p, reason in critical_items:
            if "Ληγμένο" in reason:
                tag = "expired"
            elif "Λήγει" in reason:
                tag = "near_expiry"
            else:
                tag = "low_stock"

            self.alert_tree.insert("", "end", values=(p.name, f"{p.stock} τεμ.", p.expiry_date, reason), tags=(tag,))

    # =========================================================================
    # VIEW: INVENTORY
    # =========================================================================
    def _init_inventory_frame(self):
        """Initialize inventory list with debounced searching and responsive tree."""
        self.inventory_frame = customtkinter.CTkFrame(self.main_container, fg_color="transparent")
        self.inventory_frame.grid_columnconfigure(0, weight=1)
        self.inventory_frame.grid_rowconfigure(1, weight=1)
        self.inventory_frame.grid_rowconfigure(2, weight=1)

        # Toolbar Frame
        self.inv_toolbar = customtkinter.CTkFrame(self.inventory_frame, fg_color="transparent")
        self.inv_toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 20))
        self.inv_toolbar.grid_columnconfigure(0, weight=1)

        self.search_entry = customtkinter.CTkEntry(
            self.inv_toolbar,
            placeholder_text="🔍 Αναζήτηση κατά barcode ή όνομα..."
        )
        self.search_entry.grid(row=0, column=0, padx=(0, 15), sticky="ew")
        self.search_entry.bind("<KeyRelease>", lambda e: self._inv_search_changed())

        self.add_prod_btn = customtkinter.CTkButton(
            self.inv_toolbar,
            text="➕  Νέο Προϊόν",
            fg_color="#34C759",
            hover_color="#289A47",
            font=customtkinter.CTkFont(weight="bold"),
            command=self.open_add_product_dialog
        )
        self.add_prod_btn.grid(row=0, column=1, padx=5)

        self.import_inv_btn = customtkinter.CTkButton(
            self.inv_toolbar,
            text="📥  Εισαγωγή Excel/CSV",
            fg_color=("#2ecc71", "#27ae60"),
            hover_color=("#27ae60", "#1e8449"),
            font=customtkinter.CTkFont(weight="bold"),
            command=self.import_supplier_invoice
        )
        self.import_inv_btn.grid(row=0, column=2, padx=(5, 0))

        self.inv_movements_btn = customtkinter.CTkButton(
            self.inv_toolbar,
            text="📋 Ιστορικό Κινήσεων",
            fg_color=("#8E44AD", "#7D3C98"),
            hover_color=("#7D3C98", "#6C3483"),
            font=customtkinter.CTkFont(weight="bold"),
            command=self.toggle_movement_history
        )
        self.inv_movements_btn.grid(row=0, column=3, padx=(5, 0))

        # Movement history toggle state
        self.inv_show_movements = False

        # Pagination state
        self.inv_page = 0
        self.inv_page_size = 20

        # Pagination toolbar row
        self.inv_pager = customtkinter.CTkFrame(self.inventory_frame, fg_color="transparent")
        self.inv_pager.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        self.inv_pager.grid_columnconfigure(1, weight=1)

        self.inv_page_info = customtkinter.CTkLabel(
            self.inv_pager, text="",
            font=customtkinter.CTkFont(size=12),
            text_color=_subtle_text()
        )
        self.inv_page_info.grid(row=0, column=1, sticky="e", padx=10)

        self.inv_prev_btn = customtkinter.CTkButton(
            self.inv_pager, text="◀ Προηγ.", width=90, height=28,
            font=customtkinter.CTkFont(size=12),
            fg_color=("gray80", "gray30"), hover_color=("gray70", "gray40"),
            command=self._inv_prev_page
        )
        self.inv_prev_btn.grid(row=0, column=0, padx=(0, 5))

        self.inv_next_btn = customtkinter.CTkButton(
            self.inv_pager, text="Επόμ. ▶", width=90, height=28,
            font=customtkinter.CTkFont(size=12),
            fg_color=("gray80", "gray30"), hover_color=("gray70", "gray40"),
            command=self._inv_next_page
        )
        self.inv_next_btn.grid(row=0, column=2, padx=(5, 0))

        # ── Movement History View (hidden by default) ──
        self.inv_mov_filter = customtkinter.CTkEntry(
            self.inventory_frame,
            placeholder_text="🔍 Φιλτράρισμα κατά Barcode...",
            width=250
        )
        self.inv_mov_filter.grid(row=1, column=0, sticky="w", padx=15, pady=(5, 10))
        self.inv_mov_filter.bind("<KeyRelease>", lambda e: self._refresh_movement_history())
        self.inv_mov_filter.grid_remove()

        self.inv_mov_container = customtkinter.CTkFrame(self.inventory_frame)
        self.inv_mov_container.grid(row=2, column=0, sticky="nsew", padx=15, pady=15)
        self.inv_mov_container.grid_columnconfigure(0, weight=1)
        self.inv_mov_container.grid_rowconfigure(0, weight=1)
        self.inv_mov_container.grid_remove()

        self.inv_mov_scrollbar = ttk.Scrollbar(
            self.inv_mov_container, orient="vertical")
        self.inv_mov_scrollbar.grid(row=0, column=1, sticky="ns")

        self.inv_mov_tree = ttk.Treeview(
            self.inv_mov_container,
            columns=("timestamp", "barcode", "name", "old_stock",
                     "new_stock", "diff", "reason", "ref"),
            show="headings", height=20,
            yscrollcommand=self.inv_mov_scrollbar.set,
            selectmode="browse"
        )
        self.inv_mov_tree.grid(row=0, column=0, sticky="nsew")
        self.inv_mov_scrollbar.config(command=self.inv_mov_tree.yview)

        self.inv_mov_tree.heading("timestamp", text="Ημερομηνία")
        self.inv_mov_tree.heading("barcode", text="Barcode")
        self.inv_mov_tree.heading("name", text="Προϊόν")
        self.inv_mov_tree.heading("old_stock", text="Παλιό Στοκ")
        self.inv_mov_tree.heading("new_stock", text="Νέο Στοκ")
        self.inv_mov_tree.heading("diff", text="Διαφορά")
        self.inv_mov_tree.heading("reason", text="Αιτία")
        self.inv_mov_tree.heading("ref", text="Αναφορά")

        self.inv_mov_tree.column("timestamp", width=150, anchor="w")
        self.inv_mov_tree.column("barcode", width=120, anchor="w")
        self.inv_mov_tree.column("name", width=200, anchor="w")
        self.inv_mov_tree.column("old_stock", width=80, anchor="e")
        self.inv_mov_tree.column("new_stock", width=80, anchor="e")
        self.inv_mov_tree.column("diff", width=80, anchor="e")
        self.inv_mov_tree.column("reason", width=140, anchor="w")
        self.inv_mov_tree.column("ref", width=100, anchor="w")

        # Movement pager
        self.inv_mov_pager = customtkinter.CTkFrame(
            self.inventory_frame, fg_color="transparent")
        self.inv_mov_pager.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        self.inv_mov_pager.grid_columnconfigure(1, weight=1)
        self.inv_mov_pager.grid_remove()

        self.inv_mov_page_info = customtkinter.CTkLabel(
            self.inv_mov_pager, text="",
            font=customtkinter.CTkFont(size=12),
            text_color=_subtle_text())
        self.inv_mov_page_info.grid(row=0, column=1, sticky="e", padx=10)

        self.inv_mov_page = 0

        # Main Table Container
        self.table_container = customtkinter.CTkFrame(self.inventory_frame)
        self.table_container.grid(row=1, column=0, sticky="nsew", padx=15, pady=15)
        self.table_container.grid_columnconfigure(0, weight=1)
        self.table_container.grid_rowconfigure(0, weight=1)

        self.inv_scrollbar = ttk.Scrollbar(self.table_container, orient="vertical")
        self.inv_scrollbar.grid(row=0, column=1, sticky="ns")

        self.inv_tree = ttk.Treeview(
            self.table_container,
            columns=("barcode", "name", "stock", "expiry", "price"),
            show="headings",
            height=20,
            yscrollcommand=self.inv_scrollbar.set,
            selectmode="browse",
        )
        self.inv_tree.grid(row=0, column=0, sticky="nsew")
        self.inv_scrollbar.config(command=self.inv_tree.yview)

        self.inv_tree.heading("barcode", text="Barcode")
        self.inv_tree.heading("name", text="Όνομα Προϊόντος")
        self.inv_tree.heading("stock", text="Στοκ")
        self.inv_tree.heading("expiry", text="Ημ. Λήξης")
        self.inv_tree.heading("price", text="Τιμή")

        self.inv_tree.column("barcode", width=120, anchor="w")
        self.inv_tree.column("name", width=280, anchor="w")
        self.inv_tree.column("stock", width=80, anchor="e")
        self.inv_tree.column("expiry", width=120, anchor="e")
        self.inv_tree.column("price", width=100, anchor="e")

        self.inv_tree.tag_configure("low_stock", foreground="#FF9500")
        self.inv_tree.tag_configure("expired", foreground="#FF3B30")
        self.inv_tree.tag_configure("near_expiry", foreground="#FF9500")

        self.inv_tree.configure(style="Treeview")
        self.inv_tree.bind("<Double-1>", self._on_tree_double_click)

        # ── Smart Export control bar ──
        self.inv_export_bar = customtkinter.CTkFrame(self.inventory_frame, fg_color="transparent")
        self.inv_export_bar.grid(row=3, column=0, sticky="ew", pady=(15, 0))
        self.inv_export_filter = customtkinter.CTkEntry(self.inv_export_bar, width=160, placeholder_text="Φίλτρο (π.χ. DEPON)")
        self.inv_export_filter.pack(side="left", padx=(0, 8))
        self.inv_export_limit = customtkinter.CTkEntry(self.inv_export_bar, width=100, placeholder_text="Ποσότητα (π.χ. 20 ή ALL)")
        self.inv_export_limit.pack(side="left", padx=(0, 8))
        self.inv_export_start = customtkinter.CTkEntry(self.inv_export_bar, width=110, placeholder_text="Από (YYYY-MM-DD)")
        self.inv_export_start.pack(side="left", padx=(0, 5))
        self.inv_export_end = customtkinter.CTkEntry(self.inv_export_bar, width=110, placeholder_text="Έως (YYYY-MM-DD)")
        self.inv_export_end.pack(side="left", padx=(0, 8))
        self.inv_export_format = customtkinter.CTkOptionMenu(self.inv_export_bar, values=["PDF (.txt style)", "Excel (.csv)"], width=140)
        self.inv_export_format.pack(side="left", padx=(0, 8))
        self.inv_export_btn = customtkinter.CTkButton(self.inv_export_bar, text="📤 Εξαγωγή", fg_color="#2980B9", hover_color="#1F618D",
            font=customtkinter.CTkFont(weight="bold"), command=self.export_inventory)
        self.inv_export_btn.pack(side="left")

    def export_inventory(self):
        """Export inventory products to Desktop in a background thread (with date range filter)."""
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
                        lines.append(f'{_csv_cell(p.barcode)},{_csv_cell(p.name)},{p.stock},{_csv_cell(p.expiry_date)},{p.price:.2f}')
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

    @staticmethod
    def _apply_global_ttk_style():
        """Configure the global ttk Treeview style once at startup."""
        _style = ttk.Style()
        _style.theme_use("clam")
        bg = _ttk_bg()
        fg = _ttk_fg()
        sel_bg = _ttk_selected_bg()
        _style.configure("Treeview", background=bg, foreground=fg, fieldbackground=bg, rowheight=30, font=("Segoe UI", 13))
        _style.configure("Treeview.Heading", background=bg, foreground=fg, font=("Segoe UI", 14, "bold"))
        _style.map("Treeview", background=[("selected", sel_bg)], foreground=[("selected", "#ffffff")])

    def _on_tree_double_click(self, event):
        selection = self.inv_tree.selection()
        if not selection:
            return
        item = self.inv_tree.item(selection[0])
        barcode = item["values"][0]
        product = self.db_service.get_product(str(barcode))
        if product:
            self.open_edit_product_dialog(product)

    def _inv_search_changed(self):
        """Debounced inventory search — only fires on real user input."""
        # Strict guard: ignore events when inventory frame is not visible
        if not getattr(self, 'inventory_frame', None):
            return
        if not self.inventory_frame.winfo_ismapped():
            return
        if hasattr(self, '_search_timer') and self._search_timer is not None:
            self.after_cancel(self._search_timer)
        self.inv_page = 0
        self._search_timer = self.after(300, self.refresh_inventory_list)

    def _inv_next_page(self):
        if not getattr(self, 'inventory_frame', None) or not self.inventory_frame.winfo_ismapped():
            return
        if getattr(self, '_inv_fetching', False):
            return
        self.inv_page += 1
        self.refresh_inventory_list()

    def _inv_prev_page(self):
        if not getattr(self, 'inventory_frame', None) or not self.inventory_frame.winfo_ismapped():
            return
        if getattr(self, '_inv_fetching', False):
            return
        if self.inv_page > 0:
            self.inv_page -= 1
            self.refresh_inventory_list()

    # ── Movement History Toggle ──────────────────────────────────────

    def toggle_movement_history(self):
        """Switch between product list and movement history views."""
        self.inv_show_movements = not self.inv_show_movements
        if self.inv_show_movements:
            self.inv_movements_btn.configure(
                text="🔄 Πίσω στη Λίστα Προϊόντων")
            self._show_movement_history()
        else:
            self.inv_movements_btn.configure(
                text="📋 Ιστορικό Κινήσεων")
            self._hide_movement_history()

    def _show_movement_history(self):
        """Hide product views, show movement history below toolbar."""
        self.search_entry.grid_remove()
        self.add_prod_btn.grid_remove()
        self.import_inv_btn.grid_remove()
        self.table_container.grid_remove()
        self.inv_pager.grid_remove()
        self.inv_mov_filter.grid()
        self.inv_mov_container.grid()
        self.inv_mov_pager.grid()
        self.inv_mov_page = 0
        self._refresh_movement_history()

    def _hide_movement_history(self):
        """Hide movement history, show product views."""
        self.inv_mov_filter.grid_remove()
        self.inv_mov_container.grid_remove()
        self.inv_mov_pager.grid_remove()
        self.search_entry.grid()
        self.add_prod_btn.grid()
        self.import_inv_btn.grid()
        self.table_container.grid()
        self.inv_pager.grid()
        self.refresh_inventory_list()

    def _refresh_movement_history(self):
        """Query and render movement history into the treeview."""
        if not self.inv_show_movements:
            return
        barcode_filter = self.inv_mov_filter.get().strip() or None

        def _fetch():
            try:
                rows = self.db_service.get_stock_movements(
                    barcode=barcode_filter, limit=50,
                    offset=self.inv_mov_page * 50)
                self.after(0, lambda: self._render_movements(rows, barcode_filter))
            except Exception as e:
                logging.error("Movement history fetch failed: %s", e)

        threading.Thread(target=_fetch, daemon=True).start()

    def _render_movements(self, rows, barcode_filter=None):
        """Populate movement treeview from fetched rows."""
        if not hasattr(self, 'inv_mov_tree') or self.inv_mov_tree is None:
            return
        self.inv_mov_tree.delete(*self.inv_mov_tree.get_children())
        for r in rows:
            ch = r.get("change_amount") or r.get("difference", 0)
            diff_str = f"+{ch}" if ch >= 0 else str(ch)
            ref = r.get("source") or r.get("reference_id") or "-"
            self.inv_mov_tree.insert("", "end", values=(
                r["timestamp"], r["barcode"], r["product_name"],
                r["old_stock"], r["new_stock"], diff_str,
                r["reason"], ref
            ))
        total = len(rows)
        self.inv_mov_page_info.configure(
            text=f"Σελίδα {self.inv_mov_page + 1} — {total} εγγραφές")

    def refresh_inventory_list(self):
        """Fetch ONE page of filtered products via threaded SQL — only when frame is visible."""
        # Strict guard: block fetches unless the inventory tab is mapped on screen
        if not getattr(self, 'inventory_frame', None) or not self.inventory_frame.winfo_ismapped():
            return
        if not hasattr(self, 'inv_tree') or self.inv_tree is None:
            return
        if self._inv_fetching:
            return
        self._inv_fetching = True

        search_query = self.search_entry.get().strip()
        threshold = int(self.config.get("low_stock_threshold", 10))
        alert_days = int(self.config.get("expiry_alert_days", 30))
        today = date.today()
        offset = self.inv_page * self.inv_page_size
        filter_low_stock = self._filter_low_stock
        filter_expiry = self._filter_expiry

        self._filter_low_stock = False
        self._filter_expiry = False

        def bg_fetch():
            start_time = time.time()
            try:
                page_products, total_count = self.db_service.get_products_paginated(
                    search_query=search_query,
                    filter_low_stock=filter_low_stock,
                    filter_expiry=filter_expiry,
                    threshold=threshold,
                    alert_days=alert_days,
                    limit=self.inv_page_size,
                    offset=offset,
                )
            except Exception:
                logging.exception("Inventory background fetch failed")
                page_products, total_count = [], 0

            total_pages = max(1, (total_count + self.inv_page_size - 1) // self.inv_page_size)

            if self.inv_page >= total_pages and total_count > 0:
                clamped_page = max(0, total_pages - 1)
                clamped_offset = clamped_page * self.inv_page_size
                try:
                    page_products, total_count = self.db_service.get_products_paginated(
                        search_query=search_query,
                        filter_low_stock=False,
                        filter_expiry=False,
                        threshold=threshold,
                        alert_days=alert_days,
                        limit=self.inv_page_size,
                        offset=clamped_offset,
                    )
                except Exception:
                    logging.exception("Inventory clamp re-fetch failed")
                    page_products, total_count = [], 0
                total_pages = max(1, (total_count + self.inv_page_size - 1) // self.inv_page_size)
            else:
                clamped_page = self.inv_page

            duration_ms = int((time.time() - start_time) * 1000)
            logging.info(f"Inventory DB fetch (threaded) in {duration_ms}ms | page {clamped_page+1}/{total_pages}")

            self.after(0, self._safe_update_inventory_ui, page_products, total_count, total_pages, clamped_page, threshold, alert_days, today)

        threading.Thread(target=bg_fetch, daemon=True).start()

    def _safe_update_inventory_ui(self, page_products, total_count, total_pages, current_page, threshold, alert_days, today):
        try:
            if len(page_products) > self.inv_page_size:
                page_products = page_products[:self.inv_page_size]

            offset = current_page * self.inv_page_size
            end = offset + len(page_products)

            self.inv_page_info.configure(
                text=f"Εμφάνιση {offset+1}–{min(end, total_count)} από {total_count} προϊόντα  |  Σελίδα {current_page+1}/{total_pages}"
            )

            self.inv_prev_btn.configure(state="normal" if current_page > 0 else "disabled")
            self.inv_next_btn.configure(state="normal" if current_page < total_pages - 1 else "disabled")

            self.inv_tree.delete(*self.inv_tree.get_children())

            for p in page_products:
                is_low = is_low_stock(p, threshold)
                is_exp = is_expired(p, today)
                is_near_exp = is_near_expiry(p, alert_days, today)

                tags = ()
                if is_exp:
                    tags = ("expired",)
                elif is_near_exp:
                    tags = ("near_expiry",)
                elif is_low:
                    tags = ("low_stock",)

                self.inv_tree.insert("", "end", values=(
                    p.barcode,
                    p.name,
                    p.stock,
                    p.expiry_date,
                    f"€{p.price:.2f}",
                ), tags=tags)
        finally:
            self._inv_fetching = False

    def open_add_product_dialog(self):
        dialog = ProductFormDialog(self, title="Προσθήκη Νέου Προϊόντος")
        self.wait_window(dialog)
        if dialog.result:
            p_data = dialog.result
            new_prod = Product(
                barcode=p_data["barcode"],
                name=p_data["name"],
                stock=p_data["stock"],
                expiry_date=p_data["expiry_date"],
                price=p_data["price"],
                supplier_id=p_data.get("supplier_id")
            )
            success = self.db_service.add_product(new_prod)
            if success:
                # Push undo entry for product addition
                _bc = new_prod.barcode
                _nm = new_prod.name
                def undo_add():
                    self.db_service.delete_product(_bc)
                    self.refresh_inventory_list()
                    self.refresh_dashboard()
                def redo_add():
                    self.db_service.add_product(new_prod)
                    self.refresh_inventory_list()
                    self.refresh_dashboard()
                self.action_history.push(
                    f"Προσθήκη προϊόντος: {_nm}", undo_add, redo_add)
                self._update_undo_redo_buttons()
                messagebox.showinfo("Επιτυχία", f"Το προϊόν '{new_prod.name}' καταχωρήθηκε επιτυχώς.")
                self.refresh_inventory_list()
            else:
                messagebox.showerror("Σφάλμα", "Το Barcode υπάρχει ήδη ή υπήρξε σφάλμα στη βάση δεδομένων.")

    def open_edit_product_dialog(self, product: Product):
        dialog = ProductFormDialog(self, title=f"Επεξεργασία {product.name}", product=product)
        self.wait_window(dialog)
        if dialog.result:
            p_data = dialog.result
            updated_prod = Product(
                barcode=product.barcode,
                name=p_data["name"],
                stock=p_data["stock"],
                expiry_date=p_data["expiry_date"],
                price=p_data["price"],
                supplier_id=p_data.get("supplier_id")
            )
            # Capture old state BEFORE updating for undo
            _old = self.db_service.get_product(product.barcode)
            _old_data = {
                "Barcode": _old.barcode if _old else product.barcode,
                "Name": _old.name if _old else product.name,
                "Stock": _old.stock if _old else product.stock,
                "ExpiryDate": _old.expiry_date if _old else product.expiry_date,
                "Price": _old.price if _old else product.price,
                "supplier_id": getattr(_old, 'supplier_id', None) if _old else None,
            }
            _bc = product.barcode
            _nm = updated_prod.name
            success = self.db_service.update_product(updated_prod)
            if success:
                # ── Audit trail: log manual stock edit ──
                old_stock = p_data.get("old_stock")
                if old_stock is not None and updated_prod.stock != old_stock:
                    try:
                        self.db_service.log_stock_movement(
                            updated_prod.barcode, updated_prod.name,
                            old_stock, updated_prod.stock,
                            "Χειροκίνητη Επεξεργασία")
                    except Exception:
                        pass  # non-critical — edit succeeds regardless
                def undo_edit():
                    from core.domain_models import Product as P
                    restore = P(
                        barcode=_old_data["Barcode"], name=_old_data["Name"],
                        stock=_old_data["Stock"], expiry_date=_old_data["ExpiryDate"],
                        price=_old_data["Price"], supplier_id=_old_data.get("supplier_id"))
                    self.db_service.update_product(restore)
                    self.refresh_inventory_list()
                def redo_edit():
                    self.db_service.update_product(updated_prod)
                    self.refresh_inventory_list()
                self.action_history.push(
                    f"Επεξεργασία προϊόντος: {_nm}", undo_edit, redo_edit)
                self._update_undo_redo_buttons()
                messagebox.showinfo("Επιτυχία", "Τα στοιχεία του προϊόντος ενημερώθηκαν.")
                self.refresh_inventory_list()
            else:
                messagebox.showerror("Σφάλμα", "Αποτυχία ενημέρωσης στοιχείων προϊόντος.")

    def delete_product(self, barcode: str, name: str):
        old_product = self.db_service.get_product(barcode)
        if not old_product:
            messagebox.showerror("Σφάλμα", "Το προϊόν δεν βρέθηκε.")
            return

        if messagebox.askyesno("Επιβεβαίωση Διαγραφής",
            f"Είστε σίγουροι ότι θέλετε να διαγράψετε το '{name}';\n"
            f"(Μπορείτε να το επαναφέρετε με ↩ Αναίρεση)"):
            # Capture state for undo BEFORE deleting
            old_data = {
                "Barcode": old_product.barcode,
                "Name": old_product.name,
                "Stock": old_product.stock,
                "ExpiryDate": old_product.expiry_date,
                "Price": old_product.price,
                "supplier_id": getattr(old_product, 'supplier_id', None),
            }
            success = self.db_service.delete_product(barcode)
            if success:
                def undo_del():
                    self.db_service.restore_product(old_data)
                    self.refresh_inventory_list()
                    self.refresh_dashboard()
                def redo_del():
                    self.db_service.delete_product(barcode)
                    self.refresh_inventory_list()
                    self.refresh_dashboard()
                self.action_history.push(
                    f"Διαγραφή προϊόντος: {name}", undo_del, redo_del)
                self._update_undo_redo_buttons()
                messagebox.showinfo("Διαγραφή", "Το προϊόν διαγράφηκε επιτυχώς.")
                self.refresh_inventory_list()
            else:
                messagebox.showerror("Σφάλμα", "Αποτυχία διαγραφής προϊόντος.")

    # =========================================================================
    # VIEW: INVOICES (POS CHECKOUT)
    # =========================================================================
    def _init_invoices_frame(self):
        """Initialize the layout framework for the Point Of Sale checkout area."""
        self.invoices_frame = customtkinter.CTkFrame(self.main_container, fg_color="transparent")
        self.invoices_frame.grid_columnconfigure(0, weight=3)
        self.invoices_frame.grid_columnconfigure(1, weight=2)
        self.invoices_frame.grid_rowconfigure(0, weight=1)

        # 1. LEFT PANEL: Dynamic inputs and current cart state tracking
        self.pos_left_panel = customtkinter.CTkFrame(self.invoices_frame)
        self.pos_left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 20))
        self.pos_left_panel.grid_columnconfigure(0, weight=1)
        
        # Grid Configuration for Left Panel components to eliminate visual overlap bugs
        self.pos_left_panel.grid_rowconfigure(0, weight=0)  # Selector row
        self.pos_left_panel.grid_rowconfigure(1, weight=0)  # Live search box tree
        self.pos_left_panel.grid_rowconfigure(2, weight=0)  # Fixed layout table header 
        self.pos_left_panel.grid_rowconfigure(3, weight=1)  # Expandable cart scroll box row

        # POS Input selectors
        self.pos_selector_frame = customtkinter.CTkFrame(self.pos_left_panel, fg_color="transparent")
        self.pos_selector_frame.grid(row=0, column=0, sticky="ew", padx=15, pady=15)
        self.pos_selector_frame.grid_columnconfigure(0, weight=2)
        self.pos_selector_frame.grid_columnconfigure(1, weight=1)

        self.pos_prod_lbl = customtkinter.CTkLabel(self.pos_selector_frame, text="Επιλογή Προϊόντος:", font=customtkinter.CTkFont(weight="bold"))
        self.pos_prod_lbl.grid(row=0, column=0, sticky="w", pady=(0, 5))

        self.pos_prod_menu = customtkinter.CTkEntry(
            self.pos_selector_frame,
            placeholder_text="🔍 Σκανάρετε Barcode ή πληκτρολογήστε όνομα..."
        )
        self.pos_prod_menu.grid(row=1, column=0, padx=(0, 10), sticky="ew")
        self.pos_prod_menu.bind("<Return>", lambda e: self.add_item_to_cart())
        self.pos_prod_menu.bind("<KeyRelease>", lambda e: self._pos_search_changed())

        # Live search results (Row 1)
        self.pos_search_results_tree = ttk.Treeview(
            self.pos_left_panel,
            columns=("barcode", "name", "stock", "price"),
            show="headings",
            height=5,
            selectmode="browse",
        )
        self.pos_search_results_tree.grid(row=1, column=0, sticky="ew", padx=15, pady=(0, 10))

        self.pos_search_results_tree.heading("barcode", text="Barcode")
        self.pos_search_results_tree.heading("name", text="Όνομα")
        self.pos_search_results_tree.heading("stock", text="Στοκ")
        self.pos_search_results_tree.heading("price", text="Τιμή")

        self.pos_search_results_tree.column("barcode", width=100, anchor="w")
        self.pos_search_results_tree.column("name", width=200, anchor="w")
        self.pos_search_results_tree.column("stock", width=60, anchor="e")
        self.pos_search_results_tree.column("price", width=70, anchor="e")

        self.pos_search_results_tree.configure(style="Treeview")

        self.pos_qty_lbl = customtkinter.CTkLabel(self.pos_selector_frame, text="Ποσότητα:", font=customtkinter.CTkFont(weight="bold"))
        self.pos_qty_lbl.grid(row=0, column=1, sticky="w", pady=(0, 5))

        self.pos_qty_entry = customtkinter.CTkEntry(self.pos_selector_frame)
        self.pos_qty_entry.insert(0, "1")
        self.pos_qty_entry.grid(row=1, column=1, padx=(0, 10), sticky="ew")

        self.add_cart_btn = customtkinter.CTkButton(
            self.pos_selector_frame,
            text="🛒 Προσθήκη",
            font=customtkinter.CTkFont(weight="bold"),
            command=self.add_item_to_cart
        )
        self.add_cart_btn.grid(row=1, column=2, sticky="ew")

        # Fixed layout table header (Safely occupies Row 2)
        self.cart_header = customtkinter.CTkFrame(self.pos_left_panel, fg_color=_header_bg(), height=30)
        self.cart_header.grid(row=2, column=0, sticky="ew", padx=15, pady=(5, 5))
        self.cart_header.grid_columnconfigure(0, weight=2)
        self.cart_header.grid_columnconfigure(1, weight=1)
        self.cart_header.grid_columnconfigure(2, weight=1)
        self.cart_header.grid_columnconfigure(3, weight=1)
        self.cart_header.grid_columnconfigure(4, weight=1)

        _hdr_fg = _header_fg()
        customtkinter.CTkLabel(self.cart_header, text="Όνομα Προϊόντος", font=customtkinter.CTkFont(size=11, weight="bold"), text_color=_hdr_fg).grid(row=0, column=0, padx=(15, 5), sticky="w")
        customtkinter.CTkLabel(self.cart_header, text="Τιμή Μονάδας", font=customtkinter.CTkFont(size=11, weight="bold"), text_color=_hdr_fg).grid(row=0, column=1, padx=15, sticky="e")
        customtkinter.CTkLabel(self.cart_header, text="Ποσότητα", font=customtkinter.CTkFont(size=11, weight="bold"), text_color=_hdr_fg).grid(row=0, column=2, padx=15, sticky="e")
        customtkinter.CTkLabel(self.cart_header, text="Σύνολο", font=customtkinter.CTkFont(size=11, weight="bold"), text_color=_hdr_fg).grid(row=0, column=3, padx=15, sticky="e")
        customtkinter.CTkLabel(self.cart_header, text="", font=customtkinter.CTkFont(size=11, weight="bold"), text_color=_hdr_fg).grid(row=0, column=4, padx=(5, 15), sticky="e")

        # Cart Scrollable Items (Safely occupies Row 3 with flexible weighting)
        self.cart_scroll = customtkinter.CTkScrollableFrame(self.pos_left_panel, fg_color="transparent")
        self.cart_scroll.grid(row=3, column=0, sticky="nsew", padx=15, pady=(0, 15))
        self.cart_scroll.grid_columnconfigure(0, weight=1)

        # 2. RIGHT PANEL: Checkout details & invoice execution
        self.pos_right_panel = customtkinter.CTkFrame(self.invoices_frame)
        self.pos_right_panel.grid(row=0, column=1, sticky="nsew")

        self.pos_summary_title = customtkinter.CTkLabel(self.pos_right_panel, text="Σύνοψη Παραστατικού", font=customtkinter.CTkFont(size=16, weight="bold"))
        self.pos_summary_title.pack(padx=20, pady=20, anchor="w")

        self.sum_items_count = customtkinter.CTkLabel(self.pos_right_panel, text="Συνολικά Τεμάχια: 0", font=customtkinter.CTkFont(size=13))
        self.sum_items_count.pack(padx=20, pady=5, anchor="w")

        self.sum_subtotal = customtkinter.CTkLabel(self.pos_right_panel, text="Υποσύνολο: €0.00", font=customtkinter.CTkFont(size=13))
        self.sum_subtotal.pack(padx=20, pady=5, anchor="w")

        vat_pct = float(self.config.get("vat_rate", 0.15)) * 100
        self.sum_vat = customtkinter.CTkLabel(self.pos_right_panel, text=f"ΦΠΑ ({vat_pct:.1f}%): €0.00", font=customtkinter.CTkFont(size=13))
        self.sum_vat.pack(padx=20, pady=5, anchor="w")

        self.sum_total = customtkinter.CTkLabel(self.pos_right_panel, text="Γενικό Σύνολο: €0.00", font=customtkinter.CTkFont(size=20, weight="bold"), text_color="#34C759")
        self.sum_total.pack(padx=20, pady=(15, 10), anchor="w")

        # Customer selector
        self.pos_cust_frame = customtkinter.CTkFrame(self.pos_right_panel, fg_color="transparent")
        self.pos_cust_frame.pack(padx=20, pady=5, fill="x")
        customtkinter.CTkLabel(self.pos_cust_frame, text="Πελάτης:",
            font=customtkinter.CTkFont(weight="bold")).pack(side="left", padx=(0, 8))
        self.pos_customer_var = tk.StringVar(value="Λιανική Πώληση (Κανένας)")
        self.pos_customer_menu = customtkinter.CTkOptionMenu(
            self.pos_cust_frame, variable=self.pos_customer_var,
            values=["Λιανική Πώληση (Κανένας)"], width=220,
            command=self._on_pos_customer_selected,
        )
        self.pos_customer_menu.pack(side="left")
        self._selected_customer_id = None

        self.checkout_btn = customtkinter.CTkButton(
            self.pos_right_panel, text="💳  Ολοκλήρωση Πώλησης",
            font=customtkinter.CTkFont(weight="bold", size=14),
            fg_color="#10B981", hover_color="#059669",
            command=self.process_checkout
        )
        self.checkout_btn.pack(padx=20, pady=10, fill="x")

        self.clear_cart_btn = customtkinter.CTkButton(
            self.pos_right_panel, text="🧹 Αδειασμα Καλαθιού",
            fg_color=("gray80", "gray30"), hover_color=("gray70", "gray40"),
            command=self.clear_cart
        )
        self.clear_cart_btn.pack(padx=20, pady=5, fill="x")

        # ── Smart Export control bar ──
        self.pos_export_bar = customtkinter.CTkFrame(self.pos_right_panel, fg_color="transparent")
        self.pos_export_bar.pack(padx=20, pady=(15, 5), fill="x")
        self.pos_export_filter = customtkinter.CTkEntry(self.pos_export_bar, width=140, placeholder_text="Φίλτρο (π.χ. DEPON)")
        self.pos_export_filter.pack(side="left", padx=(0, 6))
        self.pos_export_limit = customtkinter.CTkEntry(self.pos_export_bar, width=90, placeholder_text="Ποσότητα (π.χ. 20 ή ALL)")
        self.pos_export_limit.pack(side="left", padx=(0, 6))
        self.pos_export_format = customtkinter.CTkOptionMenu(self.pos_export_bar, values=["PDF (.txt style)", "Excel (.csv)"], width=130)
        self.pos_export_format.pack(side="left", padx=(0, 6))
        self.pos_export_btn = customtkinter.CTkButton(self.pos_export_bar, text="📤 Εξαγωγή", fg_color="#2980B9", hover_color="#1F618D",
            font=customtkinter.CTkFont(weight="bold"), command=self.export_cart)
        self.pos_export_btn.pack(side="left")

        # ── POS Sales Date-Range Export bar ──
        self.pos_sales_export_bar = customtkinter.CTkFrame(self.invoices_frame, fg_color="transparent")
        self.pos_sales_export_bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self.pos_sales_export_start = customtkinter.CTkEntry(self.pos_sales_export_bar, width=120, placeholder_text="Από (YYYY-MM-DD)")
        self.pos_sales_export_start.pack(side="left", padx=(0, 5))
        self.pos_sales_export_end = customtkinter.CTkEntry(self.pos_sales_export_bar, width=120, placeholder_text="Έως (YYYY-MM-DD)")
        self.pos_sales_export_end.pack(side="left", padx=(0, 8))
        self.pos_sales_export_format = customtkinter.CTkOptionMenu(self.pos_sales_export_bar, values=["Excel (.csv)", "PDF (.txt style)"], width=140)
        self.pos_sales_export_format.pack(side="left", padx=(0, 8))
        self.pos_sales_export_btn = customtkinter.CTkButton(self.pos_sales_export_bar, text="📤 Εξαγωγή Πωλήσεων", fg_color="#2980B9", hover_color="#1F618D",
            font=customtkinter.CTkFont(weight="bold"), command=self.export_pos_sales)
        self.pos_sales_export_btn.pack(side="left")

    def export_cart(self):
        """Export current POS cart as proforma quote to Desktop in a background thread."""
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
                        lines.append(f'{_csv_cell(p.barcode)},{_csv_cell(p.name)},{q},{p.price:.2f},{p.price*q:.2f}')
                    lines.append(f"")
                    lines.append(f"Υποσύνολο,,{total_qty},,{subtotal:.2f}")
                    lines.append(f"ΦΠΑ {vat_rate*100:.1f}%,,,,{vat:.2f}")
                    lines.append(f"ΓΕΝΙΚΟ ΣΥΝΟΛΟ,,,,{grand:.2f}")
                    with open(dest, "w", encoding="utf-8-sig") as f:
                        f.write("\n".join(lines))
                else:
                    dest = os.path.join(os.path.expanduser("~"), "Desktop", f"POS_Προσφορά_{ts}.txt")
                    lines = ["=" * 55, "  ENCOMM — ΠΡΟΣΦΟΡΑ / PROFORMA", "=" * 55, f"Ημ/νία: {datetime.now().strftime('%d/%m/%Y %H:%M')}", f"Είδη: {len(items)}  |  Τεμάχια: {total_qty}", "-" * 55]
                    lines.append(f"{'Barcode':<14} {'Όνομα':<22} {'Ποσ.':<6} {'Τιμή':<8} {'Σύνολο':<10}")
                    lines.append("-" * 55)
                    for p, q in items:
                        lines.append(f"{p.barcode:<14} {p.name[:22]:<22} {q:<6} €{p.price:<7.2f} €{p.price*q:<9.2f}")
                    lines.append("-" * 55)
                    lines.append(f"{'Υποσύνολο:':<42} €{subtotal:.2f}")
                    vat_label = f"ΦΠΑ ({vat_rate*100:.1f}%):"
                    lines.append(vat_label.ljust(42) + f" €{vat:.2f}")
                    lines.append(f"{'ΓΕΝΙΚΟ ΣΥΝΟΛΟ:':<42} €{grand:.2f}")
                    lines.append("=" * 55)
                    with open(dest, "w", encoding="utf-8") as f:
                        f.write("\n".join(lines))
                self.after(0, lambda: messagebox.showinfo("Επιτυχής Εξαγωγή", f"Το αρχείο αποθηκεύτηκε στην Επιφάνεια Εργασίας!"))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Σφάλμα Εξαγωγής", str(e)))
        threading.Thread(target=_write, daemon=True).start()

    def export_pos_sales(self):
        """Export POS sales from DB within date range to Desktop in a background thread."""
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
                        lines.append(f'{_csv_cell(inv["id"])},{_csv_cell(inv["date"])},{inv["subtotal"]:.2f},{inv["vat"]:.2f},{inv["total"]:.2f},{_csv_cell(inv["customer_name"])}')
                    with open(dest, "w", encoding="utf-8-sig") as f:
                        f.write("\n".join(lines))
                else:
                    dest = os.path.join(os.path.expanduser("~"), "Desktop", f"POS_Sales_{ts}.txt")
                    lines = ["=" * 65, "  ENCOMM — ΑΝΑΦΟΡΑ ΠΩΛΗΣΕΩΝ", "=" * 65,
                             f"Ημ/νία: {datetime.now().strftime('%d/%m/%Y %H:%M')}  |  Εύρος: {start_date or '—'} έως {end_date or '—'}",
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
                    lines.append(f"\n{'=' * 65}")
                    lines.append(f"  ΣΥΝΟΛΟ ΕΣΟΔΩΝ: €{total_revenue:.2f}")
                    lines.append(f"{'=' * 65}")
                    with open(dest, "w", encoding="utf-8") as f:
                        f.write("\n".join(lines))
                self.after(0, lambda: messagebox.showinfo("Επιτυχής Εξαγωγή",
                    "Το φιλτραρισμένο αρχείο βάσει ημερομηνιών αποθηκεύτηκε στην Επιφάνεια Εργασίας!"))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Σφάλμα Εξαγωγής", str(e)))
        threading.Thread(target=_write, daemon=True).start()

    def refresh_invoice_view(self):
        if not hasattr(self, 'invoices_frame') or self.invoices_frame is None:
            return
        if not hasattr(self, 'cart_scroll') or self.cart_scroll is None:
            return
        if hasattr(self, 'pos_prod_menu'):
            try:
                self.pos_prod_menu.delete(0, tk.END)
            except Exception:
                pass
        # Refresh customer dropdown
        self._refresh_pos_customer_list()
        self.refresh_cart_list()

    def _refresh_pos_customer_list(self):
        """Populate the POS customer dropdown from the database."""
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
        """Extract customer ID and trigger history popup or close existing one."""
        if choice.startswith("Λιανική"):
            self._selected_customer_id = None
            if hasattr(self, 'cust_popup') and self.cust_popup.winfo_exists():
                self.cust_popup.destroy()
        else:
            try:
                self._selected_customer_id = int(choice.split(":")[0])
                customer_name = choice.split(": ", 1)[1] if ": " in choice else choice
                self.after(80, lambda: self.show_customer_history_popup(self._selected_customer_id, customer_name))
            except (ValueError, IndexError):
                self._selected_customer_id = None

    def show_customer_history_popup(self, customer_id: int, customer_name: str):
        """Spawn a CTkToplevel popup with the customer's last 5 purchases."""
        if hasattr(self, 'cust_popup') and self.cust_popup.winfo_exists():
            self.cust_popup.destroy()

        self.cust_popup = customtkinter.CTkToplevel(self)
        self.cust_popup.title("👤 Ιστορικό Αγορών")
        self.cust_popup.geometry("450x320")
        self.cust_popup.resizable(False, False)
        self.cust_popup.transient(self)
        self.cust_popup.after(100, lambda: self.cust_popup.lift())

        customtkinter.CTkLabel(self.cust_popup,
            text=f"Πρόσφατες αγορές: {customer_name}",
            font=customtkinter.CTkFont(size=13, weight="bold")).pack(pady=15)

        scroll = customtkinter.CTkScrollableFrame(self.cust_popup, width=410, height=220)
        scroll.pack(padx=20, pady=(0, 15), fill="both", expand=True)

        loading = customtkinter.CTkLabel(scroll, text="🔄 Ανάκτηση ιστορικού από τη βάση δεδομένων...",
            font=customtkinter.CTkFont(size=12))
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
                customtkinter.CTkLabel(scroll,
                    text="Δεν βρέθηκαν παλαιότερες αγορές για τον συγκεκριμένο πελάτη.",
                    font=customtkinter.CTkFont(size=11), wraplength=380).pack(pady=20)
                return
            for r in rows:
                line = f"📅 [{r['date']}] - {r['name']} (Τεμ: {r['qty']} - €{r['price']:.2f})"
                customtkinter.CTkLabel(scroll, text=line, font=customtkinter.CTkFont(size=11),
                    anchor="w", justify="left").pack(anchor="w", pady=2)
                customtkinter.CTkFrame(scroll, height=1, fg_color=("gray85", "gray25")).pack(fill="x", pady=1)

        threading.Thread(target=_fetch, daemon=True).start()

    def _pos_search_changed(self):
        """Throttle input bursts using a 250ms debounce window before querying the DB."""
        # Strict guard: ignore events when invoices frame is not visible
        if not getattr(self, 'invoices_frame', None):
            return
        if not self.invoices_frame.winfo_ismapped():
            return
        if hasattr(self, '_pos_search_timer') and self._pos_search_timer is not None:
            self.after_cancel(self._pos_search_timer)
        self._pos_search_timer = self.after(250, self._pos_live_search)

    def _pos_live_search(self):
        """Populate search results tree safely inside a background thread."""
        # Guard: skip if frame no longer visible (user switched tabs mid-debounce)
        if not getattr(self, 'invoices_frame', None) or not self.invoices_frame.winfo_ismapped():
            return
        text = self.pos_prod_menu.get().strip()
        if not text:
            self.pos_search_results_tree.delete(*self.pos_search_results_tree.get_children())
            return
        
        def bg_pos_fetch():
            try:
                results, _ = self.db_service.get_products_paginated(search_query=text, limit=10, offset=0)
                self.after(0, self._safe_update_pos_search_ui, results, text)
            except Exception:
                logging.exception("POS live search background fetch failed")
        
        threading.Thread(target=bg_pos_fetch, daemon=True).start()

    def _safe_update_pos_search_ui(self, results: list, original_text: str):
        """Render results back to tree view only if context matches text entry input."""
        if self.pos_prod_menu.get().strip() != original_text:
            return
        self.pos_search_results_tree.delete(*self.pos_search_results_tree.get_children())
        for p in results:
            self.pos_search_results_tree.insert("", "end", values=(p.barcode, p.name, p.stock, f"€{p.price:.2f}"))

    def add_item_to_cart(self):
        """Fetch item profile inside background thread to prevent UI lockup spikes."""
        selection = self.pos_search_results_tree.selection()
        if selection:
            item = self.pos_search_results_tree.item(selection[0])
            search_val = str(item["values"][0])
        else:
            search_val = self.pos_prod_menu.get().strip()

        if not search_val:
            messagebox.showwarning("Προειδοποίηση", "Σκανάρετε ένα barcode ή επιλέξτε προϊόν.")
            return

        try:
            qty = int(self.pos_qty_entry.get())
            if qty <= 0:
                raise ValueError()
        except ValueError:
            messagebox.showwarning("Προειδοποίηση", "Εισάγετε έναν έγκυρο θετικό ακέραιο αριθμό για την ποσότητα.")
            return

        def bg_add_to_cart():
            try:
                product = self.db_service.get_product(search_val)
                if not product:
                    found, _ = self.db_service.get_products_paginated(search_query=search_val, limit=1, offset=0)
                    product = found[0] if found else None

                if not product:
                    self.after(0, lambda: messagebox.showwarning("Δεν βρέθηκε", f"Το προϊόν '{search_val}' δεν βρέθηκε."))
                    return

                self.after(0, self._safe_finalize_add_to_cart, product, qty)
            except Exception:
                logging.exception("Background add item lookup crashed")

        threading.Thread(target=bg_add_to_cart, daemon=True).start()

    def _safe_finalize_add_to_cart(self, product: Product, qty: int):
        """Append fetched items profile payload data safely structure configuration back to active cart view."""
        barcode = product.barcode
        already_in_cart = sum(item[1] for item in self.invoice_cart if item[0].barcode == barcode)
        total_requested = already_in_cart + qty

        if total_requested > product.stock:
            messagebox.showerror("Ανεπαρκές Απόθεμα", f"Διαθέσιμα μόνο {product.stock} τεμ. Έχετε ήδη {already_in_cart} στο καλάθι.")
            return

        exists_idx = -1
        for idx, (p, q) in enumerate(self.invoice_cart):
            if p.barcode == barcode:
                exists_idx = idx
                break

        if exists_idx != -1:
            self.invoice_cart[exists_idx] = (self.invoice_cart[exists_idx][0], total_requested)
        else:
            self.invoice_cart.append((product, qty))

        self.pos_prod_menu.delete(0, tk.END)
        self.pos_qty_entry.delete(0, tk.END)
        self.pos_qty_entry.insert(0, "1")
        self.pos_search_results_tree.delete(*self.pos_search_results_tree.get_children())

        self.refresh_cart_list()

    def remove_item_from_cart(self, barcode: str):
        self.invoice_cart = [item for item in self.invoice_cart if item[0].barcode != barcode]
        self.refresh_cart_list()

    def refresh_cart_list(self):
        """Re-render the scrollable list of cart items and update the summary totals safely."""
        for row in self.cart_rows_tracked:
            try:
                row.destroy()
            except Exception:
                pass
        self.cart_rows_tracked.clear()

        vat_rate = float(self.config["vat_rate"])

        if not self.invoice_cart:
            lbl = customtkinter.CTkLabel(self.cart_scroll, text="Δεν έχουν προστεθεί είδη στο παραστατικό.", text_color=_subtle_text())
            lbl.grid(row=0, column=0, pady=40, sticky="ew")
            self.sum_items_count.configure(text="Συνολικά Τεμάχια: 0")
            self.sum_subtotal.configure(text="Υποσύνολο: €0.00")
            self.sum_vat.configure(text=f"ΦΠΑ ({vat_rate*100:.1f}%): €0.00")
            self.sum_total.configure(text="Γενικό Σύνολο: €0.00")
            return

        for idx, (p, qty) in enumerate(self.invoice_cart):
            row_bg = _zebra_row(idx)
            row_frame = customtkinter.CTkFrame(self.cart_scroll, fg_color=row_bg, corner_radius=6)
            self.cart_rows_tracked.append(row_frame)
            row_frame.grid(row=idx, column=0, sticky="ew", pady=3, padx=2)
            row_frame.grid_columnconfigure(0, weight=2)
            row_frame.grid_columnconfigure(1, weight=1)
            row_frame.grid_columnconfigure(2, weight=1)
            row_frame.grid_columnconfigure(3, weight=1)
            row_frame.grid_columnconfigure(4, weight=1)

            row_total = p.price * qty

            customtkinter.CTkLabel(row_frame, text=p.name, font=customtkinter.CTkFont(weight="bold")).grid(row=0, column=0, padx=(15, 5), pady=8, sticky="w")
            customtkinter.CTkLabel(row_frame, text=f"€{p.price:.2f}").grid(row=0, column=1, padx=15, pady=8, sticky="e")
            customtkinter.CTkLabel(row_frame, text=f"{qty} τεμ.").grid(row=0, column=2, padx=15, pady=8, sticky="e")
            customtkinter.CTkLabel(row_frame, text=f"€{row_total:.2f}", font=customtkinter.CTkFont(weight="bold")).grid(row=0, column=3, padx=15, pady=8, sticky="e")

            del_btn = customtkinter.CTkButton(
                row_frame, text="❌", width=25, height=25,
                fg_color=("gray80", "gray30"), hover_color="#A30000",
                command=lambda b=p.barcode: self.remove_item_from_cart(b)
            )
            del_btn.grid(row=0, column=4, padx=(5, 15), pady=4, sticky="e")

        subtotal = sum(p.price * q for p, q in self.invoice_cart)
        vat_amount, grand_total = calculate_invoice_totals(self.invoice_cart, vat_rate)
        total_items = sum(q for p, q in self.invoice_cart)

        self.sum_items_count.configure(text=f"Συνολικά Τεμάχια: {total_items}")
        self.sum_subtotal.configure(text=f"Υποσύνολο: €{subtotal:.2f}")
        self.sum_vat.configure(text=f"ΦΠΑ ({vat_rate*100:.1f}%): €{vat_amount:.2f}")
        self.sum_total.configure(text=f"Γενικό Σύνολο: €{grand_total:.2f}")

    def clear_cart(self):
        self.invoice_cart = []
        self.refresh_cart_list()

    def process_checkout(self):
        """Perform transactional checkout with accumulated failure reporting."""
        if not self.invoice_cart:
            messagebox.showwarning(
                "Προειδοποίηση",
                "Αδύνατη η ολοκλήρωση: Το καλάθι είναι κενό.",
            )
            return

        vat_rate = float(self.config["vat_rate"])
        vat_amount, grand_total = calculate_invoice_totals(self.invoice_cart, vat_rate)
        subtotal = sum(p.price * q for p, q in self.invoice_cart)
        invoice_id = f"INV-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        invoice_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        succeeded: List[Tuple[Product, int]] = []
        failed_items: List[Tuple[str, str]] = []  # (name, reason)

        # ── Atomic transaction: validate ALL items first, then commit or rollback ──
        conn = None
        try:
            conn = self.db_service._get_connection()
            conn.execute("BEGIN TRANSACTION")

            for p, qty in self.invoice_cart:
                db_p = self.db_service.get_product(p.barcode)
                if not db_p:
                    failed_items.append((p.name, "Το προϊόν δεν βρέθηκε στη βάση δεδομένων."))
                    continue
                if db_p.stock < qty:
                    failed_items.append((
                        p.name,
                        f"Απαιτούνται {qty} τεμ., διαθέσιμα μόνο {db_p.stock}.",
                    ))
                    continue
                new_stock = db_p.stock - qty
                conn.execute(
                    "UPDATE ProductMaster SET Stock = ? WHERE Barcode = ?",
                    (new_stock, p.barcode))
                # ── Audit trail — inside same transaction, rolls back atomically ──
                conn.execute(
                    "INSERT INTO stock_movements "
                    "(timestamp, barcode, product_name, old_stock, new_stock, "
                    " change_amount, reason, source) "
                    "VALUES (datetime('now','localtime'), ?, ?, ?, ?, ?, ?, ?)",
                    (p.barcode, p.name, db_p.stock, new_stock,
                     new_stock - db_p.stock, "Πώληση", "POS"))
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
            failed_items.append(("Σύστημα", "Κρίσιμο σφάλμα βάσης δεδομένων. Η συναλλαγή ακυρώθηκε."))
        finally:
            if conn:
                conn.close()

        # --- Handle partial / total failure ---
        if failed_items:
            summary_lines = [
                "Τα παρακάτω είδη ΔΕΝ ολοκληρώθηκαν:\n",
            ]
            for name, reason in failed_items:
                summary_lines.append(f"  • {name}: {reason}")
            messagebox.showerror(
                "Αδυναμία Ολοκλήρωσης Παραστατικού",
                "\n".join(summary_lines),
            )
            # Keep only the failed items in the cart; remove succeeded ones
            failed_barcodes = set()
            for name, _ in failed_items:
                for p, _ in self.invoice_cart:
                    if p.name == name:
                        failed_barcodes.add(p.barcode)
            self.invoice_cart = [
                (p, q) for p, q in self.invoice_cart
                if p.barcode in failed_barcodes
            ]
            self.refresh_cart_list()
            return

        # --- Full success: destroy tracked rows, clear cart, show receipt ---
        for row in self.cart_rows_tracked:
            try:
                row.destroy()
            except Exception:
                pass
        self.cart_rows_tracked.clear()

        # Persist the invoice transaction atomically (non-fatal on DB error)
        try:
            self.db_service.save_invoice_transaction(
                invoice_id, subtotal, vat_amount, grand_total,
                self.invoice_cart,
                customer_id=getattr(self, '_selected_customer_id', None),
            )
        except Exception:
            logging.exception(
                "Failed to persist invoice %s — checkout continues.", invoice_id
            )

        receipt = (
            "==================================\n"
            "       ΑΠΟΔΕΙΞΗ ENCOMM       \n"
            "==================================\n"
            f"Αριθμός Παραστατικού: {invoice_id}\n"
            f"Ημερομηνία: {invoice_date}\n"
            "----------------------------------\n"
        )
        for p, qty in succeeded:
            receipt += f"{p.name[:20]:<20} x{qty:<2}  €{(p.price * qty):.2f}\n"
        receipt += (
            "----------------------------------\n"
            f"Υποσύνολο:               €{subtotal:.2f}\n"
            f"ΦΠΑ ({vat_rate * 100:.1f}%):            €{vat_amount:.2f}\n"
            f"ΣΥΝΟΛΟ:                  €{grand_total:.2f}\n"
            "==================================\n"
            "Ευχαριστούμε για την αγορά!\n"
        )

        messagebox.showinfo("Παραστατικό Καταχωρήθηκε", receipt)

        self.invoice_cart = []
        # Reset customer selector to default after successful checkout
        self._selected_customer_id = None
        if hasattr(self, 'pos_customer_var'):
            self.pos_customer_var.set("Λιανική Πώληση (Κανένας)")
        self.refresh_cart_list()
        self.refresh_dashboard()
        self.refresh_inventory_list()
        self.refresh_invoice_view()

        # ── Push undo entry for this sale ──
        _succeeded = list(succeeded)  # copy for closure
        _inv_id = invoice_id
        _inv_date = invoice_date
        _sub = subtotal
        _vat = vat_amount
        _gt = grand_total
        def undo_sale():
            conn2 = self.db_service._get_connection()
            try:
                for p, qty in _succeeded:
                    conn2.execute(
                        "UPDATE ProductMaster SET Stock = Stock + ? WHERE Barcode = ?",
                        (qty, p.barcode))
                conn2.commit()
            except Exception:
                try:
                    conn2.rollback()
                except Exception:
                    pass
            finally:
                conn2.close()
            self.db_service.delete_invoice(_inv_id)
            self.refresh_invoice_view()
            self.refresh_dashboard()
            self.refresh_inventory_list()
        def redo_sale():
            conn2 = self.db_service._get_connection()
            try:
                for p, qty in _succeeded:
                    conn2.execute(
                        "UPDATE ProductMaster SET Stock = Stock - ? WHERE Barcode = ?",
                        (qty, p.barcode))
                conn2.commit()
            except Exception:
                try:
                    conn2.rollback()
                except Exception:
                    pass
            finally:
                conn2.close()
            items_list = [(p.barcode, p.name, qty, p.price) for p, qty in _succeeded]
            self.db_service.save_invoice_transaction(
                _inv_id, _sub, _vat, _gt, _succeeded,
                customer_id=getattr(self, '_selected_customer_id', None))
            self.refresh_invoice_view()
            self.refresh_dashboard()
            self.refresh_inventory_list()
        self.action_history.push(
            f"Πώληση {_inv_id} — {len(_succeeded)} είδη, €{_gt:.2f}",
            undo_sale, redo_sale)
        self._update_undo_redo_buttons()

    def _export_invoice_pdf(self, invoice_id: str) -> None:
        """Export a text-format invoice receipt to the Desktop in a background thread."""
        items = self.db_service.get_invoice_items(invoice_id)
        invoices = self.db_service.get_all_invoices(search_id=invoice_id)

        def _write_receipt():
            try:
                invoice = invoices[0] if invoices else {"date": "", "subtotal": 0.0, "vat": 0.0, "total": 0.0}
                lines = []
                lines.append("=" * 40)
                lines.append("       ENCOMM INVOICE RECEIPT")
                lines.append("=" * 40)
                lines.append(f"Invoice #:    {invoice_id}")
                lines.append(f"Date:         {invoice.get('date', '')}")
                lines.append("-" * 40)
                lines.append(f"{'Item':<25} {'Qty':<6} {'Price':<10} {'Total':<10}")
                lines.append("-" * 40)
                for it in items:
                    qty = it.get("quantity", 0)
                    price = it.get("price", 0.0)
                    line_total = qty * price
                    lines.append(f"{it.get('name', '')[:25]:<25} {qty:<6} {price:<8.2f} {line_total:<8.2f}")
                lines.append("-" * 40)
                lines.append(f"{'Subtotal:':<35} €{invoice.get('subtotal', 0.0):.2f}")
                lines.append(f"{'VAT:':<35} €{invoice.get('vat', 0.0):.2f}")
                lines.append(f"{'TOTAL:':<35} €{invoice.get('total', 0.0):.2f}")
                lines.append("=" * 40)
                content = "\n".join(lines)

                dest = os.path.join(os.path.expanduser("~"), "Desktop", f"Invoice_{invoice_id}.txt")
                with open(dest, "w", encoding="utf-8") as f:
                    f.write(content)

                self.after(0, lambda: messagebox.showinfo("Επιτυχής Εξαγωγή", f"Το παραστατικό αποθηκεύτηκε στην Επιφάνεια Εργασίας ως: Invoice_{invoice_id}.txt"))

            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Error", str(e)))

        threading.Thread(target=_write_receipt, daemon=True).start()

    # =========================================================================
    # VIEW: CUSTOMERS
    # =========================================================================
    def _init_customers_frame(self):
        self.customers_frame = customtkinter.CTkFrame(self.main_container, fg_color="transparent")
        self.customers_frame.grid_columnconfigure(0, weight=1)
        self.customers_frame.grid_rowconfigure(2, weight=1)

        # Search bar
        search_bar = customtkinter.CTkFrame(self.customers_frame, fg_color="transparent")
        search_bar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.cust_search_entry = customtkinter.CTkEntry(search_bar, placeholder_text="Αναζήτηση πελάτη...", width=300)
        self.cust_search_entry.pack(side="left", padx=(0, 10))
        self.cust_search_entry.bind("<KeyRelease>", lambda e: self.refresh_customer_list())
        customtkinter.CTkButton(search_bar, text="🔍", width=40,
            command=self.refresh_customer_list).pack(side="left")

        # Form
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
        customtkinter.CTkButton(form, text="💾 Αποθήκευση", fg_color=("#2ecc71", "#27ae60"), hover_color=("#27ae60", "#1e8449"),
            text_color=("#FFFFFF", "#FFFFFF"), command=self.save_customer).grid(row=0, column=6, padx=(10, 0))
        customtkinter.CTkButton(form, text="❌ Διαγραφή Πελάτη", fg_color=("#E74C3C", "#C0392B"), hover_color=("#C0392B", "#A93226"),
            text_color=("#FFFFFF", "#FFFFFF"), command=self.delete_customer).grid(row=0, column=7, padx=(10, 0))

        # Treeview
        self.cust_tree = ttk.Treeview(self.customers_frame,
            columns=("id", "name", "amka", "phone"), show="headings", height=15)
        self.cust_tree.heading("id", text="ID")
        self.cust_tree.heading("name", text="Όνομα")
        self.cust_tree.heading("amka", text="ΑΜΚΑ")
        self.cust_tree.heading("phone", text="Τηλέφωνο")
        self.cust_tree.column("id", width=50)
        self.cust_tree.column("name", width=250)
        self.cust_tree.column("amka", width=120)
        self.cust_tree.column("phone", width=120)
        self.cust_tree.grid(row=2, column=0, sticky="nsew")

    def refresh_customer_list(self):
        if not hasattr(self, 'customers_frame') or self.customers_frame is None:
            return
        query = self.cust_search_entry.get().strip() if hasattr(self, 'cust_search_entry') else ""
        if query:
            rows = self.db_service.search_customers(query)
        else:
            rows = self.db_service.get_all_customers()
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
            self.cust_name_entry.delete(0, "end")
            self.cust_amka_entry.delete(0, "end")
            self.cust_phone_entry.delete(0, "end")
            self.refresh_customer_list()
            messagebox.showinfo("Επιτυχία", f"Ο πελάτης '{name}' αποθηκεύτηκε.")
        else:
            messagebox.showerror("Σφάλμα", "Αποτυχία αποθήκευσης (πιθανή διπλότυπη ΑΜΚΑ).")

    def delete_customer(self):
        """Delete the selected customer after confirmation — undoable."""
        sel = self.cust_tree.selection()
        if not sel:
            messagebox.showwarning("Προειδοποίηση", "Παρακαλώ επιλέξτε έναν πελάτη από τη λίστα.")
            return
        customer_id = self.cust_tree.item(sel[0])["values"][0]
        name = self.cust_tree.item(sel[0])["values"][1]
        if not messagebox.askyesno("Επιβεβαίωση Διαγραφής",
            f"Είστε βέβαιοι ότι θέλετε να διαγράψετε τον πελάτη '{name}';\n"
            f"(Μπορείτε να τον επαναφέρετε με ↩ Αναίρεση)", icon="warning"):
            return
        _old = self.db_service.get_customer_by_id(int(customer_id))
        _cid = int(customer_id)
        if self.db_service.delete_customer(_cid):
            if _old:
                def undo_cust():
                    self.db_service.restore_customer(dict(_old))
                    self.refresh_customer_list()
                def redo_cust():
                    self.db_service.delete_customer(_cid)
                    self.refresh_customer_list()
                self.action_history.push(
                    f"Διαγραφή πελάτη: {name}", undo_cust, redo_cust)
                self._update_undo_redo_buttons()
            messagebox.showinfo("Επιτυχία", f"Ο πελάτης '{name}' διαγράφηκε επιτυχώς.")
            self.refresh_customer_list()
        else:
            messagebox.showerror("Σφάλμα", "Αποτυχία διαγραφής πελάτη.")
    # =========================================================================
    # VIEW: SUPPLIERS
    # =========================================================================
    def _init_suppliers_frame(self):
        self.suppliers_frame = customtkinter.CTkFrame(self.main_container, fg_color="transparent")
        self.suppliers_frame.grid_columnconfigure(0, weight=1)
        self.suppliers_frame.grid_rowconfigure(2, weight=1)

        # Search bar
        search_bar = customtkinter.CTkFrame(self.suppliers_frame, fg_color="transparent")
        search_bar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.supp_search_entry = customtkinter.CTkEntry(search_bar, placeholder_text="Αναζήτηση προμηθευτή...", width=300)
        self.supp_search_entry.pack(side="left", padx=(0, 10))
        self.supp_search_entry.bind("<KeyRelease>", lambda e: self.refresh_supplier_list())
        customtkinter.CTkButton(search_bar, text="🔍", width=40, command=self.refresh_supplier_list).pack(side="left")

        # Form
        form = customtkinter.CTkFrame(self.suppliers_frame, fg_color="transparent")
        form.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        customtkinter.CTkLabel(form, text="Όνομα:", font=customtkinter.CTkFont(weight="bold")).grid(row=0, column=0, padx=(0,5))
        self.supp_name_entry = customtkinter.CTkEntry(form, width=180)
        self.supp_name_entry.grid(row=0, column=1, padx=5)
        customtkinter.CTkLabel(form, text="Τηλέφωνο:", font=customtkinter.CTkFont(weight="bold")).grid(row=0, column=2, padx=5)
        self.supp_phone_entry = customtkinter.CTkEntry(form, width=140)
        self.supp_phone_entry.grid(row=0, column=3, padx=5)
        customtkinter.CTkLabel(form, text="Email:", font=customtkinter.CTkFont(weight="bold")).grid(row=0, column=4, padx=5)
        self.supp_email_entry = customtkinter.CTkEntry(form, width=160)
        self.supp_email_entry.grid(row=0, column=5, padx=5)
        customtkinter.CTkLabel(form, text="Διεύθυνση:", font=customtkinter.CTkFont(weight="bold")).grid(row=1, column=0, padx=(0,5), pady=(10,0))
        self.supp_addr_entry = customtkinter.CTkEntry(form, width=300)
        self.supp_addr_entry.grid(row=1, column=1, columnspan=3, padx=5, pady=(10,0), sticky="ew")
        customtkinter.CTkButton(form, text="💾 Αποθήκευση", fg_color=("#2ecc71", "#27ae60"), hover_color=("#27ae60", "#1e8449"),
            text_color=("#FFFFFF", "#FFFFFF"), command=self.save_supplier).grid(row=1, column=5, padx=(10, 0), pady=(10, 0))
        customtkinter.CTkButton(form, text="❌ Διαγραφή", fg_color=("#E74C3C", "#C0392B"), hover_color=("#C0392B", "#A93226"),
            text_color=("#FFFFFF", "#FFFFFF"), command=self.delete_supplier).grid(row=1, column=6, padx=(10, 0), pady=(10, 0))

        # Treeview
        self.supp_tree = ttk.Treeview(self.suppliers_frame,
            columns=("id", "name", "phone", "email", "address"), show="headings", height=15)
        self.supp_tree.heading("id", text="ID")
        self.supp_tree.heading("name", text="Όνομα")
        self.supp_tree.heading("phone", text="Τηλέφωνο")
        self.supp_tree.heading("email", text="Email")
        self.supp_tree.heading("address", text="Διεύθυνση")
        self.supp_tree.column("id", width=40)
        self.supp_tree.column("name", width=200)
        self.supp_tree.column("phone", width=120)
        self.supp_tree.column("email", width=180)
        self.supp_tree.column("address", width=250)
        self.supp_tree.grid(row=2, column=0, sticky="nsew")

        # ── Automated Order Generation ──
        self.supp_order_btn = customtkinter.CTkButton(
            self.suppliers_frame, text="📋 Αυτόματη Λίστα Παραγγελίας",
            fg_color="#8E44AD", hover_color="#7D3C98",
            font=customtkinter.CTkFont(weight="bold"),
            command=self.generate_automated_orders
        )
        self.supp_order_btn.grid(row=3, column=0, pady=(10, 0))

    def refresh_supplier_list(self):
        if not hasattr(self, 'suppliers_frame') or self.suppliers_frame is None:
            return
        query = self.supp_search_entry.get().strip().lower() if hasattr(self, 'supp_search_entry') else ""
        rows = self.db_service.get_all_suppliers()
        if query:
            rows = [r for r in rows if query in r["name"].lower()]
        self.supp_tree.delete(*self.supp_tree.get_children())
        for r in rows:
            self.supp_tree.insert("", "end", values=(r["id"], r["name"], r["phone"], r["email"], r["address"]))

    def save_supplier(self):
        name = self.supp_name_entry.get().strip()
        phone = self.supp_phone_entry.get().strip()
        email = self.supp_email_entry.get().strip()
        address = self.supp_addr_entry.get().strip()
        if not name:
            messagebox.showwarning("Προειδοποίηση", "Το όνομα είναι υποχρεωτικό.")
            return
        if self.db_service.add_supplier(name, phone, email, address):
            self.supp_name_entry.delete(0, "end")
            self.supp_phone_entry.delete(0, "end")
            self.supp_email_entry.delete(0, "end")
            self.supp_addr_entry.delete(0, "end")
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
            f"Είστε βέβαιοι ότι θέλετε να διαγράψετε τον προμηθευτή '{name}';\n"
            f"(Μπορείτε να τον επαναφέρετε με ↩ Αναίρεση)", icon="warning"):
            return
        _old = self.db_service.get_supplier_by_id(int(supplier_id))
        _sid = int(supplier_id)
        if self.db_service.delete_supplier(_sid):
            if _old:
                def undo_supp():
                    self.db_service.restore_supplier(dict(_old))
                    self.refresh_supplier_list()
                def redo_supp():
                    self.db_service.delete_supplier(_sid)
                    self.refresh_supplier_list()
                self.action_history.push(
                    f"Διαγραφή προμηθευτή: {name}", undo_supp, redo_supp)
                self._update_undo_redo_buttons()
            messagebox.showinfo("Επιτυχία", f"Ο προμηθευτής '{name}' διαγράφηκε επιτυχώς.")
            self.refresh_supplier_list()
        else:
            messagebox.showerror("Σφάλμα", "Αποτυχία διαγραφής προμηθευτή.")

    def generate_automated_orders(self):
        """Generate per-supplier procurement CSV files in a background thread."""
        def _write():
            try:
                grouped = self.db_service.get_low_stock_by_supplier()
                if not grouped:
                    self.after(0, lambda: messagebox.showinfo("Ενημέρωση",
                        "Δεν βρέθηκαν προϊόντα με χαμηλό στοκ που να αντιστοιχούν σε προμηθευτή."))
                    return
                ts = datetime.now().strftime("%Y%m%d")
                count = 0
                for sid, items in grouped.items():
                    sname = items[0]["supplier_name"].replace(" ", "_") if items else f"Supplier_{sid}"
                    dest = os.path.join(os.path.expanduser("~"), "Desktop", f"Order_{sname}_{ts}.csv")
                    lines = ["Barcode,Όνομα Προϊόντος,Τρέχον Στοκ,Προτεινόμενη Ποσότητα Παραγγελίας"]
                    for it in items:
                        suggested = max(50, (10 - it["stock"]) * 5) if it["stock"] < 10 else 50
                        lines.append(f'{it["barcode"]},{it["name"]},{it["stock"]},{suggested}')
                    with open(dest, "w", encoding="utf-8-sig") as f:
                        f.write("\n".join(lines))
                    count += 1
                self.after(0, lambda: messagebox.showinfo("Έτοιμες Παραγγελίες",
                    "Οι λίστες ανεφοδιασμού δημιουργήθηκαν στο Desktop ομαδοποιημένες ανά προμηθευτή!"))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Σφάλμα", str(e)))
        threading.Thread(target=_write, daemon=True).start()

    # =========================================================================
    # VIEW: INVOICE HISTORY
    # =========================================================================
    def _init_invoice_history_frame(self):
        self.invoice_history_frame = customtkinter.CTkFrame(self.main_container, fg_color="transparent")
        self.invoice_history_frame.grid_columnconfigure(0, weight=1)
        self.invoice_history_frame.grid_rowconfigure(1, weight=1)

        # Filter bar
        filt = customtkinter.CTkFrame(self.invoice_history_frame, fg_color="transparent")
        filt.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        customtkinter.CTkLabel(filt, text="ID:").pack(side="left", padx=(0, 5))
        self.hist_id_entry = customtkinter.CTkEntry(filt, width=180, placeholder_text="INV-...")
        self.hist_id_entry.pack(side="left", padx=(0, 10))
        customtkinter.CTkLabel(filt, text="Ημ/νία:").pack(side="left", padx=(0, 5))
        self.hist_date_entry = customtkinter.CTkEntry(filt, width=120, placeholder_text="YYYY-MM-DD")
        self.hist_date_entry.pack(side="left", padx=(0, 10))
        self.hist_start_entry = customtkinter.CTkEntry(filt, width=130, placeholder_text="Από (YYYY-MM-DD)")
        self.hist_start_entry.pack(side="left", padx=(0, 5))
        self.hist_end_entry = customtkinter.CTkEntry(filt, width=130, placeholder_text="Έως (YYYY-MM-DD)")
        self.hist_end_entry.pack(side="left", padx=(0, 10))
        customtkinter.CTkButton(filt, text="🔍 Φίλτρο", command=self.refresh_invoice_history_list).pack(side="left")

        # Master Treeview
        self.hist_tree = ttk.Treeview(self.invoice_history_frame,
            columns=("id", "date", "subtotal", "vat", "total", "customer"), show="headings", height=20)
        self.hist_tree.heading("id", text="Αρ. Παραστατικού")
        self.hist_tree.heading("date", text="Ημερομηνία")
        self.hist_tree.heading("subtotal", text="Υποσύνολο")
        self.hist_tree.heading("vat", text="ΦΠΑ")
        self.hist_tree.heading("total", text="Σύνολο")
        self.hist_tree.heading("customer", text="Πελάτης")
        self.hist_tree.column("id", width=160)
        self.hist_tree.column("date", width=150)
        self.hist_tree.column("subtotal", width=90)
        self.hist_tree.column("vat", width=80)
        self.hist_tree.column("total", width=90)
        self.hist_tree.column("customer", width=150)
        self.hist_tree.grid(row=1, column=0, sticky="nsew")
        self.hist_tree.bind("<Double-1>", self._on_invoice_double_click)

        # ── Smart Export control bar ──
        self.hist_export_bar = customtkinter.CTkFrame(self.invoice_history_frame, fg_color="transparent")
        self.hist_export_bar.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        self.hist_export_start = customtkinter.CTkEntry(self.hist_export_bar, width=120, placeholder_text="Από (YYYY-MM-DD)")
        self.hist_export_start.pack(side="left", padx=(0, 5))
        self.hist_export_end = customtkinter.CTkEntry(self.hist_export_bar, width=120, placeholder_text="Έως (YYYY-MM-DD)")
        self.hist_export_end.pack(side="left", padx=(0, 8))
        self.hist_export_format = customtkinter.CTkOptionMenu(self.hist_export_bar, values=["Excel (.csv)", "PDF (.txt style)"], width=140)
        self.hist_export_format.pack(side="left", padx=(0, 8))
        self.hist_export_btn = customtkinter.CTkButton(self.hist_export_bar, text="📤 Εξαγωγή Ιστορικού", fg_color="#2980B9", hover_color="#1F618D",
            font=customtkinter.CTkFont(weight="bold"), command=self.export_invoice_history)
        self.hist_export_btn.pack(side="left")

    def export_invoice_history(self):
        """Export filtered invoice history to Desktop in a background thread (dedicated date entries)."""
        fmt = self.hist_export_format.get()
        is_csv = "csv" in fmt.lower()
        start_date = self.hist_export_start.get().strip() if hasattr(self, 'hist_export_start') else ""
        end_date = self.hist_export_end.get().strip() if hasattr(self, 'hist_export_end') else ""
        sid = self.hist_id_entry.get().strip() if hasattr(self, 'hist_id_entry') else ""

        def _write():
            try:
                invoices = self.db_service.get_all_invoices(
                    search_id=sid, start_date=start_date or None, end_date=end_date or None)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                if is_csv:
                    dest = os.path.join(os.path.expanduser("~"), "Desktop", f"Invoices_Ledger_{ts}.csv")
                    lines = ["Αρ.Παραστατικού,Ημερομηνία,Υποσύνολο,ΦΠΑ,Γενικό Σύνολο,Πελάτης"]
                    for inv in invoices:
                        lines.append(f'{_csv_cell(inv["id"])},{_csv_cell(inv["date"])},{inv["subtotal"]:.2f},{inv["vat"]:.2f},{inv["total"]:.2f},{_csv_cell(inv["customer_name"])}')
                    with open(dest, "w", encoding="utf-8-sig") as f:
                        f.write("\n".join(lines))
                else:
                    dest = os.path.join(os.path.expanduser("~"), "Desktop", f"Invoices_Ledger_{ts}.txt")
                    lines = ["=" * 65, "  ENCOMM — ΚΑΘΟΛΙΚΟ ΠΑΡΑΣΤΑΤΙΚΩΝ", "=" * 65,
                             f"Ημ/νία: {datetime.now().strftime('%d/%m/%Y %H:%M')}  |  Εύρος: {start_date or '—'} έως {end_date or '—'}",
                             f"Παραστατικά: {len(invoices)}", "=" * 65]
                    for inv in invoices:
                        lines.append(f"\n📋 {inv['id']}  |  {inv['date']}  |  Πελάτης: {inv['customer_name'] or 'Λιανική'}")
                        lines.append(f"   Υποσύνολο: €{inv['subtotal']:.2f}  |  ΦΠΑ: €{inv['vat']:.2f}  |  Σύνολο: €{inv['total']:.2f}")
                        items = self.db_service.get_invoice_items(inv["id"])
                        if items:
                            lines.append(f"   {'Είδος':<30} {'Ποσ.':<6} {'Τιμή':<8} {'Σύνολο':<10}")
                            for it in items:
                                qty = it.get("quantity", 0)
                                price = it.get("price", 0.0)
                                lines.append(f"   {it.get('name', '')[:30]:<30} {qty:<6} €{price:<7.2f} €{qty*price:<9.2f}")
                    lines.append("\n" + "=" * 65)
                    with open(dest, "w", encoding="utf-8") as f:
                        f.write("\n".join(lines))
                self.after(0, lambda: messagebox.showinfo("Επιτυχής Εξαγωγή",
                    "Το φιλτραρισμένο αρχείο βάσει ημερομηνιών αποθηκεύτηκε στην Επιφάνεια Εργασίας!"))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Σφάλμα Εξαγωγής", str(e)))
        threading.Thread(target=_write, daemon=True).start()

    def refresh_invoice_history_list(self):
        if not hasattr(self, 'invoice_history_frame') or self.invoice_history_frame is None:
            return
        sid = self.hist_id_entry.get().strip() if hasattr(self, 'hist_id_entry') else ""
        sdate = self.hist_date_entry.get().strip() if hasattr(self, 'hist_date_entry') else ""
        start_date = self.hist_start_entry.get().strip() if hasattr(self, 'hist_start_entry') else ""
        end_date = self.hist_end_entry.get().strip() if hasattr(self, 'hist_end_entry') else ""
        rows = self.db_service.get_all_invoices(sid, start_date=start_date or None, end_date=end_date or None)
        self.hist_tree.delete(*self.hist_tree.get_children())
        for r in rows:
            self.hist_tree.insert("", "end", values=(
                r["id"], r["date"], f'€{r["subtotal"]:.2f}',
                f'€{r["vat"]:.2f}', f'€{r["total"]:.2f}', r["customer_name"],
            ))

    def _on_invoice_double_click(self, event):
        sel = self.hist_tree.selection()
        if not sel:
            return
        invoice_id = self.hist_tree.item(sel[0])["values"][0]
        items = self.db_service.get_invoice_items(invoice_id)
        if not items:
            messagebox.showinfo("Λεπτομέρειες", "Δεν βρέθηκαν είδη για αυτό το παραστατικό.")
            return
        popup = customtkinter.CTkToplevel(self)
        popup.title(f"Παραστατικό {invoice_id}")
        popup.geometry("550x400")
        popup.transient(self)
        popup.grab_set()
        popup.deiconify()
        popup.update_idletasks()
        px = self.winfo_x() + (self.winfo_width() - 550) // 2
        py = self.winfo_y() + (self.winfo_height() - 400) // 2
        popup.geometry(f"550x400+{px}+{py}")
        customtkinter.CTkLabel(popup, text=f"Παραστατικό: {invoice_id}",
            font=customtkinter.CTkFont(size=14, weight="bold")).pack(pady=10)
        tree = ttk.Treeview(popup, columns=("barcode", "name", "qty", "price", "total"), show="headings", height=15)
        tree.heading("barcode", text="Barcode")
        tree.heading("name", text="Όνομα")
        tree.heading("qty", text="Ποσ.")
        tree.heading("price", text="Τιμή")
        tree.heading("total", text="Σύνολο")
        tree.column("barcode", width=120)
        tree.column("name", width=180)
        tree.column("qty", width=60)
        tree.column("price", width=80)
        tree.column("total", width=80)
        tree.pack(padx=15, pady=10, fill="both", expand=True)
        grand = 0
        for it in items:
            row_total = it["quantity"] * it["price"]
            grand += row_total
            tree.insert("", "end", values=(it["barcode"], it["name"], it["quantity"],
                f'€{it["price"]:.2f}', f'€{row_total:.2f}'))
        customtkinter.CTkLabel(popup, text=f"Γενικό Σύνολο: €{grand:.2f}",
            font=customtkinter.CTkFont(size=13, weight="bold")).pack(pady=5)
        customtkinter.CTkButton(popup, text="📄 Εξαγωγή & Εκτύπωση PDF", fg_color="#2980B9", hover_color="#1F618D",
            font=customtkinter.CTkFont(weight="bold"), command=lambda: self._export_invoice_pdf(invoice_id)).pack(pady=5, padx=(0, 5))
        customtkinter.CTkButton(popup, text="Κλείσιμο", command=popup.destroy).pack(pady=5)

    # =========================================================================
    # VIEW: SETTINGS
    # =========================================================================
    def _init_settings_frame(self):
        self.settings_frame = customtkinter.CTkFrame(self.main_container, fg_color="transparent")
        self.settings_card = customtkinter.CTkScrollableFrame(self.settings_frame, label_text="")
        self.settings_card.pack(padx=20, pady=20, fill="both", expand=True)

        self.set_title = customtkinter.CTkLabel(self.settings_card, text="Ρυθμίσεις Συστήματος", font=customtkinter.CTkFont(size=18, weight="bold"))
        self.set_title.pack(padx=30, pady=(30, 20), anchor="w")

        # --- Business Rules ---
        self._add_section_label("⚙️ Επιχειρηματικοί Κανόνες")

        self.set_vat_lbl = customtkinter.CTkLabel(self.settings_card, text="Ποσοστό ΦΠΑ (π.χ. 0.24 για 24%):", font=customtkinter.CTkFont(weight="bold"))
        self.set_vat_lbl.pack(padx=30, pady=(5, 2), anchor="w")
        self.set_vat_entry = customtkinter.CTkEntry(self.settings_card, width=300)
        self.set_vat_entry.pack(padx=30, pady=(0, 15), anchor="w")

        self.set_stock_lbl = customtkinter.CTkLabel(self.settings_card, text="Όριο Προειδοποίησης Χαμηλού Στοκ (Τεμάχια):", font=customtkinter.CTkFont(weight="bold"))
        self.set_stock_lbl.pack(padx=30, pady=(5, 2), anchor="w")
        self.set_stock_entry = customtkinter.CTkEntry(self.settings_card, width=300)
        self.set_stock_entry.pack(padx=30, pady=(0, 15), anchor="w")

        self.set_exp_lbl = customtkinter.CTkLabel(self.settings_card, text="Όριο Προειδοποίησης Λήξης (Ημέρες):", font=customtkinter.CTkFont(weight="bold"))
        self.set_exp_lbl.pack(padx=30, pady=(5, 2), anchor="w")
        self.set_exp_entry = customtkinter.CTkEntry(self.settings_card, width=300)
        self.set_exp_entry.pack(padx=30, pady=(0, 15), anchor="w")

        # --- Licensing ---
        self._add_section_label("🔐 Άδεια Χρήση & Διασυνδέσεις")

        self.set_hwid_lbl = customtkinter.CTkLabel(self.settings_card, text="Hardware ID (HWID):", font=customtkinter.CTkFont(weight="bold"))
        self.set_hwid_lbl.pack(padx=30, pady=(5, 2), anchor="w")
        self.set_hwid_entry = customtkinter.CTkEntry(self.settings_card, width=300)
        self.set_hwid_entry.pack(padx=30, pady=(0, 15), anchor="w")
        self.set_hwid_entry.insert(0, self.cached_hwid if self.cached_hwid else "⏳ Υπολογισμός αναγνωριστικού...")
        self.set_hwid_entry.configure(state="disabled", text_color="gray50")

        self.set_license_lbl = customtkinter.CTkLabel(self.settings_card, text="License Key:", font=customtkinter.CTkFont(weight="bold"))
        self.set_license_lbl.pack(padx=30, pady=(5, 2), anchor="w")
        self.set_license_entry = customtkinter.CTkEntry(self.settings_card, width=300)
        self.set_license_entry.pack(padx=30, pady=(0, 15), anchor="w")

        self.set_mydata_lbl = customtkinter.CTkLabel(self.settings_card, text="myDATA Κωδικός (AADE User):", font=customtkinter.CTkFont(weight="bold"))
        self.set_mydata_lbl.pack(padx=30, pady=(5, 2), anchor="w")
        self.set_mydata_entry = customtkinter.CTkEntry(self.settings_card, width=300)
        self.set_mydata_entry.pack(padx=30, pady=(0, 15), anchor="w")

        self.set_hdika_lbl = customtkinter.CTkLabel(self.settings_card, text="ΗΔΙΚΑ Κωδικός:", font=customtkinter.CTkFont(weight="bold"))
        self.set_hdika_lbl.pack(padx=30, pady=(5, 2), anchor="w")
        self.set_hdika_entry = customtkinter.CTkEntry(self.settings_card, width=300)
        self.set_hdika_entry.pack(padx=30, pady=(0, 15), anchor="w")

        # --- Theme ---
        self._add_section_label("🎨 Εμφάνιση")

        self.set_theme_menu = customtkinter.CTkOptionMenu(
            self.settings_card, values=["Σκούρο", "Φωτεινό"],
            width=300, command=self.change_appearance_theme
        )
        self.set_theme_menu.pack(padx=30, pady=(0, 25), anchor="w")

        # --- Backup & Restore ---
        self._add_section_label("💾 Ασφάλεια Δεδομένων")

        self.set_autobackup_var = tk.BooleanVar(value=False)
        self.set_autobackup_cb = customtkinter.CTkCheckBox(
            self.settings_card,
            text="Αυτόματο αντίγραφο ασφαλείας κατά το κλείσιμο",
            variable=self.set_autobackup_var,
            font=customtkinter.CTkFont(size=13),
        )
        self.set_autobackup_cb.pack(padx=30, pady=(5, 15), anchor="w")

        self.backup_btn_frame = customtkinter.CTkFrame(self.settings_card, fg_color="transparent")
        self.backup_btn_frame.pack(padx=30, pady=(5, 10), anchor="w")

        self.backup_now_btn = customtkinter.CTkButton(
            self.backup_btn_frame, text="💾 Δημιουργία Αντιγράφου Ασφαλείας",
            font=customtkinter.CTkFont(weight="bold"),
            fg_color="#2980B9", hover_color="#1F618D",
            text_color=("#FFFFFF", "#FFFFFF"),
            command=self.backup_database_now
        )
        self.backup_now_btn.pack(side="left", padx=(0, 10))

        self.restore_btn = customtkinter.CTkButton(
            self.backup_btn_frame, text="📂 Επαναφορά από Αντίγραφο",
            font=customtkinter.CTkFont(weight="bold"),
            fg_color=("#E74C3C", "#C0392B"), hover_color=("#C0392B", "#A93226"),
            text_color=("#FFFFFF", "#FFFFFF"),
            command=self.restore_database_from_backup
        )
        self.restore_btn.pack(side="left")

        self.last_backup_lbl = customtkinter.CTkLabel(
            self.settings_card,
            text="Τελευταίο αντίγραφο: Κανένα",
            font=customtkinter.CTkFont(size=11),
            text_color=("gray50", "gray60"),
        )
        self.last_backup_lbl.pack(padx=30, pady=(5, 20), anchor="w")

        self.settings_btn_frame = customtkinter.CTkFrame(self.settings_card, fg_color="transparent")
        self.settings_btn_frame.pack(padx=30, pady=10, anchor="w")

        self.save_settings_btn = customtkinter.CTkButton(
            self.settings_btn_frame, text="💾 Αποθήκευση Ρυθμίσεων",
            font=customtkinter.CTkFont(weight="bold"),
            fg_color=("#2ecc71", "#27ae60"), hover_color=("#27ae60", "#1e8449"),
            text_color=("#FFFFFF", "#FFFFFF"),
            command=self.save_settings_values
        )
        self.save_settings_btn.pack(side="left", padx=(0, 10))

        self.refresh_settings_btn = customtkinter.CTkButton(
            self.settings_btn_frame, text="🔄 Επαναφορά",
            font=customtkinter.CTkFont(size=12),
            fg_color=("#E74C3C", "#C0392B"), hover_color=("#C0392B", "#A93226"),
            text_color=("#FFFFFF", "#FFFFFF"),
            command=self.load_settings_values
        )
        self.refresh_settings_btn.pack(side="left")

        self.load_settings_values()

    def _add_section_label(self, text: str):
        separator = customtkinter.CTkFrame(self.settings_card, height=1, fg_color=("gray70", "gray30"))
        separator.pack(fill="x", padx=30, pady=(20, 5))
        customtkinter.CTkLabel(self.settings_card, text=text, font=customtkinter.CTkFont(size=14, weight="bold"), text_color=("#1A5276", "#5DADE2")).pack(padx=30, pady=(0, 10), anchor="w")

    def load_settings_values(self):
        self.set_vat_entry.configure(state="normal")
        self.set_vat_entry.delete(0, tk.END)
        self.set_vat_entry.insert(0, str(self.config.get("vat_rate", 0.15)))

        self.set_stock_entry.configure(state="normal")
        self.set_stock_entry.delete(0, tk.END)
        self.set_stock_entry.insert(0, str(self.config.get("low_stock_threshold", 10)))

        self.set_exp_entry.configure(state="normal")
        self.set_exp_entry.delete(0, tk.END)
        self.set_exp_entry.insert(0, str(self.config.get("expiry_alert_days", 30)))

        self.set_license_entry.configure(state="normal")
        self.set_license_entry.delete(0, tk.END)
        self.set_license_entry.insert(0, self.db_service.get_config("license_key", ""))

        self.set_mydata_entry.configure(state="normal")
        self.set_mydata_entry.delete(0, tk.END)
        self.set_mydata_entry.insert(0, self.db_service.get_config("mydata_user", ""))

        self.set_hdika_entry.configure(state="normal")
        self.set_hdika_entry.delete(0, tk.END)
        self.set_hdika_entry.insert(0, self.db_service.get_config("hdika_code", ""))

        self.set_hwid_entry.configure(state="normal")
        self.set_hwid_entry.delete(0, tk.END)
        if self.cached_hwid:
            self.set_hwid_entry.insert(0, self.cached_hwid)
        else:
            self.set_hwid_entry.insert(0, "⏳ Υπολογισμός αναγνωριστικού...")
        self.set_hwid_entry.configure(state="disabled", text_color="gray50")

        current = customtkinter.get_appearance_mode()
        self.set_theme_menu.set("Σκούρο" if current == "Dark" else "Φωτεινό")

        # Auto-backup preference
        auto_backup = self.db_service.get_config("auto_backup", "0")
        self.set_autobackup_var.set(auto_backup == "1")

    def change_appearance_theme(self, new_theme: str):
        if new_theme == "Σκούρο":
            customtkinter.set_appearance_mode("Dark")
        else:
            customtkinter.set_appearance_mode("Light")
        self.update()
        self.update_idletasks()

    def save_settings_values(self):
        # --- VAT validation (0.0–1.0, supports Greek decimal comma) ---
        try:
            vat_str = self.set_vat_entry.get().strip().replace(",", ".")
            vat = float(vat_str)
            if vat < 0 or vat > 1:
                messagebox.showerror(
                    "Σφάλμα",
                    "Το ΦΠΑ πρέπει να είναι δεκαδικός (π.χ. 0.24 για 24%, όχι 24)",
                )
                return
        except (ValueError, ArithmeticError):
            messagebox.showerror(
                "Σφάλμα",
                "Το ΦΠΑ πρέπει να είναι δεκαδικός (π.χ. 0.24 για 24%, όχι 24)",
            )
            return

        # --- Low stock threshold validation (integer 0–99999) ---
        try:
            stock_str = self.set_stock_entry.get().strip()
            stock = int(stock_str)
            if stock < 0 or stock > 99999:
                messagebox.showerror(
                    "Σφάλμα",
                    "Το όριο χαμηλού αποθέματος πρέπει να είναι ακέραιος 0–99999",
                )
                return
        except (ValueError, ArithmeticError):
            messagebox.showerror(
                "Σφάλμα",
                "Το όριο χαμηλού αποθέματος πρέπει να είναι ακέραιος αριθμός (π.χ. 10)",
            )
            return

        # --- Expiry alert days validation (integer 1–3650) ---
        try:
            expiry_str = self.set_exp_entry.get().strip()
            expiry = int(expiry_str)
            if expiry < 1 or expiry > 3650:
                messagebox.showerror(
                    "Σφάλμα",
                    "Οι ημέρες προειδοποίησης λήξης πρέπει να είναι ακέραιος 1–3650",
                )
                return
        except (ValueError, ArithmeticError):
            messagebox.showerror(
                "Σφάλμα",
                "Οι ημέρες προειδοποίησης λήξης πρέπει να είναι ακέραιος αριθμός (π.χ. 30)",
            )
            return

        # --- Save numeric configs ---
        self.config["vat_rate"] = vat
        self.config["low_stock_threshold"] = stock
        self.config["expiry_alert_days"] = expiry
        self.db_service.set_config("vat_rate", str(vat))
        self.db_service.set_config("low_stock_threshold", str(stock))
        self.db_service.set_config("expiry_alert_days", str(expiry))

        # --- Trimmed text configs ---
        license_key = self.set_license_entry.get().strip()
        mydata_user = self.set_mydata_entry.get().strip()
        hdika_code = self.set_hdika_entry.get().strip()

        self.db_service.set_config("license_key", license_key)
        self.db_service.set_config("mydata_user", mydata_user)
        self.db_service.set_config("hdika_code", hdika_code)

        # Auto-backup preference
        self.db_service.set_config("auto_backup", "1" if self.set_autobackup_var.get() else "0")

        # --- License key verification ---
        if license_key:
            hwid = self.cached_hwid or generate_hwid()
            is_valid, expires_at = verify_local_license(license_key, hwid)
            if is_valid:
                self.db_service.set_config("license_status", "valid")
                self.db_service.set_config("license_expires", expires_at)
            else:
                self.db_service.set_config("license_status", "invalid")
                self.db_service.set_config("license_expires", "")
                messagebox.showwarning(
                    "Άδεια Χρήσης",
                    "Το License Key δεν είναι έγκυρο για αυτό το σύστημα.\n"
                    "Οι ρυθμίσεις αποθηκεύτηκαν, αλλά η άδεια δεν ενεργοποιήθηκε.",
                )
                self.load_settings_values()
                return

        messagebox.showinfo("Επιτυχία", "Οι ρυθμίσεις συστήματος αποθηκεύτηκαν και εφαρμόστηκαν.")
        self.load_settings_values()
        self.refresh_dashboard()
        self.refresh_inventory_list()

    # ── Backup & Restore Handlers ────────────────────────────────────

    def backup_database_now(self):
        """Create a manual backup from the Settings panel."""
        def _do_backup():
            try:
                path = self.db_service.backup_database()
                fname = os.path.basename(path)
                self.after(0, lambda: [
                    self.last_backup_lbl.configure(
                        text=f"Τελευταίο αντίγραφο: {fname}"),
                    messagebox.showinfo(
                        "Επιτυχές Αντίγραφο",
                        f"Η βάση δεδομένων αποθηκεύτηκε επιτυχώς!\n\n"
                        f"Τοποθεσία: {path}")
                ])
            except Exception as e:
                self.after(0, lambda: messagebox.showerror(
                    "Σφάλμα", f"Αποτυχία δημιουργίας αντιγράφου:\n{e}"))
        threading.Thread(target=_do_backup, daemon=True).start()

    def restore_database_from_backup(self):
        """Restore database from a user-selected backup file."""
        file_path = filedialog.askopenfilename(
            title="Επιλογή Αρχείου Αντιγράφου Ασφαλείας",
            filetypes=[("SQLite Database", "*.db")]
        )
        if not file_path:
            return

        if not messagebox.askyesno(
            "Επιβεβαίωση Επαναφοράς",
            "⚠️ Προειδοποίηση: Η επαναφορά θα αντικαταστήσει "
            "όλα τα τρέχοντα δεδομένα.\n\n"
            "Η εφαρμογή θα κλείσει μετά την επαναφορά.\n"
            "Παρακαλώ ανοίξτε την ξανά χειροκίνητα.\n\n"
            "Είστε βέβαιοι ότι θέλετε να συνεχίσετε;",
            icon="warning"
        ):
            return

        # Safety backup of current state before overwriting
        try:
            self.db_service.backup_database()
        except Exception:
            pass

        if self.db_service.restore_database(file_path):
            messagebox.showinfo(
                "Επιτυχής Επαναφορά",
                "Η βάση δεδομένων επαναφέρθηκε επιτυχώς.\n\n"
                "Η εφαρμογή θα κλείσει τώρα.\n"
                "Παρακαλώ ανοίξτε την ξανά."
            )
            self.on_safe_close()
        else:
            messagebox.showerror(
                "Σφάλμα",
                "Αποτυχία επαναφοράς βάσης δεδομένων.\n"
                "Ελέγξτε ότι το αρχείο είναι έγκυρο αντίγραφο."
            )

    # =========================================================================
    # AI COMMAND BAR — Asynchronous Intent Processing
    # =========================================================================
    def process_ai_command(self):
        """Read the command bar and process via a background Thread to prevent app freezing."""
        user_text = self.ai_cmd_bar.get().strip()
        if not user_text:
            return

        self.ai_status_lbl.configure(text="⏳ Γίνεται επεξεργασία από το Encomm AI...")
        self.ai_cmd_bar.delete(0, tk.END)
        self.update_idletasks()

        def bg_ai_process():
            try:
                raw = self._get_ai_service().send_command_to_llm(user_text)
                intent_obj = self.intent_factory.parse(raw)
                self.after(0, lambda: self._execute_ai_intent(intent_obj))
            except Exception as e:
                logging.exception("AI command processing failed")
                self.after(0, lambda: self.ai_status_lbl.configure(text=f"⚠️ Σφάλμα AI: {str(e)}"))

        threading.Thread(target=bg_ai_process, daemon=True).start()

    def _execute_ai_intent(self, intent_obj: Dict[str, Any]):
        """Execute the routed AI intent safely on the main thread."""
        intent = intent_obj["intent"]
        params = intent_obj["parameters"]

        logging.info(f"AI Action Route Triggered: intent={intent}")

        if intent == "check_low_stock":
            self.select_frame("inventory")
            threshold = int(self.config.get("low_stock_threshold", 10))
            self.search_entry.delete(0, tk.END)
            self._filter_low_stock = True
            self.refresh_inventory_list()
            self.ai_status_lbl.configure(text=f"✅ Εμφάνιση προϊόντων με χαμηλό στοκ (≤{threshold} τεμ.)")

        elif intent == "check_expiry":
            self.select_frame("inventory")
            self.search_entry.delete(0, tk.END)
            self._filter_expiry = True
            self.refresh_inventory_list()
            self.ai_status_lbl.configure(text="✅ Εμφάνιση προϊόντων κοντά στη λήξη ή ληγμένων")

        elif intent == "search_inventory":
            query = params.get("query", "")
            self.select_frame("inventory")
            self.search_entry.delete(0, tk.END)
            self.search_entry.insert(0, query)
            self._inv_search_changed()
            self.ai_status_lbl.configure(text=f"✅ Αναζήτηση: \"{query}\"")

        elif intent == "view_dashboard":
            self.select_frame("dashboard")
            self.ai_status_lbl.configure(text="✅ Μετάβαση στην Αρχική")

        elif intent == "view_inventory":
            self.select_frame("inventory")
            self.ai_status_lbl.configure(text="✅ Μετάβαση στην Αποθήκη")

        elif intent == "view_pos":
            self.select_frame("invoices")
            self.ai_status_lbl.configure(text="✅ Μετάβαση στο Ταμείο")

        elif intent == "view_settings":
            self.select_frame("settings")
            self.ai_status_lbl.configure(text="✅ Μετάβαση στις Ρυθμίσεις")

        else:
            reason = params.get("reason", "Η εντολή δεν αναγνωρίστηκε.")
            self.ai_status_lbl.configure(text=f"⚠️ {reason}")

    # =========================================================================
    # DIALOG ACTIONS
    # =========================================================================
    def _show_commercial_review(self, flagged_items, products):
        """Display price-hike review modal (>8% increase safety gate)."""
        self._hide_import_progress()
        self._review_modal = customtkinter.CTkToplevel(self)
        self._review_modal.title("⚠️ Εμπορικός Έλεγχος Ανατιμήσεων (>8%)")
        self._review_modal.geometry("650x500")
        self._review_modal.resizable(False, False)
        self._review_modal.transient(self)
        self._review_modal.grab_set()
        self._review_modal.deiconify()
        self._review_modal.update_idletasks()
        px = self.winfo_x() + (self.winfo_width() - 650) // 2
        py = self.winfo_y() + (self.winfo_height() - 500) // 2
        self._review_modal.geometry(f"650x500+{px}+{py}")

        customtkinter.CTkLabel(
            self._review_modal, text="⚠️ Εμπορικός Έλεγχος Ανατιμήσεων (>8%)",
            font=customtkinter.CTkFont(size=16, weight="bold"),
            text_color="#E67E22",
        ).pack(pady=(20, 5))
        customtkinter.CTkLabel(
            self._review_modal,
            text="Encomm AI Safety Gate — Εντοπίστηκαν ανατιμήσεις άνω του 8%",
            font=customtkinter.CTkFont(size=12),
            text_color=("gray50", "gray60"),
        ).pack(pady=(0, 15))

        scroll = customtkinter.CTkScrollableFrame(self._review_modal, width=600, height=300)
        scroll.pack(padx=25, pady=10, fill="both", expand=True)

        headers = ["Όνομα", "Παλιά Τιμή", "Νέα Τιμή", "Αύξηση %"]
        for col, h in enumerate(headers):
            customtkinter.CTkLabel(
                scroll, text=h,
                font=customtkinter.CTkFont(size=12, weight="bold"),
                text_color=("gray40", "gray50"),
            ).grid(row=0, column=col, padx=10, pady=5, sticky="w")

        for i, item in enumerate(flagged_items):
            bg = ("#FFF3E0", "#2B1A0A") if i % 2 == 0 else ("#FFE0B2", "#1E1208")
            customtkinter.CTkLabel(
                scroll, text=item["name"][:30],
                font=customtkinter.CTkFont(size=12), fg_color=bg, corner_radius=4,
            ).grid(row=i + 1, column=0, padx=10, pady=2, sticky="ew")
            customtkinter.CTkLabel(
                scroll, text=f'€{item["old_price"]:.2f}',
                font=customtkinter.CTkFont(size=12), fg_color=bg, corner_radius=4,
            ).grid(row=i + 1, column=1, padx=10, pady=2, sticky="e")
            customtkinter.CTkLabel(
                scroll, text=f'€{item["new_price"]:.2f}',
                font=customtkinter.CTkFont(size=12, weight="bold"),
                fg_color=bg, corner_radius=4, text_color="#E74C3C",
            ).grid(row=i + 1, column=2, padx=10, pady=2, sticky="e")
            customtkinter.CTkLabel(
                scroll, text=f'+{item["pct_increase"]:.1f}%',
                font=customtkinter.CTkFont(size=12, weight="bold"),
                fg_color=bg, corner_radius=4, text_color="#E74C3C",
            ).grid(row=i + 1, column=3, padx=10, pady=2, sticky="e")

        btn_frame = customtkinter.CTkFrame(self._review_modal, fg_color="transparent")
        btn_frame.pack(pady=20)
        customtkinter.CTkButton(
            btn_frame, text="✅ Έγκριση από Στέφανο (OK)",
            font=customtkinter.CTkFont(weight="bold"),
            fg_color="#34C759", hover_color="#289A47",
            command=lambda: self._approve_commercial_review(products),
        ).pack(side="left", padx=10)
        customtkinter.CTkButton(
            btn_frame, text="❌ Απόρριψη / Ακύρωση",
            font=customtkinter.CTkFont(weight="bold"),
            fg_color="#E74C3C", hover_color="#C0392B",
            command=self._reject_commercial_review,
        ).pack(side="left", padx=10)

    def _approve_commercial_review(self, products):
        """Destroy review modal and proceed with bulk import in background."""
        if hasattr(self, '_review_modal') and self._review_modal is not None:
            self._review_modal.destroy()
            self._review_modal = None
        self._show_import_progress()

        def bg_commit():
            try:
                st = time.time()
                self.db_service.bulk_upsert_products(products)
                dur = round(time.time() - st, 2)
                self.after(0, lambda: [
                    self._hide_import_progress(),
                    messagebox.showinfo(
                        "Επιτυχία",
                        f"Επεξεργάστηκαν {len(products)} προϊόντα "
                        f"με επιτυχία σε {dur} δευτερόλεπτα!",
                    ),
                    self.refresh_inventory_list(),
                    self.refresh_dashboard(),
                    self.refresh_invoice_view(),
                ])
            except Exception as exc:
                logging.exception("Εγκεκριμένη εισαγωγή απέτυχε")
                self.after(0, lambda: [
                    self._hide_import_progress(),
                    messagebox.showerror(
                        "Σφάλμα Εισαγωγής",
                        f"Αποτυχία κατά την εισαγωγή:\n{exc}",
                    ),
                ])

        threading.Thread(target=bg_commit, daemon=True).start()

    def _reject_commercial_review(self):
        """Dismiss the review modal without importing."""
        if hasattr(self, '_review_modal') and self._review_modal is not None:
            self._review_modal.destroy()
            self._review_modal = None

    def _show_import_progress(self):
        """Display a modal progress overlay during bulk data import."""
        self._import_modal = customtkinter.CTkToplevel(self)
        self._import_modal.title("Επεξεργασία Τιμολογίου")
        self._import_modal.geometry("400x150")
        self._import_modal.resizable(False, False)
        self._import_modal.transient(self)
        self._import_modal.grab_set()
        self._import_modal.deiconify()
        self._import_modal.update_idletasks()
        # Centre overlay on parent window
        px = self.winfo_x() + (self.winfo_width() - 400) // 2
        py = self.winfo_y() + (self.winfo_height() - 150) // 2
        self._import_modal.geometry(f"400x150+{px}+{py}")
        customtkinter.CTkLabel(
            self._import_modal, text="Γίνεται επεξεργασία του αρχείου...",
            font=customtkinter.CTkFont(size=14, weight="bold"),
        ).pack(pady=(30, 10))
        customtkinter.CTkLabel(
            self._import_modal, text="Παρακαλώ περιμένετε",
            font=customtkinter.CTkFont(size=12),
        ).pack()
        self._import_progress = customtkinter.CTkProgressBar(
            self._import_modal, mode="indeterminate", width=300,
        )
        self._import_progress.pack(pady=15)
        self._import_progress.start()

    def _hide_import_progress(self):
        """Safely tear down the import progress modal from the main thread."""
        if hasattr(self, '_import_modal') and self._import_modal is not None:
            self._import_modal.destroy()
            self._import_modal = None

    def import_supplier_invoice(self):
        file_path = filedialog.askopenfilename(
            title="Εισαγωγή Τιμολογίων Προμηθευτή",
            filetypes=[("Excel/CSV Files", "*.xlsx *.xls *.csv")]
        )
        if not file_path:
            return

        self._show_import_progress()

        def bg_import():
            try:
                parser = ExcelParserService()
                products = parser.parse_supplier_file(file_path)

                if not products:
                    self.after(0, lambda: [
                        self._hide_import_progress(),
                        messagebox.showwarning(
                            "Προειδοποίηση",
                            "Δεν βρέθηκαν έγκυρα προϊόντα στο αρχείο.",
                        )
                    ])
                    return

                # ── Commercial Review: cross-reference prices against DB baselines ──
                existing = self.db_service.get_all_products()
                price_lookup = {p.barcode: p.price for p in existing}

                flagged_items = []
                for prod in products:
                    barcode = prod[0]  # products list uses tuple format for bulk_upsert
                    old_price = price_lookup.get(barcode)
                    if old_price is not None and old_price > 0:
                        new_price = prod[4]  # Price is index 4 in tuple
                        pct = (new_price - old_price) / old_price
                        if pct > 0.08:
                            flagged_items.append({
                                "name": prod[1][:30],
                                "barcode": barcode,
                                "old_price": old_price,
                                "new_price": new_price,
                                "pct_increase": pct * 100,
                            })

                if flagged_items:
                    self.after(0, lambda fi=flagged_items, pr=products:
                        self._show_commercial_review(fi, pr))
                    return

                start_time = time.time()
                self.db_service.bulk_upsert_products(products)
                duration = round(time.time() - start_time, 2)

                self.after(0, lambda: [
                    self._hide_import_progress(),
                    messagebox.showinfo(
                        "Επιτυχία",
                        f"Επεξεργάστηκαν {len(products)} προϊόντα "
                        f"με επιτυχία σε {duration} δευτερόλεπτα!",
                    ),
                    self.refresh_inventory_list(),
                    self.refresh_dashboard(),
                    self.refresh_invoice_view(),
                ])
            except Exception as exc:
                logging.exception("Αποτυχία εισαγωγής τιμολογίου")
                self.after(0, lambda: [
                    self._hide_import_progress(),
                    messagebox.showerror(
                        "Σφάλμα Εισαγωγής",
                        f"Αποτυχία κατά την εισαγωγή του αρχείου:\n{exc}",
                    )
                ])

        threading.Thread(target=bg_import, daemon=True).start()

    # =========================================================================
    # VIEW: STOCK MOVEMENTS AUDIT TRAIL
    # =========================================================================
    def _init_stock_movements_frame(self):
        self.stock_movements_frame = customtkinter.CTkFrame(
            self.main_container, fg_color="transparent")
        self.stock_movements_frame.grid_columnconfigure(0, weight=1)
        self.stock_movements_frame.grid_rowconfigure(0, weight=0)  # title
        self.stock_movements_frame.grid_rowconfigure(1, weight=0)  # filter bar
        self.stock_movements_frame.grid_rowconfigure(2, weight=1)  # treeview

        # Filter bar — single row, wraps to stay compact
        self.mov_filter_bar = customtkinter.CTkFrame(
            self.stock_movements_frame, fg_color="transparent")
        self.mov_filter_bar.grid(row=1, column=0, padx=20, pady=(0, 10), sticky="ew")
        self.mov_filter_bar.grid_columnconfigure(5, weight=1)  # push search btn right

        self.mov_barcode_entry = customtkinter.CTkEntry(
            self.mov_filter_bar, width=130, placeholder_text="Barcode")
        self.mov_barcode_entry.grid(row=0, column=0, padx=(0, 6))

        self.mov_name_entry = customtkinter.CTkEntry(
            self.mov_filter_bar, width=150, placeholder_text="Όνομα Προϊόντος")
        self.mov_name_entry.grid(row=0, column=1, padx=(0, 6))

        self.mov_reason_menu = customtkinter.CTkOptionMenu(
            self.mov_filter_bar, width=170,
            values=["Όλοι", "Πώληση", "Χειροκίνητη Ενημέρωση",
                    "Εισαγωγή", "Παραλαβή Παραγγελίας", "Επαναφορά"])
        self.mov_reason_menu.grid(row=0, column=2, padx=(0, 6))

        self.mov_start_entry = customtkinter.CTkEntry(
            self.mov_filter_bar, width=130, placeholder_text="Από (YYYY-MM-DD)")
        self.mov_start_entry.grid(row=0, column=3, padx=(0, 6))

        self.mov_end_entry = customtkinter.CTkEntry(
            self.mov_filter_bar, width=130, placeholder_text="Έως (YYYY-MM-DD)")
        self.mov_end_entry.grid(row=0, column=4, padx=(0, 6))

        self.mov_search_btn = customtkinter.CTkButton(
            self.mov_filter_bar, text="🔍 Αναζήτηση", width=100,
            fg_color="#2980B9", hover_color="#1F618D",
            text_color=("#FFFFFF", "#FFFFFF"),
            command=self.refresh_stock_movements)
        self.mov_search_btn.grid(row=0, column=5, sticky="e")

        # Treeview — uses global ttk style for consistent typography
        self.mov_tree = ttk.Treeview(
            self.stock_movements_frame,
            columns=("timestamp", "barcode", "product_name", "old_stock",
                     "new_stock", "change", "reason", "source"),
            show="headings", height=20,
        )
        self.mov_tree.heading("timestamp", text="Ημ/νία")
        self.mov_tree.heading("barcode", text="Barcode")
        self.mov_tree.heading("product_name", text="Προϊόν")
        self.mov_tree.heading("old_stock", text="Παλιό")
        self.mov_tree.heading("new_stock", text="Νέο")
        self.mov_tree.heading("change", text="Μεταβολή")
        self.mov_tree.heading("reason", text="Αιτία")
        self.mov_tree.heading("source", text="Πηγή")

        self.mov_tree.column("timestamp", width=140)
        self.mov_tree.column("barcode", width=110)
        self.mov_tree.column("product_name", width=200)
        self.mov_tree.column("old_stock", width=55, anchor="center")
        self.mov_tree.column("new_stock", width=55, anchor="center")
        self.mov_tree.column("change", width=65, anchor="center")
        self.mov_tree.column("reason", width=120)
        self.mov_tree.column("source", width=90)

        vsb = ttk.Scrollbar(self.stock_movements_frame, orient="vertical",
                            command=self.mov_tree.yview)
        self.mov_tree.configure(yscrollcommand=vsb.set)
        self.mov_tree.grid(row=2, column=0, padx=20, pady=(0, 20), sticky="nsew")
        vsb.grid(row=2, column=1, pady=(0, 20), sticky="ns")

        # Tag config for red/green changes
        self.mov_tree.tag_configure("negative", foreground="#E74C3C")
        self.mov_tree.tag_configure("positive", foreground="#27AE60")

        # Debounced refresh on filter changes
        self._mov_refresh_timer = None
        self.mov_barcode_entry.bind("<KeyRelease>", lambda e: self._debounce_mov_refresh())
        self.mov_name_entry.bind("<KeyRelease>", lambda e: self._debounce_mov_refresh())
        self.mov_reason_menu.configure(command=lambda _: self.refresh_stock_movements())

    def _debounce_mov_refresh(self):
        if self._mov_refresh_timer:
            self.after_cancel(self._mov_refresh_timer)
        self._mov_refresh_timer = self.after(400, self.refresh_stock_movements)

    def refresh_stock_movements(self):
        if not hasattr(self, 'stock_movements_frame') or self.stock_movements_frame is None:
            return
        if not hasattr(self, 'mov_tree') or self.mov_tree is None:
            return

        barcode = self.mov_barcode_entry.get().strip() or None
        name_filter = self.mov_name_entry.get().strip().lower() or None
        reason = self.mov_reason_menu.get()
        reason = None if reason == "Όλοι" else reason
        start_date = self.mov_start_entry.get().strip() or None
        end_date = self.mov_end_entry.get().strip() or None

        def _fetch():
            try:
                rows = self.db_service.get_stock_movements(
                    limit=500, barcode=barcode, reason=reason,
                    start_date=start_date, end_date=end_date)
                # Client-side name filter
                if name_filter:
                    rows = [r for r in rows
                            if name_filter in r.get("product_name", "").lower()]
                self.after(0, lambda: self._render_stock_movements(rows))
            except Exception as e:
                logging.error("refresh_stock_movements failed: %s", e)

        threading.Thread(target=_fetch, daemon=True).start()

    def _render_stock_movements(self, rows):
        if not hasattr(self, 'mov_tree') or self.mov_tree is None:
            return
        self.mov_tree.delete(*self.mov_tree.get_children())
        for i, r in enumerate(rows):
            change = r.get("change_amount") or r.get("difference", 0)
            change_str = f"+{change}" if change > 0 else str(change)
            tag = "positive" if change > 0 else ("negative" if change < 0 else None)
            tags = (tag,) if tag else ()
            self.mov_tree.insert("", "end", values=(
                r.get("timestamp", ""),
                r.get("barcode", ""),
                r.get("product_name", ""),
                r.get("old_stock", 0),
                r.get("new_stock", 0),
                change_str,
                r.get("reason", ""),
                r.get("source", ""),
            ), tags=tags)


class ProductFormDialog(customtkinter.CTkToplevel):
    """Secondary Modal popup for adding/updating products."""
    def __init__(self, parent: tk.Widget, title: str, product: Product = None):
        super().__init__(parent)

        self.title(title)
        self.geometry("380x480")
        self.resizable(False, False)

        self.transient(parent)
        self.deiconify()
        self.update_idletasks()
        self.grab_set()

        self.result = None
        self.product = product
        self.grid_columnconfigure(0, weight=1)

        # Header Label
        customtkinter.CTkLabel(self, text="Φόρμα Στοιχείων Προϊόντος", font=customtkinter.CTkFont(size=16, weight="bold")).pack(pady=15)

        # Barcode
        customtkinter.CTkLabel(self, text="Barcode (EAN-13):", font=customtkinter.CTkFont(weight="bold")).pack(padx=25, pady=(5, 2), anchor="w")
        self.entry_b = customtkinter.CTkEntry(self, width=330)
        self.entry_b.pack(padx=25, pady=(0, 10))
        if self.product:
            self.entry_b.insert(0, product.barcode)
            self.entry_b.configure(state="disabled", text_color="gray50")

        # Name
        customtkinter.CTkLabel(self, text="Όνομα Προϊόντος:", font=customtkinter.CTkFont(weight="bold")).pack(padx=25, pady=(5, 2), anchor="w")
        self.entry_n = customtkinter.CTkEntry(self, width=330)
        self.entry_n.pack(padx=25, pady=(0, 10))
        if self.product:
            self.entry_n.insert(0, product.name)

        # Stock
        customtkinter.CTkLabel(self, text="Στοκ (Τεμάχια):", font=customtkinter.CTkFont(weight="bold")).pack(padx=25, pady=(5, 2), anchor="w")
        self.entry_s = customtkinter.CTkEntry(self, width=330)
        self.entry_s.pack(padx=25, pady=(0, 10))
        if self.product:
            self.entry_s.insert(0, str(product.stock))

        # Expiry Date
        customtkinter.CTkLabel(self, text="Ημερομηνία Λήξης (YYYY-MM-DD):", font=customtkinter.CTkFont(weight="bold")).pack(padx=25, pady=(5, 2), anchor="w")
        self.entry_e = customtkinter.CTkEntry(self, width=330)
        self.entry_e.pack(padx=25, pady=(0, 10))
        if self.product:
            self.entry_e.insert(0, product.expiry_date)
        else:
            self.entry_e.insert(0, date.today().strftime("%Y-%m-%d"))

        # Price
        customtkinter.CTkLabel(self, text="Τιμή Πώλησης Μονάδας (€):", font=customtkinter.CTkFont(weight="bold")).pack(padx=25, pady=(5, 2), anchor="w")
        self.entry_p = customtkinter.CTkEntry(self, width=330)
        self.entry_p.pack(padx=25, pady=(0, 20))
        if self.product:
            self.entry_p.insert(0, f"{product.price:.2f}")

        # Supplier
        customtkinter.CTkLabel(self, text="Προμηθευτής:", font=customtkinter.CTkFont(weight="bold")).pack(padx=25, pady=(5, 2), anchor="w")
        self.supplier_var = tk.StringVar(value="Κανένας")
        self.supplier_menu = customtkinter.CTkOptionMenu(self, variable=self.supplier_var, values=["Κανένας"], width=330)
        self.supplier_menu.pack(padx=25, pady=(0, 20))
        self._supplier_map = {}
        try:
            suppliers = parent.db_service.get_all_suppliers()
            names = ["Κανένας"] + [s["name"] for s in suppliers]
            self.supplier_menu.configure(values=names)
            for s in suppliers:
                self._supplier_map[s["name"]] = s["id"]
            if self.product and getattr(self.product, 'supplier_id', None):
                for s in suppliers:
                    if s["id"] == self.product.supplier_id:
                        self.supplier_var.set(s["name"])
                        break
        except Exception:
            pass

        # Buttons
        btn_frame = customtkinter.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=25, pady=5)

        self.btn_save = customtkinter.CTkButton(
            btn_frame, text="Αποθήκευση", fg_color="#34C759", hover_color="#289A47",
            font=customtkinter.CTkFont(weight="bold"), command=self.save
        )
        self.btn_save.pack(side="right", padx=(5, 0), expand=True, fill="x")

        self.btn_cancel = customtkinter.CTkButton(
            btn_frame, text="Ακύρωση", fg_color=("gray80", "gray30"), hover_color=("gray70", "gray40"),
            command=self.destroy
        )
        self.btn_cancel.pack(side="left", padx=(0, 5), expand=True, fill="x")

    def save(self):
        barcode = self.entry_b.get().strip()
        name = self.entry_n.get().strip()
        stock_str = self.entry_s.get().strip()
        expiry = self.entry_e.get().strip()
        price_str = self.entry_p.get().strip()

        if not barcode or not name or not stock_str or not expiry or not price_str:
            messagebox.showwarning("Προειδοποίηση", "Όλα τα πεδία είναι υποχρεωτικά.")
            return

        try:
            stock = int(stock_str)
            if stock < 0:
                raise ValueError()
        except ValueError:
            messagebox.showerror("Σφάλμα", "Το Στοκ πρέπει να είναι μη αρνητικός ακέραιος αριθμός.")
            return

        try:
            price = float(price_str)
            if price <= 0:
                raise ValueError()
        except ValueError:
            messagebox.showerror("Σφάλμα", "Η Τιμή πρέπει να είναι θετικός δεκαδικός αριθμός.")
            return

        try:
            datetime.strptime(expiry, "%Y-%m-%d")
        except ValueError:
            messagebox.showerror("Σφάλμα", "Η Ημερομηνία Λήξης πρέπει να είναι στη μορφή YYYY-MM-DD.")
            return

        supplier_name = self.supplier_var.get()
        supplier_id = self._supplier_map.get(supplier_name, None) if supplier_name != "Κανένας" else None
        old_stock = self.product.stock if self.product else None
        self.result = {
            "barcode": barcode, "name": name, "stock": stock,
            "expiry_date": expiry, "price": price, "supplier_id": supplier_id,
            "old_stock": old_stock
        }
        self.destroy()
