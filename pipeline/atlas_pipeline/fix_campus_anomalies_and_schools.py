"""Re-resolve HCAD date-sentinel & stale campus_contiguity parcels, apply multi-building campus splits, and hydrate school_* records."""

from __future__ import annotations

from collections import Counter, defaultdict
import io
import json
import math
import pickle
from pathlib import Path
import re
import time
from typing import Any
import zipfile

import numpy as np
import orjson
import shapely
from shapely.strtree import STRtree

from atlas_pipeline.resolve_undated_countywide import (
    GENERIC_OWNERS,
    PHASE1_CURATED_LANDMARKS,
    is_valid_authoritative_donor,
    normalize_owner,
    normalize_street,
    resolve_contiguity_candidate,
    resolve_permit_deed_spatial_fallback,
)
from atlas_pipeline.schema import (
    ACTIVE_CACHE_BUST,
    MAX_VALID_CAMA_DATE_YEAR,
    MIN_VALID_YEAR,
    QUAD_NAMES,
    compile_quadrant_pmtiles,
    compute_decade,
    normalize_override_record,
    parse_hcad_date_year,
    sanitize_atlas_text,
    write_compact_json,
)

CAMPUS_SPLIT_HCADS: frozenset[str] = frozenset({
    "1223580020001",
    "1468640010001",
    "0451440030163",
    "0500610000001",
    "0500620000001",
    "0500580000001",
    "0500500000006",
    "1378920010001",
    "0451440050245",
    "0410210010510",
})

# Positive street number followed by street name (blocks '0 ELDRIDGE', '', 'TUCKERTON RD').
VALID_NUMBERED_SITUS_RE = re.compile(r"^([1-9]\d*[A-Z]?)\s+([A-Z0-9].*)$")

SCHOOL_INSTITUTIONAL_OWNER_TOKENS: tuple[str, ...] = (
    "ISD",
    "I S D",
    "CISD",
    "SCHOOL",
    "COLLEGE",
    "UNIVERSITY",
    "ACADEMY",
    "EDUCATION",
    "CHARTER",
    "DIOCESE",
    "ARCHDIOCESE",
    "CATHOLIC",
    "LUTHERAN",
    "EPISCOPAL",
    "BAPTIST",
    "METHODIST",
    "PUBLIC SCHOOLS",
    "KIPP",
    "YES PREP",
    "HARMONY",
)

# Authentic pre-1945 historic school campus that previously lacked HCAD yr_impr and fell into
# campus_contiguity; elevating to curated_landmark preserves its verified 1926 Harry D. Payne date.
ADDITIONAL_CURATED_PARCEL_CHRONOLOGIES: dict[str, tuple[int, str, str, str, str]] = {
    "0402330000134": (
        1926,
        "James S. Deady Middle School",
        "Collegiate Gothic / Classical Revival",
        "Harry D. Payne",
        "Houston ISD historic middle school at 2500 Broadway St, designed by architect Harry D. Payne and opened 1926.",
    ),
}

# Exact Concept3D (map=91) + archival building coordinates and primary-source chronologies for
# Lee College's main Baytown campus quadrangle on eastern parcel 0451440030163 (cx >= -94.980).
LEE_COLLEGE_MAIN_BUILDINGS: tuple[dict[str, Any], ...] = (
    {
        "slug": "lee_rundell_hall",
        "lon": -94.97823,
        "lat": 29.73231,
        "year_built": 1951,
        "name": "Lee College - Rundell Hall & Huddle Building",
        "address": "200 LEE DR",
        "bld_style": "Mid-Century Collegiate Modern",
        "citation": (
            "Original Lee College main administration and classroom building on the Lee Drive "
            "campus (200 Lee Dr, Baytown), completed 1951 when Lee Junior College relocated "
            "from Robert E. Lee High School."
        ),
    },
    {
        "slug": "lee_gymnasium",
        "lon": -94.97819,
        "lat": 29.73169,
        "year_built": 1951,
        "name": "Lee College Gymnasium",
        "address": "200 LEE DR",
        "bld_style": "Mid-Century Collegiate Athletic",
        "citation": (
            "Original Lee College Gymnasium on the 1951 Lee Drive campus quadrangle "
            "(200 Lee Dr, Baytown; Lee College Concept3D campus map=91)."
        ),
    },
    {
        "slug": "lee_john_britt_hall",
        "lon": -94.97710,
        "lat": 29.73226,
        "year_built": 1958,
        "name": "Lee College - John Britt Hall",
        "address": "200 LEE DR",
        "bld_style": "Mid-Century Collegiate Modern",
        "citation": (
            "Lee College John Britt Hall academic building (200 Lee Dr, Baytown), "
            "constructed 1958 during the initial Lee Drive campus expansion."
        ),
    },
    {
        "slug": "lee_moler_hall",
        "lon": -94.97683,
        "lat": 29.73178,
        "year_built": 1965,
        "name": "Lee College - Moler Hall",
        "address": "200 LEE DR",
        "bld_style": "Mid-Century Collegiate Modern",
        "citation": "Lee College Moler Hall academic facility (200 Lee Dr, Baytown), constructed 1965.",
    },
    {
        "slug": "lee_bonner_hall",
        "lon": -94.97599,
        "lat": 29.73141,
        "year_built": 1965,
        "name": "Lee College - Bonner Hall",
        "address": "200 LEE DR",
        "bld_style": "Mid-Century Collegiate Modern",
        "citation": "Lee College Bonner Hall classroom building (200 Lee Dr, Baytown), constructed 1965.",
    },
    {
        "slug": "lee_student_center",
        "lon": -94.97609,
        "lat": 29.73231,
        "year_built": 1965,
        "yr_remodel": 2009,
        "name": "Lee College - Student Center",
        "address": "200 LEE DR",
        "bld_style": "Mid-Century Collegiate / Contemporary Expansion",
        "citation": (
            "Lee College Student Center (200 Lee Dr, Baytown), original student center "
            "constructed 1965 and expanded/renovated 2009."
        ),
    },
    {
        "slug": "lee_tech_voc_complex",
        "lon": -94.97664,
        "lat": 29.73047,
        "year_built": 1965,
        "name": "Lee College - Technical-Vocational Complex (Tech-Voc 1-4 & Pilot Plant)",
        "address": "200 LEE DR",
        "bld_style": "Collegiate Technical & Vocational",
        "citation": (
            "Lee College Technical-Vocational 1-4 and Process Technology Complex "
            "(200 Lee Dr, Baytown), established 1965."
        ),
    },
    {
        "slug": "lee_south_central_plant",
        "lon": -94.97734,
        "lat": 29.73020,
        "year_built": 1968,
        "name": "Lee College - South Central Plant",
        "address": "200 LEE DR",
        "bld_style": "Campus Central Utility Plant",
        "citation": "Lee College South Central Utility Plant (200 Lee Dr, Baytown), constructed 1968.",
    },
    {
        "slug": "lee_gray_science",
        "lon": -94.97709,
        "lat": 29.73093,
        "year_built": 1990,
        "name": "Lee College - Gray Science Building",
        "address": "200 LEE DR",
        "bld_style": "Late 20th-Century Collegiate Science",
        "citation": "Lee College Gray Science Building (200 Lee Dr, Baytown), completed 1990.",
    },
    {
        "slug": "lee_tucker_hall",
        "lon": -94.97629,
        "lat": 29.73268,
        "year_built": 1991,
        "name": "Lee College - Tucker Hall",
        "address": "200 LEE DR",
        "bld_style": "Late 20th-Century Collegiate",
        "citation": "Lee College Tucker Hall (200 Lee Dr, Baytown), completed 1991.",
    },
    {
        "slug": "lee_atc_library",
        "lon": -94.97719,
        "lat": 29.73297,
        "year_built": 2002,
        "name": "Lee College - Advanced Technology Center & Library",
        "address": "200 LEE DR",
        "bld_style": "Contemporary Collegiate Technology & Library",
        "citation": "Lee College Advanced Technology Center and Library (200 Lee Dr, Baytown), opened 2002.",
    },
    {
        "slug": "lee_sports_arena",
        "lon": -94.97792,
        "lat": 29.73114,
        "year_built": 2003,
        "name": "Lee College - Sports Arena & Wellness Center",
        "address": "200 LEE DR",
        "bld_style": "Contemporary Collegiate Arena",
        "citation": "Lee College Sports Arena and Wellness Center (200 Lee Dr, Baytown), opened 2003.",
    },
)


def _extract_outer_ring(geom: dict[str, Any]) -> list[list[float]]:
    coords = (geom or {}).get("coordinates") or []
    if not coords or not coords[0]:
        return []
    ring = coords[0]
    if ring and isinstance(ring[0][0], list):
        ring = ring[0]
    return ring


def _ring_centroid_and_sqft(ring: list[list[float]]) -> tuple[float, float, float]:
    if not ring or len(ring) < 3:
        return (0.0, 0.0, 0.0)
    n = max(1, len(ring) - 1)
    cx = sum(float(pt[0]) for pt in ring[:n]) / n
    cy = sum(float(pt[1]) for pt in ring[:n]) / n
    cos_lat = math.cos(math.radians(cy))
    area_m2 = 0.5 * abs(
        sum(
            (ring[i][0] * 111320.0 * cos_lat) * (ring[i + 1][1] * 110540.0)
            - (ring[i + 1][0] * 111320.0 * cos_lat) * (ring[i][1] * 110540.0)
            for i in range(len(ring) - 1)
        )
    )
    return (cx, cy, area_m2 * 10.7639)


def _dist_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    cos_lat = math.cos(math.radians((lat1 + lat2) * 0.5))
    return math.hypot((lon1 - lon2) * 111320.0 * cos_lat, (lat1 - lat2) * 110540.0)


def _clean_whitespace_upper(val: str) -> str:
    return " ".join(sanitize_atlas_text((val or "").upper()).split())


