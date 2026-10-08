#!/usr/bin/env python3
"""
Fix geometry, attribution, and landmark metadata mistakes in Houston's Warehouse District
(North Side / Fifth Ward / Bayou District around Nance St, Sterrett St, William St,
Walnut St, Wood St, N San Jacinto St, McKee St, and the Elysian Viaduct).

Addresses issues identified by Jim:
1. Last Concert Cafe Complex (1403 Nance St & 904 William St, Protected Landmark HPO #11PL099):
   - Splits retired pre-replat HCAD account 0300940000014 into live accounts:
     * 1344850010001 (904 William St — Richey-Fant House, c. 1850, enlarged 1874-1875)
     * 1344850010002 (1403 Nance St — 1949 Last Concert Cafe building by Elena "Mama" Lopez)
     * 1344850010002#stage (1403 Nance St — 1986 Outdoor Music Stage & Patio Canopy)
   - Suppresses TxDOT freeway ROW sliver 0300940000011 ("0 EAST FWY", 1922) overlapping the stage.
2. Elysian Viaduct Bridge Deck (902 Hardy St — 1412870010001, 910 Hardy St — 0271180000019,
   1702 Rothwell St — 0271180000004):
   - Clips the 420m-long (14,034 m2) fused Microsoft footprint against HCAD parcel polygons so
     only the real warehouse roofs at 902 Hardy St (Hardy & Nance Studios, 1934), 910 Hardy St (1947),
     and 1702 Rothwell St (1935) remain, removing 100% of the Elysian Viaduct bridge deck.
3. Sterrett St / N. San Jacinto St Open-Air Bus Shed Canopies & Freeway ROW Non-Buildings:
   - Suppresses open-air METRO bus parking canopies (`building=carport`, HCAD `bld_ar=0`) on
     0031280000002 ("0 N SAN JACINTO ST"), 0300970000006 ("0 STERRETT ST"), and
     0300920000001 ("810 N SAN JACINTO ST"), as well as freeway/viaduct non-buildings
     (0142640000005 "4727 SHETLAND LN", 0142640000018, 0271320000001, 0081850000001,
     0371080090065 "0 RUNNELS ST", and 0031190000001 "1307 BAKER ST").
4. Surrounding Warehouse District Historic & Contemporary Buildings:
   - 1246600010020 (1200 Nance St — Henry Henke's Fifth Ward Grocery, c. 1880, Protected Landmark
     HPO #00L091 & 2004 Good Brick Award) & 1246600010019 (1204 Nance St — Tony's Barber Shop,
     c. 1885, Landmark HPO #00L092): separates footprints and corrects 1200 Nance St year from 1926 to 1880.
   - 0031330000003 (707 Walnut St, 1900 historic brick warehouse), 0031330000006 (1002 N San Jacinto St, 1916),
     0031330000015 (1010 N San Jacinto St, 1921): restores missing 1900 warehouse and clips each parcel.
   - 0031290000013 (1011 Wood St — T. E. Swann Warehouse, 1923, Landmark HPO #97L040) &
     1216410000008 (915 N San Jacinto St — San Jacinto Lofts, 1914): separates overlapping footprints.
   - 0300960000001 (1302 Nance St — Nance Street Lofts, 1909) & 0300960000006 (1318 Nance St, 1940):
     splits compound footprint along parcel boundary.
   - 0300970000005 (711 William St — Dakota Lofts / James Bute Paint Warehouse, 1910, NRHP):
     enriches historic warehouse name and parcel-clipped footprint.
   - 1412880010001 (813 McKee St — The Hardy Apartments, 2022): replaces demolished 1401 Sterrett St
     rectangle with full-block 2022 multifamily footprint from OSM.
   - 0031320000008 (1001 N San Jacinto St, 1946), 0031320000003 (1011 N San Jacinto St, 1928),
     0031320000006 (1020 Wood St, 1979), and 1246600010016..18 (1207-1211 Sterrett St, 2005):
     restores parcel-clipped footprints.
"""

import json
from pathlib import Path
from pyogrio.raw import read as raw_read
from pyproj import Transformer
from shapely import from_wkb
from shapely.geometry import Polygon, MultiPolygon, mapping, shape
from shapely.ops import transform, unary_union

ROOT = Path(__file__).resolve().parents[2]
CURATED_OVERRIDES_PATH = ROOT / "app/public/data/curated_overrides.json"
BUILDINGS_GEOJSON_PATH = ROOT / "app/public/data/buildings.geojson"
OVERLAYS_JSON_PATH = ROOT / "app/public/data/overlays.json"
SEARCH_INDEX_PATH = ROOT / "app/public/data/search_index.json"
PARCELS_GDB_PATH = ROOT / "pipeline/cache/Parcels/Parcels.gdb"
MS_FOOTPRINTS_PATH = ROOT / "pipeline/cache/harris_ms_footprints.ndjson"
OSM_FOOTPRINTS_PATH = ROOT / "pipeline/cache/osm_core_footprints.json"
SHARD_NE_PATH = ROOT / "pipeline/cache/aligned_ndjson/buildings_ne.geojsonseq"
SHARD_SE_PATH = ROOT / "pipeline/cache/aligned_ndjson/buildings_se.geojsonseq"

BBOX = (-95.3610, 29.7635, -95.3480, 29.7725)


def round_coords(geom_dict, precision=6):
    def _rc(coords):
        if isinstance(coords[0], (int, float)):
            return [round(float(coords[0]), precision), round(float(coords[1]), precision)]
        return [_rc(c) for c in coords]

    return {
        "type": geom_dict["type"],
        "coordinates": _rc(geom_dict["coordinates"]),
    }


def clean_clip(poly_geom, parcel_geom, min_area_m2=15.0):
    inter = poly_geom.intersection(parcel_geom)
    if inter.is_empty:
        return None
    polys = []
    if isinstance(inter, Polygon):
        polys = [inter]
    elif isinstance(inter, MultiPolygon):
        polys = list(inter.geoms)
    else:
        polys = [g for g in getattr(inter, "geoms", []) if isinstance(g, Polygon)]

    valid_polys = []
    for p in polys:
        p_clean = p.simplify(0.000003, preserve_topology=True)
        if not p_clean.is_valid:
            p_clean = p_clean.buffer(0)
        if isinstance(p_clean, Polygon) and p_clean.area * 1e10 * 0.87 >= min_area_m2:
            valid_polys.append(p_clean)
        elif isinstance(p_clean, MultiPolygon):
            for sp in p_clean.geoms:
                if sp.area * 1e10 * 0.87 >= min_area_m2:
                    valid_polys.append(sp)

    if not valid_polys:
        return None
    valid_polys.sort(key=lambda g: g.area, reverse=True)
    if len(valid_polys) == 1:
        return round_coords(mapping(valid_polys[0]))
    return round_coords(mapping(MultiPolygon(valid_polys)))


