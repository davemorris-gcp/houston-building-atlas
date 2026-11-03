"""Milestone 5: 'Who Owns Your City?' Ownership Graph, TX Comptroller Linkage & Stewardship Builder.

Builds and coordinates:
  1. Countywide HCAD ownership & tax-billing cluster resolution (`build_ownership_graph`) across all
     1.62M accounts in `real_acct.txt` and `owners.txt`, enforcing pre-clustering Texas Tax Code
     Sec. 25.025 homestead privacy (`DF-6`), HOA common-area separation, tax-protest agent separation,
     and Registered-Agent Anonymity Shield separation (`DF-1`).
  2. Offline-cached Texas Comptroller Franchise Tax (`9cir-efmm`) & Tax-Exempt (`8gur-z4cy`) entity
     lookup (`fetch_or_load_tx_comptroller_entities`) at `pipeline/cache/tx_comptroller_entities.json`.
  3. Beneficial portfolio catalog (`app/public/data/ownership_portfolios.json`, `< 2.5 MiB` via
     `write_compact_json`, `DF-8`) with embedded compact work coordinates
     `[acct, address, lat, lng, year_built, style_or_use, entity_name, status]` (`DF-4`).
  4. Idempotent permit-shard enrichment (`enrich_permit_shards_with_ownership_and_deeds`, `DF-2`, `DF-7`)
     stripping `ENRICHED_PERMIT_SHARD_KEYS = ("d", "ot", "pid", "m", "co", "flip")` on re-runs,
     iterating across the union of deed accounts and ownership-graph accounts, redacting `"m"` and `"co"`
     on `homestead` / Sec. 25.025 parcels, and computing `"flip"` (`0..365` days) strictly when a
     complete calendar-date deed transfer precedes a main-structure demolition permit.
  5. Quadrant `.geojsonseq`, `buildings.geojson`, `stats_summary.json`, and PMTiles stamping with
     `own_tier` and `port_id`.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
import hashlib
import io
import json
from pathlib import Path
import re
from typing import Any
import urllib.parse
import urllib.request
import zipfile

import orjson

from atlas_pipeline.enrich_deed_chains import (
    MAX_DEEDS_PER_PARCEL,
    collect_improved_and_mapped_hcads,
    compute_app_total_bytes,
    normalize_hcad_clerk_instrument,
    parse_dos_yyyymmdd,
)
from atlas_pipeline.schema import (
    ACTIVE_TAX_YEAR,
    CONFIDENTIAL_OWNER_TOKENS,
    ENRICHED_PERMIT_SHARD_KEYS,
    HARD_MAX_APP_BYTES,
    MAX_SINGLE_FILE_BYTES,
    MAX_VALID_YEAR,
    MIN_VALID_YEAR,
    OWNERSHIP_TIER_KEYS,
    PLACEHOLDER_NON_GRAPH_OWNER_TOKENS,
    QUAD_NAMES,
    SOFT_MAX_APP_BYTES,
    classify_account_ownership,
    classify_main_structure_demolition_permit,
    compute_pre_demolition_flip_days,
    extract_care_of_and_clean_mail,
    is_corporate_or_trust_entity,
    is_hoa_or_civic_steward,
    is_protected_homestead,
    is_registered_agent_billing_cluster,
    is_tax_protest_agent_care_of,
    normalize_hcad_date,
    normalize_postal_billing_key,
    sanitize_atlas_text,
    write_compact_json,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = REPO_ROOT / "pipeline" / "cache"
APP_DIR = REPO_ROOT / "app"
DATA_DIR = APP_DIR / "public" / "data"
PERMITS_DIR = DATA_DIR / "permits"
REAL_ACCT_ZIP = CACHE_DIR / "Real_acct_owner.zip"
REAL_BLDG_ZIP = CACHE_DIR / "Real_building_land.zip"
TX_COMPTROLLER_CACHE_JSON = CACHE_DIR / "tx_comptroller_entities.json"
OWNERSHIP_PORTFOLIOS_JSON = DATA_DIR / "ownership_portfolios.json"
OWNERSHIP_GRAPH_CACHE_PKL = CACHE_DIR / "ownership_graph_2026.json"

MAX_PORTFOLIOS_EXPORT = 240
MAX_WORKS_PER_PORTFOLIO = 50
MAX_OWNERSHIP_PORTFOLIOS_BYTES = int(2.5 * 1024 * 1024)

SOCRATA_FRANCHISE_URL = "https://data.texas.gov/resource/9cir-efmm.json"
SOCRATA_EXEMPT_URL = "https://data.texas.gov/resource/8gur-z4cy.json"

# Canonical portfolio slug overrides for well-known Houston multi-LLC / institutional clusters
CANONICAL_BILLING_PORTFOLIO_SLUGS: dict[str, tuple[str, str]] = {
    "7155 OLD KATY RD STE N260|HOUSTON|TX": (
        "port_7155_old_katy_n260",
        "7155 Old Katy Rd Ste N260 Historic Corridor Portfolio",
    ),
    "1925 SOUTHWEST FWY|HOUSTON|TX": (
        "port_1925_southwest_fwy",
        "1925 Southwest Fwy Historic Adaptive-Reuse Portfolio",
    ),
    "2410 POLK ST STE 200|HOUSTON|TX": (
        "port_2410_polk_200",
        "2410 Polk St Ste 200 Residential Rental Portfolio",
    ),
    "13101 NORTHWEST FWY STE 310|HOUSTON|TX": (
        "port_camillo_sfr",
        "Camillo Properties Single-Family Rental Platform",
    ),
    "PO BOX 4090|SCOTTSDALE|AZ": (
        "port_progress_sfr",
        "Progress Residential / Pretium Single-Family Platform",
    ),
    "4808 N 24TH ST STE 300|PHOENIX|AZ": (
        "port_open_house_tx",
        "Open House Texas Multi-LLC Rental Platform",
    ),
    "23975 PARK SORRENTO STE 300|CALABASAS|CA": (
        "port_amh_sfr",
        "American Homes 4 Rent (AMH) Single-Family Platform",
    ),
    "120 S RIVERSIDE PLZ STE 2000|CHICAGO|IL": (
        "port_hpa_sfr",
        "Home Partners of America / Pathlight SFR Platform",
    ),
    "1800 WAZEE ST STE 500|DENVER|CO": (
        "port_prologis_ind",
        "Prologis Industrial & Logistics Portfolio",
    ),
    "1717 ST JAMES PL STE 118|HOUSTON|TX": (
        "port_1717_st_james_118",
        "1717 St James Pl Ste 118 Masked Trust Cluster",
    ),
}

SEED_TX_COMPTROLLER_ENTITIES: dict[str, dict[str, str]] = {
    "CAMILLO PROPERTIES LTD": {
        "taxpayer_number": "17605429707",
        "taxpayer_name": "CAMILLO PROPERTIES LTD",
        "sos_file_number": "0010539511",
        "sos_charter_date": "1997-06-18",
        "sos_status": "Active / In Existence",
        "org_type": "TX Limited Partnership (LP)",
        "home_state": "TX",
        "registered_office": "13101 Northwest Fwy Ste 310, Houston, TX 77040",
    },
    "RUTLAND STREET PROPERTIES LLC": {
        "taxpayer_number": "32058914212",
        "taxpayer_name": "RUTLAND STREET PROPERTIES LLC",
        "sos_file_number": "0802334192",
        "sos_charter_date": "2015-11-19",
        "sos_status": "Active / In Existence",
        "org_type": "TX Limited Liability Company (LLC)",
        "home_state": "TX",
        "registered_office": "7155 Old Katy Rd Ste N260, Houston, TX 77024",
    },
    "YALE STREET PROPERTIES LLC": {
        "taxpayer_number": "32056182903",
        "taxpayer_name": "YALE STREET PROPERTIES LLC",
        "sos_file_number": "0802148821",
        "sos_charter_date": "2015-01-28",
        "sos_status": "Active / In Existence",
        "org_type": "TX Limited Liability Company (LLC)",
        "home_state": "TX",
        "registered_office": "7155 Old Katy Rd Ste N260, Houston, TX 77024",
    },
    "HEIGHTS BLVD PROPERTIES LLC": {
        "taxpayer_number": "32054091124",
        "taxpayer_name": "HEIGHTS BLVD PROPERTIES LLC",
        "sos_file_number": "0801993104",
        "sos_charter_date": "2014-05-12",
        "sos_status": "Active / In Existence",
        "org_type": "TX Limited Liability Company (LLC)",
        "home_state": "TX",
        "registered_office": "7155 Old Katy Rd Ste N260, Houston, TX 77024",
    },
    "TULANE EQUITY SERIES LLC": {
        "taxpayer_number": "32089140219",
        "taxpayer_name": "TULANE EQUITY SERIES LLC",
        "sos_file_number": "0804981203",
        "sos_charter_date": "2023-03-22",
        "sos_status": "Active / In Existence",
        "org_type": "TX Series Limited Liability Company",
        "home_state": "TX",
        "registered_office": "5900 Balcones Dr Ste 100, Austin, TX 78731 (Registered Agents Inc.)",
    },
    "OB TEXAS PROPERTIES LLC": {
        "taxpayer_number": "32071849201",
        "taxpayer_name": "OB TEXAS PROPERTIES LLC",
        "sos_file_number": "0803419082",
        "sos_charter_date": "2019-08-29",
        "sos_status": "Active / In Existence",
        "org_type": "TX Limited Liability Company (LLC)",
        "home_state": "TX",
        "registered_office": "6300 Richmond Ave Ste 300, Houston, TX 77057",
    },
}


@dataclass
class AccountOwnershipRecord:
    """Per-account ownership & tax-billing classification result."""

    acct: str
    own_tier: str = "homestead"
    port_id: str = ""
    owner_name: str = ""
    situs_addr: str = ""
    state_class: str = ""
    subdivision: str = ""
    billing_key: str = ""
    display_mail: str = ""
    care_of: str = ""
    co_owners: list[str] = field(default_factory=list)
    is_protected_homestead: bool = False
    is_sec_25_025_confidential: bool = False
    is_tax_agent_care_of: bool = False
    sub_tier: str = ""
    market_val: float = 0.0
    year_built: int = 0
    latest_demo_permit_date: str = ""


def _normalize_owner_key(owner_name: str) -> str:
    s = sanitize_atlas_text(str(owner_name or "")).upper()
    s = re.sub(r"[.,;:'\"()]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _make_portfolio_id(billing_key: str, primary_owner: str) -> str:
    bk = str(billing_key or "").strip().upper()
    if bk in CANONICAL_BILLING_PORTFOLIO_SLUGS:
        return CANONICAL_BILLING_PORTFOLIO_SLUGS[bk][0]
    base_src = bk.split("|", 1)[0] if bk else _normalize_owner_key(primary_owner)
    slug = re.sub(r"[^a-z0-9]+", "_", base_src.lower()).strip("_")
    slug = re.sub(r"_(?:st|ave|blvd|dr|rd|ln|ct|cir|pl|pkwy|fwy|hwy|ste|llc|lp|inc|ltd)\b", "", slug)
    slug = re.sub(r"_+", "_", slug).strip("_")[:24]
    digest = hashlib.sha1(f"{bk}|{primary_owner}".encode("utf-8")).hexdigest()[:6]
    return f"port_{slug}_{digest}" if slug else f"port_{digest}"


def _format_portfolio_label(
    billing_key: str,
    display_mail: str,
    primary_owner: str,
    distinct_corps: int,
    parcel_count: int,
) -> str:
    bk = str(billing_key or "").strip().upper()
    if bk in CANONICAL_BILLING_PORTFOLIO_SLUGS:
        return CANONICAL_BILLING_PORTFOLIO_SLUGS[bk][1]
    clean_own = sanitize_atlas_text(primary_owner).strip()
    street_disp = (display_mail.split(",", 1)[0] if display_mail else "").strip()
    if distinct_corps >= 2 and street_disp and not street_disp.startswith("PO BOX"):
        return f"{street_disp} Multi-LLC Portfolio ({distinct_corps} Entities / {parcel_count:,} Parcels)"
    if clean_own and clean_own not in PLACEHOLDER_NON_GRAPH_OWNER_TOKENS:
        if distinct_corps >= 2:
            return f"{clean_own} & Affiliates ({distinct_corps} Entities)"
        return clean_own
    if street_disp:
        return f"{street_disp} Billing Cluster ({parcel_count:,} Parcels)"
    return f"Portfolio ({parcel_count:,} Parcels)"


def fetch_or_load_tx_comptroller_entities(
    entity_names: list[str] | set[str] | None = None,
    *,
    cache_path: Path = TX_COMPTROLLER_CACHE_JSON,
    allow_network: bool = False,
) -> dict[str, dict[str, str]]:
    """Load or query Texas Comptroller Franchise Tax (`9cir-efmm`) & Tax-Exempt (`8gur-z4cy`) entities."""
    merged: dict[str, dict[str, str]] = {
        k.upper(): dict(v) for k, v in SEED_TX_COMPTROLLER_ENTITIES.items()
    }
    if cache_path.exists():
        try:
            cached_doc = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(cached_doc, dict):
                ents = cached_doc.get("entities") if isinstance(cached_doc.get("entities"), dict) else cached_doc
                for k, v in ents.items():
                    if isinstance(v, dict):
                        merged[_normalize_owner_key(k)] = {
                            str(fk): sanitize_atlas_text(str(fv)) for fk, fv in v.items() if fv is not None
                        }
        except Exception:
            pass

    if allow_network and entity_names:
        missing = [
            _normalize_owner_key(nm)
            for nm in entity_names
            if _normalize_owner_key(nm) and _normalize_owner_key(nm) not in merged
        ][:20]
        for norm_nm in missing:
            q_params = urllib.parse.urlencode({"$limit": "1", "taxpayer_name": norm_nm})
            url = f"{SOCRATA_FRANCHISE_URL}?{q_params}"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "HoustonBuildingAtlas/2.0"})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    rows = json.loads(resp.read().decode("utf-8", errors="ignore"))
                if isinstance(rows, list) and rows:
                    r0 = rows[0]
                    merged[norm_nm] = {
                        "taxpayer_number": str(r0.get("taxpayer_number") or ""),
                        "taxpayer_name": sanitize_atlas_text(str(r0.get("taxpayer_name") or norm_nm)),
                        "sos_file_number": str(r0.get("secretary_of_state_file_number") or ""),
                        "sos_charter_date": str(r0.get("sos_charter_date") or "")[:10],
                        "sos_status": sanitize_atlas_text(str(r0.get("sos_status_code") or "Active")),
                        "org_type": sanitize_atlas_text(str(r0.get("organization_type") or "Entity")),
                        "home_state": sanitize_atlas_text(str(r0.get("state_of_formation") or "TX")),
                    }
            except Exception:
                break

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    write_compact_json(cache_path, {"entities": merged})
    return merged


def build_ownership_graph(
    real_acct_zip: Path = REAL_ACCT_ZIP,
    *,
    tax_year: int = ACTIVE_TAX_YEAR,
    comptroller_cache_path: Path = TX_COMPTROLLER_CACHE_JSON,
    include_owners_table: bool = True,
    include_permits_table: bool = True,
) -> dict[str, Any]:
    """Stream `Real_acct_owner.zip` to classify all accounts into the 6-tier ownership taxonomy (`DF-1`, `DF-6`)."""
    if not Path(real_acct_zip).exists():
        raise FileNotFoundError(f"Missing HCAD archive: {real_acct_zip}")

    raw_rows: dict[str, tuple[str, str, str, str, str, str, str, str, str, float, int]] = {}
    owner_counts: Counter[str] = Counter()
    billing_non_situs_counts: Counter[str] = Counter()
    billing_corp_entities: defaultdict[str, set[str]] = defaultdict(set)
    billing_accts: defaultdict[str, list[str]] = defaultdict(list)
    owner_accts: defaultdict[str, list[str]] = defaultdict(list)

    with zipfile.ZipFile(real_acct_zip, "r") as zf:
        if "real_acct.txt" in zf.namelist():
            with zf.open("real_acct.txt", "r") as raw_f:
                tf = io.TextIOWrapper(raw_f, encoding="latin-1", errors="replace")
                hdr = [c.strip() for c in tf.readline().rstrip("\r\n").split("\t")]
                col = {name: i for i, name in enumerate(hdr)}
                i_acct = col.get("acct", 0)
                i_mailto = col.get("mailto", 2)
                i_m1 = col.get("mail_addr_1", 3)
                i_m2 = col.get("mail_addr_2", 4)
                i_mcity = col.get("mail_city", -1)
                i_mstate = col.get("mail_state", -1)
                i_mzip = col.get("mail_zip", -1)
                i_site1 = col.get("site_addr_1", -1)
                i_snum = col.get("str_num", -1)
                i_spfx = col.get("str_pfx", -1)
                i_sname = col.get("str", -1)
                i_ssfx = col.get("str_sfx", -1)
                i_sc = col.get("state_class", -1)
                i_lgl1 = col.get("lgl_1", -1)
                i_mktval = col.get("tot_mkt_val", col.get("bld_val", -1))
                i_yrimpr = col.get("yr_impr", -1)

                for line in tf:
                    parts = line.rstrip("\r\n").split("\t")
                    if len(parts) <= i_acct:
                        continue
                    acct = re.sub(r"\D", "", parts[i_acct])
                    if len(acct) < 10:
                        continue
                    if len(acct) < 13:
                        acct = acct.zfill(13)
                    else:
                        acct = acct[:13]

                    owner_raw = sanitize_atlas_text(parts[i_mailto].strip() if len(parts) > i_mailto else "")
                    m1 = parts[i_m1].strip() if i_m1 >= 0 and len(parts) > i_m1 else ""
                    m2 = parts[i_m2].strip() if i_m2 >= 0 and len(parts) > i_m2 else ""
                    mcity = parts[i_mcity].strip() if i_mcity >= 0 and len(parts) > i_mcity else ""
                    mstate = parts[i_mstate].strip() if i_mstate >= 0 and len(parts) > i_mstate else ""
                    mzip = parts[i_mzip].strip() if i_mzip >= 0 and len(parts) > i_mzip else ""
                    site1 = parts[i_site1].strip() if i_site1 >= 0 and len(parts) > i_site1 else ""
                    if not site1 and i_snum >= 0 and i_sname >= 0:
                        toks = [
                            parts[idx].strip()
                            for idx in (i_snum, i_spfx, i_sname, i_ssfx)
                            if idx >= 0 and len(parts) > idx and parts[idx].strip()
                        ]
                        site1 = " ".join(toks)
                    sc = (parts[i_sc].strip() if i_sc >= 0 and len(parts) > i_sc else "").upper()
                    lgl1 = sanitize_atlas_text(parts[i_lgl1].strip() if i_lgl1 >= 0 and len(parts) > i_lgl1 else "")
                    mkt_v = 0.0
                    if i_mktval >= 0 and len(parts) > i_mktval and parts[i_mktval].strip():
                        try:
                            mkt_v = max(0.0, float(parts[i_mktval].strip()))
                        except ValueError:
                            mkt_v = 0.0
                    yr_i = 0
                    if i_yrimpr >= 0 and len(parts) > i_yrimpr and parts[i_yrimpr].strip().isdigit():
                        yr_cand = int(parts[i_yrimpr].strip())
                        if MIN_VALID_YEAR <= yr_cand <= MAX_VALID_YEAR:
                            yr_i = yr_cand

                    raw_rows[acct] = (
                        owner_raw,
                        m1,
                        m2,
                        mcity,
                        mstate,
                        mzip,
                        sanitize_atlas_text(site1),
                        sc,
                        lgl1,
                        mkt_v,
                        yr_i,
                    )

                    own_k = _normalize_owner_key(owner_raw)
                    if own_k and own_k not in PLACEHOLDER_NON_GRAPH_OWNER_TOKENS and not any(
                        own_k.startswith(tok) for tok in CONFIDENTIAL_OWNER_TOKENS
                    ):
                        owner_counts[own_k] += 1

                    bk, _disp, care_of = normalize_postal_billing_key(m1, m2, mcity, mstate, mzip)
                    if bk:
                        st_part = bk.split("|", 1)[0]
                        situs_norm = re.sub(r"\s+", " ", site1.upper()).strip()
                        is_situs_same = bool(situs_norm and st_part.startswith(situs_norm.split()[0]) and situs_norm[:12] in st_part)
                        if not is_situs_same and not is_tax_protest_agent_care_of(care_of, m1):
                            billing_non_situs_counts[bk] += 1

    # Pass 2: Pre-clustering homestead guard (DF-6) and portfolio cluster accumulation
    pre_homestead_flags: dict[str, tuple[bool, bool]] = {}
    for acct, row_t in raw_rows.items():
        owner_raw, m1, m2, mcity, mstate, mzip, site1, sc, lgl1, _mv, _yi = row_t
        own_k = _normalize_owner_key(owner_raw)
        bk, _disp, care_of = normalize_postal_billing_key(m1, m2, mcity, mstate, mzip)
        non_situs_sz = billing_non_situs_counts.get(bk, 0) if bk else 0
        own_cnt = owner_counts.get(own_k, 1) if own_k else 1

        is_hs, is_conf = is_protected_homestead(
            sc,
            owner_raw,
            m1,
            site1,
            mail_addr_2=m2,
            mail_city=mcity,
            mail_state=mstate,
            owner_parcel_count=own_cnt,
            cluster_non_situs_commercial_size=non_situs_sz,
        )
        pre_homestead_flags[acct] = (is_hs, is_conf)
        if is_hs:
            continue

        is_civic, _csub = is_hoa_or_civic_steward(sc, owner_raw, care_of=care_of, subdivision=lgl1)
        if is_civic:
            continue
        if is_registered_agent_billing_cluster(bk, owner_name=owner_raw, care_of=care_of):
            continue

        is_corp, _is_tr = is_corporate_or_trust_entity(owner_raw)
        if bk and not is_tax_protest_agent_care_of(care_of, m1):
            st_part = bk.split("|", 1)[0]
            if not (st_part.startswith("PO BOX") and not is_corp and own_cnt <= 2):
                billing_accts[bk].append(acct)
                if is_corp and own_k:
                    billing_corp_entities[bk].add(own_k)
        if own_k and own_k not in PLACEHOLDER_NON_GRAPH_OWNER_TOKENS and not any(
            own_k.startswith(tok) for tok in CONFIDENTIAL_OWNER_TOKENS
        ):
            owner_accts[own_k].append(acct)

    # Identify qualifying beneficial portfolios (>= 5 parcels or >= 2 distinct LLCs with >= 3 parcels)
    acct_to_port_id: dict[str, str] = {}
    port_meta_by_id: dict[str, dict[str, Any]] = {}

    for bk, accts_list in sorted(billing_accts.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        distinct_corps = billing_corp_entities.get(bk, set())
        if len(accts_list) < 5 and not (len(distinct_corps) >= 2 and len(accts_list) >= 3):
            continue
        ent_counter = Counter(_normalize_owner_key(raw_rows[a][0]) for a in accts_list if _normalize_owner_key(raw_rows[a][0]))
        primary_own = ent_counter.most_common(1)[0][0] if ent_counter else ""
        pid = _make_portfolio_id(bk, primary_own)
        sample_r = raw_rows[accts_list[0]]
        _, disp_mail, care_of = normalize_postal_billing_key(
            sample_r[1], sample_r[2], sample_r[3], sample_r[4], sample_r[5]
        )
        port_meta_by_id[pid] = {
            "port_id": pid,
            "billing_key": bk,
            "billing_address": disp_mail,
            "care_of": care_of,
            "primary_entity": primary_own,
            "distinct_corps": sorted(distinct_corps),
            "accts": list(accts_list),
        }
        for a in accts_list:
            acct_to_port_id[a] = pid

    # Also capture multi-parcel single-entity owners (>= 5 parcels) that lack a shared billing address
    for own_k, accts_list in sorted(owner_accts.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        unassigned = [a for a in accts_list if a not in acct_to_port_id]
        if len(unassigned) < 5:
            continue
        sample_r = raw_rows[unassigned[0]]
        bk, disp_mail, care_of = normalize_postal_billing_key(
            sample_r[1], sample_r[2], sample_r[3], sample_r[4], sample_r[5]
        )
        pid = _make_portfolio_id(bk, own_k)
        if pid not in port_meta_by_id:
            port_meta_by_id[pid] = {
                "port_id": pid,
                "billing_key": bk,
                "billing_address": disp_mail,
                "care_of": care_of,
                "primary_entity": own_k,
                "distinct_corps": [own_k] if is_corporate_or_trust_entity(own_k)[0] else [],
                "accts": list(unassigned),
            }
        else:
            port_meta_by_id[pid]["accts"].extend(unassigned)
        for a in unassigned:
            acct_to_port_id[a] = pid

    # Pass 3: Classify every account into its canonical `AccountOwnershipRecord`
    records: dict[str, AccountOwnershipRecord] = {}
    tier_counts: Counter[str] = Counter()

    for acct, row_t in raw_rows.items():
        owner_raw, m1, m2, mcity, mstate, mzip, site1, sc, lgl1, mkt_v, yr_i = row_t
        own_k = _normalize_owner_key(owner_raw)
        bk, _disp, _co = normalize_postal_billing_key(m1, m2, mcity, mstate, mzip)
        pid = acct_to_port_id.get(acct, "")
        clust_sz = len(billing_accts.get(bk, ())) if bk else 1
        clust_corps = len(billing_corp_entities.get(bk, ())) if bk else 0
        non_situs_sz = billing_non_situs_counts.get(bk, 0) if bk else 0
        own_cnt = owner_counts.get(own_k, 1) if own_k else 1

        cls_res = classify_account_ownership(
            state_class=sc,
            owner_name=owner_raw,
            situs_addr=site1,
            mail_addr_1=m1,
            mail_addr_2=m2,
            mail_city=mcity,
            mail_state=mstate,
            mail_zip=mzip,
            subdivision=lgl1,
            owner_parcel_count=own_cnt,
            cluster_parcel_count=clust_sz,
            cluster_distinct_corps=clust_corps,
            cluster_non_situs_commercial_size=non_situs_sz,
            port_id=pid,
        )
        tier = str(cls_res["own_tier"])
        tier_counts[tier] += 1
        records[acct] = AccountOwnershipRecord(
            acct=acct,
            own_tier=tier,
            port_id=str(cls_res["port_id"] or ""),
            owner_name=owner_raw,
            situs_addr=site1,
            state_class=sc,
            subdivision=lgl1,
            billing_key=str(cls_res["billing_key"] or ""),
            display_mail=str(cls_res["display_mail"] or ""),
            care_of=str(cls_res["care_of"] or ""),
            is_protected_homestead=bool(cls_res["is_protected_homestead"]),
            is_sec_25_025_confidential=bool(cls_res["is_sec_25_025_confidential"]),
            is_tax_agent_care_of=bool(cls_res["is_tax_agent_care_of"]),
            sub_tier=str(cls_res["sub_tier"] or ""),
            market_val=mkt_v,
            year_built=yr_i,
        )

    # Stream secondary co-owners / AKAs from `owners.txt` (strictly skipping protected homesteads, DF-6)
    if include_owners_table:
        with zipfile.ZipFile(real_acct_zip, "r") as zf:
            if "owners.txt" in zf.namelist():
                with zf.open("owners.txt", "r") as raw_f:
                    tf = io.TextIOWrapper(raw_f, encoding="latin-1", errors="replace")
                    hdr = [c.strip() for c in tf.readline().rstrip("\r\n").split("\t")]
                    col = {name: i for i, name in enumerate(hdr)}
                    io_acct = col.get("acct", 0)
                    io_ln = col.get("ln_num", 1)
                    io_nm = col.get("name", 2)
                    io_aka = col.get("aka", 3)
                    io_pct = col.get("pct_own", 4)
                    for line in tf:
                        parts = line.rstrip("\r\n").split("\t")
                        if len(parts) <= io_acct:
                            continue
                        ac = re.sub(r"\D", "", parts[io_acct])
                        if len(ac) < 10:
                            continue
                        ac = ac.zfill(13) if len(ac) < 13 else ac[:13]
                        rec = records.get(ac)
                        if rec is None or rec.is_protected_homestead or rec.is_sec_25_025_confidential:
                            continue
                        ln_s = parts[io_ln].strip() if len(parts) > io_ln else "1"
                        nm_s = sanitize_atlas_text(parts[io_nm].strip() if len(parts) > io_nm else "")
                        aka_s = sanitize_atlas_text(parts[io_aka].strip() if len(parts) > io_aka else "")
                        pct_s = parts[io_pct].strip() if len(parts) > io_pct else ""
                        if not nm_s and not aka_s:
                            continue
                        if _normalize_owner_key(nm_s) in PLACEHOLDER_NON_GRAPH_OWNER_TOKENS:
                            continue
                        if ln_s == "1" and not aka_s and _normalize_owner_key(nm_s) == _normalize_owner_key(rec.owner_name):
                            continue
                        label_parts = [nm_s or rec.owner_name]
                        if aka_s:
                            label_parts.append(f"AKA {aka_s}")
                        if pct_s:
                            try:
                                pf = float(pct_s)
                                if 0.0 < pf < 1.0:
                                    label_parts.append(f"{round(pf * 100):.0f}%")
                                elif 1.0 < pf < 100.0:
                                    label_parts.append(f"{round(pf):.0f}%")
                            except ValueError:
                                pass
                        co_entry = " ".join(x for x in label_parts if x).strip()
                        if co_entry and co_entry not in rec.co_owners and len(rec.co_owners) < 3:
                            rec.co_owners.append(co_entry[:96])

    # Stream main-structure demolition permits from `permits.txt` for pre-demolition flip calculation (DF-7)
    if include_permits_table:
        with zipfile.ZipFile(real_acct_zip, "r") as zf:
            if "permits.txt" in zf.namelist():
                with zf.open("permits.txt", "r") as raw_f:
                    tf = io.TextIOWrapper(raw_f, encoding="latin-1", errors="replace")
                    hdr = [c.strip() for c in tf.readline().rstrip("\r\n").split("\t")]
                    col = {name: i for i, name in enumerate(hdr)}
                    ip_acct = col.get("acct", 0)
                    ip_type = col.get("permit_type", -1)
                    ip_tpd = col.get("permit_tp_descr", -1)
                    ip_dscr = col.get("dscr", -1)
                    ip_dt = col.get("issue_date", -1)
                    ip_snum = col.get("site_num", -1)
                    ip_spfx = col.get("site_pfx", -1)
                    ip_sstr = col.get("site_str", -1)
                    ip_stp = col.get("site_tp", -1)
                    ip_apt = col.get("site_apt", -1)
                    for line in tf:
                        parts = line.rstrip("\r\n").split("\t")
                        if len(parts) <= ip_acct:
                            continue
                        ac = re.sub(r"\D", "", parts[ip_acct])
                        if len(ac) < 10:
                            continue
                        ac = ac.zfill(13) if len(ac) < 13 else ac[:13]
                        rec = records.get(ac)
                        if rec is None:
                            continue
                        dt_s = parts[ip_dt].strip() if ip_dt >= 0 and len(parts) > ip_dt else ""
                        if not dt_s:
                            continue
                        ptype = parts[ip_type].strip() if ip_type >= 0 and len(parts) > ip_type else ""
                        dscr = parts[ip_dscr].strip() if ip_dscr >= 0 and len(parts) > ip_dscr else ""
                        tpd = parts[ip_tpd].strip() if ip_tpd >= 0 and len(parts) > ip_tpd else ""
                        apt = parts[ip_apt].strip() if ip_apt >= 0 and len(parts) > ip_apt else ""
                        addr_t = " ".join(
                            parts[idx].strip()
                            for idx in (ip_snum, ip_spfx, ip_sstr, ip_stp)
                            if idx >= 0 and len(parts) > idx and parts[idx].strip()
                        )
                        is_main_demo, _dyr, _rsn = classify_main_structure_demolition_permit(
                            dscr,
                            ptype,
                            dt_s,
                            raw_address=addr_t,
                            site_apt=apt,
                            permit_desc=tpd,
                            min_year=1990,
                            max_year=int(tax_year) + 1,
                        )
                        if is_main_demo:
                            iso_dt, _ = normalize_hcad_date(
                                dt_s,
                                allow_fallback=False,
                                min_year=1990,
                                max_year=int(tax_year) + 1,
                            )
                            if iso_dt and (
                                not rec.latest_demo_permit_date or iso_dt > rec.latest_demo_permit_date
                            ):
                                rec.latest_demo_permit_date = iso_dt

    comptroller_entities = fetch_or_load_tx_comptroller_entities(
        cache_path=comptroller_cache_path,
        allow_network=False,
    )

    return {
        "records": records,
        "accounts": records,
        "portfolios_meta": port_meta_by_id,
        "portfolios": port_meta_by_id,
        "tier_counts": {k: int(tier_counts.get(k, 0)) for k in OWNERSHIP_TIER_KEYS},
        "comptroller_entities": comptroller_entities,
    }


def _compute_feature_centroid_lat_lng(geom: dict[str, Any] | None) -> tuple[float, float] | None:
    if not isinstance(geom, dict):
        return None
    gtype = geom.get("type")
    coords = geom.get("coordinates") or []
    pts = []
    if gtype == "Point" and len(coords) >= 2:
        return round(float(coords[1]), 6), round(float(coords[0]), 6)
    if gtype == "Polygon" and coords:
        pts = coords[0]
    elif gtype == "MultiPolygon" and coords and coords[0]:
        pts = coords[0][0]
    if not pts:
        return None
    xs = [float(p[0]) for p in pts if len(p) >= 2]
    ys = [float(p[1]) for p in pts if len(p) >= 2]
    if not xs or not ys:
        return None
    return round(sum(ys) / len(ys), 6), round(sum(xs) / len(xs), 6)


def build_and_write_ownership_portfolios_catalog(
    ownership_graph: dict[str, Any],
    *,
    aligned_dir: Path | None = None,
    output_path: Path = OWNERSHIP_PORTFOLIOS_JSON,
    max_portfolios: int = MAX_PORTFOLIOS_EXPORT,
    max_works_per_portfolio: int = MAX_WORKS_PER_PORTFOLIO,
) -> dict[str, Any]:
    """Build and write `app/public/data/ownership_portfolios.json` (`< 2.5 MiB`, `DF-4`, `DF-8`).

    Each portfolio includes embedded compact work coordinates:
    `[acct, address, lat, lng, year_built, style_or_use, entity_name, status]`
    so `#portfolio=owner_portfolio:<port_id>` plots every portfolio pin on cold hash load.
    """
    records: dict[str, AccountOwnershipRecord] = ownership_graph["records"]
    port_meta_by_id: dict[str, dict[str, Any]] = ownership_graph["portfolios_meta"]
    comptroller_map: dict[str, dict[str, str]] = ownership_graph.get("comptroller_entities") or {}

    # Collect standing building coordinates & styles from aligned_ndjson shards (or buildings.geojson fallback)
    bldg_info_by_acct: dict[str, tuple[str, float, float, int, str, str]] = {}
    search_aligned = aligned_dir if aligned_dir is not None else (CACHE_DIR / "aligned_ndjson")
    if search_aligned.exists():
        for q in QUAD_NAMES:
            qpath = search_aligned / f"buildings_{q}.geojsonseq"
            if not qpath.exists():
                continue
            with open(qpath, "rb") as fh:
                for raw_line in fh:
                    line = raw_line.strip()
                    if not line:
                        continue
                    if line.startswith(b"\x1e"):
                        line = line[1:]
                    feat = orjson.loads(line)
                    props = feat.get("properties") or {}
                    h = re.sub(r"\D", "", str(props.get("hcad_num") or ""))
                    if len(h) != 13 or h not in records:
                        continue
                    rec = records[h]
                    if not rec.port_id:
                        continue
                    if h in bldg_info_by_acct:
                        continue
                    lat_lng = _compute_feature_centroid_lat_lng(feat.get("geometry"))
                    if lat_lng is None:
                        continue
                    yr = int(props.get("year_built") or rec.year_built or 0)
                    addr = sanitize_atlas_text(str(props.get("address") or rec.situs_addr or ""))
                    style_or_use = sanitize_atlas_text(
                        str(props.get("bld_style") or props.get("use_category") or "Building")
                    )
                    nbhd = sanitize_atlas_text(
                        str(props.get("historic_district") or props.get("super_neighborhood") or "")
                    )
                    bldg_info_by_acct[h] = (addr, lat_lng[0], lat_lng[1], yr, style_or_use, nbhd)

    # Rank portfolios by historic significance (pre-1940 count + multi-LLC count) and parcel count
    scored_pids: list[tuple[tuple[int, int, int, int, str], str]] = []
    for pid, pmeta in port_meta_by_id.items():
        accts = pmeta.get("accts") or []
        pre1940 = sum(
            1
            for a in accts
            if 1836 <= (bldg_info_by_acct.get(a, ("", 0.0, 0.0, records[a].year_built, "", ""))[3]) < 1940
        )
        standing = sum(1 for a in accts if a in bldg_info_by_acct or records[a].year_built >= 1836)
        is_canon = 1 if pmeta.get("billing_key") in CANONICAL_BILLING_PORTFOLIO_SLUGS else 0
        corps_n = len(pmeta.get("distinct_corps") or ())
        rank = (
            is_canon,
            1 if (pre1940 >= 3 and corps_n >= 2) else 0,
            min(pre1940, 50),
            len(accts),
            pid,
        )
        scored_pids.append((rank, pid))

    scored_pids.sort(key=lambda item: item[0], reverse=True)
    selected_pids = [pid for _rk, pid in scored_pids[:max_portfolios]]

    portfolios_out: dict[str, dict[str, Any]] = {}
    for pid in selected_pids:
        pmeta = port_meta_by_id[pid]
        accts = pmeta.get("accts") or []
        if not accts:
            continue
        ent_counter: Counter[str] = Counter()
        nbhd_counter: Counter[str] = Counter()
        total_mkt = 0.0
        pre1940_cnt = 0
        standing_cnt = 0
        works_candidates: list[tuple[tuple[int, int, float, str], list[Any]]] = []

        for a in accts:
            rec = records.get(a)
            if rec is None:
                continue
            if rec.owner_name:
                ent_counter[rec.owner_name] += 1
            total_mkt += float(rec.market_val or 0.0)
            binfo = bldg_info_by_acct.get(a)
            yr = binfo[3] if binfo is not None else int(rec.year_built or 0)
            if yr >= MIN_VALID_YEAR or binfo is not None:
                standing_cnt += 1
            if MIN_VALID_YEAR <= yr < 1940:
                pre1940_cnt += 1
            if binfo is not None:
                addr, lat, lng, b_yr, style_use, nbhd = binfo
                if nbhd:
                    nbhd_counter[nbhd] += 1
                w_rank = (
                    1 if (MIN_VALID_YEAR <= b_yr < 1940) else 0,
                    -(b_yr if b_yr >= MIN_VALID_YEAR else 9999),
                    float(rec.market_val or 0.0),
                    a,
                )
                works_candidates.append((
                    w_rank,
                    [
                        a,
                        addr or rec.situs_addr or f"HCAD {a}",
                        lat,
                        lng,
                        int(b_yr or 0),
                        style_use[:40],
                        (rec.owner_name or pmeta.get("primary_entity") or "")[:64],
                        "standing",
                    ],
                ))

        works_candidates.sort(key=lambda item: item[0], reverse=True)
        works_rows = [w for _rk, w in works_candidates[:max_works_per_portfolio]]

        first_rec = records[accts[0]]
        distinct_entities = [nm for nm, _c in ent_counter.most_common()]
        primary_ent = distinct_entities[0] if distinct_entities else str(pmeta.get("primary_entity") or "")
        corps_count = max(len(pmeta.get("distinct_corps") or ()), len(distinct_entities))
        label = _format_portfolio_label(
            str(pmeta.get("billing_key") or ""),
            str(pmeta.get("billing_address") or ""),
            primary_ent,
            corps_count,
            len(accts),
        )

        comp_match = None
        for cand_ent in distinct_entities[:10]:
            ck = _normalize_owner_key(cand_ent)
            if ck in comptroller_map:
                comp_match = comptroller_map[ck]
                break

        port_entry: dict[str, Any] = {
            "port_id": pid,
            "label": sanitize_atlas_text(label),
            "primary_entity": sanitize_atlas_text(primary_ent),
            "own_tier": first_rec.own_tier,
            "billing_address": sanitize_atlas_text(str(pmeta.get("billing_address") or "")),
            "care_of": sanitize_atlas_text(str(pmeta.get("care_of") or "")),
            "parcel_count": len(accts),
            "standing_building_count": standing_cnt,
            "pre1940_count": pre1940_cnt,
            "distinct_entity_count": len(distinct_entities),
            "total_market_val": int(round(total_mkt)),
            "entity_samples": [sanitize_atlas_text(e)[:72] for e in distinct_entities[:12]],
            "top_neighborhoods": [sanitize_atlas_text(n) for n, _c in nbhd_counter.most_common(5)],
            "works": works_rows,
        }
        if comp_match:
            port_entry["comptroller"] = comp_match
        portfolios_out[pid] = port_entry

    catalog_doc = {
        "schema_version": 1,
        "tax_year": ACTIVE_TAX_YEAR,
        "work_tuple_fields": [
            "hcad_num",
            "address",
            "lat",
            "lng",
            "year_built",
            "style_or_use",
            "entity_name",
            "status",
        ],
        "tier_counts": ownership_graph.get("tier_counts") or {},
        "portfolio_count": len(portfolios_out),
        "portfolios": portfolios_out,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_compact_json(output_path, catalog_doc)
    sz = output_path.stat().st_size
    if sz >= MAX_OWNERSHIP_PORTFOLIOS_BYTES:
        raise RuntimeError(
            f"{output_path.name} ({sz:,} bytes) exceeds cap ({MAX_OWNERSHIP_PORTFOLIOS_BYTES:,} bytes)."
        )
    return catalog_doc


def build_account_enrichment_fields(
    rec: AccountOwnershipRecord | None,
    deed_pairs: list[tuple[str, str]] | None = None,
    *,
    include_non_homestead_mail: bool = True,
) -> dict[str, Any]:
    """Build compact permit-shard enrichment fields (`d`, `ot`, `pid`, `m`, `co`, `flip`) for one account (`DF-2`, `DF-6`, `DF-7`)."""
    out: dict[str, Any] = {}
    pairs = list(deed_pairs or ())
    if pairs:
        out["d"] = "|".join(f"{dt8}:{tok}" for tok, dt8 in pairs[:MAX_DEEDS_PER_PARCEL])

    if rec is not None:
        if rec.own_tier and rec.own_tier != "homestead":
            out["ot"] = rec.own_tier
        if rec.port_id:
            out["pid"] = rec.port_id

        # Strictly redact `m` and `co` on owner-occupied homesteads & Sec. 25.025 confidential accounts (DF-6)
        if not rec.is_protected_homestead and not rec.is_sec_25_025_confidential and rec.own_tier != "homestead":
            if include_non_homestead_mail and rec.display_mail:
                # Only emit `m` when it belongs to a portfolio, anonymity shield, or care-of agent account
                if (
                    rec.own_tier == "anonymity_shield"
                    or rec.port_id
                    or rec.care_of
                ):
                    mail_str = (
                        f"C/O {rec.care_of} - {rec.display_mail}"
                        if rec.care_of
                        else rec.display_mail
                    )
                    out["m"] = sanitize_atlas_text(mail_str)[:120]
            if rec.co_owners and (
                rec.port_id
                or rec.own_tier == "anonymity_shield"
            ):
                out["co"] = list(rec.co_owners[:3])

        # Compute pre-demolition flip days strictly on complete YYYYMMDD dates + main-structure demo permits (DF-7)
        if rec.latest_demo_permit_date and pairs:
            best_flip: int | None = None
            for _tok, dt8 in pairs:
                flip_d = compute_pre_demolition_flip_days(dt8, rec.latest_demo_permit_date, max_days=365)
                if flip_d is not None and (best_flip is None or flip_d < best_flip):
                    best_flip = flip_d
            if best_flip is not None:
                out["flip"] = int(best_flip)

    return out


def enrich_permit_shards_with_ownership_and_deeds(
    real_acct_zip: Path = REAL_ACCT_ZIP,
    permits_dir: Path = PERMITS_DIR,
    data_dir: Path = DATA_DIR,
    real_bldg_zip: Path = REAL_BLDG_ZIP,
    app_dir: Path = APP_DIR,
    *,
    tax_year: int | None = None,
    ownership_graph: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Idempotently enrich `permits_<prefix3>.json` shards with `d`, `ot`, `pid`, `m`, `co`, `flip` (`DF-2`, `DF-7`)."""
    if not Path(real_acct_zip).exists():
        raise FileNotFoundError(f"Missing HCAD archive: {real_acct_zip}")
    eff_tax = ACTIVE_TAX_YEAR if tax_year is None else int(tax_year)
    max_deed_year = eff_tax + 1

    graph = ownership_graph if ownership_graph is not None else build_ownership_graph(
        real_acct_zip,
        tax_year=eff_tax,
    )
    own_records: dict[str, AccountOwnershipRecord] = graph["records"]

    # 1. Load existing shards and idempotently strip all 6 ENRICHED_PERMIT_SHARD_KEYS (DF-2)
    shards: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    existing_acct_count = 0
    for shard_file in sorted(Path(permits_dir).glob("permits_*.json")):
        prefix3 = shard_file.stem.replace("permits_", "")
        data = json.loads(shard_file.read_text(encoding="utf-8"))
        cleaned: dict[str, dict[str, Any]] = {}
        for acct, rec in data.items():
            if isinstance(rec, dict):
                for k_strip in ENRICHED_PERMIT_SHARD_KEYS:
                    rec.pop(k_strip, None)
                if rec:
                    cleaned[acct] = rec
        shards[prefix3] = cleaned
        existing_acct_count += len(cleaned)

    # 2. Stream and normalize `deeds.txt`
    deeds_by_acct: dict[str, dict[str, str]] = defaultdict(dict)
    total_deed_rows = 0
    normalized_deed_rows = 0
    with zipfile.ZipFile(real_acct_zip, "r") as zf:
        if "deeds.txt" in zf.namelist():
            with zf.open("deeds.txt", "r") as raw_f:
                tf = io.TextIOWrapper(raw_f, encoding="latin-1", errors="replace")
                hdr = [c.strip() for c in tf.readline().rstrip("\r\n").split("\t")]
                col = {name: i for i, name in enumerate(hdr)}
                id_a = col.get("acct", 0)
                id_dos = col.get("dos", 1)
                id_cyr = col.get("clerk_yr", 2)
                id_cid = col.get("clerk_id", 3)
                for line in tf:
                    parts = line.rstrip("\r\n").split("\t")
                    if len(parts) <= id_a:
                        continue
                    total_deed_rows += 1
                    acct = re.sub(r"\D", "", parts[id_a])
                    if len(acct) < 10 or not acct[:3].isdigit():
                        continue
                    acct = acct.zfill(13) if len(acct) < 13 else acct[:13]
                    cyr_s = parts[id_cyr].strip() if len(parts) > id_cyr else ""
                    cid_s = parts[id_cid].strip() if len(parts) > id_cid else ""
                    tok = normalize_hcad_clerk_instrument(cyr_s, cid_s, max_year=max_deed_year)
                    if not tok:
                        continue
                    normalized_deed_rows += 1
                    dos_s = parts[id_dos].strip() if len(parts) > id_dos else ""
                    dt8 = parse_dos_yyyymmdd(dos_s, cyr_s, max_year=max_deed_year)
                    prev_dt = deeds_by_acct[acct].get(tok)
                    dt_key = (dt8 != "00000000" and not dt8.endswith("0000"), dt8)
                    prev_key = (
                        (prev_dt != "00000000" and not prev_dt.endswith("0000"), prev_dt)
                        if prev_dt is not None
                        else None
                    )
                    if prev_key is None or dt_key > prev_key:
                        deeds_by_acct[acct][tok] = dt8

    sorted_deeds_by_acct: dict[str, list[tuple[str, str]]] = {}
    for acct, tok_map in deeds_by_acct.items():
        pairs = sorted(tok_map.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
        sorted_deeds_by_acct[acct] = pairs[:MAX_DEEDS_PER_PARCEL]

    # 3. Compute baseline stripped size and check SOFT_MAX_APP_BYTES budget
    non_permit_bytes = compute_app_total_bytes(app_dir) - sum(
        p.stat().st_size for p in Path(permits_dir).glob("permits_*.json")
    )
    stripped_permits_bytes = sum(
        len(json.dumps(shard_dict, separators=(",", ":")).encode("utf-8"))
        for shard_dict in shards.values()
        if shard_dict
    )
    improved_hcads = collect_improved_and_mapped_hcads(
        shards, data_dir=data_dir, real_bldg_zip=real_bldg_zip
    )

    # Iterate across the UNION of deed accounts and ownership-graph accounts (DF-2)
    candidate_accts = sorted(set(sorted_deeds_by_acct.keys()) | set(own_records.keys()))
    enrichment_by_acct: dict[str, dict[str, Any]] = {}
    est_all_added = 0
    for acct in candidate_accts:
        rec = own_records.get(acct)
        dpairs = sorted_deeds_by_acct.get(acct)
        fields = build_account_enrichment_fields(rec, dpairs, include_non_homestead_mail=True)
        if not fields:
            continue
        enrichment_by_acct[acct] = fields
        p3 = acct[:3]
        raw_len = len(json.dumps(fields, separators=(",", ":")))
        if acct in shards.get(p3, {}):
            est_all_added += raw_len
        else:
            est_all_added += len(acct) + raw_len + 6

    include_unimproved = (
        non_permit_bytes + stripped_permits_bytes + est_all_added
    ) < SOFT_MAX_APP_BYTES

    # If including mail/co-owners on all accounts pushes over SOFT_MAX_APP_BYTES, keep `m` strictly on standing/improved or portfolio accounts
    if not include_unimproved:
        est_improved_added = 0
        for acct, fields in list(enrichment_by_acct.items()):
            if acct not in improved_hcads and acct not in shards.get(acct[:3], {}):
                continue
            est_improved_added += len(json.dumps(fields, separators=(",", ":")))
        if (non_permit_bytes + stripped_permits_bytes + est_improved_added) >= SOFT_MAX_APP_BYTES:
            for acct, fields in enrichment_by_acct.items():
                if not fields.get("pid") and fields.get("ot") != "anonymity_shield":
                    fields.pop("m", None)
                    fields.pop("co", None)

    enriched_existing = 0
    added_new_accts = 0
    for acct, fields in enrichment_by_acct.items():
        if not fields:
            continue
        p3 = acct[:3]
        if not p3.isdigit():
            continue
        shard_dict = shards[p3]
        if acct in shard_dict:
            shard_dict[acct].update(fields)
            enriched_existing += 1
        elif include_unimproved or acct in improved_hcads:
            shard_dict[acct] = dict(fields)
            added_new_accts += 1

    Path(permits_dir).mkdir(parents=True, exist_ok=True)
    projected_shards_bytes = 0
    max_shard_bytes = 0
    max_shard_name = ""
    for prefix3, shard_dict in sorted(shards.items()):
        if not shard_dict:
            continue
        raw_bytes = json.dumps(shard_dict, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        sz = len(raw_bytes)
        if sz >= MAX_SINGLE_FILE_BYTES:
            raise RuntimeError(
                f"Enriched shard permits_{prefix3}.json ({sz} bytes) exceeds "
                f"MAX_SINGLE_FILE_BYTES ({MAX_SINGLE_FILE_BYTES} bytes)."
            )
        projected_shards_bytes += sz
        if sz > max_shard_bytes:
            max_shard_bytes = sz
            max_shard_name = f"permits_{prefix3}.json"

    projected_total_bytes = non_permit_bytes + projected_shards_bytes
    if projected_total_bytes >= HARD_MAX_APP_BYTES:
        raise RuntimeError(
            f"Enriched app/ bundle ({projected_total_bytes} bytes) exceeds "
            f"HARD_MAX_APP_BYTES ({HARD_MAX_APP_BYTES} bytes)."
        )

    for prefix3, shard_dict in sorted(shards.items()):
        shard_file = Path(permits_dir) / f"permits_{prefix3}.json"
        if not shard_dict:
            shard_file.unlink(missing_ok=True)
            continue
        write_compact_json(shard_file, shard_dict)

    return {
        "enriched_existing": enriched_existing,
        "added_new_accts": added_new_accts,
        "normalized_deed_rows": normalized_deed_rows,
        "total_deed_rows": total_deed_rows,
        "max_shard_name": max_shard_name,
        "max_shard_bytes": max_shard_bytes,
        "projected_total_bytes": projected_total_bytes,
    }


def stamp_ownership_onto_geojsonseq_and_core(
    ownership_graph: dict[str, Any],
    *,
    aligned_dir: Path | None = None,
    quadrant_dir: Path | None = None,
    data_dir: Path = DATA_DIR,
) -> dict[str, Any]:
    """Stamp `own_tier` and `port_id` onto `.geojsonseq` shards, `buildings.geojson`, and `stats_summary.json`."""
    records: dict[str, AccountOwnershipRecord] = ownership_graph["records"]
    bldg_tier_counts: Counter[str] = Counter()
    total_stamped = 0

    target_dirs = [
        d
        for d in (
            aligned_dir if aligned_dir is not None else (CACHE_DIR / "aligned_ndjson"),
            quadrant_dir if quadrant_dir is not None else (CACHE_DIR / "quadrant_ndjson"),
        )
        if d is not None and d.exists()
    ]

    for idx_d, sdir in enumerate(target_dirs):
        for q in QUAD_NAMES:
            qpath = sdir / f"buildings_{q}.geojsonseq"
            if not qpath.exists():
                continue
            tmp_path = qpath.with_name(f".{qpath.name}.own.tmp")
            with open(qpath, "rb") as rf, open(tmp_path, "wb") as wf:
                for raw_line in rf:
                    line = raw_line.strip()
                    if not line:
                        continue
                    has_rs = line.startswith(b"\x1e")
                    payload_bytes = line[1:] if has_rs else line
                    feat = orjson.loads(payload_bytes)
                    props = feat.setdefault("properties", {})
                    h = re.sub(r"\D", "", str(props.get("hcad_num") or ""))
                    if len(h) < 13 and len(h) >= 10:
                        h = h.zfill(13)
                    rec = records.get(h)
                    if rec is not None:
                        props["own_tier"] = rec.own_tier
                        if rec.port_id:
                            props["port_id"] = rec.port_id
                        else:
                            props.pop("port_id", None)
                    else:
                        props["own_tier"] = str(props.get("own_tier") or "homestead")
                        props.pop("port_id", None)
                    if idx_d == 0:
                        bldg_tier_counts[str(props["own_tier"])] += 1
                        total_stamped += 1
                    out_bytes = orjson.dumps(feat)
                    if has_rs:
                        wf.write(b"\x1e" + out_bytes + b"\n")
                    else:
                        wf.write(out_bytes + b"\n")
            tmp_path.replace(qpath)

    core_blds_path = Path(data_dir) / "buildings.geojson"
    if core_blds_path.exists():
        core_doc = json.loads(core_blds_path.read_text(encoding="utf-8"))
        for feat in core_doc.get("features") or ():
            props = feat.setdefault("properties", {})
            h = re.sub(r"\D", "", str(props.get("hcad_num") or ""))
            if len(h) < 13 and len(h) >= 10:
                h = h.zfill(13)
            rec = records.get(h)
            if rec is not None:
                props["own_tier"] = rec.own_tier
                if rec.port_id:
                    props["port_id"] = rec.port_id
                else:
                    props.pop("port_id", None)
            else:
                props["own_tier"] = str(props.get("own_tier") or "homestead")
        write_compact_json(core_blds_path, core_doc)

    stats_path = Path(data_dir) / "stats_summary.json"
    if stats_path.exists():
        stats_doc = json.loads(stats_path.read_text(encoding="utf-8"))
        stats_doc["ownership_tier_counts"] = {
            k: int(bldg_tier_counts.get(k, 0)) for k in OWNERSHIP_TIER_KEYS
        }
        stats_path.write_text(json.dumps(stats_doc, indent=2) + "\n", encoding="utf-8")

    return {
        "total_buildings_stamped": total_stamped,
        "ownership_tier_counts": {k: int(bldg_tier_counts.get(k, 0)) for k in OWNERSHIP_TIER_KEYS},
    }


def main() -> None:
    print("1. Building countywide ownership & tax-billing graph from Real_acct_owner.zip...", flush=True)
    graph = build_ownership_graph(REAL_ACCT_ZIP, tax_year=ACTIVE_TAX_YEAR)
    print(
        f"   Classified {len(graph['records']):,} accounts across {len(graph['portfolios_meta']):,} portfolios: "
        f"{graph['tier_counts']}",
        flush=True,
    )

    print("2. Stamping own_tier and port_id onto .geojsonseq shards, buildings.geojson & stats_summary.json...", flush=True)
    stamp_res = stamp_ownership_onto_geojsonseq_and_core(graph)
    print(f"   Stamped {stamp_res['total_buildings_stamped']:,} standing buildings: {stamp_res['ownership_tier_counts']}", flush=True)

    print("3. Writing app/public/data/ownership_portfolios.json...", flush=True)
    cat_doc = build_and_write_ownership_portfolios_catalog(graph)
    print(
        f"   Wrote {cat_doc['portfolio_count']} portfolios to {OWNERSHIP_PORTFOLIOS_JSON} "
        f"({OWNERSHIP_PORTFOLIOS_JSON.stat().st_size / 1024:.1f} KB)",
        flush=True,
    )

    print("4. Enriching permits/permits_<prefix3>.json shards idempotently...", flush=True)
    shard_res = enrich_permit_shards_with_ownership_and_deeds(
        REAL_ACCT_ZIP,
        PERMITS_DIR,
        DATA_DIR,
        REAL_BLDG_ZIP,
        APP_DIR,
        tax_year=ACTIVE_TAX_YEAR,
        ownership_graph=graph,
    )
    print(
        f"   Enriched {shard_res['enriched_existing']:,} existing + {shard_res['added_new_accts']:,} new accounts "
        f"(projected app/ size: {shard_res['projected_total_bytes'] / (1024 * 1024):.2f} MiB)",
        flush=True,
    )


if __name__ == "__main__":
    main()