def _canonical_street_core(raw_street: str) -> str:
    """Normalize street directionals/suffixes for fuzzy street-name compatibility checks."""
    s = _clean_whitespace_upper(raw_street)
    s = re.sub(
        r"\b(PKWY|PKY|PARKWAY|ST|STREET|DR|DRIVE|AVE|AVENUE|RD|ROAD|BLVD|BOULEVARD|LN|LANE|FWY|FREEWAY|HWY|HIGHWAY|CT|COURT|CIR|CIRCLE|PL|PLACE|TRL|TRAIL|WAY)\b",
        "",
        s,
    )
    s = re.sub(r"^(N|S|E|W|NORTH|SOUTH|EAST|WEST)\s+", "", s)
    s = re.sub(r"\s+(N|S|E|W|NORTH|SOUTH|EAST|WEST)$", "", s)
    return " ".join(s.split())


def is_eligible_zero_street_hydration(
    current_addr: str,
    school_addr: str,
    parcel_owner: str,
    parcel_state_class: str,
    parcel_use_category: str,
    dist_m: float,
) -> bool:
    """Return True only when a '0 <STREET>' parcel situs can safely adopt a school's street address.

    Guards against three spatial-hydration hazards (DF-5):
      1. Rejects empty ('') or unnumbered ('TUCKERTON RD') school addresses via VALID_NUMBERED_SITUS_RE.
      2. Rejects commercial/industrial strip-center parcels (state_class F1/F2) where private or
         charter schools lease tenant suites adjacent to unrelated '0 <STREET>' tracts.
      3. Requires institutional/tax-exempt ownership or X* state_class within 120m (or exact
         street-core match on '0 <SAME_STREET>' within 180m).
    """
    cur_norm = _clean_whitespace_upper(current_addr)
    if cur_norm and not cur_norm.startswith("0 "):
        return False

    sch_norm = _clean_whitespace_upper(school_addr)
    m = VALID_NUMBERED_SITUS_RE.match(sch_norm)
    if not m:
        return False

    st_cls = (parcel_state_class or "").strip().upper()
    if st_cls.startswith(("F1", "F2")):
        return False

    own_up = _clean_whitespace_upper(parcel_owner)
    is_exempt_or_inst = (
        st_cls.startswith("X")
        or any(tok in own_up for tok in SCHOOL_INSTITUTIONAL_OWNER_TOKENS)
        or (parcel_use_category == "Civic / Institutional" and not st_cls.startswith(("A", "B", "C", "D", "F")))
    )
    if not is_exempt_or_inst:
        return False

    if cur_norm.startswith("0 "):
        cur_core = _canonical_street_core(cur_norm[2:])
        sch_core = _canonical_street_core(m.group(2))
        if cur_core and sch_core and (cur_core in sch_core or sch_core in cur_core):
            return dist_m <= 180.0

    return dist_m <= 120.0


def _compute_address_shard_key(house_num: str) -> str:
    digits = re.sub(r"\D", "", str(house_num or ""))
    if not digits or digits.startswith("0"):
        return ""
    return digits[:2].ljust(2, "0")