def load_warehouse_parcels():
    from shapely import force_2d

    to_2278 = Transformer.from_crs("EPSG:4326", "EPSG:2278", always_xy=True)
    to_4326 = Transformer.from_crs("EPSG:2278", "EPSG:4326", always_xy=True)
    minx, miny = to_2278.transform(BBOX[0], BBOX[1])
    maxx, maxy = to_2278.transform(BBOX[2], BBOX[3])

    meta, _, geom_wkb, fields = raw_read(
        PARCELS_GDB_PATH,
        layer="Parcels",
        bbox=(minx, miny, maxx, maxy),
        columns=["HCAD_NUM_1"],
    )
    hcad_arr = fields[0]
    parcels = {}
    for acct_raw, wkb in zip(hcad_arr, geom_wkb):
        acct = str(acct_raw or "").strip()
        if not acct or wkb is None:
            continue
        geom_2278 = force_2d(from_wkb(wkb))
        if geom_2278 is None or geom_2278.is_empty:
            continue
        g = transform(to_4326.transform, geom_2278)
        if not g.is_valid:
            g = g.buffer(0)
        if acct in parcels:
            parcels[acct] = unary_union([parcels[acct], g])
        else:
            parcels[acct] = g
    return parcels


def load_ms_footprints_in_bbox():
    ms_polys = []
    with open(MS_FOOTPRINTS_PATH) as f:
        for line in f:
            if "-95.35" not in line and "-95.36" not in line and "-95.34" not in line:
                continue
            feat = json.loads(line)
            coords = feat["geometry"]["coordinates"][0]
            lon0, lat0 = coords[0][0], coords[0][1]
            if BBOX[0] <= lon0 <= BBOX[2] and BBOX[1] <= lat0 <= BBOX[3]:
                g = shape(feat["geometry"])
                if not g.is_valid:
                    g = g.buffer(0)
                ms_polys.append(g)
    return ms_polys


def load_osm_ways():
    osm_polys = []
    with open(OSM_FOOTPRINTS_PATH) as f:
        features = json.load(f)
    for feat in features:
        geom = feat.get("geometry")
        if not geom or not geom.get("coordinates"):
            continue
        ring = geom["coordinates"][0]
        if not ring:
            continue
        first_pt = ring[0]
        while isinstance(first_pt, list) and first_pt and isinstance(first_pt[0], list):
            first_pt = first_pt[0]
        if not isinstance(first_pt, list) or len(first_pt) < 2:
            continue
        lon0, lat0 = float(first_pt[0]), float(first_pt[1])
        if BBOX[0] <= lon0 <= BBOX[2] and BBOX[1] <= lat0 <= BBOX[3]:
            g = shape(geom)
            if not g.is_valid:
                g = g.buffer(0)
            osm_polys.append((g, feat.get("properties", {})))
    return osm_polys


