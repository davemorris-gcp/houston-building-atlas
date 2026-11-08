"""Normalized feature schema and HCAD/COH attribute mapping for Houston Building Atlas v2."""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
import gzip
import io
import json
import math
import os
import pickle
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

MIN_VALID_YEAR = 1836
ACTIVE_TAX_YEAR = 2026
MAX_VALID_CAMA_DATE_YEAR = ACTIVE_TAX_YEAR
MAX_VALID_YEAR = ACTIVE_TAX_YEAR + 1
MAX_STANDING_OVERRIDE_YEAR = 2035
ACTIVE_CACHE_BUST = "20261108a"
PERMIT_ENRICHMENT_SOFT_MAX_BYTES = 948 * 1024 * 1024
SOFT_MAX_APP_BYTES = 968 * 1024 * 1024
HARD_MAX_APP_BYTES = 980 * 1024 * 1024
MAX_SINGLE_FILE_BYTES = 95 * 1024 * 1024
ORD_2015_639_CUTOFF_ISO = "2015-07-24"
ORD_2015_639_CUTOFF_ORD_NUM = 639
QUAD_NAMES: tuple[str, ...] = ("nw_w", "nw_e", "ne", "sw", "se")


@dataclass(frozen=True)
class RollContext:
    """Immutable tax-roll execution context threaded through pipeline stages (DF-1)."""

    tax_year: int = field(default_factory=lambda: ACTIVE_TAX_YEAR)
    cache_bust: str = field(default_factory=lambda: ACTIVE_CACHE_BUST)
    cache_dir: Path | None = None
    output_dir: Path | None = None

    @property
    def max_cama_date_year(self) -> int:
        return int(self.tax_year)

    @property
    def max_valid_year(self) -> int:
        return int(self.tax_year) + 1

    @property
    def roll_dir(self) -> Path:
        if self.cache_dir is None:
            raise ValueError("RollContext.cache_dir must be set before accessing roll_dir.")
        return Path(self.cache_dir) / "rolls" / str(int(self.tax_year))

    @property
    def staged_dir(self) -> Path:
        return self.roll_dir / "staged"


# Milestone 3 cross-language domain constants (DF-1, DF-2, DF-3, DF-4, DF-9).
# Kept 1:1 with `app/js/curatedEdits.js` and `app/js/staffWorkbench.js`.
HARRIS_COUNTY_BBOX: tuple[float, float, float, float] = (-96.2, 29.3, -94.8, 30.3)
MIN_FOOTPRINT_AREA_SQ_M: float = 4.0
POLYGON_EDITOR_MAX_VERTICES: int = 64
GOOGLE_SHEETS_CELL_MAX_CHARS: int = 50000
METERS_PER_DEGREE_LAT: float = 111320.0
SQ_FT_PER_SQ_M: float = 10.76391041671

GEOMETRY_TAG_PREFIX = "[GEOMETRY_GEOJSON:"
FIELD_SURVEY_TAG_PREFIX = "[FIELD_SURVEY_JSON:"
FIELD_SURVEY_SOURCE_TYPE = "FIELD_SURVEY_OBSERVATION"

FIELD_SURVEY_INTEGRITY_GRADES: tuple[str, ...] = (
    "Intact",
    "Minor Alterations",
    "Substantially Altered",
    "Vacant / Boarded",
    "Demolished / Vacant Lot",
    "Storm / Hazard Damaged",
)
FIELD_SURVEY_CONDITION_RATINGS: tuple[str, ...] = (
    "Good",
    "Fair",
    "Poor",
    "Critical / Emergency",
)
FIELD_SURVEY_CONTRIBUTING_VALUES: tuple[str, ...] = (
    "Contributing",
    "Non-Contributing",
    "Potentially Contributing",
    "Vacant / Demolished",
    "Outside Historic District",
)

OVERRIDE_MODE_SHARD_PRIMARY = "SHARD_PRIMARY_METADATA"
OVERRIDE_MODE_ADDITIVE_POLYGON = "ADDITIVE_CUSTOM_POLYGON"
OVERRIDE_MODE_PARCEL_REPLACEMENT = "PARCEL_REPLACEMENT_POLYGON"
OVERRIDE_MODE_SUPPRESS_VACANT = "SUPPRESS_PARCEL_VACANT"

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
    max_year: int | None = None,
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
    eff_max = MAX_VALID_CAMA_DATE_YEAR if max_year is None else int(max_year)
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
            if min_year <= y_int <= eff_max:
                try:
                    dt_obj = datetime.date(y_int, m_int, d_int)
                    return dt_obj.isoformat(), y_int
                except ValueError:
                    pass

    if allow_fallback and fallback_yr_str is not None:
        fb_yr = normalize_year(fallback_yr_str, max_year=max(eff_max, min_year))
        if fb_yr != 1899 and min_year <= fb_yr <= eff_max:
            return None, fb_yr

    return None, 0


def parse_hcad_date_year(
    date_str: Any,
    fallback_yr_str: Any = None,
    *,
    allow_fallback: bool = True,
    min_year: int = MIN_VALID_YEAR,
    max_year: int | None = None,
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
    tmp_path = target.with_name(f"{target.name}.{os.getpid()}.{threading.get_ident()}.tmp")
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


EXTRA_FEATURES_CAP_PER_ACCOUNT = 6
_HCAD_RELOCATION_NOTE_RE = re.compile(
    r"\b(?:MOVED\s+(?:FROM|TO|ON|IN|HERE|ONTO)|RELOCATED\s+(?:FROM|TO))\b",
    re.IGNORECASE,
)
_BARE_PERMIT_LIST_NOTE_RE = re.compile(r"[\d\s,;/#-]+")


def normalize_hcad_appraiser_note(raw_note: Any, *, max_len: int = 240) -> tuple[str, bool]:
    """Sanitize an HCAD appraiser note and detect structure relocations in a single pass.

    Strips bare numeric permit-number lists (already represented in `permits.txt`), detects
    appraiser relocation phrases across the full unclipped text, and truncates to `max_len`.
    """
    note = sanitize_atlas_text(" ".join(str(raw_note or "").split()))
    if not note or _BARE_PERMIT_LIST_NOTE_RE.fullmatch(note):
        return "", False
    indicates_relocation = bool(_HCAD_RELOCATION_NOTE_RE.search(note))
    return note[:max_len], indicates_relocation


def is_meaningful_hcad_extra_feature(dscr: Any) -> bool:
    """Return True when an HCAD extra-feature description carries structural signal."""
    d = str(dscr or "").strip()
    if not d:
        return False
    du = d.upper()
    return not du.startswith("RESIDENTIAL GAS") and "CRACKED" not in du


def normalize_hcad_extra_feature_row(
    dscr_raw: Any,
    act_yr_raw: Any,
    *,
    max_year: int | None = None,
) -> dict[str, Any] | None:
    """Normalize an HCAD `extra_features_detail*.txt` row (`{"d": ..., "y": ...}`) or return `None`."""
    dscr = sanitize_atlas_text(str(dscr_raw or "").strip())
    if not is_meaningful_hcad_extra_feature(dscr):
        return None
    eff_max = ACTIVE_TAX_YEAR if max_year is None else int(max_year)
    ay_s = str(act_yr_raw or "").strip()
    yr = normalize_year(ay_s, max_year=eff_max) or 0
    if not yr and ay_s:
        yr = parse_hcad_date_year(ay_s, allow_fallback=False, max_year=eff_max)
    return {"d": dscr, "y": int(yr) if yr >= MIN_VALID_YEAR else 0}


PERMITS_CAP_PER_ACCOUNT = 25
PERMIT_TYPE_CATEGORY: dict[str, tuple[str, str]] = {
    "01": ("New Construction", "new"),
    "02": ("Demolition", "demo"),
    "03": ("Addition / Increase Sq Ft", "add"),
    "04": ("Finish Out / Completion", "remodel"),
    "05": ("Remodel / Renovation", "remodel"),
    "06": ("Mechanical / Electrical / Plumbing", "mep"),
    "07": ("Re-Roof / Roof Structure", "roof"),
    "08": ("Swimming Pool / Spa", "aux"),
    "09": ("Enclosure / Carport / Patio", "add"),
    "11": ("Fire / Storm Damage Repair", "repair"),
    "13": ("General Repair / Foundation", "repair"),
    "14": ("Paving / Flatwork / Driveway", "aux"),
    "15": ("Moved / Relocated Structure", "reloc"),
    "16": ("Storage / Outbuilding / Canopy", "aux"),
    "17": ("Foundation / Structural Repair", "repair"),
    "18": ("Site / Drainage / Utilities", "aux"),
    "31": ("New Custom Residence", "new"),
    "33": ("Townhome / Multi-Family New Build", "new"),
    "50": ("Commercial General Permit", "remodel"),
    "TX": ("County Tax Permit", "remodel"),
    "W": ("Water / Utility Permit", "mep"),
}


def normalize_hcad_permit_row(
    pid: Any,
    dscr: Any,
    ptype: Any,
    pdesc: Any,
    issue_date: Any,
    *,
    max_year: int | None = None,
) -> tuple[dict[str, Any], dict[str, str] | None]:
    """Normalize a single HCAD `permits.txt` row and optional moved-structure metadata."""
    iso_dt_parsed, _ = normalize_hcad_date(
        issue_date, allow_fallback=False, max_year=max_year
    )
    iso_dt = iso_dt_parsed or ""
    display_dt = iso_dt or "Undated"
    pid_s = sanitize_atlas_text(str(pid or "").strip())
    dscr_s = sanitize_atlas_text(str(dscr or "").strip())
    ptype_s = str(ptype or "").strip()
    pdesc_s = sanitize_atlas_text(str(pdesc or "").strip())

    is_omitted_imps = "OMITTED IMPS" in dscr_s.upper()
    reloc_meta: dict[str, str] | None = None
    if ptype_s == "15" and not is_omitted_imps:
        reloc_meta = {
            "reason": "City of Houston Moved Structure Permit",
            "note": f"{display_dt}: {dscr_s or pdesc_s} (Permit #{pid_s})",
            "permit_id": pid_s,
            "permit_date": iso_dt,
        }

    if is_omitted_imps:
        cat_label, cat_code = ("HCAD Omitted Improvement Assessment", "add")
    else:
        cat_label, cat_code = PERMIT_TYPE_CATEGORY.get(
            ptype_s, (pdesc_s or "Building Permit", "other")
        )

    desc_val = dscr_s if dscr_s and dscr_s.upper() != pdesc_s.upper() else pdesc_s
    permit_obj: dict[str, Any] = {
        "id": pid_s,
        "dt": iso_dt,
        "cat": cat_code,
        "tp": cat_label,
        "d": desc_val,
    }
    return permit_obj, reloc_meta


def finalize_hcad_account_permits(
    permits: list[dict[str, Any]],
    *,
    limit: int = PERMITS_CAP_PER_ACCOUNT,
) -> list[dict[str, Any]]:
    """Sort an account's normalized permits newest-first and cap at `limit`."""
    return sorted(
        permits,
        key=lambda item: (str(item.get("dt") or ""), str(item.get("id") or "")),
        reverse=True,
    )[:limit]


def extract_parcels_zip_dataset(
    parcels_zip_path: Path,
    scratch_extract_dir: Path | None = None,
) -> Path:
    """Safely extract `Parcels.zip` once (with zip-slip & size+mtime invalidation) and return its vector dataset path."""
    zp = Path(parcels_zip_path)
    if not zp.exists():
        raise ValueError(f"Missing required parcel archive: {zp}")
    size = zp.stat().st_size
    if size < 22:
        raise ValueError(f"Archive {zp.name} is truncated ({size} bytes).")
    with open(zp, "rb") as fh:
        magic = fh.read(4)
    if magic != b"PK\x03\x04":
        raise ValueError(
            f"Archive {zp.name} failed ZIP magic-byte verification (got {magic!r})."
        )

    extract_dir = Path(scratch_extract_dir) if scratch_extract_dir is not None else (zp.parent / "_parcels_extracted")
    extract_dir.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zp, "r") as zf:
            for member_info in zf.infolist():
                m_path = Path(member_info.filename)
                if m_path.is_absolute() or ".." in m_path.parts:
                    raise ValueError(
                        f"Parcels.zip contains unsafe path entry: {member_info.filename!r}"
                    )
            names = zf.namelist()
            gdb_roots = sorted({n.split(".gdb/")[0] + ".gdb" for n in names if ".gdb/" in n})
            gpkg_files = sorted(n for n in names if n.lower().endswith(".gpkg") and not n.endswith("/"))
            shp_files = sorted(n for n in names if n.lower().endswith(".shp") and not n.endswith("/"))
            geojson_files = sorted(
                n for n in names if n.lower().endswith((".geojson", ".json")) and not n.endswith("/")
            )
            if not (gdb_roots or gpkg_files or shp_files or geojson_files):
                raise ValueError(
                    f"Parcels.zip ({zp}) contains no supported vector layer (.gdb/.gpkg/.shp/.geojson)."
                )
            stat = zp.stat()
            marker = extract_dir / f".extracted_{stat.st_size}_{int(stat.st_mtime_ns)}"
            if not marker.exists():
                bad_member = zf.testzip()
                if bad_member is not None:
                    raise ValueError(f"Archive {zp.name} failed CRC check on member {bad_member!r}.")
                for child in list(extract_dir.iterdir()):
                    if child.is_dir():
                        shutil.rmtree(child, ignore_errors=True)
                    else:
                        child.unlink(missing_ok=True)
                zf.extractall(extract_dir)
                marker.write_text("ok", encoding="utf-8")
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Archive {zp.name} is not a valid ZIP file: {exc}") from exc

    if gdb_roots:
        return extract_dir / gdb_roots[0]
    if gpkg_files:
        return extract_dir / gpkg_files[0]
    if shp_files:
        return extract_dir / shp_files[0]
    return extract_dir / geojson_files[0]


