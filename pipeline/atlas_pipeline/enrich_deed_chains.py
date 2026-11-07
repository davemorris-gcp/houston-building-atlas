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
import zipfile
from pathlib import Path

from atlas_pipeline.schema import (
    MAX_VALID_YEAR,
    RE_BARE_YEAR_HYPHEN,
    RE_FILM_CODE,
    RE_LETTER_CODE,
    RE_PROBATE,
    RE_RP_FLEX,
    RE_VOL_PAGE,
    RE_YEAR_RP_SWAP,
    normalize_hcad_clerk_instrument,
    normalize_hcad_date,
    parse_dos_yyyymmdd,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = REPO_ROOT / "pipeline" / "cache"
APP_DIR = REPO_ROOT / "app"
DATA_DIR = APP_DIR / "public" / "data"
PERMITS_DIR = DATA_DIR / "permits"

REAL_ACCT_ZIP = CACHE_DIR / "Real_acct_owner.zip"
REAL_BLDG_ZIP = CACHE_DIR / "Real_building_land.zip"

MAX_DEEDS_PER_PARCEL = 8


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
