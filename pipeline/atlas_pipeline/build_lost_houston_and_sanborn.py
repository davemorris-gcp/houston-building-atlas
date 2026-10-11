"""Builds app/public/data/sanborn_catalog.json and overlays.json['lost_houston'] (plus search_index & building_photos)."""

from __future__ import annotations

import json
import math
from pathlib import Path
from lost_houston_catalog import LOST_HOUSTON_PART_1
from lost_houston_catalog_2 import LOST_HOUSTON_PART_2

ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / "pipeline" / "cache"
DATA_DIR = ROOT / "app" / "public" / "data"

LOST_HOUSTON_PART_3 = [
    {
        "id": "lost_sweeney_coombs_fredericks_1891",
        "name": "Sweeney, Coombs & Fredericks Building (1891)",
        "subtitle": "Victorian Corner-Turret Jewelry & Opera Emporium at 301 Main St",
        "category": "Early Skyscraper & Commercial",
        "address": "301 Main St at Congress Ave (Block 32)",
        "neighborhood": "Downtown",
        "year_built": 1891,
        "year_demolished": 1956,
        "demolition_cause": "Razed in the 1950s after a fire and mid-century commercial remodeling on the 300 block of Main Street.",
        "replaced_by": "301 Main Street commercial block",
        "site_hcad_nums": ["0010320000004"],
        "architect": "Eugene T. Heiner",
        "style": "High Victorian Italianate / Queen Anne with Corner Clock Turret",
        "sanborn_material": "brick",
        "sanborn_material_label": "Pink - Three-Story Brick Commercial & Jewelry House",
        "stories": 3,
        "height_m": 16.5,
        "sanborn_ref": "1896 Sheet 8; 1907 Vol. 1 Sheet 7 (301 Main St, Block 32)",
        "loc_volume_id": "sanborn08580_004",
        "loc_sheet_num": 7,
        "lon": -95.36055,
        "lat": 29.76230,
        "width_m": 20.0,
        "depth_m": 32.0,
        "angle": 35.0,
        "shape": "rect",
        "blurb": "Before constructing the extant 1922 Carter-designed Stowers/Sweeney building, jewelers J. J. Sweeney and E. L. Coombs anchored the corner of Main and Congress with this turreted 1891 Victorian landmark.",
    },
    {
        "id": "lost_mistrot_emporium_1904",
        "name": "G. A. Mistrot & Bros. Dry Goods Emporium (1904)",
        "subtitle": "Market Square Department Store Destroyed in the Great 1912 Fire",
        "category": "Early Skyscraper & Commercial",
        "address": "910 Congress Ave at Travis/Milam (Market Square South Side)",
        "neighborhood": "Downtown",
        "year_built": 1904,
        "year_demolished": 1912,
        "demolition_cause": "Destroyed in the spectacular May 19, 1912 Market Square conflagration.",
        "replaced_by": "Market Square commercial buildings (900 Congress Ave)",
        "site_hcad_nums": ["0010340000001"],
        "architect": "Olle J. Lorehn",
        "style": "Romanesque Revival Five-Story Brick Wholesale House",
        "sanborn_material": "brick",
        "sanborn_material_label": "Pink - Five-Story Brick Dry Goods & Wholesale Emporium",
        "stories": 5,
        "height_m": 22.0,
        "sanborn_ref": "1907 Vol. 1 Sheet 6 (Congress & Travis opposite City Hall Market)",
        "loc_volume_id": "sanborn08580_004",
        "loc_sheet_num": 6,
        "lon": -95.36265,
        "lat": 29.76210,
        "width_m": 28.0,
        "depth_m": 36.0,
        "angle": 35.0,
        "shape": "rect",
        "blurb": "Facing the 1904 City Hall & Market House on Market Square, Mistrot Bros. was the largest wholesale and retail dry-goods house in South Texas until a catastrophic fire destroyed the building on May 19, 1912.",
    },
    {
        "id": "lost_turnverein_hall_1908",
        "name": "Houston Turn-Verein Social & Athletic Hall (1908)",
        "subtitle": "German-Texan Gymnasium, Ballroom & Bowling Club on Prairie Avenue",
        "category": "Neighborhood & Cultural Landmark",
        "address": "1314 Prairie Ave at Austin St",
        "neighborhood": "Downtown",
        "year_built": 1908,
        "year_demolished": 1949,
        "demolition_cause": "Sold after the Turn-Verein moved to Almeda Road in 1929; downtown hall demolished for parking and commercial uses in 1949.",
        "replaced_by": "1300 Prairie Ave Downtown block",
        "site_hcad_nums": ["1353640010001"],
        "architect": "Olle J. Lorehn",
        "style": "German Renaissance Revival Brick & Stone",
        "sanborn_material": "brick",
        "sanborn_material_label": "Pink - Three-Story Brick Club, Gymnasium & Stage",
        "stories": 3,
        "height_m": 16.0,
        "sanborn_ref": "1907 Vol. 1 Sheet 14 / 1924 Vol. 1 (Prairie & Austin)",
        "loc_volume_id": "sanborn08580_004",
        "loc_sheet_num": 14,
        "lon": -95.35920,
        "lat": 29.75910,
        "width_m": 28.0,
        "depth_m": 36.0,
        "angle": 35.0,
        "shape": "rect",
        "blurb": "Founded in 1854 by German forty-eighter immigrants, the Houston Turn-Verein (Houston's oldest civic and athletic club) operated its grand downtown gymnasium, theater stage, and beer garden on Prairie Avenue before moving to South Main/Almeda.",
    },
    {
        "id": "lost_concordia_club_1903",
        "name": "The Concordia Club (1903)",
        "subtitle": "Historic Jewish Social & Cultural Clubhouse on Main Street",
        "category": "Neighborhood & Cultural Landmark",
        "address": "1217 Main St at Polk Ave",
        "neighborhood": "Downtown",
        "year_built": 1903,
        "year_demolished": 1938,
        "demolition_cause": "Demolished as South Main commercial retail blocks replaced early-20th-century clubhouses.",
        "replaced_by": "1201 Fannin / Main Street Greenstreet block",
        "site_hcad_nums": ["0012610000009"],
        "architect": "R. D. Steele",
        "style": "Beaux-Arts / Classical Revival Brick & Veranda",
        "sanborn_material": "brick",
        "sanborn_material_label": "Pink - Three-Story Brick Clubhouse & Ballroom",
        "stories": 3,
        "height_m": 15.0,
        "sanborn_ref": "1907 Vol. 1 Sheet 34 (Main & Polk)",
        "loc_volume_id": "sanborn08580_004",
        "loc_sheet_num": 34,
        "lon": -95.36580,
        "lat": 29.75465,
        "width_m": 24.0,
        "depth_m": 32.0,
        "angle": 35.0,
        "shape": "rect",
        "blurb": "Mapped on Sheet 34 of the 1907 Sanborn Atlas at Main and Polk, the Concordia Club was the premier social and cultural gathering place of Houston's early-20th-century Jewish community.",
    },
    {
        "id": "lost_temple_beth_israel_1908",
        "name": "1908 Temple Beth Israel Sanctuary (Crawford & Lamar)",
        "subtitle": "Classical Dome Sanctuary of Texas's Oldest Jewish Congregation (1908-1925)",
        "category": "Neighborhood & Cultural Landmark",
        "address": "1101 Crawford St at Lamar Ave",
        "neighborhood": "Downtown",
        "year_built": 1908,
        "year_demolished": 1954,
        "demolition_cause": "Vacated when Congregation Beth Israel built its extant 1925 sanctuary at 3517 Austin Street (now Heinen Theatre) and later razed for Downtown parking/Discovery Green precinct.",
        "replaced_by": "Discovery Green / Crawford St corridor",
        "site_hcad_nums": ["0011020000001"],
        "architect": "Sanguinet & Staats",
        "style": "Beaux-Arts / Byzantine Domed Brick & Limestone",
        "sanborn_material": "brick",
        "sanborn_material_label": "Pink/Blue - Domed Brick & Stone Synagogue Sanctuary",
        "stories": 2,
        "height_m": 18.0,
        "sanborn_ref": "1907-1924 Vol. 1 Sheet 28 (Crawford & Lamar)",
        "loc_volume_id": "sanborn08580_004",
        "loc_sheet_num": 28,
        "lon": -95.35960,
        "lat": 29.75560,
        "width_m": 26.0,
        "depth_m": 34.0,
        "angle": 35.0,
        "shape": "cross",
        "blurb": "After outgrowing its 1870 Franklin Avenue synagogue, Congregation Beth Israel (founded 1854) constructed this domed Beaux-Arts temple at Crawford and Lamar before moving south to Holman and Austin in 1925.",
    },
    {
        "id": "lost_first_presbyterian_main_mckinney_1896",
        "name": "First Presbyterian Church (Main & McKinney, 1896)",
        "subtitle": "Gothic Stone & Brick Spire Burned in the 1932 Downtown Fire",
        "category": "Neighborhood & Cultural Landmark",
        "address": "917 Main St at McKinney Ave (Block 134)",
        "neighborhood": "Downtown",
        "year_built": 1896,
        "year_demolished": 1932,
        "demolition_cause": "Destroyed by a dramatic five-alarm fire in 1932; congregation relocated to 5300 Main Street in the Museum District.",
        "replaced_by": "901-919 Main Street / Woolworth & Bank block",
        "site_hcad_nums": ["0011340000021"],
        "architect": "Cram, Goodhue & Ferguson / Olle J. Lorehn",
        "style": "High Victorian Gothic Revival Brick & Stone with Bell Spire",
        "sanborn_material": "brick",
        "sanborn_material_label": "Pink/Blue - Brick & Stone Sanctuary with 140-Foot Spire",
        "stories": 3,
        "height_m": 32.0,
        "sanborn_ref": "1907 Vol. 1 Sheet 25 (Block 134, Main & McKinney)",
        "loc_volume_id": "sanborn08580_004",
        "loc_sheet_num": 25,
        "lon": -95.36385,
        "lat": 29.75685,
        "width_m": 32.0,
        "depth_m": 44.0,
        "angle": 35.0,
        "shape": "L",
        "blurb": "Standing on Block 134 at Main and McKinney directly across from the Lamar Hotel and Metropolitan Theatre, the 1896 First Presbyterian Church anchored Downtown's church row until a 1932 fire prompted the congregation's move to the Museum District.",
    },
    {
        "id": "lost_shearn_methodist_church_1884",
        "name": "Shearn Memorial Methodist Episcopal Church (1884 & 1909)",
        "subtitle": "Historic Sanctuary of Texas's Largest Methodist Congregation Before First Methodist (1320 Main)",
        "category": "Neighborhood & Cultural Landmark",
        "address": "901 Texas Ave at Travis/Milam",
        "neighborhood": "Downtown",
        "year_built": 1884,
        "year_demolished": 1911,
        "demolition_cause": "Sold as commercial land values soared on Texas Avenue to finance the extant 1910 First United Methodist Church at 1320 Main Street.",
        "replaced_by": "Texas Avenue commercial skyscrapers",
        "site_hcad_nums": ["0010590000001"],
        "architect": "Eugene T. Heiner",
        "style": "Victorian Gothic Brick with Twin Towers",
        "sanborn_material": "brick",
        "sanborn_material_label": "Pink - Brick Sanctuary with Timber Truss Roof",
        "stories": 2,
        "height_m": 24.0,
        "sanborn_ref": "1885 Sheet 4; 1896 Sheet 15; 1907 Vol. 1 Sheet 11 (Texas Ave & Milam)",
        "loc_volume_id": "sanborn08580_004",
        "loc_sheet_num": 11,
        "lon": -95.36355,
        "lat": 29.76075,
        "width_m": 26.0,
        "depth_m": 38.0,
        "angle": 35.0,
        "shape": "rect",
        "blurb": "Named for Republic-era Houston merchant and benefactor Charles Shearn, this Victorian brick sanctuary on Texas Avenue appears on the 1885, 1896, and 1907 Sanborn maps before the congregation moved to 1320 Main Street.",
    },
    {
        "id": "lost_fifth_ward_cotton_compress_1892",
        "name": "Inman-Nelms / Standard Cotton Compress & Warehouse (1892)",
        "subtitle": "Sprawling Steam Cotton Compress Complex Featured on Sanborn Special Sheets",
        "category": "Early Skyscraper & Commercial",
        "address": "N. San Jacinto & Lyons Ave / H&TC Railroad Spur (Fifth Ward)",
        "neighborhood": "Fifth Ward / Near Northside",
        "year_built": 1892,
        "year_demolished": 1962,
        "demolition_cause": "Cleared during post-WWII containerization and I-10 East Freeway construction.",
        "replaced_by": "I-10 East Freeway / Warehouse District",
        "site_hcad_nums": [],
        "architect": "Webb Steam Cotton Compress Co.",
        "style": "Brick Fire-Wall Cotton Sheds & High-Pressure Steam Compress",
        "sanborn_material": "brick",
        "sanborn_material_label": "Pink/Yellow - Brick Fire-Divided Cotton Platforms & Steam Boilers",
        "stories": 2,
        "height_m": 11.0,
        "sanborn_ref": "1896 Sheet 65; 1907 Vol. 2 Sheet 68 (Cotton Compress Special Risk)",
        "loc_volume_id": "sanborn08580_005",
        "loc_sheet_num": 68,
        "lon": -95.34950,
        "lat": 29.77280,
        "width_m": 125.0,
        "depth_m": 68.0,
        "angle": 0.0,
        "shape": "rect",
        "blurb": "Because cotton fires were the single greatest underwriting hazard in Texas, Sanborn cartographers drew Houston's massive steam cotton compresses in extraordinary detail, noting every fire hydrant, night watchman clock, and brick firewall.",
    },
]


