"""Builds and enriches preservation, vulnerability, and tax-credit layers for The Houston Building Atlas.

Enriches / generates:
1. overlays.json['landmarks'] (509 COH Landmarks):
   - Distinguishes Protected Landmarks (PLM, 200), Landmarks inside COH Historic Districts (LM in HD, 86),
     and 90-Day Demolition Waiver Vulnerable Landmarks outside Historic Districts (LM outside HD, 223).
   - Cleans literal 'none' / 'None' strings in historic_district and pdf_summary.
2. overlays.json['potential_historic_districts'] (60 Nov 2019 COH Surveyed Potential Historic Districts & Sites):
   - Fixes missing/typo names (FID 29 -> POST Houston / Former Barbara Jordan U.S. Post Office;
     FID 39 -> Irvington Park Historic District (Potential)).
   - Computes countywide footprint statistics, pre-1940/pre-1945 counts, Ch. 42 SMLS/SMBL block counts,
     landmark/Good Brick counts, and active permit/plat alerts.
3. overlays.json['nrhp_properties'] (238 Harris County Individual National Register listings from THC):
   - Separates 222 extant NRHP-listed properties (eligible for 20% Federal + 25% Texas Historic Tax Credits
     on qualified income-producing rehabs) from 16 demolished '(Gone)' NRHP listings.
   - Snaps extant listings to HCAD parcels / building footprints and attaches direct THC Atlas & NRHP PDF URLs.
4. overlays.json['cultural_districts'] (7 COH / Texas Commission on the Arts Cultural Districts):
   - Cleans names, attaches official URLs, and computes historic building statistics.
5. Enriches overlays.json['historic_districts'] (23) and overlays.json['nrhp_districts'] (12) with
   pre_1940_count, pre_1945_count, smls_block_count, active_permit_alert_count, label_lng, label_lat, id.
6. Enriches app/public/data/buildings.geojson with missing NRHP District / Individual NRHP footprints
   from the countywide shards and tags deterministic `protection_tier` on all designated/district buildings
   for 60 FPS rendering in the upgraded 'Historic Status' Cartographic Color Lens.
7. Updates app/public/data/search_index.json idempotently with all Potential Historic Districts,
   Cultural Districts, COH/NRHP Historic Districts, and Individual NRHP Properties (searchable by NRHP #REFNUM).
"""

from __future__ import annotations

import json
import math
import re
import statistics
import urllib.request
from pathlib import Path
from shapely.geometry import MultiPolygon, Point, Polygon, mapping, shape
from shapely.geometry.polygon import orient
from shapely.strtree import STRtree
from shapely.validation import make_valid

from atlas_pipeline.lost_houston_expansion import (
    enrich_nrhp_demolished_overlay_and_search,
    sanitize_coh_landmarks_overlay,
)
from atlas_pipeline.schema import write_compact_json

ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / "pipeline" / "cache"
SHARDS_DIR = CACHE_DIR / "aligned_ndjson"
DATA_DIR = ROOT / "app" / "public" / "data"

THC_NRHP_URL = (
    "https://services7.arcgis.com/2hv9bZMrcgZpr7i9/arcgis/rest/services/"
    "national_register_properties/FeatureServer/0/query"
    "?where=COUNTY%3D'Harris'&outFields=*&outSR=4326&f=geojson"
)

INVALID_DISTRICT_STRINGS = {
    "",
    "none",
    "null",
    "n/a",
    "na",
    "outside historic district",
    "outside city district",
}


def clean_district_str(val: object) -> str:
    s = str(val or "").strip()
    if s.lower() in INVALID_DISTRICT_STRINGS:
        return ""
    return s


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return s[:64] or "feature"


def orient_geometry(geom):
    """Ensure RFC 7946 right-hand rule (CCW exterior rings, CW interior holes)."""
    geom = make_valid(geom)
    if isinstance(geom, Polygon):
        return orient(geom, sign=1.0)
    if isinstance(geom, MultiPolygon):
        polys = [orient(p, sign=1.0) for p in geom.geoms if isinstance(p, Polygon) and not p.is_empty]
        return MultiPolygon(polys) if len(polys) > 1 else (polys[0] if polys else geom)
    if hasattr(geom, "geoms"):
        polys = []
        for g in geom.geoms:
            if isinstance(g, Polygon) and not g.is_empty:
                polys.append(orient(g, sign=1.0))
            elif isinstance(g, MultiPolygon):
                polys.extend(orient(p, sign=1.0) for p in g.geoms if not p.is_empty)
        if len(polys) > 1:
            return MultiPolygon(polys)
        if len(polys) == 1:
            return polys[0]
    return geom


def round_coords(obj, digits: int = 5):
    if isinstance(obj, float):
        return round(obj, digits)
    if isinstance(obj, (list, tuple)):
        return [round_coords(x, digits) for x in obj]
    if isinstance(obj, dict):
        return {k: round_coords(v, digits) for k, v in obj.items()}
    return obj


def normalize_addr_key(addr: str) -> str:
    if not addr:
        return ""
    s = addr.upper()
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"[^A-Z0-9\s]", " ", s)
    s = re.sub(r"\b(?:HOUSTON|PASADENA|BAYTOWN|LA PORTE|BELLAIRE|KATY|SPRING|CYPRESS|TOMBALL|TX|TEXAS)\b.*$", "", s)
    repl = {
        "STREET": "ST",
        "AVENUE": "AVE",
        "BOULEVARD": "BLVD",
        "DRIVE": "DR",
        "ROAD": "RD",
        "LANE": "LN",
        "COURT": "CT",
        "PLACE": "PL",
        "PARKWAY": "PKWY",
        "FREEWAY": "FWY",
        "HIGHWAY": "HWY",
        "NORTH": "N",
        "SOUTH": "S",
        "EAST": "E",
        "WEST": "W",
        "FIRST": "1ST",
        "SECOND": "2ND",
        "THIRD": "3RD",
        "FOURTH": "4TH",
        "FIFTH": "5TH",
        "SIXTH": "6TH",
        "SEVENTH": "7TH",
        "EIGHTH": "8TH",
        "NINTH": "9TH",
        "TENTH": "10TH",
        "ELEVENTH": "11TH",
        "TWELFTH": "12TH",
        "SEVENTEENTH": "17TH",
        "EIGHTEENTH": "18TH",
        "NINETEENTH": "19TH",
        "TWENTIETH": "20TH",
    }
    tokens = [repl.get(tok, tok) for tok in s.split()]
    if not tokens or not tokens[0].isdigit():
        return ""
    return " ".join(tokens[:4])


