"""Builds Priority #3 Social History, African American Heritage, and Redlining layers for The Houston Building Atlas.

Generates and cross-references:
1. overlays.json['red_book_1915'] (970 geocoded sites from the 1915 Red Book of Houston:
   A Compendium of Social, Professional, Religious, Educational and Industrial Interests of Houston's Colored Population):
   - Source: Rice University Woodson Research Center / Spatial Studies Lab FeatureServer
     (64 Churches + 12 Schools + 394 Black-Owned Businesses + 500 Residences & Civic Leaders = 970 sites),
     enriched with birthplace metadata, verified Rice Digital Collections portrait/photo links,
     and Houston Public Library Digital Archives full 1915 Red Book scan links.
   - Cross-references every site by coordinate and historic/modern street-name aliases against
     all 1.51M countywide HCAD building footprints, COH Landmarks, NRHP listings, Good Brick Awards,
     1937 HOLC Security Zones, 1877-1955 Sanborn Fire Insurance volumes, and 1866-1926 City Directories
     to classify survival status:
       * 'surviving_era_structure' (extant structure built <= 1926 or designated era landmark on site)
       * 'rebuilt_later_structure' (extant post-1926 replacement structure on parcel)
       * 'cleared_or_row' (vacant lot, surface parking, park, or freeway right-of-way erasure)
2. overlays.json['holc_1937_zones'] (47 Residential Security polygons from the 1937 Home Owners' Loan Corporation
   survey of Houston, TX):
   - Source: University of Richmond Digital Scholarship Lab Mapping Inequality v3 (TXHouston19XX, 47 polygons:
     7 Grade A 'Best', 14 Grade B 'Still Desirable', 15 Grade C 'Definitely Declining', 11 Grade D 'Hazardous'),
     joined with Houston Advanced Research Center (HARC) / NOAA Houston Heat Watch measured urban heat-island
     temperatures (AM / Afternoon / PM mean Fahrenheit).
   - Computes exact built-environment survival and preservation equity metrics across all 1.51M footprints:
     total extant structures, surviving pre-1938 structures (standing when the 1937 map was drawn),
     post-1990 redevelopment count, COH Protected Landmarks (PLM), 90-Day Waiver Landmarks (LM),
     COH & NRHP Historic Districts, Ch. 42 SMLS/SMBL block protections, deed-restricted subdivisions,
     1915 Red Book of Houston sites inside each zone, Lost Houston demolished landmarks, and active permit/plat alerts.
3. overlays.json['holc_1937_grade_summary']:
   - Citywide A / B / C / D comparative summary table (acreage, pre-1938 survival, PLM vs waiver landmarks,
     1915 Red Book site concentration, and area-weighted afternoon Heat Watch temperature).
4. Updates app/public/data/search_index.json idempotently with all 47 1937 HOLC Security Zones and
   all 970 1915 Red Book of Houston entries.
"""

from __future__ import annotations

import json
import math
import re
import statistics
import urllib.request
from collections import Counter
from pathlib import Path

from shapely.geometry import MultiPolygon, Point, Polygon, shape
from shapely.geometry.polygon import orient
from shapely.strtree import STRtree
from shapely.validation import make_valid

ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / "pipeline" / "cache"
SHARDS_DIR = CACHE_DIR / "aligned_ndjson"
DATA_DIR = ROOT / "app" / "public" / "data"

RED_BOOK_FS_BASE = (
    "https://services.arcgis.com/lqRTrQp2HrfnJt8U/arcgis/rest/services/"
    "The_Red_Book_of_Houston/FeatureServer"
)
MAPPING_INEQUALITY_HOUSTON_URL = (
    "https://dsl.richmond.edu/panorama/redlining/static/citiesData/TXHouston19XX/geojson.json"
)
MAPPING_INEQUALITY_PORTAL_URL = (
    "https://dsl.richmond.edu/panorama/redlining/map/TX/Houston/context"
)
HARC_HEAT_WATCH_HOLC_URL = (
    "https://services2.arcgis.com/LYMgRMwHfrWWEg3s/arcgis/rest/services/"
    "Heat_Watch_HOLC_Houston_Redlining_Map/FeatureServer/0/query"
    "?where=1%3D1&outFields=*&outSR=4326&f=geojson"
)
HPL_RED_BOOK_1915_URL = "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/182"
RICE_RED_BOOK_COLLECTION_URL = (
    "https://digitalcollections.rice.edu/Documents/Search?search=Red+Book+of+Houston"
)

HOLC_GRADE_META = {
    "A": {
        "label": 'Grade A ("Best")',
        "short_label": "Best",
        "color": "#22C55E",
        "AppraisalSummary": (
            'Designated "First Grade / Best" by 1937 HOLC appraisers: newly platted, homogenously white, '
            "deed-restricted suburban enclaves where federal FHA/HOLC underwriters endorsed maximum long-term "
            "amortizing mortgage financing."
        ),
    },
    "B": {
        "label": 'Grade B ("Still Desirable")',
        "short_label": "Still Desirable",
        "color": "#38BDF8",
        "AppraisalSummary": (
            'Designated "Second Grade / Still Desirable" by 1937 HOLC appraisers: established middle- and '
            "upper-middle-class streetcar suburbs judged stable for institutional lending at slightly more "
            "conservative loan-to-value ratios than Grade A enclaves."
        ),
    },
    "C": {
        "label": 'Grade C ("Definitely Declining")',
        "short_label": "Definitely Declining",
        "color": "#FACC15",
        "AppraisalSummary": (
            'Designated "Third Grade / Definitely Declining" by 1937 HOLC appraisers: older turn-of-the-century '
            "streetcar neighborhoods, working-class frame additions, or expiring deed-restriction corridors where "
            "conventional lenders restricted mortgage terms."
        ),
    },
    "D": {
        "label": 'Grade D ("Hazardous")',
        "short_label": "Hazardous",
        "color": "#EF4444",
        "AppraisalSummary": (
            'Colored crimson ("Fourth Grade / Hazardous" - the origin of the term "redlining") by 1937 HOLC '
            "appraisers: systematically excluded Houston's historic African American wards, Mexican American "
            "barrios, and industrial bayou/rail corridors from federally backed home-loan and rehabilitation credit."
        ),
    },
}

# Historic Houston street renamings between 1915 and present for HCAD situs cross-matching
HISTORIC_STREET_TRANSLATIONS: dict[str, list[str]] = {
    "SAN FELIPE": ["W DALLAS", "DALLAS", "SAN FELIPE"],
    "W DALLAS": ["SAN FELIPE", "W DALLAS", "DALLAS"],
    "GERMAN": ["CANAL", "NAVIGATION"],
    "CANAL": ["GERMAN", "CANAL"],
    "DOWLING": ["EMANCIPATION", "DOWLING"],
    "EMANCIPATION": ["DOWLING", "EMANCIPATION"],
    "ODIN": ["LYONS", "JENSEN"],
    "LYONS": ["ODIN", "LYONS"],
    "CLARK": ["JENSEN", "CLARK"],
    "JENSEN": ["CLARK", "ODIN", "JENSEN"],
    "CALHOUN": ["ST JOSEPH", "CALHOUN"],
    "BOULEVARD": ["HEIGHTS", "BOULEVARD"],
    "HEIGHTS": ["BOULEVARD", "HEIGHTS"],
    "BUFFALO": ["ALLEN", "MEMORIAL", "BUFFALO"],
    "CROSBY": ["W DALLAS", "CROSBY"],
    "HEINER": ["HEINER", "PIERCE"],
}


def sanitize_text(val: object) -> str:
    """Strip em-dashes and normalize whitespace."""
    if val is None:
        return ""
    s = str(val).replace("\u2014", " - ").replace("\u2013", "-")
    s = re.sub(r"\s+", " ", s).strip()
    if s.lower() in {"none", "null", "n/a", "<null>"}:
        return ""
    return s


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


