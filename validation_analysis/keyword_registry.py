# ============================================================================
# Phase 0 - Stable column keyword-key normalizer (the "keywords for keys"
# foundation). REUSABLE.
#
# A validation rule in this system must attach to a COLUMN CONCEPT, not to a
# per-ingestion UUID. The canonical ``sourceColumn`` UUID is regenerated on
# every ingestion, so the "same" column in a future input file gets a new UUID
# and a rule keyed on that UUID can neither be re-applied nor matched. This
# module derives a STABLE ``column_key`` from a column's name (and optional
# alias/synonym map) so the Baseline Rule Store (validation_analysis.baseline)
# can persist a truth file's validations keyed by concept and re-apply them to
# future files by resolving ``column_key -> current UUID`` at compile time.
#
# Phase 2 extends this module with the keyword -> LOV-name and keyword ->
# dataType maps (seeded from the truth file + corpus lovCheckType list). Phase 0
# ships only the normalizer + the registry scaffold those maps plug into.
# ============================================================================
"""
validation_analysis.keyword_registry
=====================================
Normalize a column name into a stable ``column_key`` and resolve keys back to
the current file's columns.

Public surface
--------------
* :func:`normalize_column_key` - deterministic name -> stable key.
* :class:`KeywordRegistry` - alias/synonym-aware key resolution + (Phase 2) the
  keyword -> LOV / dataType lookup maps.
* :func:`build_key_to_uuid_index` - map every canonical column in a model onto
  its stable key, for ``column_key -> uuid`` resolution at compile time.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, Optional

# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------
# Characters that separate tokens in a human column header. Everything that is
# not alphanumeric collapses to a single underscore so "Pers.-Nr", "Pers Nr"
# and "pers_nr" all converge on the same key.
_NON_ALNUM = re.compile(r"[^0-9a-z]+")
_MULTI_US = re.compile(r"_+")


def _strip_accents(text: str) -> str:
    """Fold accented characters to ASCII (e.g. ``ü`` -> ``u``) deterministically."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_column_key(name: str) -> str:
    """Normalize a column name into a stable, comparable ``column_key``.

    The transform is deterministic and idempotent:

    1. Unicode-fold accents (``Nationalität`` -> ``nationalitat``).
    2. Lowercase.
    3. Replace every run of non-alphanumeric characters with a single ``_``.
    4. Trim leading/trailing ``_`` and collapse repeats.

    Examples:
        ``"Pers.-Nr."``        -> ``"pers_nr"``
        ``"Staatsangehörigkeit"`` -> ``"staatsangehorigkeit"``
        ``"  Konfession  "``   -> ``"konfession"``

    An empty / punctuation-only name yields ``""`` (the caller decides how to
    treat an unkeyable column - typically fall back to the raw UUID).
    """
    folded = _strip_accents(name or "").lower()
    underscored = _NON_ALNUM.sub("_", folded)
    collapsed = _MULTI_US.sub("_", underscored)
    return collapsed.strip("_")


# ---------------------------------------------------------------------------
# Registry (alias-aware key resolution + Phase 2 lookup maps)
# ---------------------------------------------------------------------------
@dataclass
class KeywordRegistry:
    """Alias-aware resolver from a column name to a canonical ``column_key``.

    ``aliases`` maps any number of raw/normalized spellings onto a single
    canonical key, so synonyms observed across samples ("nationality",
    "staatsangehorigkeit", "nationalitaet") resolve to one concept. Keys are
    stored already-normalized; lookups normalize their input first.

    Phase 2 will populate :attr:`lov_by_key` (``column_key -> lovCheckType``) and
    :attr:`data_type_by_key` (``column_key -> contract dataType token``). They
    are declared here so the analyzer can consult a single registry object once
    those maps are seeded; in Phase 0 they are simply empty.
    """

    # raw/alias spelling (any form) -> canonical normalized key.
    aliases: dict[str, str] = field(default_factory=dict)
    # Phase 2 lookup maps (seeded later; empty in Phase 0).
    lov_by_key: dict[str, str] = field(default_factory=dict)
    data_type_by_key: dict[str, str] = field(default_factory=dict)

    def resolve_key(self, name: str) -> str:
        """Resolve a column name to its canonical key, honouring aliases.

        Falls back to the plain :func:`normalize_column_key` when no alias is
        registered, so an unknown column still gets a stable (if un-aliased) key.
        """
        normalized = normalize_column_key(name)
        return self.aliases.get(normalized, normalized)

    def add_alias(self, spelling: str, canonical_key: str) -> None:
        """Register ``spelling`` (any form) as an alias of ``canonical_key``."""
        self.aliases[normalize_column_key(spelling)] = normalize_column_key(canonical_key)

    def lov_check_type(self, name: str) -> Optional[str]:
        """Return the pinned ``lovCheckType`` for a column, if seeded (Phase 2)."""
        return self.lov_by_key.get(self.resolve_key(name))

    def data_type(self, name: str) -> Optional[str]:
        """Return the pinned contract ``dataType`` token for a column (Phase 2)."""
        return self.data_type_by_key.get(self.resolve_key(name))


# A module-level default registry. Phase 2 seeds it from the truth file + the
# corpus lovCheckType list; Phase 0 leaves it empty so callers get pure
# name-normalization behaviour with no surprises.
DEFAULT_REGISTRY = KeywordRegistry()


# ---------------------------------------------------------------------------
# key -> uuid resolution over a canonical model
# ---------------------------------------------------------------------------
def build_key_to_uuid_index(
    columns: Iterable["object"],
    *,
    registry: Optional[KeywordRegistry] = None,
) -> dict[str, str]:
    """Build a ``column_key -> uuid`` index for a set of canonical columns.

    Each item in ``columns`` must expose ``.name`` and ``.uuid`` (a
    :class:`canonical.models.CanonicalColumn` satisfies this). When two columns
    normalize to the same key (rare - duplicate headers), the FIRST wins and the
    collision is left to the caller to detect via :func:`find_key_collisions`.

    Args:
        columns: Iterable of canonical columns (name + uuid).
        registry: Optional alias-aware registry; defaults to a plain normalizer.

    Returns:
        A mapping from stable key to the column UUID in THIS file.
    """
    reg = registry or DEFAULT_REGISTRY
    index: dict[str, str] = {}
    for col in columns:
        key = reg.resolve_key(getattr(col, "name", ""))
        if not key:
            continue
        index.setdefault(key, getattr(col, "uuid"))
    return index


def find_key_collisions(
    columns: Iterable["object"],
    *,
    registry: Optional[KeywordRegistry] = None,
) -> dict[str, list[str]]:
    """Return ``key -> [uuid, ...]`` for any key claimed by >1 column.

    Duplicate headers (two columns normalizing to the same key) make a
    keyword-keyed rule ambiguous; surfacing them lets the caller fall back to
    UUID-keyed behaviour for just those columns instead of silently mis-joining.
    """
    reg = registry or DEFAULT_REGISTRY
    buckets: dict[str, list[str]] = {}
    for col in columns:
        key = reg.resolve_key(getattr(col, "name", ""))
        if not key:
            continue
        buckets.setdefault(key, []).append(getattr(col, "uuid"))
    return {k: v for k, v in buckets.items() if len(v) > 1}


__all__ = [
    "normalize_column_key",
    "KeywordRegistry",
    "DEFAULT_REGISTRY",
    "build_key_to_uuid_index",
    "find_key_collisions",
]