def _build_campus_split_assignments(
    campus_feats_by_hcad: dict[str, list[tuple[str, int, dict[str, Any], float, float, float]]],
) -> tuple[
    dict[tuple[str, int], dict[str, Any]],
    dict[str, dict[str, Any]],
    list[dict[str, Any]],
]:
    """Build per-footprint shard mutations, geometry-free curated_overrides, and search_index items."""
    feat_updates: dict[tuple[str, int], dict[str, Any]] = {}
    new_overrides: dict[str, dict[str, Any]] = {}
    search_additions: list[dict[str, Any]] = []

    def _register(
        shard_name: str,
        line_idx: int,
        hcad: str,
        slug: str,
        *,
        year_built: int,
        orig_year: int,
        name: str,
        address: str,
        bld_style: str,
        source_type: str,
        citation: str,
        lon: float,
        lat: float,
        architect: str = "",
        yr_remodel: int = 0,
        add_to_search: bool = True,
        search_category: str = "",
    ) -> None:
        bid = f"{hcad}#{slug}"
        upd: dict[str, Any] = {
            "id": bid,
            "building_id": bid,
            "hcad_num": hcad,
            "year_built": year_built,
            "decade": compute_decade(year_built),
            "year_source": "curated_landmark",
            "landmark_name": name,
            "building_name": name,
            "address": address,
            "bld_style": bld_style,
            "use_category": "Civic / Institutional",
        }
        if architect:
            upd["architect"] = architect
        if yr_remodel >= MIN_VALID_YEAR:
            upd["remodel_year"] = yr_remodel
            upd["yr_remodel"] = yr_remodel
        feat_updates[(shard_name, line_idx)] = upd

        # Shard-Primary Sub-Building Metadata Contract (DF-4): geometry-free metadata override
        # keeps all sibling PMTiles footprints visible while preventing duplicate 3D extrusions.
        ov_rec: dict[str, Any] = {
            "id": bid,
            "building_id": bid,
            "hcad_num": hcad,
            "is_building_override": True,
            "keep_shard_footprints": True,
            "replace_parcel_shards": False,
            "address": address,
            "contributing": "Outside Historic District",
            "year_built": year_built,
            "decade": compute_decade(year_built),
            "original_hcad_year": orig_year,
            "use_category": "Civic / Institutional",
            "bld_style": bld_style,
            "landmark_name": name,
            "building_name": name,
            "lon": round(float(lon), 6),
            "lat": round(float(lat), 6),
            "source_type": source_type,
            "source_citation": citation,
            "verified_by": "Preservation Houston Archival Review",
            "updated_at": "2026-10-27",
        }
        if not add_to_search:
            ov_rec["skip_search_index"] = True
        if architect:
            ov_rec["architect"] = architect
        if yr_remodel >= MIN_VALID_YEAR:
            ov_rec["yr_remodel"] = yr_remodel
        new_overrides[bid] = normalize_override_record(bid, ov_rec)

        if add_to_search:
            sub_parts = [address, search_category or "Civic / Institutional", bld_style]
            if architect:
                sub_parts.append(f"Arch: {architect}")
            sub_parts.append(f"Built {year_built}")
            si_item: dict[str, Any] = {
                "type": "building",
                "id": bid,
                "building_id": bid,
                "hcad_num": hcad,
                "label": name,
                "building_name": name,
                "address": address,
                "sublabel": " \u2022 ".join(p for p in sub_parts if p),
                "category": search_category or f"Built {year_built}",
                "year_built": year_built,
                "bld_style": bld_style,
                "use_category": "Civic / Institutional",
                "lon": round(lon, 6),
                "lat": round(lat, 6),
                "zoom": 17.5,
            }
            if architect:
                si_item["architect"] = architect
            search_additions.append(si_item)

    # 1. 1223580020001: Cypress Ridge High School (north) vs Eldridge Transportation Center (south)
    cy_items = campus_feats_by_hcad.get("1223580020001", [])
    cy_north = sorted([x for x in cy_items if x[4] >= 29.8900], key=lambda x: (-x[5], x[3], x[4]))
    cy_south = sorted([x for x in cy_items if x[4] < 29.8900], key=lambda x: (-x[5], x[3], x[4]))
    for idx, (sname, lidx, feat, cx, cy, _sqft) in enumerate(cy_north):
        orig_yr = int((feat.get("properties") or {}).get("year_built") or 0)
        if idx == 0:
            _register(
                sname,
                lidx,
                "1223580020001",
                "cy_ridge_hs_main",
                year_built=2002,
                orig_year=orig_yr,
                name="Cypress Ridge High School",
                address="7900 N ELDRIDGE PKWY",
                bld_style="Contemporary Educational Campus",
                source_type="Cypress-Fairbanks ISD Campus Dedication & Archival Record",
                citation=(
                    "Cypress Ridge High School (7900 N Eldridge Pkwy) opened Fall 2002 in "
                    "Cypress-Fairbanks ISD; corrects HCAD permits.txt 12/30/1899 null-date "
                    "sentinel and unnumbered 0 N Eldridge Pky reserve situs."
                ),
                lon=cx,
                lat=cy,
                add_to_search=True,
                search_category="CYPRESS-FAIRBANKS ISD",
            )
        else:
            _register(
                sname,
                lidx,
                "1223580020001",
                f"cy_ridge_hs_aux_{idx}",
                year_built=2002,
                orig_year=orig_yr,
                name="Cypress Ridge High School - Campus Support Structure",
                address="7900 N ELDRIDGE PKWY",
                bld_style="Educational Campus Support / Modular Classroom",
                source_type="Cypress-Fairbanks ISD Campus Dedication & Archival Record",
                citation=(
                    "Auxiliary classroom/athletic structure at Cypress Ridge High School "
                    "(7900 N Eldridge Pkwy, opened Fall 2002)."
                ),
                lon=cx,
                lat=cy,
                add_to_search=False,
            )

    for idx, (sname, lidx, feat, cx, cy, _sqft) in enumerate(cy_south):
        orig_yr = int((feat.get("properties") or {}).get("year_built") or 0)
        if idx == 0:
            _register(
                sname,
                lidx,
                "1223580020001",
                "eldridge_trans_main",
                year_built=2002,
                orig_year=orig_yr,
                name="Cy-Fair ISD Eldridge Transportation Center",
                address="7600 N ELDRIDGE PKWY",
                bld_style="District Transportation & Maintenance Facility",
                source_type="Cypress-Fairbanks ISD Operations & HCAD COGV Record",
                citation=(
                    "Cy-Fair ISD Eldridge Transportation Center (7600 N Eldridge Pkwy; HCAD "
                    "extra_features_detail2 COGV 'SCHOOL&BUS BARN'), constructed 2002 on the "
                    "southern half of shared Cy-Fair ISD reserve 1223580020001."
                ),
                lon=cx,
                lat=cy,
                add_to_search=True,
                search_category="CYPRESS-FAIRBANKS ISD",
            )
        else:
            _register(
                sname,
                lidx,
                "1223580020001",
                f"eldridge_trans_aux_{idx}",
                year_built=2002,
                orig_year=orig_yr,
                name="Cy-Fair ISD Eldridge Transportation Center - Fleet & Service Building",
                address="7600 N ELDRIDGE PKWY",
                bld_style="District Transportation & Maintenance Facility",
                source_type="Cypress-Fairbanks ISD Operations & HCAD COGV Record",
                citation=(
                    "Fleet maintenance and bus operations structure at Cy-Fair ISD Eldridge "
                    "Transportation Center (7600 N Eldridge Pkwy, constructed 2002)."
                ),
                lon=cx,
                lat=cy,
                add_to_search=False,
            )

    # 2. 1468640010001: Robert E. Lee High School (1928 Harry D. Payne main vs 1954 Lowell Lammers shops)
    lee_hs_items = sorted(
        campus_feats_by_hcad.get("1468640010001", []),
        key=lambda x: (-x[5], x[3], x[4]),
    )
    for idx, (sname, lidx, feat, cx, cy, _sqft) in enumerate(lee_hs_items):
        orig_yr = int((feat.get("properties") or {}).get("year_built") or 0)
        if idx == 0:
            _register(
                sname,
                lidx,
                "1468640010001",
                "lee_hs_main_1928",
                year_built=1928,
                orig_year=orig_yr,
                name="Robert E. Lee High School",
                address="1809 MARKET ST",
                bld_style="Art Deco / Classical Revival",
                architect="Harry D. Payne",
                source_type="Goose Creek CISD Archival Dedication & Architectural Record",
                citation=(
                    "Robert E. Lee High School (1809 Market St, Baytown) historic main building "
                    "and auditorium opened Fall 1928, designed by Houston school architect "
                    "Harry D. Payne (also housed Lee Junior College 1934-1951); replaces HCAD "
                    "1950 COGV administrative placeholder."
                ),
                lon=cx,
                lat=cy,
                add_to_search=True,
                search_category="GOOSE CREEK CISD",
            )
        else:
            _register(
                sname,
                lidx,
                "1468640010001",
                f"lee_hs_shop_{idx}",
                year_built=1954,
                orig_year=orig_yr,
                name="Robert E. Lee High School - Vocational & Shop Addition",
                address="1809 MARKET ST",
                bld_style="Mid-Century Educational",
                architect="Lowell Lammers",
                source_type="Goose Creek CISD Facilities & Architectural Record",
                citation=(
                    "Detached western vocational and industrial arts shop addition at Robert E. "
                    "Lee High School (1809 Market St, Baytown), designed by architect Lowell "
                    "Lammers (1953-1954)."
                ),
                lon=cx,
                lat=cy,
                add_to_search=(idx == 1),
                search_category="GOOSE CREEK CISD",
            )

    # 3. 0451440030163: IMPACT Early College HS (west, cx < -94.980) vs Lee College Main Campus (east)
    lc_main_items = campus_feats_by_hcad.get("0451440030163", [])
    lc_west = sorted([x for x in lc_main_items if x[3] < -94.980], key=lambda x: (-x[5], x[3], x[4]))
    lc_east = [x for x in lc_main_items if x[3] >= -94.980]

    for idx, (sname, lidx, feat, cx, cy, _sqft) in enumerate(lc_west):
        orig_yr = int((feat.get("properties") or {}).get("year_built") or 0)
        if idx == 0:
            _register(
                sname,
                lidx,
                "0451440030163",
                "impact_echs_main",
                year_built=1981,
                orig_year=orig_yr,
                name="IMPACT Early College High School (Former GCCISD Administration Building)",
                address="1415 MARKET ST",
                bld_style="Late 20th-Century Educational / Administrative",
                source_type="Goose Creek CISD & Lee College Campus Record",
                citation=(
                    "Constructed 1981 at 1415 Market St in Baytown as Goose Creek CISD Central "
                    "Administration; adapted for IMPACT Early College High School in partnership "
                    "with Lee College. Corrects spurious 1927 demolished-lot contiguity date."
                ),
                lon=cx,
                lat=cy,
                add_to_search=True,
                search_category="GOOSE CREEK CISD",
            )
        else:
            _register(
                sname,
                lidx,
                "0451440030163",
                f"impact_echs_aux_{idx}",
                year_built=1981,
                orig_year=orig_yr,
                name="IMPACT Early College High School Campus Building",
                address="1415 MARKET ST",
                bld_style="Educational Campus Structure",
                source_type="Goose Creek CISD & Lee College Campus Record",
                citation=(
                    "Classroom and support structure on the 1415 Market St IMPACT Early College "
                    "High School / Goose Creek CISD tract (established 1981)."
                ),
                lon=cx,
                lat=cy,
                add_to_search=False,
            )

    matched_east_indices: set[int] = set()
    for spec in LEE_COLLEGE_MAIN_BUILDINGS:
        best_i = -1
        best_d = 999999.0
        for i, (_sname, _lidx, _feat, cx, cy, _sqft) in enumerate(lc_east):
            if i in matched_east_indices:
                continue
            d = _dist_m(cx, cy, float(spec["lon"]), float(spec["lat"]))
            if d < best_d and d <= 40.0:
                best_d = d
                best_i = i
        if best_i >= 0:
            matched_east_indices.add(best_i)
            sname, lidx, feat, cx, cy, _sqft = lc_east[best_i]
            orig_yr = int((feat.get("properties") or {}).get("year_built") or 0)
            _register(
                sname,
                lidx,
                "0451440030163",
                str(spec["slug"]),
                year_built=int(spec["year_built"]),
                orig_year=orig_yr,
                name=str(spec["name"]),
                address=str(spec["address"]),
                bld_style=str(spec["bld_style"]),
                yr_remodel=int(spec.get("yr_remodel") or 0),
                source_type="Lee College Facilities & Concept3D Official Campus Map",
                citation=str(spec["citation"]),
                lon=cx,
                lat=cy,
                add_to_search=True,
                search_category="LEE COLLEGE",
            )

    unmatched_east = sorted(
        [lc_east[i] for i in range(len(lc_east)) if i not in matched_east_indices],
        key=lambda x: (-x[5], x[3], x[4]),
    )
    for idx, (sname, lidx, feat, cx, cy, _sqft) in enumerate(unmatched_east, 1):
        orig_yr = int((feat.get("properties") or {}).get("year_built") or 0)
        _register(
            sname,
            lidx,
            "0451440030163",
            f"lee_campus_aux_{idx}",
            year_built=1970,
            orig_year=orig_yr,
            name="Lee College - Campus Support Pavilion",
            address="200 LEE DR",
            bld_style="Collegiate Campus Support Structure",
            source_type="Lee College Facilities & Concept3D Official Campus Map",
            citation=(
                "Campus utility and support structure on Lee College's main Baytown quadrangle "
                "(200 Lee Dr, developed post-1951; replaces spurious 1927 demolished-lot date)."
            ),
            lon=cx,
            lat=cy,
            add_to_search=False,
        )

    # 4. 0500610000001: Lee College Tech-Voc 5,6,7 (501 S Whiting) & Tech-Voc 9 (511 S Whiting)
    for idx, (sname, lidx, feat, cx, cy, _sqft) in enumerate(
        sorted(campus_feats_by_hcad.get("0500610000001", []), key=lambda x: (-x[5], x[3]))
    ):
        orig_yr = int((feat.get("properties") or {}).get("year_built") or 0)
        if idx == 0:
            _register(
                sname,
                lidx,
                "0500610000001",
                "lee_tech_voc_567",
                year_built=1968,
                orig_year=orig_yr,
                name="Lee College - Tech-Voc 5, 6, 7",
                address="501 S WHITING ST",
                bld_style="Collegiate Technical & Vocational",
                source_type="Lee College Facilities & Concept3D Official Campus Map",
                citation=(
                    "Lee College Technical-Vocational Buildings 5, 6, and 7 (501 S Whiting St, "
                    "Baytown; Concept3D map=91), constructed 1968."
                ),
                lon=cx,
                lat=cy,
                add_to_search=True,
                search_category="LEE COLLEGE",
            )
        else:
            _register(
                sname,
                lidx,
                "0500610000001",
                "lee_tech_voc_9",
                year_built=1968,
                orig_year=orig_yr,
                name="Lee College - Tech-Voc 9",
                address="511 S WHITING ST",
                bld_style="Collegiate Technical & Vocational",
                source_type="Lee College Facilities & Concept3D Official Campus Map",
                citation=(
                    "Lee College Technical-Vocational Building 9 (511 S Whiting St, Baytown; "
                    "Concept3D map=91), constructed 1968."
                ),
                lon=cx,
                lat=cy,
                add_to_search=True,
                search_category="LEE COLLEGE",
            )

    # 5. 0500620000001: Lee College Physical Plant, Transportation & Shipping/Receiving (520 S Whiting St)
    p62_specs = (
        ("lee_physical_plant", "Lee College - Physical Plant", True),
        ("lee_transportation", "Lee College - Transportation Facility", True),
        ("lee_shipping_receiving", "Lee College - Shipping & Receiving", True),
        ("lee_plant_aux_1", "Lee College - Physical Plant Support Structure", False),
        ("lee_plant_aux_2", "Lee College - Physical Plant Support Structure", False),
    )
    for idx, (sname, lidx, feat, cx, cy, _sqft) in enumerate(
        sorted(campus_feats_by_hcad.get("0500620000001", []), key=lambda x: (-x[5], x[3]))
    ):
        orig_yr = int((feat.get("properties") or {}).get("year_built") or 0)
        slug, bname, add_si = p62_specs[min(idx, len(p62_specs) - 1)]
        if idx >= len(p62_specs):
            slug = f"lee_plant_aux_{idx}"
        _register(
            sname,
            lidx,
            "0500620000001",
            slug,
            year_built=1972,
            orig_year=orig_yr,
            name=bname,
            address="520 S WHITING ST",
            bld_style="Collegiate Campus Operations & Plant",
            source_type="Lee College Facilities & Concept3D Official Campus Map",
            citation=(
                f"{bname} (520 S Whiting St, Baytown; Concept3D map=91), constructed 1972; "
                "replaces spurious 1926 demolished-lot contiguity date."
            ),
            lon=cx,
            lat=cy,
            add_to_search=add_si,
            search_category="LEE COLLEGE",
        )

    # 6. 0500580000001: Lee College Tech-Voc 10 (500 S Pruett St)
    for sname, lidx, feat, cx, cy, _sqft in campus_feats_by_hcad.get("0500580000001", []):
        orig_yr = int((feat.get("properties") or {}).get("year_built") or 0)
        _register(
            sname,
            lidx,
            "0500580000001",
            "lee_tech_voc_10",
            year_built=1972,
            orig_year=orig_yr,
            name="Lee College - Tech-Voc 10",
            address="500 S PRUETT ST",
            bld_style="Collegiate Technical & Vocational",
            source_type="Lee College Facilities & Concept3D Official Campus Map",
            citation=(
                "Lee College Technical-Vocational Building 10 (500 S Pruett St, Baytown; "
                "Concept3D map=91), constructed 1972."
            ),
            lon=cx,
            lat=cy,
            add_to_search=True,
            search_category="LEE COLLEGE",
        )

    # 7. 0500500000006: Lee College Security Facility (600 W Republic St)
    for sname, lidx, feat, cx, cy, _sqft in campus_feats_by_hcad.get("0500500000006", []):
        orig_yr = int((feat.get("properties") or {}).get("year_built") or 0)
        _register(
            sname,
            lidx,
            "0500500000006",
            "lee_security_facility",
            year_built=1970,
            orig_year=orig_yr,
            name="Lee College - Campus Security Facility",
            address="600 W REPUBLIC ST",
            bld_style="Collegiate Campus Operations",
            source_type="Lee College Facilities & Concept3D Official Campus Map",
            citation=(
                "Lee College Campus Security Facility (600 W Republic St, Baytown; Concept3D "
                "map=91), constructed circa 1970."
            ),
            lon=cx,
            lat=cy,
            add_to_search=True,
            search_category="LEE COLLEGE",
        )

    # 8. 1378920010001: Lee College Performing Arts Center (2008) & McNulty-Haddick Nursing Complex (1952)
    for idx, (sname, lidx, feat, cx, cy, _sqft) in enumerate(
        sorted(campus_feats_by_hcad.get("1378920010001", []), key=lambda x: (-x[5], x[3]))
    ):
        orig_yr = int((feat.get("properties") or {}).get("year_built") or 0)
        if idx == 0:
            _register(
                sname,
                lidx,
                "1378920010001",
                "lee_pac_2008",
                year_built=2008,
                orig_year=orig_yr,
                name="Lee College Performing Arts Center",
                address="805 W TEXAS AVE",
                bld_style="Contemporary Performing Arts Center",
                source_type="Lee College Dedication & HCAD COGV Record",
                citation=(
                    "Lee College Performing Arts Center (805 W Texas Ave, Baytown), completed "
                    "2008 (verified via Lee College dedication records and HCAD "
                    "extra_features_detail2 COGV 'PERFORMING ARTS BLDG 08')."
                ),
                lon=cx,
                lat=cy,
                add_to_search=True,
                search_category="LEE COLLEGE",
            )
        elif idx == 1:
            _register(
                sname,
                lidx,
                "1378920010001",
                "lee_mcnulty_haddick",
                year_built=1952,
                yr_remodel=1990,
                orig_year=orig_yr,
                name="Lee College - McNulty-Haddick Nursing Complex",
                address="711 W TEXAS AVE",
                bld_style="Mid-Century Medical / Collegiate Nursing",
                source_type="Lee College Facilities & Concept3D Official Campus Map",
                citation=(
                    "Lee College McNulty-Haddick Allied Health & Nursing Complex (711 W Texas "
                    "Ave, Baytown), originally constructed 1952 as a hospital facility and "
                    "adapted by Lee College in 1990."
                ),
                lon=cx,
                lat=cy,
                add_to_search=True,
                search_category="LEE COLLEGE",
            )
        else:
            _register(
                sname,
                lidx,
                "1378920010001",
                "lee_pac_aux",
                year_built=2008,
                orig_year=orig_yr,
                name="Lee College Performing Arts Support Annex",
                address="805 W TEXAS AVE",
                bld_style="Contemporary Performing Arts Support",
                source_type="Lee College Dedication & HCAD COGV Record",
                citation=(
                    "Support structure serving Lee College Performing Arts Center "
                    "(805 W Texas Ave, Baytown, completed 2008)."
                ),
                lon=cx,
                lat=cy,
                add_to_search=False,
            )

    # 9. 0451440050245: George Washington Carver Elementary (Historic Horace Mann Junior High, 1928)
    for idx, (sname, lidx, feat, cx, cy, _sqft) in enumerate(
        sorted(campus_feats_by_hcad.get("0451440050245", []), key=lambda x: (-x[5], x[3]))
    ):
        orig_yr = int((feat.get("properties") or {}).get("year_built") or 0)
        if idx == 0:
            _register(
                sname,
                lidx,
                "0451440050245",
                "carver_elem_main",
                year_built=1928,
                orig_year=orig_yr,
                name="George Washington Carver Elementary School (Historic Horace Mann Junior High)",
                address="610 S PRUETT ST",
                bld_style="Early 20th-Century Brick Educational",
                source_type="Goose Creek CISD Historic Campus Record",
                citation=(
                    "Constructed 1928 at 610 S Pruett St in Baytown as Horace Mann Junior High "
                    "School; later rededicated as George Washington Carver Elementary School in "
                    "Goose Creek CISD."
                ),
                lon=cx,
                lat=cy,
                add_to_search=True,
                search_category="GOOSE CREEK CISD",
            )
        else:
            _register(
                sname,
                lidx,
                "0451440050245",
                f"carver_elem_aux_{idx}",
                year_built=1928,
                orig_year=orig_yr,
                name="George Washington Carver Elementary School - Support Pavilion",
                address="610 S PRUETT ST",
                bld_style="Educational Campus Support",
                source_type="Goose Creek CISD Historic Campus Record",
                citation=(
                    "Campus support structure at George Washington Carver Elementary School "
                    "(610 S Pruett St, Baytown)."
                ),
                lon=cx,
                lat=cy,
                add_to_search=False,
            )

    # 10. 0410210010510: Ashbel Smith Elementary School (403 E James St, rebuilt 1996)
    for idx, (sname, lidx, feat, cx, cy, _sqft) in enumerate(
        sorted(campus_feats_by_hcad.get("0410210010510", []), key=lambda x: (-x[5], x[3]))
    ):
        orig_yr = int((feat.get("properties") or {}).get("year_built") or 0)
        slug = "ashbel_smith_elem" if idx == 0 else f"ashbel_smith_elem_aux_{idx}"
        _register(
            sname,
            lidx,
            "0410210010510",
            slug,
            year_built=1996,
            orig_year=orig_yr,
            name="Ashbel Smith Elementary School",
            address="403 E JAMES ST",
            bld_style="Late 20th-Century Educational Campus",
            source_type="Goose Creek CISD Campus Facilities Record",
            citation=(
                "Modern two-story Ashbel Smith Elementary School (403 E James St, Baytown) "
                "completed 1996 on the historic Goose Creek school site, replacing the "
                "demolished 1927 school building."
            ),
            lon=cx,
            lat=cy,
            add_to_search=(idx == 0),
            search_category="GOOSE CREEK CISD",
        )

    return feat_updates, new_overrides, search_additions


