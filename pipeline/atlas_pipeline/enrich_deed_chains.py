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

import collections
import csv
import io
import json
import re
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = REPO_ROOT / "pipeline" / "cache"
APP_DIR = REPO_ROOT / "app"
DATA_DIR = APP_DIR / "public" / "data"
PERMITS_DIR = DATA_DIR / "permits"

REAL_ACCT_ZIP = CACHE_DIR / "Real_acct_owner.zip"
REAL_BLDG_ZIP = CACHE_DIR / "Real_building_land.zip"

MAX_DEEDS_PER_PARCEL = 8
SOFT_MAX_APP_BYTES = 955 * 1024 * 1024

RE_RP_FLEX = re.compile(
    r"^(?:\d)?(?:RP[\s\-_]*|P[\s\-_]+)(20(?:1[6-9]|2\d|30))(?:[\s\-_]+\1)?[\s\-_]*0*([1-9]\d{0,6})$",
    re.IGNORECASE,
)
RE_YEAR_RP_SWAP = re.compile(
    r"^(20(?:1[6-9]|2\d))[\s\-_]+RP[\s\-_]*0*([1-9]\d{0,6})$",
    re.IGNORECASE,
)
RE_BARE_YEAR_HYPHEN = re.compile(
    r"^(20(?:1[6-9]|2\d))[\s\-_]+0*([1-9]\d{0,6})$"
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


def parse_dos_yyyymmdd(raw_dos: str, raw_yr: str) -> str:
    """Normalize HCAD date of sale (MM/DD/YYYY or YYYY-MM-DD) to 8-digit YYYYMMDD."""
    s = (raw_dos or "").strip()
    if s:
        if "/" in s:
            parts = s.split("/")
            if len(parts) == 3:
                mm, dd, yyyy = parts[0].strip(), parts[1].strip(), parts[2].strip()[:4]
                if yyyy.isdigit() and mm.isdigit() and dd.isdigit():
                    y_int = int(yyyy)
                    if 1836 <= y_int <= 2030:
                        return f"{y_int:04d}{int(mm):02d}{int(dd):02d}"
        elif "-" in s:
            parts = s.split("-")
            if len(parts) >= 3:
                yyyy, mm, dd = parts[0].strip(), parts[1].strip(), parts[2].strip()[:2]
                if yyyy.isdigit() and mm.isdigit() and dd.isdigit():
                    y_int = int(yyyy)
                    if 1836 <= y_int <= 2030:
                        return f"{y_int:04d}{int(mm):02d}{int(dd):02d}"
    yr_s = (raw_yr or "").strip()
    if yr_s.isdigit():
        y_int = int(yr_s)
        if 1836 <= y_int <= 2030:
            return f"{y_int:04d}0000"
    return "00000000"


def normalize_hcad_clerk_instrument(clerk_yr: str, clerk_id: str) -> str | None:
    """Normalize HCAD (clerk_yr, clerk_id) to a compact, verified Clerk lookup token."""
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
            if 2017 <= p_yr <= 2030 and 0 < p_seq <= 999999:
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
        if 2017 <= yr_int <= 2030 and n_len <= 7 and val_int <= 999999:
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
) -> None:
    if not real_acct_zip.exists():
        raise FileNotFoundError(f"Missing HCAD archive: {real_acct_zip}")

    print("[1/4] Loading existing permit shards from app/public/data/permits/...")
    shards: dict[str, dict[str, dict]] = collections.defaultdict(dict)
    existing_acct_count = 0
    for shard_file in sorted(permits_dir.glob("permits_*.json")):
        prefix3 = shard_file.stem.replace("permits_", "")
        data = json.loads(shard_file.read_text(encoding="utf-8"))
        # Strip any prior top-level "d" deed-chain keys so re-runs are strictly idempotent
        # (note: nested permit descriptions live at rec["p"][i]["d"] and remain untouched)
        cleaned = {}
        for acct, rec in data.items():
            if isinstance(rec, dict):
                rec.pop("d", None)
                if rec:
                    cleaned[acct] = rec
        shards[prefix3] = cleaned
        existing_acct_count += len(cleaned)
    print(f"      Loaded {len(shards)} shards with {existing_acct_count:,} existing accounts.")

    print("[2/4] Streaming and normalizing deeds.txt from Real_acct_owner.zip...")
    # Map acct -> dict[token -> best_yyyymmdd]
    deeds_by_acct: dict[str, dict[str, str]] = collections.defaultdict(dict)
    total_rows = 0
    normalized_rows = 0
    with zipfile.ZipFile(real_acct_zip) as zf:
        with zf.open("deeds.txt") as raw_f:
            rdr = csv.DictReader(io.TextIOWrapper(raw_f, encoding="latin-1"), delimiter="\t")
            for row in rdr:
                total_rows += 1
                acct = (row.get("acct") or "").strip()
                if len(acct) < 10 or not acct[:3].isdigit():
                    continue
                tok = normalize_hcad_clerk_instrument(
                    row.get("clerk_yr") or "",
                    row.get("clerk_id") or "",
                )
                if not tok:
                    continue
                normalized_rows += 1
                dt8 = parse_dos_yyyymmdd(row.get("dos") or "", row.get("clerk_yr") or "")
                prev_dt = deeds_by_acct[acct].get(tok)
                # Prefer full MM/DD/YYYY dates over year-only YYYY0000 or 00000000 placeholders, then newer date
                dt_key = (dt8 != "00000000" and not dt8.endswith("0000"), dt8)
                prev_key = (
                    (prev_dt != "00000000" and not prev_dt.endswith("0000"), prev_dt)
                    if prev_dt is not None
                    else None
                )
                if prev_key is None or dt_key > prev_key:
                    deeds_by_acct[acct][tok] = dt8

    print(
        f"      Normalized {normalized_rows:,} of {total_rows:,} deed rows across "
        f"{len(deeds_by_acct):,} unique HCAD accounts."
    )

    # Format compact deed chain strings per parcel
    compact_deed_str_by_acct: dict[str, str] = {}
    for acct, tok_map in deeds_by_acct.items():
        # Sort newest first by YYYYMMDD, then token
        pairs = sorted(tok_map.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
        top_pairs = pairs[:MAX_DEEDS_PER_PARCEL]
        compact_deed_str_by_acct[acct] = "|".join(f"{dt8}:{tok}" for tok, dt8 in top_pairs)

    # Compute baseline non-permit app/ bytes and stripped permit shard bytes (idempotent across re-runs)
    non_permit_bytes = compute_app_total_bytes(app_dir) - sum(
        p.stat().st_size for p in permits_dir.glob("permits_*.json")
    )
    stripped_permits_bytes = sum(
        len(json.dumps(shard_dict, separators=(",", ":")).encode("utf-8"))
        for shard_dict in shards.values()
        if shard_dict
    )

    # First check size with improved/mapped parcels (covers 100% of clickable buildings on the Atlas)
    improved_hcads = collect_improved_and_mapped_hcads(
        shards, data_dir=data_dir, real_bldg_zip=real_bldg_zip
    )
    print(f"[3/4] Collected {len(improved_hcads):,} improved/mapped HCAD accounts.")

    # Estimate whether all deeds.txt accounts fit under SOFT_MAX_APP_BYTES
    est_all_added = 0
    for acct, d_str in compact_deed_str_by_acct.items():
        p3 = acct[:3]
        if acct in shards.get(p3, {}):
            est_all_added += len(d_str) + 7  # ,"d":"..."
        else:
            est_all_added += len(acct) + len(d_str) + 12  # "<acct>":{"d":"..."},

    include_unimproved = (
        non_permit_bytes + stripped_permits_bytes + est_all_added
    ) < SOFT_MAX_APP_BYTES
    mode_label = (
        "ALL countywide parcels (including vacant/unimproved)"
        if include_unimproved
        else "ALL improved/mapped building parcels"
    )
    print(
        f"      Estimated total app/ size with all deeds: "
        f"{(non_permit_bytes + stripped_permits_bytes + est_all_added) / (1024 * 1024):.2f} MB -> "
        f"Mode: {mode_label}"
    )

    enriched_existing = 0
    added_new_accts = 0
    for acct, d_str in compact_deed_str_by_acct.items():
        p3 = acct[:3]
        shard_dict = shards[p3]
        if acct in shard_dict:
            shard_dict[acct]["d"] = d_str
            enriched_existing += 1
        elif include_unimproved or acct in improved_hcads:
            shard_dict[acct] = {"d": d_str}
            added_new_accts += 1

    print("[4/4] Writing enriched permit & deed chain shards...")
    permits_dir.mkdir(parents=True, exist_ok=True)
    max_shard_bytes = 0
    max_shard_name = ""
    for prefix3, shard_dict in sorted(shards.items()):
        shard_file = permits_dir / f"permits_{prefix3}.json"
        if not shard_dict:
            shard_file.unlink(missing_ok=True)
            continue
        payload = json.dumps(shard_dict, separators=(",", ":"))
        shard_file.write_text(payload, encoding="utf-8")
        sz = shard_file.stat().st_size
        if sz > max_shard_bytes:
            max_shard_bytes = sz
            max_shard_name = shard_file.name

    final_app_bytes = compute_app_total_bytes(app_dir)
    sample_key_st = shards.get("062", {}).get("0621100000014", {}).get("d")
    print(
        f"Done! Enriched {enriched_existing:,} existing shard accounts + {added_new_accts:,} "
        f"deed-only accounts across {len(shards)} shards."
    )
    print(
        f"Largest shard: {max_shard_name} ({max_shard_bytes / (1024 * 1024):.2f} MB) | "
        f"Total app/ bundle: {final_app_bytes / (1024 * 1024):.2f} MB (limit 980.00 MB)"
    )
    print(f"Verification 1127 Key St (0621100000014) d = {sample_key_st}")


if __name__ == "__main__":
    main()
