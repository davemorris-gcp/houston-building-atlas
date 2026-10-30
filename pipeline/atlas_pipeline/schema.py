"""Normalized feature schema and HCAD/COH attribute mapping for Houston Building Atlas v2."""

from __future__ import annotations

import datetime
import json
import math
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

MIN_VALID_YEAR = 1836
MAX_VALID_YEAR = 2027
MAX_VALID_CAMA_DATE_YEAR = 2026
MAX_STANDING_OVERRIDE_YEAR = 2035
ACTIVE_CACHE_BUST = "20261030d"
ORD_2015_639_CUTOFF_ISO = "2015-07-24"
ORD_2015_639_CUTOFF_ORD_NUM = 639
QUAD_NAMES: tuple[str, ...] = ("nw_w", "nw_e", "ne", "sw", "se")

PLAT_CARRY_FORWARD_REVIEW_FIELDS: tuple[str, ...] = (
    "hcad",
    "address",
    "yearBuilt",
    "designation",
    "tier",
    "threatCategory",
    "why",
    "sources",
    "followup",
)
REMOVED_TIER3_TOMBSTONE = "Removed (Tier 3)"
REMOVED_WATCHLIST_TOMBSTONE = "Removed from Watchlist"
WATCHLIST_TOMBSTONE_STATUSES: frozenset[str] = frozenset({
    REMOVED_TIER3_TOMBSTONE,
    REMOVED_WATCHLIST_TOMBSTONE,
})
WATCHLIST_TRACKER_STATUSES: tuple[str, ...] = (
    "Active / High Priority",
    "Dormant / Medium Priority",
    "For Sale",
    "Monitoring",
    "Saved",
    "Lost",
    "Archived (Tier 3)",
)

DEMOLISHED_VACANT_ISSUE = "Building demolished - lot currently vacant"
DEMOLISHED_REPLACED_ISSUE = "Building demolished & replaced by newer structure"
DEMOLISHED_VACANT_CITATION_TAG = "[DEMOLISHED / VACANT LOT - HIDE FOOTPRINT]"

_EXPLICIT_VACANT_YEAR_TOKENS: frozenset[str] = frozenset({
    "0",
    "vacant",
    "vacant lot",
    "demolished",
    "none",
    "n/a",
})
_RE_DEMOLISHED_VACANT_TEXT = re.compile(
    r"\[demolished\s*/\s*vacant\s+lot"
    r"|demolished\s*[-:/]?\s*lot\s+currently\s+vacant"
    r"|vacant\s+lot\s*\(hide\s+footprint\)"
    r"|\bsuppress_only\b",
    re.IGNORECASE,
)
_RE_LEGACY_DEMOLISHED_ISSUE = re.compile(
    r"building\s+demolished\s+or\s+replaced",
    re.IGNORECASE,
)
_RE_VACANT_LOT_CUE = re.compile(
    r"\bvacant\b|\bcleared\b|\bempty\s+lot\b|\bno\s+replacement\b",
    re.IGNORECASE,
)


def is_demolished_vacant_marker(
    year_raw: Any = None,
    *,
    footprint_issue: str = "",
    footprint_notes: str = "",
    source_type: str = "",
    source_citation: str = "",
    suppress_only: bool | None = None,
) -> bool:
    """Return True when an override/submission marks a demolished structure on a currently vacant lot.

    Supports both entry paths requested by curators:
      1. Explicit `0` or `"Vacant"` / `"Demolished"` in `year_built` / `suggested_year_built`
         (unless `suppress_only=False` is explicitly asserted on a non-vacant payload).
      2. Selecting `"Building demolished - lot currently vacant"` (or `[DEMOLISHED / VACANT LOT]`
         in `source_type` / `source_citation`, or legacy `"Building demolished or replaced"` with
         blank/`0` year and `"vacant"` in notes/citation), even when Google Apps Script `doPost`
         normalizes `0` to `""` in the sheet's `year_built` column.

    Never returns True when a valid replacement construction year (`MIN_VALID_YEAR..MAX_STANDING_OVERRIDE_YEAR`)
    is supplied unless `suppress_only=True` is explicitly set.
    """
    if suppress_only is True:
        return True

    raw_str = "" if year_raw is None else str(year_raw).strip()
    low_yr = raw_str.lower()
    if low_yr in _EXPLICIT_VACANT_YEAR_TOKENS:
        if suppress_only is False and low_yr == "0":
            return False
        return True

    parsed_yr = 0
    if raw_str:
        try:
            fval = float(raw_str)
            if not math.isfinite(fval):
                return False
            parsed_yr = int(round(fval))
        except (ValueError, TypeError):
            return False

    if MIN_VALID_YEAR <= parsed_yr <= MAX_STANDING_OVERRIDE_YEAR:
        return False
    if parsed_yr != 0:
        return False

    combined_meta = " | ".join(
        s for s in (footprint_issue, source_type, source_citation) if s
    )
    if _RE_DEMOLISHED_VACANT_TEXT.search(combined_meta):
        return True

    issue_and_src = f"{footprint_issue} | {source_type}"
    notes_and_cite = f"{footprint_notes} | {source_citation}"
    if _RE_LEGACY_DEMOLISHED_ISSUE.search(issue_and_src) and _RE_VACANT_LOT_CUE.search(notes_and_cite):
        return True

    return False

INFERRED_FALLBACK_YEAR_SOURCES: frozenset[str] = frozenset({
    "hcad_permit",
    "campus_contiguity",
    "adjacent_owner",
    "hcad_extra_feature",
    "hcad_tieback",
    "hcad_deed",
    "subdivision_median",
    "blockface_median",
})

AUTHORITATIVE_YEAR_SOURCES: frozenset[str] = frozenset({
    "",
    "hcad_cama",
    "curated_landmark",
    "good_brick_award",
    "archival",
})

VALID_CONTRIBUTING_VALUES: frozenset[str] = frozenset({
    "Contributing",
    "Non-Contributing",
    "Outside Historic District",
    "Tax-Exempt Campus Structure",
})

_RE_MDY_DATE = re.compile(
    r"^(\d{1,2})/(\d{1,2})/(\d{4})(?:[\sT].*)?$"
)
_RE_YMD_DATE = re.compile(
    r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:[\sT].*)?$"
)


def is_hcad_null_date_sentinel(year: int, month: int, day: int) -> bool:
    """Return True when (year, month, day) is an HCAD Microsoft COM/OLE/SQL null-date sentinel.

    HCAD CAMA tabular exports (permits.txt, deeds.txt, real_acct.txt splt_dt/new_own_dt)
    serialize null Microsoft COM/OLE Automation VT_DATE / Access / Excel serial-0 dates as
    12/30/1899 (or 12/31/1899), and SQL Server DATETIME / Lotus-Excel epoch defaults as
    01/01/1900 or 01/02/1900. Genuine non-sentinel 1900 calendar dates (e.g. 06/15/1900)
    are preserved.
    """
    if month <= 0 or day <= 0:
        return True
    if year == 1899:
        return True
    if year == 1900 and month == 1 and day in (1, 2):
        return True
    return False