def run_campus_and_school_fixes(
    cache_dir: Path | None = None,
    data_dir: Path | None = None,
    *,
    compile_pmtiles: bool = True,
    shard_dir: Path | None = None,
) -> dict[str, Any]:
    """Execute countywide sentinel/contiguity re-resolution, campus splits, school linkage, and tile rebuild."""
    t0 = time.time()
    repo_dir = Path(__file__).resolve().parents[2]
    cache_dir = cache_dir or (repo_dir / "pipeline" / "cache")
    data_dir = data_dir or (repo_dir / "app" / "public" / "data")
    if shard_dir is not None:
        aligned_dir = shard_dir
    elif (cache_dir / "aligned_ndjson").exists():
        aligned_dir = cache_dir / "aligned_ndjson"
    else:
        aligned_dir = cache_dir / "quadrant_ndjson"

    print("=== Campus Chronology, Sentinel Re-Resolution & School Situs Pipeline ===")

    with open(cache_dir / "cama_2026_compact.pkl", "rb") as f:
        cama_lookup: dict[str, tuple[int, int, int, str, str, str]] = pickle.load(f)

    overrides_path = data_dir / "curated_overrides.json"
    overrides_payload = json.loads(overrides_path.read_text(encoding="utf-8"))
    overrides_map: dict[str, dict[str, Any]] = overrides_payload.setdefault("overrides", {})

    curated_donor_hcads = set(PHASE1_CURATED_LANDMARKS.keys()) | set(
        ADDITIONAL_CURATED_PARCEL_CHRONOLOGIES.keys()
    )
    for ok, ov_rec in overrides_map.items():
        oh = str(ov_rec.get("hcad_num") or ok.split("#")[0]).strip()
        if oh:
            curated_donor_hcads.add(oh)

    campus_split_hcads = CAMPUS_SPLIT_HCADS

    # 1. Scan all 5 shards to seed authoritative donors and locate sentinel / stale contiguity parcels
    print("-> Scanning 5 aligned_ndjson shards for authoritative donors and sentinel/stale-contiguity targets...")
    dated_parcel_year: dict[str, int] = {}
    dated_parcel_coord: dict[str, tuple[float, float]] = {}
    re_resolve_hcads: set[str] = set()
    target_coords: dict[str, tuple[float, float]] = {}
    campus_feats_by_hcad: dict[str, list[tuple[str, int, dict[str, Any], float, float, float]]] = defaultdict(list)

    for q in QUAD_NAMES:
        seq_path = aligned_dir / f"buildings_{q}.geojsonseq"
        with open(seq_path, "rb") as f:
            for line_idx, raw in enumerate(f):
                line = raw.lstrip(b"\x1e").strip()
                if not line:
                    continue
                feat = orjson.loads(line)
                props = feat.get("properties") or {}
                hcad = str(props.get("hcad_num") or "").strip()
                yr = int(props.get("year_built") or 0)
                src = str(props.get("year_source") or "").strip()
                ring = _extract_outer_ring(feat.get("geometry") or {})
                cx, cy, sqft = _ring_centroid_and_sqft(ring)

                if hcad in campus_split_hcads:
                    campus_feats_by_hcad[hcad].append((seq_path.name, line_idx, feat, cx, cy, sqft))

                if hcad and (cx or cy) and hcad not in target_coords:
                    target_coords[hcad] = (cx, cy)

                is_sentinel_permit_or_deed = src in ("hcad_permit", "hcad_deed") and yr <= 1901
                is_stale_pre45_campus = src == "campus_contiguity" and yr < 1945
                is_out_of_range = not (MIN_VALID_YEAR <= yr <= MAX_VALID_CAMA_DATE_YEAR)

                if hcad and (is_sentinel_permit_or_deed or is_stale_pre45_campus or is_out_of_range):
                    if hcad not in campus_split_hcads:
                        re_resolve_hcads.add(hcad)
                    continue

                if hcad:
                    cama_row = cama_lookup.get(hcad)
                    bld_ar = float(cama_row[2]) if cama_row is not None else float(props.get("bld_area") or 0.0)
                    if is_valid_authoritative_donor(
                        yr,
                        src,
                        bld_ar,
                        has_curated_override=(hcad in curated_donor_hcads),
                    ):
                        if hcad not in dated_parcel_year or yr < dated_parcel_year[hcad]:
                            dated_parcel_year[hcad] = yr
                            if cx or cy:
                                dated_parcel_coord[hcad] = (cx, cy)

    for hcad, cval in cama_lookup.items():
        if hcad not in re_resolve_hcads and hcad not in dated_parcel_year and is_valid_authoritative_donor(
            int(cval[0] or 0),
            "hcad_cama",
            float(cval[2] or 0.0),
            has_curated_override=(hcad in curated_donor_hcads),
        ):
            dated_parcel_year[hcad] = int(cval[0])

    print(
        f"   Seeded {len(dated_parcel_year):,} authoritative bld_ar>0 donor parcels; "
        f"queued {len(re_resolve_hcads):,} non-split sentinel/stale-contiguity parcels for re-resolution."
    )

    # 2. Load real_acct.txt, parcel_tieback.txt, permits.txt, and deeds.txt for clean re-resolution + school guards
    acct_info: dict[str, tuple[str, str, str, str]] = {}  # acct -> (owner_raw, norm_own, state_class, site_addr_1)
    re_meta: dict[str, dict[str, str]] = {}
    owner_to_dated: dict[str, list[tuple[int, float, float, str]]] = defaultdict(list)
    addr_to_dated: dict[tuple[str, str], list[tuple[int, float, float, str]]] = defaultdict(list)
    street_to_dated_years: dict[tuple[str, str], list[int]] = defaultdict(list)
    subdiv_to_dated_years: dict[str, list[int]] = defaultdict(list)
    nbhd_to_dated_years: dict[str, list[int]] = defaultdict(list)
    mkt_to_dated_years: dict[str, list[int]] = defaultdict(list)
    tiebacks: dict[str, list[str]] = defaultdict(list)
    permit_years: dict[str, list[tuple[int, str]]] = defaultdict(list)
    deed_earliest: dict[str, int] = {}

    t_cama = time.time()
    with zipfile.ZipFile(cache_dir / "Real_acct_owner.zip", "r") as zf:
        if "parcel_tieback.txt" in zf.namelist():
            with zf.open("parcel_tieback.txt", "r") as raw_f:
                text_f = io.TextIOWrapper(raw_f, encoding="latin1", errors="replace")
                header = text_f.readline().rstrip("\r\n").split("\t")
                col = {name: i for i, name in enumerate(header)}
                i_acct = col.get("acct", 0)
                i_rel = col.get("related_acct", 3)
                for line in text_f:
                    parts = line.rstrip("\r\n").split("\t")
                    if len(parts) <= i_rel:
                        continue
                    a = parts[i_acct].strip()
                    b = parts[i_rel].strip()
                    if a and b and (a in re_resolve_hcads or b in re_resolve_hcads):
                        tiebacks[a].append(b)
                        tiebacks[b].append(a)

        with zf.open("real_acct.txt", "r") as raw_f:
            text_f = io.TextIOWrapper(raw_f, encoding="latin1", errors="replace")
            header = text_f.readline().rstrip("\r\n").split("\t")
            col = {name: i for i, name in enumerate(header)}
            i_acct = col.get("acct", 0)
            i_mailto = col.get("mailto", 2)
            i_addr1 = col.get("site_addr_1", 17)
            i_zip = col.get("site_addr_3", 19)
            i_st_cls = col.get("state_class", 20)
            i_nbhd = col.get("Neighborhood_Code", 24)
            i_mkt = col.get("Market_Area_1", 26)
            i_splt = col.get("splt_dt", 35)
            i_new_own = col.get("new_own_dt", 64)
            i_lgl2 = col.get("lgl_2", 66)

            for line in text_f:
                parts = line.rstrip("\r\n").split("\t")
                if len(parts) <= i_st_cls:
                    continue
                acct = parts[i_acct].strip()
                if not acct:
                    continue
                owner_raw = parts[i_mailto].strip()
                addr1 = parts[i_addr1].strip()
                st_cls = parts[i_st_cls].strip()
                norm_own = normalize_owner(owner_raw)
                acct_info[acct] = (owner_raw, norm_own, st_cls, addr1)

                zip_cd = parts[i_zip].strip()[:5] if len(parts) > i_zip else ""
                nbhd = parts[i_nbhd].strip() if len(parts) > i_nbhd else ""
                mkt = parts[i_mkt].strip() if len(parts) > i_mkt else ""
                lgl2 = parts[i_lgl2].strip() if len(parts) > i_lgl2 else ""
                st_num, st_name = normalize_street(addr1)

                if acct in re_resolve_hcads:
                    re_meta[acct] = {
                        "owner_raw": owner_raw,
                        "norm_own": norm_own,
                        "addr1": addr1,
                        "st_num": st_num,
                        "st_name": st_name,
                        "st_cls": st_cls,
                        "zip_cd": zip_cd,
                        "nbhd": nbhd,
                        "mkt": mkt,
                        "lgl2": lgl2,
                        "splt_dt": parts[i_splt].strip() if len(parts) > i_splt else "",
                        "new_own_dt": parts[i_new_own].strip() if len(parts) > i_new_own else "",
                    }

                dyr = dated_parcel_year.get(acct, 0)
                if MIN_VALID_YEAR <= dyr <= MAX_VALID_CAMA_DATE_YEAR:
                    coord = dated_parcel_coord.get(acct)
                    if coord:
                        if norm_own and norm_own not in GENERIC_OWNERS:
                            owner_to_dated[norm_own].append((dyr, coord[0], coord[1], acct))
                        if st_num and st_name:
                            addr_to_dated[(st_num, st_name)].append((dyr, coord[0], coord[1], acct))
                    if st_name and zip_cd:
                        street_to_dated_years[(zip_cd, st_name)].append(dyr)
                    if len(lgl2) >= 4:
                        subdiv_to_dated_years[lgl2].append(dyr)
                    if nbhd:
                        nbhd_to_dated_years[nbhd].append(dyr)
                    if mkt:
                        mkt_to_dated_years[mkt].append(dyr)

        if "permits.txt" in zf.namelist():
            with zf.open("permits.txt", "r") as raw_f:
                text_f = io.TextIOWrapper(raw_f, encoding="latin1", errors="replace")
                header = text_f.readline().rstrip("\r\n").split("\t")
                col = {name: i for i, name in enumerate(header)}
                i_acct = col.get("acct", 0)
                i_dscr = col.get("dscr", 4)
                i_tp_dscr = col.get("permit_tp_descr", 7)
                i_dt = col.get("issue_date", 9)
                i_yr = col.get("yr", 10)
                for line in text_f:
                    parts = line.rstrip("\r\n").split("\t")
                    if len(parts) <= i_dt:
                        continue
                    acct = parts[i_acct].strip()
                    if acct not in re_resolve_hcads:
                        continue
                    yr = parse_hcad_date_year(
                        parts[i_dt].strip(),
                        parts[i_yr].strip() if len(parts) > i_yr else None,
                        allow_fallback=False,
                    )
                    if MIN_VALID_YEAR <= yr <= MAX_VALID_CAMA_DATE_YEAR:
                        tp_d = parts[i_tp_dscr].strip() if len(parts) > i_tp_dscr else ""
                        dscr = parts[i_dscr].strip() if len(parts) > i_dscr else ""
                        permit_years[acct].append((yr, tp_d or dscr))

        if "deeds.txt" in zf.namelist():
            with zf.open("deeds.txt", "r") as raw_f:
                text_f = io.TextIOWrapper(raw_f, encoding="latin1", errors="replace")
                header = text_f.readline().rstrip("\r\n").split("\t")
                col = {name: i for i, name in enumerate(header)}
                i_acct = col.get("acct", 0)
                i_dos = col.get("dos", 1)
                i_cyr = col.get("clerk_yr", 2)
                for line in text_f:
                    parts = line.rstrip("\r\n").split("\t")
                    if len(parts) <= i_dos:
                        continue
                    acct = parts[i_acct].strip()
                    if acct not in re_resolve_hcads:
                        continue
                    yr = parse_hcad_date_year(
                        parts[i_dos].strip(),
                        parts[i_cyr].strip() if len(parts) > i_cyr else None,
                        allow_fallback=True,
                    )
                    if MIN_VALID_YEAR <= yr <= MAX_VALID_CAMA_DATE_YEAR:
                        if acct not in deed_earliest or yr < deed_earliest[acct]:
                            deed_earliest[acct] = yr

    for acct, meta in re_meta.items():
        for dt_field in ("splt_dt", "new_own_dt"):
            yr = parse_hcad_date_year(meta.get(dt_field, ""), allow_fallback=False)
            if MIN_VALID_YEAR <= yr <= MAX_VALID_CAMA_DATE_YEAR:
                if acct not in deed_earliest or yr < deed_earliest[acct]:
                    deed_earliest[acct] = yr

    print(f"   Loaded CAMA ownership, tieback, permit, and deed tables in {time.time() - t_cama:.1f}s.")

    # Precompute spatial/subdivision medians for Phase 1 re-resolution fallback
    street_median = {
        k: int(round(float(np.median(v)))) for k, v in street_to_dated_years.items() if len(v) >= 2
    }
    subdiv_median = {
        k: int(round(float(np.median(v)))) for k, v in subdiv_to_dated_years.items() if len(v) >= 3
    }
    nbhd_median = {
        k: int(round(float(np.median(v)))) for k, v in nbhd_to_dated_years.items() if len(v) >= 3
    }
    mkt_median = {
        k: int(round(float(np.median(v)))) for k, v in mkt_to_dated_years.items() if len(v) >= 5
    }
    grid_buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
    for acct, (dlon, dlat) in dated_parcel_coord.items():
        dyr = dated_parcel_year.get(acct, 0)
        if MIN_VALID_YEAR <= dyr <= MAX_VALID_CAMA_DATE_YEAR:
            grid_buckets[(int(round(dlon * 250.0)), int(round(dlat * 250.0)))].append(dyr)

    re_resolved: dict[str, dict[str, Any]] = {}
    for hcad in sorted(re_resolve_hcads):
        if hcad in ADDITIONAL_CURATED_PARCEL_CHRONOLOGIES:
            cyr, lm_name, style, arch, notes = ADDITIONAL_CURATED_PARCEL_CHRONOLOGIES[hcad]
            re_resolved[hcad] = {
                "year_built": cyr,
                "year_source": "curated_landmark",
                "landmark_name": lm_name,
                "bld_style": style,
                "architect": arch,
                "notes": notes,
            }
            continue
        if hcad in PHASE1_CURATED_LANDMARKS:
            cyr, lm_name, style, notes = PHASE1_CURATED_LANDMARKS[hcad]
            re_resolved[hcad] = {
                "year_built": cyr,
                "year_source": "curated_landmark",
                "landmark_name": lm_name,
                "bld_style": style,
                "notes": notes,
            }
            continue

        # Phase 2B tieback
        best_tb_yr = 0
        for rel in tiebacks.get(hcad, ()):
            ryr = dated_parcel_year.get(rel, 0)
            if ryr >= MIN_VALID_YEAR and (best_tb_yr == 0 or ryr < best_tb_yr):
                best_tb_yr = ryr
        if best_tb_yr >= MIN_VALID_YEAR:
            re_resolved[hcad] = {
                "year_built": best_tb_yr,
                "year_source": "hcad_tieback",
            }
            continue

        meta = re_meta.get(hcad, {})
        ucoord = target_coords.get(hcad)
        if ucoord:
            cand = resolve_contiguity_candidate(
                ucoord[0],
                ucoord[1],
                meta.get("norm_own", ""),
                meta.get("st_num", ""),
                meta.get("st_name", ""),
                addr_to_dated=addr_to_dated,
                owner_to_dated=owner_to_dated,
                exclude_acct=hcad,
            )
            if cand is not None:
                re_resolved[hcad] = cand
                continue

        local_era = (
            street_median.get((meta.get("zip_cd", ""), meta.get("st_name", "")))
            or subdiv_median.get(meta.get("lgl2", ""))
            or nbhd_median.get(meta.get("nbhd", ""))
        )
        nn_med = 0
        if ucoord:
            gx, gy = int(round(ucoord[0] * 250.0)), int(round(ucoord[1] * 250.0))
            cell_yrs: list[int] = []
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    cell_yrs.extend(grid_buckets.get((gx + dx, gy + dy), ()))
            if cell_yrs:
                nn_med = int(round(float(np.median(cell_yrs))))

        fb_res = resolve_permit_deed_spatial_fallback(
            permit_candidates=permit_years.get(hcad),
            earliest_deed_year=deed_earliest.get(hcad, 0),
            state_class=str(meta.get("st_cls", "")),
            local_era=int(local_era or 0),
            nn_median=int(nn_med or 0),
            market_median=int(mkt_median.get(meta.get("mkt", "")) or 0),
        )
        re_resolved[hcad] = {
            "year_built": int(fb_res["year_built"]),
            "year_source": str(fb_res["year_source"]),
        }

    print(f"   Re-resolved {len(re_resolved):,} / {len(re_resolve_hcads):,} sentinel & stale-contiguity parcels.")

    # 3. Build multi-building campus splits (Phase 2)
    campus_feat_updates, new_campus_overrides, campus_search_additions = _build_campus_split_assignments(
        campus_feats_by_hcad
    )
    print(
        f"   Prepared {len(campus_feat_updates):,} <hcad>#<slug> campus footprints and "
        f"{len(new_campus_overrides):,} geometry-free curated_overrides records."
    )

    # 4. Load search_index.json and prepare spatial index for Two-Phase school_* linkage (DF-5)
    search_index_path = data_dir / "search_index.json"
    search_index: list[dict[str, Any]] = json.loads(search_index_path.read_text(encoding="utf-8"))

    # Fix known mis-geocoded school_706 (IMPACT Early College HS, 1415 Market St) and GCCISD categories
    for item in search_index:
        sid = str(item.get("id") or "")
        if sid == "school_706":
            item["lon"] = -94.98146
            item["lat"] = 29.73271
            item["category"] = "GOOSE CREEK CISD"
        elif sid == "school_705":
            item["category"] = "GOOSE CREEK CISD"

    school_items = [it for it in search_index if str(it.get("id") or "").startswith("school_")]
    cos_ref = math.cos(math.radians(29.76))
    school_pts_m = [
        shapely.Point(float(it["lon"]) * 111320.0 * cos_ref, float(it["lat"]) * 110540.0)
        for it in school_items
    ]
    school_tree = STRtree(school_pts_m)

    # Scan shards to collect all footprint polygons within 260m of any school_* pin so we only
    # construct Shapely polygons for ~8,000 candidate footprints instead of all 1.51M footprints!
    cand_footprints: list[dict[str, Any]] = []
    parcel_fp_counts: Counter[str] = Counter()

    for q in QUAD_NAMES:
        seq_path = aligned_dir / f"buildings_{q}.geojsonseq"
        with open(seq_path, "rb") as f:
            for line_idx, raw in enumerate(f):
                line = raw.lstrip(b"\x1e").strip()
                if not line:
                    continue
                feat = orjson.loads(line)
                props = feat.get("properties") or {}
                hcad = str(props.get("hcad_num") or "").strip()
                if hcad:
                    parcel_fp_counts[hcad] += 1

                # Apply Phase 1 & Phase 2 updates virtually so school_* linkage sees final years/IDs/addresses
                c_upd = campus_feat_updates.get((seq_path.name, line_idx))
                if c_upd:
                    eff_props = {**props, **c_upd}
                elif hcad in re_resolved:
                    rinfo = re_resolved[hcad]
                    eff_props = {
                        **props,
                        "year_built": int(rinfo["year_built"]),
                        "decade": compute_decade(int(rinfo["year_built"])),
                        "year_source": rinfo["year_source"],
                    }
                    if rinfo.get("landmark_name") and not eff_props.get("landmark_name"):
                        eff_props["landmark_name"] = rinfo["landmark_name"]
                    if rinfo.get("bld_style") and not eff_props.get("bld_style"):
                        eff_props["bld_style"] = rinfo["bld_style"]
                    if rinfo.get("architect") and not eff_props.get("architect"):
                        eff_props["architect"] = rinfo["architect"]
                else:
                    eff_props = props

                ring = _extract_outer_ring(feat.get("geometry") or {})
                if len(ring) < 3:
                    continue
                cx, cy, sqft = _ring_centroid_and_sqft(ring)
                pt_m = shapely.Point(cx * 111320.0 * cos_ref, cy * 110540.0)
                near_idxs = school_tree.query(pt_m.buffer(320.0))
                if len(near_idxs) == 0:
                    continue
                poly_m = shapely.Polygon(
                    [( float(pt[0]) * 111320.0 * cos_ref, float(pt[1]) * 110540.0 ) for pt in ring]
                )
                cand_footprints.append({
                    "shard_name": seq_path.name,
                    "line_idx": line_idx,
                    "hcad_num": hcad,
                    "cx": cx,
                    "cy": cy,
                    "sqft": sqft,
                    "poly_m": poly_m,
                    "props": eff_props,
                })

    # Assign deterministic <hcad>#b<k> IDs on multi-footprint candidate school parcels whose
    # footprints currently lack '#' sub-building IDs, sorted largest-first.
    by_cand_hcad: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for cf in cand_footprints:
        if cf["hcad_num"]:
            by_cand_hcad[cf["hcad_num"]].append(cf)

    auto_sub_id_updates: dict[tuple[str, int], str] = {}
    for hcad, cflist in by_cand_hcad.items():
        if parcel_fp_counts[hcad] <= 1 or hcad in campus_split_hcads:
            continue
        # Skip parcels that already have explicit '#' overrides in curated_overrides.json
        if any(
            k.startswith(f"{hcad}#") for k in overrides_map
        ):
            continue
        cflist_sorted = sorted(cflist, key=lambda c: (-c["sqft"], round(c["cx"], 6), round(c["cy"], 6)))
        for idx, cf in enumerate(cflist_sorted, 1):
            cur_id = str(cf["props"].get("building_id") or cf["props"].get("id") or "").strip()
            if "#" not in cur_id:
                new_bid = f"{hcad}#b{idx}"
                cf["props"]["id"] = new_bid
                cf["props"]["building_id"] = new_bid
                auto_sub_id_updates[(cf["shard_name"], cf["line_idx"])] = new_bid

    cand_tree = STRtree([cf["poly_m"] for cf in cand_footprints])

    # Phase 3A: Link school_* items to best matching building footprint
    linked_schools = 0
    parcel_hydration_votes: dict[str, set[str]] = defaultdict(set)
    parcel_hydration_min_dist: dict[str, float] = {}

    for s_idx, item in enumerate(school_items):
        s_pt = school_pts_m[s_idx]
        s_addr = _clean_whitespace_upper(str(item.get("address") or ""))
        s_num, s_street = normalize_street(s_addr)
        s_core = _canonical_street_core(s_street)

        hit_idxs = cand_tree.query(s_pt.buffer(240.0))
        best_cf: dict[str, Any] | None = None
        best_score = -1e9
        best_dist = 999999.0

        for h_i in hit_idxs.tolist():
            cf = cand_footprints[int(h_i)]
            hcad = cf["hcad_num"]
            if not hcad:
                continue
            dist = float(cf["poly_m"].distance(s_pt))
            fp_addr = _clean_whitespace_upper(str(cf["props"].get("address") or ""))
            fp_num, fp_street = normalize_street(fp_addr)
            fp_core = _canonical_street_core(fp_street)
            exact_addr = bool(s_num and fp_num and s_num == fp_num and s_core and s_core == fp_core)

            if dist > 150.0 and not exact_addr:
                continue

            own_raw, _norm_own, st_cls, _a1 = acct_info.get(hcad, ("", "", "", ""))
            own_up = _clean_whitespace_upper(own_raw)
            is_inst = (
                st_cls.startswith("X")
                or any(tok in own_up for tok in SCHOOL_INSTITUTIONAL_OWNER_TOKENS)
                or cf["props"].get("use_category") == "Civic / Institutional"
            )

            score = -dist * 2.2
            if dist <= 1.0:
                score += 180.0
            if exact_addr:
                score += 520.0
            elif s_core and fp_core and (s_core in fp_core or fp_core in s_core):
                score += 240.0
            if is_inst:
                score += 220.0
            score += min(160.0, math.log10(max(100.0, cf["sqft"])) * 34.0)

            if score > best_score:
                best_score = score
                best_cf = cf
                best_dist = dist

        if best_cf is None:
            continue

        hcad = best_cf["hcad_num"]
        fp_props = best_cf["props"]
        bid = str(fp_props.get("building_id") or fp_props.get("id") or hcad).strip()
        yr = int(fp_props.get("year_built") or 0)
        if not (MIN_VALID_YEAR <= yr <= MAX_VALID_CAMA_DATE_YEAR):
            continue

        item["hcad_num"] = hcad
        item["building_id"] = bid
        item["year_built"] = yr
        if fp_props.get("architect") and not item.get("architect"):
            item["architect"] = str(fp_props["architect"]).strip()
        if fp_props.get("bld_style") and not item.get("bld_style"):
            item["bld_style"] = str(fp_props["bld_style"]).strip()

        cat = str(item.get("category") or "").strip()
        sub_parts: list[str] = []
        if s_addr:
            sub_parts.append(s_addr)
        if cat and not cat.startswith("Built "):
            sub_parts.append(cat)
        else:
            sub_parts.append("Civic / Institutional")
        if item.get("architect"):
            sub_parts.append(f"Arch: {item['architect']}")
        sub_parts.append(f"Built {yr}")
        item["sublabel"] = " \u2022 ".join(sub_parts)
        linked_schools += 1

        # Phase 3B candidate vote for guarded '0 <STREET>' hydration
        if hcad not in campus_split_hcads:
            own_raw, _norm_own, st_cls, raw_acct_addr = acct_info.get(hcad, ("", "", "", ""))
            cur_fp_addr = str(fp_props.get("address") or raw_acct_addr or "")
            if is_eligible_zero_street_hydration(
                cur_fp_addr,
                s_addr,
                own_raw,
                st_cls,
                str(fp_props.get("use_category") or ""),
                best_dist,
            ):
                parcel_hydration_votes[hcad].add(s_addr)
                prev_d = parcel_hydration_min_dist.get(hcad, 999999.0)
                if best_dist < prev_d:
                    parcel_hydration_min_dist[hcad] = best_dist

    hydrated_zero_street_by_hcad: dict[str, str] = {
        hcad: next(iter(addrs))
        for hcad, addrs in parcel_hydration_votes.items()
        if len(addrs) == 1
    }
    print(
        f"   Linked {linked_schools:,} / {len(school_items):,} ({linked_schools / max(1, len(school_items)) * 100:.1f}%) "
        f"school_* entries; approved {len(hydrated_zero_street_by_hcad):,} guarded '0 <STREET>' institutional situs hydrations."
    )

    # 5. Rewrite all 5 aligned_ndjson/*.geojsonseq shards in place & accumulate countywide stats
    print("-> Applying chronology, <hcad>#<slug> splits, and guarded school situs updates across 5 shards...")
    decade_counter: Counter[str] = Counter()
    contrib_counter: Counter[str] = Counter()
    use_counter: Counter[str] = Counter()
    district_counter: Counter[str] = Counter()
    source_counter: Counter[str] = Counter()

    total_buildings = 0
    dated_count = 0
    earliest_year = 9999
    latest_year = 0

    # Track updated parcel/building metadata for buildings.geojson and address_shards synchronization
    updated_parcel_years: dict[str, int] = {}
    updated_footprints_for_core_sync: dict[str, list[tuple[float, float, dict[str, Any]]]] = defaultdict(list)
    address_shard_upserts: list[tuple[str, float, float, int, str, str, str]] = []

    for hcad, rinfo in re_resolved.items():
        updated_parcel_years[hcad] = int(rinfo["year_built"])

    for q in QUAD_NAMES:
        seq_path = aligned_dir / f"buildings_{q}.geojsonseq"
        tmp_path = aligned_dir / f"buildings_{q}.geojsonseq.tmp"
        q_mutated = 0

        with open(seq_path, "rb") as in_f, open(tmp_path, "wb") as out_f:
            for line_idx, raw in enumerate(in_f):
                line = raw.lstrip(b"\x1e").strip()
                if not line:
                    continue
                feat = orjson.loads(line)
                props = feat["properties"]
                hcad = str(props.get("hcad_num") or "").strip()
                ring = _extract_outer_ring(feat.get("geometry") or {})
                cx, cy, _sqft = _ring_centroid_and_sqft(ring)
                mutated = False

                c_upd = campus_feat_updates.get((seq_path.name, line_idx))
                if c_upd is not None:
                    props.update(c_upd)
                    mutated = True
                    address_shard_upserts.append((
                        str(props.get("address") or ""),
                        cx,
                        cy,
                        int(props["year_built"]),
                        hcad,
                        str(props.get("building_name") or props.get("landmark_name") or ""),
                        str(props.get("building_id") or props.get("id") or ""),
                    ))
                else:
                    if hcad in re_resolved:
                        rinfo = re_resolved[hcad]
                        new_yr = int(rinfo["year_built"])
                        props["year_built"] = new_yr
                        props["decade"] = compute_decade(new_yr)
                        props["year_source"] = rinfo["year_source"]
                        if rinfo.get("landmark_name") and not props.get("landmark_name"):
                            props["landmark_name"] = rinfo["landmark_name"]
                        if rinfo.get("bld_style") and not props.get("bld_style"):
                            props["bld_style"] = rinfo["bld_style"]
                        if rinfo.get("architect") and not props.get("architect"):
                            props["architect"] = rinfo["architect"]
                        mutated = True

                    auto_bid = auto_sub_id_updates.get((seq_path.name, line_idx))
                    if auto_bid:
                        props["id"] = auto_bid
                        props["building_id"] = auto_bid
                        mutated = True

                    if hcad in hydrated_zero_street_by_hcad:
                        cur_a = _clean_whitespace_upper(str(props.get("address") or ""))
                        if not cur_a or cur_a.startswith("0 "):
                            new_addr = hydrated_zero_street_by_hcad[hcad]
                            props["address"] = new_addr
                            mutated = True
                            address_shard_upserts.append((
                                new_addr,
                                cx,
                                cy,
                                int(props.get("year_built") or 0),
                                hcad,
                                str(props.get("building_name") or props.get("landmark_name") or ""),
                                str(props.get("building_id") or props.get("id") or ""),
                            ))

                if mutated:
                    q_mutated += 1
                    if hcad:
                        updated_footprints_for_core_sync[hcad].append((cx, cy, dict(props)))

                yr = int(props.get("year_built") or 0)
                src = str(props.get("year_source") or "").strip()
                if not (MIN_VALID_YEAR <= yr <= MAX_VALID_CAMA_DATE_YEAR):
                    raise ValueError(f"Completeness violation on {hcad} in {seq_path.name}: year_built={yr}")
                if src in ("hcad_permit", "hcad_deed") and yr <= 1901:
                    raise ValueError(f"Sentinel leak on {hcad} in {seq_path.name}: {src} year_built={yr}")
                if src == "campus_contiguity" and yr < 1945:
                    raise ValueError(f"Stale pre-1945 campus_contiguity leak on {hcad}: year_built={yr}")

                dec = compute_decade(yr)
                props["decade"] = dec
                total_buildings += 1
                dated_count += 1
                decade_counter[str(dec)] += 1
                if yr < earliest_year:
                    earliest_year = yr
                if yr > latest_year:
                    latest_year = yr

                source_counter[src or "hcad_cama"] += 1
                contrib = props.get("contributing") or "Outside Historic District"
                contrib_counter[contrib] += 1
                use_cat = props.get("use_category") or "Residential"
                use_counter[use_cat] += 1
                dist = props.get("historic_district")
                if dist and str(dist).lower() not in ("none", "null", "outside historic district"):
                    district_counter[str(dist)] += 1

                out_f.write(b"\x1e" + orjson.dumps(feat) + b"\n")

        tmp_path.replace(seq_path)
        print(f"   {seq_path.name}: {q_mutated:,} footprints updated")

    # Also ensure every linked school_* with a valid numbered street address is present in address_shards
    for item in school_items:
        hcad = str(item.get("hcad_num") or "").strip()
        yr = int(item.get("year_built") or 0)
        s_addr = _clean_whitespace_upper(str(item.get("address") or ""))
        if hcad and yr >= MIN_VALID_YEAR and VALID_NUMBERED_SITUS_RE.match(s_addr):
            address_shard_upserts.append((
                s_addr,
                float(item["lon"]),
                float(item["lat"]),
                yr,
                hcad,
                str(item.get("building_name") or item.get("label") or ""),
                str(item.get("building_id") or ""),
            ))

    # 6. Sync inner-core app/public/data/buildings.geojson if any updated parcel overlaps the core
    buildings_geojson_path = data_dir / "buildings.geojson"
    core_synced = 0
    if buildings_geojson_path.exists():
        core_fc = json.loads(buildings_geojson_path.read_text(encoding="utf-8"))
        for feat in core_fc.get("features", []):
            p = feat.get("properties") or {}
            hcad = str(p.get("hcad_num") or "").strip()
            if hcad not in updated_footprints_for_core_sync:
                continue
            ring = _extract_outer_ring(feat.get("geometry") or {})
            cx, cy, _ = _ring_centroid_and_sqft(ring)
            cands = updated_footprints_for_core_sync[hcad]
            best_props = min(cands, key=lambda c: _dist_m(cx, cy, c[0], c[1]))[2]
            for fld in (
                "id",
                "building_id",
                "year_built",
                "decade",
                "year_source",
                "address",
                "landmark_name",
                "building_name",
                "architect",
                "bld_style",
            ):
                if fld in best_props and best_props[fld] not in (None, ""):
                    p[fld] = best_props[fld]
            core_synced += 1
        if core_synced > 0:
            write_compact_json(buildings_geojson_path, core_fc)
            print(f"   Synchronized {core_synced:,} overlapping features in buildings.geojson.")

    # 7. Sync app/public/data/address_shards/addr_<2-digit>.json
    addr_shards_dir = data_dir / "address_shards"
    if addr_shards_dir.exists():
        upserts_by_shard: dict[str, list[tuple[str, str, float, float, int, str, str, str]]] = defaultdict(list)
        for full_addr, lon, lat, yr, hcad, bname, bid in address_shard_upserts:
            m = VALID_NUMBERED_SITUS_RE.match(_clean_whitespace_upper(full_addr))
            if not m or yr < MIN_VALID_YEAR:
                continue
            hnum, st_name = m.group(1), m.group(2)
            skey = _compute_address_shard_key(hnum)
            if skey:
                upserts_by_shard[skey].append((st_name, hnum, round(lon, 5), round(lat, 5), yr, hcad, bname, bid))

        touched_addr_shards = 0
        for shard_file in sorted(addr_shards_dir.glob("addr_*.json")):
            skey = shard_file.stem.replace("addr_", "")
            sdata: dict[str, list[list[Any]]] = json.loads(shard_file.read_text(encoding="utf-8"))
            dirty = False

            # Update stale year_built on re-resolved single-address parcels and sanitize em-dashes in row[5]
            for _st, rows in sdata.items():
                for row in rows:
                    if len(row) >= 6 and isinstance(row[5], str) and "\u2014" in row[5]:
                        row[5] = sanitize_atlas_text(row[5]).strip()
                        dirty = True
                    if len(row) >= 5:
                        rhcad = str(row[4] or "")
                        if rhcad in updated_parcel_years and rhcad not in campus_split_hcads:
                            new_yr = updated_parcel_years[rhcad]
                            if int(row[3] or 0) != new_yr:
                                row[3] = new_yr
                                dirty = True

            if skey in upserts_by_shard:
                for st_name, hnum, lon, lat, yr, hcad, bname, bid in upserts_by_shard[skey]:
                    rows = sdata.setdefault(st_name, [])
                    clean_bname = sanitize_atlas_text(bname).strip()
                    matched_existing = False
                    for row in rows:
                        r_hnum = str(row[0] or "")
                        r_hcad = str(row[4] or "") if len(row) > 4 else ""
                        r_bid = str(row[6] or "") if len(row) > 6 else ""
                        if r_hnum == hnum and ((bid and r_bid == bid) or (not r_bid and r_hcad == hcad)):
                            row[1] = lon
                            row[2] = lat
                            row[3] = yr
                            if len(row) == 5:
                                row.extend([clean_bname, bid])
                            elif len(row) == 6:
                                if clean_bname:
                                    row[5] = clean_bname
                                if bid:
                                    row.append(bid)
                            elif len(row) >= 7:
                                if clean_bname:
                                    row[5] = clean_bname
                                if bid:
                                    row[6] = bid
                            matched_existing = True
                            dirty = True
                            break
                    if not matched_existing:
                        new_row: list[Any] = [hnum, lon, lat, yr, hcad, clean_bname]
                        if bid:
                            new_row.append(bid)
                        rows.append(new_row)
                        dirty = True

            if dirty:
                write_compact_json(shard_file, sdata)
                touched_addr_shards += 1
        print(f"   Synchronized {touched_addr_shards:,} street-address shard files in address_shards/.")

    # 8. Merge & normalize curated_overrides.json and search_index.json (compact JSON, zero em-dashes)
    for bid, ov_rec in new_campus_overrides.items():
        overrides_map[bid] = ov_rec
    for hcad, (cyr, lm_name, style, arch, notes) in ADDITIONAL_CURATED_PARCEL_CHRONOLOGIES.items():
        if hcad not in overrides_map:
            _, _, _, a1 = acct_info.get(hcad, ("", "", "", ""))
            overrides_map[hcad] = normalize_override_record(
                hcad,
                {
                    "id": hcad,
                    "building_id": hcad,
                    "hcad_num": hcad,
                    "address": _clean_whitespace_upper(a1),
                    "contributing": "Outside Historic District",
                    "year_built": cyr,
                    "decade": compute_decade(cyr),
                    "use_category": "Civic / Institutional",
                    "bld_style": style,
                    "architect": arch,
                    "landmark_name": lm_name,
                    "building_name": lm_name,
                    "source_type": "Houston ISD Historic Campus & Architectural Record",
                    "source_citation": notes,
                    "verified_by": "Preservation Houston Archival Review",
                    "updated_at": "2026-10-27",
                },
            )

    for ok in list(overrides_map.keys()):
        overrides_map[ok] = normalize_override_record(ok, overrides_map[ok])

    write_compact_json(overrides_path, overrides_payload)
    ov_bytes = overrides_path.stat().st_size
    if ov_bytes >= 8_000_000:
        raise RuntimeError(f"curated_overrides.json exceeded 8 MB guardrail: {ov_bytes:,} bytes")
    print(f"   Saved compact curated_overrides.json ({len(overrides_map):,} records, {ov_bytes / (1024 * 1024):.2f} MB).")

    # Merge campus_search_additions into search_index by canonical entity key (building_id || id)
    si_by_entity: dict[str, int] = {}
    for idx, it in enumerate(search_index):
        ekey = str(it.get("building_id") or it.get("id") or "").strip()
        if ekey and not str(it.get("id") or "").startswith("school_"):
            si_by_entity[ekey] = idx

    for add_item in campus_search_additions:
        ekey = str(add_item["building_id"]).strip()
        if ekey in si_by_entity:
            search_index[si_by_entity[ekey]].update(add_item)
        else:
            si_by_entity[ekey] = len(search_index)
            search_index.append(add_item)

    # Also update any non-school search_index entries whose parcel year changed in Phase 1
    for it in search_index:
        sid = str(it.get("id") or "")
        if sid.startswith("school_") or it.get("type") == "district":
            continue
        ekey = str(it.get("building_id") or sid).strip()
        hcad = str(it.get("hcad_num") or "").strip()
        if "#" not in ekey and hcad in updated_parcel_years and hcad not in campus_split_hcads:
            nyr = updated_parcel_years[hcad]
            it["year_built"] = nyr
            if str(it.get("category") or "").startswith("Built "):
                it["category"] = f"Built {nyr}"
            sub = str(it.get("sublabel") or "")
            if "Built " in sub:
                it["sublabel"] = re.sub(r"Built \d{4}", f"Built {nyr}", sub)

    write_compact_json(search_index_path, search_index)
    print(f"   Saved compact search_index.json ({len(search_index):,} entries).")

    # 9. Recompile all 5 PMTiles Shards via Tippecanoe & update stats_summary.json + pmtiles_manifest.json
    shard_files = [f"houston_buildings_{q}.pmtiles" for q in QUAD_NAMES]
    total_pmtiles_bytes = 0
    if compile_pmtiles:
        shard_files, total_pmtiles_bytes = compile_quadrant_pmtiles(
            aligned_dir,
            data_dir,
            QUAD_NAMES,
        )
    else:
        for fn in shard_files:
            p = data_dir / fn
            if p.exists():
                total_pmtiles_bytes += p.stat().st_size

    stats_path = data_dir / "stats_summary.json"
    existing_stats = json.loads(stats_path.read_text(encoding="utf-8")) if stats_path.exists() else {}
    ordered_decades = {str(d): decade_counter.get(str(d), 0) for d in range(1830, 2030, 10)}
    ordered_decades["unknown"] = decade_counter.get("unknown", 0)

    existing_stats.update({
        "observed_footprints": existing_stats.get("observed_footprints", total_buildings),
        "total_landmarks": existing_stats.get("total_landmarks", 0),
        "total_historic_districts": existing_stats.get("total_historic_districts", 0),
        "total_thc_markers": existing_stats.get("total_thc_markers", 0),
        "total_buildings": total_buildings,
        "dated_buildings": dated_count,
        "earliest_year": earliest_year if earliest_year != 9999 else MIN_VALID_YEAR,
        "latest_year": latest_year if latest_year != 0 else MAX_VALID_CAMA_DATE_YEAR,
        "decade_counts": ordered_decades,
        "contributing_counts": dict(contrib_counter),
        "use_category_counts": dict(use_counter),
        "top_historic_districts": dict(district_counter.most_common(25)),
        "year_source_counts": dict(source_counter),
        "pmtiles_size_bytes": total_pmtiles_bytes,
        "pmtiles_shards": shard_files,
    })
    stats_path.write_text(json.dumps(existing_stats, indent=2) + "\n", encoding="utf-8")

    manifest_path = data_dir / "pmtiles_manifest.json"
    existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    existing_manifest.update({
        "version": existing_manifest.get("version", 2),
        "mode": existing_manifest.get("mode", "full_county"),
        "cache_bust": ACTIVE_CACHE_BUST,
        "total_buildings": total_buildings,
        "building_shards": shard_files,
        "core_pmtiles": existing_manifest.get("core_pmtiles", "houston_atlas.pmtiles"),
    })
    manifest_path.write_text(json.dumps(existing_manifest, indent=2) + "\n", encoding="utf-8")

    print(
        f"=== Completed Campus & School Pipeline in {time.time() - t0:.1f}s "
        f"({dated_count:,} / {total_buildings:,} dated, 0 unknown) ==="
    )
    return existing_stats


if __name__ == "__main__":
    run_campus_and_school_fixes()