def make_footprint_polygon(
    lon: float,
    lat: float,
    width_m: float,
    depth_m: float,
    angle_deg: float = 35.0,
    shape: str = "rect",
) -> list[list[list[float]]]:
    """Generates a surveyed architectural footprint polygon (rect, L, U, T, H, cross) aligned to street grid."""
    hw = width_m / 2.0
    hd = depth_m / 2.0
    if shape == "L":
        pts_m = [
            (-hw, -hd),
            (hw, -hd),
            (hw, -hd * 0.22),
            (-hw * 0.18, -hd * 0.22),
            (-hw * 0.18, hd),
            (-hw, hd),
        ]
    elif shape == "U":
        pts_m = [
            (-hw, -hd),
            (hw, -hd),
            (hw, hd),
            (hw * 0.42, hd),
            (hw * 0.42, -hd * 0.28),
            (-hw * 0.42, -hd * 0.28),
            (-hw * 0.42, hd),
            (-hw, hd),
        ]
    elif shape == "T":
        pts_m = [
            (-hw, hd * 0.28),
            (-hw * 0.32, hd * 0.28),
            (-hw * 0.32, -hd),
            (hw * 0.32, -hd),
            (hw * 0.32, hd * 0.28),
            (hw, hd * 0.28),
            (hw, hd),
            (-hw, hd),
        ]
    elif shape == "H":
        pts_m = [
            (-hw, -hd),
            (-hw * 0.36, -hd),
            (-hw * 0.36, -hd * 0.22),
            (hw * 0.36, -hd * 0.22),
            (hw * 0.36, -hd),
            (hw, -hd),
            (hw, hd),
            (hw * 0.36, hd),
            (hw * 0.36, hd * 0.22),
            (-hw * 0.36, hd * 0.22),
            (-hw * 0.36, hd),
            (-hw, hd),
        ]
    elif shape == "cross":
        pts_m = [
            (-hw * 0.36, -hd),
            (hw * 0.36, -hd),
            (hw * 0.36, -hd * 0.36),
            (hw, -hd * 0.36),
            (hw, hd * 0.36),
            (hw * 0.36, hd * 0.36),
            (hw * 0.36, hd),
            (-hw * 0.36, hd),
            (-hw * 0.36, hd * 0.36),
            (-hw, hd * 0.36),
            (-hw, -hd * 0.36),
            (-hw * 0.36, -hd * 0.36),
        ]
    else:
        pts_m = [(-hw, -hd), (hw, -hd), (hw, hd), (-hw, hd)]

    rad = math.radians(angle_deg)
    cos_a = math.cos(rad)
    sin_a = math.sin(rad)
    m_per_deg_lat = 110850.0
    m_per_deg_lon = 111320.0 * math.cos(math.radians(lat))

    ring = []
    for x, y in pts_m:
        rx = x * cos_a - y * sin_a
        ry = x * sin_a + y * cos_a
        plon = round(lon + rx / m_per_deg_lon, 6)
        plat = round(lat + ry / m_per_deg_lat, 6)
        ring.append([plon, plat])
    ring.append(ring[0])
    return [ring]