def normalize_hcad_date(
    date_str: Any,
    fallback_yr_str: Any = None,
    *,
    allow_fallback: bool = True,
    min_year: int = MIN_VALID_YEAR,
    max_year: int = MAX_VALID_CAMA_DATE_YEAR,
) -> tuple[str | None, int]:
    """Parse an HCAD date string into (iso_yyyy_mm_dd_or_none, valid_year_or_0).

    Rejects SQL/OLE null-date sentinels (year 1899, 01/01/1900, 01/02/1900, 00/00/0000,
    invalid calendar dates, and years outside [min_year, max_year]).

    Source-specific fallback rules:
      - deeds.txt passes allow_fallback=True with clerk_yr, which records the County Clerk
        instrument filing year when dos is a 12/30/1899 null sentinel.
      - permits.txt passes allow_fallback=False because permits.txt yr stores the HCAD
        appraisal tax-roll cycle year (e.g. 2008 or 2025) rather than permit issuance year.
    """
    s = "" if date_str is None else str(date_str).strip()
    if s and s not in ("0", "00/00/0000", "0000-00-00"):
        y_int = m_int = d_int = 0
        matched = False
        m_mdy = _RE_MDY_DATE.match(s)
        if m_mdy:
            m_int, d_int, y_int = int(m_mdy.group(1)), int(m_mdy.group(2)), int(m_mdy.group(3))
            matched = True
        else:
            m_ymd = _RE_YMD_DATE.match(s)
            if m_ymd:
                y_int, m_int, d_int = int(m_ymd.group(1)), int(m_ymd.group(2)), int(m_ymd.group(3))
                matched = True

        if matched and not is_hcad_null_date_sentinel(y_int, m_int, d_int):
            if min_year <= y_int <= max_year:
                try:
                    dt_obj = datetime.date(y_int, m_int, d_int)
                    return dt_obj.isoformat(), y_int
                except ValueError:
                    pass

    if allow_fallback and fallback_yr_str is not None:
        fb_yr = normalize_year(fallback_yr_str)
        if fb_yr != 1899 and min_year <= fb_yr <= max_year:
            return None, fb_yr

    return None, 0


def parse_hcad_date_year(
    date_str: Any,
    fallback_yr_str: Any = None,
    *,
    allow_fallback: bool = True,
    min_year: int = MIN_VALID_YEAR,
    max_year: int = MAX_VALID_CAMA_DATE_YEAR,
) -> int:
    """Return the validated integer year in [min_year, max_year] for an HCAD date field, or 0."""
    _, yr = normalize_hcad_date(
        date_str,
        fallback_yr_str,
        allow_fallback=allow_fallback,
        min_year=min_year,
        max_year=max_year,
    )
    return yr


def _sanitize_payload_strings(value: Any) -> Any:
    """Recursively strip U+2014/U+2013 dashes across JSON strings while preserving schema keys."""
    if isinstance(value, str):
        if "\u2014" in value or "\u2013" in value:
            return sanitize_atlas_text(value)
        return value
    if isinstance(value, list):
        return [_sanitize_payload_strings(item) for item in value]
    if isinstance(value, dict):
        return {k: _sanitize_payload_strings(v) for k, v in value.items()}
    return value


def write_compact_json(path: Path | str, payload: Any) -> None:
    """Serialize payload as single-line compact UTF-8 JSON to keep data bundles within budget."""
    target = Path(path)
    tmp_path = target.with_name(f"{target.name}.tmp")
    clean_payload = _sanitize_payload_strings(payload)
    serialized = json.dumps(clean_payload, ensure_ascii=False, separators=(",", ":"))
    tmp_path.write_text(serialized, encoding="utf-8")
    tmp_path.replace(target)


def sanitize_atlas_text(val: str) -> str:
    """Normalize typography so public JSON artifacts contain zero U+2014/U+2013 dashes."""
    return (
        val.replace(" \u2014 ", " - ")
        .replace("\u2014", "-")
        .replace(" \u2013 ", " - ")
        .replace("\u2013", "-")
    )


_RE_ORDINANCE_YEAR_SEQ = re.compile(r"\b(19\d{2}|20\d{2})\s*[-/]\s*0*(\d+)\b")
_RE_YEAR_ONLY = re.compile(r"\b(19\d{2}|20\d{2}|21\d{2})\b")
_INACTIVE_CH42_STATUSES: frozenset[str] = frozenset({
    "pending",
    "in review",
    "applied",
    "draft",
    "withdrawn",
    "denied",
    "rescinded",
})


def _add_years_iso(iso_date: str, years: int) -> str:
    m = _RE_YMD_DATE.match(iso_date)
    if not m:
        return ""
    y_int, m_int, d_int = int(m.group(1)), int(m.group(2)), int(m.group(3))
    target_yr = y_int + years
    try:
        return datetime.date(target_yr, m_int, d_int).isoformat()
    except ValueError:
        if m_int == 2 and d_int == 29:
            return datetime.date(target_yr, 2, 28).isoformat()
        return str(target_yr)


def _extract_iso_or_year(raw_val: Any) -> tuple[str, int]:
    s = "" if raw_val is None else str(raw_val).strip()
    if not s or s.lower() in ("null", "none", "nan", "<null>", "n/a", "permanent"):
        return "", 0
    m_ymd = _RE_YMD_DATE.match(s)
    if m_ymd:
        y_int, m_int, d_int = int(m_ymd.group(1)), int(m_ymd.group(2)), int(m_ymd.group(3))
        if not is_hcad_null_date_sentinel(y_int, m_int, d_int) and 1980 <= y_int <= 2120:
            try:
                return datetime.date(y_int, m_int, d_int).isoformat(), y_int
            except ValueError:
                pass
    m_mdy = _RE_MDY_DATE.match(s)
    if m_mdy:
        m_int, d_int, y_int = int(m_mdy.group(1)), int(m_mdy.group(2)), int(m_mdy.group(3))
        if not is_hcad_null_date_sentinel(y_int, m_int, d_int) and 1980 <= y_int <= 2120:
            try:
                return datetime.date(y_int, m_int, d_int).isoformat(), y_int
            except ValueError:
                pass
    m_yr = _RE_YEAR_ONLY.search(s)
    if m_yr:
        yr = int(m_yr.group(1))
        if 1980 <= yr <= 2120:
            return str(yr), yr
    return "", 0


