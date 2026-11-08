"""Ingest Harris County Clerk (1961-present RP/MC) and pre-2005 HCAD TPIA tabular disclosures.

Architecture (Milestone 9):
  1. Flexible Multi-Format Tabular Reader:
     - Streams plain `.csv`/`.tsv`/`.txt`/`.dat`, `.gz`, and `.zip` tabular dumps with automatic
       delimiter detection (`\\t`, `|`, `,`, `;`) and encoding fallback (`utf-8-sig` -> `cp1252` -> `latin-1`).
     - Supports both Harris County Clerk `PIA.aspx` Option A relational schemas (separate Header,
       Grantor/Grantee Party, and Subdivision/Lot/Block Legal tables joined by File Number) and
       Option B flat denormalized exports, alongside pre-2005 (`1988-2004`) HCAD annual ownership
       snapshot and legacy sales/deed tables.
  2. Two-Tier Parcel Crosswalk & Ambiguity Guard:
     - Tier 1 (Exact Instrument Match): Resolves Clerk records by canonical instrument match key
       (`FN:RP-YYYY-SEQ`, 11-digit File Number, 7-char letter File Number, `FC:###-##-####`,
       `VP:VOL/PG`, `PR:CASE`) against HCAD `deeds.txt`, `permits_<prefix3>.json.gz`, and
       `owners_<prefix3>.json.gz`, preserving links across subsequent townhome replats.
     - Tier 2 (Canonical Legal Description Crosswalk): Parses HCAD `real_acct.txt` (`lgl_1..lgl_4`)
       into normalized `(subdivision, section, block, kind, id)` keys (`LOT`, `UNIT`, `RESERVE`,
       `TRACT`) with strict multi-parcel ambiguity capping (`max_parcels_per_legal_key = 4`) so
       subdivision-wide filings or un-unitized condominium regimes never fan out across dozens of accounts.
  3. Fail-Closed Statutory Confidentiality (`TX Tax Code Sec. 25.025`):
     - Aggregates confidential HCAD accounts from `permits_<prefix3>.json.gz` (`conf == 1`),
       `Real_acct_owner.zip`, and TPIA snapshots; strips all Grantor/Grantee/Mechanic's Lien/Deed of
       Trust/tenure strings on protected parcels; and routes annual roll snapshots through
       `consolidate_account_tenures` for retroactive pre-election taint shielding.
  4. Deterministic `.json.gz` Shard Promotion:
     - Supports `--dry-run` (zero live file mutations, full linkage & coverage report) and `--apply`
       (atomic header-free RFC 1952 `mtime=0` `.json.gz` writes + `pmtiles_manifest.json` span update
       guarded by `HARD_MAX_APP_BYTES` and `MAX_SINGLE_FILE_BYTES`).
"""

from __future__ import annotations

from collections import defaultdict
import csv
from dataclasses import dataclass, field
import gzip
import io
import json
import os
from pathlib import Path
import re
from typing import Any, Iterator
import zipfile

