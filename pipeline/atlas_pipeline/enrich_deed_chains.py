"""Extract and normalize Harris County Clerk recorded deed transfers from HCAD CAMA deeds.txt.

Enriches the on-demand 3-digit prefix shards in app/public/data/permits/permits_<prefix3>.json
with a compact "d" field ("YYYYMMDD:TOKEN|...") storing up to 8 unique recorded instruments per
parcel so Step 3 of the House History Research Kit can surface 1-click County Clerk File Numbers.

Normalized token formats (verified against Harris County Clerk Real Property RP.aspx):
  - R<YYYY>-<SEQ>     -> Decoded in UI as File Number "RP-YYYY-SEQ" (2016 seq >= 10027 and 2017+; no leading zeros on SEQ)
  - <YYYY><7_DIGITS>  -> 11-digit File Number (2006-2015 and Jan 2016 seq <= 10026, e.g. "20120430232")
  - <LETTER><6_DIGITS>-> 7-char Clerk File Number (1972-2006, e.g. "Y051716")
  - FC:<###-##-####>  -> Clerk Microfilm / Film Code
  - PR:<CASE_NO>      -> Harris County Probate Court Case / Docket Number
  - VP:<VOL>/<PAGE>   -> Pre-1961 Bound Deed Book Volume & Page
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path

from atlas_pipeline.schema import (
    MAX_VALID_YEAR,
    normalize_hcad_date,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = REPO_ROOT / "pipeline" / "cache"
APP_DIR = REPO_ROOT / "app"
DATA_DIR = APP_DIR / "public" / "data"
PERMITS_DIR = DATA_DIR / "permits"

REAL_ACCT_ZIP = CACHE_DIR / "Real_acct_owner.zip"
REAL_BLDG_ZIP = CACHE_DIR / "Real_building_land.zip"

MAX_DEEDS_PER_PARCEL = 8

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
    # Collapse repeated internal spaces around hyphens
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

        # Already full 11-digit YYYYNNNNNNN
        if n_len == 11 and cid_nospace[:4].isdigit():
            p_yr = int(cid_nospace[:4])
            p_seq = int(cid_nospace[4:])
            if 2006 <= p_yr <= 2015 and p_seq > 0:
                return cid_nospace
            if p_yr == 2016 and p_seq > 0:
                return cid_nospace if p_seq <= 10026 else f"R2016-{p_seq}"
            if 2017 <= p_yr <= eff_max and 0 < p_seq <= 999999:
                return f"R{p_yr}-{p_seq}"

        # 2006-2015 numeric sequence (annual Harris County filings never exceeded ~600,000):
        # - n_len <= 7: standard 1..7 digit sequence zero-padded to YYYY + 7 digits.
        # - n_len == 8 starting with yr[-1] (e.g. 2007 "70115058"): truncated year prefix -> drop first digit.
        # - n_len == 8 starting with "00" (val_int <= 999,999, e.g. "00043023"): 8-digit zero-padded sequence.
        # - n_len == 8 starting with "0[1-9]" (val_int > 999,999, e.g. 2014 "01492230"): 7-digit sequence
        #   with trailing HCAD column-overflow digit -> take cid_nospace[:7] (verified live against RP.aspx).
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

        # 2016 transition year (Jan 2016 <= 10026 is 11-digit; >= 10027 is RP-2016-SEQ)
        if yr_int == 2016 and n_len <= 7:
            if val_int <= 10026:
                return f"2016{val_int:07d}"
            return f"R2016-{val_int}"

        # 2017+ numeric sequence without RP- prefix
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


def collect_improved_and_mapped_hcads(
    existing_shards: dict[str, dict],
    data_dir: Path = DATA_DIR,
    real_bldg_zip: Path = REAL_BLDG_ZIP,
) -> set[str]:
    """Collect all HCAD accounts that exist in permit shards, core data files, or CAMA building tables."""
    hcads: set[str] = set()
    for shard_dict in existing_shards.values():
        hcads.update(shard_dict.keys())

    for fname in ("curated_overrides.json", "buildings.geojson", "search_index.json"):
        fpath = data_dir / fname
        if not fpath.exists():
            continue
        data = json.loads(fpath.read_text(encoding="utf-8"))
        if fname == "curated_overrides.json" and isinstance(data, dict):
            ov_map = data.get("overrides") if isinstance(data.get("overrides"), dict) else data
            for k, ov in ov_map.items():
                if not isinstance(ov, dict):
                    continue
                h = str(ov.get("hcad_num") or k.split("#")[0]).strip()
                if len(h) >= 10 and h.isdigit():
                    hcads.add(h)
        elif fname == "buildings.geojson" and isinstance(data, dict):
            for feat in data.get("features", []):
                h = str((feat.get("properties") or {}).get("hcad_num") or "").strip()
                if len(h) >= 10 and h.isdigit():
                    hcads.add(h)
        elif fname == "search_index.json" and isinstance(data, list):
            for row in data:
                h = str((row or {}).get("hcad_num") or "").strip()
                if len(h) >= 10 and h.isdigit():
                    hcads.add(h)

    if real_bldg_zip.exists():
        with zipfile.ZipFile(real_bldg_zip) as zf:
            for member in ("building_res.txt", "building_other.txt", "extra_features.txt"):
                if member not in zf.namelist():
                    continue
                with zf.open(member) as raw_f:
                    txt_f = io.TextIOWrapper(raw_f, encoding="latin-1")
                    header_line = txt_f.readline()
                    if not header_line:
                        continue
                    header = header_line.rstrip("\r\n").split("\t")
                    acct_idx = 0
                    for idx, col in enumerate(header):
                        if col.strip().lower() == "acct":
                            acct_idx = idx
                            break
                    for line in txt_f:
                        parts = line.split("\t", acct_idx + 1)
                        if len(parts) > acct_idx:
                            a = parts[acct_idx].strip()
                            if len(a) >= 10 and a.isdigit():
                                hcads.add(a)
    return hcads


def compute_app_total_bytes(app_dir: Path = APP_DIR) -> int:
    total = 0
    for p in app_dir.rglob("*"):
        if p.is_file():
            total += p.stat().st_size
    return total


def main(
    real_acct_zip: Path = REAL_ACCT_ZIP,
    permits_dir: Path = PERMITS_DIR,
    data_dir: Path = DATA_DIR,
    real_bldg_zip: Path = REAL_BLDG_ZIP,
    app_dir: Path = APP_DIR,
    *,
    tax_year: int | None = None,
) -> dict:
    """Idempotently enrich `permits_<prefix3>.json` shards by delegating to `enrich_permit_shards_with_ownership_and_deeds`."""
    from atlas_pipeline.build_ownership_and_history import (
        enrich_permit_shards_with_ownership_and_deeds,
    )

    summary = enrich_permit_shards_with_ownership_and_deeds(
        real_acct_zip=real_acct_zip,
        permits_dir=permits_dir,
        data_dir=data_dir,
        real_bldg_zip=real_bldg_zip,
        app_dir=app_dir,
        tax_year=tax_year,
    )
    print(
        f"Done! Enriched {summary['enriched_existing']:,} existing shard accounts + "
        f"{summary['added_new_accts']:,} new accounts "
        f"(largest shard: {summary['max_shard_name']} = {summary['max_shard_bytes'] / (1024 * 1024):.2f} MB, "
        f"projected app/ total: {summary['projected_total_bytes'] / (1024 * 1024):.2f} MB)."
    )
    return summary


if __name__ == "__main__":
    main()