def resolve_ch42_ordinance_lifecycle(
    *,
    ordinance: Any = "",
    effective_date: Any = "",
    expiration_date: Any = "",
    status: Any = "Approved",
    protection_type: str = "smls",
    ref_year: int | None = None,
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
    eff_ref = MAX_VALID_CAMA_DATE_YEAR if ref_year is None else int(ref_year)
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
    elif status_low == "expired" or (resolved_exp_yr > 0 and resolved_exp_yr < eff_ref):
        sunset_status = "expired"
    elif resolved_exp_yr > 0 and eff_ref <= resolved_exp_yr <= eff_ref + renewal_window_years:
        sunset_status = "expiring_soon"
    elif resolved_exp_yr > eff_ref + renewal_window_years:
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


_PURE_PYTHON_PMTILES_FALLBACK_MAX_FEATURES = 25_000


def _resolve_tippecanoe_binary() -> str | None:
    """Locate an executable `tippecanoe` binary, returning None when absent (e.g. GitHub Actions CI)."""
    candidates = [
        shutil.which("tippecanoe"),
        "/tmp/tippecanoe/tippecanoe",
        str(Path.home() / ".local" / "bin" / "tippecanoe"),
    ]
    for cand in candidates:
        if cand and Path(cand).is_file():
            return str(cand)
    return None


def compile_quadrant_pmtiles(
    shard_dir: Path,
    output_dir: Path,
    quad_names: tuple[str, ...] = QUAD_NAMES,
) -> tuple[list[str], int]:
    """Compile quadrant .geojsonseq shards into Web Mercator PMTiles v3 archives in parallel via Tippecanoe.

    Falls back cleanly to pure-Python `tile_builder.compile_pmtiles_archive` when `tippecanoe`
    is not installed (such as headless CI runners) and each shard contains <= 25,000 features.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    tippecanoe_bin = _resolve_tippecanoe_binary()
    if tippecanoe_bin is None:
        import orjson
        from atlas_pipeline.tile_builder import compile_pmtiles_archive

        print(
            "-> tippecanoe binary not found; compiling PMTiles v3 archives via pure-Python mapbox_vector_tile..."
        )
        t_py = time.time()
        shard_files_py: list[str] = []
        total_py_bytes = 0
        for q in quad_names:
            in_seq = shard_dir / f"buildings_{q}.geojsonseq"
            out_pmtiles = output_dir / f"houston_buildings_{q}.pmtiles"
            feats: list[dict[str, Any]] = []
            for raw_ln in in_seq.read_bytes().splitlines():
                ln = raw_ln[1:] if raw_ln.startswith(b"\x1e") else raw_ln
                ln = ln.strip()
                if ln:
                    feats.append(orjson.loads(ln))
            if len(feats) > _PURE_PYTHON_PMTILES_FALLBACK_MAX_FEATURES:
                raise RuntimeError(
                    f"tippecanoe binary not found on PATH or ~/.local/bin/tippecanoe; "
                    f"required to compile countywide shard {in_seq.name} ({len(feats):,} features)."
                )
            sz = compile_pmtiles_archive(out_pmtiles, feats, [], min_zoom=10, max_zoom=15)
            if sz <= 0 or not out_pmtiles.exists():
                raise RuntimeError(
                    f"Pure-Python PMTiles fallback produced empty archive for quadrant {q} ({len(feats)} features)."
                )
            total_py_bytes += sz
            shard_files_py.append(out_pmtiles.name)
            print(f"   Compiled {out_pmtiles.name} (pure-Python MVT): {sz / 1024:.1f} KB")
        print(f"-> Pure-Python PMTiles compilation finished in {time.time() - t_py:.2f}s.")
        return shard_files_py, total_py_bytes

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
        out["keep_shard_footprints"] = False
        out.pop("geometry", None)
    else:
        yr = normalize_standing_override_year(out.get("year_built"))
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

        # Enforce DF-1 footprint disposition invariants on sub-building records:
        # Custom-geometry additive `#aux_<N>` / sub-building records must NEVER carry
        # `keep_shard_footprints: True`, which causes `_applyCuratedOverridesInMemory`
        # to skip rendering them inside `curated-overrides-src`.
        is_sub = "#" in clean_key or bool(out.get("is_building_override"))
        if is_sub and out.get("geometry") and out.get("keep_shard_footprints") is True:
            out["keep_shard_footprints"] = False
            out.setdefault("replace_parcel_shards", False)

    if isinstance(out.get("source_citation"), str) and GEOMETRY_TAG_PREFIX in out["source_citation"]:
        out["source_citation"] = strip_embedded_geometry_tag(out["source_citation"])

    contrib = str(out.get("contributing") or "").strip()
    if contrib == "NonContributing":
        contrib = "Non-Contributing"
    if contrib not in VALID_CONTRIBUTING_VALUES:
        out["contributing"] = "Outside Historic District"
    else:
        out["contributing"] = contrib

    return out


def normalize_standing_override_year(raw_val: Any) -> int:
    """Parse a standing override construction year in [1836, 2035], or 0 if invalid/unknown (DF-9)."""
    if raw_val is None or isinstance(raw_val, bool):
        return 0
    if isinstance(raw_val, (int, float)):
        if not math.isfinite(raw_val):
            return 0
        yr = int(round(raw_val))
    else:
        s = str(raw_val).strip()
        if not s:
            return 0
        try:
            fval = float(s)
        except ValueError:
            return 0
        if not math.isfinite(fval):
            return 0
        yr = int(round(fval))

    if MIN_VALID_YEAR <= yr <= MAX_STANDING_OVERRIDE_YEAR:
        return yr
    return 0


def normalize_year(raw_val: Any, *, max_year: int | None = None) -> int:
    """Parse a raw year-built value into an integer year in [MIN_VALID_YEAR, max_year], or 0."""
    eff_max = MAX_VALID_YEAR if max_year is None else int(max_year)
    if raw_val is None or isinstance(raw_val, bool):
        return 0
    if isinstance(raw_val, (int, float)):
        if not math.isfinite(raw_val):
            return 0
        yr = int(round(raw_val))
    else:
        s = str(raw_val).strip()
        if not s:
            return 0
        try:
            fval = float(s)
            if not math.isfinite(fval):
                return 0
            yr = int(round(fval))
        except ValueError:
            return 0

    if MIN_VALID_YEAR <= yr <= eff_max:
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
    *,
    max_year: int | None = None,
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
        or raw_props.get("date_erect"),
        max_year=max_year,
    )
    yr_hist = normalize_year(hist.get("Yr_Impr"), max_year=max_year)
    yr_lm = normalize_year(lm.get("USER_YR_BUILT"), max_year=max_year)

    candidates = [y for y in (yr_lm, yr_hist, yr_cadastral) if y > 0]
    year_built = min(candidates) if candidates else 0
    remodel_year = normalize_year(raw_props.get("yr_remodel"), max_year=max_year)

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
    max_year: int | None = None,
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
    eff_max = MAX_VALID_CAMA_DATE_YEAR if max_year is None else int(max_year)
    iso_dt, yr = normalize_hcad_date(
        issue_date,
        None,
        allow_fallback=False,
        min_year=min_year,
        max_year=eff_max,
    )
    if not yr or not iso_dt:
        # Also allow bare 4-digit year string only when explicitly in [min_year, eff_max]
        s_dt = "" if issue_date is None else str(issue_date).strip()
        if re.fullmatch(r"\d{4}", s_dt):
            y_only = int(s_dt)
            if min_year <= y_only <= eff_max:
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


# ==============================================================================
# Milestone 3: Footprint Disposition, Polygon Topology & Embedded Envelope Utils
# ==============================================================================


def classify_override_footprint_mode(
    ov_key: str,
    ov: dict[str, Any] | None,
) -> str:
    """Classify an override record into one of the 4 DF-1 footprint disposition modes.

    Modes:
      1. `SUPPRESS_PARCEL_VACANT`: `suppress_only=True` hides parcel/sub-building footprint(s).
      2. `SHARD_PRIMARY_METADATA`: split-campus sub-building (`#slug` or `is_building_override`)
         with no custom `geometry` (`keep_shard_footprints=True`). Metadata merged onto matching
         PMTiles shard footprint without drawing in `curated-overrides-src`.
      3. `ADDITIVE_CUSTOM_POLYGON`: custom-traced secondary building (`#aux_<N>` / sub-building)
         with valid `Polygon`/`MultiPolygon` `geometry` and `replace_parcel_shards=False`.
         Rendered inside `curated-overrides-src` while sibling parcel shard footprints stay visible.
      4. `PARCEL_REPLACEMENT_POLYGON`: primary parcel polygon replacement (13-digit key with or
         without hydrated geometry, or sub-building with `replace_parcel_shards=True`). Suppresses
         underlying PMTiles shard footprints for `hcad_num` once polygon geometry is active.
    """
    if not isinstance(ov, dict) or not ov:
        return ""
    if bool(ov.get("suppress_only")):
        return OVERRIDE_MODE_SUPPRESS_VACANT

    key_str = str(ov_key or ov.get("building_id") or ov.get("id") or "").strip()
    is_sub = "#" in key_str or bool(ov.get("is_building_override"))
    geom = ov.get("geometry")
    has_geom = (
        isinstance(geom, dict)
        and geom.get("type") in ("Polygon", "MultiPolygon")
        and isinstance(geom.get("coordinates"), list)
        and len(geom["coordinates"]) > 0
    )

    if is_sub:
        if has_geom:
            if bool(ov.get("replace_parcel_shards")):
                return OVERRIDE_MODE_PARCEL_REPLACEMENT
            return OVERRIDE_MODE_ADDITIVE_POLYGON
        return OVERRIDE_MODE_SHARD_PRIMARY

    if ov.get("keep_shard_footprints") is True and not has_geom:
        return OVERRIDE_MODE_SHARD_PRIMARY
    return OVERRIDE_MODE_PARCEL_REPLACEMENT


def _project_ring_to_tangent_meters(
    pts: list[tuple[float, float]],
    origin: tuple[float, float] | None = None,
) -> tuple[list[tuple[float, float]], tuple[float, float], float]:
    """Project `(lng, lat)` vertices onto a local tangent plane in meters around `origin`."""
    if not pts:
        return [], (0.0, 0.0), 1.0
    if origin is None:
        c_lng = sum(p[0] for p in pts) / len(pts)
        c_lat = sum(p[1] for p in pts) / len(pts)
    else:
        c_lng, c_lat = float(origin[0]), float(origin[1])
    cos_lat = math.cos(math.radians(c_lat))
    if abs(cos_lat) < 1e-6:
        cos_lat = 1e-6
    xy = [
        (
            (lng - c_lng) * cos_lat * METERS_PER_DEGREE_LAT,
            (lat - c_lat) * METERS_PER_DEGREE_LAT,
        )
        for lng, lat in pts
    ]
    return xy, (c_lng, c_lat), cos_lat


def _signed_shoelace_area(xy: list[tuple[float, float]]) -> float:
    """Return signed planar area in m^2 (> 0 for CCW, < 0 for CW)."""
    n = len(xy)
    if n < 3:
        return 0.0
    acc = 0.0
    for i in range(n):
        x1, y1 = xy[i]
        x2, y2 = xy[(i + 1) % n]
        acc += x1 * y2 - x2 * y1
    return 0.5 * acc


def compute_ring_planar_area_sq_m(ring_or_geom: Any) -> float:
    """Return unsigned planar area in square meters for a Polygon dict or exterior ring."""
    pts_raw = _extract_raw_exterior_ring_coords(ring_or_geom)
    if not pts_raw:
        return 0.0
    pts: list[tuple[float, float]] = []
    for item in pts_raw:
        if (
            not isinstance(item, (list, tuple))
            or len(item) < 2
            or isinstance(item[0], bool)
            or isinstance(item[1], bool)
        ):
            return 0.0
        try:
            lng = float(item[0])
            lat = float(item[1])
        except (TypeError, ValueError):
            return 0.0
        if not (math.isfinite(lng) and math.isfinite(lat)):
            return 0.0
        pts.append((lng, lat))
    while len(pts) > 1 and pts[-1] == pts[0]:
        pts.pop()
    if len(pts) < 3:
        return 0.0
    xy, _, _ = _project_ring_to_tangent_meters(pts)
    return abs(_signed_shoelace_area(xy))


def _extract_raw_exterior_ring_coords(ring_or_geom: Any) -> list[Any] | None:
    if isinstance(ring_or_geom, dict):
        if ring_or_geom.get("type") != "Polygon":
            return None
        coords = ring_or_geom.get("coordinates")
        if not isinstance(coords, list) or len(coords) != 1 or not isinstance(coords[0], list):
            return None
        return coords[0]
    if isinstance(ring_or_geom, (list, tuple)):
        return list(ring_or_geom)
    return None


def _segments_intersect_planar(
    a1: tuple[float, float],
    a2: tuple[float, float],
    b1: tuple[float, float],
    b2: tuple[float, float],
    eps: float = 1e-7,
) -> bool:
    """Return True if closed planar segments `[a1, a2]` and `[b1, b2]` intersect properly or collinearly."""
    min_ax, max_ax = (a1[0], a2[0]) if a1[0] <= a2[0] else (a2[0], a1[0])
    min_ay, max_ay = (a1[1], a2[1]) if a1[1] <= a2[1] else (a2[1], a1[1])
    min_bx, max_bx = (b1[0], b2[0]) if b1[0] <= b2[0] else (b2[0], b1[0])
    min_by, max_by = (b1[1], b2[1]) if b1[1] <= b2[1] else (b2[1], b1[1])
    if max_ax + eps < min_bx or max_bx + eps < min_ax or max_ay + eps < min_by or max_by + eps < min_ay:
        return False

    def _cross(o: tuple[float, float], p: tuple[float, float], q: tuple[float, float]) -> float:
        return (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0])

    def _on_segment(o: tuple[float, float], p: tuple[float, float], q: tuple[float, float]) -> bool:
        return (
            min(o[0], q[0]) - eps <= p[0] <= max(o[0], q[0]) + eps
            and min(o[1], q[1]) - eps <= p[1] <= max(o[1], q[1]) + eps
        )

    d1 = _cross(a1, a2, b1)
    d2 = _cross(a1, a2, b2)
    d3 = _cross(b1, b2, a1)
    d4 = _cross(b1, b2, a2)

    if ((d1 > eps and d2 < -eps) or (d1 < -eps and d2 > eps)) and (
        (d3 > eps and d4 < -eps) or (d3 < -eps and d4 > eps)
    ):
        return True
    if abs(d1) <= eps and _on_segment(a1, b1, a2):
        return True
    if abs(d2) <= eps and _on_segment(a1, b2, a2):
        return True
    if abs(d3) <= eps and _on_segment(b1, a1, b2):
        return True
    if abs(d4) <= eps and _on_segment(b1, a2, b2):
        return True
    return False


def validate_and_normalize_override_polygon(
    geometry_or_ring: Any,
    *,
    bbox: tuple[float, float, float, float] = HARRIS_COUNTY_BBOX,
    min_area_sq_m: float = MIN_FOOTPRINT_AREA_SQ_M,
    max_vertices: int = POLYGON_EDITOR_MAX_VERTICES,
) -> dict[str, Any]:
    """Validate and normalize an override Polygon in strict post-rounding order (DF-3, DF-9).

    Order of operations:
      1. Parse single exterior ring from GeoJSON `Polygon` or raw `[[lng, lat], ...]`.
      2. Strip raw closing duplicate(s) and snap all coordinates to 6 decimals.
      3. Collapse consecutive snapped duplicates and wrap-around tail duplicate(s).
      4. Enforce vertex bounds (`3..max_vertices`) and county bounding box (`bbox`).
      5. Project to local tangent-plane meters (`cos(lat)`), enforce `area >= min_area_sq_m`,
         and normalize exterior ring to RFC 7946 counter-clockwise (`CCW`) winding.
      6. Reject duplicate non-adjacent vertices, 180-degree folded collinear spikes, and any
         proper or collinear/improper non-adjacent segment intersections on the snapped ring.
      7. Append bit-identical closing vertex `pts[0]`.
    """
    errors: list[str] = []
    if isinstance(geometry_or_ring, dict):
        if geometry_or_ring.get("type") != "Polygon":
            return {
                "ok": False,
                "geometry": None,
                "vertex_count": 0,
                "area_sq_m": 0.0,
                "area_sq_ft": 0.0,
                "centroid": None,
                "was_reversed": False,
                "errors": ["Geometry must have type 'Polygon'."],
            }
        coords = geometry_or_ring.get("coordinates")
        if not isinstance(coords, list) or len(coords) == 0:
            return {
                "ok": False,
                "geometry": None,
                "vertex_count": 0,
                "area_sq_m": 0.0,
                "area_sq_ft": 0.0,
                "centroid": None,
                "was_reversed": False,
                "errors": ["Polygon coordinates must contain an exterior ring."],
            }
        if len(coords) > 1:
            return {
                "ok": False,
                "geometry": None,
                "vertex_count": 0,
                "area_sq_m": 0.0,
                "area_sq_ft": 0.0,
                "centroid": None,
                "was_reversed": False,
                "errors": ["Polygon interior holes are not supported for building footprint overrides."],
            }
        raw_ring = coords[0]
    elif isinstance(geometry_or_ring, (list, tuple)):
        raw_ring = list(geometry_or_ring)
    else:
        return {
            "ok": False,
            "geometry": None,
            "vertex_count": 0,
            "area_sq_m": 0.0,
            "area_sq_ft": 0.0,
            "centroid": None,
            "was_reversed": False,
            "errors": ["Geometry must be a GeoJSON Polygon object or exterior coordinate ring."],
        }

    if not isinstance(raw_ring, (list, tuple)) or len(raw_ring) < 3:
        return {
            "ok": False,
            "geometry": None,
            "vertex_count": 0,
            "area_sq_m": 0.0,
            "area_sq_ft": 0.0,
            "centroid": None,
            "was_reversed": False,
            "errors": ["Polygon exterior ring requires at least 3 distinct vertices."],
        }

    parsed_raw: list[tuple[float, float]] = []
    for idx, pt in enumerate(raw_ring):
        if (
            not isinstance(pt, (list, tuple))
            or len(pt) < 2
            or isinstance(pt[0], bool)
            or isinstance(pt[1], bool)
        ):
            errors.append(f"Vertex {idx} is not a numeric [lng, lat] coordinate pair.")
            continue
        try:
            lng_f = float(pt[0])
            lat_f = float(pt[1])
        except (TypeError, ValueError):
            errors.append(f"Vertex {idx} has non-numeric coordinates.")
            continue
        if not (math.isfinite(lng_f) and math.isfinite(lat_f)):
            errors.append(f"Vertex {idx} has non-finite coordinates.")
            continue
        parsed_raw.append((lng_f, lat_f))

    if errors:
        return {
            "ok": False,
            "geometry": None,
            "vertex_count": 0,
            "area_sq_m": 0.0,
            "area_sq_ft": 0.0,
            "centroid": None,
            "was_reversed": False,
            "errors": errors,
        }

    while len(parsed_raw) > 1 and parsed_raw[-1] == parsed_raw[0]:
        parsed_raw.pop()

    snapped: list[tuple[float, float]] = []
    for lng_f, lat_f in parsed_raw:
        s_lng = round(lng_f, 6)
        s_lat = round(lat_f, 6)
        if s_lng == 0.0:
            s_lng = 0.0
        if s_lat == 0.0:
            s_lat = 0.0
        pt_s = (s_lng, s_lat)
        if not snapped or snapped[-1] != pt_s:
            snapped.append(pt_s)

    while len(snapped) > 1 and snapped[-1] == snapped[0]:
        snapped.pop()

    n = len(snapped)
    if n < 3:
        return {
            "ok": False,
            "geometry": None,
            "vertex_count": n,
            "area_sq_m": 0.0,
            "area_sq_ft": 0.0,
            "centroid": None,
            "was_reversed": False,
            "errors": [f"Polygon requires at least 3 distinct snapped vertices (got {n})."],
        }
    if n > max_vertices:
        return {
            "ok": False,
            "geometry": None,
            "vertex_count": n,
            "area_sq_m": 0.0,
            "area_sq_ft": 0.0,
            "centroid": None,
            "was_reversed": False,
            "errors": [f"Polygon has {n} vertices, exceeding maximum allowed ({max_vertices})."],
        }

    min_lng, min_lat, max_lng, max_lat = bbox
    for idx, (s_lng, s_lat) in enumerate(snapped):
        if not (min_lng <= s_lng <= max_lng and min_lat <= s_lat <= max_lat):
            return {
                "ok": False,
                "geometry": None,
                "vertex_count": n,
                "area_sq_m": 0.0,
                "area_sq_ft": 0.0,
                "centroid": None,
                "was_reversed": False,
                "errors": [
                    f"Vertex {idx} [{s_lng}, {s_lat}] falls outside Harris County bounds "
                    f"[{min_lng}, {min_lat}, {max_lng}, {max_lat}]."
                ],
            }

    xy, (c_lng, c_lat), _ = _project_ring_to_tangent_meters(snapped)
    signed_area = _signed_shoelace_area(xy)
    area_sq_m = abs(signed_area)
    if area_sq_m < min_area_sq_m:
        return {
            "ok": False,
            "geometry": None,
            "vertex_count": n,
            "area_sq_m": round(area_sq_m, 3),
            "area_sq_ft": round(area_sq_m * SQ_FT_PER_SQ_M, 1),
            "centroid": [round(c_lng, 6), round(c_lat, 6)],
            "was_reversed": False,
            "errors": [
                f"Polygon footprint area ({area_sq_m:.2f} sq m) is below minimum threshold ({min_area_sq_m:.1f} sq m)."
            ],
        }

    was_reversed = False
    if signed_area < 0.0:
        snapped.reverse()
        xy.reverse()
        was_reversed = True

    if len(set(snapped)) != n:
        return {
            "ok": False,
            "geometry": None,
            "vertex_count": n,
            "area_sq_m": round(area_sq_m, 2),
            "area_sq_ft": round(area_sq_m * SQ_FT_PER_SQ_M, 1),
            "centroid": [round(c_lng, 6), round(c_lat, 6)],
            "was_reversed": was_reversed,
            "errors": ["Polygon contains a pinched duplicate non-adjacent vertex (self-touching ring)."],
        }

    for i in range(n):
        p_prev = xy[(i - 1) % n]
        p_curr = xy[i]
        p_next = xy[(i + 1) % n]
        ux, uy = p_curr[0] - p_prev[0], p_curr[1] - p_prev[1]
        vx, vy = p_next[0] - p_curr[0], p_next[1] - p_curr[1]
        u_len = math.hypot(ux, uy)
        v_len = math.hypot(vx, vy)
        if u_len < 1e-6 or v_len < 1e-6:
            return {
                "ok": False,
                "geometry": None,
                "vertex_count": n,
                "area_sq_m": round(area_sq_m, 2),
                "area_sq_ft": round(area_sq_m * SQ_FT_PER_SQ_M, 1),
                "centroid": [round(c_lng, 6), round(c_lat, 6)],
                "was_reversed": was_reversed,
                "errors": ["Polygon contains a zero-length degenerate edge after 6-decimal coordinate rounding."],
            }
        cross = ux * vy - uy * vx
        dot = ux * vx + uy * vy
        if dot < 0.0 and abs(cross) <= 1e-6 * u_len * v_len:
            return {
                "ok": False,
                "geometry": None,
                "vertex_count": n,
                "area_sq_m": round(area_sq_m, 2),
                "area_sq_ft": round(area_sq_m * SQ_FT_PER_SQ_M, 1),
                "centroid": [round(c_lng, 6), round(c_lat, 6)],
                "was_reversed": was_reversed,
                "errors": ["Polygon contains a 180-degree folded collinear edge spike."],
            }

    for i in range(n):
        a1 = xy[i]
        a2 = xy[(i + 1) % n]
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue
            b1 = xy[j]
            b2 = xy[(j + 1) % n]
            if _segments_intersect_planar(a1, a2, b1, b2):
                return {
                    "ok": False,
                    "geometry": None,
                    "vertex_count": n,
                    "area_sq_m": round(area_sq_m, 2),
                    "area_sq_ft": round(area_sq_m * SQ_FT_PER_SQ_M, 1),
                    "centroid": [round(c_lng, 6), round(c_lat, 6)],
                    "was_reversed": was_reversed,
                    "errors": [f"Polygon self-intersects between edge {i} and edge {j}."],
                }

    closed_coords = [[lng, lat] for lng, lat in snapped] + [[snapped[0][0], snapped[0][1]]]
    return {
        "ok": True,
        "geometry": {"type": "Polygon", "coordinates": [closed_coords]},
        "vertex_count": n,
        "area_sq_m": round(area_sq_m, 2),
        "area_sq_ft": round(area_sq_m * SQ_FT_PER_SQ_M, 1),
        "centroid": [round(c_lng, 6), round(c_lat, 6)],
        "was_reversed": was_reversed,
        "errors": [],
    }


def rotate_polygon_tangent_plane(
    geometry_or_ring: Any,
    angle_deg: float,
    pivot: tuple[float, float] | None = None,
) -> list[list[float]]:
    """Rotate a polygon exterior ring around its tangent-plane centroid without WGS84 degree shear (DF-3)."""
    raw_ring = _extract_raw_exterior_ring_coords(geometry_or_ring)
    if not raw_ring:
        return []
    pts: list[tuple[float, float]] = [(float(p[0]), float(p[1])) for p in raw_ring if len(p) >= 2]
    while len(pts) > 1 and pts[-1] == pts[0]:
        pts.pop()
    if len(pts) < 3:
        return []
    xy, (c_lng, c_lat), cos_lat = _project_ring_to_tangent_meters(pts, origin=pivot)
    rad = math.radians(float(angle_deg))
    cos_a = math.cos(rad)
    sin_a = math.sin(rad)
    rotated: list[list[float]] = []
    for x_m, y_m in xy:
        rx = x_m * cos_a - y_m * sin_a
        ry = x_m * sin_a + y_m * cos_a
        n_lng = round(c_lng + rx / (cos_lat * METERS_PER_DEGREE_LAT), 6)
        n_lat = round(c_lat + ry / METERS_PER_DEGREE_LAT, 6)
        rotated.append([n_lng, n_lat])
    rotated.append([rotated[0][0], rotated[0][1]])
    return rotated


def nudge_polygon_tangent_plane(
    geometry_or_ring: Any,
    dx_m: float,
    dy_m: float,
) -> list[list[float]]:
    """Translate a polygon exterior ring by `(dx_m, dy_m)` meters east/north on the local tangent plane."""
    raw_ring = _extract_raw_exterior_ring_coords(geometry_or_ring)
    if not raw_ring:
        return []
    pts: list[tuple[float, float]] = [(float(p[0]), float(p[1])) for p in raw_ring if len(p) >= 2]
    while len(pts) > 1 and pts[-1] == pts[0]:
        pts.pop()
    if not pts:
        return []
    c_lat = sum(p[1] for p in pts) / len(pts)
    cos_lat = max(abs(math.cos(math.radians(c_lat))), 1e-6)
    d_lng = float(dx_m) / (cos_lat * METERS_PER_DEGREE_LAT)
    d_lat = float(dy_m) / METERS_PER_DEGREE_LAT
    shifted = [[round(lng + d_lng, 6), round(lat + d_lat, 6)] for lng, lat in pts]
    shifted.append([shifted[0][0], shifted[0][1]])
    return shifted


def _find_tagged_json_spans(text: str, prefix: str) -> list[tuple[int, int, Any]]:
    """Scan `text` for balanced `[<PREFIX>:{...}]` spans using RFC 8259 JSON decoding (DF-2, DF-4)."""
    if not text or prefix not in text:
        return []
    decoder = json.JSONDecoder()
    spans: list[tuple[int, int, Any]] = []
    pos = 0
    n = len(text)
    plen = len(prefix)
    while pos < n:
        idx = text.find(prefix, pos)
        if idx < 0:
            break
        cur = idx + plen
        while cur < n and text[cur].isspace():
            cur += 1
        if cur >= n or text[cur] not in ("{", "["):
            pos = idx + plen
            continue
        try:
            obj, end_idx = decoder.raw_decode(text, cur)
        except json.JSONDecodeError:
            pos = idx + plen
            continue
        close_idx = end_idx
        while close_idx < n and text[close_idx].isspace():
            close_idx += 1
        if close_idx < n and text[close_idx] == "]":
            spans.append((idx, close_idx + 1, obj))
            pos = close_idx + 1
        else:
            pos = idx + plen
    return spans


def _strip_tagged_json_spans(text: str, prefix: str) -> str:
    """Remove all balanced `[<PREFIX>:{...}]` envelopes from `text` and normalize surrounding spacing."""
    s = str(text or "")
    spans = _find_tagged_json_spans(s, prefix)
    if not spans:
        return s.strip()
    parts: list[str] = []
    last = 0
    for start, end, _ in spans:
        parts.append(s[last:start])
        last = end
    parts.append(s[last:])
    cleaned = "".join(parts)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\s*\|\s*$", "", cleaned)
    return cleaned.strip()


def extract_embedded_geometry_json(source_citation: Any) -> dict[str, Any] | None:
    """Extract and topology-validate the latest `[GEOMETRY_GEOJSON:{...}]` Polygon from a citation string."""
    s = str(source_citation or "")
    spans = _find_tagged_json_spans(s, GEOMETRY_TAG_PREFIX)
    for _, _, obj in reversed(spans):
        res = validate_and_normalize_override_polygon(obj)
        if res["ok"]:
            return res["geometry"]
    return None


def strip_embedded_geometry_tag(source_citation: Any) -> str:
    """Remove all balanced `[GEOMETRY_GEOJSON:{...}]` tags from a citation string (DF-2)."""
    return _strip_tagged_json_spans(str(source_citation or ""), GEOMETRY_TAG_PREFIX)


def encode_citation_with_geometry(source_citation: str, geometry_or_ring: Any) -> str:
    """Strip any prior geometry tag and append a validated compact `[GEOMETRY_GEOJSON:{...}]` suffix."""
    base = strip_embedded_geometry_tag(sanitize_atlas_text(str(source_citation or "")))
    res = validate_and_normalize_override_polygon(geometry_or_ring)
    if not res["ok"]:
        raise ValueError("; ".join(res["errors"]))
    compact_geom = json.dumps(res["geometry"], ensure_ascii=False, separators=(",", ":"))
    tag = f"{GEOMETRY_TAG_PREFIX}{compact_geom}]"
    combined = f"{base} {tag}".strip() if base else tag
    if len(combined) > GOOGLE_SHEETS_CELL_MAX_CHARS:
        raise ValueError(
            f"Encoded citation exceeds Google Sheets {GOOGLE_SHEETS_CELL_MAX_CHARS}-character cell limit "
            f"(got {len(combined)} chars)."
        )
    return combined


def extract_embedded_field_survey_json(source_citation: Any) -> dict[str, Any] | None:
    """Extract the balanced `[FIELD_SURVEY_JSON:{...}]` envelope dict from `source_citation` (DF-4)."""
    s = str(source_citation or "")
    spans = _find_tagged_json_spans(s, FIELD_SURVEY_TAG_PREFIX)
    for _, _, obj in reversed(spans):
        if isinstance(obj, dict):
            return obj
    return None


def strip_embedded_field_survey_tag(source_citation: Any) -> str:
    """Remove all balanced `[FIELD_SURVEY_JSON:{...}]` tags from `source_citation`."""
    return _strip_tagged_json_spans(str(source_citation or ""), FIELD_SURVEY_TAG_PREFIX)


def encode_citation_with_field_survey(source_citation: str, envelope: dict[str, Any]) -> str:
    """Strip prior `[FIELD_SURVEY_JSON:{...}]` tags and append a single compact envelope suffix."""
    base = strip_embedded_field_survey_tag(sanitize_atlas_text(str(source_citation or "")))
    clean_env = _sanitize_payload_strings(dict(envelope or {}))
    compact_env = json.dumps(clean_env, ensure_ascii=False, separators=(",", ":"))
    tag = f"{FIELD_SURVEY_TAG_PREFIX}{compact_env}]"
    combined = f"{base} {tag}".strip() if base else tag
    if len(combined) > GOOGLE_SHEETS_CELL_MAX_CHARS:
        raise ValueError(
            f"Encoded field survey citation exceeds Google Sheets {GOOGLE_SHEETS_CELL_MAX_CHARS}-character limit "
            f"(got {len(combined)} chars)."
        )
    return combined


# ---------------------------------------------------------------------------
# Milestone 4: Dynamic Tax-Year Bins, Stamped Caches, & Ring Normalization
# ---------------------------------------------------------------------------


def compute_demo_years(
    tax_year: int | None = None,
    *,
    start_year: int = 2020,
) -> tuple[int, ...]:
    """Return inclusive annual demolition tracker years `(start_year..tax_year)` (DF-6)."""
    eff_yr = ACTIVE_TAX_YEAR if tax_year is None else int(tax_year)
    if eff_yr < start_year:
        return (start_year,)
    return tuple(range(int(start_year), eff_yr + 1))


def compute_decade_bins(
    max_year: int | None = None,
    *,
    start_decade: int = 1830,
) -> tuple[int, ...]:
    """Return inclusive decade floors `(1830, ..., (max_year // 10) * 10)` covering `2030s+` (DF-6)."""
    eff_max = ACTIVE_TAX_YEAR if max_year is None else int(max_year)
    top_decade = max(int(start_decade), (eff_max // 10) * 10)
    return tuple(range(int(start_decade), top_decade + 10, 10))


def resolve_cama_cache_path(cache_dir: Path, tax_year: int | None = None) -> Path:
    """Return the year-specific compact CAMA pickle cache path (DF-8)."""
    eff_yr = ACTIVE_TAX_YEAR if tax_year is None else int(tax_year)
    return Path(cache_dir) / f"cama_{eff_yr}_compact.pkl"


def save_compact_cama_lookup(
    cache_dir: Path,
    by_acct: dict[str, Any],
    *,
    tax_year: int | None = None,
) -> Path:
    """Persist a stamped v2 compact CAMA lookup envelope via atomic `.tmp` swap (DF-8)."""
    eff_yr = ACTIVE_TAX_YEAR if tax_year is None else int(tax_year)
    pkl_path = resolve_cama_cache_path(cache_dir, eff_yr)
    pkl_path.parent.mkdir(parents=True, exist_ok=True)
    envelope = {
        "schema_version": 2,
        "tax_year": eff_yr,
        "by_acct": by_acct,
    }
    tmp_path = pkl_path.with_name(f".{pkl_path.name}.tmp")
    with open(tmp_path, "wb") as f:
        pickle.dump(envelope, f, protocol=pickle.HIGHEST_PROTOCOL)
    tmp_path.replace(pkl_path)
    return pkl_path


def load_compact_cama_lookup(
    cache_dir: Path,
    *,
    tax_year: int | None = None,
    min_bytes: int = 0,
) -> dict[str, Any] | None:
    """Load a compact CAMA lookup, enforcing exact `tax_year` match on v2 envelopes (DF-8).

    Legacy unstamped bare dicts are accepted strictly when `eff_yr == 2026` on legacy
    `cama_2026_compact.pkl` files. Mismatched `tax_year` envelopes return `None` so callers
    rebuild cleanly instead of reading stale data.
    """
    eff_yr = ACTIVE_TAX_YEAR if tax_year is None else int(tax_year)
    pkl_path = resolve_cama_cache_path(cache_dir, eff_yr)
    if not pkl_path.exists():
        return None
    if min_bytes > 0 and pkl_path.stat().st_size < min_bytes:
        return None
    try:
        with open(pkl_path, "rb") as f:
            payload = pickle.load(f)
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None
    if "schema_version" in payload and "by_acct" in payload:
        try:
            env_yr = int(payload.get("tax_year") or 0)
        except (TypeError, ValueError):
            return None
        if env_yr != eff_yr:
            return None
        inner = payload.get("by_acct")
        return inner if isinstance(inner, dict) else None

    # Legacy unstamped dict compatibility strictly when loading the matching legacy 2026 file.
    if eff_yr == 2026:
        return payload
    return None


def resolve_subdivisions_cache_path(
    cache_dir: Path,
    tax_year: int | None = None,
    *,
    allow_fallback_latest: bool = True,
) -> Path:
    """Return the year-specific `hcad_subdivisions_<YYYY>.pkl` path, falling back cleanly if allowed (DF-6)."""
    cdir = Path(cache_dir)
    eff_yr = ACTIVE_TAX_YEAR if tax_year is None else int(tax_year)
    exact = cdir / f"hcad_subdivisions_{eff_yr}.pkl"
    if exact.exists() or not allow_fallback_latest:
        return exact
    candidates: list[tuple[int, Path]] = []
    for p in cdir.glob("hcad_subdivisions_*.pkl"):
        m = re.fullmatch(r"hcad_subdivisions_(\d{4})\.pkl", p.name)
        if m:
            candidates.append((int(m.group(1)), p))
    if candidates:
        candidates.sort(key=lambda item: (item[0] <= eff_yr, item[0]))
        return candidates[-1][1]
    return exact


def _signed_ring_area2(ring: list[list[float]]) -> float:
    if len(ring) < 3:
        return 0.0
    x0, y0 = ring[0][0], ring[0][1]
    area2 = 0.0
    for i in range(len(ring) - 1):
        dx1, dy1 = ring[i][0] - x0, ring[i][1] - y0
        dx2, dy2 = ring[i + 1][0] - x0, ring[i + 1][1] - y0
        area2 += (dx1 * dy2) - (dx2 * dy1)
    return area2


def normalize_ring_6dp(
    raw_ring: list[Any] | tuple[Any, ...],
    *,
    ccw: bool = True,
) -> list[list[float]]:
    """Round a ring to `<= 6` decimal places, close endpoints, and orient per RFC 7946."""
    pts: list[list[float]] = []
    for pt in raw_ring or ():
        if not isinstance(pt, (list, tuple)) or len(pt) < 2:
            continue
        lon = round(float(pt[0]), 6)
        lat = round(float(pt[1]), 6)
        if not pts or pts[-1][0] != lon or pts[-1][1] != lat:
            pts.append([lon, lat])
    if len(pts) < 3:
        return []
    if pts[0] != pts[-1]:
        pts.append([pts[0][0], pts[0][1]])
    if len(pts) < 4:
        return []
    area2 = _signed_ring_area2(pts)
    if ccw and area2 < 0:
        pts.reverse()
    elif (not ccw) and area2 > 0:
        pts.reverse()
    pts[-1] = [pts[0][0], pts[0][1]]
    return pts


def normalize_polygon_rings_6dp_ccw(geom: dict[str, Any]) -> dict[str, Any]:
    """Normalize a GeoJSON `Polygon` or `MultiPolygon` to `<= 6` decimal places and RFC 7946 winding."""
    if not isinstance(geom, dict):
        return geom
    gtype = geom.get("type")
    coords = geom.get("coordinates")
    if gtype == "Polygon" and isinstance(coords, (list, tuple)):
        norm_rings: list[list[list[float]]] = []
        for r_idx, raw_r in enumerate(coords):
            nr = normalize_ring_6dp(raw_r, ccw=(r_idx == 0))
            if nr:
                norm_rings.append(nr)
            elif r_idx == 0:
                return geom
        return {"type": "Polygon", "coordinates": norm_rings}
    if gtype == "MultiPolygon" and isinstance(coords, (list, tuple)):
        norm_polys: list[list[list[list[float]]]] = []
        for poly_coords in coords:
            if not isinstance(poly_coords, (list, tuple)):
                continue
            norm_rings = []
            for r_idx, raw_r in enumerate(poly_coords):
                nr = normalize_ring_6dp(raw_r, ccw=(r_idx == 0))
                if nr:
                    norm_rings.append(nr)
                elif r_idx == 0:
                    norm_rings = []
                    break
            if norm_rings:
                norm_polys.append(norm_rings)
        if norm_polys:
            return {"type": "MultiPolygon", "coordinates": norm_polys}
    return geom


def update_pmtiles_manifest_v3(
    manifest_path: Path,
    *,
    total_buildings: int | None = None,
    building_shards: list[str] | None = None,
    data_tax_year: int | None = None,
    cache_bust: str | None = None,
    core_pmtiles: str | None = None,
    mode: str | None = None,
    owner_history_prefixes: list[str] | None = None,
    owner_history_span: list[int] | None = None,
    owner_history_accounts: int | None = None,
    permit_shard_prefixes: list[str] | None = None,
    address_shard_prefixes: list[str] | None = None,
) -> dict[str, Any]:
    """Atomically write `pmtiles_manifest.json` preserving schema `"version": 3` (DF-4)."""
    existing: dict[str, Any] = {}
    if manifest_path.exists():
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing = loaded
        except Exception:
            existing = {}

    eff_yr = (
        int(data_tax_year)
        if data_tax_year is not None
        else int(existing.get("data_tax_year") or ACTIVE_TAX_YEAR)
    )
    eff_cb = str(cache_bust or existing.get("cache_bust") or ACTIVE_CACHE_BUST)
    eff_core = str(core_pmtiles or existing.get("core_pmtiles") or "houston_atlas.pmtiles")
    eff_mode = str(mode or existing.get("mode") or "full_county")
    eff_total_bldg = (
        int(total_buildings)
        if total_buildings is not None
        else int(existing.get("total_buildings") or 0)
    )
    eff_shards = (
        list(building_shards)
        if building_shards is not None
        else list(existing.get("building_shards") or [])
    )

    eff_oh_prefixes = (
        sorted({str(p).strip() for p in owner_history_prefixes if str(p).strip()})
        if owner_history_prefixes is not None
        else existing.get("owner_history_prefixes")
    )
    eff_oh_span = (
        [int(x) for x in owner_history_span]
        if owner_history_span is not None
        else existing.get("owner_history_span")
    )
    eff_oh_accounts = (
        int(owner_history_accounts)
        if owner_history_accounts is not None
        else existing.get("owner_history_accounts")
    )
    eff_permit_prefixes = (
        sorted({str(p).strip() for p in permit_shard_prefixes if str(p).strip()})
        if permit_shard_prefixes is not None
        else existing.get("permit_shard_prefixes")
    )
    eff_addr_prefixes = (
        sorted({str(p).strip() for p in address_shard_prefixes if str(p).strip()})
        if address_shard_prefixes is not None
        else existing.get("address_shard_prefixes")
    )

    manifest: dict[str, Any] = {
        "version": 3,
        "mode": eff_mode,
        "cache_bust": eff_cb,
        "data_tax_year": eff_yr,
        "total_buildings": eff_total_bldg,
        "building_shards": eff_shards,
        "core_pmtiles": eff_core,
    }
    if isinstance(eff_oh_prefixes, list):
        manifest["owner_history_prefixes"] = eff_oh_prefixes
    if isinstance(eff_oh_span, list) and len(eff_oh_span) == 2:
        manifest["owner_history_span"] = [int(eff_oh_span[0]), int(eff_oh_span[1])]
    if isinstance(eff_oh_accounts, int) and eff_oh_accounts >= 0:
        manifest["owner_history_accounts"] = eff_oh_accounts
    if isinstance(eff_permit_prefixes, list):
        manifest["permit_shard_prefixes"] = eff_permit_prefixes
    if isinstance(eff_addr_prefixes, list):
        manifest["address_shard_prefixes"] = eff_addr_prefixes

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = manifest_path.with_name(
        f".{manifest_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    try:
        tmp_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(manifest_path)
    finally:
        tmp_path.unlink(missing_ok=True)
    return manifest


@dataclass(frozen=True)
class AnchorRule:
    """Structural anchor replacement rule for lockstep tax-year + cache-bust sync (DF-4)."""

    rel_path: str
    description: str
    pattern: re.Pattern[str]
    expected_min_matches: int
    render_replacement: Callable[[re.Match[str], int, str], str]
    optional: bool = False


REPO_SYNC_ANCHOR_RULES: tuple[AnchorRule, ...] = (
    AnchorRule(
        rel_path="pipeline/atlas_pipeline/schema.py",
        description="schema.py ACTIVE_TAX_YEAR constant",
        pattern=re.compile(r"(^ACTIVE_TAX_YEAR\s*=\s*)\d{4}(\s*$)", re.MULTILINE),
        expected_min_matches=1,
        render_replacement=lambda m, yr, _cb: f"{m.group(1)}{yr}{m.group(2)}",
    ),
    AnchorRule(
        rel_path="pipeline/atlas_pipeline/schema.py",
        description="schema.py ACTIVE_CACHE_BUST constant",
        pattern=re.compile(r'(^ACTIVE_CACHE_BUST\s*=\s*")[^"]+("\s*$)', re.MULTILINE),
        expected_min_matches=1,
        render_replacement=lambda m, _yr, cb: f"{m.group(1)}{cb}{m.group(2)}",
    ),
    AnchorRule(
        rel_path="app/js/filterStore.js",
        description="filterStore.js DEFAULT_ATLAS_MAX_YEAR constant",
        pattern=re.compile(
            r"(export\s+const\s+DEFAULT_ATLAS_MAX_YEAR\s*=\s*)\d{4}(;)"
        ),
        expected_min_matches=1,
        render_replacement=lambda m, yr, _cb: f"{m.group(1)}{yr}{m.group(2)}",
    ),
    AnchorRule(
        rel_path="app/js/curatedEdits.js",
        description="curatedEdits.js MAX_VALID_YEAR constant",
        pattern=re.compile(r"(export\s+const\s+MAX_VALID_YEAR\s*=\s*)\d{4}(;)"),
        expected_min_matches=1,
        render_replacement=lambda m, yr, _cb: f"{m.group(1)}{yr}{m.group(2)}",
    ),
    AnchorRule(
        rel_path="app/js/preservationOrganizer.js",
        description="preservationOrganizer.js ORGANIZER_CACHE_BUST constant",
        pattern=re.compile(r'(export\s+const\s+ORGANIZER_CACHE_BUST\s*=\s*")[^"]+(";)'),
        expected_min_matches=1,
        render_replacement=lambda m, _yr, cb: f"{m.group(1)}{cb}{m.group(2)}",
    ),
    AnchorRule(
        rel_path="app/js/mapController.js",
        description="mapController.js fallback cache_bust literal",
        pattern=re.compile(
            r'(this\.pmtilesManifest\.cache_bust\)\s*\|\|\s*["\'])[^"\']+(["\'])'
        ),
        expected_min_matches=1,
        render_replacement=lambda m, _yr, cb: f"{m.group(1)}{cb}{m.group(2)}",
    ),
    AnchorRule(
        rel_path="app/index.html",
        description="index.html #slider-min-year max attribute",
        pattern=re.compile(
            r'(<input\s+[^>]*id="slider-min-year"[^>]*max=")\d{4}(")',
            re.DOTALL,
        ),
        expected_min_matches=1,
        render_replacement=lambda m, yr, _cb: f"{m.group(1)}{yr}{m.group(2)}",
    ),
    AnchorRule(
        rel_path="app/index.html",
        description="index.html #slider-max-year max attribute",
        pattern=re.compile(
            r'(<input\s+[^>]*id="slider-max-year"[^>]*max=")\d{4}(")',
            re.DOTALL,
        ),
        expected_min_matches=1,
        render_replacement=lambda m, yr, _cb: f"{m.group(1)}{yr}{m.group(2)}",
    ),
    AnchorRule(
        rel_path="app/index.html",
        description="index.html #slider-max-year value attribute",
        pattern=re.compile(
            r'(<input\s+[^>]*id="slider-max-year"[^>]*value=")\d{4}(")',
            re.DOTALL,
        ),
        expected_min_matches=1,
        render_replacement=lambda m, yr, _cb: f"{m.group(1)}{yr}{m.group(2)}",
    ),
    AnchorRule(
        rel_path="app/index.html",
        description="index.html #lbl-corr-suggested-year range hint",
        pattern=re.compile(
            r'(id="lbl-corr-suggested-year">Corrected Year Built <span class="opt-hint">\(1836-)\d{4}(, or <code>0</code> if vacant\)</span></label>)'
        ),
        expected_min_matches=1,
        render_replacement=lambda m, yr, _cb: f"{m.group(1)}{yr}{m.group(2)}",
    ),
    AnchorRule(
        rel_path="app/index.html",
        description="index.html #corr-suggested-year max attribute",
        pattern=re.compile(
            r'(<input\s+[^>]*id="corr-suggested-year"[^>]*max=")\d{4}(")',
            re.DOTALL,
        ),
        expected_min_matches=1,
        render_replacement=lambda m, yr, _cb: f"{m.group(1)}{yr}{m.group(2)}",
    ),
    AnchorRule(
        rel_path="app/index.html",
        description="index.html #scorecard-sort-select expiring option",
        pattern=re.compile(
            r'(<option\s+value="expiring">Sort:\s*Expiring\s+Ch\.\s*42\s+Ordinances\s+\()\d{4}-\d{4}(\)</option>)'
        ),
        expected_min_matches=1,
        render_replacement=lambda m, yr, _cb: f"{m.group(1)}{yr}-{yr + 5}{m.group(2)}",
    ),
    AnchorRule(
        rel_path="app/index.html",
        description="index.html #scorecard-sort-select demos_total option",
        pattern=re.compile(
            r'(<option\s+value="demos_total">Sort:\s*Highest\s+2020-)\d{4}(\s+Demolitions</option>)'
        ),
        expected_min_matches=1,
        render_replacement=lambda m, yr, _cb: f"{m.group(1)}{yr}{m.group(2)}",
    ),
    AnchorRule(
        rel_path="app/index.html",
        description="index.html #sc-th-sunset header label and title",
        pattern=re.compile(
            r'(<th\s+id="sc-th-sunset"[^>]*title="Pre-July 24, 2015 Chapter 42 ordinances reaching 20-year sunset in )\d{4}-\d{4}(">Sunset )\d{2}-\d{2}(</th>)'
        ),
        expected_min_matches=1,
        render_replacement=lambda m, yr, _cb: (
            f"{m.group(1)}{yr}-{yr + 5}{m.group(2)}"
            f"{str(yr)[-2:]}-{str(yr + 5)[-2:]}{m.group(3)}"
        ),
    ),
    AnchorRule(
        rel_path="app/index.html",
        description="index.html #sc-th-demos header title",
        pattern=re.compile(
            r'(<th\s+id="sc-th-demos"[^>]*title="Main-structure teardown parcels permitted 2020)(?:-\d{4}| through [A-Za-z]+ \d{4} YTD)(")'
        ),
        expected_min_matches=1,
        render_replacement=lambda m, yr, _cb: (
            m.group(0)
            if (yr == 2026 and "through Oct 2026 YTD" in m.group(0))
            else f"{m.group(1)}-{yr}{m.group(2)}"
        ),
    ),
    AnchorRule(
        rel_path="app/index.html",
        description="index.html ?v= cache-bust asset URLs",
        pattern=re.compile(r"(\?v=)\d{8}[a-z]?\b"),
        expected_min_matches=2,
        render_replacement=lambda m, _yr, cb: f"{m.group(1)}{cb}",
    ),
    AnchorRule(
        rel_path="app/plat-monitor.html",
        description="plat-monitor.html ?v= cache-bust asset and fetch URLs",
        pattern=re.compile(r"(\?v=)\d{8}[a-z]?\b"),
        expected_min_matches=10,
        render_replacement=lambda m, _yr, cb: f"{m.group(1)}{cb}",
    ),
    AnchorRule(
        rel_path="app/js/app.js",
        description="app.js ?v= cache-bust imports and fetch URLs",
        pattern=re.compile(r"(\?v=)\d{8}[a-z]?\b"),
        expected_min_matches=20,
        render_replacement=lambda m, _yr, cb: f"{m.group(1)}{cb}",
    ),
    AnchorRule(
        rel_path="app/js/mapController.js",
        description="mapController.js ?v= cache-bust imports and fetch URLs",
        pattern=re.compile(r"(\?v=)\d{8}[a-z]?\b"),
        expected_min_matches=12,
        render_replacement=lambda m, _yr, cb: f"{m.group(1)}{cb}",
    ),
    AnchorRule(
        rel_path="tests/unit/data_integrity_and_bundle.test.mjs",
        description="data_integrity_and_bundle.test.mjs expectedVer assertion",
        pattern=re.compile(
            r'(assert\.equal\(\s*expectedVer,\s*["\'])\d{8}[a-z]?(["\']\s*,\s*`Expected active [^`]*cache_bust [\'"])\d{8}[a-z]?([\'"])'
        ),
        expected_min_matches=1,
        render_replacement=lambda m, _yr, cb: f"{m.group(1)}{cb}{m.group(2)}{cb}{m.group(3)}",
        optional=True,
    ),
    AnchorRule(
        rel_path="tests/unit/milestone5_ownership_stewardship.test.mjs",
        description="milestone5_ownership_stewardship.test.mjs manifest.cache_bust assertion",
        pattern=re.compile(
            r'(assert\.equal\(\s*manifest\.cache_bust,\s*["\'])\d{8}[a-z]?(["\']\s*\))'
        ),
        expected_min_matches=1,
        render_replacement=lambda m, _yr, cb: f"{m.group(1)}{cb}{m.group(2)}",
        optional=True,
    ),
)

_CACHE_BUST_QUERY_RE = re.compile(r"(\?v=)\d{8}[a-z]?\b")
_SELECT_DECADE_BLOCK_RE = re.compile(
    r'(<select[^>]*id="select-decade"[^>]*>)(.*?)(</select>)',
    re.DOTALL,
)
_UNKNOWN_DECADE_OPT_RE = re.compile(
    r'^([ \t]*)(<option\s+value="unknown">)',
    re.MULTILINE,
)


_NUMERIC_DECADE_OPT_LINE_RE = re.compile(
    r'^[ \t]*<option\s+value="(\d{4})"\s*>[^<]*</option>\r?\n?',
    re.MULTILINE,
)


def _prune_future_decade_opt(match: re.Match[str], max_keep_decade: int) -> str:
    return "" if int(match.group(1)) > max_keep_decade else match.group(0)


def _ensure_index_html_decades(html_text: str, tax_year: int) -> tuple[str, int]:
    """Ensure `<select id="select-decade">` includes decade options strictly through `(tax_year // 10) * 10`."""
    top_decade = (int(tax_year) // 10) * 10
    m = _SELECT_DECADE_BLOCK_RE.search(html_text)
    if m is None:
        raise ValueError("Anchor missing: app/index.html <select id=\"select-decade\"> block")
    inner = m.group(2)
    max_keep = max(2020, top_decade)
    pruned_inner = _NUMERIC_DECADE_OPT_LINE_RE.sub(
        lambda om: _prune_future_decade_opt(om, max_keep),
        inner,
    )
    unk = _UNKNOWN_DECADE_OPT_RE.search(pruned_inner)
    if unk is None:
        raise ValueError(
            'Anchor missing: app/index.html <option value="unknown"> inside #select-decade'
        )
    indent = unk.group(1)
    insert_lines: list[str] = []
    if top_decade >= 2030:
        for dec in range(2030, top_decade + 1, 10):
            opt_re = re.compile(rf'<option\s+value="{dec}"\s*>')
            if not opt_re.search(pruned_inner):
                insert_lines.append(f'{indent}<option value="{dec}">{dec}s Decade</option>\n')
    if not insert_lines and pruned_inner == inner:
        return html_text, 0
    ins_pos = unk.start()
    new_inner = pruned_inner[:ins_pos] + "".join(insert_lines) + pruned_inner[ins_pos:]
    updated = html_text[: m.start(2)] + new_inner + html_text[m.end(2) :]
    return updated, len(insert_lines)


def _load_repo_sync_target(
    root: Path,
    rel_p: str,
    buffered: dict[Path, str],
    originals: dict[Path, str],
    *,
    optional: bool = False,
) -> tuple[Path, str | None]:
    abs_p = root / rel_p
    if abs_p in buffered:
        return abs_p, buffered[abs_p]
    if not abs_p.exists():
        if optional:
            return abs_p, None
        raise ValueError(f"Required repo sync file missing: {rel_p}")
    txt = abs_p.read_text(encoding="utf-8")
    originals[abs_p] = txt
    buffered[abs_p] = txt
    return abs_p, txt


def sync_repo_tax_year_and_cache_bust(
    repo_root: Path,
    *,
    tax_year: int,
    cache_bust: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Lockstep-sync `ACTIVE_TAX_YEAR`, HTML/JS year anchors, and `?v=<cache_bust>` across `repo_root`.

    Validates EVERY structural anchor across all target files in memory first (failing fast
    with zero partial mutations if any anchor went stale) before writing atomically via `.tmp`.
    """
    yr = int(tax_year)
    if yr < MIN_VALID_YEAR or yr > 2100:
        raise ValueError(f"Invalid tax_year {yr}; expected {MIN_VALID_YEAR}..2100")
    cb = str(cache_bust).strip()
    if not re.fullmatch(r"\d{8}[a-z]?", cb):
        raise ValueError(
            f"Invalid cache_bust {cb!r}; expected YYYYMMDD or YYYYMMDD[a-z]"
        )

    root = Path(repo_root)
    buffered: dict[Path, str] = {}
    originals: dict[Path, str] = {}
    rule_hits: dict[str, int] = {}

    for rule in REPO_SYNC_ANCHOR_RULES:
        abs_p, cur_txt = _load_repo_sync_target(
            root, rule.rel_path, buffered, originals, optional=rule.optional
        )
        if cur_txt is None:
            continue
        matches = list(rule.pattern.finditer(cur_txt))
        match_count = len(matches)
        if match_count < rule.expected_min_matches:
            raise ValueError(
                f"Stale AnchorRule '{rule.description}' in {rule.rel_path}: "
                f"expected >= {rule.expected_min_matches} match(es), got {match_count}"
            )
        pieces: list[str] = []
        last_end = 0
        for m in matches:
            pieces.append(cur_txt[last_end : m.start()])
            pieces.append(rule.render_replacement(m, yr, cb))
            last_end = m.end()
        pieces.append(cur_txt[last_end:])
        buffered[abs_p] = "".join(pieces)
        rule_hits[f"{rule.rel_path}::{rule.description}"] = match_count

    # Ensure dynamic 2030s+ `<option>` rows in app/index.html
    idx_abs, idx_txt = _load_repo_sync_target(root, "app/index.html", buffered, originals)
    assert idx_txt is not None
    idx_updated, inserted_decades = _ensure_index_html_decades(idx_txt, yr)
    buffered[idx_abs] = idx_updated

    # Sweep all remaining `app/js/*.js` modules + test `?v=` imports so DF-12 never drifts
    js_dir = root / "app" / "js"
    swept_files: list[str] = []
    if js_dir.is_dir():
        for js_file in sorted(js_dir.glob("*.js")):
            rel_js = js_file.relative_to(root).as_posix()
            abs_p, cur_txt = _load_repo_sync_target(root, rel_js, buffered, originals)
            assert cur_txt is not None
            next_txt, count = _CACHE_BUST_QUERY_RE.subn(rf"\g<1>{cb}", cur_txt)
            if count > 0:
                buffered[abs_p] = next_txt
                swept_files.append(rel_js)

    for test_dir_rel, glob_pat in (
        ("tests/unit", "*.mjs"),
        ("tests/e2e", "*.py"),
    ):
        tdir = root / test_dir_rel
        if tdir.is_dir():
            for tf in sorted(tdir.glob(glob_pat)):
                rel_tf = tf.relative_to(root).as_posix()
                abs_p, cur_txt = _load_repo_sync_target(
                    root, rel_tf, buffered, originals, optional=True
                )
                if cur_txt is None:
                    continue
                next_txt, count = _CACHE_BUST_QUERY_RE.subn(rf"\g<1>{cb}", cur_txt)
                if count > 0:
                    buffered[abs_p] = next_txt
                    swept_files.append(rel_tf)

    modified_paths: list[str] = []
    pending_writes: list[tuple[Path, str, str]] = []
    for abs_p, next_txt in buffered.items():
        if next_txt != originals[abs_p]:
            modified_paths.append(abs_p.relative_to(root).as_posix())
            pending_writes.append((abs_p, originals[abs_p], next_txt))

    if not dry_run and pending_writes:
        written_rollbacks: list[tuple[Path, Path]] = []
        staged_tmps: list[tuple[Path, Path, Path]] = []
        active_tmp: Path | None = None
        active_rb: Path | None = None
        try:
            for abs_p, orig_txt, next_txt in pending_writes:
                active_tmp = abs_p.with_name(f".{abs_p.name}.sync.tmp")
                active_tmp.write_text(next_txt, encoding="utf-8")
                active_rb = abs_p.with_name(f".{abs_p.name}.rollback.tmp")
                active_rb.write_text(orig_txt, encoding="utf-8")
                staged_tmps.append((active_tmp, active_rb, abs_p))
                active_tmp = None
                active_rb = None
            for tmp_p, rb_p, abs_p in staged_tmps:
                tmp_p.replace(abs_p)
                written_rollbacks.append((rb_p, abs_p))
        except Exception as exc:
            if active_tmp is not None:
                active_tmp.unlink(missing_ok=True)
            if active_rb is not None:
                active_rb.unlink(missing_ok=True)
            failed_rollbacks: list[str] = []
            for rb_p, abs_p in reversed(written_rollbacks):
                try:
                    rb_p.replace(abs_p)
                except OSError:
                    failed_rollbacks.append(abs_p.relative_to(root).as_posix())
            for tmp_p, rb_p, _abs_p in staged_tmps:
                tmp_p.unlink(missing_ok=True)
                rb_p.unlink(missing_ok=True)
            if failed_rollbacks:
                exc.add_note(f"Unrestored live sync targets: {failed_rollbacks}")
            raise
        else:
            for _tmp_p, rb_p, _abs_p in staged_tmps:
                rb_p.unlink(missing_ok=True)

    return {
        "tax_year": yr,
        "cache_bust": cb,
        "dry_run": bool(dry_run),
        "rules_validated": len(rule_hits),
        "rule_hits": rule_hits,
        "inserted_decade_options": inserted_decades,
        "swept_cache_bust_files": swept_files,
        "modified_files": sorted(modified_paths),
    }


# ---------------------------------------------------------------------------
# Milestone 5: Single-Source Ownership Taxonomy, Sec. 25.025 Privacy & Flip Helpers (DF-1, DF-6, DF-7)
# ---------------------------------------------------------------------------

OWNERSHIP_TIER_KEYS: tuple[str, ...] = (
    "homestead",
    "local_small",
    "regional_portfolio",
    "institutional_pe",
    "anonymity_shield",
    "civic_nonprofit",
)

ENRICHED_PERMIT_SHARD_KEYS: tuple[str, ...] = (
    "d",
    "ot",
    "pid",
    "m",
    "co",
    "flip",
)

TPIA_PERMIT_SHARD_KEYS: tuple[str, ...] = (
    "ml",
    "dt_lender",
)

RESIDENTIAL_HOMESTEAD_STATE_CLASSES: frozenset[str] = frozenset({
    "A1",
    "A2",
    "A3",
    "A4",
    "B2",
    "B3",
    "B4",
})

CONFIDENTIAL_OWNER_TOKENS: frozenset[str] = frozenset({
    "CURRENT OWNER",
    "CONFIDENTIAL",
    "CONFIDENTIAL OWNER",
    "PROTECTED",
    "PROTECTED OWNER",
    "WITHHELD",
    "REDACTED",
})

PLACEHOLDER_NON_GRAPH_OWNER_TOKENS: frozenset[str] = frozenset({
    "CURRENT OWNER",
    "CONFIDENTIAL",
    "CONFIDENTIAL OWNER",
    "PROTECTED",
    "PROTECTED OWNER",
    "WITHHELD",
    "REDACTED",
    "ET AL",
    "TRUSTEE",
    "ESTATE OF",
    "UNKNOWN",
    "UNKNOWN OWNER",
    "OWNER",
})

EXTRA_PLACEHOLDER_OWNER_TOKENS: frozenset[str] = frozenset({
    "PUBLIC / UNLISTED",
    "PUBLIC UNLISTED",
    "UNLISTED",
    "OWNER OF RECORD",
    "OWNER OF RECORD HCAD",
    "PENDING",
    "NONE",
    "NA",
    "N A",
    "NULL",
    "0",
})

ALL_PLACEHOLDER_OWNER_TOKENS: frozenset[str] = (
    PLACEHOLDER_NON_GRAPH_OWNER_TOKENS | EXTRA_PLACEHOLDER_OWNER_TOKENS
)

# Keep 1:1 synchronized with CONFIDENTIAL_OWNER_PHRASE_RE and isConfidentialOwnerName in app/js/clerkSearchHelpers.js.
_RE_CONFIDENTIAL_OWNER_PHRASE = re.compile(
    r"\b(?:CURRENT\s+OWNER|CONFIDENTIAL(?:\s+OWNER)?|PROTECTED\s+OWNER|PROTECTED\s+INDIVIDUAL\s+HOMESTEAD|SEC\.?\s*25\.025|WITHHELD|REDACTED)\b",
    re.IGNORECASE,
)
RE_OWNER_CARE_OF_TAIL = re.compile(
    r"\s*(?:(?<!\d)%|\bC\s*/\s*O\b|\bCARE\s+OF\b|\bATTN\s*:?).*$",
    re.IGNORECASE,
)
_CONFIDENTIAL_FAST_SUBSTRINGS: tuple[str, ...] = (
    "CURRENT",
    "CONFIDENT",
    "PROTECTED",
    "WITHHELD",
    "REDACTED",
    "25.025",
)
_RE_CONF_CLEAN_CHARS = re.compile(r"[^A-Za-z0-9\s&/]+")
_RE_PLACEHOLDER_CLEAN_CHARS = re.compile(r"[^A-Za-z0-9\s]+")
_RE_MULTI_WS = re.compile(r"\s+")
_RE_CO_OWNER_SPLIT = re.compile(r"\s*(?:&|/|\bAND\b)\s*")


def is_confidential_owner_token(raw_name: Any) -> bool:
    """Return True if `raw_name` carries a statutory TX Tax Code Sec. 25.025 confidentiality marker (`DF-1`).

    Synchronized 1:1 with `isConfidentialOwnerName` in `app/js/clerkSearchHelpers.js`. Matches
    whole-phrase confidential markers (`CURRENT OWNER`, `CONFIDENTIAL`, `PROTECTED OWNER`,
    `PROTECTED INDIVIDUAL HOMESTEAD`, `SEC. 25.025`, `WITHHELD`, `REDACTED`) and exact co-owner
    segment equality against `CONFIDENTIAL_OWNER_TOKENS` (so bare `'PROTECTED'` is caught without
    false-positively matching entity names such as `'PROTECTED WILDLIFE FOUNDATION'`).
    """
    raw_s = str(raw_name or "").strip()
    if not raw_s:
        return False
    up = raw_s.upper()
    if not any(sub in up for sub in _CONFIDENTIAL_FAST_SUBSTRINGS):
        return False
    stripped = (
        RE_OWNER_CARE_OF_TAIL.sub("", raw_s)
        if ("%" in up or "/" in up or "CARE" in up or "ATTN" in up)
        else raw_s
    )
    if _RE_CONFIDENTIAL_OWNER_PHRASE.search(stripped):
        return True
    cleaned = _RE_MULTI_WS.sub(" ", _RE_CONF_CLEAN_CHARS.sub(" ", stripped).upper()).strip()
    if not cleaned:
        return False
    if cleaned in CONFIDENTIAL_OWNER_TOKENS:
        return True
    for seg in _RE_CO_OWNER_SPLIT.split(cleaned):
        if seg.strip() in CONFIDENTIAL_OWNER_TOKENS:
            return True
    return False


def is_placeholder_owner_token(raw_name: Any) -> bool:
    """Return True if `raw_name` is empty, confidential, or a non-owner placeholder (`DF-1`, `DF-4`).

    Synchronized 1:1 with `isPlaceholderOwnerName` in `app/js/clerkSearchHelpers.js`.
    """
    raw_s = str(raw_name or "").strip()
    if not raw_s:
        return True
    if is_confidential_owner_token(raw_s):
        return True
    up = raw_s.upper()
    has_care_tail = "%" in up or "/" in up or "CARE" in up or "ATTN" in up
    if not has_care_tail and up.isascii() and up.replace(" ", "").isalnum():
        cleaned = " ".join(up.split())
        return (not cleaned) or (cleaned in ALL_PLACEHOLDER_OWNER_TOKENS)
    base_s = RE_OWNER_CARE_OF_TAIL.sub("", raw_s) if has_care_tail else raw_s
    cleaned = _RE_MULTI_WS.sub(" ", _RE_PLACEHOLDER_CLEAN_CHARS.sub(" ", base_s).upper()).strip()
    if not cleaned:
        return True
    return cleaned in ALL_PLACEHOLDER_OWNER_TOKENS

KNOWN_REGISTERED_AGENT_ADDRESS_PREFIXES: tuple[str, ...] = (
    "5900 BALCONES DR",
    "3225 MCLEOD DR",
    "17350 SH 249 STE 220",
    "17350 STATE HWY 249 STE 220",
    "10601 CLARENCE DR",
    "1999 BRYAN ST STE 900",
    "211 E 7TH ST STE 620",
    "701 BRAZOS ST STE 720",
    "1501 S MOPAC EXPY STE 220",
    "815 BRAZOS ST STE 500",
    "1675 S STATE ST",
    "1209 ORANGE ST",
    "251 LITTLE FALLS DR",
    "30 N GOULD ST",
)

KNOWN_REGISTERED_AGENT_NAME_TOKENS: tuple[str, ...] = (
    "REGISTERED AGENT",
    "CORP SERVICE",
    "CORPORATION SERVICE",
    "CT CORPORATION",
    "LEGALINC",
    "INCORP SERVICES",
    "NORTHWEST REGISTERED",
    "COGENCY GLOBAL",
    "CAPITOL CORPORATE",
    "UNITED AGENT GROUP",
    "REPUBLIC REGISTERED",
    "REGISTERED AGENTS INC",
)

KNOWN_TAX_AGENT_TOKENS: tuple[str, ...] = (
    "O'CONNOR",
    "OCONNOR",
    "BETTENCOURT",
    "RYAN LLC",
    "POPP HUTCHESON",
    "MORRISON & HEAD",
    "CANTRELL MCCULLOCH",
    "TEXAS PROTAX",
    "RESIDENTIAL TAX",
    "KE ANDREWS",
    "DUFF & PHELPS",
    "MARVIN F POER",
    "ALTUS GROUP",
)

KNOWN_HOA_MANAGEMENT_TOKENS: tuple[str, ...] = (
    "GOODWIN",
    "CREST MANAGEMENT",
    "CIA SERVICES",
    "SPECTRUM AM",
    "SPECTRUM ASSOC",
    "GRAHAM MANAGEMENT",
    "ASSOCIA",
    "FIRSTSERVICE",
    "STERLING ASI",
    "SCS MANAGEMENT",
    "PRINCIPAL MANAGEMENT",
    "VANMOR",
    "INFRASTRUCTURE SERVICES",
    "LEAD ASSOCIATION",
    "CHAPARRAL MANAGEMENT",
    "KRJ MANAGEMENT",
    "MACO MANAGEMENT",
    "COMMUNITY ASSET",
)

_RE_CARE_OF_PREFIX = re.compile(
    r"^\s*(?:C\s*/\s*O|C-O|CARE\s+OF|ATTN\s*:?|ATTENTION\s*:?|%\s+)\s*(.+)$",
    re.IGNORECASE,
)

_RE_CORP_ENTITY_TOKEN = re.compile(
    r"\b("
    r"LLC|L\s+L\s+C|PLLC|LP|L\s+P|LLP|LTD|LIMITED|INC|INCORPORATED|CORP|CORPORATION"
    r"|COMPANY|PARTNERS|PARTNERSHIP|HOLDINGS|PROPERTIES|PROP|INVESTMENTS|INV"
    r"|VENTURES|CAPITAL|REALTY|REIT|ENTERPRISES|GROUP|DEVELOPMENT|DEV|HOMES"
    r"|RESIDENTIAL|ASSET|ASSETS|FUND|EQUITY|MANAGEMENT|MGMT|ACQUISITIONS|PORTFOLIO"
    r")\b",
    re.IGNORECASE,
)

_RE_TRUST_OR_ESTATE_TOKEN = re.compile(
    r"\b("
    r"TRUST|TRUSTEE|TRUSTEES|TR|ESTATE|REVOCABLE|IRREVOCABLE|LIVING\s+TRUST|FAMILY\s+TRUST"
    r")\b",
    re.IGNORECASE,
)

_RE_CIVIC_OR_HOA_OWNER = re.compile(
    r"\b("
    r"HOA|HOMEOWNERS\s+ASSOC|PROPERTY\s+OWNERS\s+ASSOC|COMMUNITY\s+ASSOC|CIVIC\s+ASSOC"
    r"|CHURCH|CATHEDRAL|CHAPEL|DIOCESE|PARISH|SYNAGOGUE|MOSQUE|TEMPLE|MINISTRIES|BAPTIST|METHODIST"
    r"|CATHOLIC|LUTHERAN|PRESBYTERIAN|EPISCOPAL|UNIVERSITY|COLLEGE|ISD|SCHOOL\s+DIST"
    r"|CITY\s+OF\s+HOUSTON|HARRIS\s+COUNTY|COUNTY\s+OF\s+HARRIS|STATE\s+OF\s+TEXAS|FLOOD\s+CONTROL"
    r"|METRO\s+TRANSIT|HOUSING\s+AUTHORITY|SPORTS\s+AUTHORITY|HOUSTON\s+FIRST|MUD\s+\d+|MUNICIPAL\s+UTILITY"
    r"|LODGE|SCOTTISH\s+RITE|MASONIC|FOUNDATION|MUSEUM|CONSERVANCY|HOSPITAL|MEDICAL\s+CENTER|LIBRARY"
    r")\b",
    re.IGNORECASE,
)

# Keep synchronized with RE_CIVIC_LANDMARK_OR_OWNER in app/js/filterStore.js.
_RE_CIVIC_LANDMARK_NAME = re.compile(
    r"\b("
    r"TOYOTA\s+CENTER|SHELL\s+ENERGY\s+STADIUM|DYNAMO\s+STADIUM|BBVA\s+STADIUM"
    r"|DAIKIN\s+PARK|MINUTE\s+MAID\s+PARK|NRG\s+STADIUM|NRG\s+ASTRODOME|ASTRODOME"
    r"|NRG\s+ARENA|NRG\s+CENTER|TDECU\s+STADIUM|RICE\s+STADIUM|TUDOR\s+FIELDHOUSE"
    r"|HOUSTON\s+SPORTS\s+AUTHORITY|ALLEY\s+THEATRE|WORTHAM\s+THEATER|WORTHAM\s+CENTER"
    r"|JONES\s+HALL|HOBBY\s+CENTER|MILLER\s+OUTDOOR\s+THEATRE|CONVENTION\s+CENTER"
    r"|CITY\s+HALL|COURTHOUSE|POST\s+OFFICE|CUSTOM\s+HOUSE|FIRE\s+STATION|POLICE"
    r"|CHURCH|CATHEDRAL|CHAPEL|PARISH|SYNAGOGUE|MOSQUE|TEMPLE|MINISTRIES|BAPTIST"
    r"|METHODIST|CATHOLIC|LUTHERAN|PRESBYTERIAN|EPISCOPAL|DIOCESE"
    r"|UNIVERSITY|COLLEGE|SCHOOL|ACADEMY|ISD|HOSPITAL|MEDICAL\s+CENTER|CANCER\s+CENTER"
    r"|MUSEUM|MENIL|ROTHKO|BAYOU\s+BEND|LIBRARY|COMMUNITY\s+CENTER|CULTURAL\s+CENTER"
    r"|DISCOVERY\s+GREEN|HERMANN\s+PARK|MEMORIAL\s+PARK|EMANCIPATION\s+PARK|BUFFALO\s+BAYOU"
    r")\b",
    re.IGNORECASE,
)

_RE_COMMERCIAL_SUITE_CUE = re.compile(
    r"\b(?:STE|SUITE|FL|FLOOR|BLDG|RM|PMB|BOX)\s+[A-Z0-9-]+",
    re.IGNORECASE,
)

_POSTAL_ABBREV_SUBS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bP\.?\s*O\.?\s*BOX\b", re.IGNORECASE), "PO BOX"),
    (re.compile(r"\bPOST\s+OFFICE\s+BOX\b", re.IGNORECASE), "PO BOX"),
    (re.compile(r"\bSTREET\b", re.IGNORECASE), "ST"),
    (re.compile(r"\bAVENUE\b", re.IGNORECASE), "AVE"),
    (re.compile(r"\bBOULEVARD\b", re.IGNORECASE), "BLVD"),
    (re.compile(r"\bDRIVE\b", re.IGNORECASE), "DR"),
    (re.compile(r"\bROAD\b", re.IGNORECASE), "RD"),
    (re.compile(r"\bLANE\b", re.IGNORECASE), "LN"),
    (re.compile(r"\bCOURT\b", re.IGNORECASE), "CT"),
    (re.compile(r"\bCIRCLE\b", re.IGNORECASE), "CIR"),
    (re.compile(r"\bPLACE\b", re.IGNORECASE), "PL"),
    (re.compile(r"\bPARKWAY\b", re.IGNORECASE), "PKWY"),
    (re.compile(r"\bPKY\b", re.IGNORECASE), "PKWY"),
    (re.compile(r"\bFREEWAY\b", re.IGNORECASE), "FWY"),
    (re.compile(r"\bHIGHWAY\b", re.IGNORECASE), "HWY"),
    (re.compile(r"\bSPEEDWAY\b", re.IGNORECASE), "SPWY"),
    (re.compile(r"\bTERRACE\b", re.IGNORECASE), "TER"),
    (re.compile(r"\bTRAIL\b", re.IGNORECASE), "TRL"),
    (re.compile(r"\bSQUARE\b", re.IGNORECASE), "SQ"),
    (re.compile(r"\bSUITE\b", re.IGNORECASE), "STE"),
    (re.compile(r"\bAPARTMENT\b", re.IGNORECASE), "STE"),
    (re.compile(r"\bAPT\b", re.IGNORECASE), "STE"),
    (re.compile(r"\bUNIT\b", re.IGNORECASE), "STE"),
    (re.compile(r"#\s*([A-Z0-9-]+)", re.IGNORECASE), r"STE \1"),
    (re.compile(r"\bNORTH\b", re.IGNORECASE), "N"),
    (re.compile(r"\bSOUTH\b", re.IGNORECASE), "S"),
    (re.compile(r"\bEAST\b", re.IGNORECASE), "E"),
    (re.compile(r"\bWEST\b", re.IGNORECASE), "W"),
    (re.compile(r"\bSOUTHWEST\b", re.IGNORECASE), "SW"),
    (re.compile(r"\bNORTHWEST\b", re.IGNORECASE), "NW"),
    (re.compile(r"\bSOUTHEAST\b", re.IGNORECASE), "SE"),
    (re.compile(r"\bNORTHEAST\b", re.IGNORECASE), "NE"),
)


