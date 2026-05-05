"""Address normalization + sha256 helper for geocoding_cache.address_hash.

Normalization rules:
  - NFKC unicode normalize (full-width <-> half-width, kana variants)
  - Collapse runs of whitespace to a single space
  - Trim leading/trailing whitespace

The hash is the hex sha256 of the UTF-8 encoded normalized address. This
keeps every producer (Kaipoke import, manual entry, retry path) in sync so
the UNIQUE(address_hash) constraint stays meaningful.
"""

from __future__ import annotations

import hashlib
import unicodedata


def normalize_address(raw: str) -> str:
    """Return the canonical form of *raw* used for hashing."""
    norm = unicodedata.normalize("NFKC", raw)
    norm = " ".join(norm.split())
    return norm.strip()


def address_hash(raw: str) -> str:
    """Return sha256 hex digest of the normalized address."""
    return hashlib.sha256(normalize_address(raw).encode("utf-8")).hexdigest()