from atlas_pipeline.build_historical_ownership_2005_2026 import (
    HCAD_MAINFRAME_SENTINEL,
    MAX_OWNER_TENURES_PER_PARCEL,
    RollOwnerSnapshot,
    consolidate_account_tenures,
    encode_compact_owner_history,
    sanitize_wire_owner_name,
)
from atlas_pipeline.enrich_deed_chains import compute_app_total_bytes
from atlas_pipeline.schema import (
    ACTIVE_TAX_YEAR,
    HARD_MAX_APP_BYTES,
    MAX_SINGLE_FILE_BYTES,
    TPIA_PERMIT_SHARD_KEYS,
    collect_standing_and_referenced_atlas_hcads,
    compress_deterministic_gzip_json,
    find_zip_member_case_insensitive,
    is_confidential_owner_token,
    is_placeholder_owner_token,
    iter_json_or_gzip_shard_keys,
    normalize_hcad_clerk_instrument,
    normalize_standing_hcad_token,
    parse_dos_yyyymmdd,
    read_json_or_gzip_path,
    sanitize_atlas_text,
    sanitize_json_structure_dashes,
    sum_json_or_gzip_shard_bytes,
    update_pmtiles_manifest_v3,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = REPO_ROOT / "pipeline" / "cache"
APP_DIR = REPO_ROOT / "app"
DATA_DIR = APP_DIR / "public" / "data"

DEFAULT_MAX_PARCELS_PER_LEGAL_KEY = 4
DEFAULT_MAX_TPIA_DEEDS_PER_PARCEL = 12
DEFAULT_MAX_MECHANICS_LIENS_PER_PARCEL = 6
DEFAULT_MAX_DT_LENDERS_PER_PARCEL = 4

_TABULAR_SUFFIXES: frozenset[str] = frozenset({
    ".csv",
    ".tsv",
    ".txt",
    ".psv",
    ".dat",
})

_RE_COL_NORM = re.compile(r"[^a-z0-9]+")
_RE_SUBD_STRIP_SUFFIX = re.compile(
    r"\b(?:PARTIAL\s+REPLAT|PAR\s+R\s*/?\s*P|REPLAT|R\s*/?\s*P|AMENDED|AMEND|"
    r"ADDITION|ADDN|ADD|SUBDIVISION|SUBD|SUB|CONDOMINIUMS?|CONDOS?|TOWNHOMES?|T\s*/?\s*H)\b",
    re.IGNORECASE,
)
_RE_NM_TAG = re.compile(r"\(\s*NM\s*\)", re.IGNORECASE)
_RE_SEC_EXTRACT = re.compile(r"\b(?:SEC(?:TION)?)\s*0*([0-9A-Z]{1,4})\b", re.IGNORECASE)
_RE_BLK_EXTRACT = re.compile(r"\b(?:BLK|BLOCK)\s*0*([0-9A-Z]{1,5})\b", re.IGNORECASE)
_RE_UNIT_EXTRACT = re.compile(r"\b(?:UNIT|APT|BLDG\s+[0-9A-Z]+\s+UNIT)\s*0*([0-9A-Z\-]{1,8})\b", re.IGNORECASE)
_RE_RES_EXTRACT = re.compile(r"\b(?:RES(?:ERVE)?)\s+([0-9A-Z]{1,5})\b", re.IGNORECASE)
_RE_LOT_MULTI = re.compile(
    r"\b(?:LTS|LOTS|LT|LOT)\s+([0-9A-Z]{1,5}(?:\s*(?:&|AND|,|THRU|-)\s*[0-9A-Z]{1,5})*)\b",
    re.IGNORECASE,
)
_RE_TRACT_SINGLE = re.compile(r"\b(?:TR(?:ACT)?)\s*0*([0-9A-Z]{1,5})\b", re.IGNORECASE)
_RE_ABST_LINE = re.compile(r"^\s*ABST(?:RACT)?\b", re.IGNORECASE)
_RE_NON_ALNUM = re.compile(r"[^A-Z0-9\s]+")
_RE_MULTI_SPACE = re.compile(r"\s+")

_CONVEYANCE_CODES: frozenset[str] = frozenset({
    "WD",
    "W/D",
    "WARRANTY DEED",
    "GENERAL WARRANTY DEED",
    "GWD",
    "GD",
    "G/D",
    "GRANT DEED",
    "GIFT DEED",
    "DEED",
    "D",
    "SWD",
    "S/W/D",
    "SPECIAL WARRANTY DEED",
    "QCD",
    "Q/C",
    "Q/C/D",
    "QUITCLAIM DEED",
    "QUIT CLAIM DEED",
    "TRD",
    "TR/D",
    "TRUSTEE DEED",
    "TRUSTEES DEED",
    "SUBSTITUTE TRUSTEE DEED",
    "STD",
    "EXEC DEED",
    "EXECUTORS DEED",
    "ADM DEED",
    "ADMINISTRATORS DEED",
    "PROB",
    "PROBATE",
    "MUNIMENT OF TITLE",
    "AFFIDAVIT OF HEIRSHIP",
    "AOH",
    "B/S",
    "BARGAIN AND SALE DEED",
    "CORRECTION DEED",
    "CORR DEED",
    "PARTITION DEED",
})

_MECHANICS_LIEN_CODES: frozenset[str] = frozenset({
    "M/L",
    "ML",
    "MECH LIEN",
    "MECHANICS LIEN",
    "MECHANIC LIEN",
    "MECHANIC'S LIEN",
    "MECHANICS LIEN CONTRACT",
    "BLDG CONT",
    "BUILDING CONTRACT",
    "CONSTRUCTION CONTRACT",
    "CONT",
    "LIEN CONTRACT",
})

_DEED_OF_TRUST_CODES: frozenset[str] = frozenset({
    "D/T",
    "DT",
    "DEED OF TRUST",
    "MORTGAGE",
    "MTG",
    "SECURITY INSTRUMENT",
})

_PLAT_OR_RESTRICTION_CODES: frozenset[str] = frozenset({
    "PLAT",
    "REPLAT",
    "MAP",
    "SUBD",
    "CONDO",
    "CONDO DECL",
    "CONDOMINIUM DECLARATION",
    "RESTR",
    "RESTRICTIONS",
    "DEED RESTRICTIONS",
    "DEDICATION",
})


@dataclass(frozen=True)
class LegalCrosswalkKey:
    """Normalized Harris County legal-description key for parcel crosswalk matching."""

    subdivision: str
    section: str
    block: str
    locator_kind: str  # "LOT", "UNIT", "RES", "TR"
    locator_id: str


@dataclass
class ClerkInstrumentRecord:
    """Consolidated Harris County Clerk tabular filing assembled across relational or flat rows."""

    join_id: str
    clerk_token: str = ""
    match_key: str = ""
    dt8: str = "00000000"
    category: str = ""  # "CONVEYANCE", "MECHANICS_LIEN", "DEED_OF_TRUST", "RESTRICTION_PLAT"
    raw_inst_type: str = ""
    wire_type_code: str = "D"
    grantors: list[str] = field(default_factory=list)
    grantees: list[str] = field(default_factory=list)
    amount: str = ""
    explicit_hcads: set[str] = field(default_factory=set)
    legal_keys: set[LegalCrosswalkKey] = field(default_factory=set)
    has_confidential_party: bool = False


def _norm_col_name(raw_col: str) -> str:
    return _RE_COL_NORM.sub("_", str(raw_col or "").strip().lower()).strip("_")


def _detect_delimiter(header_line: str) -> str:
    best_delim = ","
    best_count = 1
    for cand in ("\t", "|", ",", ";"):
        try:
            row = next(csv.reader([header_line], delimiter=cand))
        except Exception:
            continue
        if len(row) > best_count:
            best_count = len(row)
            best_delim = cand
    return best_delim


def _decode_bytes_with_fallback(raw_bytes: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw_bytes.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw_bytes.decode("latin-1", errors="replace")


def iter_tpia_tabular_files(input_dir: Path | None) -> Iterator[tuple[str, list[str], Iterator[dict[str, str]]]]:
    """Yield `(source_label, normalized_headers, row_dict_iterator)` across plain, `.gz`, and `.zip` files."""
    if input_dir is None or not Path(input_dir).exists():
        return
    root = Path(input_dir)
    files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file() and not p.name.startswith("."))

    def _parse_text_stream(label: str, raw_bytes: bytes) -> tuple[str, list[str], Iterator[dict[str, str]]] | None:
        text = _decode_bytes_with_fallback(raw_bytes)
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if not lines:
            return None
        delim = _detect_delimiter(lines[0])
        reader = csv.reader(lines, delimiter=delim)
        try:
            raw_hdr = next(reader)
        except StopIteration:
            return None
        norm_hdr = [_norm_col_name(c) for c in raw_hdr]
        if not any(norm_hdr):
            return None

        def _row_iter() -> Iterator[dict[str, str]]:
            for parts in reader:
                if not parts or not any(p.strip() for p in parts):
                    continue
                row_map: dict[str, str] = {}
                for i, col_name in enumerate(norm_hdr):
                    if not col_name:
                        continue
                    val = parts[i].strip() if i < len(parts) else ""
                    if val and col_name not in row_map:
                        row_map[col_name] = val
                if row_map:
                    yield row_map

        return label, norm_hdr, _row_iter()

    for fpath in files:
        low_name = fpath.name.lower()
        if low_name.endswith(".zip"):
            try:
                with zipfile.ZipFile(fpath, "r") as zf:
                    for member in sorted(zf.namelist()):
                        if member.endswith("/") or Path(member).name.startswith("."):
                            continue
                        m_suf = Path(member).suffix.lower()
                        if m_suf not in _TABULAR_SUFFIXES:
                            continue
                        parsed = _parse_text_stream(f"{fpath.name}:{member}", zf.read(member))
                        if parsed is not None:
                            yield parsed
            except zipfile.BadZipFile:
                continue
        elif low_name.endswith(".gz") and not low_name.endswith(".json.gz"):
            base_suf = Path(low_name[:-3]).suffix.lower()
            if base_suf in _TABULAR_SUFFIXES or not base_suf:
                raw_b = gzip.decompress(fpath.read_bytes())
                parsed = _parse_text_stream(fpath.name, raw_b)
                if parsed is not None:
                    yield parsed
        elif fpath.suffix.lower() in _TABULAR_SUFFIXES:
            parsed = _parse_text_stream(fpath.name, fpath.read_bytes())
            if parsed is not None:
                yield parsed


def _first_present(row: dict[str, str], keys: tuple[str, ...]) -> str:
    for k in keys:
        v = row.get(k)
        if v:
            return v.strip()
    return ""


def sanitize_tpia_party_name(raw_name: str) -> str:
    """Sanitize Grantor/Grantee/Contractor/Lender strings for safe wire serialization (zero `:`, `|`, `~`, dashes)."""
    cleaned = sanitize_wire_owner_name(sanitize_atlas_text(str(raw_name or "")))
    cleaned = cleaned.replace(":", " ").replace("|", " ").replace("~", " ")
    return _RE_MULTI_SPACE.sub(" ", cleaned).strip()


def format_clerk_match_key(clerk_token: str) -> str:
    """Map a normalized compact Clerk instrument token to its canonical cross-module `matchKey`."""
    tok = str(clerk_token or "").strip()
    if not tok:
        return ""
    up = tok.upper()
    if up.startswith(("FC:", "PR:", "VP:")):
        return up
    if re.fullmatch(r"R20\d{2}-\d+", up):
        return f"FN:RP-{up[1:]}"
    return f"FN:{up}"


def normalize_clerk_tpia_instrument(
    *,
    file_no: str = "",
    film_code: str = "",
    vol: str = "",
    page: str = "",
    rec_year: str = "",
    max_year: int = ACTIVE_TAX_YEAR + 1,
) -> str:
    """Normalize Harris County Clerk File Number, Film Code, or Vol/Page into compact Atlas Clerk token."""
    raw_fn = sanitize_atlas_text(file_no).strip().upper()
    raw_fc = sanitize_atlas_text(film_code).strip().upper()
    raw_vol = sanitize_atlas_text(vol).strip().upper()
    raw_pg = sanitize_atlas_text(page).strip().upper()

    if raw_fn:
        tok = normalize_hcad_clerk_instrument(rec_year, raw_fn, max_year=max_year)
        if tok:
            return tok
        # Direct Clerk RP-YYYY-NNNNNN or MC-YYYY-NNNNNN formats
        m_rp = re.fullmatch(r"RP[\s\-_]*(20\d{2})[\s\-_]*0*([1-9]\d{0,6})", raw_fn)
        if m_rp:
            return f"R{m_rp.group(1)}-{m_rp.group(2)}"
        m_mc = re.fullmatch(r"(?:MC|SC)[\s\-_]*(20\d{2})[\s\-_]*0*([1-9]\d{0,6})", raw_fn)
        if m_mc:
            return f"MC-{m_mc.group(1)}-{m_mc.group(2)}"
        if re.fullmatch(r"\d{11}", raw_fn) or re.fullmatch(r"[A-Z]\d{6}", raw_fn):
            return raw_fn

    if raw_fc:
        tok_fc = normalize_hcad_clerk_instrument(rec_year, raw_fc, max_year=max_year)
        if tok_fc:
            return tok_fc
        m_digits = re.fullmatch(r"(\d{3})[\s\-]*(\d{2})[\s\-]*(\d{4})", raw_fc)
        if m_digits:
            return f"FC:{m_digits.group(1)}-{m_digits.group(2)}-{m_digits.group(3)}"

    if raw_vol and raw_pg:
        v_clean = raw_vol.lstrip("0") or "0"
        p_clean = raw_pg.lstrip("0") or "0"
        if v_clean != "0" or p_clean != "0":
            return f"VP:{v_clean}/{p_clean}"

    return ""


def classify_clerk_instrument_type(raw_inst_type: str) -> tuple[str, str]:
    """Classify a County Clerk instrument code into `(category, wire_type_code)`."""
    clean = _RE_MULTI_SPACE.sub(
        " ",
        sanitize_atlas_text(raw_inst_type).upper().replace(".", "").strip(),
    )
    if not clean:
        return "", "D"
    if clean in _CONVEYANCE_CODES:
        wire_code = "O" if clean in ("PROB", "PROBATE", "MUNIMENT OF TITLE", "AFFIDAVIT OF HEIRSHIP", "AOH") else "D"
        return "CONVEYANCE", wire_code
    if clean in _MECHANICS_LIEN_CODES:
        return "MECHANICS_LIEN", "M"
    if clean in _DEED_OF_TRUST_CODES:
        return "DEED_OF_TRUST", "T"
    if clean in _PLAT_OR_RESTRICTION_CODES:
        return "RESTRICTION_PLAT", "S"

    # Substring fallback for verbose Clerk descriptions (excluding releases!)
    if "RELEASE" in clean or clean.startswith("REL ") or clean == "REL":
        return "", "D"
    if "MECHANIC" in clean or "BLDG CONT" in clean or "BUILDING CONTRACT" in clean:
        return "MECHANICS_LIEN", "M"
    if "DEED OF TRUST" in clean or "MORTGAGE" in clean:
        return "DEED_OF_TRUST", "T"
    if "WARRANTY DEED" in clean or "QUITCLAIM" in clean or "TRUSTEE DEED" in clean or "GIFT DEED" in clean:
        return "CONVEYANCE", "D"
    if "REPLAT" in clean or "CONDOMINIUM DECLARATION" in clean:
        return "RESTRICTION_PLAT", "S"
    return "", "D"


def normalize_legal_subdivision_name(raw_subd: str) -> tuple[str, str]:
    """Normalize a subdivision/addition description into `(canonical_subdivision, extracted_section)`."""
    txt = sanitize_atlas_text(raw_subd).upper()
    txt = _RE_NM_TAG.sub(" ", txt)
    sec_found = ""
    m_sec = _RE_SEC_EXTRACT.search(txt)
    if m_sec:
        sec_found = m_sec.group(1).lstrip("0") or "0"
        txt = txt[: m_sec.start()] + " " + txt[m_sec.end() :]
    txt = _RE_SUBD_STRIP_SUFFIX.sub(" ", txt)
    txt = _RE_NON_ALNUM.sub(" ", txt)
    txt = _RE_MULTI_SPACE.sub(" ", txt).strip()
    return txt, sec_found


def _expand_lot_tokens(raw_lot_expr: str, max_lots: int = 4) -> list[str]:
    """Extract discrete normalized lot designators from expressions like `'14'`, `'14 & 15'`, or `'014A'`."""
    clean = sanitize_atlas_text(raw_lot_expr).upper().strip()
    if not clean:
        return []
    # Range expression "6 THRU 10" or "6-10"
    m_range = re.fullmatch(r"0*(\d{1,4})\s*(?:THRU|-)\s*0*(\d{1,4})", clean)
    if m_range:
        lo, hi = int(m_range.group(1)), int(m_range.group(2))
        if 0 < lo <= hi and (hi - lo + 1) <= max_lots:
            return [str(x) for x in range(lo, hi + 1)]
        return []
    parts = re.split(r"\s*(?:&|AND|,)\s*|\s+", clean)
    out: list[str] = []
    for p in parts:
        tok = re.sub(r"[^0-9A-Z]", "", p)
        if not tok:
            continue
        if tok.isdigit():
            tok = tok.lstrip("0") or "0"
            if tok == "0":
                continue
        else:
            tok = re.sub(r"^0+(?=[1-9])", "", tok)
        if tok not in out:
            out.append(tok)
        if len(out) > max_lots:
            return []
    return out


def extract_hcad_legal_crosswalk_keys(
    lgl_lines: list[str] | tuple[str, ...],
) -> list[LegalCrosswalkKey]:
    """Parse HCAD `lgl_1..lgl_4` lines into canonical `LegalCrosswalkKey` tuples."""
    cleaned_lines = [
        _RE_MULTI_SPACE.sub(" ", _RE_NM_TAG.sub(" ", sanitize_atlas_text(ln).upper())).strip()
        for ln in lgl_lines
        if ln and _RE_MULTI_SPACE.sub(" ", _RE_NM_TAG.sub(" ", sanitize_atlas_text(ln).upper())).strip()
    ]
    if not cleaned_lines:
        return []

    # Identify subdivision candidate (typically final non-abstract, non-pure-lot/block line)
    subd_raw = ""
    for ln in reversed(cleaned_lines):
        if _RE_ABST_LINE.match(ln):
            continue
        if re.fullmatch(r"(?:(?:PT|E|W|N|S|HLF|1/2)\s+)*(?:LTS?|LOTS?|TR(?:ACT)?|BLK|BLOCK|SEC(?:TION)?|RES(?:ERVE)?|UNIT)\s+[0-9A-Z&\s\-/'.]+", ln):
            continue
        subd_raw = ln
        break
    if not subd_raw:
        return []

    subd_norm, subd_sec = normalize_legal_subdivision_name(subd_raw)
    if not subd_norm or len(subd_norm) < 2:
        return []

    joined = " ".join(cleaned_lines)
    m_sec = _RE_SEC_EXTRACT.search(joined)
    section = (m_sec.group(1).lstrip("0") or "0") if m_sec else subd_sec
    m_blk = _RE_BLK_EXTRACT.search(joined)
    block = (m_blk.group(1).lstrip("0") or "0") if m_blk else ""

    m_unit = _RE_UNIT_EXTRACT.search(joined)
    if m_unit:
        uid = m_unit.group(1).lstrip("0") or m_unit.group(1)
        return [LegalCrosswalkKey(subd_norm, section, block, "UNIT", uid)]

    m_lot = _RE_LOT_MULTI.search(joined)
    if m_lot:
        lots = _expand_lot_tokens(m_lot.group(1))
        if lots:
            return [LegalCrosswalkKey(subd_norm, section, block, "LOT", lt) for lt in lots]

    m_res = _RE_RES_EXTRACT.search(joined)
    if m_res:
        return [LegalCrosswalkKey(subd_norm, section, block, "RES", m_res.group(1))]

    m_tr = _RE_TRACT_SINGLE.search(joined)
    if m_tr:
        return [LegalCrosswalkKey(subd_norm, section, block, "TR", m_tr.group(1))]

    return []


def extract_clerk_row_legal_keys(row: dict[str, str]) -> list[LegalCrosswalkKey]:
    """Extract canonical `LegalCrosswalkKey`s from a County Clerk Legal or flat tabular row."""
    raw_subd = _first_present(
        row,
        ("subdivision", "subd", "subd_desc", "subdivision_name", "addition", "condo_name", "legal_desc", "description"),
    )
    if not raw_subd:
        return []
    subd_norm, subd_sec = normalize_legal_subdivision_name(raw_subd)
    if not subd_norm or len(subd_norm) < 2:
        return []

    raw_sec = _first_present(row, ("section", "sec", "subd_section"))
    sec_norm = re.sub(r"[^0-9A-Z]", "", raw_sec.upper()).lstrip("0") if raw_sec else subd_sec

    raw_blk = _first_present(row, ("block", "blk", "subd_block"))
    blk_norm = re.sub(r"[^0-9A-Z]", "", raw_blk.upper()).lstrip("0") if raw_blk else ""

    raw_unit = _first_present(row, ("unit", "unit_no", "condo_unit"))
    if raw_unit:
        u_clean = re.sub(r"[^0-9A-Z\-]", "", raw_unit.upper()).lstrip("0") or raw_unit.upper().strip()
        if u_clean:
            return [LegalCrosswalkKey(subd_norm, sec_norm, blk_norm, "UNIT", u_clean)]

    raw_lot = _first_present(row, ("lot", "lt", "lot_no", "lots"))
    if raw_lot:
        lots = _expand_lot_tokens(raw_lot)
        if lots:
            return [LegalCrosswalkKey(subd_norm, sec_norm, blk_norm, "LOT", lt) for lt in lots]

    raw_res = _first_present(row, ("reserve", "res"))
    if raw_res:
        r_clean = re.sub(r"[^0-9A-Z]", "", raw_res.upper())
        if r_clean:
            return [LegalCrosswalkKey(subd_norm, sec_norm, blk_norm, "RES", r_clean)]

    raw_tr = _first_present(row, ("tract", "tr", "tract_no"))
    if raw_tr:
        t_clean = re.sub(r"[^0-9A-Z]", "", raw_tr.upper()).lstrip("0")
        if t_clean:
            return [LegalCrosswalkKey(subd_norm, sec_norm, blk_norm, "TR", t_clean)]

    return []


def build_hcad_legal_crosswalk_index(
    real_acct_zip: Path | None,
    *,
    target_hcads: set[str] | None = None,
    max_parcels_per_legal_key: int = DEFAULT_MAX_PARCELS_PER_LEGAL_KEY,
) -> tuple[dict[LegalCrosswalkKey, tuple[str, ...]], set[str], int]:
    """Stream `Real_acct_owner.zip -> real_acct.txt` to build an ambiguity-capped legal crosswalk + confidential set."""
    raw_buckets: dict[LegalCrosswalkKey, list[str]] = defaultdict(list)
    ambiguous_keys: set[LegalCrosswalkKey] = set()
    confidential_hcads: set[str] = set()

    if real_acct_zip is None or not Path(real_acct_zip).exists():
        return {}, confidential_hcads, 0

    with zipfile.ZipFile(real_acct_zip, "r") as zf:
        ra_member = find_zip_member_case_insensitive(zf, "real_acct.txt")
        if ra_member is not None:
            with zf.open(ra_member, "r") as raw_f:
                tf = io.TextIOWrapper(raw_f, encoding="latin-1", errors="replace")
                hdr = [c.strip().lower() for c in tf.readline().rstrip("\r\n").split("\t")]
                col = {name: i for i, name in enumerate(hdr)}
                i_acct = col.get("acct", 0)
                i_mailto = col.get("mailto", -1)
                i_l1 = col.get("lgl_1", -1)
                i_l2 = col.get("lgl_2", -1)
                i_l3 = col.get("lgl_3", -1)
                i_l4 = col.get("lgl_4", -1)
                for line in tf:
                    parts = line.rstrip("\r\n").split("\t")
                    if len(parts) <= i_acct:
                        continue
                    acct = normalize_standing_hcad_token(parts[i_acct])
                    if not acct:
                        continue
                    mailto = parts[i_mailto].strip() if 0 <= i_mailto < len(parts) else ""
                    if mailto and is_confidential_owner_token(mailto):
                        confidential_hcads.add(acct)
                    if target_hcads is not None and acct not in target_hcads:
                        continue
                    l_lines = [
                        parts[idx].strip() if 0 <= idx < len(parts) else ""
                        for idx in (i_l1, i_l2, i_l3, i_l4)
                    ]
                    for lkey in extract_hcad_legal_crosswalk_keys(l_lines):
                        if lkey in ambiguous_keys:
                            continue
                        bucket = raw_buckets[lkey]
                        if acct not in bucket:
                            bucket.append(acct)
                            if len(bucket) > max_parcels_per_legal_key:
                                ambiguous_keys.add(lkey)
                                del raw_buckets[lkey]
                        # Also register section-agnostic fallback when section was blank on Clerk side
                        if lkey.section:
                            sec_free = LegalCrosswalkKey(
                                lkey.subdivision, "", lkey.block, lkey.locator_kind, lkey.locator_id
                            )
                            if sec_free not in ambiguous_keys:
                                sf_bucket = raw_buckets[sec_free]
                                if acct not in sf_bucket:
                                    sf_bucket.append(acct)
                                    if len(sf_bucket) > max_parcels_per_legal_key:
                                        ambiguous_keys.add(sec_free)
                                        del raw_buckets[sec_free]

        ow_member = find_zip_member_case_insensitive(zf, "owners.txt")
        if ow_member is not None:
            with zf.open(ow_member, "r") as raw_ow:
                tf_ow = io.TextIOWrapper(raw_ow, encoding="latin-1", errors="replace")
                hdr_ow = [c.strip().lower() for c in tf_ow.readline().rstrip("\r\n").split("\t")]
                col_ow = {name: i for i, name in enumerate(hdr_ow)}
                i_oa = col_ow.get("acct", 0)
                i_onm = col_ow.get("name", 2)
                i_oaka = col_ow.get("aka", -1)
                for line in tf_ow:
                    parts = line.rstrip("\r\n").split("\t")
                    if len(parts) <= max(i_oa, i_onm):
                        continue
                    nm = parts[i_onm].strip()
                    aka = parts[i_oaka].strip() if 0 <= i_oaka < len(parts) else ""
                    if (nm and is_confidential_owner_token(nm)) or (
                        aka and is_confidential_owner_token(aka)
                    ):
                        acct_o = normalize_standing_hcad_token(parts[i_oa])
                        if acct_o:
                            confidential_hcads.add(acct_o)

    finalized = {k: tuple(sorted(v)) for k, v in raw_buckets.items() if 1 <= len(v) <= max_parcels_per_legal_key}
    return finalized, confidential_hcads, len(ambiguous_keys)


def expand_compact_owner_history_to_snapshots(
    compact_oh: str,
) -> tuple[list[RollOwnerSnapshot], list[tuple[str, str]]]:
    """Expand an existing `owners_<prefix3>.json.gz` wire string into annual `RollOwnerSnapshot`s + paired deeds."""
    snaps: list[RollOwnerSnapshot] = []
    paired_deeds: list[tuple[str, str]] = []
    raw = str(compact_oh or "").strip()
    if not raw:
        return snaps, paired_deeds
    for seg in raw.split("|"):
        parts = seg.strip().split("~")
        if len(parts) < 4:
            continue
        span_s = parts[0].strip()
        acq_s = parts[1].strip()
        deed_s = parts[2].strip()
        owner_s = "~".join(parts[3:]).strip()
        m_sp = re.fullmatch(r"(\d{4})(?:-(\d{4}))?", span_s)
        if not m_sp or not owner_s:
            continue
        y0 = int(m_sp.group(1))
        y1 = int(m_sp.group(2)) if m_sp.group(2) else y0
        if y1 < y0:
            continue
        for yr in range(y0, y1 + 1):
            snaps.append(
                RollOwnerSnapshot(
                    year=yr,
                    owner_name=owner_s,
                    acq_dt=acq_s if acq_s != HCAD_MAINFRAME_SENTINEL else "1988-01-02",
                    is_confidential=is_confidential_owner_token(owner_s),
                )
            )
        if deed_s:
            dt8 = (
                acq_s
                if len(acq_s) == 8 and acq_s.isdigit()
                else f"{y0:04d}0000"
            )
            paired_deeds.append((dt8, deed_s))
    return snaps, paired_deeds


def parse_existing_compact_deeds(
    compact_d: str,
) -> dict[str, tuple[str, str, str, str, str, str]]:
    """Parse existing `permits_<prefix3>.json.gz["<acct>"]["d"]` into `matchKey -> (dt8, tok, deed_id, code, grantor, grantee)`."""
    by_match: dict[str, tuple[str, str, str, str, str, str]] = {}
    raw = str(compact_d or "").strip()
    if not raw:
        return by_match
    for seg in raw.split("|"):
        entry = seg.strip()
        if not entry or ":" not in entry:
            continue
        colon_idx = entry.index(":")
        dt8 = entry[:colon_idx].strip()
        rest = entry[colon_idx + 1 :].split(":")
        is_pfx = len(rest) >= 2 and rest[0].strip().upper() in ("FC", "PR", "VP")
        tok = f"{rest[0].strip().upper()}:{rest[1].strip()}" if is_pfx else rest[0].strip()
        if not tok:
            continue
        off = 2 if is_pfx else 1
        deed_id = rest[off].strip() if len(rest) > off else ""
        code = (rest[off + 1].strip() or "D") if len(rest) > off + 1 else "D"
        grantor = rest[off + 2].strip() if len(rest) > off + 2 else ""
        grantee = rest[off + 3].strip() if len(rest) > off + 3 else ""
        mkey = format_clerk_match_key(tok)
        if mkey:
            by_match[mkey] = (dt8, tok, deed_id, code, grantor, grantee)
    return by_match


def encode_compact_deed_records(
    records: list[tuple[str, str, str, str, str, str]],
    *,
    max_deeds: int = DEFAULT_MAX_TPIA_DEEDS_PER_PARCEL,
) -> str:
    """Serialize `(dt8, tok, deed_id, code, grantor, grantee)` tuples into backward-compatible `d` wire format."""
    ordered = sorted(
        records,
        key=lambda r: (r[0] if r[0] != "00000000" else "", r[1]),
        reverse=True,
    )[:max_deeds]
    segments: list[str] = []
    for dt8, tok, deed_id, code, grantor, grantee in ordered:
        if not tok:
            continue
        eff_dt = dt8 if (len(dt8) == 8 and dt8.isdigit()) else "00000000"
        eff_code = (code or "D").strip().upper()
        if grantor or grantee:
            segments.append(f"{eff_dt}:{tok}:{deed_id}:{eff_code}:{grantor}:{grantee}")
        elif deed_id or eff_code != "D":
            segments.append(f"{eff_dt}:{tok}:{deed_id}:{eff_code}")
        else:
            segments.append(f"{eff_dt}:{tok}")
    return "|".join(segments)


def merge_compact_mechanics_liens(
    existing_ml_wire: str,
    incoming_ml: list[tuple[str, str, str, str, str]],
    *,
    max_liens: int = DEFAULT_MAX_MECHANICS_LIENS_PER_PARCEL,
) -> str:
    """Merge existing `ml` wire segments with incoming `(dt8, tok, contractor, owner_nm, amount)` tuples."""
    merged: dict[str, tuple[str, str, str, str, str]] = {}
    raw_prev = str(existing_ml_wire or "").strip()
    if raw_prev:
        for seg in raw_prev.split("|"):
            ps = seg.split("~")
            if len(ps) >= 3 and ps[1]:
                mkey = format_clerk_match_key(ps[1])
                if mkey:
                    merged[mkey] = (
                        ps[0],
                        ps[1],
                        ps[2],
                        ps[3] if len(ps) > 3 else "",
                        ps[4] if len(ps) > 4 else "",
                    )
    for dt8, tok, contractor, owner_nm, amt in incoming_ml:
        mkey = format_clerk_match_key(tok)
        if mkey and contractor:
            merged[mkey] = (dt8, tok, contractor, owner_nm, amt)
    ordered = sorted(
        merged.values(),
        key=lambda x: (x[0] if x[0] != "00000000" else "", x[1]),
        reverse=True,
    )[:max_liens]
    return "|".join(
        f"{m_dt}~{m_tok}~{m_con}~{m_own}~{m_amt}".rstrip("~")
        for m_dt, m_tok, m_con, m_own, m_amt in ordered
    )


def merge_compact_dt_lenders(
    existing_dt_wire: str,
    incoming_dt: list[tuple[str, str, str]],
    *,
    max_lenders: int = DEFAULT_MAX_DT_LENDERS_PER_PARCEL,
) -> str:
    """Merge existing `dt_lender` wire segments with incoming `(dt8, tok, lender)` tuples."""
    merged: dict[str, tuple[str, str, str]] = {}
    raw_prev = str(existing_dt_wire or "").strip()
    if raw_prev:
        for seg in raw_prev.split("|"):
            ps = seg.split("~")
            if len(ps) >= 3 and ps[1]:
                mkey = format_clerk_match_key(ps[1])
                if mkey:
                    merged[mkey] = (ps[0], ps[1], ps[2])
    for dt8, tok, lender in incoming_dt:
        mkey = format_clerk_match_key(tok)
        if mkey and lender:
            merged[mkey] = (dt8, tok, lender)
    ordered = sorted(
        merged.values(),
        key=lambda x: (x[0] if x[0] != "00000000" else "", x[1]),
        reverse=True,
    )[:max_lenders]
    return "|".join(f"{d_dt}~{d_tok}~{d_len}" for d_dt, d_tok, d_len in ordered)


def _merge_clerk_instrument_records(
    dst: ClerkInstrumentRecord, src: ClerkInstrumentRecord
) -> ClerkInstrumentRecord:
    """Merge a provisional `doc_id`-keyed `ClerkInstrumentRecord` into its canonical instrument record."""
    if not dst.clerk_token and src.clerk_token:
        dst.clerk_token = src.clerk_token
        dst.match_key = src.match_key
    if src.dt8 != "00000000" and (dst.dt8 == "00000000" or dst.dt8.endswith("0000")):
        dst.dt8 = src.dt8
    if not dst.raw_inst_type and src.raw_inst_type:
        dst.raw_inst_type = src.raw_inst_type
        dst.category = src.category
        dst.wire_type_code = src.wire_type_code
    if not dst.amount and src.amount:
        dst.amount = src.amount
    dst.explicit_hcads |= src.explicit_hcads
    dst.legal_keys |= src.legal_keys
    if src.has_confidential_party:
        dst.has_confidential_party = True
    for g in src.grantors:
        if g not in dst.grantors:
            dst.grantors.append(g)
    for g in src.grantees:
        if g not in dst.grantees:
            dst.grantees.append(g)
    return dst


def ingest_hcad_tpia_tables(
    hcad_tpia_dir: Path | None,
    *,
    max_year: int = ACTIVE_TAX_YEAR + 1,
    active_tax_year: int = ACTIVE_TAX_YEAR,
) -> dict[str, Any]:
    """Parse pre-2005 (`1988-2004`) HCAD annual ownership snapshots and legacy deed/sale tables."""
    snapshots_by_acct: dict[str, list[RollOwnerSnapshot]] = defaultdict(list)
    deeds_by_acct: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)  # (dt8, tok, grantor, grantee)
    confidential_hcads: set[str] = set()
    historical_conf_hcads: set[str] = set()
    files_scanned = 0
    snapshot_rows_parsed = 0
    deed_rows_parsed = 0

    for _label, headers, rows in iter_tpia_tabular_files(hcad_tpia_dir):
        files_scanned += 1
        hdr_set = set(headers)
        has_acct = bool(hdr_set & {"acct", "account", "hcad", "hcad_num", "parcel_id"})
        if not has_acct:
            continue
        is_deed_table = bool(
            hdr_set
            & {
                "dos",
                "sale_dt",
                "sale_date",
                "deed_dt",
                "deed_date",
                "clerk_id",
                "file_no",
                "file_number",
                "film_code",
                "vol",
                "volume",
                "grantor",
                "seller",
            }
        )
        is_snapshot_table = bool(
            (hdr_set & {"yr", "tax_yr", "tax_year", "roll_yr", "roll_year", "certified_yr"})
            and (hdr_set & {"mailto", "owner", "owner_name", "name", "aka"})
        )

        for row in rows:
            raw_acct = _first_present(row, ("acct", "account", "hcad", "hcad_num", "parcel_id"))
            acct = normalize_standing_hcad_token(raw_acct)
            if not acct:
                continue

            if is_snapshot_table:
                raw_yr = _first_present(row, ("yr", "tax_yr", "tax_year", "roll_yr", "roll_year", "certified_yr"))
                if raw_yr.isdigit() and 1980 <= int(raw_yr) <= max_year:
                    yr_i = int(raw_yr)
                    raw_owner = _first_present(row, ("mailto", "owner", "owner_name", "name"))
                    all_owner_cols = [
                        row[k]
                        for k in ("mailto", "owner", "owner_name", "name", "aka", "dba", "co_owner")
                        if row.get(k)
                    ]
                    raw_conf_flag = _first_present(row, ("conf", "confidential", "confidential_flag", "sec_25_025"))
                    is_conf = bool(
                        raw_conf_flag.upper() in ("1", "Y", "YES", "TRUE", "T")
                        or any(is_confidential_owner_token(v) for v in all_owner_cols)
                    )
                    if is_conf:
                        historical_conf_hcads.add(acct)
                        if yr_i >= active_tax_year:
                            confidential_hcads.add(acct)
                    acq_raw = _first_present(row, ("new_own_dt", "acq_dt", "acquisition_date", "deed_dt", "dos"))
                    snapshots_by_acct[acct].append(
                        RollOwnerSnapshot(
                            year=yr_i,
                            owner_name=raw_owner,
                            acq_dt=acq_raw,
                            is_confidential=is_conf,
                        )
                    )
                    snapshot_rows_parsed += 1

            if is_deed_table:
                raw_dos = _first_present(row, ("dos", "sale_dt", "sale_date", "deed_dt", "deed_date", "record_date"))
                raw_cyr = _first_present(row, ("clerk_yr", "rec_yr", "year", "yr"))
                raw_cid = _first_present(row, ("clerk_id", "file_no", "file_number", "instrument_no"))
                raw_fc = _first_present(row, ("film_code", "microfilm_no"))
                raw_vol = _first_present(row, ("vol", "volume", "book"))
                raw_pg = _first_present(row, ("pg", "page"))
                tok = normalize_clerk_tpia_instrument(
                    file_no=raw_cid,
                    film_code=raw_fc,
                    vol=raw_vol,
                    page=raw_pg,
                    rec_year=raw_cyr or (raw_dos[:4] if len(raw_dos) >= 4 and raw_dos[:4].isdigit() else ""),
                    max_year=max_year,
                )
                if not tok:
                    continue
                dt8 = parse_dos_yyyymmdd(raw_dos, raw_cyr, max_year=max_year)
                raw_grantor = _first_present(row, ("grantor", "seller", "from_party"))
                raw_grantee = _first_present(row, ("grantee", "buyer", "to_party"))
                if is_confidential_owner_token(raw_grantor) or is_confidential_owner_token(raw_grantee):
                    continue
                grantor = "" if is_placeholder_owner_token(raw_grantor) else sanitize_tpia_party_name(raw_grantor)
                grantee = "" if is_placeholder_owner_token(raw_grantee) else sanitize_tpia_party_name(raw_grantee)
                deeds_by_acct[acct].append((dt8, tok, grantor, grantee))
                deed_rows_parsed += 1

    return {
        "files_scanned": files_scanned,
        "snapshot_rows_parsed": snapshot_rows_parsed,
        "deed_rows_parsed": deed_rows_parsed,
        "snapshots_by_acct": snapshots_by_acct,
        "deeds_by_acct": deeds_by_acct,
        "confidential_hcads": confidential_hcads,
        "historical_conf_hcads": historical_conf_hcads,
    }


