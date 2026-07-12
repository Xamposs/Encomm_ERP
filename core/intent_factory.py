"""
Intent Factory – Safety Gate & Intent Dispatcher for ENCOMM AI

Receives raw JSON from AIService, validates it against the
whitelist of allowed intents, and returns a clean structured
result ready for the UI layer to consume.
"""

import json
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# ── Whitelist of all recognised intents ──
VALID_INTENTS = frozenset({
    "search_inventory",
    "check_low_stock",
    "check_expiry",
    "view_dashboard",
    "view_inventory",
    "view_pos",
    "view_settings",
    "add_product",
    "unknown",
})


class IntentFactory:
    """
    Parses, validates and normalises the raw LLM response
    into a safe intent object that the UI can trust.
    """

    def __init__(self):
        pass  # stateless — no dependencies needed

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def parse(self, raw_response: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Parse and validate a raw LLM response.

        Parameters
        ----------
        raw_response : dict or None
            The dict returned by ``AIService.send_command_to_llm()``.

        Returns
        -------
        dict
            A guaranteed-safe structure::

                {
                    "intent": "<valid_intent_or_unknown>",
                    "parameters": { ... }
                }
        """
        if raw_response is None:
            logger.warning("IntentFactory received None — returning unknown.")
            return self._unknown("Κενή απάντηση από το AI.")

        # --- Extract intent (coerce to str so a null/list value can't crash) ---
        raw_intent = raw_response.get("intent", "")
        if not isinstance(raw_intent, str):
            logger.warning(
                f"Safety Gate: intent is not a string ({type(raw_intent).__name__}). "
                "Falling back to 'unknown'.")
            return self._unknown("Μη έγκυρη μορφή απάντησης από το AI.")
        intent = raw_intent.strip().lower()

        # --- Safety Gate: reject unknown intents ---
        if intent not in VALID_INTENTS:
            logger.warning(
                f"Safety Gate: rejected invalid intent '{intent}'. "
                "Falling back to 'unknown'."
            )
            return self._unknown(
                f"Η εντολή '{intent}' δεν αναγνωρίστηκε από το σύστημα."
            )

        # --- Extract parameters ---
        parameters = raw_response.get("parameters")
        if parameters is None:
            parameters = {}
        if not isinstance(parameters, dict):
            logger.warning(
                f"Safety Gate: parameters is not a dict ({type(parameters)}). "
                "Replacing with empty dict."
            )
            parameters = {}

        logger.info(f"IntentFactory OK → intent={intent}, params={parameters}")
        return {"intent": intent, "parameters": parameters}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _unknown(reason: str) -> Dict[str, Any]:
        """Return the safe default intent."""
        return {
            "intent": "unknown",
            "parameters": {"reason": reason},
        }

    def parse_json_string(self, json_string: str) -> Dict[str, Any]:
        """
        Convenience: parse a raw JSON string first, then validate.

        Useful when the caller still holds the unparsed LLM output.
        """
        try:
            raw = json.loads(json_string)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning(f"Failed to parse JSON string: {exc}")
            return self._unknown("Το AI επέστρεψε μη έγκυρο JSON.")
        return self.parse(raw)