def format_nrhp_display_name(raw_resname: str) -> tuple[str, list[str], bool]:
    """Convert THC inverted NRHP names like 'Palmer, Edward Albert, Memorial Chapel' into natural display names."""
    raw = str(raw_resname or "").strip()
    is_gone = bool(re.search(r"\((?:gone|demolished|delisted)", raw, flags=re.IGNORECASE))
    clean_base = re.sub(r"\s*\((?:gone|demolished|delisted)[^)]*\)\s*", "", raw, flags=re.IGNORECASE).strip(" ,")
    alt_names: list[str] = []
    if raw and raw != clean_base:
        alt_names.append(raw)

    display = clean_base
    # Invert 'Last, First, Suffix' e.g. 'Cohn, Arthur B., House' -> 'Arthur B. Cohn House'
    parts = [p.strip() for p in clean_base.split(",")]
    if len(parts) == 3 and parts[0] and parts[1] and parts[2]:
        candidate = f"{parts[1]} {parts[0]} {parts[2]}".replace("  ", " ").strip()
        alt_names.append(clean_base)
        display = candidate
    elif len(parts) == 2 and parts[0] and parts[1]:
        # e.g. 'Simon,urkan' or 'battelstein's, Building'
        if any(
            parts[1].lower().startswith(w)
            for w in ("house", "building", "mansion", "church", "school", "depot", "hotel", "store", "apartments")
        ):
            candidate = f"{parts[0]} {parts[1]}".strip()
            display = candidate
        else:
            candidate = f"{parts[1]} {parts[0]}".strip()
            alt_names.append(clean_base)
            display = candidate

    if is_gone:
        display_with_status = f"{display} (Demolished / Gone)"
    else:
        display_with_status = display

    dedup_alts = []
    seen = {display_with_status.lower(), display.lower()}
    for a in alt_names:
        if a and a.lower() not in seen:
            seen.add(a.lower())
            dedup_alts.append(a)
    if is_gone and display.lower() != display_with_status.lower():
        dedup_alts.insert(0, display)
    return display_with_status, dedup_alts, is_gone


POTENTIAL_DISTRICT_CURATED_FIXES: dict[int, dict] = {
    1: {
        "name": "Emancipation Avenue Main Street Historic District",
        "alt_names": ["Dowling Street Commercial Corridor", "Third Ward Main Street Corridor"],
        "survey_subtype": "potential_district",
    },
    2: {
        "name": "Brooke Smith Potential Historic District",
        "alt_names": ["Brooke Smith", "Montie Beach / Brooke Smith"],
        "survey_subtype": "potential_district",
    },
    26: {
        "name": "Hermann Park Historic Landscape District",
        "alt_names": ["Hermann Park"],
        "survey_subtype": "potential_district",
    },
    29: {
        "name": "POST Houston / Former Barbara Jordan U.S. Post Office",
        "alt_names": ["401 Franklin St", "Barbara Jordan Post Office", "Grand Central Station Site"],
        "survey_subtype": "eligible_site",
    },
    34: {
        "name": "W-K-M Company, Inc. Historic District",
        "alt_names": ["W-K-M Valve Plant Historic District"],
        "survey_subtype": "potential_district",
    },
    39: {
        "name": "Irvington Park Historic District (Potential)",
        "alt_names": ["Irvington Park", "Irvinton Park Historic District (Potential)"],
        "survey_subtype": "potential_district",
    },
    40: {
        "name": "Lindale Park Historic District (Potential)",
        "alt_names": ["Lindale Park"],
        "survey_subtype": "potential_district",
    },
    43: {
        "name": "Germantown Historic District (Surveyed 2019; Designated COH HD)",
        "alt_names": ["Germantown Historic District", "Germantown"],
        "survey_subtype": "potential_district",
        "already_designated_coh_hd": True,
    },
    44: {
        "name": "Former Albert Sidney Johnston Jr. High School",
        "alt_names": ["Former Albert Sidney Johnson Jr. High School"],
        "survey_subtype": "eligible_site",
    },
    51: {
        "name": "Main Street Corridor (Potential Historic District)",
        "alt_names": ["Main Street Corridor"],
        "survey_subtype": "potential_district",
    },
    60: {
        "name": "St. Paul's United Methodist Church",
        "alt_names": ["St. Paul's Methodist Episcopal Church", "5501 Main St"],
        "survey_subtype": "eligible_site",
    },
}