def extract_care_of_and_clean_mail(
    mail_addr_1: str,
    mail_addr_2: str = "",
) -> tuple[str, str, str]:
    """Extract `C/O` or `ATTN` lines from HCAD `mail_addr_1` / `mail_addr_2` into `(addr1, addr2, care_of)`."""
    a1 = sanitize_atlas_text(str(mail_addr_1 or "")).strip()
    a2 = sanitize_atlas_text(str(mail_addr_2 or "")).strip()
    care_of = ""

    m1 = _RE_CARE_OF_PREFIX.match(a1)
    if m1:
        care_of = m1.group(1).strip()
        a1 = a2
        a2 = ""
    else:
        m2 = _RE_CARE_OF_PREFIX.match(a2)
        if m2:
            care_of = m2.group(1).strip()
            a2 = ""
        elif a1 and a2 and not any(ch.isdigit() for ch in a1) and any(ch.isdigit() for ch in a2):
            care_of = a1
            a1 = a2
            a2 = ""

    return a1, a2, care_of


def _normalize_postal_line(raw_line: str) -> str:
    s = sanitize_atlas_text(str(raw_line or "")).upper()
    s = re.sub(r"[.,;:'\"()]", " ", s)
    for pat, rep in _POSTAL_ABBREV_SUBS:
        s = pat.sub(rep, s)
    s = re.sub(r"\bSTE\s+STE\b", "STE", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_postal_billing_key(
    mail_addr_1: str,
    mail_addr_2: str = "",
    mail_city: str = "",
    mail_state: str = "",
    mail_zip: str = "",
) -> tuple[str, str, str]:
    """Normalize an HCAD mailing address into `(billing_key, display_address, care_of)` (DF-1)."""
    a1, a2, care_of = extract_care_of_and_clean_mail(mail_addr_1, mail_addr_2)
    n1 = _normalize_postal_line(a1)
    n2 = _normalize_postal_line(a2)
    if n2 and n2 not in n1:
        street_norm = f"{n1} {n2}".strip()
    else:
        street_norm = n1

    city_norm = re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9\s]", " ", str(mail_city or "").upper())).strip()
    state_norm = re.sub(r"[^A-Z]", "", str(mail_state or "").upper())[:2]
    zip5 = re.sub(r"\D", "", str(mail_zip or ""))[:5]

    if not street_norm or street_norm in ("0", "NONE", "UNKNOWN", "N A", "NA", "UNDELIVERABLE"):
        return "", "", care_of

    billing_key = f"{street_norm}|{city_norm}|{state_norm}"
    disp_parts = [street_norm]
    city_st_zip = " ".join(p for p in (f"{city_norm}," if city_norm else "", state_norm, zip5) if p).strip(", ")
    if city_st_zip:
        disp_parts.append(city_st_zip)
    display_addr = ", ".join(disp_parts)
    return billing_key, display_addr, care_of


