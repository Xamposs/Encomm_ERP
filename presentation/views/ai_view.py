import customtkinter as ctk
import threading
import time
from .base_view import BaseView


class AIView(BaseView):
    """AI Assistant chat view — placeholder for future AI integration."""

    def __init__(self, parent, db_service, config: dict, **kwargs):
        kwargs.setdefault('fg_color', 'transparent')
        super().__init__(parent, db_service, config, **kwargs)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=0)

        self.ai_title_label = ctk.CTkLabel(
            self,
            text="Βοηθός AI Φαρμακείου (Encomm AI)",
            font=ctk.CTkFont(family="Outfit", size=18, weight="bold"),
        )
        self.ai_title_label.grid(row=0, column=0, sticky="w", pady=(0, 15))

        self.ai_chat_log = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.ai_chat_log.grid(row=1, column=0, sticky="nsew", pady=(0, 15))
        self.ai_chat_log.grid_columnconfigure(0, weight=1)

        self._append_chat_message("Encomm AI", "Γεια! Είμαι ο Encomm, ο AI βοηθός σου. Πώς μπορώ να σε βοηθήσω; 🤖")

        self.ai_input_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.ai_input_frame.grid(row=2, column=0, sticky="ew")
        self.ai_input_frame.grid_columnconfigure(0, weight=1)

        self.ai_input_entry = ctk.CTkEntry(
            self.ai_input_frame,
            placeholder_text="Γράψτε μια εντολή ή ερώτηση...",
            height=40,
            font=ctk.CTkFont(size=13),
        )
        self.ai_input_entry.grid(row=0, column=0, padx=(0, 10), sticky="ew")
        self.ai_input_entry.bind("<Return>", lambda e: self.send_ai_message())

        self.ai_send_btn = ctk.CTkButton(
            self.ai_input_frame,
            text="🚀 Αποστολή",
            width=120,
            height=40,
            font=ctk.CTkFont(weight="bold", size=13),
            fg_color="#10B981",
            hover_color="#059669",
            command=self.send_ai_message,
        )
        self.ai_send_btn.grid(row=0, column=1)

    # ------------------------------------------------------------------
    # Chat helpers
    # ------------------------------------------------------------------

    def _append_chat_message(self, sender: str, message: str):
        is_bot = sender == "Encomm AI"
        bubble_color = ("#E2E8F0", "#1E293B")
        sender_color = "#10B981" if is_bot else ("#2563EB", "#3B82F6")

        bubble = ctk.CTkFrame(self.ai_chat_log, fg_color=bubble_color, corner_radius=10)
        bubble.grid(
            row=len(self.ai_chat_log.winfo_children()),
            column=0, sticky="ew", pady=4, padx=5,
        )
        bubble.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            bubble, text=sender,
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=sender_color, anchor="w",
        ).grid(row=0, column=0, padx=12, pady=(8, 0), sticky="w")

        ctk.CTkLabel(
            bubble, text=message,
            font=ctk.CTkFont(size=13),
            text_color=BaseView._body_text(),
            anchor="w", wraplength=600, justify="left",
        ).grid(row=1, column=0, padx=12, pady=(2, 8), sticky="w")

    def send_ai_message(self):
        text = self.ai_input_entry.get().strip()
        if not text:
            return
        self._append_chat_message("Εσείς", text)
        self.ai_input_entry.delete(0, "end")

        def bg_chat_process():
            try:
                time.sleep(0.4)
                reply = "Λήψη εντολής επιτυχής. Το backend AI interface είναι έτοιμο για διασύνδεση!"
                self.after(0, lambda: self._append_chat_message("Encomm AI", reply))
            except Exception as e:
                self.after(0, lambda: self._append_chat_message("Encomm AI", f"⚠️ Σφάλμα: {str(e)}"))

        threading.Thread(target=bg_chat_process, daemon=True).start()

    def refresh(self) -> None:
        pass
