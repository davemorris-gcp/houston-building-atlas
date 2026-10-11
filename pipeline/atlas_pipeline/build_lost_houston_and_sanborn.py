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
            {"sheet": 1, "label": "Sheet 1: Index Map, Bayou Wharves & Foot of Main (Aug 1885)"},
            {"sheet": 2, "label": "Sheet 2: H&TC Passenger Depot & Roundhouse (Aug 1885)"},
            {"sheet": 8, "label": "Sheet 8: Market Square, 1876 City Hall & Hutchins House (Blocks 19 & 34)"},
            {"sheet": 9, "label": "Sheet 9: 1883 Victorian Courthouse, Quality Hill (Block 22) & Gray's Opera House"},
            {"sheet": 10, "label": "Sheet 10: 1883 Capitol Hotel (Block 57), Pillot's Opera House & Binz Site"},
            {"sheet": 11, "label": "Sheet 11: Masonic Temple (Block 68), Tremont House & Rice Home"},
        ],
        "sanborn08580_002": [
            {"sheet": 1, "label": "Sheet 1: Index Map & Key (June 1890)"},
            {"sheet": 3, "label": "Sheet 3: Grand Central Station (H&TC Ry) & Washington Ave"},
            {"sheet": 13, "label": "Sheet 13: Market Square, 1876 Market House & Hutchins House"},
            {"sheet": 14, "label": "Sheet 14: 1883 Courthouse Square & Quality Hill (Block 22)"},
            {"sheet": 15, "label": "Sheet 15: Capitol Hotel, Pillot's Opera House & Shearn Church"},
        ],
        "sanborn08580_003": [
            {"sheet": 1, "label": "Sheet 1: Hutchins House (Block 19) & Franklin Ave Wharves (1896)"},
            {"sheet": 2, "label": "Sheet 2: Quality Hill / Thomas W. House Mansion (Block 22) & Main/Franklin"},
            {"sheet": 3, "label": "Sheet 3: 1876 City Hall & Market House, Kiam Bldg & Capitol Hotel (Blocks 33, 34, 57, 58)"},
            {"sheet": 4, "label": "Sheet 4: 1883 Courthouse (Block 31), Sweeney & Coombs (Block 32) & 1895 Binz Bldg (Block 56)"},
            {"sheet": 7, "label": "Sheet 7: Bristol Hotel (Block 67) & Masonic Temple Site (Block 68)"},
            {"sheet": 8, "label": "Sheet 8: Temple Bldg (701 Main), Tremont House (Block 93) & Rice Home (Block 71)"},
            {"sheet": 33, "label": "Sheet 33: T. H. Scanlan Mansion (1917 Main St, Block 404)"},
        ],
        "sanborn08580_004": [
            {"sheet": 1, "label": "Sheet 1: Hutchins House (Block 19, Franklin & Travis) & Buffalo Bayou"},
            {"sheet": 2, "label": "Sheet 2: Quality Hill / Thomas W. House Mansion (Block 22) & 1905 First National Bank (Block 21)"},
            {"sheet": 3, "label": "Sheet 3: 1904 Market House (Block 34), Kiam Bldg & Mistrot (Block 33), Capitol Hotel (Block 57) & Shearn Church (Block 58)"},
            {"sheet": 4, "label": "Sheet 4: 1883 Courthouse (Block 31), Sweeney & Coombs (Block 32) & 1895 Binz Building (Block 56)"},
            {"sheet": 5, "label": "Sheet 5: Criminal Court & County Jail (Capitol & Bagby) & Buffalo Bayou"},
            {"sheet": 6, "label": "Sheet 6: Civic Center Precinct (Blocks 63-65, 84-89, Bagby to Louisiana)"},
            {"sheet": 7, "label": "Sheet 7: Hotel Bristol (Block 67/82), Odd Fellows Temple (Block 83) & Majestic Site (Block 81)"},
            {"sheet": 8, "label": "Sheet 8: Temple Bldg (701 Main), Tremont House (Block 93) & Rice Home (Block 71)"},
            {"sheet": 12, "label": "Sheet 12: I&GN Railroad Passenger Station & Roundhouse (Congress Ave)"},
            {"sheet": 13, "label": "Sheet 13: Houston Turn-Verein Hall (Block 53) & Majestic Vaudeville Theatre (Block 29)"},
            {"sheet": 17, "label": "Sheet 17: Central High School / Houston Academy (Block 77, Rusk & Austin)"},
            {"sheet": 18, "label": "Sheet 18: Annunciation Church & Incarnate Word Academy (Block 101, Crawford & Texas)"},
            {"sheet": 23, "label": "Sheet 23: 1904 Carnegie Library & Lyceum and 1896 First Presbyterian Church (Block 138)"},
            {"sheet": 24, "label": "Sheet 24: Theater District Block 135 (Main, Travis, McKinney, Lamar) & YMCA (Block 137)"},
            {"sheet": 33, "label": "Sheet 33: John F. Dickson Mansion (Main & Polk, Block 271)"},
            {"sheet": 34, "label": "Sheet 34: The Concordia Club (Main & Polk, Block 272)"},
            {"sheet": 50, "label": "Sheet 50: T. H. Scanlan Mansion (1917 Main St, Block 404) & Sacred Heart Church (Block 403)"},
            {"sheet": 51, "label": "Sheet 51: 1894 St. Joseph's Infirmary (Block 399, Crawford & Pierce)"},
            {"sheet": 86, "label": "Sheet 86: West End Park Baseball Grounds (Andrews & Heiner)"},
            {"sheet": 87, "label": "Sheet 87: Colored High School (San Felipe) & Antioch Baptist Church (Robin St)"},
            {"sheet": 89, "label": "Sheet 89: Frost Town ('El Barrio del Alacran', Spruce, Race & Arch Streets)"},
        ],
        "sanborn08580_005": [
            {"sheet": 1, "label": "Sheet 1: H&TC Freight Yards & Buffalo Bayou Confluence"},
            {"sheet": 2, "label": "Sheet 2: Houston Infirmary (H&TC Hospital) & Western Train Shed (Washington Ave)"},
            {"sheet": 3, "label": "Sheet 3: Grand Central Depot (H&TC / SP), Hotel Brazos & Macatee Hotel (Washington & Franklin)"},
            {"sheet": 7, "label": "Sheet 7: MKT Freight Depot, Dickson Car Wheel Co. & Planters Compress"},
            {"sheet": 68, "label": "Sheet 68: Fifth Ward Steam Cotton Compress & Special Industrial Risks"},
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
        tparts = tmpl.split(":")
        loc_resource_base = f"https://www.loc.gov/resource/{tparts[4]}.{tparts[5]}/"
        sheets = existing_sheets_by_vid.get(vid, [])
        vol_feat = featured_sheets.get(
            vid,
            [{"sheet": 1, "label": f"Sheet 1: Volume Overview ({date_str})"}],
        )
        feat_by_code = {f"{int(f['sheet']):04d}": f["label"] for f in vol_feat}
        for s in sheets:
            code = str(s.get("code", ""))
            sp_val = int(s.get("sp", 1))
            s["loc_url"] = f"{loc_resource_base}?sp={sp_val}"
            if s.get("thumb_url"):
                s["preview_url"] = s["thumb_url"]
            elif s.get("preview_url"):
                s["preview_url"] = str(s["preview_url"]).replace("/pct:25/", "/pct:12.5/").replace("/pct:50/", "/pct:12.5/")
            if s.get("hires_url"):
                s["hires_url"] = str(s["hires_url"]).replace("/pct:50/", "/pct:25/")
            if code == "titl":
                s["label"] = f"Title Sheet ({date_str})"
                s["is_featured"] = True
            elif code in ("ind1", "ind2"):
                s["label"] = f"Graphic Index & Street Key ({date_str})"
                s["is_featured"] = True
            elif code == "0000":
                s["label"] = f"Key Map & Volume Overview ({date_str})"
                s["is_featured"] = True
            elif code.endswith("s") and code[:-1].isdigit():
                s["label"] = f"Skeleton Map Sheet {int(code[:-1])} ({date_str})"
                s["is_featured"] = False
            elif code in feat_by_code:
                s["label"] = feat_by_code[code]
                s["is_featured"] = True
            elif code.isdigit():
                s["label"] = f"Sheet {int(code)} ({date_str})"
                s["is_featured"] = False
            else:
                s["label"] = f"Page {s.get('sp', '')} ({date_str})"
                s["is_featured"] = False

        # Enrich vol_feat with exact LOC sequential page index (sp) and code
        enriched_feat = []
        for f in vol_feat:
            f_code = f"{int(f['sheet']):04d}"
            ms = next((s for s in sheets if s.get("code") == f_code), None)
            if not ms:
                ms = next((s for s in sheets if s.get("sp") == int(f["sheet"])), None)
            enriched_feat.append({
                "sheet": int(f["sheet"]),
                "code": ms["code"] if ms else f_code,
                "sp": ms["sp"] if ms else int(f["sheet"]),
                "label": f["label"],
            })

        count = len(sheets) if sheets else item["sheet_count"]
        vol_obj = {
            "id": vid,
            "era": era_short,
            "date": date_str,
            "title": title_str,
            "sheet_count": count,
            "loc_url": item["loc_url"],
            "loc_resource_url": loc_resource_base,
            "iiif_template": f"https://tile.loc.gov/image-services/iiif/{tmpl}/full/pct:12.5/0/default.jpg",
            "iiif_highres_template": f"https://tile.loc.gov/image-services/iiif/{tmpl}/full/pct:25/0/default.jpg",
            "featured_sheets": enriched_feat,
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


EXPLICIT_HCAD_FIXES = {
    "lost_republic_of_texas_capitol": (
        ["0010570000009"],
        "001057",
        -95.36260,
        29.76038,
        20.0,
        28.0,
        "Main St at Texas Ave (NW Corner, Block 57)",
    ),
    "lost_capitol_hotel_1883": (
        ["0010570000009"],
        "001057",
        -95.36262,
        29.76040,
        36.0,
        33.0,
        "909 Texas Ave at Main St (NW Corner, Block 57)",
    ),
    "lost_binz_building_1895": (
        ["0010560000006"],
        "001056",
        -95.36212,
        29.76005,
        25.0,
        26.0,
        "1001 Texas Ave at Main St (NE Corner, Block 56)",
    ),
    "lost_pillot_opera_house_1880": (
        ["0010320000004", "0010320000001"],
        "001032",
        -95.36045,
        29.76152,
        22.0,
        34.0,
        "316 Fannin St between Congress & Preston (Block 32)",
    ),
    "lost_prince_theatre_1904": (
        ["0010560000001"],
        "001056",
        -95.36180,
        29.75982,
        20.0,
        26.0,
        "1011-1015 Texas Ave between Main & Fannin (Block 56)",
    ),
    "lost_first_national_bank_1905": (
        ["1228970000001", "0010210000001"],
        "122897",
        -95.36002,
        29.76238,
        26.0,
        36.0,
        "201 Main St at Franklin Ave (SE Corner, Block 21)",
    ),
    "lost_masonic_temple_1885": (
        ["0010680000009"],
        "001068",
        -95.36324,
        29.75956,
        26.0,
        18.0,
        "701 Main St at Capitol Ave (NW Corner, Block 68)",
    ),
    "lost_carnegie_library_1904": (
        ["0011390000002"],
        "001139",
        -95.36575,
        29.75705,
        26.0,
        30.0,
        "Travis St at McKinney Ave (SE Corner, Block 139)",
    ),
    "lost_hotel_brazos_1898": (
        ["0030690000004"],
        "003069",
        -95.36610,
        29.76545,
        36.0,
        44.0,
        "901 Washington Ave at Preston St (Opposite Grand Central Station)",
    ),
    "lost_hutchins_house_1866": (
        ["0010190000011", "0010190000007", "0010190000008", "0010190000006"],
        "001019",
        -95.36180,
        29.76355,
        28.0,
        36.0,
        "800 Franklin Ave at Travis St (NW Corner, Block 19)",
    ),
    "lost_macatee_hotel_1906": (
        ["0400990000008"],
        "040099",
        -95.36535,
        29.76478,
        26.0,
        34.0,
        "519 Washington Ave at Preston St Approach",
    ),
    "lost_tremont_house_1873": (
        ["1305070010001", "0010930000001"],
        "130507",
        -95.36362,
        29.75782,
        26.0,
        36.0,
        "811 Main St at Walker Ave (Block 93)",
    ),
    "lost_bristol_hotel_1895": (
        ["0010670000001"],
        "001067",
        -95.36395,
        29.76015,
        26.0,
        34.0,
        "618 Travis St at Capitol Ave (Block 67)",
    ),
    "lost_queen_theatre_1913": (
        ["0010670000001"],
        "001067",
        -95.36415,
        29.76042,
        20.0,
        38.0,
        "608 Travis St between Texas & Capitol (Block 67)",
    ),
    "lost_kiam_building_1893": (
        ["0010330000013"],
        "001033",
        -95.36145,
        29.76178,
        28.0,
        9.5,
        "320 Main St at Preston Ave (NW Corner, Block 33)",
    ),
    "lost_chronicle_building_1909": (
        ["1398670010001"],
        "139867",
        -95.36325,
        29.76100,
        42.0,
        54.0,
        "801 Texas Ave at Travis St (NW Corner, Block 58)",
    ),
    "lost_shearn_methodist_church_1884": (
        ["1398670010001"],
        "139867",
        -95.36368,
        29.76128,
        26.0,
        36.0,
        "815-901 Texas Ave at Milam St (Block 58)",
    ),
    "lost_majestic_theatre_1923": (
        ["0010810000007"],
        "001081",
        -95.36385,
        29.75888,
        32.0,
        48.0,
        "908 Rusk St between Main & Travis (Block 81)",
    ),
    "lost_kirby_theatre_1927": (
        ["0011350000001"],
        "001135",
        -95.36385,
        29.75612,
        24.0,
        38.0,
        "1002 Main St at McKinney Ave (Block 135)",
    ),
    "lost_metropolitan_theatre_1926": (
        ["0011350000001"],
        "001135",
        -95.36402,
        29.75588,
        28.0,
        44.0,
        "1016 Main St between McKinney & Lamar (Block 135)",
    ),
    "lost_loews_state_theatre_1927": (
        ["0011350000001"],
        "001135",
        -95.36420,
        29.75562,
        28.0,
        44.0,
        "1022 Main St at Lamar Ave (Block 135)",
    ),
    "lost_azteca_theatre_1927": (
        ["0010220000002"],
        "001022",
        -95.35910,
        29.76182,
        18.0,
        32.0,
        "1112 Congress Ave between Fannin & San Jacinto (Block 22)",
    ),
    "lost_lincoln_theatre_1926": (
        ["0010470000012", "0010470000001"],
        "001047",
        -95.35955,
        29.75965,
        20.0,
        34.0,
        "1211 Prairie Ave between San Jacinto & Caroline (Block 47)",
    ),
    "lost_scanlan_mansion_1891": (
        [],
        "002050",
        -95.36885,
        29.75025,
        22.0,
        26.0,
        "1917 Main St between Pierce & St. Joseph (Block bounded by Main, Fannin, Pierce, St. Joseph)",
    ),
    "lost_house_mansion_quality_hill_1870": (
        ["0010220000033", "0010220000002"],
        "001022",
        -95.35928,
        29.76195,
        20.0,
        24.0,
        "1104 Congress Ave at Fannin St (Quality Hill, Block 22)",
    ),
    "lost_rice_home_rusk_1860": (
        ["0010710000008", "0010710000017"],
        "001071",
        -95.36075,
        29.75835,
        18.0,
        22.0,
        "Rusk Ave at San Jacinto St (Block 71)",
    ),
    "lost_ross_sterling_mansion_montrose_1912": (
        ["0302450000008"],
        "030245",
        -95.39185,
        29.73220,
        22.0,
        20.0,
        "4515 Yoakum Blvd at Sunset/Bissonnet (Montrose / Museum District)",
    ),
    "lost_cullinan_mansion_1906": (
        ["0011250000006"],
        "001125",
        -95.35909,
        29.75436,
        11.5,
        10.5,
        "1600 Polk St at Crawford St (Block 125)",
    ),
    "lost_dickson_mansion_main_1896": (
        ["0020150000001"],
        "002015",
        -95.36645,
        29.75402,
        20.0,
        24.0,
        "1316 Main St at Polk/Clay (Block 262)",
    ),
    "lost_incarnate_word_academy_1873": (
        ["0011020000001"],
        "001102",
        -95.35665,
        29.75718,
        26.0,
        30.0,
        "609 Crawford St at Capitol Ave (Block 97)",
    ),
    "lost_pilgrim_building_1926": (
        ["1035350000018"],
        "103535",
        -95.37178,
        29.75732,
        28.0,
        36.0,
        "222 W. Dallas St at Bagby St (Fourth Ward)",
    ),
    "lost_sweeney_coombs_fredericks_1891": (
        ["0010320000005"],
        "001032",
        -95.36065,
        29.76192,
        20.0,
        30.0,
        "301 Main St at Congress Ave (SW Corner, Block 32)",
    ),
    "lost_mistrot_emporium_1904": (
        ["0010330000030", "0010330000024", "0010330000026"],
        "001033",
        -95.36155,
        29.76252,
        26.0,
        32.0,
        "910 Congress Ave at Travis St (Block 33)",
    ),
    "lost_turnverein_hall_1908": (
        ["0010480000006", "0010480000013"],
        "001048",
        -95.35865,
        29.75925,
        26.0,
        34.0,
        "1314 Prairie Ave at Austin St (Block 48)",
    ),
    "lost_concordia_club_1903": (
        ["0012600000010"],
        "001260",
        -95.36569,
        29.75488,
        24.0,
        32.0,
        "1217 Main St at Polk Ave (Block 260)",
    ),
    "lost_temple_beth_israel_1908": (
        ["0011260000002"],
        "001126",
        -95.36003,
        29.75446,
        26.0,
        34.0,
        "1101 Crawford St at Lamar Ave (Block 126)",
    ),
    "lost_first_presbyterian_main_mckinney_1896": (
        ["1272590000067", "0011340000021"],
        "001134",
        -95.36360,
        29.75665,
        30.0,
        40.0,
        "917 Main St at McKinney Ave (NE Corner, Block 134)",
    ),
    "lost_emancipation_park_1918_pavilion": (
        ["0191150000001"],
        "019115",
        -95.36487,
        29.73596,
        28.0,
        36.0,
        "3018 Emancipation Ave (Dowling St) at Elgin St",
    ),
    "lost_luna_park_1924": (
        ["1398730010001", "0051080000013"],
        "139873",
        -95.37280,
        29.77580,
        140.0,
        105.0,
        "Houston Ave at White Oak Bayou & Crockett St",
    ),
    "lost_colored_high_school_1893": (
        ["0090730000003", "1246690020002"],
        "009073",
        -95.37920,
        29.75620,
        30.0,
        24.0,
        "303 W. Dallas / San Felipe Rd at Tirrell & Andrews (Fourth Ward)",
    ),
    "lost_colored_carnegie_library_1913": (
        ["1274610010002"],
        "127461",
        -95.37665,
        29.75448,
        18.0,
        24.0,
        "1302 Frederick St at Robin St (Fourth Ward / Freedmen's Town)",
    ),
}


def load_parcel_index() -> dict[str, list[dict]]:
    """Indexes HCAD parcels from app/public/data/parcels.geojson with polygon rings and surveyed edge angles."""
    parcels_path = DATA_DIR / "parcels.geojson"
    if not parcels_path.exists():
        return {}
    features = json.loads(parcels_path.read_text(encoding="utf-8")).get("features", [])
    p_by_hcad: dict[str, list[dict]] = {}
    for f in features:
        p = f.get("properties", {})
        h = str(p.get("hcad_num", ""))
        if not h:
            continue
        ring = f["geometry"]["coordinates"][0]
        if isinstance(ring[0][0], list):
            ring = ring[0]
        lons = [pt[0] for pt in ring]
        lats = [pt[1] for pt in ring]
        mlon, mlat = sum(lons) / len(lons), sum(lats) / len(lats)
        best_l = 0.0
        best_a = -34.65
        for i in range(len(ring) - 1):
            dx = (ring[i + 1][0] - ring[i][0]) * 111320.0 * math.cos(math.radians(mlat))
            dy = (ring[i + 1][1] - ring[i][1]) * 110850.0
            l = math.hypot(dx, dy)
            if l > best_l:
                best_l = l
                a = math.degrees(math.atan2(dy, dx))
                while a > 45.0:
                    a -= 90.0
                while a < -45.0:
                    a += 90.0
                best_a = a
        p_by_hcad.setdefault(h, []).append({
            "hcad": h,
            "lon": mlon,
            "lat": mlat,
            "ring": ring,
            "angle": best_a,
            "max_edge_m": best_l,
        })
    return p_by_hcad


def _point_in_ring(u: float, v: float, ring_uv: list[tuple[float, float]]) -> bool:
    inside = False
    n = len(ring_uv)
    j = n - 1
    for i in range(n):
        ui, vi = ring_uv[i]
        uj, vj = ring_uv[j]
        if ((vi > v) != (vj > v)) and (
            u < (uj - ui) * (v - vi) / ((vj - vi) or 1e-12) + ui
        ):
            inside = not inside
        j = i
    return inside


def _point_in_any_ring(
    u: float, v: float, rings_uv: list[list[tuple[float, float]]]
) -> bool:
    if any(_point_in_ring(u, v, r) for r in rings_uv):
        return True
    for du, dv in ((0.08, 0.0), (-0.08, 0.0), (0.0, 0.08), (0.0, -0.08)):
        if any(_point_in_ring(u + du, v + dv, r) for r in rings_uv):
            return True
    return False


_SAMPLE_FRACS = (-1.0, -0.75, -0.5, -0.25, 0.0, 0.25, 0.5, 0.75, 1.0)


def _rect_inside_single_ring(
    uc: float,
    vc: float,
    hw: float,
    hd: float,
    rings_uv: list[list[tuple[float, float]]],
) -> bool:
    for r in rings_uv:
        ok = True
        for fx in _SAMPLE_FRACS:
            for fy in _SAMPLE_FRACS:
                if not _point_in_ring(uc + fx * hw, vc + fy * hd, r):
                    ok = False
                    break
            if not ok:
                break
        if ok:
            return True
    return False


def _rect_inside_rings(
    uc: float,
    vc: float,
    hw: float,
    hd: float,
    rings_uv: list[list[tuple[float, float]]],
) -> bool:
    for fx in _SAMPLE_FRACS:
        for fy in _SAMPLE_FRACS:
            if not _point_in_any_ring(uc + fx * hw, vc + fy * hd, rings_uv):
                return False
    return True


def fit_rect_in_parcel(
    lon: float,
    lat: float,
    width_m: float,
    depth_m: float,
    angle_deg: float,
    rings_lonlat: list[list[list[float]]],
    setback_m: float = 1.5,
    max_shift_m: float = 35.0,
) -> tuple[float, float, float, float]:
    """Fits a building footprint strictly inside the polygon ring(s) of its HCAD parcel(s) in the rotated street-grid frame."""
    if not rings_lonlat:
        return lon, lat, width_m, depth_m
    rad = math.radians(angle_deg)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    m_lat = 110850.0
    m_lon = 111320.0 * math.cos(math.radians(lat))

    rings_uv = []
    all_u, all_v = [], []
    for ring in rings_lonlat:
        ruv = []
        for plon, plat in ring:
            dx = (plon - lon) * m_lon
            dy = (plat - lat) * m_lat
            u = dx * cos_a + dy * sin_a
            v = -dx * sin_a + dy * cos_a
            ruv.append((u, v))
            all_u.append(u)
            all_v.append(v)
        rings_uv.append(ruv)

    umin, umax = min(all_u), max(all_u)
    vmin, vmax = min(all_v), max(all_v)

    for check_fn in (_rect_inside_single_ring, _rect_inside_rings):
        for scale in (1.0, 0.92, 0.85, 0.78, 0.70, 0.62, 0.55):
            for tw, td in ((width_m, depth_m), (depth_m, width_m)):
                w = round(tw * scale, 1)
                d = round(td * scale, 1)
                hw = w / 2.0 + setback_m
                hd = d / 2.0 + setback_m
                if umax - umin < 2 * hw or vmax - vmin < 2 * hd:
                    continue

                best_uv = None
                best_dist = 1e18
                u_steps = max(1, int((umax - umin - 2 * hw) / 1.0) + 1)
                v_steps = max(1, int((vmax - vmin - 2 * hd) / 1.0) + 1)
                for iu in range(u_steps + 1):
                    uc = (umin + hw) + (umax - umin - 2 * hw) * (iu / max(1, u_steps))
                    for iv in range(v_steps + 1):
                        vc = (vmin + hd) + (vmax - vmin - 2 * hd) * (iv / max(1, v_steps))
                        dist = math.hypot(uc, vc)
                        if (
                            dist <= max_shift_m
                            and dist < best_dist
                            and check_fn(uc, vc, hw, hd, rings_uv)
                        ):
                            best_dist = dist
                            best_uv = (uc, vc)
                if best_uv is not None:
                    uc, vc = best_uv
                    dx_new = uc * cos_a - vc * sin_a
                    dy_new = uc * sin_a + vc * cos_a
                    return (
                        round(lon + dx_new / m_lon, 6),
                        round(lat + dy_new / m_lat, 6),
                        w,
                        d,
                    )

    avail_w = (umax - umin) - 2.0 * setback_m
    avail_d = (vmax - vmin) - 2.0 * setback_m
    w = min(width_m, max(12.0, avail_w)) if avail_w > 12.0 else width_m
    d = min(depth_m, max(12.0, avail_d)) if avail_d > 12.0 else depth_m
    hw, hd = w / 2.0, d / 2.0
    uc = (
        max(umin + setback_m + hw, min(umax - setback_m - hw, 0.0))
        if umin + setback_m + hw <= umax - setback_m - hw
        else (umin + umax) / 2.0
    )
    vc = (
        max(vmin + setback_m + hd, min(vmax - setback_m - hd, 0.0))
        if vmin + setback_m + hd <= vmax - setback_m - hd
        else (vmin + vmax) / 2.0
    )
    if math.hypot(uc, vc) > max_shift_m:
        return lon, lat, width_m, depth_m
    dx_new = uc * cos_a - vc * sin_a
    dy_new = uc * sin_a + vc * cos_a
    return (
        round(lon + dx_new / m_lon, 6),
        round(lat + dy_new / m_lat, 6),
        round(w, 1),
        round(d, 1),
    )


# Visually verified against the digitized LOC Sanborn plates:
# Maps each landmark ID -> (loc_volume_id, printed_sheet_num, sanborn_ref_label, extra_plates[(vol_id, sheet_num, label)])
VERIFIED_SANBORN_REFS: dict[str, tuple[str | None, int | None, str, list[tuple[str, int, str]]]] = {
    # Block 57 (Main, Travis, Prairie, Texas) -> 1885 Sheet 10, 1896 Sheet 3, 1907 Vol. 1 Sheet 3
    "lost_republic_of_texas_capitol": (
        "sanborn08580_001",
        10,
        "1885 Sheet 10 & 1907 Vol. 1 Sheet 3 (Block 57: Main & Texas NW Corner, Old Capitol Site)",
        [("sanborn08580_004", 3, "1907 Vol. 1 Sheet 3 (Block 57: Main & Texas NW Corner)")],
    ),
    "lost_capitol_hotel_1883": (
        "sanborn08580_004",
        3,
        "1885 Sheet 10; 1896 Sheet 3; 1907 Vol. 1 Sheet 3 (Block 57: Main & Texas NW Corner)",
        [
            ("sanborn08580_001", 10, "1885 Sheet 10 (Block 57: 1883 Capitol Hotel)"),
            ("sanborn08580_003", 3, "1896 Sheet 3 (Block 57: Capitol Hotel at Main & Texas)"),
        ],
    ),
    # Block 45 (Main, Fannin, Preston, Prairie) -> 1885 Sheet 9, 1896 Sheet 4, 1907 Vol. 1 Sheet 4
    "lost_first_texas_white_house": (
        "sanborn08580_001",
        9,
        "1885 Sheet 9 & 1907 Vol. 1 Sheet 4 (Block 45: 405 Main St at Preston SE Corner)",
        [("sanborn08580_004", 4, "1907 Vol. 1 Sheet 4 (Block 45: Main & Preston SE Corner)")],
    ),
    # Block 34 (Market Square: Congress, Preston, Travis, Milam) -> 1885 Sheet 8, 1896 Sheet 3, 1907 Vol. 1 Sheet 3
    "lost_market_house_1840": (
        "sanborn08580_001",
        8,
        "1885 Sheet 8 & 1907 Vol. 1 Sheet 3 (Block 34: Market Square, Congress to Preston)",
        [("sanborn08580_004", 3, "1907 Vol. 1 Sheet 3 (Block 34: Market Square)")],
    ),
    "lost_market_house_1876": (
        "sanborn08580_003",
        3,
        "1885 Sheet 8 & 1896 Sheet 3 (Block 34: 1876 City Hall & Market House on Market Square)",
        [
            ("sanborn08580_001", 8, "1885 Sheet 8 (Block 34: 1876 City Hall & Market House)"),
            ("sanborn08580_004", 3, "1907 Vol. 1 Sheet 3 (Block 34: 1904 Successor Market House)"),
        ],
    ),
    "lost_market_house_1904": (
        "sanborn08580_004",
        3,
        "1907 Vol. 1 Sheet 3 (Block 34: 1904 City Market House, Bounded by Congress, Preston, Travis, Milam)",
        [("sanborn08580_003", 3, "1896 Sheet 3 (Block 34: Earlier 1876 Market House on Same Square)")],
    ),
    # Block 31 (Courthouse Square: Congress, Preston, Fannin, San Jacinto) -> 1885 Sheet 9, 1896 Sheet 4, 1907 Vol. 1 Sheet 4
    "lost_courthouse_1851": (
        "sanborn08580_001",
        9,
        "1885 Sheet 9 & 1907 Vol. 1 Sheet 4 (Block 31: Courthouse Square, Fannin to San Jacinto)",
        [("sanborn08580_004", 4, "1907 Vol. 1 Sheet 4 (Block 31: Courthouse Square)")],
    ),
    "lost_courthouse_1883": (
        "sanborn08580_004",
        4,
        "1885 Sheet 9; 1896 Sheet 4; 1907 Vol. 1 Sheet 4 (Block 31: 1883 Harris County Court House)",
        [
            ("sanborn08580_001", 9, "1885 Sheet 9 (Block 31: 1883 Victorian Harris County Court House)"),
            ("sanborn08580_003", 4, "1896 Sheet 4 (Block 31: Harris County Court House)"),
        ],
    ),
    # Block 56 (Main, Fannin, Prairie, Texas) -> 1896 Sheet 4, 1907 Vol. 1 Sheet 4
    "lost_binz_building_1895": (
        "sanborn08580_004",
        4,
        "1896 Sheet 4 & 1907 Vol. 1 Sheet 4 (Block 56: 1001 Texas Ave at Main St NE Corner)",
        [("sanborn08580_003", 4, "1896 Sheet 4 (Block 56: Newly Completed 6-Story 1895 Binz Building)")],
    ),
    "lost_prince_theatre_1904": (
        "sanborn08580_004",
        4,
        "1907 Vol. 1 Sheet 4 (Block 56: Texas Ave between Main & Fannin, Adj. to Binz Building)",
        [("sanborn08580_003", 4, "1896 Sheet 4 (Block 56: Main, Fannin, Prairie, Texas)")],
    ),
    # Block 21 (Main, Fannin, Franklin, Congress) -> 1885 Sheet 9, 1896 Sheet 2, 1907 Vol. 1 Sheet 2
    "lost_first_national_bank_1905": (
        "sanborn08580_004",
        2,
        "1907 Vol. 1 Sheet 2 (Block 21: 201 Main St at Franklin Ave SE Corner, 'First Nat'l Bank Bldg.')",
        [("sanborn08580_003", 2, "1896 Sheet 2 (Block 21: Earlier 3-Story First National Bank at Main & Franklin)")],
    ),
    # Block 68 / 69 (Main & Capitol) -> 1885 Sheet 11, 1896 Sheet 7 & 8, 1907 Vol. 1 Sheet 7 & 8
    "lost_masonic_temple_1885": (
        "sanborn08580_004",
        8,
        "1885 Sheet 11; 1896 Sheet 8; 1907 Vol. 1 Sheet 8 & Sheet 7 (701 Main St at Capitol Ave, Temple Bldg)",
        [
            ("sanborn08580_004", 7, "1907 Vol. 1 Sheet 7 (Block 68: West Side of 700 Main at Capitol)"),
            ("sanborn08580_001", 11, "1885 Sheet 11 (Blocks 68 & 69: Main St & Capitol Ave)"),
        ],
    ),
    # Block 138 / 139 (Main, Travis, McKinney, Lamar) -> 1907 Vol. 1 Sheet 23
    "lost_carnegie_library_1904": (
        "sanborn08580_004",
        23,
        "1907 Vol. 1 Sheet 23 (Block 138: Travis St & McKinney Ave, 'Carnegie Library and Lyceum')",
        [],
    ),
    "lost_first_presbyterian_main_mckinney_1896": (
        "sanborn08580_004",
        23,
        "1907 Vol. 1 Sheet 23 (Block 138: Main St & McKinney Ave, '1st Presby'n Church')",
        [],
    ),
    "lost_lamar_hotel_1927": (
        "sanborn08580_004",
        23,
        "1907 Vol. 1 Sheet 23 (Block 138: Main St at Lamar Ave West Side, Pre-1927 Site Plate)",
        [],
    ),
    # Fourth Ward (Frederick & Robin) -> 1907 Vol. 1 Sheet 88
    "lost_colored_carnegie_library_1913": (
        "sanborn08580_004",
        88,
        "1907 Vol. 1 Sheet 88 (Fourth Ward: Frederick & Robin Streets, Pre-1913 Site Plate)",
        [("sanborn08580_004", 87, "1907 Vol. 1 Sheet 87 (Adjacent Fourth Ward Colored High School & Antioch Baptist)")],
    ),
    # South Main / Texas Medical Center -> 1924-1950 Vol. 11 (sanborn08580_027)
    "lost_shamrock_hotel_1949": (
        "sanborn08580_027",
        1,
        "1924-1950 Vol. 11 Sheet 1 (South Main & Holcombe Blvd Volume Overview)",
        [],
    ),
    "lost_prudential_building_1952": (
        "sanborn08580_027",
        1,
        "1924-1950 Vol. 11 Sheet 1 (South Main & Holcombe Medical Center Corridor Overview)",
        [],
    ),
    # Washington Ave & Preston / Franklin (Grand Central Depot, Hotel Brazos, Macatee Hotel) -> 1907 Vol. 2 Sheet 3
    "lost_hotel_brazos_1898": (
        "sanborn08580_005",
        3,
        "1907 Vol. 2 Sheet 3 (Washington Ave Opposite H&TC Grand Central Depot, 'Hotel Brazos')",
        [("sanborn08580_005", 2, "1907 Vol. 2 Sheet 2 (Adjacent H&TC Train Shed & Houston Infirmary)")],
    ),
    "lost_macatee_hotel_1906": (
        "sanborn08580_005",
        3,
        "1907 Vol. 2 Sheet 3 (Washington Ave & Franklin Approach, 'The Macatee Hotel')",
        [],
    ),
    "lost_grand_central_station_1887": (
        "sanborn08580_005",
        3,
        "1890 Sheet 3 & 1907 Vol. 2 Sheet 3 (Washington Ave: 'Grand Central Depot - Passenger Depot & Train Shed')",
        [
            ("sanborn08580_005", 2, "1907 Vol. 2 Sheet 2 (Western H&TC Passenger Train Shed & Hospital)"),
            ("sanborn08580_001", 2, "1885 Sheet 2 (Earlier H&TC Passenger Depot & Roundhouse on Same Site)"),
        ],
    ),
    "lost_southern_pacific_station_1934": (
        "sanborn08580_005",
        3,
        "1907 Vol. 2 Sheet 3 (Washington Ave & Franklin: Grand Central / Southern Pacific Depot Site)",
        [("sanborn08580_005", 2, "1907 Vol. 2 Sheet 2 (Western Passenger Yard & Bayou Frontage)")],
    ),
    # Block 19 (Franklin, Congress, Travis, Milam) -> 1885 Sheet 8, 1896 Sheet 1, 1907 Vol. 1 Sheet 1
    "lost_hutchins_house_1866": (
        "sanborn08580_004",
        1,
        "1885 Sheet 8; 1896 Sheet 1; 1907 Vol. 1 Sheet 1 (Block 19: Franklin Ave & Travis NW Corner)",
        [
            ("sanborn08580_001", 8, "1885 Sheet 8 (Block 19: Hutchins House Hotel at Franklin & Travis)"),
            ("sanborn08580_003", 1, "1896 Sheet 1 (Block 19: Hutchins House)"),
        ],
    ),
    # Block 93 (Main, Travis, Rusk, Walker) -> 1885 Sheet 11, 1896 Sheet 8, 1907 Vol. 1 Sheet 8
    "lost_tremont_house_1873": (
        "sanborn08580_004",
        8,
        "1885 Sheet 11; 1896 Sheet 8; 1907 Vol. 1 Sheet 8 (Block 93: 811 Main St at Walker Ave)",
        [
            ("sanborn08580_001", 11, "1885 Sheet 11 (Block 93: Victorian Tremont House Hotel)"),
            ("sanborn08580_003", 8, "1896 Sheet 8 (Block 93: Tremont House at Main & Walker)"),
        ],
    ),
    # Block 67 / 82 (Travis & Capitol) -> 1896 Sheet 7, 1907 Vol. 1 Sheet 7
    "lost_bristol_hotel_1895": (
        "sanborn08580_004",
        7,
        "1896 Sheet 7 & 1907 Vol. 1 Sheet 7 (Blocks 67 & 82: Travis St at Capitol Ave, 'Hotel Bristol')",
        [("sanborn08580_003", 7, "1896 Sheet 7 (Blocks 67 & 82: Travis St & Capitol Ave)")],
    ),
    "lost_queen_theatre_1913": (
        "sanborn08580_004",
        7,
        "1907 Vol. 1 Sheet 7 (Block 67: 608 Travis St between Texas & Capitol, Pre-1913 Site Plate)",
        [],
    ),
    # Block 66 (Milam, Louisiana, Texas, Capitol) -> 1907 Vol. 1 Sheet 7
    "lost_city_auditorium_1910": (
        "sanborn08580_004",
        7,
        "1907 Vol. 1 Sheet 7 (Block 66: Bounded by Louisiana, Milam, Texas & Capitol, Pre-1910 Site Plate)",
        [],
    ),
    # Civic Center (Bagby to Smith, Capitol to Walker) -> 1907 Vol. 1 Sheet 6
    "lost_sam_houston_hall_1928": (
        "sanborn08580_004",
        6,
        "1907 Vol. 1 Sheet 6 (Blocks 63-64 & 85-86: Bagby to Smith at Rusk/Walker, Pre-1928 Civic Center Site)",
        [],
    ),
    "lost_sam_houston_coliseum_1937": (
        "sanborn08580_004",
        6,
        "1907 Vol. 1 Sheet 6 (Blocks 63-64 & 85-86: Bagby to Smith at Walker, Pre-1937 Coliseum Site)",
        [],
    ),
    # Block 33 (Main, Travis, Congress, Preston) -> 1885 Sheet 8, 1896 Sheet 3, 1907 Vol. 1 Sheet 3
    "lost_kiam_building_1893": (
        "sanborn08580_004",
        3,
        "1896 Sheet 3 & 1907 Vol. 1 Sheet 3 (Block 33: 320 Main St at Preston NW Corner, 'Kiam Bldg')",
        [("sanborn08580_003", 3, "1896 Sheet 3 (Block 33: Kiam Building at Main & Preston)")],
    ),
    "lost_mistrot_emporium_1904": (
        "sanborn08580_004",
        3,
        "1907 Vol. 1 Sheet 3 (Block 33: 910 Congress Ave at Travis St, Facing Market Square)",
        [("sanborn08580_003", 3, "1896 Sheet 3 (Block 33: South Side of Market Square)")],
    ),
    # Block 58 (Travis, Milam, Prairie, Texas) -> 1885 Sheet 10, 1896 Sheet 3, 1907 Vol. 1 Sheet 3
    "lost_chronicle_building_1909": (
        "sanborn08580_004",
        3,
        "1907 Vol. 1 Sheet 3 (Block 58: 801 Texas Ave at Travis NW Corner, Pre-1909 Site Adjoining Shearn Church)",
        [("sanborn08580_003", 3, "1896 Sheet 3 (Block 58: Travis, Milam, Prairie, Texas)")],
    ),
    "lost_shearn_methodist_church_1884": (
        "sanborn08580_004",
        3,
        "1885 Sheet 10; 1896 Sheet 3; 1907 Vol. 1 Sheet 3 (Block 58: 'Chas. Shearn Memorial Church' at Texas & Milam)",
        [
            ("sanborn08580_001", 10, "1885 Sheet 10 (Block 58: 1884 Shearn Methodist Church on Texas Ave)"),
            ("sanborn08580_003", 3, "1896 Sheet 3 (Block 58: Shearn Memorial Methodist Church)"),
        ],
    ),
    # Block 32 (Main, Fannin, Congress, Preston) -> 1885 Sheet 9 & 10, 1896 Sheet 4, 1907 Vol. 1 Sheet 4
    "lost_pillot_opera_house_1880": (
        "sanborn08580_004",
        4,
        "1885 Sheet 10; 1896 Sheet 4; 1907 Vol. 1 Sheet 4 (Block 32: Main to Fannin between Congress & Preston)",
        [
            ("sanborn08580_001", 10, "1885 Sheet 10 (Block 32: Pillot's Building / Opera House)"),
            ("sanborn08580_003", 4, "1896 Sheet 4 (Block 32: Sweeney & Coombs' Opera House)"),
        ],
    ),
    "lost_sweeney_coombs_fredericks_1891": (
        "sanborn08580_004",
        4,
        "1896 Sheet 4 & 1907 Vol. 1 Sheet 4 (Block 32: 301 Main St at Congress SW Corner, 'Sweeney & Coombs')",
        [("sanborn08580_003", 4, "1896 Sheet 4 (Block 32: Sweeney, Coombs & Fredericks at 301 Main)")],
    ),
    # Block 81 (Main, Travis, Capitol, Rusk) -> 1907 Vol. 1 Sheet 7
    "lost_majestic_theatre_1923": (
        "sanborn08580_004",
        7,
        "1907 Vol. 1 Sheet 7 (Block 81: 908 Rusk St between Main & Travis; plus 1907 Majestic on Sheet 13)",
        [("sanborn08580_004", 13, "1907 Vol. 1 Sheet 13 (Block 29: Earlier 1907 'Majestic Theatre' on Congress Ave)")],
    ),
    # Block 135 (Main, Fannin, McKinney, Lamar) -> 1907 Vol. 1 Sheet 24
    "lost_metropolitan_theatre_1926": (
        "sanborn08580_004",
        24,
        "1907 Vol. 1 Sheet 24 (Block 135: 1016 Main St between McKinney & Lamar, Pre-1926 Site Plate)",
        [],
    ),
    "lost_loews_state_theatre_1927": (
        "sanborn08580_004",
        24,
        "1907 Vol. 1 Sheet 24 (Block 135: 1022 Main St at Lamar Ave, Pre-1927 Site Plate)",
        [],
    ),
    "lost_kirby_theatre_1927": (
        "sanborn08580_004",
        24,
        "1907 Vol. 1 Sheet 24 (Block 135: 1002 Main St at McKinney Ave, Pre-1927 Site Plate)",
        [],
    ),
    # Block 22 (Quality Hill: Fannin, San Jacinto, Franklin, Congress) -> 1885 Sheet 9, 1896 Sheet 2, 1907 Vol. 1 Sheet 2
    "lost_house_mansion_quality_hill_1870": (
        "sanborn08580_004",
        2,
        "1885 Sheet 9; 1896 Sheet 2; 1907 Vol. 1 Sheet 2 (Block 22: Bounded by Fannin, Franklin, San Jacinto & Congress)",
        [
            ("sanborn08580_001", 9, "1885 Sheet 9 (Block 22: Thomas W. House Mansion on Quality Hill)"),
            ("sanborn08580_003", 2, "1896 Sheet 2 (Block 22: Fannin, Franklin, San Jacinto & Congress)"),
        ],
    ),
    "lost_azteca_theatre_1927": (
        "sanborn08580_004",
        2,
        "1907 Vol. 1 Sheet 2 (Block 22: 1112 Congress Ave between Fannin & San Jacinto)",
        [("sanborn08580_003", 2, "1896 Sheet 2 (Block 22: Congress Ave between Fannin & San Jacinto)")],
    ),
    # Block 47 (San Jacinto, Caroline, Preston, Prairie) -> 1896 Sheet 4, 1907 Vol. 1 Sheet 4
    "lost_lincoln_theatre_1926": (
        "sanborn08580_004",
        4,
        "1907 Vol. 1 Sheet 4 (Block 47: 1211 Prairie Ave between San Jacinto & Caroline)",
        [("sanborn08580_003", 4, "1896 Sheet 4 (Block 47: Prairie Ave between San Jacinto & Caroline)")],
    ),
    # I&GN Passenger Depot (Congress & Emanuel) -> 1907 Vol. 1 Sheet 12
    "lost_IGN_depot_1880": (
        "sanborn08580_004",
        12,
        "1907 Vol. 1 Sheet 12 (Congress Ave & Emanuel: 'I. & G. N. R. R. Passenger Station' & Roundhouse)",
        [],
    ),
    # MKT Katy Station (Main St Viaduct at White Oak Bayou) -> 1907 Vol. 2 Sheet 7
    "lost_mkt_station_1914": (
        "sanborn08580_005",
        7,
        "1907 Vol. 2 Sheet 7 (M. K. & T. Ry. Freight & Terminal Yard along White Oak / Buffalo Bayou)",
        [],
    ),
    # SA&AP Depot (South End / Blodgett) -> 1924-1950 Vol. 11 Sheet 1
    "lost_sap_aransas_pass_depot_1890": (
        "sanborn08580_027",
        1,
        "1924-1950 Vol. 11 Sheet 1 (South Main & Blodgett Corridor Overview)",
        [],
    ),
    # Block 59 (Milam, Louisiana, Prairie, Texas) -> 1907 Vol. 1 Sheet 3
    "lost_galveston_houston_interurban_terminal_1911": (
        "sanborn08580_004",
        3,
        "1907 Vol. 1 Sheet 3 (Block 59: Texas Ave between Milam & Louisiana, Pre-1911 Terminal Block)",
        [],
    ),
    # Block 404 (1917 Main St between Calhoun & Pierce) -> 1907 Vol. 1 Sheet 50 & 1896 Sheet 33
    "lost_scanlan_mansion_1891": (
        "sanborn08580_004",
        50,
        "1896 Sheet 33 & 1907 Vol. 1 Sheet 50 (Block 404: 1917 Main St between Calhoun & Pierce, Pink Brick Turreted Mansion)",
        [
            ("sanborn08580_003", 33, "1896 Sheet 33 (Block 404: T. H. Scanlan Mansion at 1917 Main St)"),
            ("sanborn08580_004", 49, "1907 Vol. 1 Sheet 49 (Adjacent West Side of 1900 Main St, Block 405)"),
        ],
    ),
    # Block 71 (San Jacinto, Caroline, Texas, Capitol) -> 1885 Sheet 11, 1896 Sheet 8, 1907 Vol. 1 Sheet 8
    "lost_rice_home_rusk_1860": (
        "sanborn08580_004",
        8,
        "1885 Sheet 11; 1896 Sheet 8; 1907 Vol. 1 Sheet 8 (Block 71: San Jacinto to Caroline)",
        [("sanborn08580_001", 11, "1885 Sheet 11 (Block 71: San Jacinto & Capitol/Rusk Precinct)")],
    ),
    # Montrose (4515 Yoakum) -> 1924-1950 Vol. 11
    "lost_ross_sterling_mansion_montrose_1912": (
        "sanborn08580_027",
        1,
        "1924-1950 Vol. 11 Sheet 1 (Montrose / Museum District South End Overview)",
        [],
    ),
    # Block 125 / 277 (1600 Polk at Crawford) -> 1907 Vol. 1 Sheet 25 & Sheet 35
    "lost_cullinan_mansion_1906": (
        "sanborn08580_004",
        35,
        "1907 Vol. 1 Sheet 35 & Sheet 25 (Polk Ave & Crawford St, John F. Dickson / J. S. Cullinan Precinct)",
        [("sanborn08580_004", 25, "1907 Vol. 1 Sheet 25 (Adjacent Block 125: Polk, Dallas, La Branch, Crawford)")],
    ),
    # Block 271 / 296 (1316 Main at Polk/Clay) -> 1907 Vol. 1 Sheet 33
    "lost_dickson_mansion_main_1896": (
        "sanborn08580_004",
        33,
        "1907 Vol. 1 Sheet 33 (Blocks 271 & 296: West Side of 1200-1300 Main St at Polk & Clay)",
        [("sanborn08580_004", 34, "1907 Vol. 1 Sheet 34 (East Side of 1200-1300 Main St at Polk & Clay)")],
    ),
    # Block 272 (1217 Main at Polk) -> 1907 Vol. 1 Sheet 34
    "lost_concordia_club_1903": (
        "sanborn08580_004",
        34,
        "1907 Vol. 1 Sheet 34 (Block 272: East Side of 1200 Main St at Polk Ave)",
        [("sanborn08580_004", 8, "1907 Vol. 1 Sheet 8 (Block 71: Earlier Concordia Club at San Jacinto & Texas)")],
    ),
    # Block 77 (Caroline, Austin, Capitol, Rusk) -> 1907 Vol. 1 Sheet 17
    "lost_houston_academy_clopper_1858": (
        "sanborn08580_004",
        17,
        "1907 Vol. 1 Sheet 17 (Block 77: 'Houston High School' Bounded by Capitol, Rusk, Caroline & Austin)",
        [],
    ),
    # Fourth Ward (San Felipe & Tirrell) -> 1907 Vol. 1 Sheet 87
    "lost_colored_high_school_1893": (
        "sanborn08580_004",
        87,
        "1907 Vol. 1 Sheet 87 (Fourth Ward: 'Negro High School' at San Felipe & Tirrell)",
        [],
    ),
    # Block 101 (Crawford, Jackson, Texas, Capitol) -> 1907 Vol. 1 Sheet 18
    "lost_incarnate_word_academy_1873": (
        "sanborn08580_004",
        18,
        "1907 Vol. 1 Sheet 18 (Block 101: 'Incarnate Word Academy' & 'Annunciation R. C. Church' at Crawford & Texas/Capitol)",
        [],
    ),
    # Block 399 (Crawford, La Branch, Calhoun, Pierce) -> 1907 Vol. 1 Sheet 51
    "lost_st_joseph_infirmary_1887": (
        "sanborn08580_004",
        51,
        "1907 Vol. 1 Sheet 51 (Block 399: 'St. Joseph Infirmary' Bounded by Crawford, La Branch, Calhoun & Pierce)",
        [],
    ),
    # Allen Parkway (Fourth Ward / Buffalo Bayou) -> 1907 Vol. 1 Sheet 86
    "lost_jefferson_davis_hospital_1937": (
        "sanborn08580_004",
        86,
        "1907 Vol. 1 Sheet 86 (Old City Cemetery / Founders Memorial & Andrews/Heiner Corridor on Buffalo Bayou)",
        [],
    ),
    # H&TC / Southern Pacific Hospital -> 1907 Vol. 2 Sheet 2
    "lost_sunset_hospital_sp_1911": (
        "sanborn08580_005",
        2,
        "1907 Vol. 2 Sheet 2 (Predecessor 'Houston Infirmary' / H&TC Railroad Hospital at Washington Ave)",
        [],
    ),
    # West End Park (Andrews & Heiner) -> 1907 Vol. 1 Sheet 86
    "lost_west_end_park_1905": (
        "sanborn08580_004",
        86,
        "1907 Vol. 1 Sheet 86 (Andrews & Heiner Streets: 'Baseball Grounds' & Grand Stand)",
        [],
    ),
    # Mid-century ballparks & amusement parks -> 1924-1955 Supplement / Vol. 11 / Vol. 12
    "lost_buff_stadium_1928": (
        "sanborn08580_030",
        1,
        "1924-1955 Supplement Sheet 1 (East End / Polk & Cullen Corridor Overview)",
        [],
    ),
    "lost_colt_stadium_1962": (
        "sanborn08580_027",
        1,
        "1924-1950 Vol. 11 Sheet 1 (South Main & Kirby Corridor Pre-Stadium Overview)",
        [],
    ),
    "lost_luna_park_1924": (
        "sanborn08580_005",
        1,
        "1907 Vol. 2 Sheet 1 (White Oak Bayou & Houston Ave Northside Overview)",
        [],
    ),
    "lost_playland_park_1940": (
        "sanborn08580_027",
        1,
        "1924-1950 Vol. 11 Sheet 1 (South Main & Old Spanish Trail Corridor Overview)",
        [],
    ),
    "lost_astroworld_1968": (
        "sanborn08580_027",
        1,
        "1924-1950 Vol. 11 Sheet 1 (South Main & Kirby Corridor Overview)",
        [],
    ),
    # Antioch Baptist Church (Fourth Ward) -> 1907 Vol. 1 Sheet 87
    "lost_antioch_old_tabernacle_1879": (
        "sanborn08580_004",
        87,
        "1907 Vol. 1 Sheet 87 (Fourth Ward: 'Antioch Baptist Church' at Robin & Smith/Shaw)",
        [],
    ),
    # Emancipation Park (Third Ward) -> 1924-1950 Vol. 11
    "lost_emancipation_park_1918_pavilion": (
        "sanborn08580_027",
        1,
        "1924-1950 Vol. 11 Sheet 1 (Third Ward / South End Volume Overview)",
        [],
    ),
    # Pilgrim Building (W. Dallas & Bagby) -> 1907 Vol. 1 Sheet 22
    "lost_pilgrim_building_1926": (
        "sanborn08580_004",
        22,
        "1907 Vol. 1 Sheet 22 (Fourth Ward: Dallas Ave & Bagby St, Pre-1926 Site Plate)",
        [],
    ),
    # Odd Fellows Temple (Louisiana & Rusk, Block 83) -> 1907 Vol. 1 Sheet 7
    "lost_odd_fellows_temple_fourth_ward_1908": (
        "sanborn08580_004",
        7,
        "1907 Vol. 1 Sheet 7 (Block 83: 'Odd Fellows Temple' at Louisiana St & Rusk Ave)",
        [],
    ),
    # Frost Town (Spruce, Race, Arch, Gable on Buffalo Bayou) -> 1907 Vol. 1 Sheet 89 & Sheet 90
    "lost_frost_town_germantown_cottages_1850": (
        "sanborn08580_004",
        89,
        "1907 Vol. 1 Sheet 89 & Sheet 90 (Frost Town: Arch, Race, Spruce, Pine & Gable Streets on Buffalo Bayou)",
        [("sanborn08580_004", 90, "1907 Vol. 1 Sheet 90 (Eastern Frost Town: Gable, Runnels, Maple & Schrimpf Streets)")],
    ),
    # Magnolia Brewery / Foot of Main & Buffalo Bayou -> 1907 Vol. 1 Sheet 5 & 1885 Sheet 1
    "lost_magnolia_brewery_foot_of_main_1893": (
        "sanborn08580_004",
        5,
        "1885 Sheet 1 & 1907 Vol. 1 Sheet 5 (Buffalo Bayou Bend at Preston/Franklin Bridge & Foot of Main)",
        [("sanborn08580_001", 1, "1885 Sheet 1 (Buffalo Bayou Wharves & Foot of Main Street)")],
    ),
    # Houston Turn-Verein (Block 53, Prairie & Austin) -> 1907 Vol. 1 Sheet 13
    "lost_turnverein_hall_1908": (
        "sanborn08580_004",
        13,
        "1907 Vol. 1 Sheet 13 (Block 53: 'Houston Turnverein' Club House, Dance Hall & Bowling Alleys at Prairie & Austin)",
        [],
    ),
    # Temple Beth Israel (1870 Franklin Ave on Sheet 10; 1908 Crawford & Lamar on Sheet 25)
    "lost_temple_beth_israel_1908": (
        "sanborn08580_004",
        25,
        "1907 Vol. 1 Sheet 25 (Block 126: Crawford & Lamar 1908 Site; plus 1870 Temple on Sheet 10, Block 105)",
        [("sanborn08580_004", 10, "1907 Vol. 1 Sheet 10 (Block 105: Earlier 1870 'Temple Beth Israel' on Franklin Ave)")],
    ),
    # Fifth Ward Cotton Compress -> 1907 Vol. 2 Sheet 68 & Sheet 7
    "lost_fifth_ward_cotton_compress_1892": (
        "sanborn08580_005",
        68,
        "1907 Vol. 2 Sheet 68 & Sheet 7 (Fifth Ward Steam Cotton Compress & Special Industrial Risks)",
        [("sanborn08580_005", 7, "1907 Vol. 2 Sheet 7 (Planters Compress Co. & MKT Freight Yards)")],
    ),
}


def _resolve_sanborn_sheet(
    sanborn_catalog: dict, loc_vol: str | None, loc_sheet: int | None
) -> tuple[str | None, str | None, str | None, int | None, str | None]:
    """Resolves (loc_vol, printed_sheet_num) by matching s['code'] == f'{loc_sheet:04d}' first, returning (preview_url, hires_url, loc_url, sp, code)."""
    if not loc_vol or not loc_sheet:
        return None, None, None, None, None
    target_code = f"{int(loc_sheet):04d}"
    for v in sanborn_catalog["volumes"]:
        if v["id"] == loc_vol:
            v_sheets = v.get("sheets", [])
            loc_res_base = str(v.get("loc_resource_url") or v.get("loc_url") or f"https://www.loc.gov/item/{loc_vol}/")
            matched_s = next((s for s in v_sheets if s.get("code") == target_code), None)
            if not matched_s:
                matched_s = next((s for s in v_sheets if s.get("sp") == int(loc_sheet)), None)
            if matched_s:
                sp = int(matched_s["sp"])
                code = str(matched_s.get("code", target_code))
                preview_url = matched_s["preview_url"]
                hires_url = matched_s["hires_url"]
                loc_url = str(matched_s.get("loc_url") or f"{loc_res_base}?sp={sp}")
                return preview_url, hires_url, loc_url, sp, code
            preview_url = v.get("iiif_highres_template", v["iiif_template"]).format(sheet=int(loc_sheet))
            return preview_url, preview_url, f"{loc_res_base}?sp={int(loc_sheet)}", int(loc_sheet), target_code
    return None, None, None, None, None


def main() -> None:
    # 1. Write sanborn_catalog.json
    sanborn_catalog = build_sanborn_catalog()
    sanborn_path = DATA_DIR / "sanborn_catalog.json"
    sanborn_path.write_text(json.dumps(sanborn_catalog, indent=2), encoding="utf-8")
    print(f"Wrote {sanborn_path} ({len(sanborn_catalog['volumes'])} volumes, {sanborn_catalog['total_digitized_sheets']} sheets)")

    # 2. Build overlays.json['lost_houston'] with surveyed HCAD parcel fitting & orientation
    p_by_hcad = load_parcel_index()
    all_entries = LOST_HOUSTON_PART_1 + LOST_HOUSTON_PART_2 + LOST_HOUSTON_PART_3
    features = []
    photos_path = DATA_DIR / "building_photos.json"
    photos_db = json.loads(photos_path.read_text(encoding="utf-8")) if photos_path.exists() else {"photos": []}
    for k in list(photos_db.keys()):
        if k.startswith("lost_"):
            del photos_db[k]
    photo_list = [
        p for p in photos_db.get("photos", [])
        if not str(p.get("building_id", "")).startswith("lost_")
    ]

    for entry in all_entries:
        eid = entry["id"]
        lon = float(entry["lon"])
        lat = float(entry["lat"])
        width_m = float(entry["width_m"])
        depth_m = float(entry["depth_m"])
        site_hcads = list(entry.get("site_hcad_nums", []))
        address = str(entry["address"])

        if eid in EXPLICIT_HCAD_FIXES:
            site_hcads, _, lon, lat, width_m, depth_m, address = EXPLICIT_HCAD_FIXES[eid]

        rings = []
        best_edge_len = -1.0
        parcel_ang = None
        for h in site_hcads:
            for r in p_by_hcad.get(h, []):
                rings.append(r["ring"])
                if r["max_edge_m"] > best_edge_len:
                    best_edge_len = r["max_edge_m"]
                    parcel_ang = r["angle"]

        cat_ang = float(entry.get("angle", -34.65))
        if cat_ang == 115.0 or eid in ("lost_hotel_brazos_1898", "lost_macatee_hotel_1906"):
            ang = -25.0  # H&TC Grand Central / Southern Pacific Station & Washington Ave corridor
        elif cat_ang == 15.0:
            ang = -14.2  # MKT Katy Station at White Oak Bayou
        elif cat_ang == 12.0:
            ang = 0.5  # 1937 Jefferson Davis Hospital on Allen Pkwy
        elif cat_ang == 82.0:
            ang = -34.65  # SA&AP Depot at Blodgett
        elif eid == "lost_cullinan_mansion_1906":
            ang = -34.65  # 1600 Polk at Crawford on Borden Downtown grid
        elif parcel_ang is not None:
            ang = parcel_ang
            if -38.0 <= ang <= -30.0 and not (-35.2 <= ang <= -34.1):
                ang = -34.65
        else:
            ang = -34.65 if abs(cat_ang - 35.0) < 5.0 else cat_ang

        if (
            rings
            and max(width_m, depth_m) <= 95.0
            and eid
            not in (
                "lost_grand_central_station_1887",
                "lost_southern_pacific_station_1934",
                "lost_mkt_station_1914",
                "lost_magnolia_brewery_foot_of_main_1893",
                "lost_frost_town_germantown_cottages_1850",
            )
        ):
            lon, lat, width_m, depth_m = fit_rect_in_parcel(
                lon, lat, width_m, depth_m, ang, rings, setback_m=1.5, max_shift_m=35.0
            )

        coords = make_footprint_polygon(
            lon=lon,
            lat=lat,
            width_m=width_m,
            depth_m=depth_m,
            angle_deg=ang,
            shape=entry.get("shape", "rect"),
        )
        loc_vol = entry.get("loc_volume_id")
        loc_sheet = entry.get("loc_sheet_num")
        sanborn_ref = str(entry.get("sanborn_ref", ""))
        extra_plates: list[tuple[str, int, str]] = []
        if eid in VERIFIED_SANBORN_REFS:
            loc_vol, loc_sheet, sanborn_ref, extra_plates = VERIFIED_SANBORN_REFS[eid]

        (
            sanborn_sheet_image_url,
            sanborn_sheet_hires_url,
            sanborn_sheet_loc_url,
            loc_sp,
            loc_sheet_code,
        ) = _resolve_sanborn_sheet(sanborn_catalog, loc_vol, loc_sheet)

        photo_url = entry.get("photo_url") or sanborn_sheet_image_url
        photo_credit = entry.get("photo_credit") or (
            f"Library of Congress Sanborn Map ({sanborn_ref})" if sanborn_sheet_image_url else None
        )
        yb = int(entry["year_built"])
        yd = int(entry["year_demolished"])
        primary_hcad = site_hcads[0] if site_hcads else ""

        props = {
            "id": eid,
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
            "address": address,
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
            "sanborn_ref": sanborn_ref,
            "source": f"Sanborn Fire Insurance Atlas ({sanborn_ref}) · Preservation Houston Lost Houston Archive",
            "loc_volume_id": loc_vol,
            "loc_sheet_num": loc_sheet,
            "loc_sp": loc_sp,
            "loc_sheet_code": loc_sheet_code,
            "sanborn_sheet_image_url": sanborn_sheet_image_url,
            "sanborn_sheet_hires_url": sanborn_sheet_hires_url,
            "sanborn_sheet_loc_url": sanborn_sheet_loc_url,
            "photo_url": photo_url,
            "photo_credit": photo_credit,
            "blurb": entry["blurb"],
            "historic_significance": entry["blurb"],
            "description": entry["blurb"],
            "lon": lon,
            "lng": lon,
            "lat": lat,
            "label_lng": lon,
            "label_lat": lat,
        }

        if entry.get("photo_url"):
            photo_list.append({
                "building_id": entry["id"],
                "landmark_name": entry["name"],
                "address": address,
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
            primary_year = (
                1885 if loc_vol == "sanborn08580_001"
                else 1890 if loc_vol == "sanborn08580_002"
                else 1896 if loc_vol == "sanborn08580_003"
                else 1907 if loc_vol in ("sanborn08580_004", "sanborn08580_005")
                else 1950
            )
            vol_short = (
                f"1885 Sheet {loc_sheet}" if loc_vol == "sanborn08580_001"
                else f"1890 Sheet {loc_sheet}" if loc_vol == "sanborn08580_002"
                else f"1896 Sheet {loc_sheet}" if loc_vol == "sanborn08580_003"
                else f"1907 Vol. 1 Sheet {loc_sheet}" if loc_vol == "sanborn08580_004"
                else f"1907 Vol. 2 Sheet {loc_sheet}" if loc_vol == "sanborn08580_005"
                else f"1924-1950 Vol. 11 Sheet {loc_sheet}" if loc_vol == "sanborn08580_027"
                else f"1924-1955 Sheet {loc_sheet}"
            )
            paren_idx = sanborn_ref.find("(")
            block_suffix = f" {sanborn_ref[paren_idx:]}" if paren_idx != -1 else ""
            photo_list.append({
                "building_id": entry["id"],
                "landmark_name": entry["name"],
                "address": address,
                "image_url": sanborn_sheet_image_url,
                "thumb_url": sanborn_sheet_image_url,
                "full_url": sanborn_sheet_hires_url or sanborn_sheet_image_url,
                "photo_year": primary_year,
                "era_label": f"Sanborn Plate: {vol_short}{block_suffix}",
                "caption": f"Digitized Library of Congress Sanborn Fire Insurance Map plate showing the block and surrounding context for {entry['name']} ({vol_short}{block_suffix}).",
                "attribution": "Library of Congress Geography & Map Division",
                "source_url": sanborn_sheet_loc_url or "https://www.loc.gov/collections/sanborn-maps/",
            })
        for ex_vol, ex_sheet, ex_label in extra_plates:
            ex_prev, ex_hi, ex_loc, _, _ = _resolve_sanborn_sheet(sanborn_catalog, ex_vol, ex_sheet)
            if ex_prev:
                ex_year = (
                    1885 if ex_vol == "sanborn08580_001"
                    else 1890 if ex_vol == "sanborn08580_002"
                    else 1896 if ex_vol == "sanborn08580_003"
                    else 1907 if ex_vol in ("sanborn08580_004", "sanborn08580_005")
                    else 1950
                )
                photo_list.append({
                    "building_id": entry["id"],
                    "landmark_name": entry["name"],
                    "address": address,
                    "image_url": ex_prev,
                    "thumb_url": ex_prev,
                    "full_url": ex_hi or ex_prev,
                    "photo_year": ex_year,
                    "era_label": f"Sanborn Plate: {ex_label}",
                    "caption": f"Comparative Library of Congress Sanborn Fire Insurance Map plate for {entry['name']}: {ex_label}.",
                    "attribution": "Library of Congress Geography & Map Division",
                    "source_url": ex_loc or "https://www.loc.gov/collections/sanborn-maps/",
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