def ingest_clerk_tpia_tables(
    clerk_tpia_dir: Path | None,
    *,
    max_year: int = ACTIVE_TAX_YEAR + 1,
) -> dict[str, Any]:
    """Assemble County Clerk `PIA.aspx` relational (Header + Party + Legal) or flat export tables."""
    instruments: dict[str, ClerkInstrumentRecord] = {}
    doc_id_aliases: dict[str, str] = {}
    saw_inst_type_column = False
    files_scanned = 0
    rows_scanned = 0
    skipped_non_target = 0

    inst_type_cols = {
        "inst_type",
        "instrument_type",
        "doc_type",
        "document_type",
        "type_code",
        "type_desc",
        "instrument_code",
    }

    for _label, headers, rows in iter_tpia_tabular_files(clerk_tpia_dir):
        files_scanned += 1
        hdr_set = set(headers)
        if not (
            hdr_set
            & {
                "file_no",
                "file_number",
                "clerk_file_no",
                "instrument_no",
                "instrument_number",
                "doc_id",
                "document_id",
                "instrument_id",
                "film_code",
                "microfilm",
                "microfilm_code",
                "vol",
                "volume",
                "book",
            }
        ):
            continue
        if hdr_set & inst_type_cols:
            saw_inst_type_column = True

        for row in rows:
            rows_scanned += 1
            raw_fn = _first_present(
                row,
                ("file_no", "file_number", "clerk_file_no", "instrument_no", "instrument_number"),
            )
            raw_fc = _first_present(row, ("film_code", "microfilm", "microfilm_code"))
            raw_vol = _first_present(row, ("vol", "volume", "book"))
            raw_pg = _first_present(row, ("pg", "page"))
            raw_doc_id = _first_present(row, ("doc_id", "document_id", "instrument_id"))
            raw_dt = _first_present(
                row,
                ("file_date", "record_date", "recording_date", "rec_dt", "rec_date", "date_filed", "instrument_date", "dos"),
            )
            raw_yr = _first_present(row, ("file_year", "rec_year", "clerk_yr", "year"))
            cand_dt8 = parse_dos_yyyymmdd(raw_dt, raw_yr, max_year=max_year) if (raw_dt or raw_yr) else "00000000"
            yr_hint = raw_yr or (cand_dt8[:4] if cand_dt8 != "00000000" else "")

            norm_tok = (
                normalize_clerk_tpia_instrument(
                    file_no=raw_fn,
                    film_code=raw_fc,
                    vol=raw_vol,
                    page=raw_pg,
                    rec_year=yr_hint,
                    max_year=max_year,
                )
                if (raw_fn or raw_fc or (raw_vol and raw_pg))
                else ""
            )
            canon_key = format_clerk_match_key(norm_tok) if norm_tok else ""
            doc_key = f"DOC:{raw_doc_id.upper().strip()}" if raw_doc_id else ""

            if canon_key and doc_key:
                prev_alias = doc_id_aliases.get(doc_key)
                if doc_key in instruments:
                    orphan_rec = instruments.pop(doc_key)
                    if canon_key in instruments:
                        _merge_clerk_instrument_records(instruments[canon_key], orphan_rec)
                    else:
                        orphan_rec.join_id = canon_key
                        instruments[canon_key] = orphan_rec
                elif prev_alias and prev_alias != canon_key and prev_alias in instruments:
                    orphan_rec = instruments.pop(prev_alias)
                    if canon_key in instruments:
                        _merge_clerk_instrument_records(instruments[canon_key], orphan_rec)
                    else:
                        orphan_rec.join_id = canon_key
                        instruments[canon_key] = orphan_rec
                doc_id_aliases[doc_key] = canon_key

            join_id = (
                canon_key
                or (doc_id_aliases.get(doc_key, doc_key) if doc_key else "")
                or (raw_fn or raw_fc or (f"VP:{raw_vol}/{raw_pg}" if raw_vol and raw_pg else "")).upper().strip()
            )
            if not join_id:
                continue

            rec = instruments.get(join_id)
            if rec is None:
                rec = ClerkInstrumentRecord(join_id=join_id)
                instruments[join_id] = rec

            if cand_dt8 != "00000000" and (rec.dt8 == "00000000" or rec.dt8.endswith("0000")):
                rec.dt8 = cand_dt8

            if norm_tok and not rec.clerk_token:
                rec.clerk_token = norm_tok
                rec.match_key = canon_key

            raw_type = _first_present(
                row,
                ("inst_type", "instrument_type", "doc_type", "document_type", "type_code", "type_desc", "instrument_code"),
            )
            if raw_type and not rec.raw_inst_type:
                rec.raw_inst_type = raw_type
                cat, wcode = classify_clerk_instrument_type(raw_type)
                rec.category = cat
                rec.wire_type_code = wcode

            raw_amt = _first_present(row, ("consideration", "amount", "contract_amount", "lien_amount"))
            if raw_amt and not rec.amount:
                clean_amt = re.sub(r"[^0-9.]", "", raw_amt)
                if clean_amt:
                    try:
                        amt_int = int(round(float(clean_amt)))
                        if amt_int > 0:
                            rec.amount = str(amt_int)
                    except ValueError:
                        pass

            raw_hcad = _first_present(row, ("acct", "hcad", "hcad_num", "tax_id", "parcel_id"))
            if raw_hcad:
                nh = normalize_standing_hcad_token(raw_hcad)
                if nh:
                    rec.explicit_hcads.add(nh)

            def _add_party(dest: list[str], raw_val: str) -> None:
                if not raw_val:
                    return
                if is_confidential_owner_token(raw_val):
                    rec.has_confidential_party = True
                elif not is_placeholder_owner_token(raw_val):
                    clean_p = sanitize_tpia_party_name(raw_val)
                    if clean_p and clean_p not in dest:
                        dest.append(clean_p)

            _add_party(
                rec.grantors,
                _first_present(row, ("grantor", "grantor_name", "direct_name", "seller", "owner_name")),
            )
            _add_party(
                rec.grantees,
                _first_present(row, ("grantee", "grantee_name", "reverse_name", "buyer", "contractor", "lender")),
            )

            party_role = _first_present(row, ("party_type", "party_role", "role", "party_code", "direct_reverse")).upper()
            party_nm = _first_present(row, ("party_name", "name", "full_name"))
            if party_role and party_nm:
                if party_role in ("1", "D", "DIR", "DIRECT", "GRANTOR", "SELLER", "BORROWER", "TRUSTOR", "OWNER"):
                    _add_party(rec.grantors, party_nm)
                elif party_role in (
                    "2",
                    "R",
                    "REV",
                    "REVERSE",
                    "GRANTEE",
                    "BUYER",
                    "CONTRACTOR",
                    "CLAIMANT",
                    "BUILDER",
                    "LENDER",
                    "BENEFICIARY",
                    "MORTGAGEE",
                ):
                    _add_party(rec.grantees, party_nm)
                elif is_confidential_owner_token(party_nm):
                    rec.has_confidential_party = True

            for lkey in extract_clerk_row_legal_keys(row):
                rec.legal_keys.add(lkey)

    kept_instruments: list[ClerkInstrumentRecord] = []
    for rec in instruments.values():
        if not rec.clerk_token:
            continue
        # Only default untyped Clerk RP rows to CONVEYANCE when no table in clerk_tpia_dir carried an inst_type column
        if not saw_inst_type_column and not rec.raw_inst_type and (rec.grantors or rec.grantees):
            rec.category = "CONVEYANCE"
            rec.wire_type_code = "D"
        if not rec.category:
            skipped_non_target += 1
            continue
        kept_instruments.append(rec)

    return {
        "files_scanned": files_scanned,
        "rows_scanned": rows_scanned,
        "total_assembled_instruments": len(instruments),
        "kept_target_instruments": len(kept_instruments),
        "skipped_non_target_instruments": skipped_non_target,
        "instruments": kept_instruments,
    }