def normalize_addr_keys(addr: str) -> list[str]:
    """Generate normalized address keys including historic street-renaming variants."""
    if not addr:
        return []
    s = addr.upper()
    s = re.sub(r"\(.*?\)", " ", s)
    # Strip historic second-floor suite fractions ('714 1/2 Prairie Ave' -> '714 Prairie Ave')
    s = re.sub(r"\b1\s*/\s*2\b", " ", s)
    s = re.sub(r"[^A-Z0-9\s]", " ", s)
    s = re.sub(
        r"\b(?:HOUSTON|PASADENA|BAYTOWN|LA PORTE|BELLAIRE|KATY|SPRING|CYPRESS|TOMBALL|TX|TEXAS)\b.*$",
        "",
        s,
    )
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
        "TWENTY": "20",
    }
    # Handle "22ND" written as "TWENTY SECOND"
    s = re.sub(r"\bTWENTY\s+FIRST\b", "21ST", s)
    s = re.sub(r"\bTWENTY\s+SECOND\b", "22ND", s)
    s = re.sub(r"\bTWENTY\s+THIRD\b", "23RD", s)
    s = re.sub(r"\bTWENTY\s+FOURTH\b", "24TH", s)
    s = re.sub(r"\bTWENTY\s+FIFTH\b", "25TH", s)

    tokens = [repl.get(tok, tok) for tok in s.split()]
    if not tokens or not tokens[0].isdigit():
        return []

    num = tokens[0]
    rest = tokens[1:]
    if not rest:
        return []

    # Strip trailing street suffix for street-stem matching as well as full key
    suffixes = {"ST", "AVE", "BLVD", "DR", "RD", "LN", "CT", "PL", "PKWY", "FWY", "HWY", "WAY"}
    stem_tokens = [t for t in rest if t not in suffixes]
    if not stem_tokens:
        stem_tokens = rest[:2]

    keys: list[str] = []
    full_key = " ".join([num] + rest[:3])
    stem_key = " ".join([num] + stem_tokens[:2])
    for k in (full_key, stem_key):
        if k not in keys:
            keys.append(k)

    # Also strip leading directional (N/S/E/W) variant in case HCAD omits or adds it
    if stem_tokens[0] in {"N", "S", "E", "W"} and len(stem_tokens) >= 2:
        nodir_stem = " ".join([num] + stem_tokens[1:2])
        if nodir_stem not in keys:
            keys.append(nodir_stem)

    # Expand historic street-renaming aliases (e.g., 2309 GERMAN -> 2309 CANAL, 2905 DOWLING -> 2905 EMANCIPATION)
    stem_str = " ".join(stem_tokens[:2])
    stem_nodir = " ".join(stem_tokens[1:3]) if stem_tokens[0] in {"N", "S", "E", "W"} else stem_str
    for cand_stem in {stem_str, stem_nodir, stem_tokens[0]}:
        if cand_stem in HISTORIC_STREET_TRANSLATIONS:
            for alt_st in HISTORIC_STREET_TRANSLATIONS[cand_stem]:
                for sfx in ("", " ST", " AVE", " DR"):
                    ak = f"{num} {alt_st}{sfx}".strip()
                    if ak not in keys:
                        keys.append(ak)
    return keys


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "HoustonBuildingAtlas/2.0"})
    with urllib.request.urlopen(req, timeout=35) as resp:
        return json.loads(resp.read().decode("utf-8", "ignore"))


def ensure_cached_datasets() -> tuple[dict, dict, dict]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    rb_cache = CACHE_DIR / "rice_red_book_1915_raw.json"
    if not rb_cache.exists():
        print("Downloading 1915 Red Book of Houston layers from Rice University GIS...")
        layers_data = {}
        for lid in (0, 1, 2, 3, 4):
            u = f"{RED_BOOK_FS_BASE}/{lid}/query?where=1%3D1&outFields=*&outSR=4326&f=geojson"
            layers_data[str(lid)] = fetch_json(u)
        rb_cache.write_text(json.dumps(layers_data))
    else:
        layers_data = json.loads(rb_cache.read_text())

    holc_cache = CACHE_DIR / "mapping_inequality_tx_houston_1937.geojson"
    if not holc_cache.exists():
        print("Downloading 1937 HOLC Houston polygons from Mapping Inequality (Univ. of Richmond)...")
        holc_gj = fetch_json(MAPPING_INEQUALITY_HOUSTON_URL)
        holc_cache.write_text(json.dumps(holc_gj))
    else:
        holc_gj = json.loads(holc_cache.read_text())

    harc_cache = CACHE_DIR / "harc_heat_watch_holc_houston.geojson"
    if not harc_cache.exists():
        print("Downloading HARC / NOAA Houston Heat Watch 1937 HOLC thermal dataset...")
        harc_gj = fetch_json(HARC_HEAT_WATCH_HOLC_URL)
        harc_cache.write_text(json.dumps(harc_gj))
    else:
        harc_gj = json.loads(harc_cache.read_text())

    return layers_data, holc_gj, harc_gj


def clean_verified_rice_photo_url(raw_url: object) -> str:
    """Return verified HTTP-200 Rice Digital Collections URL, rejecting retired digitalprojects.rice.edu URLs."""
    u = sanitize_text(raw_url)
    if not u:
        return ""
    if u.startswith("http://digitalcollections.rice.edu"):
        u = "https://" + u[len("http://") :]
    if u.startswith("https://digitalcollections.rice.edu/"):
        return u
    return ""


def approx_acres(geom) -> float:
    """Convert WGS84 degree^2 polygon area at Houston latitude (~29.75N) to acres."""
    lat_rad = math.radians(29.75)
    meters_per_deg_lat = 110850.0
    meters_per_deg_lng = 111320.0 * math.cos(lat_rad)
    sq_meters = geom.area * meters_per_deg_lat * meters_per_deg_lng
    return round(sq_meters / 4046.8564224, 1)


