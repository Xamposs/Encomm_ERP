import customtkinter as ctk
import tkinter as tk
import os
import logging
import threading
from tkinter import messagebox, filedialog
from .base_view import BaseView


class SettingsView(BaseView):
    """Settings panel with VAT, stock, myDATA, license, backup/restore, theme and about sections."""

    def __init__(self, parent, db_service, config: dict, **kwargs):
        kwargs.setdefault('fg_color', 'transparent')
        super().__init__(parent, db_service, config, **kwargs)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.settings_card = ctk.CTkScrollableFrame(self, label_text="")
        self.settings_card.grid(row=0, column=0, sticky="nsew")

        # ── Section 1: VAT / Stock / Expiry ──
        self._add_section_label("📊 Φορολογικές & Αποθεματικές Ρυθμίσεις")

        self.set_vat_label = ctk.CTkLabel(self.settings_card, text="Ποσοστό ΦΠΑ (π.χ. 0.15 για 15%):",
            font=ctk.CTkFont(size=13))
        self.set_vat_label.pack(padx=30, pady=(5, 2), anchor="w")
        self.set_vat_entry = ctk.CTkEntry(self.settings_card, width=300)
        self.set_vat_entry.pack(padx=30, pady=(0, 15), anchor="w")

        self.set_stock_label = ctk.CTkLabel(self.settings_card, text="Όριο Χαμηλού Αποθέματος (0-99999):",
            font=ctk.CTkFont(size=13))
        self.set_stock_label.pack(padx=30, pady=(5, 2), anchor="w")
        self.set_stock_entry = ctk.CTkEntry(self.settings_card, width=300)
        self.set_stock_entry.pack(padx=30, pady=(0, 15), anchor="w")

        self.set_exp_label = ctk.CTkLabel(self.settings_card, text="Ημέρες Προειδοποίησης Λήξης (1-3650):",
            font=ctk.CTkFont(size=13))
        self.set_exp_label.pack(padx=30, pady=(5, 2), anchor="w")
        self.set_exp_entry = ctk.CTkEntry(self.settings_card, width=300)
        self.set_exp_entry.pack(padx=30, pady=(0, 20), anchor="w")

        # ── Section 2: myDATA / ΗΔΙΚΑ ──
        self._add_section_label("🏛️ myDATA / ΗΔΙΚΑ (ΑΑΔΕ)")
        self.set_mydata_label = ctk.CTkLabel(self.settings_card,
            text="Ρυθμίσεις διασύνδεσης με myDATA REST API (σε επόμενη έκδοση).",
            font=ctk.CTkFont(size=12), text_color=BaseView._subtle_text(),
            wraplength=400, justify="left")
        self.set_mydata_label.pack(padx=30, pady=(5, 15), anchor="w")

        # ── Section 3: License ──
        self._add_section_label("🔑 Άδεια Χρήσης & Ενεργοποίηση")
        self.set_hwid_label = ctk.CTkLabel(self.settings_card, text="Αναγνωριστικό Υλικού (HWID):",
            font=ctk.CTkFont(size=13))
        self.set_hwid_label.pack(padx=30, pady=(5, 2), anchor="w")
        self.set_hwid_entry = ctk.CTkEntry(self.settings_card, width=400, state="disabled")
        self.set_hwid_entry.pack(padx=30, pady=(0, 15), anchor="w")

        self.set_license_label = ctk.CTkLabel(self.settings_card, text="Κλειδί Άδειας (License Key):",
            font=ctk.CTkFont(size=13))
        self.set_license_label.pack(padx=30, pady=(5, 2), anchor="w")
        self.set_license_entry = ctk.CTkEntry(self.settings_card, width=400)
        self.set_license_entry.pack(padx=30, pady=(0, 15), anchor="w")

        # ── Section 4: Backup & Restore ──
        self._add_section_label("💾 Ασφάλεια Δεδομένων")

        self.set_autobackup_var = tk.BooleanVar(value=False)
        self.set_autobackup_cb = ctk.CTkCheckBox(
            self.settings_card, text="Αυτόματο αντίγραφο ασφαλείας κατά το κλείσιμο",
            variable=self.set_autobackup_var, font=ctk.CTkFont(size=13))
        self.set_autobackup_cb.pack(padx=30, pady=(5, 15), anchor="w")

        self.backup_btn_frame = ctk.CTkFrame(self.settings_card, fg_color="transparent")
        self.backup_btn_frame.pack(padx=30, pady=(5, 10), anchor="w")
        self.backup_now_btn = ctk.CTkButton(
            self.backup_btn_frame, text="💾 Δημιουργία Αντιγράφου Ασφαλείας",
            font=ctk.CTkFont(weight="bold"),
            fg_color="#2980B9", hover_color="#1F618D",
            text_color=("#FFFFFF", "#FFFFFF"),
            command=self.backup_database_now)
        self.backup_now_btn.pack(side="left", padx=(0, 10))
        self.restore_btn = ctk.CTkButton(
            self.backup_btn_frame, text="📂 Επαναφορά από Αντίγραφο",
            font=ctk.CTkFont(weight="bold"),
            fg_color=("#E74C3C", "#C0392B"), hover_color=("#C0392B", "#A93226"),
            text_color=("#FFFFFF", "#FFFFFF"),
            command=self.restore_database_from_backup)
        self.restore_btn.pack(side="left")

        self.last_backup_lbl = ctk.CTkLabel(self.settings_card,
            text="Τελευταίο αντίγραφο: Κανένα",
            font=ctk.CTkFont(size=11), text_color=("gray50", "gray60"))
        self.last_backup_lbl.pack(padx=30, pady=(5, 20), anchor="w")

        # ── Section 5: Theme ──
        self._add_section_label("🎨 Εμφάνιση")
        self.set_theme_var = tk.StringVar(value="Dark")
        self.set_theme_menu = ctk.CTkOptionMenu(self.settings_card,
            variable=self.set_theme_var, values=["Dark", "Light"], width=200,
            command=self._on_theme_changed)
        self.set_theme_menu.pack(padx=30, pady=(5, 20), anchor="w")

        # ── Section 6: About ──
        self._add_section_label("ℹ️ Σχετικά")
        self.about_lbl = ctk.CTkLabel(self.settings_card,
            text="ENCOMM ERP v1.0.0\nPharmacy Management System\n© 2025-2026 ENCOMM Tensor Intelligence",
            font=ctk.CTkFont(size=12), text_color=BaseView._subtle_text(),
            wraplength=400, justify="left")
        self.about_lbl.pack(padx=30, pady=(5, 20), anchor="w")

        # ── Save button ──
        self.save_settings_btn = ctk.CTkButton(self.settings_card, text="💾  Αποθήκευση Ρυθμίσεων",
            font=ctk.CTkFont(weight="bold", size=14),
            fg_color=("#2ecc71", "#27ae60"), hover_color=("#27ae60", "#1e8449"),
            text_color=("#FFFFFF", "#FFFFFF"),
            command=self.save_settings_values)
        self.save_settings_btn.pack(pady=(15, 30))

        # Callbacks to trigger global refresh after save (set by MainWindow)
        self._on_settings_saved = None
        self._on_theme_applied = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _add_section_label(self, text: str):
        lbl = ctk.CTkLabel(self.settings_card, text=text,
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=("#1A5276", "#5DADE2"))
        lbl.pack(padx=30, pady=(20, 10), anchor="w")

    def load_settings_values(self):
        """Load persisted settings from SystemConfig into the UI fields."""
        vat = self.db_service.get_config("vat_rate", str(self.config.get("vat_rate", 0.15)))
        self.set_vat_entry.delete(0, "end")
        self.set_vat_entry.insert(0, vat)

        stock = self.db_service.get_config("low_stock_threshold", str(self.config.get("low_stock_threshold", 10)))
        self.set_stock_entry.delete(0, "end")
        self.set_stock_entry.insert(0, stock)

        expiry = self.db_service.get_config("expiry_alert_days", str(self.config.get("expiry_alert_days", 30)))
        self.set_exp_entry.delete(0, "end")
        self.set_exp_entry.insert(0, expiry)

        auto_backup = self.db_service.get_config("auto_backup", "0")
        self.set_autobackup_var.set(auto_backup == "1")

        theme = self.db_service.get_config("theme", "Dark")
        self.set_theme_var.set(theme)

        licence = self.db_service.get_config("license_key", "")
        self.set_license_entry.delete(0, "end")
        self.set_license_entry.insert(0, licence)

        # HWID is set externally by MainWindow via _set_cached_hwid

    def _on_theme_changed(self, choice: str):
        """Live theme preview — applies instantly when dropdown changes."""
        ctk.set_appearance_mode(choice)
        self.db_service.set_config("theme", choice)
        self.config["theme"] = choice
        # Notify MainWindow to re-apply ttk styles (Treeviews etc.)
        if self._on_theme_applied:
            self._on_theme_applied(choice)

    def save_settings_values(self):
        """Validate and persist all settings to SystemConfig."""
        # VAT: 0.0-1.0, supports Greek comma
        try:
            vat_str = self.set_vat_entry.get().strip().replace(",", ".")
            vat = float(vat_str)
            if vat < 0 or vat > 1:
                raise ValueError
        except (ValueError, ArithmeticError):
            messagebox.showerror("Σφάλμα", "Το ΦΠΑ πρέπει να είναι δεκαδικός (π.χ. 0.24 για 24%, όχι 24)")
            return

        # Stock: int 0-99999
        try:
            stock = int(self.set_stock_entry.get().strip())
            if stock < 0 or stock > 99999:
                raise ValueError
        except (ValueError, ArithmeticError):
            messagebox.showerror("Σφάλμα", "Το όριο χαμηλού αποθέματος πρέπει να είναι ακέραιος 0–99999")
            return

        # Expiry: int 1-3650
        try:
            expiry = int(self.set_exp_entry.get().strip())
            if expiry < 1 or expiry > 3650:
                raise ValueError
        except (ValueError, ArithmeticError):
            messagebox.showerror("Σφάλμα", "Οι ημέρες προειδοποίησης λήξης πρέπει να είναι ακέραιος 1–3650")
            return

        self.db_service.set_config("vat_rate", str(vat))
        self.db_service.set_config("low_stock_threshold", str(stock))
        self.db_service.set_config("expiry_alert_days", str(expiry))
        self.db_service.set_config("auto_backup", "1" if self.set_autobackup_var.get() else "0")
        # Theme is already persisted live by _on_theme_changed; sync config dict
        self.config["theme"] = self.set_theme_var.get()
        self.db_service.set_config("license_key", self.set_license_entry.get().strip())

        self.config["vat_rate"] = vat
        self.config["low_stock_threshold"] = stock
        self.config["expiry_alert_days"] = expiry

        # Apply theme
        ctk.set_appearance_mode(self.set_theme_var.get())

        messagebox.showinfo("Επιτυχία", "Οι ρυθμίσεις αποθηκεύτηκαν επιτυχώς.")

        # Notify MainWindow to refresh other views
        if self._on_settings_saved:
            self._on_settings_saved()

    # ------------------------------------------------------------------
    # Backup & Restore
    # ------------------------------------------------------------------
    def backup_database_now(self):
        def _do_backup():
            try:
                path = self.db_service.backup_database()
                fname = os.path.basename(path)
                self.after(0, lambda: [
                    self.last_backup_lbl.configure(text=f"Τελευταίο αντίγραφο: {fname}"),
                    messagebox.showinfo("Επιτυχές Αντίγραφο",
                        f"Η βάση δεδομένων αποθηκεύτηκε επιτυχώς!\n\nΤοποθεσία: {path}")
                ])
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Σφάλμα", f"Αποτυχία δημιουργίας αντιγράφου:\n{e}"))
        threading.Thread(target=_do_backup, daemon=True).start()

    def restore_database_from_backup(self):
        file_path = filedialog.askopenfilename(
            title="Επιλογή Αρχείου Αντιγράφου Ασφαλείας",
            filetypes=[("SQLite Database", "*.db")])
        if not file_path:
            return
        if not messagebox.askyesno(
            "Επιβεβαίωση Επαναφοράς",
            "⚠️ Προειδοποίηση: Η επαναφορά θα αντικαταστήσει "
            "όλα τα τρέχοντα δεδομένα.\n\n"
            "Η εφαρμογή θα κλείσει μετά την επαναφορά.\n"
            "Παρακαλώ ανοίξτε την ξανά χειροκίνητα.\n\n"
            "Είστε βέβαιοι ότι θέλετε να συνεχίσετε;",
            icon="warning"):
            return
        try:
            self.db_service.backup_database()
        except Exception:
            pass
        if self.db_service.restore_database(file_path):
            messagebox.showinfo("Επιτυχής Επαναφορά",
                "Η βάση δεδομένων επαναφέρθηκε επιτυχώς.\n\n"
                "Η εφαρμογή θα κλείσει τώρα.\nΠαρακαλώ ανοίξτε την ξανά.")
            os._exit(0)
        else:
            messagebox.showerror("Σφάλμα",
                "Αποτυχία επαναφοράς βάσης δεδομένων.\n"
                "Ελέγξτε ότι το αρχείο είναι έγκυρο αντίγραφο.")

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        self.load_settings_values()