def resolve_ch42_ordinance_lifecycle(
    *,
    ordinance: Any = "",
    effective_date: Any = "",
    expiration_date: Any = "",
    status: Any = "Approved",
    protection_type: str = "smls",
    ref_year: int = MAX_VALID_CAMA_DATE_YEAR,
    renewal_window_years: int = 5,
) -> dict[str, Any]:
    """Resolve COH Chapter 42 ordinance effective/sunset lifecycle metadata (DF-1).

    Under City of Houston Code of Ordinances Sec. 42-207(a) (SMLS) and Sec. 42-178(a) (SMBL),
    ordinances enacted prior to Ord. No. 2015-639 (effective July 24, 2015) carried a 20-year
    term, whereas ordinances effective on or after July 24, 2015 carry a 40-year term.
    Because pre-2015 COH SMBL and selected SMLS GIS rows omit explicit Expiration_Date (and
    some SMBL rows also omit START_DATE, encoding enactment only in ORDINANCE_ like 2007-0123),
    this helper derives statutory sunset dates with explicit `is_estimated_sunset: True`
    provenance while leaving non-enacted `Pending` applications without fabricated sunsets.
    """
    ptype = str(protection_type or "").strip().lower()
    ord_str = sanitize_atlas_text(_clean_str(ordinance))
    status_str = sanitize_atlas_text(_clean_str(status)) or "Approved"
    status_low = status_str.lower()

    if ptype == "conservation":
        eff_norm, eff_yr = _extract_iso_or_year(effective_date)
        return {
            "effective_date": eff_norm,
            "effective_year": eff_yr,
            "expiration_date": "",
            "expiration_year": 0,
            "statutory_term_years": 0,
            "is_estimated_sunset": False,
            "sunset_status": "permanent",
            "expiration_label": "No statutory sunset (Ch. 33 Conservation District)",
        }

    eff_norm, eff_yr = _extract_iso_or_year(effective_date)
    exp_norm, exp_yr = _extract_iso_or_year(expiration_date)

    ord_yr = 0
    ord_seq = 0
    m_ord = _RE_ORDINANCE_YEAR_SEQ.search(ord_str)
    if m_ord:
        cand_oy = int(m_ord.group(1))
        if 1995 <= cand_oy <= 2050:
            ord_yr = cand_oy
            ord_seq = int(m_ord.group(2) or "0")

    base_yr = eff_yr or ord_yr
    term_years = 0
    if len(eff_norm) == 10:
        term_years = 20 if eff_norm < ORD_2015_639_CUTOFF_ISO else 40
    elif ord_yr > 0:
        if ord_yr < 2015:
            term_years = 20
        elif ord_yr > 2015:
            term_years = 40
        else:
            term_years = 20 if (0 < ord_seq < ORD_2015_639_CUTOFF_ORD_NUM) else 40
    elif eff_yr > 0:
        term_years = 20 if eff_yr < 2015 else 40

    is_pending = any(tok in status_low for tok in ("pend", "review", "applied", "draft"))
    is_rejected = any(tok in status_low for tok in ("denied", "withdraw", "rescind", "reject"))
    is_inactive = is_pending or is_rejected or status_low in _INACTIVE_CH42_STATUSES
    is_estimated = False
    resolved_exp_date = "" if (is_pending or is_rejected) else exp_norm
    resolved_exp_yr = 0 if (is_pending or is_rejected) else exp_yr

    if resolved_exp_yr > 0:
        if term_years == 0 and base_yr > 0:
            span = resolved_exp_yr - base_yr
            if 15 <= span <= 25:
                term_years = 20
            elif 35 <= span <= 45:
                term_years = 40
    elif not is_inactive and term_years > 0 and base_yr >= 1995:
        is_estimated = True
        resolved_exp_yr = base_yr + term_years
        if len(eff_norm) == 10:
            resolved_exp_date = _add_years_iso(eff_norm, term_years)
        else:
            resolved_exp_date = f"{resolved_exp_yr}-12-31"

    if is_pending:
        sunset_status = "pending"
    elif is_rejected:
        sunset_status = "inactive"
    elif status_low == "expired" or (resolved_exp_yr > 0 and resolved_exp_yr < ref_year):
        sunset_status = "expired"
    elif resolved_exp_yr > 0 and ref_year <= resolved_exp_yr <= ref_year + renewal_window_years:
        sunset_status = "expiring_soon"
    elif resolved_exp_yr > ref_year + renewal_window_years:
        sunset_status = "active"
    else:
        sunset_status = "unknown"

    if sunset_status == "pending":
        exp_label = "Pending application (not yet enacted)"
    elif sunset_status == "inactive":
        exp_label = status_str
    elif resolved_exp_yr > 0:
        if is_estimated:
            date_disp = resolved_exp_date if len(eff_norm) == 10 else str(resolved_exp_yr)
            exp_label = f"Est. {date_disp} ({term_years}-yr statutory term)"
        else:
            exp_label = resolved_exp_date
    else:
        exp_label = "Date unrecorded in COH GIS"

    return {
        "effective_date": "" if (is_pending or is_rejected) else eff_norm,
        "effective_year": 0 if (is_pending or is_rejected) else base_yr,
        "expiration_date": resolved_exp_date,
        "expiration_year": resolved_exp_yr,
        "statutory_term_years": 40 if is_pending else (0 if is_rejected else term_years),
        "is_estimated_sunset": is_estimated,
        "sunset_status": sunset_status,
        "expiration_label": exp_label,
    }


def is_boundary_search_entry(item: dict[str, Any]) -> bool:
    """Return True when a search_index.json entry represents a boundary/district/place overlay (DF-7)."""
    if not isinstance(item, dict):
        return False
    if item.get("type") == "district":
        return True
    if bool(item.get("is_neighborhood_entry")):
        return True
    if item.get("overlay_layer"):
        return True
    return False


def upsert_search_index_slice(
    index_list: list[dict[str, Any]],
    *,
    slice_predicate: Callable[[dict[str, Any]], bool],
    new_entries: list[dict[str, Any]],
    prepend: bool = True,
) -> list[dict[str, Any]]:
    """Idempotently replace only the targeted boundary slice in search_index.json (DF-7).

    Preserves all unrelated boundary families (historic districts, HOLC zones, potential
    districts, council districts, cultural districts, neighborhoods, subdivisions) and all
    building/property entries intact regardless of script execution order.
    """
    incoming_ids = {
        str(entry.get("id") or "").strip()
        for entry in new_entries
        if isinstance(entry, dict) and str(entry.get("id") or "").strip()
    }
    retained: list[dict[str, Any]] = []
    for item in index_list:
        if not isinstance(item, dict):
            continue
        if slice_predicate(item):
            continue
        item_id = str(item.get("id") or "").strip()
        if item_id and item_id in incoming_ids and is_boundary_search_entry(item):
            continue
        retained.append(item)
    return (list(new_entries) + retained) if prepend else (retained + list(new_entries))


def compile_quadrant_pmtiles(
    shard_dir: Path,
    output_dir: Path,
    quad_names: tuple[str, ...] = QUAD_NAMES,
) -> tuple[list[str], int]:
    """Compile quadrant .geojsonseq shards into Web Mercator PMTiles v3 archives in parallel via Tippecanoe."""
    tippecanoe_bin = shutil.which("tippecanoe") or "/tmp/tippecanoe/tippecanoe"
    if not Path(tippecanoe_bin).exists():
        tippecanoe_bin = str(Path.home() / ".local" / "bin" / "tippecanoe")

    print("-> Compiling 5 Web Mercator tile-aligned PMTiles v3 archives in parallel via Tippecanoe...")
    t_tip = time.time()
    procs: list[tuple[str, subprocess.Popen[bytes], Path]] = []
    for q in quad_names:
        in_seq = shard_dir / f"buildings_{q}.geojsonseq"
        out_pmtiles = output_dir / f"houston_buildings_{q}.pmtiles"
        cmd = [
            tippecanoe_bin,
            "-o",
            str(out_pmtiles),
            "--force",
            "-l",
            "buildings",
            "-Z",
            "10",
            "-z",
            "15",
            "--hilbert",
            "--read-parallel",
            "--drop-densest-as-needed",
            "--extend-zooms-if-still-dropping",
            "--maximum-tile-bytes=2500000",
            "--maximum-tile-features=400000",
            "--simplification=2",
            str(in_seq),
        ]
        procs.append((q, subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE), out_pmtiles))

    shard_files: list[str] = []
    total_pmtiles_bytes = 0
    for q, proc, out_pmtiles in procs:
        _, stderr = proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"Tippecanoe failed for quadrant {q}: {stderr.decode('utf-8', errors='replace')}"
            )
        sz = out_pmtiles.stat().st_size
        total_pmtiles_bytes += sz
        shard_files.append(out_pmtiles.name)
        print(f"   Compiled {out_pmtiles.name}: {sz / (1024 * 1024):.1f} MB")
    print(f"-> Tippecanoe compilation finished in {time.time() - t_tip:.1f}s.")
    return shard_files, total_pmtiles_bytes