def main() -> None:
    rb_raw, holc_raw, harc_raw = ensure_cached_datasets()

    overlays_path = DATA_DIR / "overlays.json"
    buildings_path = DATA_DIR / "buildings.geojson"
    search_index_path = DATA_DIR / "search_index.json"
    sanborn_path = DATA_DIR / "sanborn_catalog.json"
    citydir_path = DATA_DIR / "citydir_index.json"
    permit_watch_path = DATA_DIR / "permit_watch.json"
    plat_watch_path = DATA_DIR / "plat_watch.json"

    overlays = json.loads(overlays_path.read_text())
    buildings_fc = json.loads(buildings_path.read_text())
    sanborn_cat = json.loads(sanborn_path.read_text()) if sanborn_path.exists() else {}
    citydir_idx = json.loads(citydir_path.read_text()) if citydir_path.exists() else {}
    citydir_sightings: dict = citydir_idx.get("sightings") or {}

    # Load spatial context trees from existing overlays
    def build_layer_tree(layer_key: str, filter_fn=None):
        feats = (overlays.get(layer_key) or {}).get("features") or []
        kept_feats = []
        geoms = []
        for f in feats:
            if filter_fn and not filter_fn(f):
                continue
            g_dict = f.get("geometry")
            if not g_dict:
                continue
            g = make_valid(shape(g_dict))
            if g.is_empty:
                continue
            kept_feats.append(f)
            geoms.append(g)
        return kept_feats, geoms, (STRtree(geoms) if geoms else None)

    ward_feats, ward_geoms, ward_tree = build_layer_tree(
        "historic_wards", lambda f: int((f.get("properties") or {}).get("era") or 0) == 1903
    )
    sn_feats, sn_geoms, sn_tree = build_layer_tree("super_neighborhoods")
    nbhd_feats, nbhd_geoms, nbhd_tree = build_layer_tree("neighborhoods")
    coh_hd_feats, coh_hd_geoms, coh_hd_tree = build_layer_tree("historic_districts")
    nrhp_dist_feats, nrhp_dist_geoms, nrhp_dist_tree = build_layer_tree("nrhp_districts")
    lup_feats, lup_geoms, lup_tree = build_layer_tree("land_use_protections")
    deed_feats, deed_geoms, deed_tree = build_layer_tree(
        "platted_subdivisions", lambda f: bool((f.get("properties") or {}).get("has_deed_docs"))
    )

    # Sanborn volume coverage polygons
    sanborn_vb_feats = sanborn_cat.get("volume_boundaries") or []
    if isinstance(sanborn_vb_feats, dict):
        sanborn_vb_feats = sanborn_vb_feats.get("features") or []
    sanborn_geoms = []
    sanborn_years = []
    for sf in sanborn_vb_feats:
        gd = sf.get("geometry")
        if gd:
            g = make_valid(shape(gd))
            if not g.is_empty:
                sp = sf.get("properties") or {}
                vy_raw = sanitize_text(sp.get("year") or sp.get("VolYear") or sp.get("sanborn_era"))
                vy = int(vy_raw[:4]) if vy_raw[:4].isdigit() else 0
                sanborn_geoms.append(g)
                sanborn_years.append(vy)
    sanborn_tree = STRtree(sanborn_geoms) if sanborn_geoms else None

    # Index COH Landmarks, NRHP Individual Properties, Good Brick Awards, Lost Houston, Permit/Plat Watch
    lm_by_hcad: dict[str, dict] = {}
    lm_pts: list[tuple[Point, dict]] = []
    for f in (overlays.get("landmarks") or {}).get("features") or []:
        lp = f.get("properties") or {}
        hc = sanitize_text(lp.get("hcad_num"))
        if hc:
            lm_by_hcad[hc] = lp
        gd = f.get("geometry")
        if gd and gd.get("type") == "Point":
            c = gd.get("coordinates") or [0, 0]
            lm_pts.append((Point(c[0], c[1]), lp))

    nrhp_by_hcad: dict[str, dict] = {}
    for f in (overlays.get("nrhp_properties") or {}).get("features") or []:
        np = f.get("properties") or {}
        hc = sanitize_text(np.get("hcad_num"))
        if hc and not np.get("is_demolished_nrhp"):
            nrhp_by_hcad[hc] = np

    gb_pts: list[Point] = []
    for f in (overlays.get("good_brick_awards") or {}).get("features") or []:
        gd = f.get("geometry")
        if gd:
            gb_pts.append(shape(gd).representative_point())

    lost_pts: list[tuple[Point, dict]] = []
    for f in (overlays.get("lost_houston") or {}).get("features") or []:
        gd = f.get("geometry")
        if gd:
            lost_pts.append((shape(gd).representative_point(), f.get("properties") or {}))

    permit_pts: list[Point] = []
    if permit_watch_path.exists():
        pw_data = json.loads(permit_watch_path.read_text())
        pw_feats = pw_data.get("features") if isinstance(pw_data, dict) else []
        for f in pw_feats or []:
            gd = f.get("geometry")
            if gd:
                permit_pts.append(shape(gd).representative_point())

    plat_pts: list[Point] = []
    if plat_watch_path.exists():
        pl_data = json.loads(plat_watch_path.read_text())
        pl_feats = pl_data.get("features") if isinstance(pl_data, dict) else []
        for f in pl_feats or []:
            gd = f.get("geometry")
            if gd:
                plat_pts.append(shape(gd).representative_point())

    # -------------------------------------------------------------------------
    # 1. Build 47 1937 HOLC Security Polygons (Mapping Inequality + HARC Heat Watch)
    # -------------------------------------------------------------------------
    harc_geoms = []
    harc_props = []
    for hf in harc_raw.get("features") or []:
        gd = hf.get("geometry")
        if gd:
            g = make_valid(shape(gd))
            if not g.is_empty:
                harc_geoms.append(g)
                harc_props.append(hf.get("properties") or {})
    harc_tree = STRtree(harc_geoms) if harc_geoms else None

    raw_holc_items = []
    for hf in holc_raw.get("features") or []:
        hp = hf.get("properties") or {}
        grade = sanitize_text(hp.get("grade")).upper()
        if grade not in HOLC_GRADE_META:
            continue
        geom = orient_geometry(shape(hf["geometry"]))
        if geom.is_empty:
            continue
        rep = geom.representative_point()
        raw_holc_items.append((grade, -round(rep.y, 3), round(rep.x, 3), hp, geom))

    # Deterministic order: Grade A -> B -> C -> D, North-to-South then West-to-East within each grade
    grade_order = {"A": 0, "B": 1, "C": 2, "D": 3}
    raw_holc_items.sort(key=lambda item: (grade_order[item[0]], item[1], item[2]))

    grade_counters: dict[str, int] = Counter()
    holc_features: list[dict] = []
    holc_geoms: list = []

    for grade, _, _, hp, geom in raw_holc_items:
        grade_counters[grade] += 1
        seq = grade_counters[grade]
        zone_code = f"{grade}-{seq}"
        zone_id = f"holc_1937_{grade.lower()}{seq}"
        mi_area_id = int(hp.get("area_id") or 0)
        rep = geom.representative_point()
        acres = approx_acres(geom)
        sq_mi = round(acres / 640.0, 2)

        # Intersecting neighborhoods (ranked by overlap area inside the HOLC polygon)
        nbhd_overlaps: list[tuple[float, str]] = []
        if nbhd_tree is not None:
            for n_idx in nbhd_tree.query(geom):
                ng = nbhd_geoms[n_idx]
                if geom.intersects(ng):
                    inter_a = geom.intersection(ng).area
                    if inter_a > 1e-8:
                        n_name = sanitize_text((nbhd_feats[n_idx].get("properties") or {}).get("name"))
                        if n_name:
                            nbhd_overlaps.append((inter_a, n_name))
        nbhd_overlaps.sort(reverse=True)
        top_nbhds = []
        seen_nb = set()
        for _, n_name in nbhd_overlaps:
            low = n_name.lower()
            if low not in seen_nb:
                seen_nb.add(low)
                top_nbhds.append(n_name)
            if len(top_nbhds) == 4:
                break

        # Intersecting 1903 Historic Wards
        ward_overlaps: list[tuple[float, str]] = []
        if ward_tree is not None:
            for w_idx in ward_tree.query(geom):
                wg = ward_geoms[w_idx]
                if geom.intersects(wg):
                    ia = geom.intersection(wg).area
                    if ia > geom.area * 0.04:
                        w_name = sanitize_text((ward_feats[w_idx].get("properties") or {}).get("name"))
                        if w_name and w_name not in [x[1] for x in ward_overlaps]:
                            ward_overlaps.append((ia, w_name))
        ward_overlaps.sort(reverse=True)
        covered_wards = [w for _, w in ward_overlaps[:2]]

        # Intersecting Super Neighborhoods
        sn_overlaps: list[tuple[float, str]] = []
        if sn_tree is not None:
            for s_idx in sn_tree.query(geom):
                sg = sn_geoms[s_idx]
                if geom.intersects(sg):
                    ia = geom.intersection(sg).area
                    if ia > geom.area * 0.05:
                        sn_name = sanitize_text((sn_feats[s_idx].get("properties") or {}).get("name"))
                        if sn_name:
                            sn_overlaps.append((ia, sn_name))
        sn_overlaps.sort(reverse=True)
        covered_sns = []
        for _, sname in sn_overlaps:
            if sname.lower() not in {x.lower() for x in covered_sns}:
                covered_sns.append(sname)
            if len(covered_sns) == 3:
                break

        # Build concise geographic subtitle / area title
        area_places = top_nbhds[:2] if top_nbhds else (covered_sns[:2] if covered_sns else covered_wards[:1])
        if covered_wards and covered_wards[0] not in area_places and grade in {"C", "D"}:
            if not any("ward" in p.lower() for p in area_places):
                area_places = [covered_wards[0]] + area_places[:1]
        area_title = " / ".join(area_places) if area_places else f"Houston Sector {zone_code}"
        meta = HOLC_GRADE_META[grade]
        full_name = f"1937 HOLC {zone_code} ({meta['short_label']}): {area_title}"

        # Area-weighted HARC / NOAA Heat Watch temperatures (AM / Afternoon / PM Fahrenheit)
        am_temp = None
        af_temp = None
        pm_temp = None
        if harc_tree is not None:
            w_sum = 0.0
            am_acc = 0.0
            af_acc = 0.0
            pm_acc = 0.0
            for h_idx in harc_tree.query(geom):
                hg = harc_geoms[h_idx]
                if geom.intersects(hg):
                    ia = geom.intersection(hg).area
                    if ia > 0:
                        hprop = harc_props[h_idx]
                        af_v = hprop.get("AF_Temp_Mean")
                        if af_v is not None:
                            w_sum += ia
                            af_acc += ia * float(af_v)
                            am_acc += ia * float(hprop.get("AM_Temp_Mean") or 0.0)
                            pm_acc += ia * float(hprop.get("PM_Temp_Mean") or 0.0)
            if w_sum > 0:
                af_temp = round(af_acc / w_sum, 1)
                am_temp = round(am_acc / w_sum, 1)
                pm_temp = round(pm_acc / w_sum, 1)

        # Intersecting COH Historic Districts & NRHP Districts
        coh_hds = []
        if coh_hd_tree is not None:
            for idx in coh_hd_tree.query(geom):
                if geom.intersects(coh_hd_geoms[idx]) and geom.intersection(coh_hd_geoms[idx]).area > 1e-8:
                    nm = sanitize_text((coh_hd_feats[idx].get("properties") or {}).get("name"))
                    if nm and nm not in coh_hds:
                        coh_hds.append(nm)

        nrhp_hds = []
        if nrhp_dist_tree is not None:
            for idx in nrhp_dist_tree.query(geom):
                if geom.intersects(nrhp_dist_geoms[idx]) and geom.intersection(nrhp_dist_geoms[idx]).area > 1e-8:
                    nm = sanitize_text((nrhp_dist_feats[idx].get("properties") or {}).get("name"))
                    if nm and nm not in nrhp_hds:
                        nrhp_hds.append(nm)

        # Ch. 42 SMLS / SMBL blocks & Deed-Restricted subdivisions inside or intersecting zone
        smls_cnt = 0
        smbl_cnt = 0
        if lup_tree is not None:
            for idx in lup_tree.query(geom):
                lg = lup_geoms[idx]
                if geom.contains(lg.representative_point()):
                    pt_type = sanitize_text((lup_feats[idx].get("properties") or {}).get("protection_type")).upper()
                    if "SMLS" in pt_type or "LOT" in pt_type:
                        smls_cnt += 1
                    elif "SMBL" in pt_type or "LINE" in pt_type:
                        smbl_cnt += 1

        deed_cnt = 0
        if deed_tree is not None:
            for idx in deed_tree.query(geom):
                dg = deed_geoms[idx]
                if geom.contains(dg.representative_point()):
                    deed_cnt += 1

        # Point overlays inside HOLC polygon
        plm_cnt = 0
        waiver_lm_cnt = 0
        total_lm_cnt = 0
        for pt, lp in lm_pts:
            if geom.contains(pt):
                total_lm_cnt += 1
                if lp.get("protection_tier") == "lm_waiver_vulnerable":
                    waiver_lm_cnt += 1
                else:
                    plm_cnt += 1

        gb_cnt = sum(1 for pt in gb_pts if geom.contains(pt))
        lost_cnt = sum(1 for pt, _ in lost_pts if geom.contains(pt))
        permit_cnt = sum(1 for pt in permit_pts if geom.contains(pt))
        plat_cnt = sum(1 for pt in plat_pts if geom.contains(pt))

        holc_geoms.append(geom)
        holc_features.append(
            {
                "type": "Feature",
                "properties": {
                    "id": zone_id,
                    "zone_code": zone_code,
                    "holc_grade": grade,
                    "grade_label": meta["label"],
                    "grade_short": meta["short_label"],
                    "color": meta["color"],
                    "name": full_name,
                    "area_title": area_title,
                    "mapping_inequality_area_id": mi_area_id,
                    "mapping_inequality_url": MAPPING_INEQUALITY_PORTAL_URL,
                    "area_acres": acres,
                    "area_sq_mi": sq_mi,
                    "top_neighborhoods": top_nbhds,
                    "historic_wards": covered_wards,
                    "super_neighborhoods": covered_sns,
                    "coh_historic_districts": coh_hds,
                    "nrhp_districts": nrhp_hds,
                    "smls_block_count": smls_cnt,
                    "smbl_block_count": smbl_cnt,
                    "deed_subdiv_count": deed_cnt,
                    "landmark_count": total_lm_cnt,
                    "plm_count": plm_cnt,
                    "vulnerable_lm_count": waiver_lm_cnt,
                    "good_brick_count": gb_cnt,
                    "lost_houston_count": lost_cnt,
                    "active_permit_alert_count": permit_cnt,
                    "active_plat_alert_count": plat_cnt,
                    "af_temp_f": af_temp,
                    "am_temp_f": am_temp,
                    "pm_temp_f": pm_temp,
                    "nara_sheet_status": (
                        "NARA Record Group 195 preserves Houston's original 1937 color-graded HOLC Residential "
                        "Security Map sheet, though the accompanying mimeographed Area Description text forms "
                        "for Houston were not retained in the federal archive."
                    ),
                    "appraisal_summary": meta["AppraisalSummary"],
                    "label_lng": round(rep.x, 5),
                    "label_lat": round(rep.y, 5),
                    # Populated in footprint + Red Book passes below:
                    "building_count": 0,
                    "earliest_year": 0,
                    "median_year": 0,
                    "pre_1938_count": 0,
                    "pre_1940_count": 0,
                    "post_1990_count": 0,
                    "red_book_1915_count": 0,
                    "red_book_1915_surviving_count": 0,
                },
                "geometry": round_coords(geom.__geo_interface__, 5),
            }
        )

    holc_tree = STRtree(holc_geoms)

    # -------------------------------------------------------------------------
    # 2. Build 970 1915 Red Book of Houston Heritage Sites (Layers 0, 1, 3, 4 + Layer 2 Bio Enrichment)
    # -------------------------------------------------------------------------
    birthplace_by_name: dict[str, dict] = {}
    for bf in (rb_raw.get("2") or {}).get("features") or []:
        bp = bf.get("properties") or {}
        nm = sanitize_text(bp.get("USER_name") or bp.get("Name"))
        if nm:
            birthplace_by_name[nm.lower()] = bp

    rb_features: list[dict] = []
    rb_pts: list[Point] = []
    rb_by_addr_key: dict[str, list[int]] = {}
    iiif_cache_path = CACHE_DIR / "rice_quartex_iiif_map.json"
    if not iiif_cache_path.exists():
        iiif_cache_path = Path(__file__).resolve().parent / ".cache" / "rice_quartex_iiif_map.json"
    rice_iiif_map: dict[str, dict] = (
        json.loads(iiif_cache_path.read_text()) if iiif_cache_path.exists() else {}
    )

    layer_specs = [
        ("0", "church", "Historic Black Church (1915)"),
        ("3", "school", "Colored Public / Private School (1915)"),
        ("1", "business", "Black-Owned Business / Enterprise (1915)"),
        ("4", "residence", "Civic Leader & Residence (1915)"),
    ]

    for lid_str, cat_key, cat_label in layer_specs:
        feats = (rb_raw.get(lid_str) or {}).get("features") or []
        for f in feats:
            p = f.get("properties") or {}
            coords = (f.get("geometry") or {}).get("coordinates") or [0.0, 0.0]
            lng = round(float(coords[0]), 6)
            lat = round(float(coords[1]), 6)
            pt = Point(lng, lat)
            oid = int(p.get("ObjectID") or len(rb_features) + 1)
            rb_id = f"rb1915_{cat_key}_{oid}"

            if cat_key == "church":
                title = sanitize_text(p.get("USER_church_name") or p.get("Name")) or "Historic Church (1915)"
                hist_addr = sanitize_text(p.get("USER_church_address"))
                mod_addr = sanitize_text(p.get("USER_modern_addresses") or p.get("StAddr") or hist_addr)
                pastor = sanitize_text(p.get("USER_pastor_name"))
                person_name = pastor
                role_or_type = f"Pastor: {pastor}" if pastor else "African American Congregation (1915)"
                photo1 = clean_verified_rice_photo_url(p.get("USER_image_URL"))
                photo2 = ""
                gender = ""
                occupation = ""
                business_type = "Church / Religious Congregation"
            elif cat_key == "school":
                title = sanitize_text(p.get("USER_school_name") or p.get("Name")) or "Historic School (1915)"
                hist_addr = sanitize_text(p.get("USER_school_address"))
                mod_addr = sanitize_text(p.get("USER_modern_addresses") or p.get("StAddr") or hist_addr)
                pastor = ""
                person_name = ""
                role_or_type = "1915 African American Educational Institution"
                photo1 = clean_verified_rice_photo_url(p.get("image_01"))
                photo2 = ""
                gender = ""
                occupation = ""
                business_type = "Educational Institution"
            elif cat_key == "business":
                biz_name = sanitize_text(p.get("USER_name_of_business"))
                owner = sanitize_text(p.get("USER_business_owner_employee") or p.get("Name"))
                btype = sanitize_text(p.get("USER_type_of_business")) or "Enterprise"
                if biz_name and owner and biz_name.lower() != owner.lower():
                    title = biz_name
                    role_or_type = f"{btype} · Proprietor/Staff: {owner}"
                elif biz_name:
                    title = biz_name
                    role_or_type = btype
                elif owner:
                    title = f"{owner} ({btype})"
                    role_or_type = f"{btype} · Proprietor: {owner}"
                else:
                    title = f"1915 {btype}"
                    role_or_type = btype
                hist_addr = sanitize_text(p.get("USER_address"))
                mod_addr = sanitize_text(p.get("USER_modern_addresses") or p.get("StAddr") or hist_addr)
                pastor = ""
                person_name = owner
                gender = sanitize_text(p.get("USER_owner_male"))
                occupation = btype
                business_type = btype
                photo1 = clean_verified_rice_photo_url(p.get("image_01"))
                photo2 = ""
            else:
                person_name = sanitize_text(p.get("USER_name") or p.get("Name")) or "1915 Resident"
                occupation = sanitize_text(p.get("USER_occupation"))
                teaches_at = sanitize_text(p.get("USER_teaches_at"))
                if teaches_at and "teach" not in occupation.lower():
                    occ_disp = f"{occupation} ({teaches_at})" if occupation else f"Educator at {teaches_at}"
                elif teaches_at and occupation:
                    occ_disp = f"{occupation}, {teaches_at}"
                else:
                    occ_disp = occupation or "1915 Civic & Community Resident"
                title = person_name
                role_or_type = occ_disp
                hist_addr = sanitize_text(p.get("USER_home_address"))
                mod_addr = sanitize_text(p.get("USER_modern_address") or p.get("StAddr") or hist_addr)
                pastor = ""
                gender = sanitize_text(p.get("USER_gender"))
                business_type = ""
                photo1 = clean_verified_rice_photo_url(p.get("USER_image_01"))
                photo2 = clean_verified_rice_photo_url(p.get("USER_image_02"))

            # Biographical / fraternal / birthplace fields (especially rich on Layer 4 + cross-matched from Layer 2)
            bio_extra = birthplace_by_name.get(person_name.lower(), {}) if person_name else {}
            teaches_at_val = sanitize_text(p.get("USER_teaches_at") or bio_extra.get("USER_teaches_at"))
            church_att = sanitize_text(p.get("USER_church_attended") or bio_extra.get("USER_church_attended"))
            school_att = sanitize_text(p.get("USER_school_attended") or bio_extra.get("USER_school_attended"))
            lodge_m = sanitize_text(p.get("USER_male_club_lodges_member") or bio_extra.get("USER_male_club_lodges_member"))
            lodge_f = sanitize_text(
                p.get("USER_female_club_lodges_member") or bio_extra.get("USER_female_club_lodges_member")
            )
            lodges = " · ".join([x for x in (lodge_m, lodge_f) if x])

            bp_city = sanitize_text(
                p.get("USER_place_of_birth_city_county") or bio_extra.get("USER_place_of_birth_city_county")
            )
            bp_county = sanitize_text(
                p.get("USER_place_of_birth_county") or bio_extra.get("USER_place_of_birth_county")
            )
            bp_state = sanitize_text(
                p.get("USER_place_of_birth_state") or bio_extra.get("USER_place_of_birth_state")
            )
            bp_parts = [x for x in (bp_city, f"{bp_county} County" if bp_county and bp_county != bp_city else "", bp_state) if x]
            birthplace = ", ".join(bp_parts)
            birth_year_raw = sanitize_text(p.get("USER_birth_year") or bio_extra.get("USER_birth_year"))
            birth_year = int(birth_year_raw) if birth_year_raw.isdigit() else 0
            married_to = sanitize_text(p.get("USER_married_to") or bio_extra.get("USER_married_to"))
            children = sanitize_text(p.get("USER_children") or bio_extra.get("USER_children"))

            # Spatial context: 1903 Historic Ward, Super Neighborhood, 1937 HOLC Zone, Sanborn coverage
            ward_name = ""
            if ward_tree is not None:
                for w_idx in ward_tree.query(pt):
                    if ward_geoms[w_idx].contains(pt):
                        ward_name = sanitize_text((ward_feats[w_idx].get("properties") or {}).get("name"))
                        break

            sn_name = ""
            if sn_tree is not None:
                for s_idx in sn_tree.query(pt):
                    if sn_geoms[s_idx].contains(pt):
                        sn_name = sanitize_text((sn_feats[s_idx].get("properties") or {}).get("name"))
                        break
            if not sn_name:
                sn_name = sanitize_text(p.get("Nbrhd"))

            holc_grade = ""
            holc_zone_code = ""
            holc_zone_id = ""
            for h_idx in holc_tree.query(pt):
                if holc_geoms[h_idx].contains(pt):
                    hp_hit = holc_features[h_idx]["properties"]
                    holc_grade = hp_hit["holc_grade"]
                    holc_zone_code = hp_hit["zone_code"]
                    holc_zone_id = hp_hit["id"]
                    hp_hit["red_book_1915_count"] += 1
                    break

            sanborn_1907 = False
            sanborn_vol_cnt = 0
            if sanborn_tree is not None:
                for sb_idx in sanborn_tree.query(pt):
                    if sanborn_geoms[sb_idx].contains(pt):
                        sanborn_vol_cnt += 1
                        if sanborn_years[sb_idx] == 1907:
                            sanborn_1907 = True

            e_idx = len(rb_features)
            rb_pts.append(pt)
            all_akeys: list[str] = []
            for cand_addr in (mod_addr, hist_addr, sanitize_text(p.get("StAddr"))):
                for ak in normalize_addr_keys(cand_addr):
                    if ak not in all_akeys:
                        all_akeys.append(ak)
                        rb_by_addr_key.setdefault(ak, []).append(e_idx)

            rb_features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "id": rb_id,
                        "category": cat_key,
                        "category_label": cat_label,
                        "name": title,
                        "subtitle": role_or_type,
                        "person_name": person_name,
                        "gender": gender,
                        "occupation": occupation,
                        "business_type": business_type,
                        "pastor_name": pastor,
                        "teaches_at": teaches_at_val,
                        "church_attended": church_att,
                        "school_attended": school_att,
                        "lodges_clubs": lodges,
                        "birthplace": birthplace,
                        "birth_year": birth_year,
                        "married_to": married_to,
                        "children": children,
                        "historic_address_1915": hist_addr or mod_addr,
                        "modern_address": mod_addr or hist_addr,
                        "addr_precision": sanitize_text(p.get("Addr_type")),
                        "historic_ward": ward_name,
                        "super_neighborhood": sn_name,
                        "holc_1937_grade": holc_grade,
                        "holc_1937_zone_code": holc_zone_code,
                        "holc_1937_zone_id": holc_zone_id,
                        "sanborn_1907_covered": sanborn_1907,
                        "sanborn_volumes_count": sanborn_vol_cnt,
                        "rice_photo_url": photo1,
                        "rice_photo_url_2": photo2,
                        "rice_photo_img_url": (rice_iiif_map.get(photo1) or {}).get("img_url", ""),
                        "rice_photo_full_url": (rice_iiif_map.get(photo1) or {}).get("full_img_url", ""),
                        "red_book_page": (rice_iiif_map.get(photo1) or {}).get("page_cite", ""),
                        "hpl_book_url": HPL_RED_BOOK_1915_URL,
                        "rice_collection_url": RICE_RED_BOOK_COLLECTION_URL,
                        # Populated in footprint matching below:
                        "survival_status": "cleared_or_row",
                        "survival_label": "Demolished / Cleared Lot or Right-of-Way",
                        "hcad_num": "",
                        "building_id": "",
                        "extant_year_built": 0,
                        "extant_building_name": "",
                        "landmark_designation": "",
                        "citydir_earliest_year": 0,
                        "_addr_keys": all_akeys,
                    },
                    "geometry": {"type": "Point", "coordinates": [lng, lat]},
                }
            )

    # -------------------------------------------------------------------------
    # 3. Single Countywide Footprint Pass (1.51M structures across 5 .geojsonseq shards)
    #    - Computes exact building/era stats for all 47 1937 HOLC polygons
    #    - Matches all 970 1915 Red Book sites to extant structures & classifies survival!
    # -------------------------------------------------------------------------
    holc_stats = [
        {"count": 0, "years": [], "pre1938": 0, "pre1940": 0, "post1990": 0}
        for _ in range(len(holc_features))
    ]

    # Buffer Red Book points by ~0.00033 deg (~36m) for tight parcel/footprint spatial match
    rb_buffered = [pt.buffer(0.00033) for pt in rb_pts]
    rb_pt_tree = STRtree(rb_buffered)
    rb_best_match: dict[int, tuple[float, dict]] = {}

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
                bp = feat.get("properties") or {}
                coords = geom_dict.get("coordinates")
                if not coords:
                    continue
                ring = (
                    coords[0]
                    if geom_dict.get("type") == "Polygon"
                    else (coords[0][0] if coords[0] else None)
                )
                if not ring:
                    continue
                cx = sum(pt_c[0] for pt_c in ring) / len(ring)
                cy = sum(pt_c[1] for pt_c in ring) / len(ring)
                pt = Point(cx, cy)
                yr = int(bp.get("year_built") or 0)

                # 3a. HOLC polygon stats
                for h_idx in holc_tree.query(pt):
                    if holc_geoms[h_idx].contains(pt):
                        st = holc_stats[h_idx]
                        st["count"] += 1
                        if yr >= 1836:
                            st["years"].append(yr)
                            if yr <= 1937:
                                st["pre1938"] += 1
                            if yr < 1940:
                                st["pre1940"] += 1
                            if yr >= 1990:
                                st["post1990"] += 1

                # 3b. Red Book site matching (by exact normalized street address or tight footprint proximity)
                b_addr = sanitize_text(bp.get("address"))
                b_akeys = normalize_addr_keys(b_addr)
                cand_rb: set[int] = set()
                for bak in b_akeys:
                    if bak in rb_by_addr_key:
                        for e_idx in rb_by_addr_key[bak]:
                            rc = rb_features[e_idx]["geometry"]["coordinates"]
                            if math.hypot(cx - rc[0], cy - rc[1]) <= 0.0042:  # within ~450m for exact address
                                cand_rb.add(e_idx)

                for e_idx in rb_pt_tree.query(pt):
                    cand_rb.add(int(e_idx))

                if cand_rb:
                    b_geom = shape(geom_dict)
                    b_akey_set = set(b_akeys)
                    for e_idx in cand_rb:
                        rp = rb_features[e_idx]["properties"]
                        rc = rb_features[e_idx]["geometry"]["coordinates"]
                        r_pt = Point(rc[0], rc[1])
                        r_akey_set = set(rp["_addr_keys"])
                        exact_addr = bool(b_akey_set and r_akey_set and (b_akey_set & r_akey_set))
                        contains_pt = b_geom.contains(r_pt)
                        dist_deg = b_geom.distance(r_pt)
                        # ArcGIS 'StreetName' precision means the house number no longer exists on the modern street
                        # network (demolished block); never falsely latch onto a neighboring era building!
                        if rp.get("addr_precision") == "StreetName" and not exact_addr:
                            if not (contains_pt and yr > 1926):
                                continue
                        # When both records have street addresses that disagree, never jump across parcel lines
                        # to a neighboring house; only match if the pin falls strictly inside a post-1926 redevelopment block.
                        if b_akey_set and r_akey_set and not exact_addr:
                            if not (contains_pt and yr > 1926):
                                continue
                        if not exact_addr and dist_deg > 0.00014:
                            continue
                        age_bonus = -0.00003 if (exact_addr and 1836 <= yr <= 1926) else 0.0
                        score = (
                            (0.0 if exact_addr else 0.00022)
                            + (0.0 if contains_pt else dist_deg)
                            + age_bonus
                        )
                        prev = rb_best_match.get(e_idx)
                        if prev is None or score < prev[0]:
                            rb_best_match[e_idx] = (score, bp)

    # Also check curated buildings.geojson (which has enriched building_name, landmark_code, overrides)
    for b_feat in buildings_fc.get("features") or []:
        bp = b_feat.get("properties") or {}
        b_geom = shape(b_feat["geometry"])
        rep = b_geom.representative_point()
        yr = int(bp.get("year_built") or 0)
        b_akeys = normalize_addr_keys(sanitize_text(bp.get("address")))
        b_akey_set = set(b_akeys)
        cand_rb: set[int] = set()
        for bak in b_akeys:
            if bak in rb_by_addr_key:
                for e_idx in rb_by_addr_key[bak]:
                    rc = rb_features[e_idx]["geometry"]["coordinates"]
                    if math.hypot(rep.x - rc[0], rep.y - rc[1]) <= 0.0042:
                        cand_rb.add(e_idx)
        for e_idx in rb_pt_tree.query(rep):
            cand_rb.add(int(e_idx))
        for e_idx in cand_rb:
            rp = rb_features[e_idx]["properties"]
            rc = rb_features[e_idx]["geometry"]["coordinates"]
            r_pt = Point(rc[0], rc[1])
            r_akey_set = set(rp["_addr_keys"])
            exact_addr = bool(b_akey_set and r_akey_set and (b_akey_set & r_akey_set))
            contains_pt = b_geom.contains(r_pt)
            dist_deg = b_geom.distance(r_pt)
            if rp.get("addr_precision") == "StreetName" and not exact_addr:
                if not (contains_pt and yr > 1926):
                    continue
            if b_akey_set and r_akey_set and not exact_addr:
                if not (contains_pt and yr > 1926):
                    continue
            if not exact_addr and dist_deg > 0.00014:
                continue
            age_bonus = -0.00003 if (exact_addr and 1836 <= yr <= 1926) else 0.0
            score = (0.0 if exact_addr else 0.00022) + (0.0 if contains_pt else dist_deg) + age_bonus - 0.00001
            prev = rb_best_match.get(e_idx)
            if prev is None or score < prev[0]:
                rb_best_match[e_idx] = (score, bp)

    # Finalize Red Book survival classifications & landmark / City Directory cross-links
    holc_id_to_idx = {f["properties"]["id"]: idx for idx, f in enumerate(holc_features)}
    survival_counts: Counter = Counter()

    curated_iconic_landmarks: dict[str, dict] = {
        "rb1915_church_4": {
            "modern_address": "500 Clay Ave",
            "hcad_num": "0321670000022",
            "building_id": "coh_lm_034",
            "extant_year_built": 1875,
            "extant_building_name": "Antioch Missionary Baptist Church",
            "landmark_designation": "COH Landmark & NRHP #76002038",
            "survival_label": "Surviving Designated Landmark (1875)",
            "coords": [-95.372064, 29.756286],
        },
        "rb1915_church_1": {
            "modern_address": "801 Andrews St",
            "hcad_num": "0050060000015",
            "building_id": "coh_lm_072",
            "extant_year_built": 1923,
            "extant_building_name": "Bethel Baptist Church Building",
            "landmark_designation": "COH Landmark & NRHP #97001626",
            "survival_label": "Surviving Designated Landmark (1923)",
            "coords": [-95.376231, 29.755638],
        },
        "rb1915_school_6": {
            "modern_address": "1300 Victor St",
            "hcad_num": "0050180000019",
            "building_id": "coh_lm_105",
            "extant_year_built": 1926,
            "extant_building_name": "African American Library at the Gregory School",
            "landmark_designation": "COH Protected Landmark",
            "survival_label": "Surviving Designated Landmark (1926)",
            "coords": [-95.380488, 29.754207],
        },
        "rb1915_business_191": {
            "modern_address": "1300 Court Street",
            "hcad_num": "",
            "building_id": "coh_lm_505",
            "extant_year_built": 1875,
            "extant_building_name": "Olivewood Cemetery",
            "landmark_designation": "COH Protected Landmark",
            "survival_label": "Surviving Designated Landmark Site (1875)",
            "coords": [-95.391925, 29.773856],
        },
        "rb1915_residence_2": {
            "modern_address": "1218 Wilson St",
            "hcad_num": "0090790000009",
            "building_id": "coh_lm_204",
            "extant_year_built": 1907,
            "extant_building_name": "J. Vance Lewis House",
            "landmark_designation": "COH Protected Landmark",
            "survival_label": "Surviving Designated Landmark (1907)",
            "coords": [-95.381401, 29.755989],
        },
        "rb1915_residence_370": {
            "modern_address": "1218 Wilson St",
            "hcad_num": "0090790000009",
            "building_id": "coh_lm_204",
            "extant_year_built": 1907,
            "extant_building_name": "J. Vance Lewis House",
            "landmark_designation": "COH Protected Landmark",
            "survival_label": "Surviving Designated Landmark (1907)",
            "coords": [-95.381401, 29.755989],
        },
        "rb1915_residence_197": {
            "modern_address": "1319 Andrews St",
            "hcad_num": "0050210000005",
            "building_id": "coh_lm_207",
            "extant_year_built": 1898,
            "extant_building_name": "Reverend Ned P. Pullum House",
            "landmark_designation": "COH Protected Landmark",
            "survival_label": "Surviving Designated Landmark (1898)",
            "coords": [-95.38123, 29.755638],
        },
        "rb1915_residence_468": {
            "modern_address": "1319 Andrews St",
            "hcad_num": "0050210000005",
            "building_id": "coh_lm_207",
            "extant_year_built": 1898,
            "extant_building_name": "Reverend Ned P. Pullum House",
            "landmark_designation": "COH Protected Landmark",
            "survival_label": "Surviving Designated Landmark (1898)",
            "coords": [-95.38123, 29.755638],
        },
    }

    for e_idx, r_feat in enumerate(rb_features):
        rp = r_feat["properties"]
        rp.pop("_addr_keys", None)
        cur_ov = curated_iconic_landmarks.get(rp["id"])
        if cur_ov is not None:
            rp["modern_address"] = cur_ov["modern_address"]
            rp["hcad_num"] = cur_ov["hcad_num"]
            rp["building_id"] = cur_ov["building_id"]
            rp["extant_year_built"] = cur_ov["extant_year_built"]
            rp["extant_building_name"] = cur_ov["extant_building_name"]
            rp["landmark_designation"] = cur_ov["landmark_designation"]
            rp["survival_status"] = "surviving_era_structure"
            rp["survival_label"] = cur_ov["survival_label"]
            r_feat["geometry"]["coordinates"] = list(cur_ov["coords"])
            hz_id = rp.get("holc_1937_zone_id")
            if hz_id and hz_id in holc_id_to_idx:
                holc_features[holc_id_to_idx[hz_id]]["properties"]["red_book_1915_surviving_count"] += 1
            survival_counts[rp["survival_status"]] += 1
            continue

        match = rb_best_match.get(e_idx)
        if match is not None:
            _, bp = match
            hcad = sanitize_text(bp.get("hcad_num"))
            bid = sanitize_text(bp.get("id") or bp.get("building_id"))
            byr = int(bp.get("year_built") or 0)
            bname = sanitize_text(bp.get("building_name") or bp.get("landmark_name"))
            rp["hcad_num"] = hcad
            rp["building_id"] = bid
            rp["extant_year_built"] = byr if byr >= 1836 else 0
            rp["extant_building_name"] = bname

            lm_info = lm_by_hcad.get(hcad) if hcad else None
            nrhp_info = nrhp_by_hcad.get(hcad) if hcad else None
            desig_parts = []
            if lm_info:
                desig_parts.append(
                    "COH Protected Landmark" if lm_info.get("is_protected_landmark") else "COH Landmark"
                )
            if nrhp_info:
                desig_parts.append(f"NRHP #{nrhp_info.get('refnum')}" if nrhp_info.get("refnum") else "NRHP Listed")
            rp["landmark_designation"] = " & ".join(desig_parts)

            if hcad and hcad in citydir_sightings:
                cd_entry = citydir_sightings[hcad]
                if isinstance(cd_entry, dict):
                    rp["citydir_earliest_year"] = int(cd_entry.get("e") or 0)

            # Classify era survival vs later rebuild
            is_era_landmark = bool(
                (lm_info and 1836 <= int(lm_info.get("year_built") or 0) <= 1928)
                or (nrhp_info and 1836 <= int(nrhp_info.get("year_built") or 0) <= 1928)
            )
            if (1836 <= byr <= 1926) or is_era_landmark:
                rp["survival_status"] = "surviving_era_structure"
                yr_note = f" (Built {byr})" if byr >= 1836 else ""
                rp["survival_label"] = f"Surviving Era Structure on Site{yr_note}"
                hz_id = rp.get("holc_1937_zone_id")
                if hz_id and hz_id in holc_id_to_idx:
                    holc_features[holc_id_to_idx[hz_id]]["properties"]["red_book_1915_surviving_count"] += 1
            else:
                rp["survival_status"] = "rebuilt_later_structure"
                yr_note = f" (Current Structure Built {byr})" if byr >= 1836 else " (Post-1926 Structure on Parcel)"
                rp["survival_label"] = f"Historic Site Redeveloped{yr_note}"

        # Also check proximity & name/address match against COH Landmarks & NRHP Individual Properties
        # (e.g. Antioch Missionary Baptist Church at 313 Robin / 500 Clay, Bethel Baptist Church at 801 Andrews)
        if rp["survival_status"] != "surviving_era_structure":
            rc = r_feat["geometry"]["coordinates"]
            r_pt = Point(rc[0], rc[1])
            r_name = rp["name"].lower()
            r_addr_keys = set(normalize_addr_keys(rp.get("historic_address_1915") or "")) | set(
                normalize_addr_keys(rp.get("modern_address") or "")
            )
            for l_pt, lp in lm_pts:
                dist_lm = r_pt.distance(l_pt)
                if dist_lm <= 0.0014:
                    l_name = sanitize_text(lp.get("building_name") or lp.get("name")).lower()
                    l_akeys = set(normalize_addr_keys(sanitize_text(lp.get("address"))))
                    name_hit = bool(
                        l_name
                        and len(l_name) >= 10
                        and len(r_name) >= 10
                        and l_name[:14] == r_name[:14]
                    )
                    addr_hit = bool(r_addr_keys and l_akeys and (r_addr_keys & l_akeys))
                    if name_hit or (addr_hit and dist_lm <= 0.0014):
                        rp["survival_status"] = "surviving_era_structure"
                        lyr = int(lp.get("year_built") or 0)
                        rp["extant_year_built"] = lyr if lyr >= 1836 else rp["extant_year_built"]
                        rp["extant_building_name"] = (
                            sanitize_text(lp.get("building_name") or lp.get("name")) or rp["extant_building_name"]
                        )
                        lhcad = sanitize_text(lp.get("hcad_num"))
                        if lhcad:
                            rp["hcad_num"] = lhcad
                            rp["building_id"] = sanitize_text(lp.get("building_id") or lp.get("id")) or rp["building_id"]
                        if sanitize_text(lp.get("address")):
                            rp["modern_address"] = sanitize_text(lp.get("address"))
                        if name_hit or addr_hit:
                            r_feat["geometry"]["coordinates"] = [round(l_pt.x, 6), round(l_pt.y, 6)]
                        nrhp_hit = nrhp_by_hcad.get(rp["hcad_num"]) if rp["hcad_num"] else None
                        desig_list = [
                            "COH Protected Landmark" if lp.get("is_protected_landmark") else "COH Landmark"
                        ]
                        if nrhp_hit:
                            desig_list.append(
                                f"NRHP #{nrhp_hit.get('refnum')}" if nrhp_hit.get("refnum") else "NRHP Listed"
                            )
                        rp["landmark_designation"] = " & ".join(desig_list)
                        rp["survival_label"] = (
                            f"Surviving Designated Landmark ({lyr})" if lyr else "Surviving Designated Landmark Site"
                        )
                        hz_id = rp.get("holc_1937_zone_id")
                        if hz_id and hz_id in holc_id_to_idx:
                            holc_features[holc_id_to_idx[hz_id]]["properties"]["red_book_1915_surviving_count"] += 1
                        break

        survival_counts[rp["survival_status"]] += 1

    # Finalize HOLC polygon stats & citywide Grade A/B/C/D comparative summary
    grade_A_temps = [
        f["properties"]["af_temp_f"]
        for f in holc_features
        if f["properties"]["holc_grade"] == "A" and f["properties"]["af_temp_f"] is not None
    ]
    grade_A_mean_temp = round(sum(grade_A_temps) / len(grade_A_temps), 1) if grade_A_temps else 94.2

    grade_rollup: dict[str, dict] = {
        g: {
            "grade": g,
            "label": HOLC_GRADE_META[g]["label"],
            "color": HOLC_GRADE_META[g]["color"],
            "zone_count": 0,
            "area_acres": 0.0,
            "building_count": 0,
            "pre_1938_count": 0,
            "plm_count": 0,
            "vulnerable_lm_count": 0,
            "smls_block_count": 0,
            "red_book_1915_count": 0,
            "red_book_1915_surviving_count": 0,
            "_temp_weighted_sum": 0.0,
            "_temp_acres": 0.0,
        }
        for g in ("A", "B", "C", "D")
    }

    for h_idx, h_feat in enumerate(holc_features):
        hp = h_feat["properties"]
        st = holc_stats[h_idx]
        hp["building_count"] = st["count"]
        hp["pre_1938_count"] = st["pre1938"]
        hp["pre_1940_count"] = st["pre1940"]
        hp["post_1990_count"] = st["post1990"]
        if st["years"]:
            hp["earliest_year"] = min(st["years"])
            hp["median_year"] = int(round(statistics.median(st["years"])))
        if hp["af_temp_f"] is not None:
            hp["af_temp_delta_vs_a"] = round(hp["af_temp_f"] - grade_A_mean_temp, 1)
        else:
            hp["af_temp_delta_vs_a"] = None

        gr = grade_rollup[hp["holc_grade"]]
        gr["zone_count"] += 1
        gr["area_acres"] = round(gr["area_acres"] + hp["area_acres"], 1)
        gr["building_count"] += hp["building_count"]
        gr["pre_1938_count"] += hp["pre_1938_count"]
        gr["plm_count"] += hp["plm_count"]
        gr["vulnerable_lm_count"] += hp["vulnerable_lm_count"]
        gr["smls_block_count"] += hp["smls_block_count"]
        gr["red_book_1915_count"] += hp["red_book_1915_count"]
        gr["red_book_1915_surviving_count"] += hp["red_book_1915_surviving_count"]
        if hp["af_temp_f"] is not None:
            gr["_temp_weighted_sum"] += hp["af_temp_f"] * hp["area_acres"]
            gr["_temp_acres"] += hp["area_acres"]

    holc_summary_list = []
    for g in ("A", "B", "C", "D"):
        gr = grade_rollup[g]
        t_acres = gr.pop("_temp_acres")
        t_sum = gr.pop("_temp_weighted_sum")
        gr["mean_af_temp_f"] = round(t_sum / t_acres, 1) if t_acres > 0 else None
        holc_summary_list.append(gr)

    print(f"Scanned {total_scanned:,} countywide footprints.")
    print(f"1915 Red Book of Houston ({len(rb_features)} sites) survival breakdown: {dict(survival_counts)}")
    print("1937 HOLC Grade Rollup:")
    for row in holc_summary_list:
        print(" ", row)

    # -------------------------------------------------------------------------
    # 4. Write overlays.json
    # -------------------------------------------------------------------------
    overlays["red_book_1915"] = {"type": "FeatureCollection", "features": rb_features}
    overlays["holc_1937_zones"] = {"type": "FeatureCollection", "features": holc_features}
    overlays["holc_1937_grade_summary"] = holc_summary_list

    overlays_path.write_text(json.dumps(overlays, separators=(",", ":")))
    print(f"Updated overlays.json ({overlays_path.stat().st_size / 1e6:.2f} MB)")

    # -------------------------------------------------------------------------
    # 5. Idempotently Update search_index.json
    # -------------------------------------------------------------------------
    raw_si: list[dict] = json.loads(search_index_path.read_text())
    cleaned_si = [
        item
        for item in raw_si
        if not str(item.get("id") or "").startswith(("holc_1937_", "rb1915_"))
        and item.get("type") != "red_book_1915"
        and item.get("overlay_layer") != "holc_1937_zones"
    ]

    holc_search_entries: list[dict] = []
    for h_feat in holc_features:
        hp = h_feat["properties"]
        geom = shape(h_feat["geometry"])
        alts = [
            f"HOLC {hp['zone_code']}",
            f"HOLC Grade {hp['holc_grade']}",
            f"1937 Redlining {hp['area_title']}",
        ] + list(hp.get("top_neighborhoods") or [])
        holc_search_entries.append(
            {
                "id": hp["id"],
                "is_neighborhood_entry": True,
                "place_type": f"1937 HOLC {hp['grade_label']}",
                "overlay_layer": "holc_1937_zones",
                "name": hp["name"],
                "building_name": hp["name"],
                "alt_names": alts,
                "super_neighborhood": (hp["super_neighborhoods"][0] if hp.get("super_neighborhoods") else ""),
                "historic_ward": (hp["historic_wards"][0] if hp.get("historic_wards") else ""),
                "address": (
                    f"1937 HOLC {hp['grade_label']} · {hp['building_count']:,} structures · "
                    f"{hp['pre_1938_count']:,} pre-1938 standing · {hp['red_book_1915_count']} Red Book sites"
                ),
                "category": f"1937 HOLC Grade {hp['holc_grade']}",
                "year_built": 1937,
                "earliest_year": hp.get("earliest_year") or 0,
                "building_count": hp["building_count"],
                "pre_1940_count": hp["pre_1940_count"],
                "lng": hp["label_lng"],
                "lat": hp["label_lat"],
                "bbox": [round(v, 5) for v in geom.bounds],
            }
        )

    rb_search_entries: list[dict] = []
    for r_feat in rb_features:
        rp = r_feat["properties"]
        rc = r_feat["geometry"]["coordinates"]
        alts = ["1915 Red Book of Houston"]
        if rp.get("person_name") and rp["person_name"] != rp["name"]:
            alts.append(rp["person_name"])
        if rp.get("pastor_name") and rp["pastor_name"] not in alts:
            alts.append(rp["pastor_name"])
        if rp.get("historic_address_1915") and rp["historic_address_1915"] != rp.get("modern_address"):
            alts.append(rp["historic_address_1915"])
        if rp.get("lodges_clubs"):
            alts.append(rp["lodges_clubs"])

        cat_short_map = {
            "church": "1915 Red Book Church",
            "school": "1915 Red Book School",
            "business": "1915 Red Book Business",
            "residence": "1915 Red Book Leader / Home",
        }
        addr_disp = rp.get("historic_address_1915") or rp.get("modern_address") or "Houston, TX"
        if (
            rp.get("modern_address")
            and rp.get("historic_address_1915")
            and rp["modern_address"].lower() != rp["historic_address_1915"].lower()
        ):
            addr_disp = f"{rp['historic_address_1915']} (Modern: {rp['modern_address']})"

        rb_search_entries.append(
            {
                "type": "red_book_1915",
                "id": rp["id"],
                "hcad_num": rp.get("hcad_num") or "",
                "building_id": rp.get("building_id") or "",
                "red_book_category": rp["category"],
                "label": rp["name"],
                "building_name": rp["name"],
                "alt_names": alts,
                "address": addr_disp,
                "sublabel": f"{addr_disp} • {rp['subtitle']} • {rp['survival_label']}",
                "category": cat_short_map.get(rp["category"], "1915 Red Book"),
                "year_built": 1915,
                "historic_ward": rp.get("historic_ward") or "",
                "super_neighborhood": rp.get("super_neighborhood") or "",
                "survival_status": rp["survival_status"],
                "lon": rc[0],
                "lat": rc[1],
                "zoom": 17.6,
            }
        )

    insert_pos = 0
    for idx, item in enumerate(cleaned_si):
        if item.get("is_neighborhood_entry"):
            insert_pos = idx + 1
        else:
            break

    final_si = (
        cleaned_si[:insert_pos]
        + holc_search_entries
        + cleaned_si[insert_pos:]
        + rb_search_entries
    )
    search_index_path.write_text(json.dumps(final_si, separators=(",", ":")))
    print(
        f"Updated search_index.json ({search_index_path.stat().st_size / 1e6:.2f} MB): "
        f"{len(final_si):,} total searchable entries."
    )


if __name__ == "__main__":
    main()