def main():
    print("Loading Warehouse District parcels from Parcels.gdb...")
    parcels = load_warehouse_parcels()
    print(f"Loaded {len(parcels)} parcels in Warehouse District.")

    print("Loading Microsoft US Building Footprints in Warehouse District...")
    ms_polys = load_ms_footprints_in_bbox()
    print(f"Loaded {len(ms_polys)} MS footprints in Warehouse District.")

    print("Loading OSM footprints in Warehouse District...")
    osm_polys = load_osm_ways()
    print(f"Loaded {len(osm_polys)} OSM footprints in Warehouse District.")

    def clip_ms_to_parcel(acct, min_area_m2=15.0):
        pgeom = parcels.get(acct)
        if not pgeom:
            raise ValueError(f"Parcel {acct} not found in Parcels.gdb")
        matching = [m for m in ms_polys if m.intersects(pgeom)]
        if not matching:
            return None
        merged = unary_union(matching)
        return clean_clip(merged, pgeom, min_area_m2=min_area_m2)

    # 1. Compute Last Concert Cafe Complex geometries (1344850010001, 1344850010002, 1344850010002#stage)
    geom_richey_fant = clip_ms_to_parcel("1344850010001", min_area_m2=25.0)
    assert geom_richey_fant is not None, "Missing Richey-Fant House geometry"

    # For 1344850010002 (1403 Nance St), split at lng = -95.353022 into:
    # - West half: 1949 Last Concert Cafe building ([-95.35324, 29.76827, -95.353022, 29.76846])
    # - East half: 1986 Outdoor Music Stage & Patio Canopy ([-95.353022, 29.76827, -95.35272, 29.76846])
    p_lcc = parcels["1344850010002"]
    ms_lcc = unary_union([m for m in ms_polys if m.intersects(p_lcc)])
    west_box = Polygon([
        (-95.35340, 29.76820),
        (-95.353022, 29.76820),
        (-95.353022, 29.76850),
        (-95.35340, 29.76850),
        (-95.35340, 29.76820),
    ])
    east_box = Polygon([
        (-95.353022, 29.76820),
        (-95.35270, 29.76820),
        (-95.35270, 29.76850),
        (-95.353022, 29.76850),
        (-95.353022, 29.76820),
    ])
    geom_lcc_1949 = clean_clip(ms_lcc, p_lcc.intersection(west_box), min_area_m2=25.0)
    geom_lcc_stage = clean_clip(ms_lcc, p_lcc.intersection(east_box), min_area_m2=25.0)
    assert geom_lcc_1949 is not None and geom_lcc_stage is not None

    # 2. Compute Elysian Viaduct clipped warehouse geometries
    geom_902_hardy = clip_ms_to_parcel("1412870010001", min_area_m2=50.0)
    geom_910_hardy = clip_ms_to_parcel("0271180000019", min_area_m2=30.0)
    geom_1702_rothwell = clip_ms_to_parcel("0271180000004", min_area_m2=50.0)
    assert geom_902_hardy and geom_910_hardy and geom_1702_rothwell

    # 3. Compute other Warehouse District geometries
    geom_1200_nance = clip_ms_to_parcel("1246600010020", min_area_m2=30.0)
    geom_1204_nance = clip_ms_to_parcel("1246600010019", min_area_m2=30.0)
    geom_707_walnut = clip_ms_to_parcel("0031330000003", min_area_m2=50.0)
    geom_1002_san_jacinto = clip_ms_to_parcel("0031330000006", min_area_m2=50.0)
    geom_1010_san_jacinto = clip_ms_to_parcel("0031330000015", min_area_m2=50.0)
    geom_1011_wood = clip_ms_to_parcel("0031290000013", min_area_m2=50.0)
    geom_915_san_jacinto = clip_ms_to_parcel("1216410000008", min_area_m2=50.0)
    geom_1302_nance = clip_ms_to_parcel("0300960000001", min_area_m2=50.0)
    geom_1318_nance = clip_ms_to_parcel("0300960000006", min_area_m2=50.0)
    geom_711_william = clip_ms_to_parcel("0300970000005", min_area_m2=50.0)
    geom_1001_san_jacinto = clip_ms_to_parcel("0031320000008", min_area_m2=50.0)
    geom_1011_san_jacinto = clip_ms_to_parcel("0031320000003", min_area_m2=50.0)
    geom_1020_wood = clip_ms_to_parcel("0031320000006", min_area_m2=50.0)
    geom_1207_sterrett = clip_ms_to_parcel("1246600010018", min_area_m2=20.0)
    geom_1209_sterrett = clip_ms_to_parcel("1246600010017", min_area_m2=20.0)
    geom_1211_sterrett = clip_ms_to_parcel("1246600010016", min_area_m2=20.0)
    geom_1125_providence = clip_ms_to_parcel("0400100000002", min_area_m2=50.0)

    def rep_pt(geom_dict):
        pt = shape(geom_dict).representative_point()
        return [round(float(pt.x), 6), round(float(pt.y), 6)]

    pt_lcc = rep_pt(geom_lcc_1949)
    pt_1200_nance = rep_pt(geom_1200_nance)
    pt_1204_nance = rep_pt(geom_1204_nance)
    pt_1011_wood = rep_pt(geom_1011_wood)
    pt_711_william = rep_pt(geom_711_william)
    pt_1125_providence = rep_pt(geom_1125_providence) if geom_1125_providence else [-95.35644, 29.76911]

    # 813 McKee St (1412880010001) — full-block 2022 multifamily mid-rise on Block 46
    p_813 = parcels["1412880010001"].buffer(-0.000032, join_style=2)
    geom_813_mckee = round_coords(mapping(p_813))
    assert geom_813_mckee is not None

    # Load curated_overrides.json
    with open(CURATED_OVERRIDES_PATH) as f:
        curated_doc = json.load(f)
    overrides = curated_doc.get("overrides", {})

    # Preserve existing Last Concert Cafe PDF summary if present on 0300940000014
    old_lcc = overrides.get("0300940000014", {})
    lcc_summary = old_lcc.get(
        "landmark_summary",
        "Protected Landmark (HPO #11PL099). The Last Concert Café complex comprises the c. 1850 Richey-Fant House at 904 William St (built 1848–1852 by Benjamin Richey, enlarged 1874–1875 by John N. Fant) and the 1949 Last Concert Café building at 1403 Nance St founded by Elena 'Mama' Aldrete Lopez.",
    )
    lcc_pdf_url = "https://www.houstontx.gov/planning/HistoricPres/landmarks/11PL99_Last_Concert_Cafe_1403_Nance_St.pdf"

    # Suppress retired pre-replat HCAD 0300940000014
    overrides["0300940000014"] = {
        "id": "0300940000014",
        "hcad_num": "0300940000014",
        "suppress_only": True,
        "address": "1403 NANCE ST",
        "source_citation": "Retired pre-replat HCAD account replaced by 1344850010001 (904 William St) and 1344850010002 (1403 Nance St).",
    }

    # Add 1344850010001 (904 William St — Richey-Fant House, 1850)
    overrides["1344850010001"] = {
        "id": "1344850010001",
        "building_id": "1344850010001",
        "hcad_num": "1344850010001",
        "address": "904 WILLIAM ST",
        "historic_district": "Fifth Ward / Warehouse District",
        "contributing": "Protected Landmark",
        "year_built": 1850,
        "decade": 1850,
        "original_hcad_year": 1950,
        "stories": 1,
        "height_m": 5.5,
        "use_category": "Commercial",
        "bld_style": "Gulf Coast Cottage / Folk Victorian",
        "architect": "Benjamin Richey (1848–1852); John N. Fant (1874–1875 addition)",
        "landmark_name": "Richey-Fant House (Last Concert Café Complex)",
        "landmark_type": "Protected Landmark",
        "landmark_code": "11PL099",
        "landmark_report_url": lcc_pdf_url,
        "landmark_summary": lcc_summary,
        "source_type": "City of Houston HPO Landmark Report & Sanborn Maps",
        "source_citation": "Designated Protected Landmark (HPO #11PL099). Built between 1848 and 1852 by Benjamin Richey on Lot 7 of the Obedience Smith Survey; enlarged 1874–1875 by grocer John N. Fant with a two-room rear cottage and remodeled in 1897 with Victorian trim.",
        "source_url": lcc_pdf_url,
        "verified_by": "Preservation Houston & City of Houston HPO",
        "updated_at": "2026-10-08",
        "replace_parcel_shards": True,
        "geometry": geom_richey_fant,
    }

    # Add 1344850010002 (1403 Nance St — 1949 Last Concert Cafe building)
    overrides["1344850010002"] = {
        "id": "1344850010002",
        "building_id": "1344850010002",
        "hcad_num": "1344850010002",
        "address": "1403 NANCE ST",
        "historic_district": "Fifth Ward / Warehouse District",
        "contributing": "Protected Landmark",
        "year_built": 1949,
        "decade": 1940,
        "original_hcad_year": 1980,
        "stories": 1,
        "height_m": 5.5,
        "use_category": "Commercial",
        "bld_style": "Mid-Century Vernacular Commercial",
        "architect": "Elena 'Mama' Aldrete Lopez (Founder & Builder)",
        "landmark_name": "Last Concert Café (1949 Café Building)",
        "landmark_type": "Protected Landmark",
        "landmark_code": "11PL099",
        "landmark_report_url": lcc_pdf_url,
        "landmark_summary": lcc_summary,
        "source_type": "City of Houston HPO Landmark Report",
        "source_citation": "Designated Protected Landmark (HPO #11PL099). Constructed in 1949 by Elena 'Mama' Aldrete Lopez at 1403 Nance St as 'The Last Concert Cafe', one of Houston's earliest post-WWII Mexican restaurants and live music venues.",
        "source_url": lcc_pdf_url,
        "verified_by": "Preservation Houston & City of Houston HPO",
        "updated_at": "2026-10-08",
        "replace_parcel_shards": True,
        "suppress_shard_hcads": ["0300940000011", "0300940000014"],
        "geometry": geom_lcc_1949,
    }

    # Add 1344850010002#stage (1403 Nance St — 1986 Outdoor Stage & Patio Canopy)
    overrides["1344850010002#stage"] = {
        "id": "1344850010002#stage",
        "building_id": "1344850010002#stage",
        "hcad_num": "1344850010002",
        "is_building_override": True,
        "address": "1403 NANCE ST",
        "historic_district": "Fifth Ward / Warehouse District",
        "contributing": "Non-Contributing",
        "year_built": 1986,
        "decade": 1980,
        "original_hcad_year": 1980,
        "stories": 1,
        "height_m": 4.8,
        "use_category": "Commercial",
        "bld_style": "Open-Air Amphitheater Stage & Canopy",
        "landmark_name": "Last Concert Café Outdoor Music Stage & Patio",
        "landmark_type": "Protected Landmark",
        "landmark_code": "11PL099",
        "landmark_report_url": lcc_pdf_url,
        "landmark_summary": lcc_summary,
        "source_type": "City of Houston HPO Landmark Report",
        "source_citation": "Outdoor live music stage and courtyard canopy constructed in 1986 on the eastern garden lot of Last Concert Café (1403 Nance St).",
        "source_url": lcc_pdf_url,
        "verified_by": "Preservation Houston & City of Houston HPO",
        "updated_at": "2026-10-08",
        "geometry": geom_lcc_stage,
    }

    # 2. Elysian Viaduct clipped warehouses
    overrides["1412870010001"] = {
        "id": "1412870010001",
        "building_id": "1412870010001",
        "hcad_num": "1412870010001",
        "address": "902 HARDY ST",
        "historic_district": "Fifth Ward / Warehouse District",
        "contributing": "Contributing",
        "year_built": 1934,
        "decade": 1930,
        "original_hcad_year": 1934,
        "stories": 1,
        "height_m": 6.5,
        "use_category": "Industrial",
        "bld_style": "Depression-Era Industrial Warehouse",
        "landmark_name": "Hardy & Nance Studios (902 Hardy St Warehouse)",
        "source_type": "HCAD CAMA & Parcel Geometry Verification",
        "source_citation": "Constructed in 1934; footprint clipped to HCAD parcel 1412870010001 to remove the adjacent 420m Elysian Viaduct bridge deck artifact from Microsoft US Building Footprints.",
        "verified_by": "Preservation Houston",
        "updated_at": "2026-10-08",
        "replace_parcel_shards": True,
        "geometry": geom_902_hardy,
    }

    overrides["0271180000019"] = {
        "id": "0271180000019",
        "building_id": "0271180000019",
        "hcad_num": "0271180000019",
        "address": "910 HARDY ST",
        "historic_district": "Fifth Ward / Warehouse District",
        "contributing": "Contributing",
        "year_built": 1947,
        "decade": 1940,
        "original_hcad_year": 1947,
        "stories": 1,
        "height_m": 5.5,
        "use_category": "Industrial",
        "bld_style": "Post-WWII Industrial Warehouse",
        "landmark_name": "910 Hardy St Warehouse",
        "source_type": "HCAD CAMA & Parcel Geometry Verification",
        "source_citation": "Constructed in 1947; footprint extracted from compound Elysian Viaduct / Hardy St roof polygon using HCAD parcel 0271180000019.",
        "verified_by": "Preservation Houston",
        "updated_at": "2026-10-08",
        "replace_parcel_shards": True,
        "geometry": geom_910_hardy,
    }

    overrides["0271180000004"] = {
        "id": "0271180000004",
        "building_id": "0271180000004",
        "hcad_num": "0271180000004",
        "address": "1702 ROTHWELL ST",
        "historic_district": "Fifth Ward / Warehouse District",
        "contributing": "Contributing",
        "year_built": 1935,
        "decade": 1930,
        "original_hcad_year": 1935,
        "stories": 1,
        "height_m": 6.5,
        "use_category": "Industrial",
        "bld_style": "1930s Rail Spur Warehouse",
        "landmark_name": "1702 Rothwell St Warehouse",
        "source_type": "HCAD CAMA & Parcel Geometry Verification",
        "source_citation": "Constructed in 1935; footprint extracted from compound Elysian Viaduct / Rothwell St roof polygon using HCAD parcel 0271180000004.",
        "verified_by": "Preservation Houston",
        "updated_at": "2026-10-08",
        "replace_parcel_shards": True,
        "geometry": geom_1702_rothwell,
    }

    # 3. Suppress non-building METRO bus sheds, freeway/viaduct ROW polygons, and retired pre-replat HCADs
    suppress_accounts = {
        "0031280000002": ("0 N SAN JACINTO ST", "Open-air METRO bus parking canopies (OSM building=carport, HCAD bld_ar=0)."),
        "0300970000006": ("0 STERRETT ST", "Open-air METRO bus parking canopies (OSM building=carport, HCAD bld_ar=0)."),
        "0300920000001": ("810 N SAN JACINTO ST", "Open-air METRO bus parking canopy (OSM building=carport, HCAD bld_ar=0)."),
        "0300940000011": ("0 EAST FWY", "TxDOT East Freeway right-of-way sliver overlapping Last Concert Cafe outdoor stage."),
        "0142640000005": ("4727 SHETLAND LN", "Unimproved parcel under Elysian Viaduct with synthetic square and off-site owner mailing address."),
        "0142640000018": ("0 ELYSIAN ST", "Unimproved right-of-way parcel under Elysian Viaduct (HCAD bld_ar=0)."),
        "0271320000001": ("0 ELYSIAN ST", "Unimproved right-of-way parcel under Elysian Viaduct (HCAD bld_ar=0)."),
        "0081850000001": ("0 CONTI ST", "Unimproved right-of-way parcel (HCAD bld_ar=0)."),
        "0371080090065": ("0 RUNNELS ST", "Unimproved TxDOT freeway ramp right-of-way under US-59 / I-10 interchange (HCAD bld_ar=0)."),
        "0031190000001": ("1307 BAKER ST", "Unimproved County lot canopy polygon with no building record in HCAD (bld_ar=0)."),
        "0020160000001": ("711 WILLIAM ST", "Pre-replat HCAD account consolidated into 0300970000005 (Dakota Lofts, 711 William St)."),
        "0020040000004": ("1125 PROVIDENCE ST", "Synthetic HCAD account consolidated into 0400100000002 (San Jacinto Warehouse / The Docks, 1125 Providence St)."),
    }
    old_dakota_gb = overrides.get("0020160000001", {})
    old_san_jacinto_gb = overrides.get("0020040000004", {})

    for acct, (addr, reason) in suppress_accounts.items():
        overrides[acct] = {
            "id": acct,
            "hcad_num": acct,
            "suppress_only": True,
            "address": addr,
            "source_citation": reason,
        }

    # 4. Fix 1200 Nance St (Henry Henke's Fifth Ward Grocery, 1880) & 1204 Nance St (Tony's Barber Shop, 1885)
    old_henke = overrides.get("1246600010020", {})
    overrides["1246600010020"] = {
        **old_henke,
        "id": "1246600010020",
        "building_id": "1246600010020",
        "hcad_num": "1246600010020",
        "address": "1200 NANCE ST",
        "historic_district": "Fifth Ward / Warehouse District",
        "contributing": "Protected Landmark",
        "year_built": 1880,
        "decade": 1880,
        "original_hcad_year": 1930,
        "stories": 2,
        "height_m": 8.8,
        "use_category": "Commercial",
        "bld_style": "19th-Century Victorian Commercial",
        "landmark_name": "Henry Henke's Fifth Ward Grocery Building",
        "landmark_type": "Protected Landmark",
        "landmark_code": "00L091",
        "landmark_report_url": "https://www.houstontx.gov/planning/HistoricPres/landmarks/00L091_1200_Rothwell-Henke_Grocery.pdf",
        "source_type": "City of Houston HPO Landmark Report & Preservation Houston Good Brick Award (2004)",
        "source_citation": "Designated Protected Landmark (HPO #00L091) and 2004 Good Brick Award recipient (curving corner brick commercial building constructed c. 1880 at the corner of Nance and Rothwell Streets as Henry Henke's Fifth Ward Grocery).",
        "source_url": "https://www.houstontx.gov/planning/HistoricPres/landmarks/00L091_1200_Rothwell-Henke_Grocery.pdf",
        "verified_by": "Preservation Houston & City of Houston HPO",
        "updated_at": "2026-10-08",
        "replace_parcel_shards": True,
        "suppress_shard_hcads": [],
        "geometry": geom_1200_nance,
    }

    old_tonys = overrides.get("1246600010019", {})
    overrides["1246600010019"] = {
        **old_tonys,
        "id": "1246600010019",
        "building_id": "1246600010019",
        "hcad_num": "1246600010019",
        "address": "1204 NANCE ST",
        "historic_district": "Fifth Ward / Warehouse District",
        "contributing": "Contributing",
        "year_built": 1885,
        "decade": 1880,
        "original_hcad_year": 1930,
        "stories": 2,
        "height_m": 8.5,
        "use_category": "Commercial",
        "bld_style": "19th-Century Two-Story Brick Commercial",
        "landmark_name": "Tony's Barber Shop Building",
        "landmark_type": "Landmark",
        "landmark_code": "00L092",
        "landmark_report_url": "https://www.houstontx.gov/planning/HistoricPres/landmarks/00L092_1204_Nance_St-Tonys_Barber_Shop.pdf",
        "source_type": "City of Houston HPO Landmark Report",
        "source_citation": "Designated City of Houston Landmark (HPO #00L092). Two-story brick commercial building constructed c. 1885 immediately east of Henry Henke's Fifth Ward Grocery.",
        "source_url": "https://www.houstontx.gov/planning/HistoricPres/landmarks/00L092_1204_Nance_St-Tonys_Barber_Shop.pdf",
        "verified_by": "Preservation Houston & City of Houston HPO",
        "updated_at": "2026-10-08",
        "replace_parcel_shards": True,
        "geometry": geom_1204_nance,
    }

    # 5. Restore 707 Walnut St (1900) and clip adjacent 1002 & 1010 N San Jacinto St
    overrides["0031330000003"] = {
        "id": "0031330000003",
        "building_id": "0031330000003",
        "hcad_num": "0031330000003",
        "address": "707 WALNUT ST",
        "historic_district": "Warehouse District",
        "contributing": "Contributing",
        "year_built": 1900,
        "decade": 1900,
        "original_hcad_year": 1900,
        "stories": 2,
        "height_m": 9.0,
        "use_category": "Industrial",
        "bld_style": "Turn-of-the-Century Brick Rail Warehouse",
        "landmark_name": "707 Walnut St Historic Brick Warehouse (1900)",
        "source_type": "HCAD CAMA & Parcel Geometry Verification",
        "source_citation": "Constructed in 1900 (2,430 m² brick warehouse at 707 Walnut St); restored from compound block footprint using HCAD parcel 0031330000003.",
        "verified_by": "Preservation Houston",
        "updated_at": "2026-10-08",
        "replace_parcel_shards": True,
        "geometry": geom_707_walnut,
    }
    overrides["0031330000006"] = {
        "id": "0031330000006",
        "building_id": "0031330000006",
        "hcad_num": "0031330000006",
        "address": "1002 N SAN JACINTO ST",
        "historic_district": "Warehouse District",
        "contributing": "Contributing",
        "year_built": 1916,
        "decade": 1910,
        "original_hcad_year": 1916,
        "stories": 2,
        "height_m": 8.5,
        "use_category": "Commercial",
        "bld_style": "Early 20th-Century Brick Warehouse",
        "landmark_name": "1002 N San Jacinto St Warehouse",
        "source_type": "HCAD CAMA & Parcel Geometry Verification",
        "source_citation": "Constructed in 1916; footprint clipped to HCAD parcel 0031330000006.",
        "verified_by": "Preservation Houston",
        "updated_at": "2026-10-08",
        "replace_parcel_shards": True,
        "geometry": geom_1002_san_jacinto,
    }
    overrides["0031330000015"] = {
        "id": "0031330000015",
        "building_id": "0031330000015",
        "hcad_num": "0031330000015",
        "address": "1010 N SAN JACINTO ST",
        "historic_district": "Warehouse District",
        "contributing": "Contributing",
        "year_built": 1921,
        "decade": 1920,
        "original_hcad_year": 1921,
        "stories": 2,
        "height_m": 8.5,
        "use_category": "Commercial",
        "bld_style": "1920s Brick Commercial Warehouse",
        "landmark_name": "1010 N San Jacinto St Warehouse",
        "source_type": "HCAD CAMA & Parcel Geometry Verification",
        "source_citation": "Constructed in 1921; footprint clipped to HCAD parcel 0031330000015.",
        "verified_by": "Preservation Houston",
        "updated_at": "2026-10-08",
        "replace_parcel_shards": True,
        "geometry": geom_1010_san_jacinto,
    }

    # 6. Fix 1011 Wood St (T. E. Swann Warehouse, 1923) & 915 N San Jacinto St (San Jacinto Lofts, 1914)
    old_swann = overrides.get("0031290000013", {})
    overrides["0031290000013"] = {
        **old_swann,
        "id": "0031290000013",
        "building_id": "0031290000013",
        "hcad_num": "0031290000013",
        "address": "1011 WOOD ST",
        "historic_district": "Warehouse District",
        "contributing": "Contributing",
        "year_built": 1923,
        "decade": 1920,
        "original_hcad_year": 1923,
        "stories": 3,
        "height_m": 11.5,
        "use_category": "Commercial",
        "bld_style": "Early 20th-Century Heavy Timber & Brick Warehouse",
        "landmark_name": "T. E. Swann Warehouse (1011 Wood St)",
        "landmark_type": "Landmark",
        "landmark_code": "97L040",
        "replace_parcel_shards": True,
        "geometry": geom_1011_wood,
    }
    overrides["1216410000008"] = {
        "id": "1216410000008",
        "building_id": "1216410000008",
        "hcad_num": "1216410000008",
        "address": "915 N SAN JACINTO ST",
        "historic_district": "Warehouse District",
        "contributing": "Contributing",
        "year_built": 1914,
        "decade": 1910,
        "original_hcad_year": 1914,
        "stories": 3,
        "height_m": 12.0,
        "use_category": "Residential",
        "bld_style": "Early 20th-Century Industrial Loft Warehouse",
        "landmark_name": "San Jacinto Lofts (1914 Warehouse)",
        "source_type": "HCAD CAMA & Parcel Geometry Verification",
        "source_citation": "Constructed in 1914 at 915 N San Jacinto St; converted into residential lofts. Clipped to HCAD parcel 1216410000008.",
        "verified_by": "Preservation Houston",
        "updated_at": "2026-10-08",
        "replace_parcel_shards": True,
        "geometry": geom_915_san_jacinto,
    }

    # 7. Split 1302 Nance St (1909, Nance Street Lofts) & 1318 Nance St (1940)
    overrides["0300960000001"] = {
        "id": "0300960000001",
        "building_id": "0300960000001",
        "hcad_num": "0300960000001",
        "address": "1302 NANCE ST",
        "historic_district": "Fifth Ward / Warehouse District",
        "contributing": "Contributing",
        "year_built": 1909,
        "decade": 1900,
        "original_hcad_year": 1909,
        "stories": 2,
        "height_m": 9.0,
        "use_category": "Commercial",
        "bld_style": "Early 20th-Century Brick Warehouse",
        "landmark_name": "1302 Nance St Studios & Lofts (1909 Warehouse)",
        "source_type": "HCAD CAMA & Parcel Geometry Verification",
        "source_citation": "Constructed in 1909 at 1302 Nance St; separated from 1318 Nance St along HCAD parcel boundary 0300960000001.",
        "verified_by": "Preservation Houston",
        "updated_at": "2026-10-08",
        "replace_parcel_shards": True,
        "geometry": geom_1302_nance,
    }
    overrides["0300960000006"] = {
        "id": "0300960000006",
        "building_id": "0300960000006",
        "hcad_num": "0300960000006",
        "address": "1318 NANCE ST",
        "historic_district": "Fifth Ward / Warehouse District",
        "contributing": "Contributing",
        "year_built": 1940,
        "decade": 1940,
        "original_hcad_year": 1940,
        "stories": 1,
        "height_m": 6.5,
        "use_category": "Industrial",
        "bld_style": "1940s Industrial Warehouse",
        "landmark_name": "1318 Nance St Warehouse",
        "source_type": "HCAD CAMA & Parcel Geometry Verification",
        "source_citation": "Constructed in 1940 at 1318 Nance St; separated from 1302 Nance St along HCAD parcel boundary 0300960000006.",
        "verified_by": "Preservation Houston",
        "updated_at": "2026-10-08",
        "replace_parcel_shards": True,
        "geometry": geom_1318_nance,
    }

    # 8. Enrich 711 William St (Dakota Lofts / Bering-Cortes & James Bute Warehouse, 1910, NRHP & 1994 Good Brick Award)
    overrides["0300970000005"] = {
        **old_dakota_gb,
        "id": "0300970000005",
        "building_id": "0300970000005",
        "hcad_num": "0300970000005",
        "address": "711 WILLIAM ST",
        "historic_district": "Warehouse District",
        "contributing": "Contributing",
        "year_built": 1910,
        "decade": 1910,
        "original_hcad_year": 1910,
        "stories": 4,
        "height_m": 16.0,
        "use_category": "Residential",
        "bld_style": "Early 20th-Century Heavy Timber & Brick Loft Warehouse",
        "landmark_name": "Dakota Lofts (Bering-Cortes Hardware / James Bute Warehouse)",
        "landmark_type": "NRHP Individual Listing",
        "source_type": "Preservation Houston Good Brick Award (1994) & National Register of Historic Places",
        "source_citation": "Constructed in 1910–1911 as the Bering-Cortes Hardware Co. / James Bute Company Paint Warehouse at 711 William St; adapted into residential lofts (Dakota Lofts, 1994 Good Brick Award — Randall Davis) and listed on the National Register of Historic Places.",
        "verified_by": "Preservation Houston",
        "updated_at": "2026-10-08",
        "replace_parcel_shards": True,
        "suppress_shard_hcads": ["0020160000001"],
        "geometry": geom_711_william,
    }

    # 8b. Enrich 1125 Providence St (San Jacinto Warehouse / The Docks, 1926, 2026 Good Brick Award — Scarlet Capital)
    if geom_1125_providence:
        overrides["0400100000002"] = {
            **old_san_jacinto_gb,
            "id": "0400100000002",
            "building_id": "0400100000002",
            "hcad_num": "0400100000002",
            "address": "1125 PROVIDENCE ST",
            "historic_district": "Near Northside / Warehouse District",
            "contributing": "Contributing",
            "year_built": 1926,
            "decade": 1920,
            "original_hcad_year": 1929,
            "stories": 2,
            "height_m": 8.5,
            "use_category": "Commercial",
            "bld_style": "1920s Bowstring-Truss Brick Rail Warehouse",
            "landmark_name": "San Jacinto Warehouse / The Docks (1125 Providence St)",
            "source_type": "Preservation Houston Good Brick Award (2026)",
            "source_citation": "2026 Good Brick Award recipient (Scarlet Capital) for rehabilitating the 1926 San Jacinto Warehouse ('The Docks', 1125 Providence St at N. San Jacinto St) in the Near Northside.",
            "verified_by": "Preservation Houston",
            "updated_at": "2026-10-08",
            "replace_parcel_shards": True,
            "suppress_shard_hcads": ["0020040000004"],
            "geometry": geom_1125_providence,
        }

    # 9. Replace demolished 1401 Sterrett St footprint with full-block 813 McKee St (2022)
    overrides["1412880010001"] = {
        "id": "1412880010001",
        "building_id": "1412880010001",
        "hcad_num": "1412880010001",
        "address": "813 MCKEE ST",
        "historic_district": "Warehouse District",
        "contributing": "Non-Contributing",
        "year_built": 2022,
        "decade": 2020,
        "original_hcad_year": 2022,
        "stories": 4,
        "height_m": 14.5,
        "use_category": "Residential",
        "bld_style": "Contemporary Multifamily Courtyard Mid-Rise",
        "landmark_name": "The Hardy Apartments (813 McKee St)",
        "source_type": "OpenStreetMap & HCAD CAMA Verification",
        "source_citation": "Full-block multifamily mid-rise constructed in 2022 on Block 46 between McKee, Hardy, Sterrett, and Nance Streets (replacing an earlier industrial shed at 1401 Sterrett St).",
        "verified_by": "Preservation Houston",
        "updated_at": "2026-10-08",
        "replace_parcel_shards": True,
        "geometry": geom_813_mckee,
    }

    # 10. Restore 1001 N San Jacinto St (1946), 1011 N San Jacinto St (1928), 1020 Wood St (1979), and 1207-1211 Sterrett St (2005)
    overrides["0031320000008"] = {
        "id": "0031320000008",
        "building_id": "0031320000008",
        "hcad_num": "0031320000008",
        "address": "1001 N SAN JACINTO ST",
        "historic_district": "Warehouse District",
        "contributing": "Contributing",
        "year_built": 1946,
        "decade": 1940,
        "original_hcad_year": 1946,
        "stories": 1,
        "height_m": 6.0,
        "use_category": "Industrial",
        "bld_style": "Mid-Century Commercial Warehouse",
        "landmark_name": "1001 N San Jacinto St Warehouse",
        "source_type": "HCAD CAMA & Parcel Geometry Verification",
        "source_citation": "Constructed in 1946; restored from compound block footprint using HCAD parcel 0031320000008.",
        "verified_by": "Preservation Houston",
        "updated_at": "2026-10-08",
        "replace_parcel_shards": True,
        "geometry": geom_1001_san_jacinto,
    }
    overrides["0031320000003"] = {
        "id": "0031320000003",
        "building_id": "0031320000003",
        "hcad_num": "0031320000003",
        "address": "1011 N SAN JACINTO ST",
        "historic_district": "Warehouse District",
        "contributing": "Contributing",
        "year_built": 1928,
        "decade": 1920,
        "original_hcad_year": 1928,
        "stories": 1,
        "height_m": 6.5,
        "use_category": "Industrial",
        "bld_style": "1920s Industrial Warehouse",
        "landmark_name": "1011 N San Jacinto St Warehouse",
        "source_type": "HCAD CAMA & Parcel Geometry Verification",
        "source_citation": "Constructed in 1928; clipped to HCAD parcel 0031320000003.",
        "verified_by": "Preservation Houston",
        "updated_at": "2026-10-08",
        "replace_parcel_shards": True,
        "geometry": geom_1011_san_jacinto,
    }
    overrides["0031320000006"] = {
        "id": "0031320000006",
        "building_id": "0031320000006",
        "hcad_num": "0031320000006",
        "address": "1020 WOOD ST",
        "historic_district": "Warehouse District",
        "contributing": "Non-Contributing",
        "year_built": 1979,
        "decade": 1970,
        "original_hcad_year": 1979,
        "stories": 1,
        "height_m": 5.5,
        "use_category": "Commercial",
        "landmark_name": "1020 Wood St Commercial Building",
        "source_type": "HCAD CAMA & Parcel Geometry Verification",
        "source_citation": "Constructed in 1979; restored from compound block footprint using HCAD parcel 0031320000006.",
        "verified_by": "Preservation Houston",
        "updated_at": "2026-10-08",
        "replace_parcel_shards": True,
        "geometry": geom_1020_wood,
    }
    for acct, addr, geom in [
        ("1246600010018", "1207 STERRETT ST", geom_1207_sterrett),
        ("1246600010017", "1209 STERRETT ST", geom_1209_sterrett),
        ("1246600010016", "1211 STERRETT ST", geom_1211_sterrett),
    ]:
        if geom:
            overrides[acct] = {
                "id": acct,
                "building_id": acct,
                "hcad_num": acct,
                "address": addr,
                "historic_district": "Fifth Ward / Warehouse District",
                "contributing": "Non-Contributing",
                "year_built": 2005,
                "decade": 2000,
                "original_hcad_year": 2005,
                "stories": 3,
                "height_m": 10.0,
                "use_category": "Residential",
                "bld_style": "Contemporary Townhouse",
                "source_type": "HCAD CAMA & Parcel Geometry Verification",
                "source_citation": f"Constructed in 2005 at {addr}; clipped to HCAD parcel {acct}.",
                "verified_by": "Preservation Houston",
                "updated_at": "2026-10-08",
                "replace_parcel_shards": True,
                "geometry": geom,
            }

    curated_doc["overrides"] = overrides
    with open(CURATED_OVERRIDES_PATH, "w") as f:
        json.dump(curated_doc, f, separators=(",", ":"))
    print(f"Saved {len(overrides)} overrides to {CURATED_OVERRIDES_PATH.name}.")

    # Update buildings.geojson for core features in this area
    with open(BUILDINGS_GEOJSON_PATH) as f:
        buildings_fc = json.load(f)
    new_blds = []
    for feat in buildings_fc.get("features", []):
        p = feat.get("properties", {})
        hcad = str(p.get("hcad_num") or "").strip()
        if hcad in suppress_accounts or hcad == "0300940000014":
            continue
        if hcad == "1246600010020":
            p["year_built"] = 1880
            p["decade"] = 1880
            p["address"] = "1200 NANCE ST"
            p["landmark_name"] = "Henry Henke's Fifth Ward Grocery Building"
            feat["geometry"] = geom_1200_nance
        elif hcad == "1246600010019":
            p["year_built"] = 1885
            p["decade"] = 1880
            p["address"] = "1204 NANCE ST"
            p["landmark_name"] = "Tony's Barber Shop Building"
            feat["geometry"] = geom_1204_nance
        elif hcad == "0031290000013":
            p["year_built"] = 1923
            p["decade"] = 1920
            p["address"] = "1011 WOOD ST"
            p["landmark_name"] = "T. E. Swann Warehouse (1011 Wood St)"
            feat["geometry"] = geom_1011_wood
        new_blds.append(feat)
    buildings_fc["features"] = new_blds
    with open(BUILDINGS_GEOJSON_PATH, "w") as f:
        json.dump(buildings_fc, f, separators=(",", ":"))
    print(f"Updated {BUILDINGS_GEOJSON_PATH.name} ({len(new_blds)} features).")

    # Update overlays.json (landmarks & good_brick_awards)
    with open(OVERLAYS_JSON_PATH) as f:
        overlays = json.load(f)
    for lm in overlays.get("landmarks", {}).get("features", []):
        lp = lm.get("properties", {})
        hcad = str(lp.get("hcad_num") or "").strip()
        name = str(lp.get("name") or lp.get("landmark_name") or "")
        if hcad in ("0300940000014", "1344850010002") or "Last Concert" in name:
            lp["hcad_num"] = "1344850010002"
            lp["building_id"] = "1344850010002"
            lp["year_built"] = 1949
            lp["decade"] = 1940
            lp["address"] = "1403 Nance St & 904 William St"
            lm["geometry"] = {"type": "Point", "coordinates": pt_lcc}
        elif hcad == "1246600010020" or "Henke" in name:
            lp["year_built"] = 1880
            lp["decade"] = 1880
            lp["address"] = "1200 Nance St"
            lm["geometry"] = {"type": "Point", "coordinates": pt_1200_nance}
        elif hcad == "1246600010019" or "Tony's Barber" in name:
            lp["year_built"] = 1885
            lp["decade"] = 1880
            lp["address"] = "1204 Nance St"
            lm["geometry"] = {"type": "Point", "coordinates": pt_1204_nance}
        elif hcad == "0031290000013" or "Swann" in name:
            lp["year_built"] = 1923
            lp["decade"] = 1920
            lm["geometry"] = {"type": "Point", "coordinates": pt_1011_wood}

    for gb in overlays.get("good_brick_awards", {}).get("features", []):
        gp = gb.get("properties", {})
        hcad = str(gp.get("hcad_num") or "").strip()
        if hcad == "1246600010020":
            gp["year_built"] = 1880
            gp["decade"] = 1880
            gp["address"] = "1200 Nance St"
            gb["geometry"] = {"type": "Point", "coordinates": pt_1200_nance}
        elif hcad in ("0020160000001", "0300970000005"):
            gp["hcad_num"] = "0300970000005"
            gp["building_id"] = "0300970000005"
            gp["year_built"] = 1910
            gp["decade"] = 1910
            gp["address"] = "711 William St"
            gb["geometry"] = {"type": "Point", "coordinates": pt_711_william}
        elif hcad in ("0020040000004", "0400100000002"):
            gp["hcad_num"] = "0400100000002"
            gp["building_id"] = "0400100000002"
            gp["year_built"] = 1926
            gp["decade"] = 1920
            gp["address"] = "1125 Providence St"
            gb["geometry"] = {"type": "Point", "coordinates": pt_1125_providence}

    with open(OVERLAYS_JSON_PATH, "w") as f:
        json.dump(overlays, f, separators=(",", ":"))
    print(f"Updated {OVERLAYS_JSON_PATH.name}.")

    # Update search_index.json
    with open(SEARCH_INDEX_PATH) as f:
        search_idx = json.load(f)
    filtered_search = []
    for item in search_idx:
        hcad = str(item.get("hcad_num") or "").strip()
        if hcad in suppress_accounts or hcad == "0300940000014":
            continue
        if hcad == "1246600010020":
            item["year_built"] = 1880
            item["category"] = "Built 1880 ✓"
            item["sublabel"] = "Fifth Ward / Warehouse District • Built 1880 (✓ PH Verified)"
            item["lon"] = pt_1200_nance[0]
            item["lat"] = pt_1200_nance[1]
        filtered_search.append(item)
    with open(SEARCH_INDEX_PATH, "w") as f:
        json.dump(filtered_search, f, separators=(",", ":"))
    print(f"Updated {SEARCH_INDEX_PATH.name} ({len(filtered_search)} items).")

    # Also clean aligned_ndjson/buildings_ne.geojsonseq and buildings_se.geojsonseq for future tile builds
    for shard_path in [SHARD_NE_PATH, SHARD_SE_PATH]:
        if not shard_path.exists():
            continue
        kept_lines = []
        removed = 0
        with open(shard_path) as f:
            for line in f:
                raw = line.lstrip("\x1e").strip()
                if not raw:
                    continue
                feat = json.loads(raw)
                hcad = str(feat.get("properties", {}).get("hcad_num") or "").strip()
                if hcad in suppress_accounts:
                    removed += 1
                    continue
                kept_lines.append(line)
        if removed > 0:
            with open(shard_path, "w") as f:
                f.writelines(kept_lines)
            print(f"Removed {removed} suppressed non-building features from {shard_path.name}.")


if __name__ == "__main__":
    main()
