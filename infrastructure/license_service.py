"""
License Service – Hardware-bound License Verification

Generates a pseudo-unique Hardware ID (HWID) from system
identifiers and verifies license keys against it.
"""

import hashlib
import logging
import platform
import uuid
from datetime import datetime, timedelta
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def generate_hwid() -> str:
    """
    Produce a deterministic HWID string from system identifiers.

    Combines platform node, machine, processor, and a MAC address
    into a SHA-256 digest. Cross-platform (Windows / Linux / macOS).
    """
    raw_parts = [
        platform.node(),          # hostname
        platform.machine(),       # e.g. x86_64, AMD64
        platform.processor(),     # CPU identifier
        hex(uuid.getnode()),      # primary MAC address
    ]
    raw = "|".join(raw_parts)
    hwid = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32].upper()
    logging.info(f"HWID generated: {hwid}")
    return hwid


def verify_local_license(license_key: str, hwid: str) -> Tuple[bool, Optional[str]]:
    """
    Verify a license key against the given HWID.

    The current implementation uses a deterministic HMAC-style check
    so that valid keys can be generated offline for development/demo
    purposes.  In production this would call an external licensing
    server (e.g. AAERP, Keygen.sh).

    Parameters
    ----------
    license_key : str
        The license string supplied by the user.
    hwid : str
        The machine's hardware identifier.

    Returns
    -------
    (is_valid, expires_at) : tuple[bool, str | None]
        * ``is_valid`` – True when the key matches the HWID.
        * ``expires_at`` – ISO-8601 expiry date (365 days from now),
          or *None* when invalid.
    """
    if not license_key or not hwid:
        logging.warning("License verification failed: empty key or HWID.")
        return False, None

    # Deterministic expected key: SHA-256(hwid + salt)[:24].upper()
    salt = "ENCOMM-TENSOR-2026"
    expected = hashlib.sha256(f"{hwid}{salt}".encode("utf-8")).hexdigest()[:24].upper()

    is_valid = license_key.strip().upper() == expected

    if is_valid:
        expires_at = (datetime.now() + timedelta(days=365)).strftime("%Y-%m-%d")
        logging.info(f"License verified successfully. Expires: {expires_at}")
        return True, expires_at
    else:
        logging.warning("License verification failed: key does not match HWID.")
        return False, None


def generate_license_key(hwid: str) -> str:
    """
    Generate a valid license key for a given HWID (admin tool).

    In production this would be a server-side only function.
    Included here for development / demo convenience.
    """
    salt = "ENCOMM-TENSOR-2026"
    key = hashlib.sha256(f"{hwid}{salt}".encode("utf-8")).hexdigest()[:24].upper()
    logging.info(f"License key generated for HWID {hwid}: {key}")
    return key