def _extract_street_num_and_core(addr: str) -> tuple[str, str]:
    """Return `(house_number, street_core_without_suite)` for situs vs. mailing comparison."""
    norm = _normalize_postal_line(addr)
    norm = re.sub(r"\b(?:STE|FL|FLOOR|BLDG|RM|PMB)\s+[A-Z0-9-]+\b.*$", "", norm).strip()
    m = re.match(r"^(\d+[A-Z]?)\s+(.+)$", norm)
    if not m:
        return "", norm
    st_num = m.group(1)
    st_body = m.group(2).strip()
    st_body = re.sub(
        r"\s+(?:ST|AVE|BLVD|DR|RD|LN|CT|CIR|PL|PKWY|FWY|HWY|WAY|TER|TRL|SQ|LOOP)$",
        "",
        st_body,
    ).strip()
    return st_num, st_body


def is_corporate_or_trust_entity(owner_name: str) -> tuple[bool, bool]:
    """Return `(is_corporate_entity, is_trust_or_estate)` for an HCAD owner name."""
    up = re.sub(r"[.,;:'\"()]", " ", str(owner_name or "").upper())
    up = re.sub(r"\s+", " ", up).strip()
    if not up or up in PLACEHOLDER_NON_GRAPH_OWNER_TOKENS:
        return False, False
    is_corp = bool(_RE_CORP_ENTITY_TOKEN.search(up))
    is_trust = bool(_RE_TRUST_OR_ESTATE_TOKEN.search(up))
    return is_corp, is_trust