def run_tpia_ingestion(
    *,
    output_dir: Path = DATA_DIR,
    cache_dir: Path = CACHE_DIR,
    app_dir: Path | None = APP_DIR,
    clerk_tpia_dir: Path | None = None,
    hcad_tpia_dir: Path | None = None,
    real_acct_zip: Path | None = None,
    apply: bool = False,
    cache_bust: str | None = None,
    active_tax_year: int = ACTIVE_TAX_YEAR,
    max_parcels_per_legal_key: int = DEFAULT_MAX_PARCELS_PER_LEGAL_KEY,
) -> dict[str, Any]:
    """Execute Day-1 TPIA Clerk + pre-2005 HCAD ingestion in `--dry-run` (default) or `--apply` mode."""
    ddir = Path(output_dir)
    cdir = Path(cache_dir)
    permits_dir = ddir / "permits"
    oh_dir = ddir / "owner_history"
    eff_acct_zip = real_acct_zip if real_acct_zip is not None else (cdir / "Real_acct_owner.zip")

    if (clerk_tpia_dir is None or not Path(clerk_tpia_dir).exists()) and (
        hcad_tpia_dir is None or not Path(hcad_tpia_dir).exists()
    ):
        raise ValueError(
            "Provide at least one existing TPIA directory via --clerk-tpia-dir or --hcad-tpia-dir."
        )

    # 1. Load existing permit & owner_history shards and collect Tier 1 instrument matchKeys + confidential accounts
    permit_shards: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    oh_shards: dict[str, dict[str, str]] = defaultdict(dict)
    confidential_hcads: set[str] = set()
    tier1_inst_to_hcads: dict[str, set[str]] = defaultdict(set)
    target_hcads: set[str] = collect_standing_and_referenced_atlas_hcads(ddir, cdir)

    for pfx, ppath in iter_json_or_gzip_shard_keys(permits_dir, "permits_", r"\d{3}"):
        pdoc = read_json_or_gzip_path(ppath)
        permit_shards[pfx] = pdoc
        for acct, rec in pdoc.items():
            norm_a = normalize_standing_hcad_token(acct)
            if not norm_a or not isinstance(rec, dict):
                continue
            target_hcads.add(norm_a)
            if rec.get("conf"):
                confidential_hcads.add(norm_a)
                continue
            for mkey in parse_existing_compact_deeds(str(rec.get("d") or "")).keys():
                tier1_inst_to_hcads[mkey].add(norm_a)

    for pfx, ohpath in iter_json_or_gzip_shard_keys(oh_dir, "owners_", r"\d{3}"):
        ohdoc = read_json_or_gzip_path(ohpath)
        oh_shards[pfx] = {str(k): str(v) for k, v in ohdoc.items() if isinstance(v, str)}
        for acct, wire in oh_shards[pfx].items():
            norm_a = normalize_standing_hcad_token(acct)
            if not norm_a:
                continue
            target_hcads.add(norm_a)
            _snaps, p_deeds = expand_compact_owner_history_to_snapshots(wire)
            for _dt8, dtok in p_deeds:
                mkey = format_clerk_match_key(dtok)
                if mkey:
                    tier1_inst_to_hcads[mkey].add(norm_a)

    # 2. Build HCAD legal crosswalk index + confidential set from Real_acct_owner.zip (when available)
    legal_index, zip_conf_hcads, ambiguous_legal_keys_count = build_hcad_legal_crosswalk_index(
        eff_acct_zip if eff_acct_zip.exists() else None,
        target_hcads=target_hcads if target_hcads else None,
        max_parcels_per_legal_key=max_parcels_per_legal_key,
    )
    confidential_hcads |= zip_conf_hcads

    # 3. Parse HCAD TPIA & County Clerk TPIA inputs
    hcad_res = ingest_hcad_tpia_tables(
        hcad_tpia_dir, max_year=active_tax_year + 1, active_tax_year=active_tax_year
    )
    confidential_hcads |= hcad_res["confidential_hcads"]
    clerk_res = ingest_clerk_tpia_tables(clerk_tpia_dir, max_year=active_tax_year + 1)

    # 4. Crosswalk County Clerk instruments to 13-digit HCAD parcels (Tier 1 exact instrument -> Tier 2 legal key)
    tier1_matched_instruments = 0
    tier2_matched_instruments = 0
    unmatched_instruments = 0
    confidential_suppressed_instruments = 0

    clerk_conveyances_by_acct: dict[str, list[tuple[str, str, str, str, str]]] = defaultdict(list)
    clerk_ml_by_acct: dict[str, list[tuple[str, str, str, str, str]]] = defaultdict(list)
    clerk_dt_by_acct: dict[str, list[tuple[str, str, str]]] = defaultdict(list)

    for inst in clerk_res["instruments"]:
        matched_hcads: set[str] = set(inst.explicit_hcads)
        matched_via_tier1 = False
        if inst.match_key and inst.match_key in tier1_inst_to_hcads:
            matched_hcads |= tier1_inst_to_hcads[inst.match_key]
            matched_via_tier1 = True

        if not matched_hcads and inst.legal_keys:
            legal_hits: set[str] = set()
            for lk in inst.legal_keys:
                accts_hit = legal_index.get(lk)
                if accts_hit:
                    legal_hits.update(accts_hit)
            if 1 <= len(legal_hits) <= max_parcels_per_legal_key:
                matched_hcads |= legal_hits

        if not matched_hcads:
            unmatched_instruments += 1
            continue

        if matched_via_tier1:
            tier1_matched_instruments += 1
        else:
            tier2_matched_instruments += 1

        if inst.has_confidential_party or (matched_hcads & confidential_hcads):
            confidential_suppressed_instruments += 1
            # Strictly exclude confidential accounts; if the filing itself named a confidential party, drop completely
            if inst.has_confidential_party:
                continue
            matched_hcads -= confidential_hcads
            if not matched_hcads:
                continue

        grantor_str = " & ".join(inst.grantors[:2])
        grantee_str = " & ".join(inst.grantees[:2])

        for acct in sorted(matched_hcads):
            if acct in confidential_hcads:
                continue
            if inst.category in ("CONVEYANCE", "RESTRICTION_PLAT"):
                clerk_conveyances_by_acct[acct].append(
                    (inst.dt8, inst.clerk_token, inst.wire_type_code, grantor_str, grantee_str)
                )
            elif inst.category == "MECHANICS_LIEN":
                contractor = grantee_str or grantor_str
                owner_nm = grantor_str if grantee_str else ""
                if contractor:
                    clerk_ml_by_acct[acct].append(
                        (inst.dt8, inst.clerk_token, contractor, owner_nm, inst.amount)
                    )
            elif inst.category == "DEED_OF_TRUST":
                lender = grantee_str
                if lender:
                    clerk_dt_by_acct[acct].append((inst.dt8, inst.clerk_token, lender))

    # 5. Merge HCAD pre-2005 snapshots + deeds into `oh_shards` and `permit_shards`
    touched_oh_prefixes: set[str] = set()
    touched_permit_prefixes: set[str] = set()
    updated_oh_accounts = 0
    updated_permit_accounts = 0

    # Fail-closed scrub on any newly discovered confidential HCAD accounts
    for conf_acct in sorted(confidential_hcads):
        pfx = conf_acct[:3]
        if conf_acct in oh_shards.get(pfx, {}):
            del oh_shards[pfx][conf_acct]
            touched_oh_prefixes.add(pfx)
        prec = permit_shards.get(pfx, {}).get(conf_acct)
        if isinstance(prec, dict):
            before_prec = dict(prec)
            for k_rm in ("d", "ot", "pid", "m", "co", "flip", *TPIA_PERMIT_SHARD_KEYS):
                prec.pop(k_rm, None)
            prec["conf"] = 1
            if prec != before_prec:
                touched_permit_prefixes.add(pfx)

    all_snapshot_accts = set(hcad_res["snapshots_by_acct"].keys()) | {
        acct
        for acct in (set(hcad_res["deeds_by_acct"].keys()) | set(clerk_conveyances_by_acct.keys()))
        if acct in oh_shards.get(acct[:3], {})
    }

    min_observed_roll_year: int | None = None
    for pfx_map in oh_shards.values():
        for wire_val in pfx_map.values():
            for seg in wire_val.split("|"):
                sp = seg.split("~", 1)[0].strip()
                if len(sp) >= 4 and sp[:4].isdigit():
                    yr_v = int(sp[:4])
                    if min_observed_roll_year is None or yr_v < min_observed_roll_year:
                        min_observed_roll_year = yr_v

    historical_conf_hcads: set[str] = set(hcad_res["historical_conf_hcads"])

    for acct in sorted(all_snapshot_accts):
        if (
            acct in confidential_hcads
            or (target_hcads and acct not in target_hcads)
            or len(acct) < 3
            or not acct[:3].isdigit()
        ):
            continue
        pfx = acct[:3]
        existing_wire = oh_shards.get(pfx, {}).get(acct, "")
        base_snaps, base_deeds = expand_compact_owner_history_to_snapshots(existing_wire)
        extra_snaps = hcad_res["snapshots_by_acct"].get(acct, [])
        combined_snaps = base_snaps + list(extra_snaps)
        if not combined_snaps:
            continue

        deed_pairs: list[tuple[str, str]] = list(base_deeds)
        for dt8, tok, _gr, _ge in hcad_res["deeds_by_acct"].get(acct, ()):
            deed_pairs.append((dt8, tok))
        for dt8, tok, _wc, _gr, _ge in clerk_conveyances_by_acct.get(acct, ()):
            deed_pairs.append((dt8, tok))

        tenures = consolidate_account_tenures(
            combined_snaps,
            deeds=deed_pairs,
            active_tax_year=active_tax_year,
            max_tenures=MAX_OWNER_TENURES_PER_PARCEL,
        )
        new_wire = encode_compact_owner_history(tenures)
        if not new_wire:
            if acct in oh_shards.get(pfx, {}):
                del oh_shards[pfx][acct]
                touched_oh_prefixes.add(pfx)
            continue

        for t in tenures:
            if min_observed_roll_year is None or t.start_year < min_observed_roll_year:
                min_observed_roll_year = t.start_year

        if new_wire != existing_wire:
            oh_shards[pfx][acct] = new_wire
            touched_oh_prefixes.add(pfx)
            updated_oh_accounts += 1

    # Merge deeds (`d`), Mechanic's Liens (`ml`), and Deed of Trust lenders (`dt_lender`) into `permit_shards`
    all_permit_enrich_accts = (
        set(hcad_res["deeds_by_acct"].keys())
        | set(clerk_conveyances_by_acct.keys())
        | set(clerk_ml_by_acct.keys())
        | set(clerk_dt_by_acct.keys())
        | historical_conf_hcads
    )
    for acct in sorted(all_permit_enrich_accts):
        if (
            acct in confidential_hcads
            or (target_hcads and acct not in target_hcads)
            or len(acct) < 3
            or not acct[:3].isdigit()
        ):
            continue
        pfx = acct[:3]
        rec = dict(permit_shards.get(pfx, {}).get(acct) or {})
        if rec.get("conf"):
            continue
        before_rec = dict(rec)
        strip_parties = acct in historical_conf_hcads

        # Merge `d` (clearing Grantor/Grantee party names if the parcel had any historical Sec. 25.025 tenure)
        deed_map = parse_existing_compact_deeds(str(rec.get("d") or ""))
        if strip_parties and deed_map:
            deed_map = {
                mk: (v[0], v[1], v[2], v[3], "", "")
                for mk, v in deed_map.items()
            }
        for dt8, tok, grantor, grantee in hcad_res["deeds_by_acct"].get(acct, ()):
            mkey = format_clerk_match_key(tok)
            if not mkey:
                continue
            eff_gr = "" if strip_parties else grantor
            eff_ge = "" if strip_parties else grantee
            prev = deed_map.get(mkey)
            if prev is None:
                deed_map[mkey] = (dt8, tok, "", "D", eff_gr, eff_ge)
            else:
                eff_dt = dt8 if (dt8 != "00000000" and prev[0] == "00000000") else prev[0]
                deed_map[mkey] = (
                    eff_dt,
                    prev[1],
                    prev[2],
                    prev[3],
                    "" if strip_parties else (eff_gr or prev[4]),
                    "" if strip_parties else (eff_ge or prev[5]),
                )

        for dt8, tok, wcode, grantor, grantee in clerk_conveyances_by_acct.get(acct, ()):
            mkey = format_clerk_match_key(tok)
            if not mkey:
                continue
            eff_gr = "" if strip_parties else grantor
            eff_ge = "" if strip_parties else grantee
            prev = deed_map.get(mkey)
            if prev is None:
                deed_map[mkey] = (dt8, tok, "", wcode or "D", eff_gr, eff_ge)
            else:
                eff_dt = dt8 if (dt8 != "00000000" and (prev[0] == "00000000" or prev[0].endswith("0000"))) else prev[0]
                eff_code = wcode if (prev[3] == "D" and wcode and wcode != "D") else prev[3]
                deed_map[mkey] = (
                    eff_dt,
                    prev[1],
                    prev[2],
                    eff_code,
                    "" if strip_parties else (eff_gr or prev[4]),
                    "" if strip_parties else (eff_ge or prev[5]),
                )

        if deed_map:
            rec["d"] = encode_compact_deed_records(list(deed_map.values()))

        if strip_parties:
            rec.pop("ml", None)
            rec.pop("dt_lender", None)
        else:
            # Merge `ml` (Mechanic's Liens / Building Contracts)
            if acct in clerk_ml_by_acct:
                merged_ml = merge_compact_mechanics_liens(
                    str(rec.get("ml") or ""), clerk_ml_by_acct[acct]
                )
                if merged_ml:
                    rec["ml"] = merged_ml

            # Merge `dt_lender` (Deed of Trust institutional lender signals)
            if acct in clerk_dt_by_acct:
                merged_dt = merge_compact_dt_lenders(
                    str(rec.get("dt_lender") or ""), clerk_dt_by_acct[acct]
                )
                if merged_dt:
                    rec["dt_lender"] = merged_dt

        if rec != before_rec:
            permit_shards[pfx][acct] = rec
            touched_permit_prefixes.add(pfx)
            updated_permit_accounts += 1

    # 6. Pre-compress touched `.json.gz` shards and validate bundle size ceilings before any disk write
    staged_permit_gz: dict[str, bytes] = {}
    for pfx in sorted(touched_permit_prefixes):
        s_dict = permit_shards.get(pfx) or {}
        if s_dict:
            gz_b = compress_deterministic_gzip_json(sanitize_json_structure_dashes(s_dict))
            if len(gz_b) >= MAX_SINGLE_FILE_BYTES:
                raise RuntimeError(
                    f"TPIA permit shard permits_{pfx}.json.gz ({len(gz_b)} bytes) exceeds MAX_SINGLE_FILE_BYTES."
                )
            staged_permit_gz[pfx] = gz_b

    staged_oh_gz: dict[str, bytes] = {}
    for pfx in sorted(touched_oh_prefixes):
        oh_dict = oh_shards.get(pfx) or {}
        if oh_dict:
            gz_b = compress_deterministic_gzip_json(sanitize_json_structure_dashes(oh_dict))
            if len(gz_b) >= MAX_SINGLE_FILE_BYTES:
                raise RuntimeError(
                    f"TPIA owner_history shard owners_{pfx}.json.gz ({len(gz_b)} bytes) exceeds MAX_SINGLE_FILE_BYTES."
                )
            staged_oh_gz[pfx] = gz_b

    projected_app_bytes = 0
    if app_dir is not None and Path(app_dir).exists():
        base_app = compute_app_total_bytes(Path(app_dir))
        cur_p_bytes = sum_json_or_gzip_shard_bytes(permits_dir, "permits_", r"\d{3}")
        cur_oh_bytes = sum_json_or_gzip_shard_bytes(oh_dir, "owners_", r"\d{3}")
        next_p_bytes = 0
        for pfx_k, ppath_k in iter_json_or_gzip_shard_keys(permits_dir, "permits_", r"\d{3}"):
            if pfx_k not in touched_permit_prefixes:
                next_p_bytes += ppath_k.stat().st_size
        next_p_bytes += sum(len(b) for b in staged_permit_gz.values())
        next_oh_bytes = 0
        for pfx_k, ohpath_k in iter_json_or_gzip_shard_keys(oh_dir, "owners_", r"\d{3}"):
            if pfx_k not in touched_oh_prefixes:
                next_oh_bytes += ohpath_k.stat().st_size
        next_oh_bytes += sum(len(b) for b in staged_oh_gz.values())
        projected_app_bytes = max(0, base_app - cur_p_bytes - cur_oh_bytes) + next_p_bytes + next_oh_bytes
        if projected_app_bytes >= HARD_MAX_APP_BYTES:
            raise RuntimeError(
                f"Projected app/ bundle after TPIA ingestion ({projected_app_bytes} bytes) "
                f"exceeds HARD_MAX_APP_BYTES ({HARD_MAX_APP_BYTES} bytes)."
            )

    if apply:
        permits_dir.mkdir(parents=True, exist_ok=True)
        oh_dir.mkdir(parents=True, exist_ok=True)
        for pfx in sorted(touched_permit_prefixes):
            gz_path = permits_dir / f"permits_{pfx}.json.gz"
            legacy_json = permits_dir / f"permits_{pfx}.json"
            if pfx in staged_permit_gz:
                tmp_p = gz_path.with_name(f".{gz_path.name}.{os.getpid()}.tmp")
                try:
                    tmp_p.write_bytes(staged_permit_gz[pfx])
                    tmp_p.replace(gz_path)
                finally:
                    tmp_p.unlink(missing_ok=True)
                legacy_json.unlink(missing_ok=True)
            else:
                gz_path.unlink(missing_ok=True)
                legacy_json.unlink(missing_ok=True)

        for pfx in sorted(touched_oh_prefixes):
            gz_path = oh_dir / f"owners_{pfx}.json.gz"
            if pfx in staged_oh_gz:
                tmp_oh = gz_path.with_name(f".{gz_path.name}.{os.getpid()}.tmp")
                try:
                    tmp_oh.write_bytes(staged_oh_gz[pfx])
                    tmp_oh.replace(gz_path)
                finally:
                    tmp_oh.unlink(missing_ok=True)
            else:
                gz_path.unlink(missing_ok=True)

        manifest_path = ddir / "pmtiles_manifest.json"
        if manifest_path.exists():
            active_oh_prefixes = sorted(
                pfx for pfx, oh_d in oh_shards.items() if oh_d
            )
            active_oh_accounts = sum(len(oh_d) for oh_d in oh_shards.values() if oh_d)
            active_permit_prefixes = sorted(
                pfx for pfx, p_d in permit_shards.items() if p_d
            )
            eff_span = (
                [min(min_observed_roll_year, 2005), active_tax_year]
                if min_observed_roll_year is not None
                else None
            )
            update_pmtiles_manifest_v3(
                manifest_path,
                cache_bust=cache_bust,
                owner_history_prefixes=active_oh_prefixes,
                owner_history_span=eff_span,
                owner_history_accounts=active_oh_accounts,
                permit_shard_prefixes=active_permit_prefixes,
            )

    return {
        "apply": bool(apply),
        "hcad_files_scanned": hcad_res["files_scanned"],
        "hcad_snapshot_rows_parsed": hcad_res["snapshot_rows_parsed"],
        "hcad_deed_rows_parsed": hcad_res["deed_rows_parsed"],
        "clerk_files_scanned": clerk_res["files_scanned"],
        "clerk_rows_scanned": clerk_res["rows_scanned"],
        "clerk_target_instruments": clerk_res["kept_target_instruments"],
        "clerk_skipped_non_target": clerk_res["skipped_non_target_instruments"],
        "legal_crosswalk_keys": len(legal_index),
        "ambiguous_legal_keys_skipped": ambiguous_legal_keys_count,
        "tier1_matched_instruments": tier1_matched_instruments,
        "tier2_matched_instruments": tier2_matched_instruments,
        "unmatched_instruments": unmatched_instruments,
        "confidential_suppressed_instruments": confidential_suppressed_instruments,
        "updated_owner_history_accounts": updated_oh_accounts,
        "updated_permit_accounts": updated_permit_accounts,
        "touched_owner_history_shards": len(touched_oh_prefixes),
        "touched_permit_shards": len(touched_permit_prefixes),
        "owner_history_min_year": min_observed_roll_year,
        "projected_app_bytes": projected_app_bytes,
    }