def ensure_thc_nrhp_cached() -> dict:
    cache_path = CACHE_DIR / "thc_nrhp_properties_harris.geojson"
    if not cache_path.exists():
        req = urllib.request.Request(THC_NRHP_URL, headers={"User-Agent": "HoustonBuildingAtlas/2.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            cache_path.write_text(resp.read().decode("utf-8"))
    return json.loads(cache_path.read_text())


def main() -> None:
    overlays_path = DATA_DIR / "overlays.json"
    buildings_path = DATA_DIR / "buildings.geojson"
    search_index_path = DATA_DIR / "search_index.json"
    permit_alerts_path = DATA_DIR / "active_permit_alerts.json"
    plat_monitor_path = DATA_DIR / "plat_agenda_monitor.json"

    overlays = json.loads(overlays_path.read_text())
    buildings_fc = json.loads(buildings_path.read_text())
    permit_alerts = json.loads(permit_alerts_path.read_text()) if permit_alerts_path.exists() else {"alerts": []}
    plat_monitor = json.loads(plat_monitor_path.read_text()) if plat_monitor_path.exists() else {"items": []}

    # Prepare spatial trees for existing boundaries
    coh_hd_features = overlays["historic_districts"]["features"]
    coh_hd_geoms = [orient_geometry(shape(f["geometry"])) for f in coh_hd_features]
    coh_hd_tree = STRtree(coh_hd_geoms)

    nrhp_dist_features = overlays["nrhp_districts"]["features"]
    nrhp_dist_geoms = [orient_geometry(shape(f["geometry"])) for f in nrhp_dist_features]
    nrhp_dist_tree = STRtree(nrhp_dist_geoms)

    heritage_features = overlays["heritage_districts"]["features"]
    heritage_geoms = [orient_geometry(shape(f["geometry"])) for f in heritage_features]

    sn_features = overlays.get("super_neighborhoods", {}).get("features", [])
    sn_geoms = [make_valid(shape(f["geometry"])) for f in sn_features]
    sn_tree = STRtree(sn_geoms) if sn_geoms else None

    ward_1903_features = [
        f
        for f in overlays.get("historic_wards", {}).get("features", [])
        if int(f.get("properties", {}).get("era") or 0) == 1903
    ]
    ward_1903_geoms = [make_valid(shape(f["geometry"])) for f in ward_1903_features]
    ward_1903_tree = STRtree(ward_1903_geoms) if ward_1903_geoms else None

    prot_features = overlays.get("land_use_protections", {}).get("features", [])
    prot_geoms = [make_valid(shape(f["geometry"])) for f in prot_features]
    prot_tree = STRtree(prot_geoms) if prot_geoms else None

    gb_features = overlays.get("good_brick_awards", {}).get("features", [])
    gb_pts = [shape(f["geometry"]) for f in gb_features]

    alert_pts = []
    for a in permit_alerts.get("alerts", []):
        lng, lat = a.get("lng") or a.get("lon"), a.get("lat")
        if lng is not None and lat is not None:
            alert_pts.append(Point(float(lng), float(lat)))

    plat_pts = []
    for it in plat_monitor.get("items", []):
        lng, lat = it.get("lng") or it.get("lon"), it.get("lat")
        if lng is not None and lat is not None:
            plat_pts.append(Point(float(lng), float(lat)))

    # -------------------------------------------------------------------------
    # 1. Enrich COH Landmarks (509) with PLM vs LM-in-HD vs 90-Day Waiver LM
    # -------------------------------------------------------------------------
    plm_hcads: set[str] = set()
    lm_in_hd_hcads: set[str] = set()
    lm_waiver_hcads: set[str] = set()
    landmark_pts: list[Point] = []
    landmark_tiers: list[str] = []

    # Sanitize COH landmark HCAD fields & demolished flags before building tiers (DF-3)
    sanitize_coh_landmarks_overlay(overlays["landmarks"])

    tier_counts = {"plm": 0, "lm_in_hd": 0, "lm_waiver_vulnerable": 0, "lm_demolished": 0}
    for idx, feat in enumerate(overlays["landmarks"]["features"]):
        p = feat["properties"]
        pt = shape(feat["geometry"])
        landmark_pts.append(pt)
        if not p.get("id"):
            p["id"] = f"coh_lm_{idx + 1:03d}"

        # Clean literal 'none' / 'None' in historic_district and pdf_summary
        raw_hd = clean_district_str(p.get("historic_district"))
        matched_hd_name = ""
        for hi in coh_hd_tree.query(pt):
            if coh_hd_geoms[hi].contains(pt):
                matched_hd_name = coh_hd_features[hi]["properties"]["name"]
                break

        p["historic_district"] = matched_hd_name or raw_hd
        p["in_coh_historic_district"] = bool(matched_hd_name)

        if p.get("pdf_summary"):
            cleaned_sum = re.sub(
                r"\s+within the (?:none|None|null) Historic District",
                "",
                str(p["pdf_summary"]),
            )
            p["pdf_summary"] = cleaned_sum

        is_plm = str(p.get("designation") or "").strip() == "Protected Landmark"
        p["is_protected_landmark"] = is_plm

        if p.get("demolished"):
            tier = "lm_demolished"
            rule_short = "Demolished / Destroyed Former Landmark (Recorded in Lost Houston Archive)"
            city_tax_eligible = False
        elif is_plm:
            tier = "plm"
            rule_short = "Ch. 33 Protected Landmark (HAHC COA Required; Permanent Demolition Ban)"
            city_tax_eligible = True
        elif matched_hd_name:
            tier = "lm_in_hd"
            rule_short = f"Landmark Protected by {matched_hd_name} COA Rules (Ch. 33)"
            city_tax_eligible = True
        else:
            tier = "lm_waiver_vulnerable"
            rule_short = "Non-Protected Landmark Outside Historic District (Subject to 90-Day Demolition Waiver)"
            city_tax_eligible = True

        p["protection_tier"] = tier
        p["demolition_rule_short"] = rule_short
        p["city_tax_exemption_eligible"] = city_tax_eligible
        tier_counts[tier] += 1
        landmark_tiers.append(tier)

        hcad = str(p.get("hcad_num") or "").strip()
        if hcad and re.match(r"^\d{13}$", hcad) and not p.get("demolished"):
            if tier == "plm":
                plm_hcads.add(hcad)
            elif tier == "lm_in_hd":
                lm_in_hd_hcads.add(hcad)
            elif tier == "lm_waiver_vulnerable":
                lm_waiver_hcads.add(hcad)

    print(f"Enriched 509 COH Landmarks -> {tier_counts}")

    # -------------------------------------------------------------------------
    # 2. Load Potential Historic Districts (60) & Cultural Districts (7)
    # -------------------------------------------------------------------------
    pot_raw = json.loads((CACHE_DIR / "coh_potential_historic_districts.geojson").read_text())
    pot_features: list[dict] = []
    pot_geoms: list = []

    for feat in pot_raw.get("features", []):
        if not feat.get("geometry"):
            continue
        rp = feat.get("properties") or {}
        fid = int(rp.get("FID") or (len(pot_features) + 1))
        raw_name = str(rp.get("NAME") or "").strip().rstrip(",").replace("’", "'")
        fix = POTENTIAL_DISTRICT_CURATED_FIXES.get(fid, {})
        name = fix.get("name") or raw_name or f"Surveyed Historic Resource #{fid}"
        alt_names = list(fix.get("alt_names", []))
        if raw_name and raw_name.lower() != name.lower() and raw_name not in alt_names:
            alt_names.append(raw_name)

        geom = orient_geometry(shape(feat["geometry"]).simplify(0.00004, preserve_topology=True))
        if geom.is_empty:
            continue
        rep = geom.representative_point()
        area_deg2 = float(geom.area)

        subtype = fix.get("survey_subtype")
        if not subtype:
            if (
                any(k in name.lower() for k in ("district", "corridor", "canal", "ditch", "park"))
                or area_deg2 >= 0.000012
            ):
                subtype = "potential_district"
            else:
                subtype = "eligible_site"

        already_coh_hd = bool(fix.get("already_designated_coh_hd", False))
        sn_name = ""
        if sn_tree is not None:
            for sni in sn_tree.query(rep):
                if sn_geoms[sni].contains(rep):
                    sn_name = sn_features[sni]["properties"]["name"]
                    break
        ward_name = ""
        if ward_1903_tree is not None:
            for wi in ward_1903_tree.query(rep):
                if ward_1903_geoms[wi].contains(rep):
                    ward_name = ward_1903_features[wi]["properties"]["name"]
                    break

        pot_geoms.append(geom)
        pot_features.append(
            {
                "type": "Feature",
                "properties": {
                    "id": f"pot_hd_{fid:02d}_{slugify(name)[:36]}",
                    "fid": fid,
                    "name": name,
                    "alt_names": alt_names,
                    "survey_subtype": subtype,
                    "type": (
                        "Designated COH Historic District (2019 Survey)"
                        if already_coh_hd
                        else "Surveyed Potential Historic District"
                        if subtype == "potential_district"
                        else "Surveyed NRHP-Eligible Historic Site / Structure"
                    ),
                    "type_label": (
                        "Surveyed Potential District (Now Designated COH HD)"
                        if already_coh_hd
                        else "Surveyed Potential Historic District"
                        if subtype == "potential_district"
                        else "Surveyed Eligible Landmark / Site"
                    ),
                    "already_designated_coh_hd": already_coh_hd,
                    "super_neighborhood": sn_name,
                    "historic_ward": ward_name,
                    "source": "City of Houston Planning & Development Historic Survey (Nov. 2019)",
                    "label_lng": round(rep.x, 5),
                    "label_lat": round(rep.y, 5),
                    "area_deg2": round(area_deg2, 9),
                },
                "geometry": round_coords(mapping(geom), 5),
            }
        )

    cult_raw = json.loads((CACHE_DIR / "coh_cultural_districts.geojson").read_text())
    cult_features: list[dict] = []
    cult_geoms: list = []

    for idx, feat in enumerate(cult_raw.get("features", [])):
        if not feat.get("geometry"):
            continue
        rp = feat.get("properties") or {}
        name = str(rp.get("NAME") or "").strip()
        if not name:
            continue
        website = str(rp.get("Website") or "").strip()
        if website.rstrip("/") == "https://projectrowhouses.org/cultural-district":
            website = "https://projectrowhouses.org/"
        geom = orient_geometry(shape(feat["geometry"]).simplify(0.00005, preserve_topology=True))
        if geom.is_empty:
            continue
        rep = geom.representative_point()
        area_deg2 = float(geom.area)

        sn_name = ""
        if sn_tree is not None:
            for sni in sn_tree.query(rep):
                if sn_geoms[sni].contains(rep):
                    sn_name = sn_features[sni]["properties"]["name"]
                    break
        ward_name = ""
        if ward_1903_tree is not None:
            for wi in ward_1903_tree.query(rep):
                if ward_1903_geoms[wi].contains(rep):
                    ward_name = ward_1903_features[wi]["properties"]["name"]
                    break

        cult_geoms.append(geom)
        cult_features.append(
            {
                "type": "Feature",
                "properties": {
                    "id": f"cultural_dist_{idx + 1:02d}_{slugify(name)[:36]}",
                    "name": name,
                    "type": "State-Recognized Cultural Arts District",
                    "type_label": "Cultural Arts District (COH / TCA)",
                    "website": website,
                    "super_neighborhood": sn_name,
                    "historic_ward": ward_name,
                    "source": "City of Houston Mayor's Office of Cultural Affairs & Texas Commission on the Arts",
                    "description": (
                        f"{name} is an official Cultural District recognized by the City of Houston and the "
                        "Texas Commission on the Arts (TCA) to support cultural heritage, arts institutions, "
                        "creative placemaking, and historic preservation grants. Cultural District status is "
                        "promotional and grant-qualifying; it does not impose Chapter 33 demolition review."
                    ),
                    "label_lng": round(rep.x, 5),
                    "label_lat": round(rep.y, 5),
                    "area_deg2": round(area_deg2, 9),
                },
                "geometry": round_coords(mapping(geom), 5),
            }
        )

    # -------------------------------------------------------------------------
    # 3. Load THC Harris County Individual NRHP Listings (238)
    # -------------------------------------------------------------------------
    nrhp_raw = ensure_thc_nrhp_cached()
    nrhp_prop_features: list[dict] = []
    nrhp_extant_pts: list[Point] = []
    nrhp_extant_indices: list[int] = []
    nrhp_by_addr_key: dict[str, list[int]] = {}

    for idx, feat in enumerate(nrhp_raw.get("features", [])):
        if not feat.get("geometry"):
            continue
        rp = feat.get("properties") or {}
        refnum_raw = str(rp.get("REFNUM") or "").strip()
        refnum = refnum_raw.zfill(8) if refnum_raw.isdigit() and len(refnum_raw) == 7 else refnum_raw
        atlas_num = str(rp.get("AtlasNum") or "").strip()
        if not atlas_num and len(refnum) == 8 and refnum.isdigit():
            atlas_num = f"20{refnum}"
        raw_resname = str(rp.get("RESNAME") or "").strip()
        display_name, alt_names, is_gone = format_nrhp_display_name(raw_resname)
        multiname = str(rp.get("MULTINAME") or "").strip()
        raw_date = str(rp.get("LISTED_DAT") or "").strip()
        listed_date = (
            f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
            if len(raw_date) == 8 and raw_date.isdigit()
            else raw_date
        )
        listed_year = int(raw_date[:4]) if len(raw_date) >= 4 and raw_date[:4].isdigit() else 0
        address = str(rp.get("ADDRESS") or "").strip()
        city = str(rp.get("CITY") or "Houston").strip()

        raw_pt = shape(feat["geometry"])
        pt = raw_pt if isinstance(raw_pt, Point) else raw_pt.representative_point()
        lng, lat = round(pt.x, 6), round(pt.y, 6)

        matched_coh_hd = ""
        for hi in coh_hd_tree.query(pt):
            if coh_hd_geoms[hi].contains(pt):
                matched_coh_hd = coh_hd_features[hi]["properties"]["name"]
                break

        matched_nrhp_dist = ""
        for ni in nrhp_dist_tree.query(pt):
            if nrhp_dist_geoms[ni].contains(pt):
                matched_nrhp_dist = nrhp_dist_features[ni]["properties"]["name"]
                break

        sn_name = ""
        if sn_tree is not None:
            for sni in sn_tree.query(pt):
                if sn_geoms[sni].contains(pt):
                    sn_name = sn_features[sni]["properties"]["name"]
                    break

        thc_atlas_url = f"https://atlas.thc.texas.gov/Details/{atlas_num}" if atlas_num else ""
        if refnum in {"83004671", "85002810", "85002815"}:
            nomination_pdf_url = f"https://npgallery.nps.gov/AssetDetail/NRIS/{refnum}"
        else:
            nomination_pdf_url = f"https://atlas.thc.texas.gov/NR/pdfs/{refnum}/{refnum}.pdf" if refnum else ""

        entry_idx = len(nrhp_prop_features)
        nrhp_prop_features.append(
            {
                "type": "Feature",
                "properties": {
                    "id": f"nrhp_{refnum_raw or (idx + 1)}",
                    "refnum": refnum,
                    "atlas_num": atlas_num,
                    "name": display_name,
                    "raw_resname": raw_resname,
                    "alt_names": alt_names,
                    "multiname": multiname,
                    "listed_date": listed_date,
                    "listed_year": listed_year,
                    "address": address,
                    "city": city,
                    "is_demolished_nrhp": is_gone,
                    "historic_district": matched_coh_hd,
                    "nrhp_district": matched_nrhp_dist,
                    "super_neighborhood": sn_name,
                    "thc_atlas_url": thc_atlas_url,
                    "nomination_pdf_url": nomination_pdf_url,
                    "tax_credit_eligible": not is_gone,
                    "hcad_num": "",
                    "building_id": "",
                    "year_built": 0,
                },
                "geometry": {"type": "Point", "coordinates": [lng, lat]},
            }
        )

        if not is_gone:
            nrhp_extant_pts.append(pt)
            nrhp_extant_indices.append(entry_idx)
            # Support dual addresses like '6221 and 6265 Main St.'
            akey = normalize_addr_key(address)
            if akey:
                nrhp_by_addr_key.setdefault(akey, []).append(entry_idx)
            m_dual = re.match(r"^(\d+)\s+(?:and|&|-)\s+(\d+)\s+(.+)$", address, flags=re.IGNORECASE)
            if m_dual:
                for num_part in (m_dual.group(1), m_dual.group(2)):
                    ak2 = normalize_addr_key(f"{num_part} {m_dual.group(3)}")
                    if ak2:
                        nrhp_by_addr_key.setdefault(ak2, []).append(entry_idx)

    # -------------------------------------------------------------------------
    # 4. Stream All 5 Countywide .geojsonseq Shards Once (~8-10s) to:
    #    - Compute exact stats for Potential HDs (60), Cultural Districts (7),
    #      COH Historic Districts (23), and NRHP Districts (12)
    #    - Match extant Individual NRHP Properties (222) to HCAD / building footprints
    #    - Extract missing footprints inside NRHP Districts (e.g. Idylwood, Near Northside,
    #      Independence Heights) + Individual NRHP buildings to add to buildings.geojson!
    # -------------------------------------------------------------------------
    all_poly_groups = (
        [("pot", i, pot_geoms[i]) for i in range(len(pot_geoms))]
        + [("cult", i, cult_geoms[i]) for i in range(len(cult_geoms))]
        + [("coh_hd", i, coh_hd_geoms[i]) for i in range(len(coh_hd_geoms))]
        + [("nrhp_dist", i, nrhp_dist_geoms[i]) for i in range(len(nrhp_dist_geoms))]
    )
    combined_poly_geoms = [g for _, _, g in all_poly_groups]
    combined_poly_tree = STRtree(combined_poly_geoms)

    poly_stats: dict[tuple[str, int], dict] = {
        (kind, idx): {"count": 0, "years": [], "pre1940": 0, "pre1945": 0, "pre1960": 0}
        for kind, idx, _ in all_poly_groups
    }

    # Buffer extant NRHP points by ~0.00042 deg (~45m) to quickly find nearby building footprints
    nrhp_buffered_geoms = [pt.buffer(0.00042) for pt in nrhp_extant_pts]
    nrhp_pt_tree = STRtree(nrhp_buffered_geoms) if nrhp_buffered_geoms else None
    nrhp_best_match: dict[int, tuple[float, dict]] = {}  # entry_idx -> (score_lower_better, feature)

    existing_bld_ids = {str(f["properties"].get("id") or "") for f in buildings_fc["features"]}
    existing_bld_hcads = {
        str(f["properties"].get("hcad_num") or "").strip()
        for f in buildings_fc["features"]
        if str(f["properties"].get("hcad_num") or "").strip()
    }
    extra_nrhp_district_footprints: dict[str, dict] = {}

    shard_files = sorted(SHARDS_DIR.glob("buildings_*.geojsonseq"))
    total_scanned = 0
    for sf in shard_files:
        with open(sf) as f:
            for line in f:
                raw = line.lstrip("\x1e").strip()
                if not raw:
                    continue
                feat = json.loads(raw)
                total_scanned += 1
                geom_dict = feat.get("geometry")
                if not geom_dict:
                    continue
                p = feat.get("properties") or {}
                # Fast bbox centroid approximation for 1.51M polygon point-in-polygon tests
                coords = geom_dict.get("coordinates")
                if not coords:
                    continue
                ring = coords[0] if geom_dict.get("type") == "Polygon" else (coords[0][0] if coords[0] else None)
                if not ring:
                    continue
                cx = sum(pt[0] for pt in ring) / len(ring)
                cy = sum(pt[1] for pt in ring) / len(ring)
                pt = Point(cx, cy)
                yr = int(p.get("year_built") or 0)

                # Polygon stats + capture missing NRHP District footprints
                cand_polys = combined_poly_tree.query(pt)
                for c_idx in cand_polys:
                    kind, g_idx, poly_geom = all_poly_groups[c_idx]
                    if poly_geom.contains(pt):
                        st = poly_stats[(kind, g_idx)]
                        st["count"] += 1
                        if yr >= 1836:
                            st["years"].append(yr)
                            if yr < 1940:
                                st["pre1940"] += 1
                            if yr <= 1945:
                                st["pre1945"] += 1
                            if yr < 1960:
                                st["pre1960"] += 1
                        if kind == "nrhp_dist":
                            fid_str = str(p.get("id") or "")
                            hcad_str = str(p.get("hcad_num") or "").strip()
                            if (
                                fid_str
                                and fid_str not in existing_bld_ids
                                and (not hcad_str or hcad_str not in existing_bld_hcads)
                                and fid_str not in extra_nrhp_district_footprints
                            ):
                                fp_copy = json.loads(raw)
                                fp_copy["properties"]["nrhp_district"] = nrhp_dist_features[g_idx]["properties"]["name"]
                                extra_nrhp_district_footprints[fid_str] = fp_copy

                # Match extant Individual NRHP Properties by address or spatial containment/proximity
                addr_str = str(p.get("address") or "").strip()
                akey = normalize_addr_key(addr_str)
                matched_nrhp_entries: set[int] = set()
                if akey and akey in nrhp_by_addr_key:
                    for e_idx in nrhp_by_addr_key[akey]:
                        n_coords = nrhp_prop_features[e_idx]["geometry"]["coordinates"]
                        dist_deg = math.hypot(cx - n_coords[0], cy - n_coords[1])
                        if dist_deg <= 0.0045:  # within ~480m for same street address
                            matched_nrhp_entries.add(e_idx)

                if nrhp_pt_tree is not None:
                    for b_idx in nrhp_pt_tree.query(pt):
                        matched_nrhp_entries.add(nrhp_extant_indices[b_idx])

                if matched_nrhp_entries:
                    b_geom = shape(geom_dict)
                    for e_idx in matched_nrhp_entries:
                        n_coords = nrhp_prop_features[e_idx]["geometry"]["coordinates"]
                        n_pt = Point(n_coords[0], n_coords[1])
                        n_akey = normalize_addr_key(nrhp_prop_features[e_idx]["properties"]["address"])
                        exact_addr = bool(akey and n_akey and akey == n_akey)
                        contains_pt = b_geom.contains(n_pt)
                        dist_deg = b_geom.distance(n_pt)
                        if not exact_addr and dist_deg > 0.00038:
                            continue
                        # Lower score is better: exact address (0) > polygon contains point (0.00005) > distance
                        # Slight preference for older historic main structure over newer rear garage on same parcel
                        age_bonus = -0.00001 if (1836 <= yr <= 1978) else 0.0
                        score = (
                            (0.0 if exact_addr else 0.0002)
                            + (0.0 if contains_pt else dist_deg)
                            + age_bonus
                        )
                        prev = nrhp_best_match.get(e_idx)
                        if prev is None or score < prev[0]:
                            nrhp_best_match[e_idx] = (score, json.loads(raw))

    print(
        f"Scanned {total_scanned:,} countywide shard footprints; "
        f"matched {len(nrhp_best_match)}/{len(nrhp_extant_indices)} extant NRHP properties; "
        f"found {len(extra_nrhp_district_footprints):,} additional NRHP District footprints."
    )

    # Also check existing buildings.geojson for any even-closer/curated NRHP property matches
    for b_feat in buildings_fc["features"]:
        bp = b_feat.get("properties") or {}
        b_geom = shape(b_feat["geometry"])
        rep = b_geom.representative_point()
        akey = normalize_addr_key(str(bp.get("address") or ""))
        yr = int(bp.get("year_built") or 0)
        cand_e: set[int] = set()
        if akey and akey in nrhp_by_addr_key:
            for e_idx in nrhp_by_addr_key[akey]:
                n_coords = nrhp_prop_features[e_idx]["geometry"]["coordinates"]
                if math.hypot(rep.x - n_coords[0], rep.y - n_coords[1]) <= 0.0045:
                    cand_e.add(e_idx)
        if nrhp_pt_tree is not None:
            for b_idx in nrhp_pt_tree.query(rep):
                cand_e.add(nrhp_extant_indices[b_idx])
        for e_idx in cand_e:
            n_coords = nrhp_prop_features[e_idx]["geometry"]["coordinates"]
            n_pt = Point(n_coords[0], n_coords[1])
            n_akey = normalize_addr_key(nrhp_prop_features[e_idx]["properties"]["address"])
            exact_addr = bool(akey and n_akey and akey == n_akey)
            contains_pt = b_geom.contains(n_pt)
            dist_deg = b_geom.distance(n_pt)
            if not exact_addr and dist_deg > 0.00038:
                continue
            age_bonus = -0.00002 if (1836 <= yr <= 1978) else 0.0
            score = (0.0 if exact_addr else 0.0002) + (0.0 if contains_pt else dist_deg) + age_bonus - 0.000005
            prev = nrhp_best_match.get(e_idx)
            if prev is None or score < prev[0]:
                nrhp_best_match[e_idx] = (score, b_feat)

    # Apply matched HCAD / building_id / COH landmark cross-links onto nrhp_properties
    nrhp_by_hcad: dict[str, dict] = {}
    nrhp_by_bld_id: dict[str, dict] = {}
    for e_idx, (_, matched_b_feat) in nrhp_best_match.items():
        np = nrhp_prop_features[e_idx]["properties"]
        bp = matched_b_feat.get("properties") or {}
        hcad = str(bp.get("hcad_num") or "").strip()
        bid = str(bp.get("id") or bp.get("building_id") or "").strip()
        byr = int(bp.get("year_built") or 0)
        if hcad:
            np["hcad_num"] = hcad
            nrhp_by_hcad[hcad] = np
        if bid:
            np["building_id"] = bid
            nrhp_by_bld_id[bid] = np
        if byr >= 1836:
            np["year_built"] = byr
        # Ensure matched individual NRHP footprint is in buildings.geojson if not already
        if (
            bid
            and bid not in existing_bld_ids
            and (not hcad or hcad not in existing_bld_hcads)
            and bid not in extra_nrhp_district_footprints
        ):
            extra_nrhp_district_footprints[bid] = matched_b_feat

        # Attach local Ch. 33 status summary onto each NRHP property
        if hcad and hcad in plm_hcads:
            np["local_protection_status"] = "Also a COH Protected Landmark (PLM) - Ch. 33 Demolition Ban Applies"
        elif np.get("historic_district") or (hcad and hcad in lm_in_hd_hcads):
            np["local_protection_status"] = (
                f"Inside {np.get('historic_district') or 'COH Historic District'} - Ch. 33 COA Rules Apply"
            )
        elif hcad and hcad in lm_waiver_hcads:
            np["local_protection_status"] = (
                "Also a Non-Protected COH Landmark (LM) - Subject to 90-Day Demolition Waiver"
            )
        else:
            np["local_protection_status"] = (
                "Federal NRHP Listing Only - Eligible for 45% Combined Tax Credits; No City Demolition Ban"
            )

    for feat in nrhp_prop_features:
        np = feat["properties"]
        if "local_protection_status" not in np:
            if np["is_demolished_nrhp"]:
                np["local_protection_status"] = (
                    "Demolished After NRHP Listing (Illustrates That NRHP Listing Does Not Block Private Demolition)"
                )
            elif np.get("historic_district"):
                np["local_protection_status"] = f"Inside {np['historic_district']} - Ch. 33 COA Rules Apply"
            else:
                np["local_protection_status"] = (
                    "Federal NRHP Listing Only - Eligible for 45% Combined Tax Credits; No City Demolition Ban"
                )

    # -------------------------------------------------------------------------
    # 5. Populate Polygon Stats & Narratives on Potential HDs, Cultural Districts,
    #    COH Historic Districts, and NRHP Districts
    # -------------------------------------------------------------------------
    def attach_overlay_counts(kind: str, idx: int, poly_geom, props: dict) -> None:
        st = poly_stats[(kind, idx)]
        yrs = st["years"]
        props["building_count"] = st["count"]
        props["pre_1940_count"] = st["pre1940"]
        props["pre_1945_count"] = st["pre1945"]
        props["pre_1960_count"] = st["pre1960"]
        props["earliest_year"] = min(yrs) if yrs else int(props.get("earliest_year") or 0)
        props["median_year"] = int(round(statistics.median(yrs))) if yrs else int(props.get("median_year") or 0)

        lm_cnt = 0
        plm_cnt = 0
        vuln_lm_cnt = 0
        for l_pt, l_tier in zip(landmark_pts, landmark_tiers):
            if l_tier != "lm_demolished" and poly_geom.contains(l_pt):
                lm_cnt += 1
                if l_tier == "plm":
                    plm_cnt += 1
                elif l_tier == "lm_waiver_vulnerable":
                    vuln_lm_cnt += 1
        props["landmark_count"] = lm_cnt
        props["plm_count"] = plm_cnt
        props["vulnerable_lm_count"] = vuln_lm_cnt
        props["good_brick_count"] = sum(1 for gpt in gb_pts if poly_geom.contains(gpt))
        props["active_permit_alert_count"] = sum(1 for apt in alert_pts if poly_geom.contains(apt))
        props["active_plat_alert_count"] = sum(1 for ppt in plat_pts if poly_geom.contains(ppt))

        smls_cnt = 0
        smbl_cnt = 0
        cons_cnt = 0
        if prot_tree is not None:
            for pi in prot_tree.query(poly_geom):
                if prot_geoms[pi].intersects(poly_geom):
                    inter_area = prot_geoms[pi].intersection(poly_geom).area
                    if inter_area > 1e-9:
                        ptype = str(prot_features[pi]["properties"].get("protection_type") or "smls")
                        if ptype == "smbl":
                            smbl_cnt += 1
                        elif ptype == "conservation":
                            cons_cnt += 1
                        else:
                            smls_cnt += 1
        props["smls_block_count"] = smls_cnt
        props["smbl_block_count"] = smbl_cnt
        props["conservation_dist_count"] = cons_cnt

    for i, feat in enumerate(pot_features):
        p = feat["properties"]
        attach_overlay_counts("pot", i, pot_geoms[i], p)
        if p.get("already_designated_coh_hd"):
            p["description"] = (
                f"{p['name']} was identified in the City of Houston's November 2019 Potential Historic Districts "
                "survey and subsequently achieved formal designation as a City of Houston Historic District "
                "protected under Chapter 33 Certificate of Appropriateness (COA) review."
            )
        elif p["survey_subtype"] == "potential_district":
            armor_note = (
                f" Contains {p['smls_block_count']} Chapter 42 Special Minimum Lot Size (SMLS) and "
                f"{p['smbl_block_count']} Special Minimum Building Line (SMBL) ordinance blocks."
                if (p["smls_block_count"] or p["smbl_block_count"])
                else " Has no Chapter 42 Special Minimum Lot Size (SMLS) block ordinances currently in place."
            )
            p["description"] = (
                f"Identified by the City of Houston Planning & Development Department (Nov. 2019 Survey) as a "
                f"Surveyed Potential Historic District ({p['building_count']:,} structures; {p['pre_1945_count']:,} "
                f"built through 1945). Surveyed status documents historic cohesion for potential National Register "
                f"tax-credit nomination or future landowner-initiated Chapter 33 designation, but provides NO "
                f"current municipal demolition ban.{armor_note}"
            )
        else:
            p["description"] = (
                f"{p['name']} was documented in the City of Houston's November 2019 Historic Preservation Survey "
                "as an individual historic structure, campus, or engineering landmark potentially eligible for "
                "National Register of Historic Places (NRHP) listing and historic rehabilitation tax credits."
            )

    for i, feat in enumerate(cult_features):
        attach_overlay_counts("cult", i, cult_geoms[i], feat["properties"])

    for i, feat in enumerate(coh_hd_features):
        p = feat["properties"]
        if p.get("name") == "Courtland Place Historic District":
            p["name"] = "Courtlandt Place Historic District"
            alts = list(p.get("alt_names") or [])
            if "Courtland Place Historic District" not in alts:
                alts.append("Courtland Place Historic District")
            p["alt_names"] = alts
            p.pop("id", None)
            p.pop("description", None)
        geom = coh_hd_geoms[i]
        rep = geom.representative_point()
        if not p.get("id"):
            p["id"] = f"coh_hd_{i + 1:02d}_{slugify(p['name'])[:36]}"
        p["label_lng"] = round(rep.x, 5)
        p["label_lat"] = round(rep.y, 5)
        p["area_deg2"] = round(float(geom.area), 9)
        attach_overlay_counts("coh_hd", i, geom, p)
        if not p.get("description"):
            p["description"] = (
                f"{p['name']} is an official City of Houston Historic District governed by Chapter 33 of the "
                "Code of Ordinances. Exterior alterations, additions, new construction, and demolition of "
                "contributing structures require a Certificate of Appropriateness (COA) from the Houston "
                "Archaeological and Historical Commission (HAHC). Contributing properties also qualify for the "
                "City of Houston's 15-year Historic Site Tax Exemption upon approved rehabilitation."
            )
        feat["geometry"] = round_coords(mapping(geom), 5)

    for i, feat in enumerate(heritage_features):
        p = feat["properties"]
        if not p.get("id"):
            p["id"] = f"coh_her_{i + 1:02d}_{slugify(p.get('name', 'heritage_district'))[:36]}"
        if not p.get("description") and "Freedmen" in (p.get("name") or ""):
            p["description"] = (
                "Freedmen's Town Heritage District was designated by Houston City Council under Chapter 33, "
                "Article VII of the Code of Ordinances to recognize and protect the historic cultural landscape "
                "and public-realm heritage infrastructure of Freedmen's Town in Fourth Ward, founded by emancipated "
                "African Americans after Juneteenth 1865 and famous for the hand-laid brick streets along Andrews "
                "and Wilson Streets financed and laid by Black residents in 1914 under Rev. Ned P. Pullum."
            )

    for i, feat in enumerate(nrhp_dist_features):
        p = feat["properties"]
        geom = nrhp_dist_geoms[i]
        rep = geom.representative_point()
        if not p.get("id"):
            p["id"] = f"nrhp_dist_{i + 1:02d}_{slugify(p['name'])[:36]}"
        p["label_lng"] = round(rep.x, 5)
        p["label_lat"] = round(rep.y, 5)
        p["area_deg2"] = round(float(geom.area), 9)
        attach_overlay_counts("nrhp_dist", i, geom, p)
        if not p.get("description"):
            p["description"] = (
                f"{p['name']} is a federal National Register of Historic Places (NRHP) Historic District "
                "administered by the National Park Service and Texas Historical Commission. Contributing "
                "income-producing buildings qualify for up to 45% in combined historic rehabilitation tax credits "
                "(20% Federal + 25% Texas). Important: NRHP listing alone does NOT restrict demolition or "
                "remodeling with private funds unless the property is also inside a City of Houston Chapter 33 "
                "Historic District or designated as a Protected Landmark."
            )
        feat["geometry"] = round_coords(mapping(geom), 5)

    overlays["potential_historic_districts"] = {"type": "FeatureCollection", "features": pot_features}
    overlays["nrhp_properties"] = {"type": "FeatureCollection", "features": nrhp_prop_features}
    overlays["cultural_districts"] = {"type": "FeatureCollection", "features": cult_features}
    enrich_nrhp_demolished_overlay_and_search(overlays["nrhp_properties"], None)

    write_compact_json(overlays_path, overlays)
    print(
        f"Saved overlays.json ({overlays_path.stat().st_size / 1e6:.2f} MB) with "
        f"{len(pot_features)} potential HDs, {len(nrhp_prop_features)} NRHP properties, "
        f"and {len(cult_features)} cultural districts."
    )

    # -------------------------------------------------------------------------
    # 6. Merge Extra NRHP Footprints into buildings.geojson & Tag `protection_tier`
    # -------------------------------------------------------------------------
    if extra_nrhp_district_footprints:
        buildings_fc["features"].extend(extra_nrhp_district_footprints.values())
        print(
            f"Added {len(extra_nrhp_district_footprints):,} NRHP District/Property footprints to buildings.geojson "
            f"(new total: {len(buildings_fc['features']):,})"
        )

    b_tier_counts: dict[str, int] = {}
    for feat in buildings_fc["features"]:
        bp = feat["properties"]
        b_geom = shape(feat["geometry"])
        rep = b_geom.representative_point()
        hcad = str(bp.get("hcad_num") or "").strip()
        bid = str(bp.get("id") or "").strip()
        yr = int(bp.get("year_built") or 0)

        # Clean invalid historic_district strings
        bp["historic_district"] = clean_district_str(bp.get("historic_district"))

        # Check COH HD containment if missing
        in_coh_hd_name = bp["historic_district"]
        if not in_coh_hd_name:
            for hi in coh_hd_tree.query(rep):
                if coh_hd_geoms[hi].contains(rep):
                    in_coh_hd_name = coh_hd_features[hi]["properties"]["name"]
                    bp["historic_district"] = in_coh_hd_name
                    break

        # Check NRHP District containment
        in_nrhp_dist_name = str(bp.get("nrhp_district") or "").strip()
        if not in_nrhp_dist_name:
            for ni in nrhp_dist_tree.query(rep):
                if nrhp_dist_geoms[ni].contains(rep):
                    in_nrhp_dist_name = nrhp_dist_features[ni]["properties"]["name"]
                    break
        if in_nrhp_dist_name:
            bp["nrhp_district"] = in_nrhp_dist_name

        # Check Individual NRHP Property match
        matched_nrhp_prop = nrhp_by_bld_id.get(bid) or (nrhp_by_hcad.get(hcad) if hcad else None)
        if matched_nrhp_prop:
            bp["nrhp_refnum"] = matched_nrhp_prop["refnum"]
            bp["nrhp_resname"] = matched_nrhp_prop["name"]
            bp["nrhp_thc_url"] = matched_nrhp_prop["thc_atlas_url"]
            bp["nrhp_pdf_url"] = matched_nrhp_prop["nomination_pdf_url"]

        lm_type = str(bp.get("landmark_type") or "").strip()
        contrib = str(bp.get("contributing") or "").strip()

        is_plm = lm_type == "Protected Landmark" or (hcad and hcad in plm_hcads)
        is_lm = lm_type == "Landmark" or (hcad and (hcad in lm_in_hd_hcads or hcad in lm_waiver_hcads))

        tier = ""
        if is_plm:
            tier = "plm"
        elif in_coh_hd_name and (contrib == "Contributing" or is_lm or (contrib != "Non-Contributing" and 1836 <= yr <= 1965)):
            tier = "coh_hd_contributing"
        elif is_lm and not in_coh_hd_name:
            tier = "lm_waiver"
        elif matched_nrhp_prop or (in_nrhp_dist_name and (yr == 0 or yr <= 1976) and contrib != "Non-Contributing"):
            tier = "nrhp_eligible"
        elif in_coh_hd_name and contrib == "Non-Contributing":
            tier = "coh_hd_noncontributing"

        if tier:
            bp["protection_tier"] = tier
            b_tier_counts[tier] = b_tier_counts.get(tier, 0) + 1
        else:
            bp.pop("protection_tier", None)

    buildings_path.write_text(json.dumps(buildings_fc, separators=(",", ":")))
    print(
        f"Updated buildings.geojson ({buildings_path.stat().st_size / 1e6:.2f} MB) "
        f"protection_tier counts: {b_tier_counts}"
    )

    # -------------------------------------------------------------------------
    # 7. Idempotently Update search_index.json
    #    - Insert/replace boundary entries right at the end of existing is_neighborhood_entry items
    #    - Append Individual NRHP Property entries at the tail (never unshift!)
    # -------------------------------------------------------------------------
    raw_si: list[dict] = json.loads(search_index_path.read_text())
    generated_prefixes = ("pot_hd_", "cultural_dist_", "coh_hd_", "nrhp_dist_", "nrhp_")
    cleaned_si = [
        item
        for item in raw_si
        if not str(item.get("id") or "").startswith(generated_prefixes)
        and item.get("type") != "nrhp_property"
    ]

    new_boundary_entries: list[dict] = []
    for feat in coh_hd_features:
        p = feat["properties"]
        geom = shape(feat["geometry"])
        new_boundary_entries.append(
            {
                "id": p["id"],
                "is_neighborhood_entry": True,
                "place_type": "COH Historic District (Ch. 33)",
                "overlay_layer": "historic_districts",
                "name": p["name"],
                "building_name": p["name"],
                "alt_names": [],
                "address": (
                    f"City of Houston Historic District (Ch. 33 COA Protected) · "
                    f"{p.get('building_count', 0):,} structures · Earliest {p.get('earliest_year') or 'N/A'}"
                ),
                "category": "COH Historic District",
                "year_built": p.get("earliest_year") or 1910,
                "earliest_year": p.get("earliest_year") or 0,
                "building_count": p.get("building_count") or 0,
                "pre_1940_count": p.get("pre_1940_count") or 0,
                "lng": p["label_lng"],
                "lat": p["label_lat"],
                "bbox": [round(v, 5) for v in geom.bounds],
            }
        )

    for feat in nrhp_dist_features:
        p = feat["properties"]
        geom = shape(feat["geometry"])
        new_boundary_entries.append(
            {
                "id": p["id"],
                "is_neighborhood_entry": True,
                "place_type": "National Register District (NRHP)",
                "overlay_layer": "nrhp_districts",
                "name": p["name"],
                "building_name": p["name"],
                "alt_names": [],
                "address": (
                    f"National Register Historic District (45% Combined Tax Credit Eligible) · "
                    f"{p.get('building_count', 0):,} structures"
                ),
                "category": "NRHP Historic District",
                "year_built": p.get("earliest_year") or 1915,
                "earliest_year": p.get("earliest_year") or 0,
                "building_count": p.get("building_count") or 0,
                "pre_1940_count": p.get("pre_1940_count") or 0,
                "lng": p["label_lng"],
                "lat": p["label_lat"],
                "bbox": [round(v, 5) for v in geom.bounds],
            }
        )

    for feat in pot_features:
        p = feat["properties"]
        geom = shape(feat["geometry"])
        cat_label = (
            "Potential Historic District"
            if p["survey_subtype"] == "potential_district"
            else "Surveyed Eligible Site"
        )
        new_boundary_entries.append(
            {
                "id": p["id"],
                "is_neighborhood_entry": True,
                "place_type": p["type_label"],
                "overlay_layer": "potential_historic_districts",
                "name": p["name"],
                "building_name": p["name"],
                "alt_names": p.get("alt_names", []),
                "super_neighborhood": p.get("super_neighborhood") or "",
                "historic_ward": p.get("historic_ward") or "",
                "address": (
                    f"2019 COH Historic Survey ({cat_label}) · {p.get('building_count', 0):,} structures · "
                    f"{p.get('pre_1945_count', 0):,} pre-1946"
                ),
                "category": cat_label,
                "year_built": p.get("median_year") or p.get("earliest_year") or 1930,
                "earliest_year": p.get("earliest_year") or 0,
                "building_count": p.get("building_count") or 0,
                "pre_1940_count": p.get("pre_1940_count") or 0,
                "lng": p["label_lng"],
                "lat": p["label_lat"],
                "bbox": [round(v, 5) for v in geom.bounds],
            }
        )

    for feat in cult_features:
        p = feat["properties"]
        geom = shape(feat["geometry"])
        new_boundary_entries.append(
            {
                "id": p["id"],
                "is_neighborhood_entry": True,
                "place_type": "Cultural Arts District (COH / TCA)",
                "overlay_layer": "cultural_districts",
                "name": p["name"],
                "building_name": p["name"],
                "alt_names": [],
                "super_neighborhood": p.get("super_neighborhood") or "",
                "address": (
                    f"State-Recognized Cultural District · {p.get('building_count', 0):,} structures · "
                    f"{p.get('landmark_count', 0)} landmarks"
                ),
                "category": "Cultural District",
                "year_built": p.get("median_year") or 1935,
                "earliest_year": p.get("earliest_year") or 0,
                "building_count": p.get("building_count") or 0,
                "pre_1940_count": p.get("pre_1940_count") or 0,
                "lng": p["label_lng"],
                "lat": p["label_lat"],
                "bbox": [round(v, 5) for v in geom.bounds],
            }
        )

    new_nrhp_point_entries: list[dict] = []
    for feat in nrhp_prop_features:
        p = feat["properties"]
        coords = feat["geometry"]["coordinates"]
        ref_str = f"NRHP #{p['refnum']}" if p.get("refnum") else "NRHP Listed"
        alts = list(p.get("alt_names") or [])
        if p.get("refnum"):
            alts.append(f"NRHP #{p['refnum']}")
            alts.append(p["refnum"])
        if p.get("multiname"):
            alts.append(p["multiname"])
        status_note = "Demolished / Gone" if p["is_demolished_nrhp"] else "45% Tax Credit Eligible"
        new_nrhp_point_entries.append(
            {
                "type": "nrhp_property",
                "id": p["id"],
                "hcad_num": p.get("hcad_num") or "",
                "building_id": p.get("building_id") or "",
                "refnum": p.get("refnum") or "",
                "label": p["name"],
                "building_name": p["name"],
                "alt_names": alts,
                "address": p.get("address") or p.get("city") or "Houston, TX",
                "sublabel": (
                    f"{p.get('address') or 'Harris County'} • {ref_str} "
                    f"({p.get('listed_year') or 'Listed'}) • {status_note}"
                ),
                "category": "NRHP Listed (Gone)" if p["is_demolished_nrhp"] else "National Register (NRHP)",
                "year_built": p.get("year_built") or 0,
                "historic_district": p.get("historic_district") or "",
                "super_neighborhood": p.get("super_neighborhood") or "",
                "thc_atlas_url": p.get("thc_atlas_url") or "",
                "nomination_pdf_url": p.get("nomination_pdf_url") or "",
                "is_demolished_nrhp": bool(p["is_demolished_nrhp"]),
                "lon": coords[0],
                "lat": coords[1],
                "zoom": 17.5,
            }
        )

    # Find end of existing is_neighborhood_entry block so place entries stay grouped and building order is preserved
    insert_pos = 0
    for idx, item in enumerate(cleaned_si):
        if item.get("is_neighborhood_entry"):
            insert_pos = idx + 1
        else:
            break

    final_si = (
        cleaned_si[:insert_pos]
        + new_boundary_entries
        + cleaned_si[insert_pos:]
        + new_nrhp_point_entries
    )
    sanitize_coh_landmarks_overlay(overlays["landmarks"], final_si)
    enrich_nrhp_demolished_overlay_and_search(overlays["nrhp_properties"], final_si)
    write_compact_json(search_index_path, final_si)
    print(
        f"Updated search_index.json ({search_index_path.stat().st_size / 1e6:.2f} MB): "
        f"{len(final_si):,} total searchable entries."
    )


if __name__ == "__main__":
    main()