def is_registered_agent_billing_cluster(
    billing_key: str,
    owner_name: str = "",
    care_of: str = "",
) -> bool:
    """Return True if a billing address or care-of line matches a known Registered-Agent shield."""
    bk_up = str(billing_key or "").upper()
    street_part = bk_up.split("|", 1)[0].strip()
    for pfx in KNOWN_REGISTERED_AGENT_ADDRESS_PREFIXES:
        if street_part.startswith(pfx):
            return True
    combined_names = f"{owner_name} | {care_of}".upper()
    return any(tok in combined_names for tok in KNOWN_REGISTERED_AGENT_NAME_TOKENS)


def is_tax_protest_agent_care_of(care_of: str, mail_addr_1: str = "") -> bool:
    """Return True if a `C/O` line or mailing address belongs to a third-party property-tax agent."""
    combined = f"{care_of} | {mail_addr_1}".upper()
    return any(tok in combined for tok in KNOWN_TAX_AGENT_TOKENS)


def is_hoa_or_civic_steward(
    state_class: str,
    owner_name: str,
    care_of: str = "",
    subdivision: str = "",
) -> tuple[bool, str]:
    """Return `(is_civic_or_hoa, sub_tier)` for `X1..X4` tax-exempt or HOA common-area reserves."""
    sc = str(state_class or "").strip().upper()
    own_up = str(owner_name or "").strip().upper()
    co_up = str(care_of or "").strip().upper()
    sub_up = str(subdivision or "").strip().upper()

    if sc.startswith("X"):
        return True, "tax_exempt_steward"
    if sc == "C3" or "HOA_RE" in sub_up or "RESERVE" in sub_up and "ASSOC" in own_up:
        return True, "hoa_common"
    if any(tok in f"{own_up} | {co_up}" for tok in KNOWN_HOA_MANAGEMENT_TOKENS) and (
        "ASSOC" in own_up or "HOA" in own_up or sc.startswith(("C", "D", "F"))
    ):
        return True, "hoa_common"
    if _RE_CIVIC_OR_HOA_OWNER.search(own_up):
        if "ASSOC" in own_up or "HOA" in own_up:
            return True, "hoa_common"
        return True, "civic_institution"
    return False, ""


