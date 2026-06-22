"""
AI Service – LLM Command Interface for ENCOMM ERP

Reads provider configuration from SystemConfig and sends
structured prompts to OpenAI-compatible APIs (DeepSeek, GLM, etc.).
Returns parsed JSON intents for the Intent Factory.
"""

import json
import logging
import requests
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# ── System prompt that forces the LLM to return a structured intent ──
SYSTEM_PROMPT = """Είσαι ο Encomm AI, ο νοημονικός βοηθός του ENCOMM ERP (φαρμακευτικό σύστημα).
Ο χρήστης σου δίνει μια εντολή στα Ελληνικά ή στα Αγγλικά.
Πρέπει να επιστρέψεις **ΜΟΝΟ** ένα έγκυρο JSON object (χωρίς markdown, χωρίς εξηγήσεις).

Τα επιτρεπόμενα intents είναι:
- "search_inventory"  → Αναζήτηση προϊόντος. Parameters: {"query": "<όροι αναζήτησης>"}
- "check_low_stock"   → Εμφάνιση προϊόντων με χαμηλό στοκ. Parameters: {} (κενό)
- "check_expiry"      → Εμφάνιση προϊόντων κοντά στη λήξη ή ληγμένων. Parameters: {}
- "view_dashboard"    → Πήγαινε στο Dashboard / Αρχική. Parameters: {}
- "view_inventory"    → Πήγαινε στην Αποθήκη. Parameters: {}
- "view_pos"          → Πήγαινε στο Ταμείο / Πωλήσεις. Parameters: {}
- "view_settings"     → Πήγαινε στις Ρυθμίσεις. Parameters: {}
- "add_product"       → Προσθήκη νέου προϊόντος. Parameters: {"barcode":"...","name":"...","stock":N,"price":F}
- "unknown"           → Όταν δεν καταλαβαίνεις την εντολή. Parameters: {"reason": "..."}

ΠΑΡΑΔΕΙΓΜΑ ΑΠΑΝΤΗΣΗΣ:
{"intent": "search_inventory", "parameters": {"query": "panadol"}}

ΑΠΑΝΤΑ ΜΟΝΟ ΤΟ JSON. ΤΙΠΟΤΑ ΑΛΛΟ."""


class AIService:
    """Thin wrapper around an OpenAI-compatible chat completion endpoint."""

    def __init__(self, db_service):
        """
        Parameters
        ----------
        db_service : DatabaseService
            Used to read provider/key from SystemConfig.
        """
        self.db_service = db_service
        self._load_config()

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------
    def _load_config(self):
        """Read AI provider settings from SystemConfig (persisted in SQLite)."""
        self.api_url = self.db_service.get_config(
            "ai_api_url",
            "https://api.openai.com/v1/chat/completions"
        )
        self.api_key = self.db_service.get_config("ai_api_key", "")
        self.model = self.db_service.get_config("ai_model", "gpt-4o-mini")

    def is_configured(self) -> bool:
        """Return True when an API key has been stored."""
        return bool(self.api_key and self.api_key.strip())

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def send_command_to_llm(self, user_text: str) -> Optional[Dict[str, Any]]:
        """
        Send a user command to the LLM and return the parsed intent dict.

        Returns a dict with intent="unknown" on any failure (network, parse, missing key).
        """
        if not self.is_configured():
            logger.warning("AI Service not configured — no API key stored.")
            return {
                "intent": "unknown",
                "parameters": {
                    "reason": "Δεν έχει ρυθμιστεί API Key. Πηγαίνετε στις Ρυθμίσεις και εισάγετε το κλειδί σας."
                }
            }

        self._load_config()  # refresh in case settings changed

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ],
            "temperature": 0.1,
            "max_tokens": 256,
        }

        try:
            resp = requests.post(
                self.api_url,
                headers=headers,
                json=payload,
                timeout=30,
            )
            resp.raise_for_status()

            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()

            # Strip markdown fences if the model wraps them
            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(lines[1:-1])

            parsed = json.loads(content)
            logger.info(f"LLM intent received: {parsed}")
            return parsed

        except requests.exceptions.RequestException as exc:
            logger.error(f"AI API request failed: {exc}")
            return {
                "intent": "unknown",
                "parameters": {"reason": f"Σφάλμα σύνδεσης με το AI: {exc}"}
            }
        except (json.JSONDecodeError, KeyError, IndexError) as exc:
            logger.error(f"Failed to parse LLM response: {exc}")
            return {
                "intent": "unknown",
                "parameters": {"reason": "Το AI επέστρεψε μη έγκυρη απάντηση."}
            }
