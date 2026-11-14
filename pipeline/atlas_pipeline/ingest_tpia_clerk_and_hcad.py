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
from concurrent.futures import ThreadPoolExecutor
import csv
from dataclasses import dataclass, field
import functools
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
    write_compact_json,
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
    r"\b(?:"
    r"(?:PARTIAL\s+REPLAT|PAR\s+R\s*/?\s*P|REPLAT|R\s*/?\s*P|AMENDING\s+PLAT|AMENDED\s+PLAT)"
    r"(?:\s+NO\.?\s*[0-9A-Z]+)?(?:\s+AND\s+EXTENSION)?"
    r"|(?:(?:FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH|SEVENTH|EIGHTH|NINTH|TENTH|[0-9]+(?:ST|ND|RD|TH))\s+)?"
    r"AMEND(?:ED|MENT|ING)?"
    r"|DECLARATION\s+OF\s+CONDOMINIUM(?:S)?"
    r"|ADDITION|ADDN|ADD|SUBDIVISION|SUBD|SUB|CONDOMINIUMS?|CONDOS?|TOWNHOMES?|T\s*/?\s*H"
    r")\b",
    re.IGNORECASE,
)
_RE_IS_REPLAT_DESC = re.compile(
    r"\b(?:PARTIAL\s+REPLAT|PAR\s+R\s*/?\s*P|REPLAT|R\s*/?\s*P|AMENDING\s+PLAT|AMENDED\s+PLAT|"
    r"AMEND(?:ED|MENT|ING)?|DECLARATION\s+OF\s+CONDOMINIUM)\b",
    re.IGNORECASE,
)
_RE_NM_TAG = re.compile(r"\(\s*NM\s*\)", re.IGNORECASE)
_RE_SEC_EXTRACT = re.compile(
    r"\b(?:SEC(?:TION)?(?:\b|\s+|(?=[0-9])))\s*0*([0-9A-Z]{1,4})\b",
    re.IGNORECASE,
)
_RE_BLK_EXTRACT = re.compile(r"\b(?:BLK|BLOCK)\s*0*([0-9A-Z]{1,5})\b", re.IGNORECASE)
_RE_UNIT_EXTRACT = re.compile(r"\b(?:UNIT|APT|BLDG\s+[0-9A-Z]+\s+UNIT)\s*0*([0-9A-Z\-]{1,8})\b", re.IGNORECASE)
_RE_RES_EXTRACT = re.compile(r"\b(?:RES(?:ERVE)?)\s+([0-9A-Z]{1,5})\b", re.IGNORECASE)
_RE_LOT_MULTI = re.compile(
    r"\b(?:LTS|LOTS|LT|LOT)\s+([0-9A-Z]{1,5}(?:\s*(?:&|AND|,|THRU|-)\s*[0-9A-Z]{1,5})*)\b",
    re.IGNORECASE,
)
_RE_TRACT_SINGLE = re.compile(r"\b(?:TR(?:ACT)?)\s*0*([0-9A-Z]{1,5})\b", re.IGNORECASE)
_RE_COMMENT_LOT_BLK = re.compile(
    r"\bL(?:OTS?|TS?)?\s*0*([0-9A-Z]{1,4}(?:\s*(?:&|AND|-|THRU)\s*0*[0-9A-Z]{1,4})?)"
    r"\s+B(?:LOCKS?|LK)?\s*0*([0-9A-Z]{1,5})\b",
    re.IGNORECASE,
)
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
    "TRSALE",
    "EXEC DEED",
    "EXECUTORS DEED",
    "ADM DEED",
    "ADMINISTRATORS DEED",
    "PROB",
    "PROBATE",
    "WILL",
    "MUNIMENT OF TITLE",
    "AFFIDAVIT OF HEIRSHIP",
    "AOH",
    "B/S",
    "BARGAIN AND SALE DEED",
    "CORRECTION DEED",
    "CORR DEED",
    "CORREC",
    "CINSTR",
    "PARTITION DEED",
    "DECREE",
    "CONVEY",
    "PTCNVY",
    "RCNVY",
    "CONDMN",
    "RWD",
    "R/W/D",
})

_MECHANICS_LIEN_CODES: frozenset[str] = frozenset({
    "M/L",
    "ML",
    "MECH LIEN",
    "MECHANICS LIEN",
    "MECHANIC LIEN",
    "MECHANIC'S LIEN",
    "MECHANICS LIEN CONTRACT",
    "L AFFT",
    "LAFFT",
    "LIEN AFFT",
    "LIEN AFFIDAVIT",
    "BLDG CONT",
    "BUILDING CONTRACT",
    "CONSTRUCTION CONTRACT",
    "CONT",
    "LIEN CONTRACT",
})

_DEED_OF_TRUST_CODES: frozenset[str] = frozenset({
    "D/T",
    "DT",
    "SUPD/T",
    "SUP D/T",
    "SUBD/T",
    "MST DT",
    "MST-DT",
    "DEED OF TRUST",
    "MORTGAGE",
    "MTG",
    "SECURITY INSTRUMENT",
})

_PLAT_OR_RESTRICTION_CODES: frozenset[str] = frozenset({
    "PLAT",
    "REPLAT",
    "MAP",
    "REMAP",
    "SUBD",
    "CONDO",
    "CONDO DECL",
    "CONDOMINIUM DECLARATION",
    "RESTR",
    "DREST",
    "DECLAR",
    "AMEND",
    "RESTRICTIONS",
    "DEED RESTRICTIONS",
    "DED",
    "DEDICATION",
})