def is_protected_homestead(
    state_class: str,
    owner_name: str,
    mail_addr_1: str,
    situs_addr: str,
    *,
    mail_addr_2: str = "",
    mail_city: str = "",
    mail_state: str = "",
    owner_parcel_count: int = 1,
    cluster_non_situs_commercial_size: int = 0,
) -> tuple[bool, bool]:
    """Pre-clustering Texas Tax Code Sec. 25.025 homestead & owner-occupancy guard (`DF-6`).

    Returns `(is_homestead, is_sec_25_025_confidential)`.
    """
    sc = str(state_class or "").strip().upper()
    is_res_class = sc in RESIDENTIAL_HOMESTEAD_STATE_CLASSES or sc.startswith("A")
    is_conf_token = is_confidential_owner_token(owner_name)

    a1, a2, care_of = extract_care_of_and_clean_mail(mail_addr_1, mail_addr_2)
    m_num, m_core = _extract_street_num_and_core(a1)
    s_num, s_core = _extract_street_num_and_core(situs_addr)
    situs_matches_mail = bool(
        s_num
        and s_core
        and m_num == s_num
        and (m_core == s_core or m_core.startswith(s_core) or s_core.startswith(m_core))
    )

    if is_conf_token and sc.startswith("A1"):
        has_commercial_suite = bool(_RE_COMMERCIAL_SUITE_CUE.search(f"{a1} {a2}"))
        is_commercial_cluster = (
            cluster_non_situs_commercial_size >= 3
            and not situs_matches_mail
            and bool(m_core)
            and (has_commercial_suite or not m_core.startswith("PO BOX"))
        )
        if not is_commercial_cluster:
            return True, True

    if not is_res_class:
        return False, False

    is_corp, _is_trust = is_corporate_or_trust_entity(owner_name)
    if is_corp:
        return False, False
    if is_registered_agent_billing_cluster(
        f"{_normalize_postal_line(a1)}|{str(mail_city or '').upper()}|{str(mail_state or '').upper()}",
        owner_name=owner_name,
        care_of=care_of,
    ):
        return False, False

    if situs_matches_mail:
        return True, is_conf_token

    st_up = str(mail_state or "TX").strip().upper()
    if sc.startswith("A1") and owner_parcel_count <= 1 and st_up in ("", "TX"):
        if not _RE_COMMERCIAL_SUITE_CUE.search(f"{a1} {a2}"):
            return True, is_conf_token

    return False, False


def classify_account_ownership(
    *,
    state_class: str,
    owner_name: str,
    situs_addr: str,
    mail_addr_1: str = "",
    mail_addr_2: str = "",
    mail_city: str = "",
    mail_state: str = "",
    mail_zip: str = "",
    subdivision: str = "",
    owner_parcel_count: int = 1,
    cluster_parcel_count: int = 1,
    cluster_distinct_corps: int = 0,
    cluster_non_situs_commercial_size: int = 0,
    port_id: str = "",
) -> dict[str, Any]:
    """Single-source deterministic 6-tier ownership classifier shared across pipeline stages (`DF-1`)."""
    billing_key, display_mail, care_of = normalize_postal_billing_key(
        mail_addr_1, mail_addr_2, mail_city, mail_state, mail_zip
    )
    tax_agent_co = is_tax_protest_agent_care_of(care_of, mail_addr_1)
    is_conf_token = is_confidential_owner_token(owner_name)

    is_civic, civic_sub = is_hoa_or_civic_steward(
        state_class, owner_name, care_of=care_of, subdivision=subdivision
    )
    if is_civic:
        return {
            "own_tier": "civic_nonprofit",
            "port_id": "",
            "is_protected_homestead": False,
            "is_sec_25_025_confidential": bool(is_conf_token),
            "sub_tier": civic_sub,
            "billing_key": "" if is_conf_token else billing_key,
            "display_mail": "" if is_conf_token else display_mail,
            "care_of": "" if is_conf_token else care_of,
            "is_tax_agent_care_of": False if is_conf_token else tax_agent_co,
        }

    is_hs, is_conf = is_protected_homestead(
        state_class,
        owner_name,
        mail_addr_1,
        situs_addr,
        mail_addr_2=mail_addr_2,
        mail_city=mail_city,
        mail_state=mail_state,
        owner_parcel_count=owner_parcel_count,
        cluster_non_situs_commercial_size=cluster_non_situs_commercial_size,
    )
    if is_hs:
        return {
            "own_tier": "homestead",
            "port_id": "",
            "is_protected_homestead": True,
            "is_sec_25_025_confidential": is_conf,
            "sub_tier": "sec_25_025_confidential" if is_conf else "owner_occupied",
            "billing_key": "",
            "display_mail": "",
            "care_of": "",
            "is_tax_agent_care_of": False,
        }

    own_clean = re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9\s]", " ", str(owner_name or "").upper())).strip()
    is_corp, is_trust = is_corporate_or_trust_entity(owner_name)
    is_reg_agent = is_registered_agent_billing_cluster(billing_key, owner_name=owner_name, care_of=care_of)

    if is_reg_agent or (is_conf_token and not is_hs) or (is_trust and "BLIND" in own_clean):
        return {
            "own_tier": "anonymity_shield",
            "port_id": "",
            "is_protected_homestead": False,
            "is_sec_25_025_confidential": bool(is_conf_token),
            "sub_tier": "registered_agent_shield" if is_reg_agent else "masked_commercial_cluster",
            "billing_key": "" if is_conf_token else billing_key,
            "display_mail": "" if is_conf_token else display_mail,
            "care_of": "" if is_conf_token else care_of,
            "is_tax_agent_care_of": False if is_conf_token else tax_agent_co,
        }

    eff_parcels = max(int(owner_parcel_count or 1), int(cluster_parcel_count or 1))
    st_up = str(mail_state or "TX").strip().upper()
    is_out_of_state = bool(st_up and st_up != "TX")

    if eff_parcels >= 100 or (is_out_of_state and (is_corp or eff_parcels >= 5)):
        return {
            "own_tier": "institutional_pe",
            "port_id": str(port_id or ""),
            "is_protected_homestead": False,
            "is_sec_25_025_confidential": False,
            "sub_tier": "out_of_state_platform" if is_out_of_state else "institutional_100plus",
            "billing_key": billing_key,
            "display_mail": display_mail,
            "care_of": care_of,
            "is_tax_agent_care_of": tax_agent_co,
        }

    if eff_parcels >= 5 or int(cluster_distinct_corps or 0) >= 2:
        return {
            "own_tier": "regional_portfolio",
            "port_id": str(port_id or ""),
            "is_protected_homestead": False,
            "is_sec_25_025_confidential": False,
            "sub_tier": "multi_llc_cluster" if int(cluster_distinct_corps or 0) >= 2 else "regional_5plus",
            "billing_key": billing_key,
            "display_mail": display_mail,
            "care_of": care_of,
            "is_tax_agent_care_of": tax_agent_co,
        }

    return {
        "own_tier": "local_small",
        "port_id": str(port_id or ""),
        "is_protected_homestead": False,
        "is_sec_25_025_confidential": False,
        "sub_tier": "small_llc" if is_corp else "local_individual",
        "billing_key": billing_key,
        "display_mail": display_mail,
        "care_of": care_of,
        "is_tax_agent_care_of": tax_agent_co,
    }