def build_sanborn_catalog() -> dict:
    """Builds app/public/data/sanborn_catalog.json from cached LOC data."""
    raw_path = CACHE_DIR / "loc_sanborn_raw.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))

    iiif_templates = {
        "sanborn08580_001": ("1885", "August 1885", "1885 Edition (15 Sheets - Original Downtown & Bayou Wharves)", "service:gmd:gmd403m:g4034m:g4034hm:g4034hm_g085801885:08580_1885-{sheet:04d}"),
        "sanborn08580_002": ("1890", "June 1890", "1890 Edition (33 Sheets - Victorian Houston, Wards 1-5)", "service:gmd:gmd403m:g4034m:g4034hm:g4034hm_g085801890:08580_1890-{sheet:04d}"),
        "sanborn08580_003": ("1896", "1896", "1896 Edition (81 Sheets - Gilded Age Expansion & Houston Heights)", "service:gmd:gmd403m:g4034m:g4034hm:g4034hm_g085801896:08580_1896-{sheet:04d}"),
        "sanborn08580_004": ("1907", "1907 (Vol. 1)", "1907 Edition, Volume 1 (111 Sheets - Downtown, North Side & East End)", "service:gmd:gmd403m:g4034m:g4034hm:g4034hm_g08580190701:08580_01_1907-{sheet:04d}"),
        "sanborn08580_005": ("1907", "1907 (Vol. 2)", "1907 Edition, Volume 2 (84 Sheets - South End, Fourth Ward, Third Ward & Heights)", "service:gmd:gmd403m:g4034m:g4034hm:g4034hm_g08580190702:08580_02_1907-{sheet:04d}"),
        "sanborn08580_015": ("1925-1946", "1925-Dec 1946 (Vol. 12)", "1925-1946 Revision, Volume 12 (67 Sheets - Inner Loop Expansion)", "service:gmd:gmd403m:g4034m:g4034hm:g4034hm_g08580194612:08580_12_1946-{sheet:04d}"),
        "sanborn08580_016": ("1925-1950", "1925-1950 (Vol. 15)", "1925-1950 Revision, Volume 15 (74 Sheets - Ship Channel & Industrial)", "service:gmd:gmd403m:g4034m:g4034hm:g4034hm_g08580195015:08580_15_1950-{sheet:04d}"),
        "sanborn08580_027": ("1924-1950", "1924-Nov 1950 (Vol. 11)", "1924-1950 Revision, Volume 11 (48 Sheets - South Main, Medical Center & Shamrock)", "service:gmd:gmd403m:g4034m:g4034hm:g4034hm_g08580195011:08580_11_1950-{sheet:04d}"),
        "sanborn08580_028": ("1925-1950", "1925-Nov 1950 (Vol. 12)", "1925-1950 Revision, Volume 12 (72 Sheets - Residential Subdivisions)", "service:gmd:gmd403m:g4034m:g4034hm:g4034hm_g08580195012:08580_12_1950-{sheet:04d}"),
        "sanborn08580_030": ("1924-1955", "1924-1955 Supplement", "1924-1955 Supplemental Volume (44 Sheets - Post-WWII Growth)", "service:gmd:gmd403m:g4034m:g4034hm:g4034hm_g085801955:08580_1955-{sheet:04d}"),
    }

    featured_sheets = {
        "sanborn08580_001": [
            {"sheet": 1, "label": "Index & Title Sheet (Aug 1885)"},
            {"sheet": 2, "label": "Sheet 2: Market Square, 1876 City Hall & Hutchins House"},
            {"sheet": 3, "label": "Sheet 3: 1883 Victorian Courthouse & Quality Hill"},
            {"sheet": 4, "label": "Sheet 4: 1883 Capitol Hotel &Pillot's Opera House"},
            {"sheet": 6, "label": "Sheet 6: Houston Academy & Incarnate Word Convent"},
        ],
        "sanborn08580_002": [
            {"sheet": 1, "label": "Index Map & Key (June 1890)"},
            {"sheet": 2, "label": "Sheet 2: Grand Central Station (H&TC Ry)"},
            {"sheet": 4, "label": "Sheet 4: Market Square & 1876 Market House"},
            {"sheet": 6, "label": "Sheet 6: Courthouse Square & Congress Ave"},
            {"sheet": 8, "label": "Sheet 8: Capitol Hotel & Main Street Core"},
        ],
        "sanborn08580_003": [
            {"sheet": 1, "label": "Index Map & Key (1896)"},
            {"sheet": 7, "label": "Sheet 7: Market Square & 1876 City Hall"},
            {"sheet": 10, "label": "Sheet 10: 1883 Courthouse & Quality Hill Mansions"},
            {"sheet": 14, "label": "Sheet 14: Capitol Hotel & Pillot's Opera House"},
            {"sheet": 15, "label": "Sheet 15: 1895 Binz Building & Shearn Methodist"},
            {"sheet": 34, "label": "Sheet 34: Scanlan Mansion (1917 Main St)"},
        ],
        "sanborn08580_004": [
            {"sheet": 1, "label": "Sheet 1: Key Map, H&TC Grand Central Depot & Hotel Brazos"},
            {"sheet": 2, "label": "Sheet 2: Magnolia Brewery & Foot of Main Street"},
            {"sheet": 3, "label": "Sheet 3: 1905 First National Bank (8-Story Steel Frame)"},
            {"sheet": 6, "label": "Sheet 6: 1904 City Hall & Market House + Mistrot Bros."},
            {"sheet": 7, "label": "Sheet 7: 1893 Kiam Building & Sweeney-Coombs"},
            {"sheet": 9, "label": "Sheet 9: 1883 Victorian Courthouse (Before 1910 Rebuild)"},
            {"sheet": 12, "label": "Sheet 12: 1883 Capitol Hotel & 1895 Binz Building"},
            {"sheet": 20, "label": "Sheet 20: Central High School (Block 88)"},
            {"sheet": 25, "label": "Sheet 25: First Presbyterian Church (Main & McKinney)"},
            {"sheet": 26, "label": "Sheet 26: 1904 Houston Lyceum & Carnegie Library"},
            {"sheet": 43, "label": "Sheet 43: Frost Town ('El Barrio del Alacran') on Buffalo Bayou"},
            {"sheet": 46, "label": "Sheet 46: I&GN Railroad Passenger Depot (Congress Ave)"},
        ],
        "sanborn08580_005": [
            {"sheet": 1, "label": "Sheet 1: Vol. 2 Graphic Index & Key"},
            {"sheet": 68, "label": "Sheet 68: Fifth Ward Steam Cotton Compress Special Sheet"},
            {"sheet": 79, "label": "Sheet 79: West End Park ('Base Ball Park', Andrews & Heiner)"},
            {"sheet": 80, "label": "Sheet 80: Colored High School (Fourth Ward)"},
            {"sheet": 101, "label": "Sheet 101: T. H. Scanlan Mansion (1917 Main St)"},
            {"sheet": 105, "label": "Sheet 105: 1894 St. Joseph's Infirmary (Nicholas J. Clayton)"},
        ],
    }

    existing_sheets_by_vid = {}
    existing_catalog_path = DATA_DIR / "sanborn_catalog.json"
    if existing_catalog_path.exists():
        try:
            existing_cat = json.loads(existing_catalog_path.read_text(encoding="utf-8"))
            for ev in existing_cat.get("volumes", []):
                if ev.get("id") and ev.get("sheets"):
                    existing_sheets_by_vid[ev["id"]] = ev["sheets"]
        except Exception:
            pass

    volumes = []
    for item in raw.get("volumes", []):
        vid = item["id"]
        if vid not in iiif_templates:
            continue
        era_short, date_str, title_str, tmpl = iiif_templates[vid]
        sheets = existing_sheets_by_vid.get(vid, [])
        count = len(sheets) if sheets else item["sheet_count"]
        vol_obj = {
            "id": vid,
            "era": era_short,
            "date": date_str,
            "title": title_str,
            "sheet_count": count,
            "loc_url": item["loc_url"],
            "iiif_template": f"https://tile.loc.gov/image-services/iiif/{tmpl}/full/pct:12.5/0/default.jpg",
            "iiif_highres_template": f"https://tile.loc.gov/image-services/iiif/{tmpl}/full/pct:25/0/default.jpg",
            "featured_sheets": featured_sheets.get(
                vid,
                [{"sheet": 1, "label": "Sheet 1: Volume Title & Graphic Index Map"}],
            ),
        }
        if sheets:
            vol_obj["sheets"] = sheets
        volumes.append(vol_obj)

    return {
        "title": "Library of Congress & UT PCL Digitized Houston Sanborn Fire Insurance Atlases (1885-1955)",
        "total_digitized_sheets": sum(v["sheet_count"] for v in volumes),
        "volumes": volumes,
        "volume_boundaries": raw.get("volume_extents", []),
        "underwriters_color_key": [
            {"code": "wood_frame", "color": "#e9c46a", "stroke": "#b5830e", "label": "Yellow - Wood Frame / Timber Construction"},
            {"code": "brick", "color": "#e07a7a", "stroke": "#a83232", "label": "Pink - Brick / Masonry Bearing Wall"},
            {"code": "stone", "color": "#6baed6", "stroke": "#2b6cb0", "label": "Blue - Cut Stone / Concrete Block"},
            {"code": "iron_metal", "color": "#9e9ac8", "stroke": "#5e548e", "label": "Gray - Iron / Clad Metal / Structural Steel"},
            {"code": "fireproof", "color": "#8ab17d", "stroke": "#3f6a34", "label": "Olive - Fireproof Steel & Reinforced Concrete"},
        ],
    }