_TPIA_PLACEHOLDER_PARTY_TOKENS: frozenset[str] = frozenset({
    "SEE INSTRUMENT",
    "SEE RECORD",
    "SEE DEED",
    "SEE PLAT",
    "SEE MAP",
    "N/A",
    "NA",
    "NONE",
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
    related_match_keys: set[str] = field(default_factory=set)
    has_confidential_party: bool = False
    map_code: str = ""
    map_desc: str = ""
    map_vol_page: str = ""
    page_count: int = 0
    surveys: list[str] = field(default_factory=list)
    abstracts: list[str] = field(default_factory=list)


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


def _detect_stream_encoding(sample_bytes: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            # Ignore a truncated trailing multi-byte sequence at the sample boundary
            import codecs

            dec = codecs.getincrementaldecoder(enc)()
            dec.decode(sample_bytes, final=False)
            return enc
        except UnicodeDecodeError:
            continue
    return "latin-1"


def iter_tpia_tabular_files(
    input_dir: Path | None,
    *,
    allowed_first_col: set[str] | None = None,
    skip_filenames: set[str] | None = None,
) -> Iterator[tuple[str, list[str], Iterator[dict[str, str]]]]:
    """Yield `(source_label, normalized_headers, row_dict_iterator)` across plain, `.gz`, and `.zip` files."""
    if input_dir is None or not Path(input_dir).exists():
        return
    root = Path(input_dir)
    files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file() and not p.name.startswith("."))

    def _build_row_map(norm_hdr: list[str], parts: list[str], is_rpi_5col: bool) -> dict[str, str]:
        if is_rpi_5col and len(parts) >= 7 and parts[3].strip().upper() in ("DEED", "MTG", "DT", "REST", "MISC"):
            row_7: dict[str, str] = {}
            for k, v in (
                ("file_no", parts[0].strip()),
                ("document_type", parts[1].strip()),
                ("filedate", parts[2].strip()),
                ("vol", parts[4].strip()),
                ("page", parts[5].strip()),
                ("no_of_pages", parts[6].strip()),
            ):
                if v:
                    row_7[k] = v
            return row_7
        row_map: dict[str, str] = {}
        n_parts = len(parts)
        for i, col_name in enumerate(norm_hdr):
            if not col_name or i >= n_parts:
                continue
            val = parts[i].strip()
            if val and col_name not in row_map:
                row_map[col_name] = val
        return row_map

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
        is_rpi_5col = norm_hdr == ["file_no", "document_type", "filedate", "film_code_no", "no_of_pages"]

        def _row_iter() -> Iterator[dict[str, str]]:
            for parts in reader:
                if not parts or not any(p.strip() for p in parts):
                    continue
                row_map = _build_row_map(norm_hdr, parts, is_rpi_5col)
                if row_map:
                    yield row_map

        return label, norm_hdr, _row_iter()

    def _parse_disk_file_stream(
        label: str, fpath: Path, *, is_gz: bool = False
    ) -> tuple[str, list[str], Iterator[dict[str, str]]] | None:
        try:
            if is_gz:
                with gzip.open(fpath, "rb") as gzf:
                    sample = gzf.read(65536)
            else:
                with open(fpath, "rb") as bf:
                    sample = bf.read(65536)
        except OSError:
            return None
        if not sample or not sample.strip():
            return None
        enc = _detect_stream_encoding(sample)
        sample_text = sample.decode(enc, errors="replace")
        first_line = ""
        for ln in sample_text.splitlines():
            if ln.strip():
                first_line = ln
                break
        if not first_line:
            return None
        delim = _detect_delimiter(first_line)
        try:
            raw_hdr = next(csv.reader([first_line], delimiter=delim))
        except Exception:
            return None
        norm_hdr = [_norm_col_name(c) for c in raw_hdr]
        if not any(norm_hdr):
            return None
        is_rpi_5col = norm_hdr == ["file_no", "document_type", "filedate", "film_code_no", "no_of_pages"]
        fast_split = delim in ("|", "\t")
        use_first_col_filter = (
            allowed_first_col is not None
            and fast_split
            and bool(norm_hdr)
            and norm_hdr[0] in ("file_no", "fileno", "file_number", "clerk_file_no", "instrument_no")
            and not fpath.name.lower().startswith("maps")
        )

        def _disk_row_iter() -> Iterator[dict[str, str]]:
            open_ctx = (
                gzip.open(fpath, "rt", encoding=enc, errors="replace", newline="")
                if is_gz
                else open(fpath, "r", encoding=enc, errors="replace", newline="")
            )
            with open_ctx as tf:
                saw_header = False
                for line in tf:
                    if not saw_header:
                        if line.strip():
                            saw_header = True
                        continue
                    if use_first_col_filter:
                        first_tok = line.partition(delim)[0].strip().upper()
                        if first_tok not in allowed_first_col:  # type: ignore[operator]
                            continue
                    stripped = line.rstrip("\r\n")
                    if not stripped.strip():
                        continue
                    if fast_split and '"' not in stripped:
                        parts = stripped.split(delim)
                    else:
                        try:
                            parts = next(csv.reader([stripped], delimiter=delim))
                        except Exception:
                            continue
                    row_map = _build_row_map(norm_hdr, parts, is_rpi_5col)
                    if row_map:
                        yield row_map

        return label, norm_hdr, _disk_row_iter()

    def _iter_zip_archive(
        outer_label: str, zf_source: Path | bytes, depth: int = 0
    ) -> Iterator[tuple[str, list[str], Iterator[dict[str, str]]]]:
        try:
            zf_ctx = (
                zipfile.ZipFile(zf_source, "r")
                if isinstance(zf_source, Path)
                else zipfile.ZipFile(io.BytesIO(zf_source), "r")
            )
            with zf_ctx as zf:
                for member in sorted(zf.namelist()):
                    if member.endswith("/") or Path(member).name.startswith("."):
                        continue
                    m_low = member.lower()
                    m_suf = Path(m_low).suffix
                    m_label = f"{outer_label}:{member}"
                    if m_suf == ".zip" and depth < 2:
                        yield from _iter_zip_archive(m_label, zf.read(member), depth + 1)
                    elif m_suf == ".gz" and not m_low.endswith(".json.gz"):
                        inner_suf = Path(m_low[:-3]).suffix
                        if inner_suf in _TABULAR_SUFFIXES or not inner_suf:
                            parsed = _parse_text_stream(m_label, gzip.decompress(zf.read(member)))
                            if parsed is not None:
                                yield parsed
                    elif m_suf in _TABULAR_SUFFIXES:
                        parsed = _parse_text_stream(m_label, zf.read(member))
                        if parsed is not None:
                            yield parsed
        except (zipfile.BadZipFile, OSError):
            return

    for fpath in files:
        if skip_filenames and fpath.name.lower() in skip_filenames:
            continue
        low_name = fpath.name.lower()
        if low_name.endswith(".zip"):
            yield from _iter_zip_archive(fpath.name, fpath, depth=0)
        elif low_name.endswith(".gz") and not low_name.endswith(".json.gz"):
            base_suf = Path(low_name[:-3]).suffix.lower()
            if base_suf in _TABULAR_SUFFIXES or not base_suf:
                parsed = _parse_disk_file_stream(fpath.name, fpath, is_gz=True)
                if parsed is not None:
                    yield parsed
        elif fpath.suffix.lower() in _TABULAR_SUFFIXES:
            parsed = _parse_disk_file_stream(fpath.name, fpath, is_gz=False)
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

    # Pre-1961 numeric Clerk File Numbers (1..7 digits, e.g. Maps&CondosInstruments.txt 1897-1960)
    if raw_fn:
        m_num = re.fullmatch(r"0*([1-9]\d{0,6})", raw_fn)
        if m_num:
            return m_num.group(1)

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
        wire_code = (
            "O"
            if clean in ("PROB", "PROBATE", "WILL", "MUNIMENT OF TITLE", "AFFIDAVIT OF HEIRSHIP", "AOH")
            else "D"
        )
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


@functools.lru_cache(maxsize=131072)
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


@functools.lru_cache(maxsize=131072)
def _expand_lot_tokens_cached(raw_lot_expr: str, max_lots: int = 4) -> tuple[str, ...]:
    clean = sanitize_atlas_text(raw_lot_expr).upper().strip()
    if not clean:
        return ()
    # Range expression "6 THRU 10" or "6-10"
    m_range = re.fullmatch(r"0*(\d{1,4})\s*(?:THRU|-)\s*0*(\d{1,4})", clean)
    if m_range:
        lo, hi = int(m_range.group(1)), int(m_range.group(2))
        if 0 < lo <= hi and (hi - lo + 1) <= max_lots:
            return tuple(str(x) for x in range(lo, hi + 1))
        return ()
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
            return ()
    return tuple(out)


def _expand_lot_tokens(raw_lot_expr: str, max_lots: int = 4) -> list[str]:
    """Extract discrete normalized lot designators from expressions like `'14'`, `'14 & 15'`, or `'014A'`."""
    return list(_expand_lot_tokens_cached(raw_lot_expr, max_lots))


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
        (
            "subdivision",
            "subd",
            "subd_desc",
            "subdivision_name",
            "addition",
            "condo_name",
            "mapdesc",
            "map_desc",
            "map_description",
            "legal_desc",
            "description",
        ),
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

    raw_tr = _first_present(row, ("tract", "tr", "tract_no", "outlot", "out_lot"))
    if raw_tr:
        t_clean = re.sub(r"[^0-9A-Z]", "", raw_tr.upper()).lstrip("0")
        if t_clean:
            return [LegalCrosswalkKey(subd_norm, sec_norm, blk_norm, "TR", t_clean)]

    raw_comment = _first_present(row, ("comment", "misc"))
    if raw_comment:
        m_cb = _RE_COMMENT_LOT_BLK.search(raw_comment)
        if m_cb:
            lots = _expand_lot_tokens(m_cb.group(1))
            blk_c = re.sub(r"[^0-9A-Z]", "", m_cb.group(2).upper()).lstrip("0") or "0"
            eff_blk = blk_norm or blk_c
            if lots:
                return [LegalCrosswalkKey(subd_norm, sec_norm, eff_blk, "LOT", lt) for lt in lots]

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


_CATEGORY_PRIORITY: dict[str, int] = {
    "CONVEYANCE": 4,
    "RESTRICTION_PLAT": 3,
    "MECHANICS_LIEN": 2,
    "DEED_OF_TRUST": 1,
    "": 0,
}


def _merge_clerk_instrument_records(
    dst: ClerkInstrumentRecord, src: ClerkInstrumentRecord
) -> ClerkInstrumentRecord:
    """Merge a provisional `doc_id`-keyed `ClerkInstrumentRecord` into its canonical instrument record."""
    if not dst.clerk_token and src.clerk_token:
        dst.clerk_token = src.clerk_token
        dst.match_key = src.match_key
    if src.dt8 != "00000000" and (dst.dt8 == "00000000" or dst.dt8.endswith("0000")):
        dst.dt8 = src.dt8
    src_prio = _CATEGORY_PRIORITY.get(src.category, 0)
    dst_prio = _CATEGORY_PRIORITY.get(dst.category, 0)
    if (not dst.raw_inst_type and src.raw_inst_type) or (src_prio > dst_prio):
        dst.raw_inst_type = src.raw_inst_type
        dst.category = src.category
        dst.wire_type_code = src.wire_type_code
    if not dst.amount and src.amount:
        dst.amount = src.amount
    if not dst.map_code and src.map_code:
        dst.map_code = src.map_code
    if not dst.map_desc and src.map_desc:
        dst.map_desc = src.map_desc
    if not dst.map_vol_page and src.map_vol_page:
        dst.map_vol_page = src.map_vol_page
    if not dst.page_count and src.page_count:
        dst.page_count = src.page_count
    dst.explicit_hcads |= src.explicit_hcads
    dst.legal_keys |= src.legal_keys
    dst.related_match_keys |= src.related_match_keys
    if src.has_confidential_party:
        dst.has_confidential_party = True
    for g in src.grantors:
        if g not in dst.grantors:
            dst.grantors.append(g)
    for g in src.grantees:
        if g not in dst.grantees:
            dst.grantees.append(g)
    for sv in src.surveys:
        if sv not in dst.surveys:
            dst.surveys.append(sv)
    for ab in src.abstracts:
        if ab not in dst.abstracts:
            dst.abstracts.append(ab)
    return dst


def format_clerk_display_token(clerk_token: str) -> str:
    """Render a compact Atlas Clerk token (`R2026-435651`) in official public display form (`RP-2026-435651`)."""
    tok = str(clerk_token or "").strip().upper()
    if re.fullmatch(r"R20\d{2}-\d+", tok):
        return f"RP-{tok[1:]}"
    return tok


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
    """Assemble County Clerk `PIA.aspx` relational (Header + Party + Legal) or flat export tables.

    Uses a Two-Pass Streaming Pre-Filter:
      - Pass 1 scans typed Instrument/Header/Flat tables (`inst_type` / `document_type` / `mapcode`)
        and immediately rejects non-target codes (`REL`, `A/J`, `CONSNT`, etc.) before allocating
        `ClerkInstrumentRecord` objects.
      - Pass 2 scans untyped child tables (`Names`, `LegalDesc`, `RelatedDocs`) and skips rows
        whose `join_id` is not in `instruments` whenever Pass 1 saw at least one typed table.
    """
    instruments: dict[str, ClerkInstrumentRecord] = {}
    doc_id_aliases: dict[str, str] = {}
    skipped_join_ids: set[str] = set()
    type_cache: dict[str, tuple[str, str]] = {}
    saw_inst_type_column = False
    files_scanned = 0
    rows_scanned = 0

    id_cols = {
        "file_no",
        "fileno",
        "file_number",
        "clerk_file_no",
        "instrument_no",
        "instrument_number",
        "doc_id",
        "document_id",
        "instrument_id",
        "film_code",
        "film_code_no",
        "microfilm",
        "microfilm_code",
        "vol",
        "volume",
        "volumnpage",
        "volumn_page",
        "volume_page",
        "book",
    }
    inst_type_cols = {
        "inst_type",
        "instrument_type",
        "doc_type",
        "document_type",
        "type_code",
        "type_desc",
        "instrument_code",
        "mapcode",
        "map_code",
    }

    def _classify_cached(raw_t: str) -> tuple[str, str]:
        hit = type_cache.get(raw_t)
        if hit is None:
            hit = classify_clerk_instrument_type(raw_t)
            type_cache[raw_t] = hit
        return hit

    def _process_table_rows(rows: Iterator[dict[str, str]], *, is_typed_pass: bool) -> None:
        nonlocal rows_scanned
        for row in rows:
            rows_scanned += 1
            raw_type = _first_present(
                row,
                ("inst_type", "instrument_type", "doc_type", "document_type", "type_code", "type_desc", "instrument_code"),
            )
            raw_map_code = _first_present(row, ("mapcode", "map_code")).upper()
            if not raw_type and raw_map_code in ("M", "C", "MAP", "CONDO", "PLAT"):
                raw_type = "MAP" if raw_map_code == "M" else ("CONDO" if raw_map_code == "C" else raw_map_code)

            cat = ""
            wcode = "D"
            if raw_type:
                cat, wcode = _classify_cached(raw_type)
                if is_typed_pass and not cat:
                    raw_fn_skip = _first_present(
                        row,
                        ("file_no", "fileno", "file_number", "clerk_file_no", "instrument_no", "instrument_number"),
                    )
                    raw_fc_skip = _first_present(
                        row,
                        ("film_code", "film_code_no", "microfilm", "microfilm_code", "volumnpage", "volumn_page", "volume_page"),
                    )
                    raw_vol_skip = _first_present(row, ("vol", "volume", "book"))
                    raw_pg_skip = _first_present(row, ("pg", "page"))
                    skip_tok = normalize_clerk_tpia_instrument(
                        file_no=raw_fn_skip,
                        film_code=raw_fc_skip,
                        vol=raw_vol_skip,
                        page=raw_pg_skip,
                        max_year=max_year,
                    )
                    skip_key = (
                        format_clerk_match_key(skip_tok)
                        if skip_tok
                        else (
                            raw_fn_skip
                            or _first_present(row, ("doc_id", "document_id", "instrument_id"))
                            or raw_fc_skip
                        )
                        .strip()
                        .upper()
                    )
                    if skip_key:
                        skipped_join_ids.add(skip_key)
                    continue

            raw_fn = _first_present(
                row,
                ("file_no", "fileno", "file_number", "clerk_file_no", "instrument_no", "instrument_number"),
            )
            raw_fc = _first_present(
                row,
                ("film_code", "film_code_no", "microfilm", "microfilm_code", "volumnpage", "volumn_page", "volume_page"),
            )
            raw_vol = _first_present(row, ("vol", "volume", "book"))
            raw_pg = _first_present(row, ("pg", "page"))
            raw_doc_id = _first_present(row, ("doc_id", "document_id", "instrument_id"))
            raw_dt = _first_present(
                row,
                (
                    "file_date",
                    "filedate",
                    "record_date",
                    "recording_date",
                    "rec_dt",
                    "rec_date",
                    "date_filed",
                    "instrument_date",
                    "dos",
                ),
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
                if not is_typed_pass and saw_inst_type_column:
                    # Fast Pre-Filter: skip child rows belonging to non-target instruments rejected in Pass 1
                    continue
                rec = ClerkInstrumentRecord(join_id=join_id)
                instruments[join_id] = rec

            if cand_dt8 != "00000000" and (rec.dt8 == "00000000" or rec.dt8.endswith("0000")):
                rec.dt8 = cand_dt8

            if norm_tok and not rec.clerk_token:
                rec.clerk_token = norm_tok
                rec.match_key = canon_key

            if raw_type and not rec.raw_inst_type:
                rec.raw_inst_type = raw_type
                rec.category = cat
                rec.wire_type_code = wcode

            if raw_map_code and not rec.map_code:
                rec.map_code = raw_map_code
            raw_map_desc = _first_present(row, ("mapdesc", "map_desc", "map_description"))
            if raw_map_desc and not rec.map_desc:
                rec.map_desc = sanitize_tpia_party_name(raw_map_desc)
            raw_vp = _first_present(row, ("volumnpage", "volumn_page", "volume_page"))
            if raw_vp and not rec.map_vol_page:
                rec.map_vol_page = sanitize_atlas_text(raw_vp).strip()
            raw_pages = _first_present(row, ("nopages", "no_pages", "no_of_pages", "pages", "page_count"))
            if raw_pages and raw_pages.isdigit() and not rec.page_count:
                rec.page_count = int(raw_pages)

            raw_surv = _first_present(row, ("survey", "surevey", "orig_survey", "original_survey"))
            if raw_surv and raw_surv.strip().upper() not in ("N/A", "NA", "NONE", "NULL", "UNKNOWN", "-"):
                clean_surv = sanitize_tpia_party_name(raw_surv).upper()
                if (
                    len(clean_surv) > 1
                    and clean_surv not in _TPIA_PLACEHOLDER_PARTY_TOKENS
                    and clean_surv not in ("N/A", "NA", "NONE", "NULL", "UNKNOWN")
                    and clean_surv not in rec.surveys
                ):
                    rec.surveys.append(clean_surv)
            raw_abst = _first_present(row, ("abstract", "abstracts", "abst", "abstract_no"))
            if raw_abst and raw_abst.strip().upper() not in ("N/A", "NA", "NONE", "NULL", "UNKNOWN", "-"):
                clean_abst = re.sub(r"^A", "", re.sub(r"[^0-9A-Z]", "", raw_abst.upper())).lstrip("0")
                if clean_abst and any(ch.isdigit() for ch in clean_abst) and clean_abst not in rec.abstracts:
                    rec.abstracts.append(clean_abst)

            raw_rel = _first_present(
                row,
                ("reldocno", "rel_doc_no", "related_doc_no", "related_document_number", "related_file_no"),
            )
            if raw_rel:
                rel_tok = normalize_clerk_tpia_instrument(file_no=raw_rel, rec_year=yr_hint, max_year=max_year)
                rel_mkey = format_clerk_match_key(rel_tok) if rel_tok else ""
                if rel_mkey and rel_mkey != rec.match_key:
                    rec.related_match_keys.add(rel_mkey)

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
                elif (
                    not is_placeholder_owner_token(raw_val)
                    and raw_val.strip().upper() not in _TPIA_PLACEHOLDER_PARTY_TOKENS
                ):
                    clean_p = sanitize_tpia_party_name(raw_val)
                    if (
                        clean_p
                        and clean_p.upper() not in _TPIA_PLACEHOLDER_PARTY_TOKENS
                        and clean_p not in dest
                    ):
                        dest.append(clean_p)

            _add_party(
                rec.grantors,
                _first_present(row, ("grantor", "grantor_name", "direct_name", "seller", "owner_name", "mapdesc", "map_desc")),
            )
            _add_party(
                rec.grantees,
                _first_present(row, ("grantee", "grantee_name", "reverse_name", "buyer", "contractor", "lender")),
            )

            party_role = _first_present(
                row,
                ("party_type", "party_role", "role", "party_code", "direct_reverse", "ntype", "type_of_name", "name_type"),
            ).upper()
            party_nm = _first_present(row, ("party_name", "name", "full_name"))
            if party_role and party_nm:
                if party_role in ("1", "OR", "D", "DIR", "DIRECT", "GRANTOR", "SELLER", "BORROWER", "TRUSTOR", "OWNER"):
                    _add_party(rec.grantors, party_nm)
                elif party_role in (
                    "2",
                    "EE",
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

    # Pass 1: Process typed Header / Instrument / Flat tables first
    for _label, headers, rows in iter_tpia_tabular_files(clerk_tpia_dir):
        hdr_set = set(headers)
        if not (hdr_set & id_cols):
            continue
        if hdr_set & inst_type_cols:
            files_scanned += 1
            saw_inst_type_column = True
            _process_table_rows(rows, is_typed_pass=True)

    # Pass 2: Process untyped child tables (Names, LegalDesc, RelatedDocs) or standalone untyped tables
    for _label, headers, rows in iter_tpia_tabular_files(clerk_tpia_dir):
        hdr_set = set(headers)
        if not (hdr_set & id_cols) or (hdr_set & inst_type_cols):
            continue
        files_scanned += 1
        _process_table_rows(rows, is_typed_pass=False)

    # Propagate symmetric related_match_keys edges across assembled instruments (e.g. RelatedDocs.txt)
    for rec in list(instruments.values()):
        if not rec.match_key or not rec.related_match_keys:
            continue
        for rel_mk in list(rec.related_match_keys):
            rel_rec = instruments.get(rel_mk)
            if rel_rec is not None and rec.match_key != rel_mk:
                rel_rec.related_match_keys.add(rec.match_key)

    skipped_non_target = len(skipped_join_ids)
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
        "total_assembled_instruments": len(instruments) + len(skipped_join_ids),
        "kept_target_instruments": len(kept_instruments),
        "skipped_non_target_instruments": skipped_non_target,
        "instruments": kept_instruments,
    }


def calibrate_platted_subdivisions_from_clerk(
    overlays_doc: dict[str, Any],
    clerk_instruments: list[ClerkInstrumentRecord],
    *,
    max_replats_per_subdivision: int = 12,
) -> dict[str, int]:
    """Cross-link County Clerk `MAP_Instruments.txt` and `MAP_LegalDesc.txt` into `overlays.json['platted_subdivisions']`.

    - Tier 1 (Exact Citation Match): Matches 6-digit Map Film Code (`recnum` == `map_vol_page`),
      Clerk File Number (`deed_num`), or Map Book Volume/Page (`VP:vol/page`).
    - Tier 2 (Original Subdivision Name Match): Matches normalized `full_name` / `name` when the
      Clerk filing is an original subdivision plat (`not is_replat_filing`).
    - Tier 3 (Parent Addition Replat Linkage): Links subsequent partial replat or amendment filings
      (`HIGHLAND VILLAGE PARTIAL REPLAT NO 3`, `RIVERSIDE TERRACE SEC 6 PARTIAL REPLAT NO 4`) to
      their parent subdivision polygon's `clerk_replats` list and enriches Texas GLO `survey` and
      `abstract` without overwriting the historic addition's original dedication year.
    """
    raw_subd = overlays_doc.get("platted_subdivisions")
    features = (
        raw_subd.get("features")
        if isinstance(raw_subd, dict)
        else (raw_subd if isinstance(raw_subd, list) else None)
    )
    if not isinstance(features, list) or not features or not clerk_instruments:
        return {
            "calibrated_subdivisions": 0,
            "subdivision_replats_linked": 0,
            "subdivision_surveys_enriched": 0,
        }

    by_recnum: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_deed_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_vol_page: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_norm_name: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)

    for feat in features:
        if not isinstance(feat, dict):
            continue
        props = feat.get("properties")
        if not isinstance(props, dict):
            continue
        recnum = str(props.get("recnum") or "").strip().lstrip("0")
        if recnum:
            by_recnum[recnum].append(props)
        deed_num = str(props.get("deed_num") or "").strip()
        if deed_num:
            d_tok = normalize_clerk_tpia_instrument(file_no=deed_num)
            d_mk = format_clerk_match_key(d_tok) if d_tok else ""
            if d_mk:
                by_deed_key[d_mk].append(props)
        vol = str(props.get("vol") or "").strip().lstrip("0")
        pg = str(props.get("page") or "").strip().lstrip("0")
        if vol and pg:
            by_vol_page[f"VP:{vol}/{pg}"].append(props)

        full_nm = str(props.get("full_name") or props.get("name") or "").strip()
        short_nm = str(props.get("name") or "").strip()
        p_norm, p_sec = normalize_legal_subdivision_name(full_nm)
        if p_norm:
            by_norm_name[p_norm].append((p_sec, props))
        if short_nm and short_nm.upper() != full_nm.upper():
            s_norm, s_sec = normalize_legal_subdivision_name(short_nm)
            if s_norm and s_norm != p_norm:
                by_norm_name[s_norm].append((s_sec, props))

    calibrated_props_ids: set[int] = set()
    replat_props_ids: set[int] = set()
    survey_props_ids: set[int] = set()

    for inst in clerk_instruments:
        if inst.category != "RESTRICTION_PLAT" and not inst.map_code:
            continue
        if len(inst.dt8) != 8 or not inst.dt8[:4].isdigit() or inst.dt8 == "00000000":
            continue
        clerk_year = int(inst.dt8[:4])
        if clerk_year < 1823 or clerk_year > ACTIVE_TAX_YEAR + 1:
            continue
        iso_date = (
            f"{inst.dt8[:4]}-{inst.dt8[4:6]}-{inst.dt8[6:8]}"
            if inst.dt8[4:6] != "00" and inst.dt8[6:8] != "00"
            else f"{inst.dt8[:4]}-01-01"
        )
        display_file_no = format_clerk_display_token(inst.clerk_token)
        raw_desc = (
            inst.map_desc
            or (inst.grantors[0] if inst.grantors else "")
            or (next(iter(inst.legal_keys)).subdivision if inst.legal_keys else "")
        ).strip()
        is_replat_filing = bool(_RE_IS_REPLAT_DESC.search(raw_desc))

        matched_targets: list[tuple[str, dict[str, Any]]] = []
        seen_prop_ids: set[int] = set()

        vp_clean = str(inst.map_vol_page or "").strip().lstrip("0")
        if vp_clean and vp_clean in by_recnum:
            for p_hit in by_recnum[vp_clean]:
                if id(p_hit) not in seen_prop_ids:
                    seen_prop_ids.add(id(p_hit))
                    matched_targets.append(("recnum", p_hit))

        if inst.match_key and inst.match_key in by_deed_key:
            for p_hit in by_deed_key[inst.match_key]:
                if id(p_hit) not in seen_prop_ids:
                    seen_prop_ids.add(id(p_hit))
                    matched_targets.append(("deed_num", p_hit))

        if inst.clerk_token.startswith("VP:") and inst.clerk_token in by_vol_page:
            for p_hit in by_vol_page[inst.clerk_token]:
                if id(p_hit) not in seen_prop_ids:
                    seen_prop_ids.add(id(p_hit))
                    matched_targets.append(("vol_page", p_hit))

        if not matched_targets and raw_desc:
            subd_norm, subd_sec = normalize_legal_subdivision_name(raw_desc)
            if subd_norm and subd_norm in by_norm_name:
                candidates = by_norm_name[subd_norm]
                sec_filtered = (
                    [p for sec, p in candidates if not sec or sec == subd_sec]
                    if subd_sec
                    else [p for _sec, p in candidates]
                )
                for p_hit in (sec_filtered or [p for _sec, p in candidates]):
                    if id(p_hit) not in seen_prop_ids:
                        seen_prop_ids.add(id(p_hit))
                        matched_targets.append(("replat_name" if is_replat_filing else "orig_name", p_hit))

        if not matched_targets:
            continue

        surv_str = " / ".join(inst.surveys[:2]) if inst.surveys else ""
        abst_str = (
            ", ".join(f"A-{a}" if not a.startswith("A-") else a for a in inst.abstracts[:2])
            if inst.abstracts
            else ""
        )

        for match_tier, props in matched_targets:
            if surv_str and not props.get("survey"):
                props["survey"] = surv_str
                survey_props_ids.add(id(props))
            if abst_str and not props.get("abstract"):
                props["abstract"] = abst_str
                survey_props_ids.add(id(props))

            poly_full_nm = str(props.get("full_name") or props.get("name") or "")
            is_poly_replat = bool(_RE_IS_REPLAT_DESC.search(poly_full_nm))
            earliest_yr = int(props.get("earliest_year") or 0)
            pre_1965_cnt = int(props.get("pre_1965_count") or 0)
            is_unreplatted_historic_addition = bool(
                1836 <= earliest_yr < 1965 and pre_1965_cnt >= 2 and not is_poly_replat
            )

            treat_as_replat = (
                (is_replat_filing and not is_poly_replat)
                or (is_unreplatted_historic_addition and clerk_year > earliest_yr + 5)
            )

            if not treat_as_replat:
                cur_est = int(props.get("estimated_plat_year") or 0)
                if not props.get("plat_year_is_exact") or cur_est == 0 or clerk_year < cur_est:
                    props["estimated_plat_year"] = clerk_year
                    props["plat_year_is_exact"] = True
                    props["plat_year_label"] = str(clerk_year)
                    props["plat_file_date"] = iso_date
                    if display_file_no:
                        props["plat_clerk_file"] = display_file_no
                    if inst.map_vol_page and not props.get("recnum") and inst.map_vol_page.isdigit():
                        props["recnum"] = inst.map_vol_page
                    props["plat_year_basis"] = (
                        f"Harris County Clerk Maps & Condos Index ({display_file_no}, filed {iso_date})"
                        if display_file_no
                        else f"Harris County Clerk Maps & Condos Index (filed {iso_date})"
                    )
                    if clerk_year < 1950:
                        props["is_historic_plat"] = True
                    calibrated_props_ids.add(id(props))
            else:
                existing_replats = list(props.get("clerk_replats") or [])
                by_fno = {
                    str(item.get("file_no") or ""): dict(item)
                    for item in existing_replats
                    if isinstance(item, dict) and item.get("file_no")
                }
                entry: dict[str, Any] = {
                    "file_no": display_file_no,
                    "date": iso_date,
                    "name": raw_desc or display_file_no,
                }
                if inst.map_vol_page:
                    entry["film_code"] = inst.map_vol_page
                if inst.page_count > 0:
                    entry["pages"] = inst.page_count
                if display_file_no and by_fno.get(display_file_no) != entry:
                    by_fno[display_file_no] = entry
                    ordered_replats = sorted(
                        by_fno.values(),
                        key=lambda r: (str(r.get("date") or ""), str(r.get("file_no") or "")),
                        reverse=True,
                    )[:max_replats_per_subdivision]
                    props["clerk_replats"] = ordered_replats
                    replat_props_ids.add(id(props))

    return {
        "calibrated_subdivisions": len(calibrated_props_ids),
        "subdivision_replats_linked": len(replat_props_ids),
        "subdivision_surveys_enriched": len(survey_props_ids),
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
            for seg in wire.split("|"):
                parts = seg.split("~", 3)
                if len(parts) >= 3:
                    dtok = parts[2].strip()
                    if dtok:
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

        if not matched_hcads and inst.related_match_keys:
            rel_hits: set[str] = set()
            for rmk in inst.related_match_keys:
                r_accts = tier1_inst_to_hcads.get(rmk)
                if r_accts:
                    rel_hits.update(r_accts)
            if 1 <= len(rel_hits) <= max_parcels_per_legal_key:
                matched_hcads |= rel_hits
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

    # 5b. Calibrate `overlays.json["platted_subdivisions"]` from County Clerk Maps & Condos Index
    overlays_path = ddir / "overlays.json"
    overlays_doc: dict[str, Any] | None = None
    overlays_dirty = False
    subd_stats = {
        "calibrated_subdivisions": 0,
        "subdivision_replats_linked": 0,
        "subdivision_surveys_enriched": 0,
    }
    if clerk_res["instruments"] and overlays_path.exists():
        loaded_ov = read_json_or_gzip_path(overlays_path)
        raw_subd = loaded_ov.get("platted_subdivisions") if isinstance(loaded_ov, dict) else None
        has_subd = (
            (isinstance(raw_subd, dict) and isinstance(raw_subd.get("features"), list))
            or isinstance(raw_subd, list)
        )
        if has_subd:
            subd_stats = calibrate_platted_subdivisions_from_clerk(
                loaded_ov, clerk_res["instruments"]
            )
            if any(subd_stats.values()):
                overlays_doc = sanitize_json_structure_dashes(loaded_ov)
                overlays_dirty = True

    # 6. Pre-compress touched `.json.gz` shards and validate bundle size ceilings before any disk write
    def _compress_shard_pair(item: tuple[str, dict[str, Any]]) -> tuple[str, bytes]:
        pfx_k, s_dict_k = item
        return pfx_k, compress_deterministic_gzip_json(sanitize_json_structure_dashes(s_dict_k))

    def _stage_shards_gz(
        shards_by_pfx: dict[str, dict[str, Any]],
        touched_pfxs: set[str],
        shard_desc: str,
        file_prefix: str,
    ) -> dict[str, bytes]:
        work = [(pfx, shards_by_pfx[pfx]) for pfx in sorted(touched_pfxs) if shards_by_pfx.get(pfx)]
        staged: dict[str, bytes] = {}
        if len(work) > 4:
            with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 4)) as pool:
                pairs = list(pool.map(_compress_shard_pair, work))
        else:
            pairs = [_compress_shard_pair(item) for item in work]
        for pfx_k, gz_b in pairs:
            if len(gz_b) >= MAX_SINGLE_FILE_BYTES:
                raise RuntimeError(
                    f"TPIA {shard_desc} shard {file_prefix}_{pfx_k}.json.gz ({len(gz_b)} bytes) exceeds MAX_SINGLE_FILE_BYTES."
                )
            staged[pfx_k] = gz_b
        return staged

    staged_permit_gz = _stage_shards_gz(permit_shards, touched_permit_prefixes, "permit", "permits")
    staged_oh_gz = _stage_shards_gz(oh_shards, touched_oh_prefixes, "owner_history", "owners")

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
        ov_delta = 0
        if overlays_dirty and overlays_doc is not None and overlays_path.exists():
            next_ov_bytes = len(
                json.dumps(overlays_doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            )
            if next_ov_bytes >= MAX_SINGLE_FILE_BYTES:
                raise RuntimeError(
                    f"Calibrated overlays.json ({next_ov_bytes} bytes) exceeds MAX_SINGLE_FILE_BYTES."
                )
            ov_delta = next_ov_bytes - overlays_path.stat().st_size
        projected_app_bytes = (
            max(0, base_app - cur_p_bytes - cur_oh_bytes) + next_p_bytes + next_oh_bytes + ov_delta
        )
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

        if overlays_dirty and overlays_doc is not None:
            write_compact_json(overlays_path, overlays_doc)

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
        "calibrated_subdivisions": subd_stats["calibrated_subdivisions"],
        "subdivision_replats_linked": subd_stats["subdivision_replats_linked"],
        "subdivision_surveys_enriched": subd_stats["subdivision_surveys_enriched"],
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
        f"   Subdivision plats calibrated : {stats.get('calibrated_subdivisions', 0):,} original plats / {stats.get('subdivision_replats_linked', 0):,} replats / {stats.get('subdivision_surveys_enriched', 0):,} surveys",
        f"   Sec. 25.025 shielded filings : {stats['confidential_suppressed_instruments']:,}",
        f"   Updated accounts (oh / perm) : {stats['updated_owner_history_accounts']:,} owner_history / {stats['updated_permit_accounts']:,} permits",
        f"   Projected app/ bundle size   : {proj_mib:.2f} MiB",
    ])