def normalize_override_record(key: str, record: dict[str, Any]) -> dict[str, Any]:
    """Enforce integer decade, canonical IDs, clean typography, and valid contributing enum on an override record."""
    out = dict(record)
    for fld, fval in list(out.items()):
        if isinstance(fval, str) and "\u2014" in fval:
            out[fld] = sanitize_atlas_text(fval)
        elif isinstance(fval, list) and any(isinstance(x, str) and "\u2014" in x for x in fval):
            out[fld] = [sanitize_atlas_text(x) if isinstance(x, str) else x for x in fval]

    clean_key = str(key).strip()
    raw_prefix = clean_key.split("#")[0].strip()
    hcad = str(out.get("hcad_num") or (raw_prefix if raw_prefix.isdigit() else "")).strip()
    if hcad:
        out["hcad_num"] = hcad
    if not out.get("id"):
        out["id"] = clean_key
    if "#" in clean_key and not out.get("building_id"):
        out["building_id"] = clean_key

    if bool(out.get("suppress_only")):
        out["suppress_only"] = True
        out["year_built"] = 0
        out["decade"] = 0
        out.pop("geometry", None)
    else:
        yr = normalize_year(out.get("year_built"))
        if yr >= MIN_VALID_YEAR:
            out["year_built"] = yr
            out["decade"] = compute_decade(yr)
        elif "decade" in out:
            raw_dec = out.get("decade")
            if isinstance(raw_dec, str):
                dec_digits = re.sub(r"\D", "", raw_dec)
                out["decade"] = int(dec_digits) if len(dec_digits) == 4 else 0
            elif isinstance(raw_dec, (int, float)):
                out["decade"] = int(raw_dec)
            else:
                out["decade"] = 0

    contrib = str(out.get("contributing") or "").strip()
    if contrib == "NonContributing":
        contrib = "Non-Contributing"
    if contrib not in VALID_CONTRIBUTING_VALUES:
        out["contributing"] = "Outside Historic District"
    else:
        out["contributing"] = contrib

    return out


def normalize_year(raw_val: Any) -> int:
    """Parse a raw year-built value into an integer year in [1836, 2027], or 0 if invalid/unknown."""
    if raw_val is None:
        return 0
    if isinstance(raw_val, (int, float)):
        if math.isnan(raw_val):
            return 0
        yr = int(round(raw_val))
    else:
        s = str(raw_val).strip()
        if not s:
            return 0
        try:
            yr = int(round(float(s)))
        except ValueError:
            return 0

    if MIN_VALID_YEAR <= yr <= MAX_VALID_YEAR:
        return yr
    return 0


