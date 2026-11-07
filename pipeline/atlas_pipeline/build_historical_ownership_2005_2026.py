"""Build 22-year (2005-2026) HCAD certified-roll ownership history shards (`owners_<prefix3>.json.gz`).

Architecture (Milestone 8):
  1. Pure Stateless Consolidation & Privacy Layer:
     - Enforces 1:1 statutory Texas Tax Code Sec. 25.025 privacy shielding (`CONFIDENTIAL_OWNER_TOKENS`),
       suppressing active-roll confidential parcels completely and retroactively tainting/redacting
       contiguous pre-election snapshots (and secondary `owners.txt` co-owner rows `ln_num >= 1`)
       when an owner elected confidentiality mid-window before a later non-confidential sale (`DF-1`).
     - Consolidates clerical punctuation, `ET UX`, middle-initial, and `>= 18`-char column-truncation
       jitter while guarding series LLCs (`SERIES 1` vs `SERIES 12`), Roman numerals (`I` vs `II`),
       generational suffixes (`JR`/`SR`/`II`/`III`), and geographic/entity stopwords (`DF-2`).
     - Normalizes HCAD's `1988-01-02` (`01/02/1988`) mainframe baseline sentinel to `"<=1988"`, rejects
       `1899` / `1900-01-01` / `1900-01-02` null-epoch placeholders while preserving genuine historic
       1900 dates, preserves the earliest valid `acq_dt` within a merged tenure, and pairs `YYYYMMDD`
       (`+-120` days) and `YYYY0000` (`[prev_end_year, start_year]`) Clerk instruments with 1:1
       exclusive token assignment (`DF-2`, `DF-6`).
     - Emits explicit sanitized `<owner_name>` strings on all tenures including the active roll year
       (`|`, `~`, control chars, and `U+2014`/`U+2013` dashes stripped) and compresses shards via
       `schema.compress_deterministic_gzip_json()` (header-free RFC 1952 `mtime=0`) for deterministic SHA-256 output (`DF-4`, `DF-7`).
  2. Archive Streaming & Single-Writer Shard Layer:
     - Streams `2005..ACTIVE_TAX_YEAR` `Real_acct_owner.zip` archives (`owners.txt`, `real_acct.txt`,
       `deeds.txt` matched case-insensitively by basename with lowercase-trimmed headers and a
       `>= 1,000,000` parsed-account floor per full roll) without mutating `permits_<prefix3>.json`,
       updates `pmtiles_manifest.json` via `schema.update_pmtiles_manifest_v3`, prunes stale shards,
       and verifies `app/` bundle size `< HARD_MAX_APP_BYTES` (`980 MiB`) (`DF-3`).
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, ThreadPoolExecutor, wait
from dataclasses import dataclass
import datetime
import gc
import io
import json
import multiprocessing
import os
from pathlib import Path
import re
import sys
import threading
import time
from typing import Any
import zipfile

from atlas_pipeline.schema import (
    ACTIVE_TAX_YEAR,
    HARD_MAX_APP_BYTES,
    MAX_SINGLE_FILE_BYTES,
    MIN_VALID_YEAR,
    RE_OWNER_CARE_OF_TAIL,
    collect_standing_and_referenced_atlas_hcads,
    compress_deterministic_gzip_json,
    download_archive_streaming,
    find_zip_member_case_insensitive as _find_zip_member_case_insensitive,
    is_confidential_owner_token,
    is_placeholder_owner_token,
    normalize_hcad_clerk_instrument,
    normalize_hcad_date,
    normalize_standing_hcad_token,
    parse_dos_yyyymmdd,
    peek_real_acct_zip_year as _peek_real_acct_zip_year,
    sanitize_atlas_text,
    update_pmtiles_manifest_v3,
    verify_zip_file_integrity,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = REPO_ROOT / "pipeline" / "cache"
ROLLS_CACHE_DIR = CACHE_DIR / "rolls"
APP_DIR = REPO_ROOT / "app"
DATA_DIR = APP_DIR / "public" / "data"
PERMITS_DIR = DATA_DIR / "permits"
OWNER_HISTORY_DIR = DATA_DIR / "owner_history"

HCAD_CAMA_BASE_URL = "https://download.hcad.org/data/CAMA"
OWNER_HISTORY_START_YEAR = 2005
MAX_OWNER_TENURES_PER_PARCEL = 12
MIN_PARSED_ACCOUNTS_PER_ROLL = 1_000_000
HCAD_MAINFRAME_BASELINE_ISO = "1988-01-02"
HCAD_MAINFRAME_SENTINEL = "<=1988"

_RE_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]+")
_RE_DOTTED_ABBREV = re.compile(r"\b([A-Z])\.([A-Z])\.(?:([A-Z])\.)?(?:([A-Z])\.)?")
_RE_DBA_SUFFIX = re.compile(r"\s*\(DBA\s+[^)]*\)\s*$", re.IGNORECASE)
_RE_BOILERPLATE_TRAIL = re.compile(
    r"\b(?:ET\s*AL|ETAL|ET\s*UX|ETUX|ET\s*VIR|ETVIR|HEIRS|LIFE\s+EST(?:ATE)?)\b",
    re.IGNORECASE,
)
_RE_SERIES_DESIGNATOR = re.compile(
    r"\b(?:SERIES|SER|FUND|TRANCHE|POOL|PHASE|PH|CLASS|UNIT|BLDG|LOT|SEC)\s+([0-9A-Z\-]+)\b",
    re.IGNORECASE,
)
_RE_CORP_MARKER = re.compile(
    r"\b(?:LLC|PLLC|LP|LLP|LTD|LIMITED|INC|INCORPORATED|CORP|CORPORATION|COMPANY|BANK|FSB|NA|REIT|HOA|POA|ASSOC|ASSOCIATION)\b",
    re.IGNORECASE,
)
_RE_TRUST_MARKER = re.compile(
    r"\b(?:TRUST|TRUSTEE|TRUSTEES|TR|LIVING\s+TRUST|REVOCABLE|IRREVOCABLE|FAMILY\s+TRUST|ESTATE)\b",
    re.IGNORECASE,
)

GENERATIONAL_SUFFIX_TOKENS: frozenset[str] = frozenset({
    "JR",
    "SR",
    "II",
    "III",
    "IV",
    "V",
})

ROMAN_NUMERAL_TOKENS: frozenset[str] = frozenset({
    "I",
    "II",
    "III",
    "IV",
    "V",
    "VI",
    "VII",
    "VIII",
    "IX",
    "X",
    "XI",
    "XII",
})

TENURE_ENTITY_STOPWORDS: frozenset[str] = frozenset({
    "HOUSTON",
    "TEXAS",
    "TX",
    "HARRIS",
    "COUNTY",
    "CITY",
    "HEIGHTS",
    "MONTROSE",
    "MEMORIAL",
    "RIVER",
    "OAKS",
    "OAK",
    "WEST",
    "EAST",
    "NORTH",
    "SOUTH",
    "CENTRAL",
    "DOWNTOWN",
    "MIDTOWN",
    "PARK",
    "BAYOU",
    "SPRING",
    "CYPRESS",
    "KATY",
    "HUMBLE",
    "BAYTOWN",
    "PASADENA",
    "BELLAIRE",
    "PROPERTIES",
    "PROPERTY",
    "PROP",
    "HOLDINGS",
    "HOLDING",
    "TRUST",
    "TRUSTEE",
    "TRUSTEES",
    "TR",
    "TRS",
    "LIVING",
    "REVOCABLE",
    "IRREVOCABLE",
    "FAMILY",
    "ESTATE",
    "HEIRS",
    "OF",
    "THE",
    "AND",
    "INVESTMENTS",
    "INVESTMENT",
    "INVESTORS",
    "INV",
    "REALTY",
    "REAL",
    "PARTNERS",
    "PARTNERSHIP",
    "DEVELOPMENT",
    "DEVELOPERS",
    "DEV",
    "VENTURES",
    "VENTURE",
    "CAPITAL",
    "HOMES",
    "HOME",
    "HOMEBUILDERS",
    "BUILDERS",
    "BUILDER",
    "CONSTRUCTION",
    "GROUP",
    "ENTERPRISES",
    "ENTERPRISE",
    "MANAGEMENT",
    "MGMT",
    "COMMERCIAL",
    "RESIDENTIAL",
    "APARTMENTS",
    "VILLAS",
    "TOWNHOMES",
    "LOFTS",
    "PLAZA",
    "CENTER",
    "CENTRE",
    "ASSET",
    "ASSETS",
    "FUND",
    "EQUITY",
    "ACQUISITIONS",
    "PORTFOLIO",
    "SERVICES",
    "SOLUTIONS",
    "LLC",
    "PLLC",
    "LP",
    "LLP",
    "LTD",
    "LIMITED",
    "INC",
    "INCORPORATED",
    "CORP",
    "CORPORATION",
    "COMPANY",
    "CO",
    "ET",
    "AL",
    "ETAL",
    "UX",
    "ETUX",
    "VIR",
    "ETVIR",
    "LE",
})


@dataclass(frozen=True)
class RollOwnerSnapshot:
    """Single-year certified HCAD roll ownership observation for one parcel."""

    year: int
    owner_name: str
    is_confidential: bool = False
    acq_dt: str = ""


@dataclass
class ConsolidatedOwnerTenure:
    """Merged continuous ownership tenure spanning `[start_year, end_year]`."""

    start_year: int
    end_year: int
    owner_name: str
    acq_dt: str = ""
    deed_token: str = ""


def _clean_wire_fragment(raw_val: str) -> str:
    cleaned = sanitize_atlas_text(str(raw_val or ""))
    cleaned = _RE_CONTROL_CHARS.sub(" ", cleaned).replace("|", " ").replace("~", " ")
    cleaned = RE_OWNER_CARE_OF_TAIL.sub("", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip(" -;,")


def sanitize_wire_owner_name(raw_name: str, raw_aka: str = "", *, max_len: int = 84) -> str:
    """Sanitize an HCAD owner name (plus optional DBA `aka`) for `|` / `~` wire encoding (`DF-4`)."""
    base = _clean_wire_fragment(raw_name)
    if not base or is_placeholder_owner_token(base):
        return ""

    aka = _clean_wire_fragment(raw_aka)

    if (
        aka
        and len(aka) >= 3
        and not aka.isdigit()
        and not is_placeholder_owner_token(aka)
        and not re.match(r"^\d+\s+[A-Z]", aka.upper())
        and not re.match(r"^P\.?\s*O\.?\s*BOX\b", aka.upper())
    ):
        base_alnum = re.sub(r"[^A-Z0-9]+", "", base.upper())
        aka_alnum = re.sub(r"[^A-Z0-9]+", "", aka.upper())
        if aka_alnum and aka_alnum not in base_alnum and base_alnum not in aka_alnum:
            cand = f"{base} (DBA {aka})"
            if len(cand) <= max_len:
                base = cand

    if len(base) > max_len:
        base = base[:max_len].rstrip(" -;,(")
    return base


def normalize_acquisition_date_token(
    raw_new_own_dt: str,
    *,
    max_year: int = ACTIVE_TAX_YEAR,
) -> str:
    """Normalize HCAD `new_own_dt` into `'YYYYMMDD'`, `'<=1988'`, or `''` (`DF-2`, `DF-6`, `DF-7`).

    Domain rationale (`pdataCodebook.pdf`):
      - HCAD converted paper tax cards to its CAMA mainframe on January 2, 1988 (`01/02/1988`).
        Parcels whose ownership predated that mainframe load carry `01/02/1988` as a synthetic
        floor sentinel rather than an actual closing date, so it is normalized to `'<=1988'`.
      - Microsoft COM/OLE (`12/30/1899`, `12/31/1899`) and SQL Server (`01/01/1900`, `01/02/1900`)
        null-epoch placeholders are rejected via `schema.normalize_hcad_date`, while genuine
        historic 1900 conveyance dates (e.g. `06/15/1900`) are preserved.
    """
    s = str(raw_new_own_dt or "").strip()
    if not s or s.upper() in ("0", "NONE", "NULL", "NA", "N/A", "00000000"):
        return ""
    if s == HCAD_MAINFRAME_SENTINEL:
        return HCAD_MAINFRAME_SENTINEL

    s_part = s.split(" ")[0].strip()
    if re.fullmatch(r"\d{8}", s_part):
        s_part = f"{s_part[:4]}-{s_part[4:6]}-{s_part[6:8]}"

    iso_dt, _yr = normalize_hcad_date(
        s_part,
        allow_fallback=False,
        min_year=MIN_VALID_YEAR,
        max_year=int(max_year) + 1,
    )
    if not iso_dt:
        return ""
    if iso_dt == HCAD_MAINFRAME_BASELINE_ISO:
        return HCAD_MAINFRAME_SENTINEL
    return iso_dt.replace("-", "")


def _parse_yyyymmdd_to_date(dt8: str) -> datetime.date | None:
    if not dt8 or len(dt8) != 8 or not dt8.isdigit():
        return None
    yr, mo, dy = int(dt8[:4]), int(dt8[4:6]), int(dt8[6:8])
    if mo == 0 or dy == 0:
        return None
    try:
        return datetime.date(yr, mo, dy)
    except ValueError:
        return None


def _normalize_owner_for_comparison(name: str) -> str:
    s = _RE_DBA_SUFFIX.sub("", str(name or "")).upper()
    s = RE_OWNER_CARE_OF_TAIL.sub("", s)
    s = _RE_DOTTED_ABBREV.sub(
        lambda m: "".join(g for g in m.groups() if g),
        s,
    )
    s = s.replace("&", " AND ")
    s = re.sub(r"[^A-Z0-9\s]+", " ", s)
    s = re.sub(r"\bL\s+L\s+C\b", "LLC", s)
    s = re.sub(r"\bP\s+L\s+L\s+C\b", "PLLC", s)
    s = re.sub(r"\bL\s+L\s+P\b", "LLP", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _extract_series_and_numbers(norm_name: str) -> tuple[tuple[str, ...], tuple[str, ...], frozenset[str]]:
    series_hits = tuple(sorted(m.group(1).upper() for m in _RE_SERIES_DESIGNATOR.finditer(norm_name)))
    tokens = norm_name.split()
    numeric_tokens = tuple(sorted(t for t in tokens if any(ch.isdigit() for ch in t)))
    roman_tokens = frozenset(t for t in tokens if t in ROMAN_NUMERAL_TOKENS and t != "I" or (t == "I" and ("SERIES I" in norm_name or "FUND I" in norm_name or "PHASE I" in norm_name)))
    return series_hits, numeric_tokens, roman_tokens


def _extract_generational_suffixes(norm_name: str) -> frozenset[str]:
    tokens = norm_name.split()
    if len(tokens) <= 1:
        return frozenset()
    return frozenset(t for t in tokens[1:] if t in GENERATIONAL_SUFFIX_TOKENS)


def are_same_owner_tenure(
    prev_name: str,
    prev_acq_dt: str,
    prev_end_year: int,
    curr_name: str,
    curr_acq_dt: str,
    curr_year: int,
    *,
    has_intervening_deed: bool = False,
) -> bool:
    """Determine whether two adjacent HCAD roll snapshots represent the same ownership tenure (`DF-2`)."""
    norm_a = _normalize_owner_for_comparison(prev_name)
    norm_b = _normalize_owner_for_comparison(curr_name)
    if not norm_a or not norm_b:
        return False
    if norm_a == norm_b:
        return True

    # 1. Hard series / numeric / Roman numeral guard (e.g. SERIES 1 vs SERIES 12, FUND I vs FUND II, 101 MAIN LLC vs 105 MAIN LLC)
    ser_a, num_a, rom_a = _extract_series_and_numbers(norm_a)
    ser_b, num_b, rom_b = _extract_series_and_numbers(norm_b)
    if ser_a != ser_b or num_a != num_b or rom_a != rom_b:
        return False

    # 2. Corporate / trust structural boundary guard
    corp_a = bool(_RE_CORP_MARKER.search(norm_a))
    corp_b = bool(_RE_CORP_MARKER.search(norm_b))
    if corp_a != corp_b:
        return False

    acq_changed = bool(prev_acq_dt and curr_acq_dt and prev_acq_dt != curr_acq_dt)
    acq_jumped_at_transition = False
    if curr_acq_dt and curr_acq_dt != HCAD_MAINFRAME_SENTINEL and curr_acq_dt != prev_acq_dt:
        curr_acq_yr = int(curr_acq_dt[:4]) if curr_acq_dt[:4].isdigit() else 0
        if curr_acq_yr >= prev_end_year:
            acq_jumped_at_transition = True

    trust_a = bool(_RE_TRUST_MARKER.search(norm_a))
    trust_b = bool(_RE_TRUST_MARKER.search(norm_b))
    if trust_a != trust_b and (acq_changed or acq_jumped_at_transition or has_intervening_deed):
        return False

    # 3. Generational suffix guard (SMITH JOHN SR -> SMITH JOHN JR or parent -> child)
    gen_a = _extract_generational_suffixes(norm_a)
    gen_b = _extract_generational_suffixes(norm_b)
    if gen_a != gen_b:
        if gen_a and gen_b:
            return False
        if acq_changed or acq_jumped_at_transition or has_intervening_deed:
            return False

    strip_a = re.sub(r"\s+", " ", _RE_BOILERPLATE_TRAIL.sub(" ", norm_a)).strip()
    strip_b = re.sub(r"\s+", " ", _RE_BOILERPLATE_TRAIL.sub(" ", norm_b)).strip()
    if strip_a and strip_a == strip_b:
        return True

    # Never fuzzy-merge non-identical owners across a recorded conveyance or jumped acquisition date
    if has_intervening_deed or acq_jumped_at_transition:
        return False

    # 4. Genuine >= 18-char fixed-width HCAD column truncation guard
    shorter, longer = (strip_a, strip_b) if len(strip_a) <= len(strip_b) else (strip_b, strip_a)
    if len(shorter) >= 18 and longer.startswith(shorter):
        rem = longer[len(shorter) :]
        # Reject truncations inside a number or distinct series word
        if not rem or not rem[0].isdigit():
            return True

    # 5. Stopword-safe distinctive token equivalence (rejects "HOUSTON HEIGHTS PROPERTIES LLC" vs "HOUSTON HEIGHTS HOLDINGS LLC")
    toks_a = [t for t in strip_a.split() if len(t) > 1 and t not in GENERATIONAL_SUFFIX_TOKENS]
    toks_b = [t for t in strip_b.split() if len(t) > 1 and t not in GENERATIONAL_SUFFIX_TOKENS]
    if not toks_a or not toks_b:
        return False

    dist_a = {t for t in toks_a if t not in TENURE_ENTITY_STOPWORDS}
    dist_b = {t for t in toks_b if t not in TENURE_ENTITY_STOPWORDS}
    if not dist_a or not dist_b:
        return False

    # Require identical distinctive non-stopword token sets with >= 2 distinctive tokens
    # (e.g. {"MORRIS", "LAURA"} vs {"MORRIS", "LAURA"} when only middle initial "M" changed)
    if dist_a == dist_b and len(dist_a) >= 2:
        # Also ensure primary first token (surname or lead entity word) matches
        if toks_a[0] == toks_b[0]:
            return True

    return False


def _pick_earliest_valid_acq_dt(existing_dt: str, candidate_dt: str, tenure_start_year: int) -> str:
    """Preserve the earliest valid `acq_dt` (`<= start_yr`) within a merged tenure (`DF-2`)."""
    if existing_dt == HCAD_MAINFRAME_SENTINEL or candidate_dt == HCAD_MAINFRAME_SENTINEL:
        return HCAD_MAINFRAME_SENTINEL

    valid_cands: list[str] = []
    for dt in (existing_dt, candidate_dt):
        if not dt or len(dt) != 8 or not dt.isdigit():
            continue
        yr = int(dt[:4])
        if yr <= tenure_start_year:
            valid_cands.append(dt)
    if valid_cands:
        return min(valid_cands)

    # Fallback if HCAD recorded an acquisition date in the roll year itself (e.g. early Jan closing)
    any_cands = [dt for dt in (existing_dt, candidate_dt) if dt and len(dt) == 8 and dt.isdigit()]
    return min(any_cands) if any_cands else ""


def _deduplicate_account_deeds(
    raw_deeds: list[tuple[str, str]] | tuple[tuple[str, str], ...],
) -> tuple[list[tuple[str, str]], set[str]]:
    """Deduplicate `(dos_yyyymmdd, clerk_token)` across 22 rolls, preferring full `YYYYMMDD` over `YYYY0000`."""
    best_date_by_tok: dict[str, str] = {}
    untokened: set[str] = set()
    for raw_dt, raw_tok in raw_deeds or ():
        dt = str(raw_dt or "").strip()
        tok = str(raw_tok or "").strip()
        if not tok:
            if len(dt) >= 4 and dt[:4].isdigit() and dt != "00000000":
                untokened.add(dt)
            continue
        prev_dt = best_date_by_tok.get(tok)
        if prev_dt is None:
            best_date_by_tok[tok] = dt
        else:
            prev_is_full = len(prev_dt) == 8 and prev_dt.isdigit() and not prev_dt.endswith("0000") and prev_dt != "00000000"
            curr_is_full = len(dt) == 8 and dt.isdigit() and not dt.endswith("0000") and dt != "00000000"
            if curr_is_full and not prev_is_full:
                best_date_by_tok[tok] = dt
            elif curr_is_full == prev_is_full and dt > prev_dt:
                best_date_by_tok[tok] = dt

    merged = [(dt, tok) for tok, dt in best_date_by_tok.items()]
    merged.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return merged, untokened


def _has_deed_in_year_window(
    deeds: list[tuple[str, str]],
    all_deed_dates: set[str],
    min_yr: int,
    max_yr: int,
    after_acq_dt: str = "",
) -> bool:
    exact_floor = (
        after_acq_dt
        if len(after_acq_dt) == 8 and after_acq_dt.isdigit() and not after_acq_dt.endswith("0000")
        else ""
    )
    year_floor = (
        int(after_acq_dt[:4])
        if len(after_acq_dt) == 8 and after_acq_dt[:4].isdigit() and after_acq_dt.endswith("0000")
        else 0
    )

    def _is_intervening(dt: str) -> bool:
        if len(dt) < 4 or not dt[:4].isdigit():
            return False
        y = int(dt[:4])
        if not (min_yr <= y <= max_yr):
            return False
        if exact_floor and len(dt) == 8 and dt.isdigit() and not dt.endswith("0000") and dt <= exact_floor:
            return False
        if year_floor and y <= year_floor:
            return False
        return True

    for dt, _tok in deeds:
        if _is_intervening(dt):
            return True
    for dt in all_deed_dates:
        if _is_intervening(dt):
            return True
    return False


def consolidate_account_tenures(
    snapshots: list[RollOwnerSnapshot] | tuple[RollOwnerSnapshot, ...],
    *,
    deeds: list[tuple[str, str]] | tuple[tuple[str, str], ...] = (),
    all_deed_dates: set[str] | None = None,
    active_tax_year: int = ACTIVE_TAX_YEAR,
    max_tenures: int = MAX_OWNER_TENURES_PER_PARCEL,
) -> list[ConsolidatedOwnerTenure]:
    """Consolidate annual roll snapshots into privacy-shielded, deed-paired ownership tenures (`DF-1`, `DF-2`, `DF-4`)."""
    if not snapshots:
        return []

    ordered = [
        RollOwnerSnapshot(
            year=int(s.year),
            owner_name=s.owner_name,
            acq_dt=normalize_acquisition_date_token(s.acq_dt, max_year=active_tax_year),
            is_confidential=s.is_confidential,
        )
        for s in sorted(snapshots, key=lambda s: int(s.year))
    ]
    # If the latest observed roll snapshot (or explicit active_tax_year snapshot) is confidential, omit parcel completely.
    if ordered[-1].is_confidential:
        return []
    for s in ordered:
        if s.year == active_tax_year and (s.is_confidential or is_confidential_owner_token(s.owner_name)):
            return []

    dedup_deeds, untokened_dates = _deduplicate_account_deeds(deeds)
    deed_dates_set = set(all_deed_dates or ()) | untokened_dates
    for dt, _tok in dedup_deeds:
        if len(dt) >= 4 and dt[:4].isdigit() and dt != "00000000":
            deed_dates_set.add(dt)

    n = len(ordered)
    tainted = [
        bool(s.is_confidential or is_confidential_owner_token(s.owner_name))
        for s in ordered
    ]

    # Retroactive pre-election taint (backward) + intra-tenure taint (forward) (`DF-1`):
    # If an owner held the parcel in 2011-2015 publicly, elected Sec. 25.025 in 2016-2021, and sold in 2022,
    # taint 2011-2015 and any clerically unmasked post-election years of that same tenure so personal names
    # and acquisition deed tokens never leak (including when HCAD resets confidential new_own_dt to <=1988 or '').
    recovered_conf_acqs: set[str] = set()
    for idx in range(n):
        if not tainted[idx]:
            continue
        conf_acq = ordered[idx].acq_dt
        pre_taint_owner = ""
        # Walk backward across contiguous snapshots belonging to the same pre-election tenure
        for j in range(idx - 1, -1, -1):
            if tainted[j]:
                if (not conf_acq or conf_acq == HCAD_MAINFRAME_SENTINEL) and ordered[j].acq_dt:
                    conf_acq = ordered[j].acq_dt
                if (
                    not pre_taint_owner
                    and ordered[j].owner_name
                    and not is_confidential_owner_token(ordered[j].owner_name)
                ):
                    pre_taint_owner = ordered[j].owner_name
                continue
            snap_j = ordered[j]
            snap_next = ordered[j + 1]
            has_deed = _has_deed_in_year_window(
                dedup_deeds, deed_dates_set, snap_j.year, snap_next.year, after_acq_dt=snap_j.acq_dt
            )
            if conf_acq and snap_j.acq_dt:
                if snap_j.acq_dt == conf_acq and (
                    conf_acq != HCAD_MAINFRAME_SENTINEL or not has_deed
                ):
                    tainted[j] = True
                    if (
                        not pre_taint_owner
                        and snap_j.owner_name
                        and not is_confidential_owner_token(snap_j.owner_name)
                    ):
                        pre_taint_owner = snap_j.owner_name
                    continue
                same_as_pre_taint = bool(
                    pre_taint_owner
                    and are_same_owner_tenure(
                        snap_j.owner_name,
                        "",
                        snap_j.year,
                        pre_taint_owner,
                        "",
                        snap_next.year,
                        has_intervening_deed=has_deed,
                    )
                )
                if same_as_pre_taint or (
                    not has_deed
                    and not pre_taint_owner
                    and (
                        conf_acq == HCAD_MAINFRAME_SENTINEL
                        or snap_j.acq_dt == HCAD_MAINFRAME_SENTINEL
                    )
                ):
                    tainted[j] = True
                    if snap_j.acq_dt and snap_j.acq_dt != HCAD_MAINFRAME_SENTINEL:
                        recovered_conf_acqs.add(snap_j.acq_dt)
                        if conf_acq == HCAD_MAINFRAME_SENTINEL:
                            conf_acq = snap_j.acq_dt
                    if (
                        not pre_taint_owner
                        and snap_j.owner_name
                        and not is_confidential_owner_token(snap_j.owner_name)
                    ):
                        pre_taint_owner = snap_j.owner_name
                    continue
                break
            same_blank_pre_taint = bool(
                pre_taint_owner
                and are_same_owner_tenure(
                    snap_j.owner_name,
                    "",
                    snap_j.year,
                    pre_taint_owner,
                    "",
                    snap_next.year,
                    has_intervening_deed=has_deed,
                )
            )
            if same_blank_pre_taint or (
                not has_deed
                and (
                    not pre_taint_owner
                    or is_confidential_owner_token(snap_j.owner_name)
                )
            ):
                tainted[j] = True
                if not conf_acq and snap_j.acq_dt:
                    conf_acq = snap_j.acq_dt
                if (
                    not pre_taint_owner
                    and snap_j.owner_name
                    and not is_confidential_owner_token(snap_j.owner_name)
                ):
                    pre_taint_owner = snap_j.owner_name
            else:
                break

        # Walk forward across contiguous snapshots belonging to the same un-conveyed confidential tenure
        fwd_acq = conf_acq if (conf_acq and conf_acq != HCAD_MAINFRAME_SENTINEL) else ordered[idx].acq_dt
        if fwd_acq and fwd_acq != HCAD_MAINFRAME_SENTINEL:
            recovered_conf_acqs.add(fwd_acq)
        known_taint_owner = pre_taint_owner
        for j in range(idx + 1, n):
            if tainted[j]:
                if (not fwd_acq or fwd_acq == HCAD_MAINFRAME_SENTINEL) and ordered[j].acq_dt:
                    fwd_acq = ordered[j].acq_dt
                    if fwd_acq and fwd_acq != HCAD_MAINFRAME_SENTINEL:
                        recovered_conf_acqs.add(fwd_acq)
                continue
            snap_prev = ordered[j - 1]
            snap_j = ordered[j]
            eff_prev_acq = (
                snap_prev.acq_dt
                if (snap_prev.acq_dt and snap_prev.acq_dt != HCAD_MAINFRAME_SENTINEL)
                else fwd_acq
            )
            has_deed = _has_deed_in_year_window(
                dedup_deeds, deed_dates_set, snap_prev.year, snap_j.year, after_acq_dt=eff_prev_acq
            )
            eff_prev_owner = (
                snap_prev.owner_name
                if (snap_prev.owner_name and not is_confidential_owner_token(snap_prev.owner_name))
                else known_taint_owner
            )
            if fwd_acq and snap_j.acq_dt and snap_j.acq_dt == fwd_acq and not has_deed:
                tainted[j] = True
                if snap_j.owner_name and not is_confidential_owner_token(snap_j.owner_name):
                    known_taint_owner = snap_j.owner_name
            elif (
                (not fwd_acq or fwd_acq == HCAD_MAINFRAME_SENTINEL)
                and snap_j.acq_dt
                and snap_j.acq_dt != HCAD_MAINFRAME_SENTINEL
                and len(snap_j.acq_dt) >= 4
                and snap_j.acq_dt[:4].isdigit()
                and int(snap_j.acq_dt[:4]) < ordered[idx].year
                and not has_deed
                and (
                    not known_taint_owner
                    or are_same_owner_tenure(
                        known_taint_owner,
                        "",
                        ordered[idx].year,
                        snap_j.owner_name,
                        "",
                        snap_j.year,
                        has_intervening_deed=False,
                    )
                )
            ):
                tainted[j] = True
                fwd_acq = snap_j.acq_dt
                recovered_conf_acqs.add(fwd_acq)
                if snap_j.owner_name and not is_confidential_owner_token(snap_j.owner_name):
                    known_taint_owner = snap_j.owner_name
            elif bool(eff_prev_owner) and are_same_owner_tenure(
                eff_prev_owner,
                "",
                snap_prev.year,
                snap_j.owner_name,
                "",
                snap_j.year,
                has_intervening_deed=has_deed,
            ):
                tainted[j] = True
                if snap_j.acq_dt and snap_j.acq_dt != HCAD_MAINFRAME_SENTINEL:
                    fwd_acq = snap_j.acq_dt
                    recovered_conf_acqs.add(snap_j.acq_dt)
                if snap_j.owner_name and not is_confidential_owner_token(snap_j.owner_name):
                    known_taint_owner = snap_j.owner_name
            else:
                break

    # If taint reached the latest/active snapshot, omit the entire parcel (`DF-1`).
    if tainted[-1]:
        return []

    # Collect redacted year windows & tainted acq_dts so deeds belonging to redacted tenures are never mispaired
    redacted_acq_dts: set[str] = {
        ordered[i].acq_dt
        for i in range(n)
        if tainted[i] and ordered[i].acq_dt and ordered[i].acq_dt != HCAD_MAINFRAME_SENTINEL
    } | recovered_conf_acqs
    redacted_years: set[int] = {ordered[i].year for i in range(n) if tainted[i]}

    # Build contiguous tenures from non-tainted, non-placeholder snapshots
    tenures_asc: list[ConsolidatedOwnerTenure] = []
    prev_boundary_years: list[int] = []  # Tracks preceding roll year before each tenure started

    last_seen_year_any = 0
    last_was_invalid_or_tainted = False

    for i, snap in enumerate(ordered):
        if tainted[i]:
            last_seen_year_any = snap.year
            last_was_invalid_or_tainted = True
            continue

        clean_name = sanitize_wire_owner_name(snap.owner_name)
        norm_acq = snap.acq_dt
        if not clean_name:
            last_seen_year_any = snap.year
            last_was_invalid_or_tainted = True
            continue

        if tenures_asc and not last_was_invalid_or_tainted:
            prev_t = tenures_asc[-1]
            has_deed = _has_deed_in_year_window(
                dedup_deeds, deed_dates_set, prev_t.end_year, snap.year, after_acq_dt=prev_t.acq_dt
            )
            if are_same_owner_tenure(
                prev_t.owner_name,
                prev_t.acq_dt,
                prev_t.end_year,
                clean_name,
                norm_acq,
                snap.year,
                has_intervening_deed=has_deed,
            ):
                prev_t.end_year = snap.year
                prev_t.acq_dt = _pick_earliest_valid_acq_dt(prev_t.acq_dt, norm_acq, prev_t.start_year)
                # Prefer newer clean display name unless newer dropped a DBA or truncated shorter
                if "(DBA " in prev_t.owner_name and "(DBA " not in clean_name:
                    pass
                elif len(clean_name) + 3 >= len(prev_t.owner_name):
                    prev_t.owner_name = clean_name
                last_seen_year_any = snap.year
                last_was_invalid_or_tainted = False
                continue

        prev_boundary = last_seen_year_any if last_seen_year_any > 0 else (snap.year - 1 if snap.year > OWNER_HISTORY_START_YEAR else 0)
        tenures_asc.append(
            ConsolidatedOwnerTenure(
                start_year=snap.year,
                end_year=snap.year,
                owner_name=clean_name,
                acq_dt=_pick_earliest_valid_acq_dt("", norm_acq, snap.year),
            )
        )
        prev_boundary_years.append(prev_boundary)
        last_seen_year_any = snap.year
        last_was_invalid_or_tainted = False

    if not tenures_asc:
        return []

    # Pair Clerk deed tokens 1:1 exclusively across consolidated tenures (`DF-2`)
    candidate_pairs: list[tuple[int, int, int, int]] = []  # (priority_tier, distance_score, tenure_idx, deed_idx)
    reserved_redacted_deed_idxs: set[int] = set()
    for d_idx, (d_dt, _d_tok) in enumerate(dedup_deeds):
        if d_dt in redacted_acq_dts:
            reserved_redacted_deed_idxs.add(d_idx)
            continue
        if len(d_dt) >= 4 and d_dt[:4].isdigit():
            d_yr = int(d_dt[:4])
            # If deed year falls strictly inside redacted years and before the next public tenure's transition window
            if d_yr in redacted_years and all(
                not (prev_boundary_years[t_i] <= d_yr <= tenures_asc[t_i].start_year and prev_boundary_years[t_i] == tenures_asc[t_i].start_year - 1)
                for t_i in range(len(tenures_asc))
            ):
                reserved_redacted_deed_idxs.add(d_idx)
            elif (d_yr + 1) in redacted_years and d_yr not in redacted_years:
                d_date_chk = _parse_yyyymmdd_to_date(d_dt)
                has_tier1_public_match = any(
                    (t_dt := _parse_yyyymmdd_to_date(t.acq_dt)) is not None
                    and d_date_chk is not None
                    and abs((d_date_chk - t_dt).days) <= 120
                    for t in tenures_asc
                )
                if not has_tier1_public_match:
                    reserved_redacted_deed_idxs.add(d_idx)

    for t_idx, ten in enumerate(tenures_asc):
        prev_end_yr = prev_boundary_years[t_idx]
        ten_date = _parse_yyyymmdd_to_date(ten.acq_dt)
        ten_acq_yr = int(ten.acq_dt[:4]) if (len(ten.acq_dt) >= 4 and ten.acq_dt[:4].isdigit()) else 0

        for d_idx, (d_dt, d_tok) in enumerate(dedup_deeds):
            if d_idx in reserved_redacted_deed_idxs or not d_tok:
                continue
            if len(d_dt) != 8 or not d_dt.isdigit() or d_dt == "00000000":
                continue
            d_yr = int(d_dt[:4])
            d_date = _parse_yyyymmdd_to_date(d_dt)

            # Tier 1: Exact or <= 120-day calendar match between YYYYMMDD deed and YYYYMMDD acq_dt
            if ten_date is not None and d_date is not None:
                day_diff = abs((d_date - ten_date).days)
                if day_diff <= 120:
                    candidate_pairs.append((1, day_diff, t_idx, d_idx))
                    continue

            # Tier 2: Year-only YYYY0000 deed matching ten_acq_yr
            if d_date is None and ten_acq_yr > 0 and d_yr == ten_acq_yr:
                candidate_pairs.append((2, 0, t_idx, d_idx))
                continue

            # Tier 3: Transition window containment [window_lo, ten.start_year] without calendar math on YYYY0000
            if ten.acq_dt != HCAD_MAINFRAME_SENTINEL:
                if prev_end_yr > 0:
                    if prev_end_yr <= d_yr <= ten.start_year:
                        # Avoid pairing if tenure has an explicit much-earlier acq_dt (e.g. 1995 acq_dt vs 2006 deed)
                        if ten_acq_yr == 0 or abs(d_yr - ten_acq_yr) <= 1:
                            dist = abs(ten.start_year - d_yr)
                            candidate_pairs.append((3, dist, t_idx, d_idx))
                elif ten.start_year == OWNER_HISTORY_START_YEAR and ten_acq_yr > 0 and abs(d_yr - ten_acq_yr) <= 1:
                    candidate_pairs.append((3, abs(d_yr - ten_acq_yr), t_idx, d_idx))

    candidate_pairs.sort(key=lambda x: (x[0], x[1], -x[2], x[3]))
    assigned_tenures: set[int] = set()
    used_tokens: set[str] = set()

    for _tier, _dist, t_idx, d_idx in candidate_pairs:
        if t_idx in assigned_tenures:
            continue
        d_dt, d_tok = dedup_deeds[d_idx]
        if d_tok in used_tokens:
            continue
        ten = tenures_asc[t_idx]
        ten.deed_token = d_tok
        if not ten.acq_dt and _parse_yyyymmdd_to_date(d_dt) is not None:
            ten.acq_dt = d_dt
        assigned_tenures.add(t_idx)
        used_tokens.add(d_tok)

    tenures_desc = list(reversed(tenures_asc))
    return tenures_desc[: max(1, int(max_tenures))]


def encode_compact_owner_history(tenures: list[ConsolidatedOwnerTenure] | tuple[ConsolidatedOwnerTenure, ...]) -> str:
    """Encode consolidated tenures newest-to-oldest as `'<span_yr>~<acq_dt>~<deed_tok>~<owner_name>|...'` (`DF-4`, `ANL-2`).

    Compact Pipe/Tilde Wire Schema Contract (synchronized 1:1 with `decodeCompactOwnerHistory`
    in `app/js/clerkSearchHelpers.js`):
      - Record separator: `'|'`, ordered newest-to-oldest, capped at `MAX_OWNER_TENURES_PER_PARCEL = 12`.
      - Field separator : `'~'` (4 positional fields per tenure; decoder uses rest-joining on field `[3]`):
          `[0] <span_yr>`   : `'YYYY-YYYY'` when `start_year != end_year`, or `'YYYY'` for a single roll year.
          `[1] <acq_dt>`    : `'YYYYMMDD'` exact acquisition date, `'<=1988'` for HCAD's `01/02/1988` mainframe
                              conversion floor sentinel, or `''` when unrecorded.
          `[2] <deed_tok>`  : Paired Harris County Clerk instrument token (`'R2019-149789'`, `'20120430232'`,
                              `'FC:...'`, `'PR:...'`, `'VP:...'`) or `''`.
          `[3] <owner_name>`: Sanitized HCAD owner display string (with optional `'(DBA ...)'` suffix). Retained
                              explicitly on all tenures including the active roll year; if omitted (`''`) by a
                              custom caller, `decodeCompactOwnerHistory` falls back to a non-placeholder
                              `currentOwnerName` or omits unnamed attribution.
      - Sanitization preconditions: `'|'`, `'~'`, ASCII control characters (`0x00-0x1f`, `0x7f`), and
        `U+2014`/`U+2013` dashes are stripped/normalized before serialization.
    """
    if not tenures:
        return ""
    parts: list[str] = []
    for t in tenures:
        name_clean = sanitize_wire_owner_name(t.owner_name)
        if not name_clean:
            continue
        span_s = f"{t.start_year}-{t.end_year}" if t.start_year != t.end_year else f"{t.start_year}"
        acq_s = sanitize_atlas_text(str(t.acq_dt or "")).replace("|", "").replace("~", "").strip()
        tok_s = sanitize_atlas_text(str(t.deed_token or "")).replace("|", "").replace("~", "").strip()
        parts.append(f"{span_s}~{acq_s}~{tok_s}~{name_clean}")
    return "|".join(parts)


# ---------------------------------------------------------------------------
# Layer 2: Archive Discovery, Case-Insensitive Streaming & Parallel Shard Builder
# ---------------------------------------------------------------------------


def _split_filtered_roll_row(
    line: str,
    i_acct: int,
    max_need: int,
    target_hcads: set[str] | None,
) -> tuple[str, list[str]] | None:
    """Extract normalized 13-digit HCAD acct and split columns only for target rows."""
    if i_acct == 0 and target_hcads is not None:
        tab = line.find("\t")
        if tab <= 0:
            return None
        raw_acct = line[:tab].strip()
        if not raw_acct:
            return None
        acct = raw_acct if raw_acct in target_hcads else normalize_standing_hcad_token(raw_acct)
        if not acct or acct not in target_hcads:
            return None
        return acct, line.rstrip("\r\n").split("\t", max_need + 1)
    parts = line.rstrip("\r\n").split("\t", max_need + 1)
    if len(parts) <= i_acct:
        return None
    raw_acct = parts[i_acct].strip()
    if not raw_acct:
        return None
    acct = normalize_standing_hcad_token(raw_acct)
    if not acct or (target_hcads is not None and acct not in target_hcads):
        return None
    return acct, parts


def resolve_annual_roll_zip_paths(
    cache_dir: Path = CACHE_DIR,
    *,
    start_year: int = OWNER_HISTORY_START_YEAR,
    end_year: int = ACTIVE_TAX_YEAR,
) -> dict[int, Path]:
    """Return `{year: zip_path}` for all existing `Real_acct_owner.zip` archives in `[start_year, end_year]`."""
    cdir = Path(cache_dir)
    found: dict[int, Path] = {}
    for yr in range(int(start_year), int(end_year) + 1):
        cand_roll = cdir / "rolls" / str(yr) / "Real_acct_owner.zip"
        if cand_roll.exists() and cand_roll.stat().st_size > 0:
            found[yr] = cand_roll

    cand_top = cdir / "Real_acct_owner.zip"
    if cand_top.exists() and cand_top.stat().st_size > 0:
        already_same_file = False
        for existing_zp in found.values():
            try:
                if os.path.samefile(cand_top, existing_zp):
                    already_same_file = True
                    break
            except OSError:
                pass
        if not already_same_file:
            top_yr = _peek_real_acct_zip_year(cand_top)
            if top_yr is None:
                top_yr = ACTIVE_TAX_YEAR
            if int(start_year) <= top_yr <= int(end_year) and top_yr not in found:
                found[top_yr] = cand_top
    return dict(sorted(found.items()))


def ensure_annual_real_acct_archives(
    *,
    cache_dir: Path = CACHE_DIR,
    start_year: int = OWNER_HISTORY_START_YEAR,
    end_year: int = ACTIVE_TAX_YEAR,
    allow_download: bool = True,
) -> dict[int, Path]:
    """Ensure all `start_year..end_year` `Real_acct_owner.zip` archives exist locally and pass ZIP integrity (`DF-7`)."""
    cdir = Path(cache_dir)
    paths = resolve_annual_roll_zip_paths(cdir, start_year=start_year, end_year=end_year)
    missing = [yr for yr in range(int(start_year), int(end_year) + 1) if yr not in paths]
    if not missing:
        return paths
    if not allow_download:
        raise FileNotFoundError(
            f"Missing cached HCAD Real_acct_owner.zip archives for years: {missing}"
        )

    def _download_one(yr: int) -> tuple[int, Path]:
        out_dir = cdir / "rolls" / str(yr)
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / "Real_acct_owner.zip"
        url = f"{HCAD_CAMA_BASE_URL}/{yr}/Real_acct_owner.zip"
        download_archive_streaming(url, dest, timeout=180.0)
        verify_zip_file_integrity(dest)
        with zipfile.ZipFile(dest, "r") as zf:
            if _find_zip_member_case_insensitive(zf, "real_acct.txt") is None:
                dest.unlink(missing_ok=True)
                raise RuntimeError(f"Downloaded {url} lacks real_acct.txt")
        return yr, dest

    with ThreadPoolExecutor(max_workers=min(6, len(missing))) as pool:
        for yr, dest in pool.map(_download_one, missing):
            paths[yr] = dest
    return dict(sorted(paths.items()))


def parse_single_roll_snapshot(
    zip_path: Path,
    roll_year: int,
    target_hcads: set[str] | None = None,
    *,
    min_parsed_accounts: int = MIN_PARSED_ACCOUNTS_PER_ROLL,
    active_tax_year: int = ACTIVE_TAX_YEAR,
) -> tuple[int, int, dict[str, tuple[str, bool, str]], dict[str, list[tuple[str, str]]]]:
    """Stream one HCAD `Real_acct_owner.zip` into compact `(owner_name, is_confidential, acq_dt)` + `deeds` maps.

    Checks `real_acct.txt` `mailto` AND all `owners.txt` rows (`ln_num >= 1`) for confidential markers (`DF-1`),
    matches ZIP members and column headers case-insensitively (`DF-3`), and enforces `min_parsed_accounts`.
    """
    zpath = Path(zip_path)
    yr = int(roll_year)

    # acct -> [mailto_name, acq_dt, is_conf]
    acct_real: dict[str, list[Any]] = {}
    total_parsed = 0

    with zipfile.ZipFile(zpath, "r") as zf:
        real_member = _find_zip_member_case_insensitive(zf, "real_acct.txt")
        if real_member is None:
            raise ValueError(f"Roll {yr} archive {zpath} is missing real_acct.txt")

        with zf.open(real_member, "r") as raw_f:
            tf = io.TextIOWrapper(raw_f, encoding="latin-1", errors="replace")
            hdr_line = tf.readline()
            if not hdr_line:
                raise ValueError(f"Roll {yr} real_acct.txt is empty")
            hdr_cols = hdr_line.rstrip("\r\n").split("\t")
            total_hdr_cols = len(hdr_cols)
            cols = {c.strip().lower(): idx for idx, c in enumerate(hdr_cols)}
            if "acct" not in cols or "mailto" not in cols:
                raise ValueError(f"Roll {yr} real_acct.txt missing required acct/mailto columns: {list(cols)}")
            i_acct = cols["acct"]
            i_mailto = cols["mailto"]
            i_new_own = cols.get("new_own_dt", -1)
            max_need = max(i_acct, i_mailto, i_new_own)
            fast_tail = (
                i_acct == 0
                and i_mailto > 0
                and i_new_own > i_mailto + 10
                and total_hdr_cols > i_new_own
            )
            tail_offset = total_hdr_cols - i_new_own
            expected_rest_tabs = total_hdr_cols - 2

            eff_max_yr = max(yr, active_tax_year)
            acq_cache: dict[str, str] = {"": ""}

            for line in tf:
                if fast_tail:
                    tab = line.find("\t")
                    if tab <= 0:
                        continue
                    raw_acct = line[:tab].strip()
                    if not raw_acct:
                        continue
                    acct = normalize_standing_hcad_token(raw_acct)
                    if not acct:
                        continue
                    total_parsed += 1
                    if target_hcads is not None and acct not in target_hcads:
                        continue
                    row_clean = line[tab + 1 :].rstrip("\r\n")
                    if row_clean.count("\t") == expected_rest_tabs:
                        head_parts = row_clean.split("\t", i_mailto)
                        mailto_raw = head_parts[i_mailto - 1].strip()
                        tail_parts = row_clean.rsplit("\t", tail_offset)
                        new_own_raw = tail_parts[1].strip()
                    else:
                        full_parts = line.rstrip("\r\n").split("\t", max_need + 1)
                        mailto_raw = full_parts[i_mailto].strip() if len(full_parts) > i_mailto else ""
                        new_own_raw = full_parts[i_new_own].strip() if len(full_parts) > i_new_own else ""
                else:
                    parts = line.rstrip("\r\n").split("\t", max_need + 1)
                    if len(parts) <= i_acct:
                        continue
                    raw_acct = parts[i_acct].strip()
                    if not raw_acct:
                        continue
                    acct = normalize_standing_hcad_token(raw_acct)
                    if not acct:
                        continue
                    total_parsed += 1
                    if target_hcads is not None and acct not in target_hcads:
                        continue
                    mailto_raw = parts[i_mailto].strip() if len(parts) > i_mailto else ""
                    new_own_raw = parts[i_new_own].strip() if (i_new_own >= 0 and len(parts) > i_new_own) else ""
                is_conf = is_confidential_owner_token(mailto_raw)
                acq_tok = acq_cache.get(new_own_raw)
                if acq_tok is None:
                    if len(acq_cache) > 100_000:
                        acq_cache.clear()
                        acq_cache[""] = ""
                    acq_tok = normalize_acquisition_date_token(new_own_raw, max_year=eff_max_yr)
                    acq_cache[new_own_raw] = acq_tok
                acct_real[acct] = (mailto_raw, acq_tok, is_conf)

        if total_parsed < int(min_parsed_accounts):
            raise ValueError(
                f"Roll {yr} parsed account count {total_parsed:,} fell below floor {min_parsed_accounts:,} ({zpath})"
            )

        # Stream owners.txt: inspect ALL ln_num >= 1 rows for confidential markers, capture primary name + aka
        owners_primary: dict[str, tuple[int, str, str]] = {}
        owners_conf: set[str] = set()
        owners_member = _find_zip_member_case_insensitive(zf, "owners.txt")
        if owners_member is not None:
            with zf.open(owners_member, "r") as raw_f:
                tf = io.TextIOWrapper(raw_f, encoding="latin-1", errors="replace")
                hdr_line = tf.readline()
                if hdr_line:
                    cols = {c.strip().lower(): idx for idx, c in enumerate(hdr_line.rstrip("\r\n").split("\t"))}
                    i_acct = cols.get("acct", 0)
                    i_ln = cols.get("ln_num", 1)
                    i_name = cols.get("name", 2)
                    i_aka = cols.get("aka", -1)
                    max_need = max(i_acct, i_ln, i_name, i_aka)
                    for line in tf:
                        matched = _split_filtered_roll_row(line, i_acct, max_need, target_hcads)
                        if matched is None:
                            continue
                        acct, parts = matched
                        nm_raw = parts[i_name].strip() if len(parts) > i_name else ""
                        aka_raw = parts[i_aka].strip() if (i_aka >= 0 and len(parts) > i_aka) else ""
                        if is_confidential_owner_token(nm_raw) or is_confidential_owner_token(aka_raw):
                            owners_conf.add(acct)
                            continue
                        ln_s = parts[i_ln].strip() if len(parts) > i_ln else "1"
                        ln_num = int(ln_s) if ln_s.isdigit() else 99
                        prev_p = owners_primary.get(acct)
                        if prev_p is None or ln_num < prev_p[0]:
                            if nm_raw:
                                owners_primary[acct] = (ln_num, nm_raw, aka_raw)

        # Merge real_acct + owners into compact snapshot tuples and immediately release intermediate maps
        snapshots_out: dict[str, tuple[str, bool, str]] = {}
        name_cache: dict[str, str] = {"": ""}

        def _cached_clean_name(raw_s: str) -> str:
            hit = name_cache.get(raw_s)
            if hit is not None:
                return hit
            if len(name_cache) > 250_000:
                name_cache.clear()
                name_cache[""] = ""
            cleaned = sanitize_wire_owner_name(raw_s)
            name_cache[raw_s] = cleaned
            return cleaned

        all_seen_accts = set(acct_real.keys()) | set(owners_primary.keys()) | owners_conf
        for acct in all_seen_accts:
            real_row = acct_real.get(acct)
            mailto_raw = real_row[0] if real_row is not None else ""
            acq_tok = real_row[1] if real_row is not None else ""
            is_conf = bool((real_row is not None and real_row[2]) or (acct in owners_conf))
            if is_conf:
                snapshots_out[acct] = ("", True, acq_tok)
                continue

            own_row = owners_primary.get(acct)
            if own_row is not None:
                _, p_name, p_aka = own_row
                # Prefer mailto when mailto includes co-owner "&" on same surname and p_name only had line 1,
                # otherwise prefer owners.txt name + DBA aka when mailto is blank or identical.
                clean_primary = sanitize_wire_owner_name(p_name, p_aka) if p_aka else _cached_clean_name(p_name)
                if not mailto_raw or (not p_aka and mailto_raw == p_name):
                    chosen = clean_primary
                else:
                    clean_mailto = sanitize_wire_owner_name(mailto_raw, p_aka if not p_name else "") if (p_aka and not p_name) else _cached_clean_name(mailto_raw)
                    if clean_mailto and " & " in clean_mailto and " & " not in clean_primary and "(DBA " not in clean_primary:
                        chosen = clean_mailto
                    else:
                        chosen = clean_primary or clean_mailto
            else:
                chosen = _cached_clean_name(mailto_raw)

            if chosen or acq_tok:
                snapshots_out[acct] = (chosen, False, acq_tok)

        acct_real.clear()
        owners_primary.clear()
        owners_conf.clear()
        all_seen_accts.clear()
        name_cache.clear()

        # Stream deeds.txt for target accounts (deduplicated per account)
        deeds_sets: dict[str, set[tuple[str, str]]] = defaultdict(set)
        deeds_member = _find_zip_member_case_insensitive(zf, "deeds.txt")
        if deeds_member is not None:
            with zf.open(deeds_member, "r") as raw_f:
                tf = io.TextIOWrapper(raw_f, encoding="latin-1", errors="replace")
                hdr_line = tf.readline()
                if hdr_line:
                    cols = {c.strip().lower(): idx for idx, c in enumerate(hdr_line.rstrip("\r\n").split("\t"))}
                    i_acct = cols.get("acct", 0)
                    i_dos = cols.get("dos", 1)
                    i_cyr = cols.get("clerk_yr", 2)
                    i_cid = cols.get("clerk_id", 3)
                    max_need = max(i_acct, i_dos, i_cyr, i_cid)
                    eff_max_yr = max(yr, active_tax_year)
                    dos_cache: dict[tuple[str, str], str] = {}
                    tok_cache: dict[tuple[str, str], str] = {}
                    for line in tf:
                        matched = _split_filtered_roll_row(line, i_acct, max_need, target_hcads)
                        if matched is None:
                            continue
                        acct, parts = matched
                        raw_dos = parts[i_dos].strip() if len(parts) > i_dos else ""
                        raw_cyr = parts[i_cyr].strip() if len(parts) > i_cyr else ""
                        raw_cid = parts[i_cid].strip() if len(parts) > i_cid else ""
                        dos_key = (raw_dos, raw_cyr)
                        dt8 = dos_cache.get(dos_key)
                        if dt8 is None:
                            if len(dos_cache) > 100_000:
                                dos_cache.clear()
                            dt8 = parse_dos_yyyymmdd(raw_dos, raw_cyr, max_year=eff_max_yr)
                            dos_cache[dos_key] = dt8
                        tok_key = (raw_cyr, raw_cid)
                        tok = tok_cache.get(tok_key)
                        if tok is None:
                            if len(tok_cache) > 250_000:
                                tok_cache.clear()
                            tok = normalize_hcad_clerk_instrument(raw_cyr, raw_cid, max_year=eff_max_yr)
                            tok_cache[tok_key] = tok
                        if tok or (dt8 and dt8 != "00000000"):
                            deeds_sets[acct].add((dt8, tok or ""))

    deeds_out = {acct: list(pairs) for acct, pairs in deeds_sets.items()}
    deeds_sets.clear()
    return yr, total_parsed, snapshots_out, deeds_out


# Process-pool globals populated via copy-on-write fork on Linux
_WORKER_TARGET_HCADS: set[str] | None = None
_WORKER_MIN_PARSED: int = MIN_PARSED_ACCOUNTS_PER_ROLL
_WORKER_ACTIVE_TAX_YEAR: int = ACTIVE_TAX_YEAR


def _worker_parse_roll(task: tuple[int, str]) -> tuple[int, int, dict[str, tuple[str, bool, str]], dict[str, list[tuple[str, str]]]]:
    yr, zpath_s = task
    return parse_single_roll_snapshot(
        Path(zpath_s),
        yr,
        _WORKER_TARGET_HCADS,
        min_parsed_accounts=_WORKER_MIN_PARSED,
        active_tax_year=_WORKER_ACTIVE_TAX_YEAR,
    )


def collect_target_atlas_hcads(
    *,
    data_dir: Path = DATA_DIR,
    permits_dir: Path = PERMITS_DIR,
    cache_dir: Path = CACHE_DIR,
) -> tuple[set[str], list[str]]:
    """Collect all standing, mapped, curated, and watchlist 13-digit HCAD accounts + shard prefixes."""
    ddir = Path(data_dir)
    pdir = Path(permits_dir)
    cdir = Path(cache_dir)

    hcads = collect_standing_and_referenced_atlas_hcads(ddir, cdir)
    permit_prefixes: set[str] = set()
    permit_only_hcads: set[str] = set()

    if pdir.exists():
        for pfile in sorted(pdir.glob("permits_*.json")):
            m = re.fullmatch(r"permits_(\d{3})\.json", pfile.name)
            if m:
                permit_prefixes.add(m.group(1))
            if not hcads:
                doc = json.loads(pfile.read_text(encoding="utf-8"))
                if isinstance(doc, dict):
                    for k in doc:
                        nk = normalize_standing_hcad_token(k)
                        if nk:
                            permit_only_hcads.add(nk)

    if not hcads and permit_only_hcads:
        hcads = permit_only_hcads

    if not permit_prefixes:
        permit_prefixes = {h[:3] for h in hcads if len(h) >= 3}
    else:
        permit_prefixes |= {h[:3] for h in hcads if len(h) >= 3}

    return hcads, sorted(permit_prefixes)


def _consolidate_and_compress_prefix_shard(
    args: tuple[str, dict[str, list[tuple[int, str, bool, str]]], dict[str, list[tuple[str, str]]], int],
) -> tuple[str, bytes, int, int, int, int]:
    """Worker helper to consolidate all accounts in one 3-digit prefix and return gzipped shard bytes + telemetry."""
    prefix3, snaps_by_acct, deeds_by_acct, active_tax_year = args
    shard_map: dict[str, str] = {}
    multi_tenure_count = 0
    pre2005_tenure_count = 0
    paired_deed_count = 0

    for acct in sorted(snaps_by_acct.keys()):
        raw_snaps = snaps_by_acct[acct]
        snaps = [
            RollOwnerSnapshot(year=yr, owner_name=nm, is_confidential=is_c, acq_dt=acq)
            for yr, nm, is_c, acq in raw_snaps
        ]
        raw_deeds = deeds_by_acct.get(acct) or ()

        tenures = consolidate_account_tenures(
            snaps,
            deeds=raw_deeds,
            active_tax_year=active_tax_year,
        )
        if not tenures:
            continue
        wire = encode_compact_owner_history(tenures)
        if not wire:
            continue
        shard_map[acct] = wire
        if len(tenures) >= 2:
            multi_tenure_count += 1
        oldest = tenures[-1]
        if oldest.acq_dt == HCAD_MAINFRAME_SENTINEL or (
            len(oldest.acq_dt) >= 4 and oldest.acq_dt[:4].isdigit() and int(oldest.acq_dt[:4]) < OWNER_HISTORY_START_YEAR
        ):
            pre2005_tenure_count += 1
        paired_deed_count += sum(1 for t in tenures if t.deed_token)

    gz_bytes = compress_deterministic_gzip_json(shard_map)
    return (
        prefix3,
        gz_bytes,
        len(shard_map),
        multi_tenure_count,
        pre2005_tenure_count,
        paired_deed_count,
    )


def build_owner_history_shards(
    *,
    roll_zips_by_year: dict[int, Path],
    data_dir: Path = DATA_DIR,
    permits_dir: Path = PERMITS_DIR,
    cache_dir: Path = CACHE_DIR,
    app_dir: Path | None = APP_DIR,
    target_hcads: set[str] | None = None,
    shard_prefixes: list[str] | None = None,
    active_tax_year: int = ACTIVE_TAX_YEAR,
    min_parsed_accounts: int = MIN_PARSED_ACCOUNTS_PER_ROLL,
    max_workers: int | None = None,
    update_manifest: bool = True,
) -> dict[str, Any]:
    """Build deterministic `owner_history/owners_<prefix3>.json.gz` shards across `roll_zips_by_year` (`DF-3`, `DF-7`)."""
    if not roll_zips_by_year:
        raise ValueError("roll_zips_by_year must contain at least one annual HCAD archive.")

    ddir = Path(data_dir)
    pdir = Path(permits_dir)
    cdir = Path(cache_dir)
    out_dir = ddir / "owner_history"
    out_dir.mkdir(parents=True, exist_ok=True)

    if target_hcads is None or shard_prefixes is None:
        auto_hcads, auto_prefixes = collect_target_atlas_hcads(
            data_dir=ddir,
            permits_dir=pdir,
            cache_dir=cdir,
        )
        eff_hcads = auto_hcads if target_hcads is None else set(target_hcads)
        eff_prefixes = auto_prefixes if shard_prefixes is None else sorted(set(shard_prefixes))
    else:
        eff_hcads = set(target_hcads)
        eff_prefixes = sorted(set(shard_prefixes))

    years_sorted = sorted(int(y) for y in roll_zips_by_year.keys())
    tasks = [(yr, str(roll_zips_by_year[yr])) for yr in years_sorted]

    # Fold per-year results immediately into prefix-partitioned dicts with interned strings & deduplicated deeds
    snaps_by_prefix: dict[str, dict[str, list[tuple[int, str, bool, str]]]] = {
        pfx: defaultdict(list) for pfx in eff_prefixes
    }
    deeds_by_prefix: dict[str, dict[str, set[tuple[str, str]]]] = {
        pfx: defaultdict(set) for pfx in eff_prefixes
    }
    parsed_counts_by_year: dict[int, int] = {}

    def _fold_roll_into_prefixes(
        yr_val: int,
        snaps_map: dict[str, tuple[str, bool, str]],
        deeds_map: dict[str, list[tuple[str, str]]],
    ) -> None:
        intern = sys.intern
        for acct, (nm, is_c, acq) in snaps_map.items():
            acct_i = intern(acct)
            pfx = acct_i[:3]
            if pfx not in snaps_by_prefix:
                snaps_by_prefix[pfx] = defaultdict(list)
                deeds_by_prefix[pfx] = defaultdict(set)
            snaps_by_prefix[pfx][acct_i].append((yr_val, intern(nm), bool(is_c), intern(acq)))
        snaps_map.clear()

        for acct, d_list in deeds_map.items():
            acct_i = intern(acct)
            pfx = acct_i[:3]
            if pfx not in deeds_by_prefix:
                snaps_by_prefix[pfx] = defaultdict(list)
                deeds_by_prefix[pfx] = defaultdict(set)
            target_set = deeds_by_prefix[pfx][acct_i]
            for dt_s, tok_s in d_list:
                target_set.add((intern(dt_s), intern(tok_s)))
        deeds_map.clear()

    workers = max_workers if max_workers is not None else min(3, len(tasks), os.cpu_count() or 4)
    global _WORKER_TARGET_HCADS, _WORKER_MIN_PARSED, _WORKER_ACTIVE_TAX_YEAR
    _WORKER_TARGET_HCADS = {sys.intern(h) for h in eff_hcads}
    _WORKER_MIN_PARSED = int(min_parsed_accounts)
    _WORKER_ACTIVE_TAX_YEAR = int(active_tax_year)

    use_fork = (
        workers > 1
        and len(tasks) > 1
        and threading.active_count() == 1
        and "fork" in multiprocessing.get_all_start_methods()
    )
    done_rolls = 0
    if use_fork:
        mp_ctx = multiprocessing.get_context("fork")
        with ProcessPoolExecutor(max_workers=workers, mp_context=mp_ctx) as pool:
            task_iter = iter(tasks)
            in_flight = set()
            for _ in range(workers):
                nxt = next(task_iter, None)
                if nxt is not None:
                    in_flight.add(pool.submit(_worker_parse_roll, nxt))

            while in_flight:
                done_futs, in_flight = wait(in_flight, return_when=FIRST_COMPLETED)
                for fut in done_futs:
                    yr, total_parsed, snaps_map, deeds_map = fut.result()
                    parsed_counts_by_year[yr] = total_parsed
                    _fold_roll_into_prefixes(yr, snaps_map, deeds_map)
                    del snaps_map, deeds_map
                    done_rolls += 1
                    if len(tasks) >= 5:
                        print(
                            f"   [{done_rolls}/{len(tasks)}] Folded {yr} roll ({total_parsed:,} accounts)...",
                            flush=True,
                        )
                    nxt = next(task_iter, None)
                    if nxt is not None:
                        in_flight.add(pool.submit(_worker_parse_roll, nxt))
    else:
        for yr, zpath_s in tasks:
            yr_out, total_parsed, snaps_map, deeds_map = parse_single_roll_snapshot(
                Path(zpath_s),
                yr,
                _WORKER_TARGET_HCADS,
                min_parsed_accounts=min_parsed_accounts,
                active_tax_year=active_tax_year,
            )
            parsed_counts_by_year[yr_out] = total_parsed
            _fold_roll_into_prefixes(yr_out, snaps_map, deeds_map)
            del snaps_map, deeds_map
            done_rolls += 1
            if len(tasks) >= 5:
                print(
                    f"   [{done_rolls}/{len(tasks)}] Folded {yr_out} roll ({total_parsed:,} accounts)...",
                    flush=True,
                )

    _WORKER_TARGET_HCADS = None
    eff_hcads.clear()
    gc.collect()

    all_prefixes = sorted(snaps_by_prefix.keys())
    written_prefixes: list[str] = []
    total_accounts_written = 0
    total_multi_tenure = 0
    total_pre2005_tenure = 0
    total_paired_deeds = 0
    total_gz_bytes = 0
    expected_filenames: set[str] = set()

    # Consolidate and gzip prefixes in bounded pop-chunks so peak RAM decreases monotonically (zero fork-pickle surge)
    chunk_size = 16
    thread_workers = min(4, max(1, workers))
    for start_idx in range(0, len(all_prefixes), chunk_size):
        batch_pfxs = all_prefixes[start_idx : start_idx + chunk_size]
        batch_tasks = []
        for pfx in batch_pfxs:
            p_snaps = dict(snaps_by_prefix.pop(pfx, {}))
            p_deeds_sets = deeds_by_prefix.pop(pfx, {})
            p_deeds = {acct_k: list(dset) for acct_k, dset in p_deeds_sets.items()}
            p_deeds_sets.clear()
            batch_tasks.append((pfx, p_snaps, p_deeds, int(active_tax_year)))

        if thread_workers > 1 and len(batch_tasks) > 1:
            with ThreadPoolExecutor(max_workers=thread_workers) as tpool:
                batch_results = list(tpool.map(_consolidate_and_compress_prefix_shard, batch_tasks))
        else:
            batch_results = [_consolidate_and_compress_prefix_shard(st) for st in batch_tasks]

        batch_tasks.clear()
        for pfx, gz_bytes, acct_cnt, multi_cnt, pre2005_cnt, deed_cnt in batch_results:
            if acct_cnt == 0:
                continue
            if len(gz_bytes) >= MAX_SINGLE_FILE_BYTES:
                raise ValueError(
                    f"Shard owners_{pfx}.json.gz ({len(gz_bytes):,} bytes) exceeds single-file cap."
                )
            fname = f"owners_{pfx}.json.gz"
            expected_filenames.add(fname)
            written_prefixes.append(pfx)
            dst = out_dir / fname
            tmp = out_dir / f".{fname}.tmp.{os.getpid()}.{threading.get_ident()}"
            try:
                tmp.write_bytes(gz_bytes)
                tmp.replace(dst)
            finally:
                tmp.unlink(missing_ok=True)

            total_accounts_written += acct_cnt
            total_multi_tenure += multi_cnt
            total_pre2005_tenure += pre2005_cnt
            total_paired_deeds += deed_cnt
            total_gz_bytes += len(gz_bytes)

        del batch_results
        gc.collect()

    # Prune stale owners_*.json.gz shards (`DF-3`)
    pruned_stale = 0
    for existing_gz in sorted(out_dir.glob("owners_*.json.gz")):
        if existing_gz.name not in expected_filenames:
            existing_gz.unlink(missing_ok=True)
            pruned_stale += 1

    written_prefixes.sort()
    span = [years_sorted[0], years_sorted[-1]]

    manifest_path = ddir / "pmtiles_manifest.json"
    if update_manifest and manifest_path.exists():
        update_pmtiles_manifest_v3(
            manifest_path,
            data_tax_year=int(active_tax_year),
            owner_history_prefixes=written_prefixes,
            owner_history_span=span,
            owner_history_accounts=total_accounts_written,
        )

    projected_app_bytes = 0
    if app_dir is not None and Path(app_dir).exists():
        for fp in Path(app_dir).rglob("*"):
            if fp.is_file() and "__pycache__" not in fp.parts and not fp.name.startswith("."):
                projected_app_bytes += fp.stat().st_size
        if projected_app_bytes >= HARD_MAX_APP_BYTES:
            raise RuntimeError(
                f"Projected app/ bundle size ({projected_app_bytes / (1024 * 1024):.2f} MiB) "
                f"exceeds HARD_MAX_APP_BYTES ({HARD_MAX_APP_BYTES / (1024 * 1024):.2f} MiB)."
            )

    return {
        "years": years_sorted,
        "owner_history_span": span,
        "shard_count": len(written_prefixes),
        "owner_history_prefixes": written_prefixes,
        "owner_history_accounts": total_accounts_written,
        "multi_tenure_accounts": total_multi_tenure,
        "pre2005_tenure_accounts": total_pre2005_tenure,
        "paired_deed_tokens": total_paired_deeds,
        "owner_history_gzip_bytes": total_gz_bytes,
        "owner_history_gzip_mib": round(total_gz_bytes / (1024 * 1024), 2),
        "pruned_stale_shards": pruned_stale,
        "parsed_counts_by_year": dict(sorted(parsed_counts_by_year.items())),
        "projected_app_bytes": projected_app_bytes,
        "projected_app_mib": round(projected_app_bytes / (1024 * 1024), 2) if projected_app_bytes else 0.0,
    }


def maybe_rebuild_owner_history_for_staged_roll(
    *,
    cache_dir: Path,
    staged_roll_zip: Path,
    tax_year: int,
    staged_data_dir: Path,
    min_historical_rolls: int = 2,
    min_parsed_accounts: int = MIN_PARSED_ACCOUNTS_PER_ROLL,
) -> dict[str, Any]:
    """Optional annual-refresh hook (`REG-5`): rebuilds `owner_history/*.json.gz` in `staged_data_dir` when historical rolls exist."""
    roll_zips = resolve_annual_roll_zip_paths(
        cache_dir,
        start_year=OWNER_HISTORY_START_YEAR,
        end_year=max(ACTIVE_TAX_YEAR, int(tax_year)),
    )
    if Path(staged_roll_zip).exists():
        roll_zips[int(tax_year)] = Path(staged_roll_zip)
    roll_zips = {yr: zp for yr, zp in sorted(roll_zips.items()) if yr <= int(tax_year)}
    if len(roll_zips) < int(min_historical_rolls):
        return {"skipped": True, "reason": "historical_roll_archives_absent", "available_rolls": sorted(roll_zips.keys())}

    return build_owner_history_shards(
        roll_zips_by_year=roll_zips,
        data_dir=staged_data_dir,
        permits_dir=Path(staged_data_dir) / "permits",
        cache_dir=cache_dir,
        app_dir=None,
        active_tax_year=int(tax_year),
        min_parsed_accounts=int(min_parsed_accounts),
        update_manifest=True,
    )


def main(argv: list[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(
        description="Build 22-year (2005-2026) HCAD certified-roll ownership history shards."
    )
    parser.add_argument("--start-year", type=int, default=OWNER_HISTORY_START_YEAR)
    parser.add_argument("--end-year", type=int, default=ACTIVE_TAX_YEAR)
    parser.add_argument("--no-download", action="store_true")
    parser.add_argument("--workers", type=int, default=None)
    args = parser.parse_args(argv)

    t0 = time.monotonic()
    print(
        f"1. Verifying HCAD Real_acct_owner.zip archives ({args.start_year}-{args.end_year})...",
        flush=True,
    )
    roll_zips = ensure_annual_real_acct_archives(
        cache_dir=CACHE_DIR,
        start_year=args.start_year,
        end_year=args.end_year,
        allow_download=not args.no_download,
    )
    print(f"   Verified {len(roll_zips)} annual roll archives.", flush=True)

    print("2. Streaming 22-year HCAD ownership chains, consolidating tenures, and compressing shards...", flush=True)
    stats = build_owner_history_shards(
        roll_zips_by_year=roll_zips,
        data_dir=DATA_DIR,
        permits_dir=PERMITS_DIR,
        cache_dir=CACHE_DIR,
        app_dir=APP_DIR,
        active_tax_year=args.end_year,
        max_workers=args.workers,
        update_manifest=True,
    )
    elapsed = time.monotonic() - t0
    print(
        f"   Completed in {elapsed:.1f}s:\n"
        f"   - Shards written         : {stats['shard_count']} (span {stats['owner_history_span'][0]}-{stats['owner_history_span'][1]})\n"
        f"   - Accounts indexed       : {stats['owner_history_accounts']:,}\n"
        f"   - Multi-owner (2005+)    : {stats['multi_tenure_accounts']:,}\n"
        f"   - Pre-2005 tenures       : {stats['pre2005_tenure_accounts']:,}\n"
        f"   - Paired Clerk deeds     : {stats['paired_deed_tokens']:,}\n"
        f"   - Total owner_history/   : {stats['owner_history_gzip_mib']:.2f} MiB ({stats['owner_history_gzip_bytes']:,} bytes)\n"
        f"   - Total app/ bundle      : {stats['projected_app_mib']:.2f} MiB (< 980.00 MiB ceiling)",
        flush=True,
    )
    return stats


if __name__ == "__main__":
    main()
