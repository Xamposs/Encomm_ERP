"""
Source identity for XLSX import files (Phase B4).

Produces and verifies cryptographic fingerprints of XLSX byte content
and column mappings.  Pure Python, no database access.
"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass

from infrastructure.product_import_preview import ImportColumnMapping


CHUNK_SIZE = 1_048_576  # 1 MiB

_FORMAT_VERSION = 1


@dataclass(frozen=True)
class ImportSourceSignature:
    format_version: int = _FORMAT_VERSION
    file_size_bytes: int = 0
    file_sha256: str = ""
    mapping_sha256: str = ""


def _sha256_file(file_path: str) -> str:
    """Stream SHA-256 a file in fixed-size chunks. Never loads full file."""
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"Δεν βρέθηκε το αρχείο: {file_path}")
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _sha256_mapping(mapping: ImportColumnMapping) -> str:
    """Deterministic canonical hash of all five mapping fields."""
    canonical = "|".join([
        mapping.barcode_column,
        mapping.name_column,
        mapping.stock_column,
        mapping.price_column,
        mapping.expiry_date_column,
    ])
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def fingerprint_import_source(
    file_path: str, mapping: ImportColumnMapping
) -> ImportSourceSignature:
    """Create a signature for the XLSX file + column mapping."""
    # Stat before hashing
    st_before = os.stat(file_path)
    file_size = st_before.st_size

    fsha = _sha256_file(file_path)
    msha = _sha256_mapping(mapping)

    # Stat after — detect change during hashing
    st_after = os.stat(file_path)
    if (st_before.st_size != st_after.st_size
            or st_before.st_mtime_ns != st_after.st_mtime_ns):
        raise ValueError(
            "Το αρχείο άλλαξε κατά τη δημιουργία ταυτότητας. "
            "Επιλέξτε το ξανά.")

    return ImportSourceSignature(
        format_version=_FORMAT_VERSION,
        file_size_bytes=file_size,
        file_sha256=fsha,
        mapping_sha256=msha,
    )


def verify_import_source(
    signature: ImportSourceSignature,
    file_path: str,
    mapping: ImportColumnMapping,
) -> bool:
    """Recompute and compare against a stored signature."""
    try:
        current = fingerprint_import_source(file_path, mapping)
    except (FileNotFoundError, ValueError):
        return False
    return (
        current.file_sha256 == signature.file_sha256
        and current.mapping_sha256 == signature.mapping_sha256
        and current.file_size_bytes == signature.file_size_bytes
    )