def infer_fallback_ownership_tier(
    use_category: str = "",
    owner_name: str = "",
    landmark_name: str = "",
    address: str = "",
    current_tier: str = "",
) -> str:
    """Resolve a deterministic 6-tier ownership key when an HCAD account is missing/retired or a feature is an override.

    Ensures non-residential structures (`Civic / Institutional`, `Commercial`, `Industrial`, `Vacant / Exempt`)
    and civic/public stadiums, churches, museums, schools, and parks never falsely default to `homestead`.
    Owner-occupied `Multi-Family` structures (duplexes/triplexes/fourplexes, HCAD `B2`-`B4`) with an explicit
    `current_tier == 'homestead'` from HCAD situs-mail matching preserve `'homestead'`.
    """
    use_clean = str(use_category or "").strip()
    cur_clean = str(current_tier or "").strip().lower()
    combined_text = f"{owner_name or ''} | {landmark_name or ''} | {address or ''}".upper()

    if use_clean == "Civic / Institutional" or _RE_CIVIC_LANDMARK_NAME.search(combined_text):
        if (
            cur_clean in ("institutional_pe", "regional_portfolio")
            and use_clean != "Civic / Institutional"
            and not _RE_CIVIC_LANDMARK_NAME.search(combined_text)
        ):
            return cur_clean
        return "civic_nonprofit"

    is_civic, _sub = is_hoa_or_civic_steward("", owner_name)
    if is_civic:
        return "civic_nonprofit"

    if cur_clean in OWNERSHIP_TIER_KEYS and cur_clean != "homestead":
        return cur_clean

    is_corp, _is_trust = is_corporate_or_trust_entity(owner_name)

    # Preserve genuine HCAD-verified owner-occupied duplex/triplex/fourplex (`Multi-Family`) or `Residential` homesteads
    # strictly when the owner is not a corporate entity (LLCs/LPs/Corps cannot hold a TX homestead).
    if cur_clean == "homestead" and use_clean in ("Residential", "Multi-Family") and not is_corp:
        return "homestead"

    # Non-residential structures or corporate-owned structures without a matched HCAD record:
    if use_clean in ("Commercial", "Industrial", "Vacant / Exempt", "Multi-Family") or is_corp:
        if owner_name:
            res = classify_account_ownership(
                state_class="F1",
                owner_name=str(owner_name),
                situs_addr=str(address or ""),
            )
            tier = str(res.get("own_tier") or "local_small")
            if tier != "homestead":
                return tier
        return "local_small"

    if owner_name:
        res = classify_account_ownership(
            state_class="A1",
            owner_name=str(owner_name),
            situs_addr=str(address or ""),
        )
        return str(res.get("own_tier") or "homestead")

    return "homestead"


def compute_pre_demolition_flip_days(
    deed_dos_yyyymmdd: str,
    demo_permit_date_raw: str,
    *,
    max_days: int = 365,
) -> int | None:
    """Compute days between a complete-date deed transfer (`YYYYMMDD`) and a main-structure demolition permit (`DF-7`).

    Rejects `YYYY0000` partial dates, `00000000`, and `1899`/`1900` sentinel dates.
    """
    dos_s = str(deed_dos_yyyymmdd or "").strip()
    if len(dos_s) != 8 or not dos_s.isdigit():
        return None
    d_yr = int(dos_s[:4])
    d_mo = int(dos_s[4:6])
    d_dy = int(dos_s[6:8])
    if d_yr <= 1900 or d_mo < 1 or d_mo > 12 or d_dy < 1 or d_dy > 31:
        return None
    try:
        deed_dt = datetime.date(d_yr, d_mo, d_dy)
    except ValueError:
        return None

    p_iso, _p_yr = normalize_hcad_date(
        str(demo_permit_date_raw or "").strip(),
        allow_fallback=False,
        min_year=1901,
        max_year=MAX_VALID_YEAR,
    )
    if not p_iso or len(p_iso) != 10:
        return None
    try:
        p_yr_i = int(p_iso[:4])
        p_mo_i = int(p_iso[5:7])
        p_dy_i = int(p_iso[8:10])
        if p_yr_i <= 1900 or p_mo_i < 1 or p_mo_i > 12 or p_dy_i < 1 or p_dy_i > 31:
            return None
        permit_dt = datetime.date(p_yr_i, p_mo_i, p_dy_i)
    except ValueError:
        return None

    delta_days = (permit_dt - deed_dt).days
    if 0 <= delta_days <= int(max_days):
        return int(delta_days)
    return None


ALLOWED_PREWAR_FOUNDATIONS: frozenset[str] = frozenset(
    {
        "Crawl Space",
        "Piers 8 ft or Greater",
        "Piers 8 ft or Greater with Enclosure",
        "Full Basement",
        "Partial Basement",
        "Basement",
        "Masonry Bearing",
        "Reinforced Concrete",
        "Wood or Light Steel",
    }
)


def load_bld1_structural_flags(
    bld_zip_path: Path,
    target_hcads: set[str],
) -> dict[str, Any]:
    """Scan HCAD `Real_building_land.zip` for Building 1 structural flags & multi-building years.

    Parses both `building_res.txt` / `building_other.txt` (including commercial col 6
    `Masonry Bearing` / `Reinforced Concrete` / `Wood or Light Steel` structural descriptors)
    and `structural_elem1.txt` / `structural_elem2.txt` (`FND`, `SL` / `SLAB`, and
    `CAD == 'New / Rebuilt'`) so `audit_historical_accuracy.py` and `build_1927_cutoff_triage.py`
    enforce 1:1 structural classification without silent exception swallowing.
    """
    fnds_by_hcad: dict[str, set[str]] = {}
    slab_hcads: set[str] = set()
    rebuilt_hcads: set[str] = set()
    bldg_years_by_hcad: dict[str, list[int]] = {}
    meta_by_hcad: dict[str, tuple[int, int, str, bool]] = {}

    if not target_hcads or not bld_zip_path.exists():
        return {
            "fnds_by_hcad": fnds_by_hcad,
            "slab_hcads": slab_hcads,
            "rebuilt_hcads": rebuilt_hcads,
            "bldg_years_by_hcad": bldg_years_by_hcad,
            "meta_by_hcad": meta_by_hcad,
        }

    with zipfile.ZipFile(bld_zip_path, "r") as zf:
        names = set(zf.namelist())
        for bfile, i_erect, i_eff, i_rem, is_comm_tbl in (
            ("building_res.txt", 12, 13, 14, False),
            ("building_other.txt", 14, 15, 16, True),
        ):
            if bfile not in names:
                continue
            with zf.open(bfile) as fh:
                fh.readline()
                for raw_b in fh:
                    acct_p = raw_b[:16].decode("latin1", errors="ignore").split("\t", 1)[0].strip()
                    if acct_p not in target_hcads:
                        continue
                    pp = raw_b.decode("latin1", errors="ignore").rstrip("\r\n").split("\t")
                    if len(pp) <= i_rem:
                        continue
                    try:
                        bnum = int(pp[2].strip() or "0")
                    except ValueError:
                        bnum = 0
                    try:
                        ey = int(pp[i_erect].strip() or "0")
                    except ValueError:
                        ey = 0
                    try:
                        eff_y = int(pp[i_eff].strip() or "0")
                    except ValueError:
                        eff_y = 0
                    try:
                        rem_y = int(pp[i_rem].strip() or "0")
                    except ValueError:
                        rem_y = 0
                    sdscr = pp[6].strip() if len(pp) > 6 else ""
                    if ey >= MIN_VALID_YEAR:
                        bldg_years_by_hcad.setdefault(acct_p, []).append(ey)
                    if bnum == 1 or acct_p not in meta_by_hcad:
                        meta_by_hcad[acct_p] = (eff_y, rem_y, sdscr, is_comm_tbl)
                        if is_comm_tbl and sdscr in (
                            "Masonry Bearing",
                            "Reinforced Concrete",
                            "Wood or Light Steel",
                        ):
                            fnds_by_hcad.setdefault(acct_p, set()).add(sdscr)

        for sfile in ("structural_elem1.txt", "structural_elem2.txt"):
            if sfile not in names:
                continue
            with zf.open(sfile) as fh:
                fh.readline()
                for raw_b in fh:
                    acct_p = raw_b[:16].decode("latin1", errors="ignore").split("\t", 1)[0].strip()
                    if acct_p not in target_hcads:
                        continue
                    pp = raw_b.decode("latin1", errors="ignore").rstrip("\r\n").split("\t")
                    if len(pp) < 7:
                        continue
                    try:
                        bnum = int(pp[1].strip() or "0")
                    except ValueError:
                        continue
                    if bnum != 1:
                        continue
                    tp = pp[4].strip()
                    cdscr = pp[6].strip()
                    if tp == "SL" or "SLAB" in cdscr.upper():
                        slab_hcads.add(acct_p)
                    elif tp == "CAD" and cdscr == "New / Rebuilt":
                        rebuilt_hcads.add(acct_p)
                    elif tp == "FND" and cdscr:
                        fnds_by_hcad.setdefault(acct_p, set()).add(cdscr)

    return {
        "fnds_by_hcad": fnds_by_hcad,
        "slab_hcads": slab_hcads,
        "rebuilt_hcads": rebuilt_hcads,
        "bldg_years_by_hcad": bldg_years_by_hcad,
        "meta_by_hcad": meta_by_hcad,
    }


def collect_overridden_hcad_accounts(overrides: dict[str, Any]) -> set[str]:
    """Extract normalized 13-digit HCAD parcel accounts from bare and `<hcad>#<slug>` override entries."""
    out: set[str] = set()
    for raw_key, ov in (overrides or {}).items():
        cand = ""
        if isinstance(ov, dict):
            cand = str(ov.get("hcad_num") or "").strip()
        if not cand:
            cand = str(raw_key or "").split("#", 1)[0].strip()
        if len(cand) == 13 and cand.isdigit():
            out.add(cand)
    return out


_EXPLICIT_ATLAS_HCAD_JSON_KEYS: frozenset[str] = frozenset({
    "hcad_num",
    "HCAD_NUM",
    "USER_HCAD_NUM",
    "hcad",
    "hcadAccount",
    "hcadAccounts",
    "related_hcads",
    "site_hcad_num",
    "site_hcad_nums",
    "modern_hcad_num",
})


def normalize_standing_hcad_token(raw_val: Any) -> str:
    """Canonical fast-path 13-digit HCAD account token normalizer across JSON/TSV roll inputs."""
    s = str(raw_val or "").strip()
    if not s:
        return ""
    head = s.split("#", 1)[0].strip()
    if len(head) == 13 and head.isdigit() and head != "0000000000000":
        return head
    digits = re.sub(r"\D", "", head)
    if 10 <= len(digits) <= 13:
        z = digits.zfill(13)
        if z != "0000000000000":
            return z
    return ""


def _walk_standing_hcads_in_json(node: Any, out: set[str]) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            ks = str(k).strip()
            kh = ks.split("#", 1)[0].strip()
            if len(kh) == 13 and kh.isdigit() and kh != "0000000000000":
                out.add(kh)
            if ks in _EXPLICIT_ATLAS_HCAD_JSON_KEYS:
                if isinstance(v, str):
                    for tok in re.split(r"[,;\s|]+", v):
                        nh = normalize_standing_hcad_token(tok)
                        if nh:
                            out.add(nh)
                elif isinstance(v, list):
                    for item in v:
                        if isinstance(item, str):
                            nh = normalize_standing_hcad_token(item)
                            if nh:
                                out.add(nh)
            elif ks == "works" and isinstance(v, list):
                for row in v:
                    if isinstance(row, list) and row and isinstance(row[0], str):
                        nh = normalize_standing_hcad_token(row[0])
                        if nh:
                            out.add(nh)
                    elif isinstance(row, dict):
                        _walk_standing_hcads_in_json(row, out)
            elif isinstance(v, (dict, list)):
                _walk_standing_hcads_in_json(v, out)
    elif isinstance(node, list):
        for item in node:
            if isinstance(item, (dict, list)):
                _walk_standing_hcads_in_json(item, out)


def collect_standing_and_referenced_atlas_hcads(
    data_dir: Path,
    cache_dir: Path | None = None,
) -> set[str]:
    """Collect all 13-digit HCAD accounts mapped or referenced across standing footprints & Atlas JSONs.

    Never scans raw floats or epoch timestamps, guaranteeing zero synthetic prefix pollution.
    """
    ddir = Path(data_dir)
    hcads: set[str] = set()

    if cache_dir is not None:
        aligned_dir = Path(cache_dir) / "aligned_ndjson"
        if aligned_dir.is_dir():
            hcad_re = re.compile(rb'"hcad_num"\s*:\s*"(\d{10,13})"')
            for seq_p in sorted(aligned_dir.glob("buildings_*.geojsonseq")):
                with open(seq_p, "rb") as fh:
                    for line in fh:
                        for m in hcad_re.finditer(line):
                            nh = normalize_standing_hcad_token(m.group(1).decode("ascii"))
                            if nh:
                                hcads.add(nh)

    skip_top_files = {"pmtiles_manifest.json", "stats_summary.json"}
    if ddir.is_dir():
        for entry in sorted(ddir.iterdir()):
            if not entry.is_file() or entry.name in skip_top_files:
                continue
            if entry.suffix.lower() not in (".json", ".geojson"):
                continue
            try:
                doc = json.loads(entry.read_text(encoding="utf-8"))
            except Exception:
                continue
            _walk_standing_hcads_in_json(doc, hcads)

        exp_dir = ddir / "export_cache"
        if exp_dir.is_dir():
            for ep in sorted(exp_dir.glob("*.json")):
                try:
                    edoc = json.loads(ep.read_text(encoding="utf-8"))
                except Exception:
                    continue
                _walk_standing_hcads_in_json(edoc, hcads)

    return hcads


def match_earliest_platted_subdivisions(
    points_xy: list[tuple[float, float]],
    platted_subdivisions_features: list[dict[str, Any]],
    *,
    min_year: int = MIN_VALID_YEAR,
    max_year: int = 9999,
) -> list[tuple[str, int, bool, str]]:
    """Vectorized earliest-subdivision spatial join (`(name, year, is_exact, basis)`) for `(x, y)` points.

    Sanitizes polygon topology via `shapely.validation.make_valid` and uses `STRtree.query(...,
    predicate='intersects')` so boundary-collinear parcel centroids match consistently across
    `audit_historical_accuracy.py` and `build_1927_cutoff_triage.py` without bare exception swallowing.
    """
    n_pts = len(points_xy)
    if n_pts == 0 or not platted_subdivisions_features:
        return [("", 0, False, "")] * n_pts

    import numpy as np
    import shapely
    from shapely.geometry import shape
    from shapely.strtree import STRtree
    from shapely.validation import make_valid

    xs = np.asarray([pt[0] for pt in points_xy], dtype=np.float64)
    ys = np.asarray([pt[1] for pt in points_xy], dtype=np.float64)
    pts_tree = STRtree(shapely.points(xs, ys))

    names = [""] * n_pts
    years = [0] * n_pts
    exacts = [False] * n_pts
    bases = [""] * n_pts

    for pfeat in platted_subdivisions_features:
        if not isinstance(pfeat, dict):
            continue
        pp = pfeat.get("properties") or {}
        try:
            py = int(pp.get("estimated_plat_year") or 0)
        except (ValueError, TypeError):
            continue
        if not (min_year <= py <= max_year):
            continue
        pg = pfeat.get("geometry")
        if not isinstance(pg, dict) or not pg.get("type") or not pg.get("coordinates"):
            continue
        sg = shape(pg)
        if not sg.is_valid:
            sg = make_valid(sg)
        if sg.is_empty:
            continue
        pname = str(pp.get("name") or "").strip()
        pexact = bool(pp.get("plat_year_is_exact"))
        pbasis = str(pp.get("plat_year_basis") or "").strip()
        for idx_h in pts_tree.query(sg, predicate="intersects"):
            ii = int(idx_h)
            if years[ii] == 0 or py < years[ii]:
                names[ii] = pname
                years[ii] = py
                exacts[ii] = pexact
                bases[ii] = pbasis

    return list(zip(names, years, exacts, bases))


CONTENTDM_CITYDIR_COLLECTION_URL = "https://cdm17006.contentdm.oclc.org/digital/collection/citydir"