def format_tpia_ingestion_cli_summary(stats: dict[str, Any], *, elapsed_s: float) -> str:
    """Render a concise operator CLI summary for `--mode ingest-tpia`."""
    mode_lbl = "APPLY (promoted .json.gz shards)" if stats.get("apply") else "DRY-RUN (zero live mutations)"
    proj_mib = stats.get("projected_app_bytes", 0) / (1024 * 1024)
    return "\n".join([
        f"=== Houston Building Atlas TPIA Ingestion ({mode_lbl}) ===",
        f"   Elapsed time                 : {elapsed_s:.1f}s",
        f"   HCAD TPIA files / snaps/deeds: {stats['hcad_files_scanned']} files / {stats['hcad_snapshot_rows_parsed']:,} snapshots / {stats['hcad_deed_rows_parsed']:,} deeds",
        f"   Clerk TPIA files / filings   : {stats['clerk_files_scanned']} files / {stats['clerk_target_instruments']:,} target ({stats['clerk_skipped_non_target']:,} non-target skipped)",
        f"   Crosswalk matches (T1 / T2)  : {stats['tier1_matched_instruments']:,} exact instrument / {stats['tier2_matched_instruments']:,} legal description ({stats['ambiguous_legal_keys_skipped']:,} ambiguous keys capped)",
        f"   Sec. 25.025 shielded filings : {stats['confidential_suppressed_instruments']:,}",
        f"   Updated accounts (oh / perm) : {stats['updated_owner_history_accounts']:,} owner_history / {stats['updated_permit_accounts']:,} permits",
        f"   Projected app/ bundle size   : {proj_mib:.2f} MiB",
    ])