def main() -> None:
    # 1. Write sanborn_catalog.json
    sanborn_catalog = build_sanborn_catalog()
    sanborn_path = DATA_DIR / "sanborn_catalog.json"
    sanborn_path.write_text(json.dumps(sanborn_catalog, indent=2), encoding="utf-8")
    print(f"Wrote {sanborn_path} ({len(sanborn_catalog['volumes'])} volumes, {sanborn_catalog['total_digitized_sheets']} sheets)")

    # 2. Build overlays.json['lost_houston']
    all_entries = LOST_HOUSTON_PART_1 + LOST_HOUSTON_PART_2 + LOST_HOUSTON_PART_3
    features = []
    photos_path = DATA_DIR / "building_photos.json"
    photos_db = json.loads(photos_path.read_text(encoding="utf-8")) if photos_path.exists() else {"photos": []}
    # Clean any accidental top-level lost_* keys and existing lost_* entries in photos_db["photos"]
    for k in list(photos_db.keys()):
        if k.startswith("lost_"):
            del photos_db[k]
    photo_list = [
        p for p in photos_db.get("photos", [])
        if not str(p.get("building_id", "")).startswith("lost_")
    ]

    for entry in all_entries:
        coords = make_footprint_polygon(
            lon=entry["lon"],
            lat=entry["lat"],
            width_m=entry["width_m"],
            depth_m=entry["depth_m"],
            angle_deg=entry.get("angle", 35.0),
            shape=entry.get("shape", "rect"),
        )
        loc_vol = entry.get("loc_volume_id")
        loc_sheet = entry.get("loc_sheet_num")
        sanborn_sheet_image_url = None
        sanborn_sheet_hires_url = None
        sanborn_sheet_loc_url = None
        if loc_vol and loc_sheet:
            for v in sanborn_catalog["volumes"]:
                if v["id"] == loc_vol:
                    v_sheets = v.get("sheets", [])
                    matched_s = next((s for s in v_sheets if s.get("sp") == loc_sheet), None)
                    if matched_s:
                        sanborn_sheet_image_url = matched_s["preview_url"]
                        sanborn_sheet_hires_url = matched_s["hires_url"]
                    else:
                        sanborn_sheet_image_url = v.get("iiif_highres_template", v["iiif_template"]).format(sheet=loc_sheet)
                        sanborn_sheet_hires_url = sanborn_sheet_image_url
                    sanborn_sheet_loc_url = f"https://www.loc.gov/resource/{loc_vol}.?sp={loc_sheet}"
                    break

        photo_url = entry.get("photo_url") or sanborn_sheet_image_url
        photo_credit = entry.get("photo_credit") or (
            f"Library of Congress Sanborn Map ({entry.get('sanborn_ref')})" if sanborn_sheet_image_url else None
        )
        yb = int(entry["year_built"])
        yd = int(entry["year_demolished"])
        site_hcads = entry.get("site_hcad_nums", [])
        primary_hcad = site_hcads[0] if site_hcads else ""

        props = {
            "id": entry["id"],
            "overlay_layer": "lost_houston",
            "is_boundary_feature": True,
            "feature_type": "lost_houston",
            "is_demolished": True,
            "name": entry["name"],
            "building_name": entry["name"],
            "subtitle": entry["subtitle"],
            "type_label": f"Lost Houston · {entry['category']} ({yb}-{yd})",
            "lifespan_label": f"{yb}-{yd}",
            "lifespan_years": max(1, yd - yb),
            "category": entry["category"],
            "address": entry["address"],
            "neighborhood": entry["neighborhood"],
            "year_built": yb,
            "year_demolished": yd,
            "demolition_cause": entry["demolition_cause"],
            "replaced_by": entry["replaced_by"],
            "site_hcad_nums": site_hcads,
            "hcad_num": primary_hcad,
            "architect": entry.get("architect", "Unknown"),
            "style": entry.get("style", "Historic Architecture"),
            "sanborn_material": entry["sanborn_material"],
            "sanborn_material_label": entry["sanborn_material_label"],
            "stories": entry["stories"],
            "height_m": entry["height_m"],
            "sanborn_ref": entry["sanborn_ref"],
            "source": f"Sanborn Fire Insurance Atlas ({entry.get('sanborn_ref', '1885-1950')}) · Preservation Houston Lost Houston Archive",
            "loc_volume_id": loc_vol,
            "loc_sheet_num": loc_sheet,
            "sanborn_sheet_image_url": sanborn_sheet_image_url,
            "sanborn_sheet_loc_url": sanborn_sheet_loc_url,
            "photo_url": photo_url,
            "photo_credit": photo_credit,
            "blurb": entry["blurb"],
            "historic_significance": entry["blurb"],
            "description": entry["blurb"],
            "lon": entry["lon"],
            "lng": entry["lon"],
            "lat": entry["lat"],
            "label_lng": entry["lon"],
            "label_lat": entry["lat"],
        }

        if entry.get("photo_url"):
            photo_list.append({
                "building_id": entry["id"],
                "landmark_name": entry["name"],
                "address": entry["address"],
                "image_url": entry["photo_url"],
                "thumb_url": entry["photo_url"],
                "full_url": entry["photo_url"],
                "photo_year": yb,
                "era_label": f"Historic Archival View ({yb}-{yd})",
                "caption": f"{entry['name']} ({yb}-{yd}) - {entry['subtitle']}. {entry['blurb']}",
                "attribution": entry.get("photo_credit") or "Wikimedia Commons / Public Domain Archive",
                "source_url": entry["photo_url"],
            })
        if sanborn_sheet_image_url:
            photo_list.append({
                "building_id": entry["id"],
                "landmark_name": entry["name"],
                "address": entry["address"],
                "image_url": sanborn_sheet_image_url,
                "thumb_url": sanborn_sheet_image_url,
                "full_url": sanborn_sheet_hires_url or sanborn_sheet_image_url,
                "photo_year": 1907 if "1907" in str(entry.get("sanborn_ref", "")) else yb,
                "era_label": f"Sanborn Atlas Plate ({entry.get('sanborn_ref', 'LOC')})",
                "caption": f"Digitized Sanborn Fire Insurance Map plate showing the block and surrounding context for {entry['name']} ({entry.get('sanborn_ref', '')}).",
                "attribution": "Library of Congress Geography & Map Division",
                "source_url": sanborn_sheet_loc_url or "https://www.loc.gov/collections/sanborn-maps/",
            })

        features.append({
            "type": "Feature",
            "id": entry["id"],
            "properties": props,
            "geometry": {
                "type": "Polygon",
                "coordinates": coords,
            },
        })

    overlays_path = DATA_DIR / "overlays.json"
    overlays = json.loads(overlays_path.read_text(encoding="utf-8"))
    overlays["lost_houston"] = {
        "type": "FeatureCollection",
        "features": features,
    }
    overlays_path.write_text(json.dumps(overlays, separators=(",", ":")), encoding="utf-8")
    photos_db["photos"] = photo_list
    photos_path.write_text(json.dumps(photos_db, indent=2), encoding="utf-8")
    print(f"Updated {overlays_path} with {len(features)} surveyed Lost Houston features and {len(photo_list)} total photos.")

    # 3. Also index Lost Houston landmarks into search_index.json so users can search for them by name!
    search_path = DATA_DIR / "search_index.json"
    if search_path.exists():
        sdata = json.loads(search_path.read_text(encoding="utf-8"))
        items = sdata.get("items", sdata) if isinstance(sdata, dict) else sdata
        items = [it for it in items if not (isinstance(it, dict) and str(it.get("id", "")).startswith("lost_"))]
        added = 0
        for f in features:
            p = f["properties"]
            items.append({
                "id": p["id"],
                "type": "lost_houston",
                "overlay_layer": "lost_houston",
                "label": f"{p['name']} ({p['year_built']}-{p['year_demolished']})",
                "name": p["name"],
                "building_name": p["name"],
                "sublabel": f"Demolished {p['year_demolished']} • {p['address']} • {p['sanborn_material_label']}",
                "category": f"Lost Houston ({p['year_built']}-{p['year_demolished']})",
                "address": p["address"],
                "neighborhood": p["neighborhood"],
                "year_built": p["year_built"],
                "year_demolished": p["year_demolished"],
                "architect": p["architect"],
                "style": p["style"],
                "use_category": p["category"],
                "lon": p["lon"],
                "lat": p["lat"],
            })
            added += 1
        if isinstance(sdata, dict):
            sdata["items"] = items
            search_path.write_text(json.dumps(sdata, separators=(",", ":")), encoding="utf-8")
        else:
            search_path.write_text(json.dumps(items, separators=(",", ":")), encoding="utf-8")
        print(f"Added {added} Lost Houston entries to {search_path}.")


if __name__ == "__main__":
    main()