def build_citydir_search_url(address: str, fallback_hcad: str = "") -> str:
    """Build a percent-encoded ContentDM `citydir` search URL matching JS `encodeURIComponent`.

    Encodes path/fragment delimiters (`'/'` in `'1/2'` or `' / '`, `'#'` in unit suffixes, and `'&'`)
    via `urllib.parse.quote(..., safe='')` so `/search/searchterm/<term>` routes never split or truncate.
    """
    raw_query = str(address or "").strip() or str(fallback_hcad or "").strip()
    if not raw_query:
        return CONTENTDM_CITYDIR_COLLECTION_URL
    encoded = urllib.parse.quote(raw_query, safe="")
    return f"{CONTENTDM_CITYDIR_COLLECTION_URL}/search/searchterm/{encoded}"


def resolve_citydir_cdm_url(dir_rec: dict[str, Any] | None, fallback_url: str) -> tuple[int, str]:
    """Resolve validated ContentDM integer page pointer (`p`) and direct item URL.

    Never interpolates non-integer OCR citation strings (`c`/`x`) into `/id/<ptr>/rec/1`.
    """
    if not isinstance(dir_rec, dict):
        return 0, fallback_url
    raw_ptr = dir_rec.get("p")
    ptr_int = (
        int(raw_ptr)
        if isinstance(raw_ptr, int) and not isinstance(raw_ptr, bool)
        else (int(str(raw_ptr).strip()) if str(raw_ptr or "").strip().isdigit() else 0)
    )
    if ptr_int > 0:
        return ptr_int, f"{CONTENTDM_CITYDIR_COLLECTION_URL}/id/{ptr_int}/rec/1"
    return 0, fallback_url


def compress_deterministic_gzip_json(shard_obj: dict[str, Any]) -> bytes:
    """Serialize `shard_obj` to single-line sorted UTF-8 JSON and gzip with `mtime=0` and empty filename (`DF-7`)."""
    raw_json = json.dumps(
        shard_obj,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    if "\u2014" in raw_json or "\u2013" in raw_json:
        raise ValueError("Forbidden U+2014/U+2013 dash detected in gzipped shard payload.")
    return gzip.compress(raw_json.encode("utf-8"), compresslevel=9, mtime=0)


def sanitize_json_structure_dashes(obj: Any) -> Any:
    """Recursively normalize any U+2014 or U+2013 dash characters inside JSON-compatible containers."""
    if isinstance(obj, str):
        if "\u2014" in obj or "\u2013" in obj:
            return sanitize_atlas_text(obj)
        return obj
    if isinstance(obj, list):
        return [sanitize_json_structure_dashes(item) for item in obj]
    if isinstance(obj, tuple):
        return [sanitize_json_structure_dashes(item) for item in obj]
    if isinstance(obj, dict):
        return {
            (sanitize_atlas_text(k) if isinstance(k, str) and ("\u2014" in k or "\u2013" in k) else k): sanitize_json_structure_dashes(v)
            for k, v in obj.items()
        }
    return obj


def read_json_or_gzip_path(path: Path) -> dict[str, Any]:
    """Read a JSON object from either a `.json.gz` (RFC 1952 gzip) or plain `.json` file."""
    p = Path(path)
    if not p.exists():
        return {}
    raw_bytes = p.read_bytes()
    if not raw_bytes:
        return {}
    if len(raw_bytes) >= 2 and raw_bytes[0] == 0x1F and raw_bytes[1] == 0x8B:
        txt = gzip.decompress(raw_bytes).decode("utf-8")
    else:
        txt = raw_bytes.decode("utf-8")
    parsed = json.loads(txt)
    if not isinstance(parsed, dict):
        raise ValueError(f"Expected JSON object in {p}, got {type(parsed).__name__}")
    return parsed


def read_json_or_gzip_shard(shard_dir: Path, stem: str) -> dict[str, Any]:
    """Load `<shard_dir>/<stem>.json.gz` if present, falling back transparently to `<stem>.json`."""
    d = Path(shard_dir)
    clean_stem = stem[:-8] if stem.endswith(".json.gz") else (stem[:-5] if stem.endswith(".json") else stem)
    gz_path = d / f"{clean_stem}.json.gz"
    if gz_path.exists():
        return read_json_or_gzip_path(gz_path)
    json_path = d / f"{clean_stem}.json"
    if json_path.exists():
        return read_json_or_gzip_path(json_path)
    return {}


def write_deterministic_gzip_json_shard(
    target_path: Path,
    shard_obj: dict[str, Any],
    *,
    unlink_legacy_json: bool = True,
    sanitize_dashes: bool = False,
) -> int:
    """Atomically write `<stem>.json.gz` with deterministic RFC 1952 headers (`mtime=0`, no FNAME)."""
    p = Path(target_path)
    if p.name.endswith(".json"):
        gz_path = p.with_name(f"{p.name}.gz")
        legacy_json_path = p
    elif p.name.endswith(".json.gz"):
        gz_path = p
        legacy_json_path = p.with_name(p.name[:-3])
    else:
        gz_path = p.with_name(f"{p.name}.json.gz")
        legacy_json_path = p.with_name(f"{p.name}.json")

    payload_obj = sanitize_json_structure_dashes(shard_obj) if sanitize_dashes else shard_obj
    compressed = compress_deterministic_gzip_json(payload_obj)
    gz_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = gz_path.with_name(
        f".{gz_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    try:
        tmp_path.write_bytes(compressed)
        tmp_path.replace(gz_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    if unlink_legacy_json and legacy_json_path != gz_path and legacy_json_path.exists():
        legacy_json_path.unlink(missing_ok=True)
    return len(compressed)


def iter_json_or_gzip_shard_keys(
    shard_dir: Path,
    prefix: str,
    key_pattern: str = r"\d+",
) -> list[tuple[str, Path]]:
    """Discover `<prefix><key>.json.gz` (preferred) and legacy `<prefix><key>.json` shards in sorted key order."""
    d = Path(shard_dir)
    if not d.exists():
        return []
    re_gz = re.compile(rf"^{re.escape(prefix)}({key_pattern})\.json\.gz$")
    re_json = re.compile(rf"^{re.escape(prefix)}({key_pattern})\.json$")
    by_key: dict[str, Path] = {}
    for f in sorted(d.iterdir()):
        if not f.is_file() or f.name.startswith("."):
            continue
        m_gz = re_gz.fullmatch(f.name)
        if m_gz:
            by_key[m_gz.group(1)] = f
            continue
        m_js = re_json.fullmatch(f.name)
        if m_js and m_js.group(1) not in by_key:
            by_key[m_js.group(1)] = f
    return [(k, by_key[k]) for k in sorted(by_key.keys())]


def sum_json_or_gzip_shard_bytes(
    shard_dir: Path,
    prefix: str,
    key_pattern: str = r"\d+",
) -> int:
    """Sum on-disk sizes across all `.json.gz` and legacy `.json` shards matching `prefix`."""
    d = Path(shard_dir)
    if not d.exists():
        return 0
    re_any = re.compile(rf"^{re.escape(prefix)}(?:{key_pattern})\.json(?:\.gz)?$")
    return sum(
        f.stat().st_size
        for f in d.iterdir()
        if f.is_file() and not f.name.startswith(".") and re_any.fullmatch(f.name)
    )


RE_RP_FLEX = re.compile(
    r"^(?:\d)?(?:RP[\s\-_]*|P[\s\-_]+)(20(?:1[6-9]|[2-9]\d))(?:[\s\-_]+\1)?[\s\-_]*0*([1-9]\d{0,6})$",
    re.IGNORECASE,
)
RE_YEAR_RP_SWAP = re.compile(
    r"^(20(?:1[6-9]|[2-9]\d))[\s\-_]+RP[\s\-_]*0*([1-9]\d{0,6})$",
    re.IGNORECASE,
)
RE_BARE_YEAR_HYPHEN = re.compile(
    r"^(20(?:1[6-9]|[2-9]\d))[\s\-_]+0*([1-9]\d{0,6})$"
)
RE_LETTER_CODE = re.compile(r"^([A-Z])([0-9O]{6})$")
RE_FILM_CODE = re.compile(r"^(\d{3}-\d{2}-\d{4})$")
RE_PROBATE = re.compile(
    r"^(?:DKT|DOCKET|CASE|PR|PROBATE)[\s\.#:\-]*([0-9A-Z\-]{3,14})$",
    re.IGNORECASE,
)
RE_VOL_PAGE = re.compile(
    r"^(?:VOL\.?\s*|V)(\d{1,5}|[A-Z]{1,3})[\s,/\-]*(?:PG\.?\s*|PAGE\s*|P)(\d{1,5})(?:DR|MR)?$",
    re.IGNORECASE,
)


def parse_dos_yyyymmdd(
    raw_dos: str,
    raw_yr: str,
    *,
    max_year: int | None = None,
) -> str:
    """Normalize HCAD date of sale (MM/DD/YYYY or YYYY-MM-DD) to 8-digit YYYYMMDD."""
    eff_max = MAX_VALID_YEAR if max_year is None else int(max_year)
    iso_dt, yr = normalize_hcad_date(
        raw_dos,
        raw_yr,
        allow_fallback=True,
        max_year=eff_max,
    )
    if iso_dt:
        return iso_dt.replace("-", "")
    if yr > 0:
        return f"{yr:04d}0000"
    return "00000000"


def normalize_hcad_clerk_instrument(
    clerk_yr: str,
    clerk_id: str,
    *,
    max_year: int | None = None,
) -> str | None:
    """Normalize HCAD (clerk_yr, clerk_id) to a compact, verified Clerk lookup token."""
    eff_max = max(2030, MAX_VALID_YEAR if max_year is None else int(max_year))
    cid = (clerk_id or "").strip().upper()
    if not cid or cid in ("0", "NONE", "N/A", "NA", "UNKNOWN", "UNKN", "NULL"):
        return None
    cid_clean = re.sub(r"\s+", " ", cid)
    yr_s = (clerk_yr or "").strip()
    yr_int = int(yr_s) if yr_s.isdigit() else 0

    # 1. Film Code (###-##-####)
    m_fc = RE_FILM_CODE.match(cid_clean)
    if m_fc:
        return f"FC:{m_fc.group(1)}"

    # 2. 1972-2006 Letter + 6 digits (e.g. Y051716, P202281; fixing O->0 typos).
    # Evaluated before RE_RP_FLEX so 1993-1994 "P20xxxx" codes are never parsed as RP-20xx-SEQ.
    cid_nospace = cid_clean.replace(" ", "")
    m_let = RE_LETTER_CODE.match(cid_nospace)
    if m_let:
        digits = m_let.group(2).replace("O", "0")
        if digits != "000000":
            return f"{m_let.group(1)}{digits}"

    # 3. Explicit RP-YYYY-NNNNNN variants (2016+)
    for rx in (RE_RP_FLEX, RE_YEAR_RP_SWAP, RE_BARE_YEAR_HYPHEN):
        m = rx.match(cid_clean)
        if m:
            y_val = int(m.group(1))
            seq_val = int(m.group(2))
            if y_val > eff_max:
                return None
            if y_val == 2016 and seq_val <= 10026:
                return f"2016{seq_val:07d}"
            return f"R{y_val}-{seq_val}"

    # 4. Pure numeric clerk_id
    if cid_nospace.isdigit():
        val_int = int(cid_nospace)
        if val_int == 0:
            return None
        n_len = len(cid_nospace)

        if n_len == 11 and cid_nospace[:4].isdigit():
            p_yr = int(cid_nospace[:4])
            p_seq = int(cid_nospace[4:])
            if 2006 <= p_yr <= 2015 and p_seq > 0:
                return cid_nospace
            if p_yr == 2016 and p_seq > 0:
                return cid_nospace if p_seq <= 10026 else f"R2016-{p_seq}"
            if 2017 <= p_yr <= eff_max and 0 < p_seq <= 999999:
                return f"R{p_yr}-{p_seq}"

        if 2006 <= yr_int <= 2015:
            if n_len <= 7:
                return f"{yr_int}{val_int:07d}"
            if n_len == 8:
                if cid_nospace[0] == str(yr_int)[-1]:
                    seq7 = int(cid_nospace[1:])
                    if seq7 > 0:
                        return f"{yr_int}{seq7:07d}"
                if cid_nospace.startswith("0"):
                    if val_int <= 999999:
                        return f"{yr_int}{val_int:07d}"
                    seq7 = int(cid_nospace[:7])
                    if seq7 > 0:
                        return f"{yr_int}{seq7:07d}"

        if yr_int == 2016 and n_len <= 7:
            if val_int <= 10026:
                return f"2016{val_int:07d}"
            return f"R2016-{val_int}"

        if 2017 <= yr_int <= eff_max and n_len <= 7 and val_int <= 999999:
            return f"R{yr_int}-{val_int}"

        return None

    # 5. Probate Docket / Case #
    m_pr = RE_PROBATE.match(cid_clean)
    if m_pr:
        return f"PR:{m_pr.group(1)}"

    # 6. Pre-1961 Bound Book Volume / Page
    m_vp = RE_VOL_PAGE.match(cid_clean)
    if m_vp:
        vol_tok = m_vp.group(1).lstrip("0") or "0"
        pg_tok = m_vp.group(2).lstrip("0") or "0"
        return f"VP:{vol_tok}/{pg_tok}"

    return None


def find_zip_member_case_insensitive(zf: zipfile.ZipFile, basename_lower: str) -> str | None:
    """Find a non-directory ZIP member matching `basename_lower` case-insensitively regardless of parent folder."""
    target = basename_lower.strip().lower()
    for name in zf.namelist():
        if name.endswith("/"):
            continue
        if PurePosixPath(name).name.lower() == target:
            return name
    return None


def peek_real_acct_zip_year(zip_path: Path) -> int | None:
    """Inspect the first few rows of `real_acct.txt` inside `zip_path` to determine its modal roll year."""
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            member = find_zip_member_case_insensitive(zf, "real_acct.txt")
            if member is None:
                return None
            with zf.open(member, "r") as raw_f:
                tf = io.TextIOWrapper(raw_f, encoding="latin-1", errors="replace")
                hdr_line = tf.readline()
                if not hdr_line:
                    return None
                cols = {c.strip().lower(): idx for idx, c in enumerate(hdr_line.rstrip("\r\n").split("\t"))}
                i_yr = cols.get("yr", -1)
                if i_yr < 0:
                    return None
                counts: dict[int, int] = {}
                for _ in range(16):
                    ln = tf.readline()
                    if not ln:
                        break
                    parts = ln.rstrip("\r\n").split("\t", i_yr + 1)
                    if len(parts) > i_yr:
                        s_yr = parts[i_yr].strip()
                        if len(s_yr) == 4 and s_yr.isdigit():
                            yv = int(s_yr)
                            counts[yv] = counts.get(yv, 0) + 1
                if counts:
                    return max(counts.items(), key=lambda kv: kv[1])[0]
    except (OSError, zipfile.BadZipFile):
        return None
    return None


def verify_zip_file_integrity(
    path: Path,
    *,
    error_cls: type[Exception] = ValueError,
) -> list[str]:
    """Verify ZIP local header magic bytes and full archive CRC/readability using stdlib only."""
    if not path.exists():
        raise error_cls(f"Missing required roll archive: {path}")
    size = path.stat().st_size
    if size < 22:
        raise error_cls(f"Archive {path.name} is truncated ({size} bytes).")
    with open(path, "rb") as fh:
        magic = fh.read(4)
    if magic != b"PK\x03\x04":
        raise error_cls(
            f"Archive {path.name} failed ZIP magic-byte verification "
            f"(got {magic!r}; upstream may have returned an HTML error page)."
        )
    try:
        with zipfile.ZipFile(path, "r") as zf:
            bad_member = zf.testzip()
            if bad_member is not None:
                raise error_cls(
                    f"Archive {path.name} failed CRC check on member {bad_member!r}."
                )
            return list(zf.namelist())
    except zipfile.BadZipFile as exc:
        raise error_cls(f"Archive {path.name} is not a valid ZIP file: {exc}") from exc


def download_archive_streaming(
    url: str,
    dest_path: Path,
    *,
    opener: Callable[..., Any] | None = None,
    timeout: float = 120.0,
    error_cls: type[Exception] = RuntimeError,
) -> None:
    """Stream an upstream HCAD `.zip` into `<name>.part`, validate ZIP integrity, then rename atomically."""
    open_fn = opener or urllib.request.urlopen
    part_path = dest_path.with_name(f"{dest_path.name}.part")
    part_path.unlink(missing_ok=True)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "HoustonBuildingAtlas/2.0"},
    )
    try:
        with open_fn(req, timeout=timeout) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            if status != 200:
                raise error_cls(
                    f"Download failed for {url}: expected HTTP 200, got HTTP {status}."
                )
            ctype = str(resp.headers.get("Content-Type") or "").lower()
            if "text/html" in ctype:
                raise error_cls(
                    f"Download failed for {url}: received Content-Type {ctype!r} (HTML error page)."
                )
            with open(part_path, "wb") as out_fh:
                first_chunk = True
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    if first_chunk:
                        first_chunk = False
                        if len(chunk) < 4 or chunk[:4] != b"PK\x03\x04":
                            raise error_cls(
                                f"Download failed for {url}: payload lacks ZIP magic bytes (got {chunk[:16]!r})."
                            )
                    out_fh.write(chunk)
        verify_zip_file_integrity(part_path, error_cls=error_cls)
        part_path.replace(dest_path)
    except Exception as exc:
        part_path.unlink(missing_ok=True)
        if isinstance(exc, error_cls):
            raise
        raise error_cls(f"Failed downloading {url}: {exc}") from exc