def compute_decade(year: int) -> int:
    """Return the decade floor (e.g. 1910 for 1914) or 0 if year <= 0."""
    if year < MIN_VALID_YEAR:
        return 0
    return (year // 10) * 10


def classify_use_category(state_class: str, landuse_desc: str, group_desc: str = "") -> str:
    """Map HCAD state class and COH land-use description into a high-level use category."""
    sc = (state_class or "").strip().upper()
    lu = f"{landuse_desc or ''} {group_desc or ''}".strip().lower()

    if sc.startswith("B") or "multi-family" in lu or "duplex" in lu or "two-family" in lu or "apartment" in lu or "condo" in lu:
        return "Multi-Family"
    if sc.startswith("A") or "single-family" in lu or "residential" in lu or "townhouse" in lu:
        return "Residential"
    if sc.startswith("X") or "exempt" in lu or "public" in lu or "institutional" in lu or "church" in lu or "school" in lu or "religious" in lu or "civic" in lu:
        return "Civic / Institutional"
    if sc.startswith("F2") or "industrial" in lu or "warehouse" in lu or "manufacturing" in lu:
        return "Industrial"
    if sc.startswith("F1") or "commercial" in lu or "office" in lu or "retail" in lu:
        return "Commercial"
    if sc.startswith("C") or sc.startswith("D") or "vacant" in lu or "undeveloped" in lu or "agricultural" in lu:
        return "Vacant / Exempt"
    return "Residential"


def estimate_stories_and_height(
    bld_area: float,
    footprint_area_sqft: float,
    use_category: str,
    osm_levels: Any = None,
    osm_height: Any = None,
) -> tuple[float, float]:
    """Estimate building stories and 3D extrusion height in meters."""
    stories = 0.0
    height_m = 0.0

    if osm_levels is not None:
        try:
            val = float(str(osm_levels).strip().split(";")[0])
            if 1.0 <= val <= 100.0:
                stories = val
        except ValueError:
            pass

    if osm_height is not None:
        try:
            raw_h = str(osm_height).strip().lower().replace("m", "").strip()
            val_h = float(raw_h)
            if 2.5 <= val_h <= 350.0:
                height_m = val_h
        except ValueError:
            pass

    if stories <= 0.0:
        if bld_area > 0 and footprint_area_sqft > 150:
            ratio = bld_area / footprint_area_sqft
            stories = max(1.0, min(round(ratio * 2) / 2, 60.0 if use_category in ("Commercial", "Multi-Family", "Civic / Institutional") else 3.5))
        else:
            if use_category == "Commercial":
                stories = 2.0
            elif use_category == "Multi-Family":
                stories = 2.5
            elif use_category == "Civic / Institutional":
                stories = 2.0
            elif use_category == "Industrial":
                stories = 1.5
            elif use_category == "Vacant / Exempt":
                stories = 1.0
            else:
                stories = 1.5 if bld_area > 2200 else 1.0

    if height_m <= 0.0:
        floor_h = 3.8 if use_category in ("Commercial", "Civic / Institutional", "Industrial") else 3.5
        height_m = round(stories * floor_h, 1)

    return round(stories, 1), round(height_m, 1)


def _clean_str(val: Any) -> str:
    if val is None:
        return ""
    s = str(val).strip()
    if s.lower() in ("null", "none", "nan", "<null>"):
        return ""
    return " ".join(s.split())


def _clean_float(val: Any) -> float:
    if val is None:
        return 0.0
    try:
        v = float(val)
        return 0.0 if math.isnan(v) or v < 0 else round(v, 1)
    except (ValueError, TypeError):
        return 0.0


def normalize_parcel_record(
    raw_props: dict[str, Any],
    historic_override: dict[str, Any] | None = None,
    landmark_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize a parcel attribute dict from COH Cadastral / HCAD + Historic layers into the 20-field schema."""
    hist = historic_override or {}
    lm = landmark_override or {}

    hcad_num = (
        _clean_str(raw_props.get("TAX_ID"))
        or _clean_str(raw_props.get("HCAD_NUM"))
        or _clean_str(hist.get("HCAD_NUM"))
        or _clean_str(lm.get("USER_HCAD_NUM"))
    )

    address = (
        _clean_str(raw_props.get("SITE_ADDR_1"))
        or _clean_str(raw_props.get("Site_addr_1"))
        or _clean_str(hist.get("Site_addr_1"))
        or _clean_str(lm.get("USER_SITE_ADDRESS"))
        or _clean_str(raw_props.get("LocAddr"))
    )
    if not address:
        str_num = _clean_str(raw_props.get("STR_NUM") or raw_props.get("Str_num"))
        str_name = _clean_str(raw_props.get("STR_NAME") or raw_props.get("Str_name"))
        str_sfx = _clean_str(raw_props.get("STR_SFX") or raw_props.get("Str_sfx"))
        address = " ".join(p for p in (str_num, str_name, str_sfx) if p)

    yr_cadastral = normalize_year(
        raw_props.get("YR_IMPR")
        or raw_props.get("Yr_Impr")
        or raw_props.get("date_erect")
    )
    yr_hist = normalize_year(hist.get("Yr_Impr"))
    yr_lm = normalize_year(lm.get("USER_YR_BUILT"))

    candidates = [y for y in (yr_lm, yr_hist, yr_cadastral) if y > 0]
    year_built = min(candidates) if candidates else 0
    remodel_year = normalize_year(raw_props.get("yr_remodel"))

    owner = (
        _clean_str(raw_props.get("OWNER_MAILTO"))
        or _clean_str(raw_props.get("Owner_Mailto"))
        or _clean_str(hist.get("Owner_Mailto"))
        or _clean_str(lm.get("USER_OWNER"))
        or _clean_str(raw_props.get("CurrOwner"))
    )

    bld_area = _clean_float(
        raw_props.get("TOTAL_BUILDING_AREA")
        or raw_props.get("Total_Building_Area")
        or hist.get("Total_Building_Area")
        or raw_props.get("im_sq_ft")
    )
    land_area = _clean_float(
        raw_props.get("TOTAL_LAND_AREA")
        or raw_props.get("Total_Land_Area")
        or hist.get("Total_Land_Area")
    )

    state_class = _clean_str(
        raw_props.get("STATE_CLASS")
        or raw_props.get("State_Class")
        or hist.get("State_Class")
    )
    landuse_desc = _clean_str(
        raw_props.get("LANDUSE_DSCR")
        or raw_props.get("landuse_dscr")
        or hist.get("landuse_dscr")
    )
    group_desc = _clean_str(
        raw_props.get("GROUP_DSCR")
        or raw_props.get("group_dscr")
        or hist.get("group_dscr")
    )
    use_category = classify_use_category(state_class, landuse_desc, group_desc)

    historic_district = (
        _clean_str(hist.get("Historic_District_Name"))
        or _clean_str(raw_props.get("Historic_District_Name"))
        or _clean_str(lm.get("USER_HISTORIC_DISTRICT"))
    )

    raw_contrib = _clean_str(
        hist.get("Building_Classification")
        or raw_props.get("Building_Classification")
    )
    if raw_contrib == "Contributing":
        contributing = "Contributing"
    elif raw_contrib in ("NonContributing", "Non-Contributing"):
        contributing = "Non-Contributing"
    else:
        contributing = "Outside Historic District"

    landmark_name = (
        _clean_str(lm.get("USER_SITE_NAME"))
        or _clean_str(raw_props.get("PDLandMark"))
    )
    if landmark_name.lower() in (
        "no",
        "none",
        "0",
        "false",
        "no designation",
        "not a landmark",
        "n/a",
        "undesignated",
    ):
        landmark_name = ""

    raw_lm_type = _clean_str(
        lm.get("LandmarkDesignation")
        or hist.get("Landmark_Designation")
        or raw_props.get("Landmark_Designation")
        or raw_props.get("DESIG_TYPE")
    )
    if raw_lm_type in ("Protected Landmark", "PLM"):
        landmark_type = "Protected Landmark"
    elif raw_lm_type in ("Landmark", "LM") or landmark_name:
        landmark_type = "Landmark"
    else:
        landmark_type = ""

    architect = _clean_str(lm.get("USER_ARCHITECT___BUILDER"))
    bld_style = (
        _clean_str(lm.get("USER_STYLE"))
        or _clean_str(raw_props.get("style_desc"))
        or _clean_str(raw_props.get("ECON_BLD_CLASS"))
        or _clean_str(hist.get("Econ_Bld_Class"))
    )
    subdivision = (
        _clean_str(raw_props.get("LEGAL_DSCR_1"))
        or _clean_str(raw_props.get("Legal_Dscr_1"))
        or _clean_str(hist.get("Legal_Dscr_1"))
        or _clean_str(lm.get("USER_SUBDIVISION"))
    )

    stories, height_m = estimate_stories_and_height(
        bld_area=bld_area,
        footprint_area_sqft=bld_area * 0.65 if bld_area > 0 else 0.0,
        use_category=use_category,
    )

    return {
        "id": f"pcl_{hcad_num or id(raw_props)}",
        "hcad_num": hcad_num,
        "address": address,
        "year_built": year_built,
        "decade": compute_decade(year_built),
        "remodel_year": remodel_year,
        "owner": owner,
        "bld_area": bld_area,
        "land_area": land_area,
        "stories": stories,
        "height_m": height_m,
        "use_category": use_category,
        "landuse_desc": landuse_desc or use_category,
        "bld_style": bld_style,
        "subdivision": subdivision,
        "historic_district": historic_district,
        "contributing": contributing,
        "landmark_name": landmark_name,
        "landmark_type": landmark_type,
        "architect": architect,
    }


def _find_em_dash_fields(record: dict[str, Any], prefix: str = "") -> list[str]:
    bad: list[str] = []
    for k, v in record.items():
        path = f"{prefix}.{k}" if prefix else str(k)
        if isinstance(v, str) and "\u2014" in v:
            bad.append(path)
        elif isinstance(v, list):
            for idx_i, elem in enumerate(v):
                if isinstance(elem, str) and "\u2014" in elem:
                    bad.append(f"{path}[{idx_i}]")
                elif isinstance(elem, dict):
                    bad.extend(_find_em_dash_fields(elem, f"{path}[{idx_i}]"))
        elif isinstance(v, dict):
            bad.extend(_find_em_dash_fields(v, path))
    return bad


def validate_plat_monitor_item_contract(
    item: dict[str, Any],
    review: dict[str, Any] | None = None,
) -> list[str]:
    """Validate Pass #2 structural contracts on a Plat Monitor item and its review overlay."""
    errors: list[str] = []
    if not isinstance(item, dict):
        return ["item is not a dict"]

    app = str(item.get("app") or "").strip()
    if not app:
        errors.append("item missing non-empty 'app'")

    expected_on_agenda = bool(str(item.get("agendaItemNo") or "").strip())
    if not isinstance(item.get("onMeetingAgenda"), bool):
        errors.append(f"{app}: 'onMeetingAgenda' must be bool")
    elif item["onMeetingAgenda"] != expected_on_agenda:
        errors.append(
            f"{app}: 'onMeetingAgenda' ({item['onMeetingAgenda']}) != bool(agendaItemNo) ({expected_on_agenda})"
        )

    if "linkedAppNo" in item and not isinstance(item.get("linkedAppNo"), str):
        errors.append(f"{app}: 'linkedAppNo' must be str when present")

    em_item = _find_em_dash_fields(item, f"item[{app}]")
    if em_item:
        errors.append(f"{app}: em-dash found in {', '.join(em_item)}")

    if review is not None:
        if not isinstance(review, dict):
            errors.append(f"{app}: review is not a dict")
        else:
            valid_prov = set(PLAT_CARRY_FORWARD_REVIEW_FIELDS) | {
                "reviewer",
                "overrideScreenOut",
            }
            for prov_key in ("carriedFields", "humanEditedFields"):
                if prov_key in review:
                    val = review.get(prov_key)
                    if not isinstance(val, list) or not all(
                        isinstance(f, str) and f in valid_prov for f in val
                    ):
                        errors.append(f"{app}: invalid review '{prov_key}'={val!r}")
            em_rev = _find_em_dash_fields(review, f"review[{app}]")
            if em_rev:
                errors.append(f"{app}: em-dash found in {', '.join(em_rev)}")

    return errors


def validate_watch_property_contract(
    prop: dict[str, Any],
    *,
    allow_tombstone: bool = False,
) -> list[str]:
    """Validate Pass #2 structural contracts on a Watchlist property dossier."""
    errors: list[str] = []
    if not isinstance(prop, dict):
        return ["prop is not a dict"]

    pid = str(prop.get("id") or "").strip()
    if not pid:
        errors.append("prop missing non-empty 'id'")

    if not isinstance(prop.get("broker"), str):
        errors.append(f"{pid}: 'broker' must be str")
    if not isinstance(prop.get("tierConfirmed"), bool):
        errors.append(f"{pid}: 'tierConfirmed' must be bool")

    status = str(prop.get("trackerStatus") or "").strip()
    is_tombstone = bool(prop.get("removedFromWatchlist")) or status in WATCHLIST_TOMBSTONE_STATUSES
    if is_tombstone and not allow_tombstone:
        errors.append(f"{pid}: unexpected tombstone in active watchlist (status={status!r})")
    elif not is_tombstone and status not in WATCHLIST_TRACKER_STATUSES:
        errors.append(f"{pid}: invalid trackerStatus={status!r}")

    em_prop = _find_em_dash_fields(prop, f"watch[{pid}]")
    if em_prop:
        errors.append(f"{pid}: em-dash found in {', '.join(em_prop)}")

    return errors


LOST_HOUSTON_COHORTS: tuple[str, ...] = (
    "curated_landmark",
    "coh_landmark_demolished",
    "nrhp_demolished",
    "red_book_1915_vanished",
    "historic_teardown_2020_2026",
)
LOST_HOUSTON_VALID_COHORTS: frozenset[str] = frozenset(LOST_HOUSTON_COHORTS)

LOST_HOUSTON_COHORT_LABELS: dict[str, str] = {
    "curated_landmark": "Curated Survey & Archival Landmark",
    "coh_landmark_demolished": "City of Houston Designated Landmark (Demolished)",
    "nrhp_demolished": "National Register Property (Demolished / Gone)",
    "red_book_1915_vanished": "1915 Red Book of Houston (Vanished Place)",
    "historic_teardown_2020_2026": "2020-2026 Pre-1940 Historic Teardown",
}

_RE_MAIN_DEMO_ALLOW = re.compile(
    r"\b("
    r"DEMO\s*(?:RES|HSE|HOUSE|DWG|DWELLING|SF|SFR|BLDG|BLD|BUILDING|COM|COMM|COMMERCIAL|STRUC|STRUCTURE|DUPLEX|TRIPLEX|FOURPLEX|4-PLEX|MAIN|CHURCH|SCHOOL)"
    r"|TOTAL\s*DEMO(?:LITION)?"
    r"|DEMOLISH\s*(?:RES|HSE|HOUSE|DWG|BLDG|BLD|BUILDING|MAIN|DUPLEX|STRUCTURE)"
    r"|COMPLETE\s*DEMO(?:LITION)?"
    r"|D\.?B\.?\s*/\s*(?:DEMO\s*/\s*)?RESIDENTIAL"
    r"|COH\s*DEMO\s*/\s*RESIDENTIAL"
    r"|MAIN\s*STRUC(?:TURE)?"
    r"|RAZED"
    r"|TEARDOWN"
    r")\b",
    re.IGNORECASE,
)

_RE_PRIMARY_PLUS_ACCESSORY_DEMO = re.compile(
    r"\b(?:DEMO(?:LISH)?\s+)?(?:MAIN\s+)?(?:HOUSE|RESIDENCE|BLDG|BLD|BUILDING|DWELLING)\s*"
    r"(?:&|AND|\+)\s*(?:DETACHED\s+)?(?:GAR(?:AGE)?|POOL|CARPORT|SHED|QTRS|QUARTERS)\b",
    re.IGNORECASE,
)

_RE_PARTIAL_OR_NOTE_DENY = re.compile(
    r"\b("
    r"INTERIOR|PARTIAL|SELECTIVE|NON[- ]?STRUCT(?:URAL)?|NON[- ]?LOAD|NON[- ]?FIRE|EXPLORATORY"
    r"|FACADE|ROOF|REROOF|FOUNDATION\s*REPAIR|SLAB\s*ONLY|REMODEL|ADDITION|ALTERATION"
    r"|REPAIR|FIRE\s*DAMAGE\s*REPAIR|WATER\s*DAMAGE|STORM\s*DAMAGE"
    r"|CHECK\s*FOR|VERIFY|RECHECK|INSPECT|REVIEW|PENDING|CANCEL|VOID|EXPIRED"
    r"|TENANT\s*BUILD|BUILD[- ]?OUT|FINISH[- ]?OUT"
    r")\b",
    re.IGNORECASE,
)

_RE_MAIN_DEMO_DENY = re.compile(
    r"\b("
    r"GAR|GARAGE|CARPORT|SHED|POOL|SPA|ACC|ACCESSORY|OUTBUILDING|STORAGE|DET|DETACHED"
    r"|QTRS|QUARTERS|GUEST|CABANA|GAZEBO|PAVILION|PERGOLA|BARN|STABLE|WORKSHOP"
    r"|INTERIOR|INT|PARTIAL|SELECTIVE|NON[- ]?STRUCT(?:URAL)?|NON[- ]?LOAD|NON[- ]?FIRE|EXPLORATORY"
    r"|FACADE|ROOF|REROOF|PORCH|PATIO|DECK|BALCONY|STAIR|FENCE|WALL|DRIVEWAY|PAVING"
    r"|SIDEWALK|FLATWORK|RETAINING|CANOPY|AWNING|SIGN|BILLBOARD|ANTENNA|TOWER"
    r"|FOUNDATION\s*REPAIR|SLAB\s*ONLY|REMODEL|ADDITION|ALT|ALTERATION"
    r"|REPAIR|FIRE\s*DAMAGE\s*REPAIR|WATER\s*DAMAGE|STORM\s*DAMAGE"
    r"|CHECK\s*FOR|VERIFY|RECHECK|INSPECT|REVIEW|TEST|PENDING|CANCEL|VOID|EXPIRED"
    r"|TENANT\s*BUILD|BUILD[- ]?OUT|FINISH[- ]?OUT|MECH|HVAC|PLUMB|ELEC"
    r")\b",
    re.IGNORECASE,
)

_RE_SECONDARY_UNIT_ADDR = re.compile(
    r"(?:^\s*0+\b"
    r"|\b1/2\b"
    r"|\bREAR\b"
    r"|\bGARAGE\b"
    r"|\bCARPORT\b"
    r"|\bQTRS\b"
    r"|\bQUARTERS\b"
    r"|\bGUEST\b"
    r"|\bAPT\s*[A-Z0-9]*\b"
    r"|\bUNIT\s+[B-Z2-9]\w*\b"
    r"|\bSTE\s+\w+\b"
    r"|\bSUITE\s+\w+\b"
    r"|\bBLDG?\s+[B-Z2-9]\w*\b"
    r"|#\s*[A-Z0-9]+\b)",
    re.IGNORECASE,
)

_HISTORIC_HOUSTON_STREET_RENAMES: tuple[tuple[str, str], ...] = (
    ("SAN FELIPE", "W DALLAS"),
    ("W DALLAS", "SAN FELIPE"),
    ("DOWLING", "EMANCIPATION"),
    ("EMANCIPATION", "DOWLING"),
    ("ODIN", "JENSEN"),
    ("CARR", "JENSEN"),
    ("JENSEN", "ODIN"),
    ("GRANT", "MONTROSE"),
    ("MONTROSE", "GRANT"),
    ("HATHAWAY", "WESTHEIMER"),
    ("WESTHEIMER", "HATHAWAY"),
    ("BRUNNER", "WASHINGTON"),
    ("COURTLAND", "CORTLANDT"),
    ("CORTLANDT", "COURTLAND"),
    ("SHEPHERD DAM", "GREENBRIAR"),
    ("ALMEDA", "CRAWFORD"),
)

_STREET_SUFFIX_CANON: dict[str, str] = {
    "STREET": "ST",
    "ST": "ST",
    "AVENUE": "AVE",
    "AV": "AVE",
    "AVE": "AVE",
    "BOULEVARD": "BLVD",
    "BLVD": "BLVD",
    "DRIVE": "DR",
    "DR": "DR",
    "ROAD": "RD",
    "RD": "RD",
    "PLACE": "PL",
    "PL": "PL",
    "COURT": "CT",
    "CT": "CT",
    "LANE": "LN",
    "LN": "LN",
    "PARKWAY": "PKWY",
    "PKWY": "PKWY",
    "FREEWAY": "FWY",
    "FWY": "FWY",
    "HIGHWAY": "HWY",
    "HWY": "HWY",
    "CIRCLE": "CIR",
    "CIR": "CIR",
    "TERRACE": "TER",
    "TER": "TER",
    "SQUARE": "SQ",
    "SQ": "SQ",
    "TRAIL": "TRL",
    "TRL": "TRL",
    "WAY": "WAY",
}

_DIRECTIONAL_CANON: dict[str, str] = {
    "NORTH": "N",
    "N": "N",
    "SOUTH": "S",
    "S": "S",
    "EAST": "E",
    "E": "E",
    "WEST": "W",
    "W": "W",
}


def is_main_structure_demolition_scope(
    description: Any,
    *,
    allow_empty: bool = False,
) -> bool:
    """Shared primary-vs-accessory demolition text screener (DF-5 / MOD-2).

    Used across both `classify_main_structure_demolition_permit` and
    `build_neighborhood_scorecards` so pool, detached garage, carport, canopy,
    shed, interior remodel, roof, repair, and cancel/void permits are rejected
    by a single source of truth, while explicit combined teardowns such as
    `DEMO HOUSE & GARAGE` or `HOUSE & POOL` pass.
    """
    dscr = "" if description is None else str(description).strip()
    if not dscr:
        return bool(allow_empty)
    if _RE_PARTIAL_OR_NOTE_DENY.search(dscr):
        return False
    if _RE_PRIMARY_PLUS_ACCESSORY_DEMO.search(dscr):
        return True
    if _RE_MAIN_DEMO_DENY.search(dscr):
        return False
    return True


def classify_main_structure_demolition_permit(
    description: Any,
    permit_type: Any = "",
    issue_date: Any = "",
    *,
    raw_address: Any = "",
    site_apt: Any = "",
    permit_desc: Any = "",
    min_year: int = 2020,
    max_year: int = MAX_VALID_CAMA_DATE_YEAR,
) -> tuple[bool, int, str]:
    """Deterministically classify whether a permit removes a primary structure (DF-5).

    Returns `(is_main_demo, permit_year, reason_code)`:
      - Rejects blank/empty descriptions (never treats raw `SD`/`4D` codes alone as proof
        without description inspection; for COH 2026 structural permits where `permit_desc`
        is explicitly `"Demolition"` or `"Dgr Bldg Demo"`, still inspects `description`/`use_desc`
        against the allowlist and accessory/interior denylist).
      - Rejects HCAD SQL/OLE null-date sentinels (`12/30/1899`, `01/01/1900`, `01/02/1900`)
        and dates outside `[min_year, max_year]`.
      - Rejects `0 <STREET>` and secondary unit suffixes (`1/2`, `REAR`, `GARAGE`, `APT`,
        `BLD B`, `UNIT B`, `#B`).
      - Rejects accessory, garage, carport, pool, shed, interior, partial, roof, facade,
        remodel, addition, repair, and appraiser note permits (`CHECK FOR DEMO`, `VERIFY`),
        while permitting combined teardowns like `DEMO HOUSE & GARAGE`.
    """
    iso_dt, yr = normalize_hcad_date(
        issue_date,
        None,
        allow_fallback=False,
        min_year=min_year,
        max_year=max_year,
    )
    if not yr or not iso_dt:
        # Also allow bare 4-digit year string only when explicitly in [min_year, max_year]
        s_dt = "" if issue_date is None else str(issue_date).strip()
        if re.fullmatch(r"\d{4}", s_dt):
            y_only = int(s_dt)
            if min_year <= y_only <= max_year:
                yr = y_only
            else:
                return False, 0, "year_out_of_range"
        else:
            return False, 0, "invalid_or_sentinel_date"

    dscr = "" if description is None else str(description).strip()
    if not dscr:
        return False, 0, "empty_description"

    addr = "" if raw_address is None else str(raw_address).strip()
    apt = "" if site_apt is None else str(site_apt).strip()
    if apt and re.search(r"(1/2|REAR|GAR|APT|QTRS|GUEST|[B-Z2-9])", apt, re.IGNORECASE):
        return False, 0, "secondary_unit_apt"
    if addr:
        if _RE_SECONDARY_UNIT_ADDR.search(addr):
            return False, 0, "secondary_or_zero_address"
        if not re.match(r"^[1-9]\d*\s+\S+", addr):
            return False, 0, "missing_street_number"

    if not is_main_structure_demolition_scope(dscr, allow_empty=False):
        return False, 0, "denied_accessory_or_partial"

    p_type = "" if permit_type is None else str(permit_type).strip().upper()
    p_desc = "" if permit_desc is None else str(permit_desc).strip().upper()

    if _RE_MAIN_DEMO_ALLOW.search(dscr) or _RE_PRIMARY_PLUS_ACCESSORY_DEMO.search(dscr):
        return True, yr, "main_structure_demo"

    # COH structural demolition feed edge case: permit_type SD/4D + permit_desc DEMOLITION/DGR BLDG DEMO
    # with explicit SINGLE FAMILY / RESIDENTIAL / COMMERCIAL BUILDING description alongside DEMO token
    if p_type in ("SD", "4D") and p_desc in ("DEMOLITION", "DGR BLDG DEMO"):
        if re.search(r"\bDEMO(?:LITION)?\b", dscr, re.IGNORECASE) and re.search(
            r"\b(SINGLE\s*FAMILY|RESIDENTIAL|RESIDENCE|HOUSE|DWELLING|COMMERCIAL|BUILDING|CHURCH|SCHOOL)\b",
            dscr,
            re.IGNORECASE,
        ):
            return True, yr, "coh_structural_main_demo"

    return False, 0, "unmatched_description"


def normalize_houston_address_keys(
    raw_address: Any,
    *,
    include_historic_aliases: bool = True,
) -> list[str]:
    """Normalize a Houston street address to canonical uppercase lookup keys.

    Strips parenthetical notes (`1010 Prairie St (SW corner Main & Prairie)`), historic
    annotations (`now 2000 Emancipation`), punctuation, trailing city/state/ZIP tokens,
    and expands multi-number ranges (`815-817 Main St` -> `815 MAIN`, `817 MAIN`) as well
    as documented Houston street renamings (`San Felipe` <-> `W Dallas`, `Dowling` <->
    `Emancipation`, `Odin`/`Carr` <-> `Jensen`, `Courtland` <-> `Cortlandt`, `19th Ave`
    <-> `W 19th St`) so cross-cohort dedup and extant-collision checks operate strictly
    on structured keys rather than fragile substrings.
    """
    s = "" if raw_address is None else sanitize_atlas_text(str(raw_address)).strip().upper()
    if not s:
        return []

    # Collect any explicit "(now <address>)" or "(formerly <address>)" alias too
    segments = [s]
    for m_alias in re.finditer(r"\(\s*(?:NOW|FORMERLY|HISTORICALLY|LATER)\s+([^)]+)\)", s):
        segments.append(m_alias.group(1).strip())

    out_keys: list[str] = []
    seen: set[str] = set()

    def _add(k: str) -> None:
        kk = re.sub(r"\s+", " ", k).strip()
        if kk and kk not in seen:
            seen.add(kk)
            out_keys.append(kk)

    for seg in segments:
        clean = re.sub(r"\([^)]*\)", " ", seg)
        clean = re.split(r"\bHOUSTON\b|,", clean)[0]
        clean = re.sub(r"\bTX\b|\bTEXAS\b|\b77\d{3}(?:-\d{4})?\b", " ", clean)
        clean = clean.replace(".", " ").replace("#", " #")
        clean = re.sub(r"\s+", " ", clean).strip()
        if not clean:
            continue

        # Reject secondary / zero addresses from producing bare primary matches
        if re.match(r"^0+\b", clean):
            continue
        has_half = bool(re.search(r"^\d+\s+1/2\b", clean))

        m_num = re.match(r"^([1-9]\d*)(?:\s*[-/&]+\s*([1-9]\d*))*?\s+(?:(1/2)\s+)?(.+)$", clean)
        if not m_num:
            continue

        lead_part_end = m_num.start(4)
        lead_nums = [n for n in re.findall(r"\b[1-9]\d*\b", clean[:lead_part_end]) if n != "2" or not has_half]
        if has_half:
            # Keep 1/2 explicitly separate so `202 1/2 E 31ST` NEVER collides with `202 E 31ST`
            lead_nums = [f"{lead_nums[0]} 1/2"] if lead_nums else []
        rest = m_num.group(4).strip()
        # Strip trailing apartment/unit tokens
        rest = re.split(r"\b(?:APT|UNIT|STE|SUITE|BLDG|BLD|FL|FLOOR|REAR)\b|#", rest)[0].strip()
        tokens = [t for t in rest.split() if t]
        if not tokens or not lead_nums:
            continue

        dir_prefix = ""
        if tokens and tokens[0] in _DIRECTIONAL_CANON and len(tokens) >= 2:
            dir_prefix = _DIRECTIONAL_CANON[tokens[0]]
            tokens = tokens[1:]

        suffix = ""
        if len(tokens) >= 2 and tokens[-1] in _STREET_SUFFIX_CANON:
            suffix = _STREET_SUFFIX_CANON[tokens[-1]]
            tokens = tokens[:-1]
        elif len(tokens) >= 3 and tokens[-1] in _DIRECTIONAL_CANON and tokens[-2] in _STREET_SUFFIX_CANON:
            # trailing directional e.g. MONTROSE BLVD S
            suffix = _STREET_SUFFIX_CANON[tokens[-2]]
            tokens = tokens[:-2]

        core = " ".join(tokens).strip()
        if not core:
            continue

        street_variants: list[tuple[str, str, str]] = [(dir_prefix, core, suffix)]
        if include_historic_aliases:
            full_street = f"{dir_prefix} {core}".strip()
            for old_st, new_st in _HISTORIC_HOUSTON_STREET_RENAMES:
                if full_street == old_st:
                    parts_n = new_st.split()
                    if parts_n[0] in ("N", "S", "E", "W") and len(parts_n) >= 2:
                        street_variants.append((parts_n[0], " ".join(parts_n[1:]), suffix))
                    else:
                        street_variants.append(("", new_st, suffix))
                elif core == old_st and not dir_prefix:
                    parts_n = new_st.split()
                    if parts_n[0] in ("N", "S", "E", "W") and len(parts_n) >= 2:
                        street_variants.append((parts_n[0], " ".join(parts_n[1:]), suffix))
                    else:
                        street_variants.append(("", new_st, suffix))
            # Houston Heights numbered avenues -> W/E numbered streets (e.g. 829 19TH AVE -> W 19TH ST)
            if re.fullmatch(r"\d+(?:ST|ND|RD|TH)", core) and suffix == "AVE" and not dir_prefix:
                street_variants.append(("W", core, "ST"))
                street_variants.append(("E", core, "ST"))

        for num_tok in lead_nums[:4]:
            for dp, cr, sf in street_variants:
                base_with_dir = f"{num_tok} {dp} {cr}".replace("  ", " ").strip()
                if sf:
                    _add(f"{base_with_dir} {sf}")
                _add(base_with_dir)

    return out_keys

