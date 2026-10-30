"""Systematic multi-cohort expansion, collision gating, and HOLC sync for Lost Houston (Milestone 2).

Implements pure Functional Core logic for:
  - Cohort 1: Curated survey, archival, HMRC contractor, and midcentury landmarks (`curated_landmark`)
  - Cohort 2: Demolished City of Houston designated landmarks (`coh_landmark_demolished`)
  - Cohort 3: Demolished National Register of Historic Places properties (`nrhp_demolished`)
  - Cohort 4: Vanished 1915 Red Book of Houston places (`red_book_1915_vanished`)
  - Cohort 5: 2020-2026 demolished pre-1940 historic teardowns (`historic_teardown_2020_2026`)
"""

from __future__ import annotations

import csv
import json
import math
import pickle
import re
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

from shapely.geometry import Point, Polygon, shape
from shapely.strtree import STRtree

from atlas_pipeline.schema import (
    LOST_HOUSTON_COHORT_LABELS,
    LOST_HOUSTON_VALID_COHORTS,
    MAX_VALID_CAMA_DATE_YEAR,
    MIN_VALID_YEAR,
    classify_main_structure_demolition_permit,
    normalize_houston_address_keys,
    sanitize_atlas_text,
)

# Migrated from build_permits_and_contractors.py so build_lost_houston_and_sanborn.py is the
# single authoritative writer of overlays["lost_houston"].
DEMOLISHED_CONTRACTOR_SITES: list[dict[str, Any]] = [
    {
        "id": "lost_rb_220_avondale",
        "name": "Russell Brown Model Residence (Plan No. 211)",
        "subtitle": "Two-Story Avondale Residence from 'Modern Homes, Book Number Two'",
        "category": "Gilded Age Mansion",
        "style": "Craftsman / American Foursquare",
        "neighborhood": "Montrose / Avondale",
        "address": "220 AVONDALE ST",
        "site_hcad_nums": ["1203760010003"],
        "year_built": 1914,
        "year_demolished": 1998,
        "year_demolished_qualifier": "circa",
        "demolition_cause": "Demolished c. 1998 for residential redevelopment on Avondale Street.",
        "replaced_by": "1999 single-family residence at 220 Avondale St",
        "architect": "The Russell Brown Company (Plan No. 211)",
        "builder": "The Russell Brown Company",
        "builder_source": "HMRC: 'Modern Homes, Book Number Two' (c. 1917), Plan 211, p. 15",
        "source_url": "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/12354/rec/1",
        "photo_url": "https://cdm17006.contentdm.oclc.org/digital/api/singleitem/collection/books/id/12354/thumbnail",
        "sanborn_material": "wood_frame",
        "sanborn_material_label": "Yellow - Two-Story Wood Frame Residence",
        "stories": 2,
        "height_m": 8.8,
        "center": [-95.3849, 29.7468],
        "description": "Two-story residence in Avondale Addition built in 1914 by The Russell Brown Company and featured as Plan No. 211 on Page 15 of 'Modern Homes, Book Number Two' (HMRC); demolished c. 1998.",
    },
    {
        "id": "lost_rb_3904_day",
        "name": "Russell Brown Model Residence (Plan No. 217)",
        "subtitle": "1913 Craftsman Residence from 'Modern Homes, Book Number Two'",
        "category": "Gilded Age Mansion",
        "style": "Craftsman",
        "neighborhood": "Montrose",
        "address": "3904 DAY ST",
        "site_hcad_nums": [],
        "year_built": 1913,
        "year_demolished": 1972,
        "year_demolished_qualifier": "circa",
        "demolition_cause": "Cleared during post-WWII multifamily and townhome redevelopment in Montrose.",
        "replaced_by": "Montrose residential redevelopment along Day Street",
        "architect": "The Russell Brown Company (Plan No. 217)",
        "builder": "The Russell Brown Company",
        "builder_source": "HMRC: 'Modern Homes, Book Number Two' (c. 1917), Plan 217, p. 23",
        "source_url": "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/12362/rec/1",
        "photo_url": "https://cdm17006.contentdm.oclc.org/digital/api/singleitem/collection/books/id/12362/thumbnail",
        "sanborn_material": "wood_frame",
        "sanborn_material_label": "Yellow - Two-Story Wood Frame Craftsman Residence",
        "stories": 2,
        "height_m": 8.5,
        "center": [-95.3882, 29.7385],
        "description": "Two-story Craftsman residence erected in 1913 at 3904 Day Street by The Russell Brown Company and featured as Plan No. 217 on Page 23 of 'Modern Homes, Book Number Two' (verified via Gary Coover HMRC Index).",
    },
    {
        "id": "lost_rb_5300_san_jacinto",
        "name": "Russell Brown Model Residence (Plan No. 200)",
        "subtitle": "1917 Museum District Residence Featured on Pages 2-4 of 'Modern Homes'",
        "category": "Gilded Age Mansion",
        "style": "Prairie / Mediterranean Revival",
        "neighborhood": "Museum District",
        "address": "5300 SAN JACINTO ST",
        "site_hcad_nums": [],
        "year_built": 1917,
        "year_demolished": 1968,
        "year_demolished_qualifier": "circa",
        "demolition_cause": "Demolished during mid-century institutional and commercial expansion along San Jacinto Street.",
        "replaced_by": "Museum District institutional/commercial parcel at 5300 San Jacinto St",
        "architect": "The Russell Brown Company (Plan No. 200)",
        "builder": "The Russell Brown Company",
        "builder_source": "HMRC: 'Modern Homes, Book Number Two' (c. 1917), Plan 200, p. 3",
        "source_url": "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/12342/rec/1",
        "photo_url": "https://cdm17006.contentdm.oclc.org/digital/api/singleitem/collection/books/id/12342/thumbnail",
        "sanborn_material": "brick",
        "sanborn_material_label": "Pink - Two-Story Brick Residence",
        "stories": 2,
        "height_m": 9.0,
        "center": [-95.3849, 29.7255],
        "description": "Two-story brick residence at 5300 San Jacinto Street in the Museum District, built in 1917 by The Russell Brown Company and showcased as Plan No. 200 on Pages 2-4 of 'Modern Homes, Book Number Two' (HMRC).",
    },
    {
        "id": "lost_rb_3404_yoakum",
        "name": "Russell Brown Model Residence (Plan No. 202)",
        "subtitle": "1916 Yoakum Boulevard Residence from 'Modern Homes, Book Number Two'",
        "category": "Gilded Age Mansion",
        "style": "Craftsman / Prairie",
        "neighborhood": "Montrose",
        "address": "3404 YOAKUM BLVD",
        "site_hcad_nums": ["0261760010003"],
        "year_built": 1916,
        "year_demolished": 1974,
        "year_demolished_qualifier": "before",
        "demolition_cause": "Demolished on or before 1974 for residential replacement construction on Yoakum Boulevard.",
        "replaced_by": "1974 building at 3404 Yoakum Blvd",
        "architect": "The Russell Brown Company (Plan No. 202)",
        "builder": "The Russell Brown Company",
        "builder_source": "HMRC: 'Modern Homes, Book Number Two' (c. 1917), Plan 202, p. 6",
        "source_url": "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/12345/rec/1",
        "photo_url": "https://cdm17006.contentdm.oclc.org/digital/api/singleitem/collection/books/id/12345/thumbnail",
        "sanborn_material": "wood_frame",
        "sanborn_material_label": "Yellow - Two-Story Wood Frame Residence",
        "stories": 2,
        "height_m": 8.8,
        "center": [-95.3941, 29.7420],
        "description": "Two-story residence at 3404 Yoakum Boulevard, built in 1916 by The Russell Brown Company and featured as Plan No. 202 on Page 6 of 'Modern Homes, Book Number Two' (HMRC); replaced by 1974.",
    },
    {
        "id": "lost_rb_3617_montrose",
        "name": "Russell Brown Model Residence (Plan No. 213)",
        "subtitle": "1917 Montrose Boulevard Mansion from 'Modern Homes, Book Number Two'",
        "category": "Gilded Age Mansion",
        "style": "Classical / Prairie Revival",
        "neighborhood": "Montrose",
        "address": "3617 MONTROSE BLVD",
        "site_hcad_nums": [],
        "year_built": 1917,
        "year_demolished": 1966,
        "year_demolished_qualifier": "circa",
        "demolition_cause": "Razed during mid-century commercial redevelopment of Montrose Boulevard.",
        "replaced_by": "Montrose Boulevard commercial corridor",
        "architect": "The Russell Brown Company (Plan No. 213)",
        "builder": "The Russell Brown Company",
        "builder_source": "HMRC: 'Modern Homes, Book Number Two' (c. 1917), Plan 213, p. 17",
        "source_url": "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/12356/rec/1",
        "photo_url": "https://cdm17006.contentdm.oclc.org/digital/api/singleitem/collection/books/id/12356/thumbnail",
        "sanborn_material": "brick",
        "sanborn_material_label": "Pink - Two-Story Brick Mansion",
        "stories": 2,
        "height_m": 9.2,
        "center": [-95.3911, 29.7399],
        "description": "Grand two-story Montrose Boulevard mansion at 3617 Montrose Boulevard built in 1917 by The Russell Brown Company and featured as Plan No. 213 on Page 17 of 'Modern Homes, Book Number Two' (HMRC).",
    },
    {
        "id": "lost_rb_811_lovett",
        "name": "Russell Brown Model Residence (Plan No. 215)",
        "subtitle": "1916 Lovett Boulevard Residence from 'Modern Homes, Book Number Two'",
        "category": "Gilded Age Mansion",
        "style": "Georgian / Colonial Revival",
        "neighborhood": "Montrose",
        "address": "811 LOVETT BLVD",
        "site_hcad_nums": [],
        "year_built": 1916,
        "year_demolished": 1972,
        "year_demolished_qualifier": "before",
        "demolition_cause": "Demolished on or before 1972 for townhome construction along Lovett Boulevard.",
        "replaced_by": "1972 townhome complex at 811 Lovett Blvd",
        "architect": "The Russell Brown Company (Plan No. 215)",
        "builder": "The Russell Brown Company",
        "builder_source": "HMRC: 'Modern Homes, Book Number Two' (c. 1917), Plan 215, p. 20",
        "source_url": "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/12359/rec/1",
        "photo_url": "https://cdm17006.contentdm.oclc.org/digital/api/singleitem/collection/books/id/12359/thumbnail",
        "sanborn_material": "brick",
        "sanborn_material_label": "Pink - Two-Story Brick Residence",
        "stories": 2,
        "height_m": 9.0,
        "center": [-95.3902, 29.7429],
        "description": "Two-story brick residence at 811 Lovett Boulevard, built in 1916 by The Russell Brown Company and featured as Plan No. 215 on Pages 20-21 of 'Modern Homes, Book Number Two' (HMRC); replaced in 1972.",
    },
    {
        "id": "lost_rb_4007_montrose",
        "name": "Russell Brown Model Residence (Plan No. 221)",
        "subtitle": "1918 Montrose Boulevard Residence from 'Modern Homes, Book Number Two'",
        "category": "Gilded Age Mansion",
        "style": "Mediterranean / Prairie Revival",
        "neighborhood": "Montrose",
        "address": "4007 MONTROSE BLVD",
        "site_hcad_nums": [],
        "year_built": 1918,
        "year_demolished": 1969,
        "year_demolished_qualifier": "circa",
        "demolition_cause": "Demolished during post-WWII commercial expansion along Montrose Boulevard.",
        "replaced_by": "4000 block Montrose Boulevard commercial parcel",
        "architect": "The Russell Brown Company (Plan No. 221)",
        "builder": "The Russell Brown Company",
        "builder_source": "HMRC: 'Modern Homes, Book Number Two' (c. 1917), Plan 221, p. 27",
        "source_url": "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/12366/rec/1",
        "photo_url": "https://cdm17006.contentdm.oclc.org/digital/api/singleitem/collection/books/id/12366/thumbnail",
        "sanborn_material": "brick",
        "sanborn_material_label": "Pink - Two-Story Brick Residence",
        "stories": 2,
        "height_m": 9.0,
        "center": [-95.3912, 29.7362],
        "description": "Two-story brick residence at 4007 Montrose Boulevard, built in 1918 by The Russell Brown Company and featured as Plan No. 221 on Pages 27 and 29 of 'Modern Homes, Book Number Two' (HMRC).",
    },
    {
        "id": "lost_rb_1208_lovett",
        "name": "Russell Brown Model Residence (Plan No. 310)",
        "subtitle": "1916 Lovett Boulevard Residence from 'Modern Homes, Book Number Two'",
        "category": "Gilded Age Mansion",
        "style": "Colonial / Prairie Revival",
        "neighborhood": "Montrose",
        "address": "1208 LOVETT BLVD",
        "site_hcad_nums": [],
        "year_built": 1916,
        "year_demolished": 1975,
        "year_demolished_qualifier": "circa",
        "demolition_cause": "Demolished in the 1970s during commercial and residential redevelopment along Lovett Boulevard.",
        "replaced_by": "1208 Lovett Blvd redevelopment",
        "architect": "The Russell Brown Company (Plan No. 310)",
        "builder": "The Russell Brown Company",
        "builder_source": "HMRC: 'Modern Homes, Book Number Two' (c. 1917), Plan 310, p. 34",
        "source_url": "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/12373/rec/1",
        "photo_url": "https://cdm17006.contentdm.oclc.org/digital/api/singleitem/collection/books/id/12373/thumbnail",
        "sanborn_material": "brick",
        "sanborn_material_label": "Pink - Two-Story Brick Residence",
        "stories": 2,
        "height_m": 9.2,
        "center": [-95.3942, 29.7433],
        "description": "Two-story brick residence at 1208 Lovett Boulevard, built in 1915-1916 by The Russell Brown Company and featured as Plan No. 310 on Pages 34-35 of 'Modern Homes, Book Number Two' (HMRC).",
    },
]

# Cohort 1 Midcentury, Downtown & Civic Monument Additions (verified against HCAD parcels + standing monuments)
LOST_HOUSTON_PART_4_MIDCENTURY_AND_CIVIC: list[dict[str, Any]] = [
    {
        "id": "lost_foleys_downtown_1947",
        "name": "Foley's Downtown Flagship Department Store (1947)",
        "subtitle": "Windowless Postwar Modernist Retail Landmark Covering Block 264",
        "category": "Early Skyscraper & Commercial",
        "address": "1110 Main St (Full Block Bounded by Main, Travis, Lamar & Dallas)",
        "neighborhood": "Downtown",
        "year_built": 1947,
        "year_demolished": 2013,
        "demolition_cause": "Imploded on September 22, 2013 after closing in May 2013 for construction of the Hilcorp tower at 1111 Travis.",
        "replaced_by": "Hilcorp Energy Tower (1111 Travis St, built 2014)",
        "site_hcad_nums": ["0012440000001"],
        "architect": "Kenneth Franzheim (with industrial designer Raymond Loewy)",
        "style": "Postwar Modernist / Streamlined Retail Block",
        "sanborn_material": "stone_concrete",
        "sanborn_material_label": "Blue - Texas Cordova Shellstone, Minnesota Granite & Reinforced Concrete",
        "stories": 10,
        "height_m": 46.0,
        "sanborn_ref": "1924-1950 Vol. 1 Sheet 32 (Block 264: 1110 Main St at Lamar & Dallas)",
        "lon": -95.366179,
        "lat": 29.756178,
        "width_m": 74.0,
        "depth_m": 74.0,
        "angle": -34.65,
        "shape": "rect",
        "blurb": "Opened on October 20, 1947 as Federated Department Stores' revolutionary windowless, fully air-conditioned flagship designed by Kenneth Franzheim with interiors by Raymond Loewy (expanded from six to ten stories in 1957), Foley's Downtown anchored Main Street retail and Houston's Thanksgiving Day Parade for 66 years before its implosion in September 2013.",
    },
    {
        "id": "lost_houston_club_building_1955",
        "name": "Houston Club Building (1955)",
        "subtitle": "18-Story Mid-Century Modernist Office & Private Club Tower on Rusk Street",
        "category": "Early Skyscraper & Commercial",
        "address": "811 Rusk St at Milam St (Block 82)",
        "neighborhood": "Downtown",
        "year_built": 1955,
        "year_demolished": 2015,
        "demolition_cause": "Imploded on October 18, 2015 by Skanska to clear Block 82 for Capitol Tower / Bank of America Tower.",
        "replaced_by": "Bank of America Tower (800 Capitol St, built 2017)",
        "site_hcad_nums": ["0010820000001"],
        "architect": "J. Russ Baty",
        "style": "Mid-Century Modernist Curtain Wall & Brick",
        "sanborn_material": "iron_steel",
        "sanborn_material_label": "Blue/Gray - Steel Frame, Aluminum Ribbon Windows & Buff Brick",
        "stories": 18,
        "height_m": 72.0,
        "sanborn_ref": "1907 Vol. 1 Sheet 12 (Block 82: Rusk, Capitol, Travis & Milam Pre-1955 Site)",
        "loc_volume_id": "sanborn08580_004",
        "loc_sheet_num": 12,
        "lon": -95.36496,
        "lat": 29.75986,
        "width_m": 44.0,
        "depth_m": 58.0,
        "angle": -34.65,
        "shape": "L",
        "blurb": "Designed by Houston architect J. Russ Baty and completed in 1955 at 811 Rusk Street on Block 82, this 18-story Modernist office tower housed the influential Houston Club across its monumental lower club floors until the club relocated to One Shell Plaza and Skanska imploded the tower in October 2015.",
    },
    {
        "id": "lost_cotton_hotel_1913",
        "name": "The Cotton Hotel / Montague Hotel (1913)",
        "subtitle": "11-Story Early-Skyscraper Commercial Hotel at Rusk & Travis Streets",
        "category": "Grand Hotel",
        "address": "818 Rusk St at Travis St (SE Corner, Block 82)",
        "neighborhood": "Downtown",
        "year_built": 1913,
        "year_demolished": 2014,
        "demolition_cause": "Demolished in 2014 along with the adjacent Houston Club Building for redevelopment of Block 82.",
        "replaced_by": "Bank of America Tower (800 Capitol St, built 2017)",
        "site_hcad_nums": ["0010820000001"],
        "architect": "C. D. Hill & Company (built for Almon Cotton)",
        "style": "Chicago School / Classical Revival Brick & Terra Cotta",
        "sanborn_material": "brick",
        "sanborn_material_label": "Pink/Blue - 11-Story Steel & Reinforced Concrete Hotel with Brick Curtain Wall",
        "stories": 11,
        "height_m": 42.0,
        "sanborn_ref": "1907 Vol. 1 Sheet 12 (Block 82: SE Corner Rusk & Travis Pre-1913 Site)",
        "loc_volume_id": "sanborn08580_004",
        "loc_sheet_num": 12,
        "lon": -95.36442,
        "lat": 29.75940,
        "width_m": 28.0,
        "depth_m": 32.0,
        "angle": -34.65,
        "shape": "rect",
        "blurb": "Opened on February 17, 1913 for real-estate developer Almon Cotton at the corner of Rusk and Travis Streets on Block 82, the 11-story fireproof Cotton Hotel (later the Montague Hotel) advertised 'every room an outside room' for traveling businesspeople over a century before its 2014 demolition.",
    },
    {
        "id": "lost_downtown_central_ymca_1941",
        "name": "Downtown Central YMCA Building (1941)",
        "subtitle": "10-Story Art Moderne Civic & Athletic Landmark Designed by Kenneth Franzheim",
        "category": "Civic & Government",
        "address": "1600 Louisiana St at Pease St",
        "neighborhood": "Downtown",
        "year_built": 1941,
        "year_demolished": 2011,
        "demolition_cause": "Sold to Chevron after opening of the Tellepsen Family Downtown YMCA across the street and imploded on June 12, 2011.",
        "replaced_by": "Chevron campus parcel at 1600 Louisiana St",
        "site_hcad_nums": ["0060030000031"],
        "architect": "Kenneth Franzheim",
        "style": "Art Moderne / Stripped Classical Buff Brick & Limestone",
        "sanborn_material": "brick",
        "sanborn_material_label": "Pink/Blue - Ten-Story Steel Frame with Buff Brick & Limestone Facade",
        "stories": 10,
        "height_m": 38.0,
        "sanborn_ref": "1924-1950 Vol. 1 (1600 Louisiana St between Pease & Leeland)",
        "lon": -95.371708,
        "lat": 29.754015,
        "width_m": 52.0,
        "depth_m": 58.0,
        "angle": -34.65,
        "shape": "L",
        "blurb": "Completed in 1941 from plans by Kenneth Franzheim, the 10-story Downtown Central YMCA at 1600 Louisiana Street combined gymnasiums, a tiled indoor natatorium, community meeting rooms, and upper-story single-room residences for seven decades until its implosion in June 2011.",
    },
    {
        "id": "lost_sheraton_lincoln_hotel_1962",
        "name": "Sheraton-Lincoln Hotel (1962)",
        "subtitle": "28-Story International Style Hotel Tower Anchoring Lincoln Liberty Center",
        "category": "Grand Hotel",
        "address": "777 Polk St at Louisiana & Milam Streets",
        "neighborhood": "Downtown",
        "year_built": 1962,
        "year_demolished": 2011,
        "demolition_cause": "Shuttered during the 1986 Houston oil bust and dismantled floor-by-floor between 2010 and 2011 after standing vacant for 24 years.",
        "replaced_by": "Surface parking lot opposite One Allen Center / 711 Polk St",
        "site_hcad_nums": [],
        "architect": "Quinn & Christiansen with Kenneth Franzheim & Associates",
        "style": "International Style Steel, White Marble & Glass Curtain Wall",
        "sanborn_material": "iron_steel",
        "sanborn_material_label": "Blue/Gray - 28-Story Steel Frame & Curtain Wall Tower on Podium",
        "stories": 28,
        "height_m": 108.0,
        "sanborn_ref": "Downtown West Block Bounded by Polk, Dallas, Milam & Louisiana",
        "lon": -95.36932,
        "lat": 29.75560,
        "width_m": 46.0,
        "depth_m": 56.0,
        "angle": -34.65,
        "shape": "T",
        "blurb": "Opened in June 1962 as Houston's sleek Space Age convention hotel welcoming Apollo-era dignitaries to Lincoln Liberty Center at 777 Polk Street, the 28-story Sheraton-Lincoln closed during the 1986 energy downturn and loomed empty over the downtown skyline for nearly a quarter-century before its dismantling in 2011.",
    },
    {
        "id": "lost_medical_arts_building_1926",
        "name": "Medical Arts Building (1926)",
        "subtitle": "16-Story Gothic Revival Terra-Cotta Skyscraper for Houston Physicians",
        "category": "Early Skyscraper & Commercial",
        "address": "1215 Walker Ave at Caroline St (Block 130)",
        "neighborhood": "Downtown",
        "year_built": 1926,
        "year_demolished": 1986,
        "demolition_cause": "Demolished in 1986 during Houston Center site assembly after tenants had shifted to the Texas Medical Center.",
        "replaced_by": "Fulbright Tower / Houston Center Plaza (1301 McKinney St, Block 130)",
        "site_hcad_nums": ["0011300000001"],
        "architect": "Sanguinet, Staats, Hedrick & Gottlieb",
        "style": "Gothic Revival Cream Brick & Polychrome Terra Cotta Skyscraper",
        "sanborn_material": "stone_concrete",
        "sanborn_material_label": "Blue - 16-Story Reinforced Concrete Frame with Brick & Terra Cotta",
        "stories": 16,
        "height_m": 64.0,
        "sanborn_ref": "1907 Vol. 1 Sheet 24 (Block 130: Walker & Caroline NW Corner Pre-1926 Site)",
        "loc_volume_id": "sanborn08580_004",
        "loc_sheet_num": 24,
        "lon": -95.36182,
        "lat": 29.75575,
        "width_m": 38.0,
        "depth_m": 42.0,
        "angle": -34.65,
        "shape": "L",
        "blurb": "Constructed in 1926 at Walker Avenue and Caroline Street from designs by Sanguinet, Staats, Hedrick & Gottlieb, the 16-story Gothic Revival Medical Arts Building concentrated hundreds of Houston medical and dental practices under one roof prior to the post-WWII rise of the Texas Medical Center; it was demolished in 1986.",
    },
    {
        "id": "lost_first_baptist_downtown_1905",
        "name": "First Baptist Church Downtown Sanctuary (1905)",
        "subtitle": "Classical Revival Domed Downtown Sanctuary at Lamar & Fannin Streets",
        "category": "Neighborhood & Cultural Landmark",
        "address": "1020 Lamar Ave at Fannin St",
        "neighborhood": "Downtown",
        "year_built": 1905,
        "year_demolished": 1974,
        "demolition_cause": "Sold and razed in 1974 when First Baptist Church relocated to the Loop 610 West campus, paving the way for First City Tower.",
        "replaced_by": "First City Tower (1001 Fannin St, built 1980)",
        "site_hcad_nums": ["0011350000001"],
        "architect": "R. D. Steele (expanded 1924-1953)",
        "style": "Classical Revival Brick & Limestone with Central Dome",
        "sanborn_material": "brick",
        "sanborn_material_label": "Pink/Blue - Domed Masonry Sanctuary & Education Annex",
        "stories": 3,
        "height_m": 22.0,
        "sanborn_ref": "1907 Vol. 1 Sheet 23 (Lamar Ave & Fannin St Church Row)",
        "loc_volume_id": "sanborn08580_004",
        "loc_sheet_num": 23,
        "lon": -95.36420,
        "lat": 29.75612,
        "width_m": 36.0,
        "depth_m": 44.0,
        "angle": -34.65,
        "shape": "cross",
        "blurb": "Erected at Lamar Avenue and Fannin Street at the heart of downtown Houston's historic church corridor, First Baptist Church's domed Classical Revival sanctuary and adjoining education buildings anchored downtown worship for nearly seventy years before the congregation relocated to Katy Freeway at Loop 610 and the site was cleared in 1974.",
    },
    {
        "id": "lost_robertson_stadium_1942",
        "name": "Robertson Stadium / HISD Public School Stadium (1942)",
        "subtitle": "WPA Concrete Bowl, Original Home of the 1960 AFL Champion Houston Oilers & UH Cougars",
        "category": "Historic Stadium & Amusement Park",
        "address": "3874 Holman St at Cullen Blvd (University of Houston)",
        "neighborhood": "Third Ward / University of Houston",
        "year_built": 1942,
        "year_demolished": 2012,
        "demolition_cause": "Demolished in December 2012 to build TDECU Stadium on the identical campus footprint.",
        "replaced_by": "TDECU Stadium (University of Houston, opened 2014)",
        "site_hcad_nums": [],
        "architect": "Harry D. Payne & Alfred C. Finn",
        "builder": "Works Progress Administration (WPA) & Fretz Construction Co.",
        "style": "Art Moderne Concrete Bowl Stadium with Twin Entry Pylons",
        "sanborn_material": "stone_concrete",
        "sanborn_material_label": "Blue - Reinforced Concrete Grandstands & Moderne Ticket Pylons",
        "stories": 4,
        "height_m": 26.0,
        "sanborn_ref": "University of Houston Campus at Cullen Blvd & Holman St",
        "lon": -95.34912,
        "lat": 29.72185,
        "width_m": 145.0,
        "depth_m": 115.0,
        "angle": -35.0,
        "shape": "U",
        "blurb": "Dedicated in September 1942 as Houston ISD's Public School Stadium (renamed Jeppesen Stadium in 1958 and Corbin J. Robertson Stadium in 1980), Harry D. Payne and Alfred C. Finn's WPA concrete stadium hosted the Houston Oilers' back-to-back 1960 and 1961 AFL Championships, University of Houston football, and Houston Dynamo MLS Cup seasons before its demolition in December 2012.",
    },
    {
        "id": "lost_delman_theatre_1939",
        "name": "The Delman Theatre (1939)",
        "subtitle": "1,200-Seat Art Moderne Neighborhood Movie Palace on South Main",
        "category": "Historic Theatre & Opera House",
        "address": "4412 Main St at Wheeler Ave",
        "neighborhood": "Midtown / Museum District",
        "year_built": 1939,
        "year_demolished": 1972,
        "year_demolished_qualifier": "circa",
        "demolition_cause": "Closed after suburban multiplex competition and demolished in the early 1970s along the 4400 block of Main Street.",
        "replaced_by": "Vacant / commercial parcel at 4412 Main St",
        "site_hcad_nums": ["0250220000013"],
        "architect": "Pettigrew & Worley",
        "style": "Streamline Moderne / Art Deco Cinema",
        "sanborn_material": "brick",
        "sanborn_material_label": "Pink/Blue - Fireproof Masonry & Steel-Truss Auditorium",
        "stories": 2,
        "height_m": 13.0,
        "sanborn_ref": "1924-1950 Vol. 11 (4412 Main St Corridor)",
        "lon": -95.383637,
        "lat": 29.734204,
        "width_m": 24.0,
        "depth_m": 40.0,
        "angle": -34.65,
        "shape": "rect",
        "blurb": "Opened on November 23, 1939 by Interstate/independent exhibitors at 4412 South Main Street near Wheeler Avenue, the 1,200-seat Delman Theatre was one of Houston's premier Art Moderne neighborhood cinemas alongside the River Oaks, Alabama, and Tower before falling to demolition around 1972.",
    },
    {
        "id": "lost_ost_theatre_1949",
        "name": "The OST Theatre (1949)",
        "subtitle": "Postwar Neighborhood Cinema Landmark on Old Spanish Trail",
        "category": "Historic Theatre & Opera House",
        "address": "2619 Old Spanish Trail",
        "neighborhood": "OST / South Union (Third Ward South)",
        "year_built": 1949,
        "year_demolished": 2015,
        "demolition_cause": "Demolished in May 2015 following decades of adaptive reuse as church and commercial space.",
        "replaced_by": "Cleared commercial parcel on Old Spanish Trail",
        "site_hcad_nums": [],
        "architect": "Jack Corgan (neighborhood cinema circuit)",
        "style": "Postwar Streamline Moderne Brick & Vertical Neon Marquee",
        "sanborn_material": "brick",
        "sanborn_material_label": "Pink - Masonry Auditorium with Stepped Moderne Parapet",
        "stories": 2,
        "height_m": 12.0,
        "sanborn_ref": "Old Spanish Trail Postwar Commercial Corridor (2619 OST)",
        "lon": -95.36795,
        "lat": 29.69890,
        "width_m": 22.0,
        "depth_m": 38.0,
        "angle": 0.0,
        "shape": "rect",
        "blurb": "Opened in 1949 at 2619 Old Spanish Trail with a soaring vertical neon blade marquee and streamlined brick facade, the OST Theatre served postwar Riverside Terrace and South Union moviegoers and later neighborhood congregations until excavators demolished the auditorium in May 2015.",
    },
    {
        "id": "lost_astrohall_1966",
        "name": "The Astrohall (1966)",
        "subtitle": "Y-Shaped Exposition Complex & Rodeo Livestock Hall Adjacent to the Astrodome",
        "category": "Civic & Government",
        "address": "8500 Kirby Dr (NRG Park South Exposition Site)",
        "neighborhood": "Astrodome / Medical Center South",
        "year_built": 1966,
        "year_demolished": 2000,
        "demolition_cause": "Demolished in 2000 to make way for the 1.4-million-square-foot Reliant Center (now NRG Center).",
        "replaced_by": "NRG Center (built 2002 on NRG Park grounds)",
        "site_hcad_nums": [],
        "architect": "Lloyd, Morgan & Jones",
        "style": "Mid-Century Modernist Long-Span Exhibition Pavilion",
        "sanborn_material": "iron_steel",
        "sanborn_material_label": "Gray/Blue - Long-Span Steel Frame & Precast Concrete Exhibition Wings",
        "stories": 2,
        "height_m": 14.0,
        "sanborn_ref": "Astrodomain Complex South of the 1965 Astrodome (8500 Kirby Dr)",
        "lon": -95.41060,
        "lat": 29.68155,
        "width_m": 160.0,
        "depth_m": 110.0,
        "angle": 0.0,
        "shape": "T",
        "blurb": "Completed in early 1966 immediately south of the Astrodome by Judge Roy Hofheinz to house the Houston Livestock Show and Rodeo, the Offshore Technology Conference, and major national conventions (expanded into the Astroarena complex in 1971), the sprawling Astrohall was demolished in 2000 for construction of NRG Center.",
    },
]

# Cohort 2: Verified demolished City of Houston designated landmarks (`USER_DEMOD == 'Y'` in COH GIS).
# Note: `coh_lm_081` (Jefferson Davis Hospital Buildings, 1801 Allen Pkwy, HPO #98LD74) merges onto
# existing Cohort 1 `lost_jefferson_davis_hospital_1937` so the site is never double-counted.
# Note: `coh_lm_089` (Fifth Ward Hotel Building, 1206 Nance St, HPO #00L090) has `USER_DEMOD == ''`
# and IS EXTANT; `sanitize_coh_landmarks_overlay` clears the erroneous COH GIS `USER_HCAD_NUM='Demolished'` glitch.
COH_LANDMARK_ENRICHMENTS_ON_CURATED: dict[str, dict[str, Any]] = {
    "lost_jefferson_davis_hospital_1937": {
        "coh_landmark_id": "coh_lm_081",
        "coh_landmark_code": "98LD74",
        "site_hcad_nums": ["1244130010002"],
        "year_demolished": 1999,
        "extra_citation": {
            "label": "City of Houston Historic Preservation Office Designated Landmark #98LD74 (Demolished May 1999)",
            "url": "https://houstontx.gov/planning/HistoricPres/landmarks.html",
        },
    },
}

COH_DEMOLISHED_LANDMARKS_METADATA: dict[str, dict[str, Any]] = {
    "coh_lm_044": {
        "id": "lost_coh_lm_bill_mraz_dance_hall_1948",
        "landmark_code": "98L043",
        "name": "Bill Mraz Dance Hall (1948)",
        "subtitle": "Historic Czech & Polka Community Dance Hall on West 34th Street (COH Landmark #98L043)",
        "category": "Neighborhood & Cultural Landmark",
        "address": "835 W 34th St",
        "neighborhood": "Garden Oaks / Oak Forest",
        "site_hcad_nums": ["1495760010001"],
        "year_built": 1948,
        "year_built_qualifier": "exact",
        "year_demolished": 2005,
        "year_demolished_qualifier": "exact",
        "demolition_cause": "Destroyed by a structure fire in 2005 after operating for more than half a century as Houston's famed Czech, polka, and big-band dance hall.",
        "replaced_by": "Commercial redevelopment at 835 W 34th St",
        "architect": "Bill Mraz (owner-builder)",
        "style": "Postwar Vernacular Dance Hall with Hardwood Maple Dance Floor",
        "sanborn_material": "wood_frame",
        "sanborn_material_label": "Yellow - Clear-Span Timber Truss & Wood Frame Dance Hall",
        "stories": 1,
        "height_m": 6.5,
        "width_m": 24.0,
        "depth_m": 34.0,
        "angle": 0.0,
        "blurb": "Built in 1948 by bandleader Bill Mraz at 835 West 34th Street and designated City of Houston Landmark #98L043, the Bill Mraz Dance Hall anchored Houston's Czech-Texan polka, country, and community dance tradition until a 2005 fire destroyed the landmark prior to the strengthened 2010 preservation ordinance.",
    },
    "coh_lm_107": {
        "id": "lost_coh_lm_mallalieu_umc_1926",
        "landmark_code": "03L107",
        "name": "Mallalieu United Methodist Church & Parsonage (1926)",
        "subtitle": "First Ward Church Framed with Salvaged WWI Camp Logan Timber (COH Landmark #03L107)",
        "category": "Neighborhood & Cultural Landmark",
        "address": "1918-1920 Hickory St",
        "neighborhood": "First Ward",
        "site_hcad_nums": ["0050930000027"],
        "year_built": 1926,
        "year_built_qualifier": "exact",
        "year_demolished": 2015,
        "year_demolished_qualifier": "exact",
        "demolition_cause": "Demolished in 2015 following church closure and townhome parcel replatting in First Ward.",
        "replaced_by": "First Ward townhome infill on the 1900 block of Hickory St",
        "architect": "Sanguinet & Staats (COH landmark record attribution)",
        "style": "Craftsman / Vernacular Gothic Wood Frame Sanctuary & Bungalow Parsonage",
        "sanborn_material": "wood_frame",
        "sanborn_material_label": "Yellow - Wood Frame Sanctuary Built from Salvaged Camp Logan Lumber",
        "stories": 2,
        "height_m": 9.5,
        "width_m": 15.0,
        "depth_m": 24.0,
        "angle": 0.0,
        "blurb": "Built in 1926 at 1918-1920 Hickory Street in First Ward using lumber salvaged from Houston's World War I Camp Logan Army cantonment and designated City of Houston Landmark #03L107, Mallalieu United Methodist Church and its adjoining parsonage stood until their demolition in 2015.",
    },
    "coh_lm_115": {
        "id": "lost_coh_lm_beatty_west_building_1912",
        "landmark_code": "04L118",
        "name": "Beatty-West Building (1912)",
        "subtitle": "Six-Story Reinforced Concrete Downtown Commercial Landmark (COH Landmark #04L118)",
        "category": "Early Skyscraper & Commercial",
        "address": "815-817 Main St (Block 93)",
        "neighborhood": "Downtown",
        "site_hcad_nums": ["1305070010001"],
        "year_built": 1912,
        "year_built_qualifier": "exact",
        "year_demolished": 2007,
        "year_demolished_qualifier": "exact",
        "demolition_cause": "Demolished in 2007 during clearance of Block 93 following a statutory pre-2010 landmark waiver period.",
        "replaced_by": "BG Group Place / 811 Main garage & tower block (Block 93)",
        "architect": "Early Houston Commercial Concrete Design (built for Dr. Franklin Beatty & J. M. West)",
        "style": "Chicago School Commercial Style Brick & Terra Cotta",
        "sanborn_material": "stone_concrete",
        "sanborn_material_label": "Blue/Pink - Six-Story Reinforced Concrete Frame with Brick Veneer",
        "stories": 6,
        "height_m": 24.0,
        "width_m": 16.0,
        "depth_m": 11.5,
        "angle": -34.65,
        "center": [-95.36389, 29.75756],
        "blurb": "Erected in 1912 for oilman James M. West and Dr. Franklin Beatty at 815-817 Main Street on Block 93 (adjacent to the San Jacinto/Bender Hotel) and designated City of Houston Landmark #04L118, the six-story Beatty-West Building was demolished in 2007 prior to the 2010 preservation ordinance reforms.",
    },
    "coh_lm_220": {
        "id": "lost_coh_lm_tuttle_house_1931",
        "landmark_code": "08L193",
        "name": "William B. & Susan Tuttle House (1931)",
        "subtitle": "William D. Bordeaux Manorial Residence in River Oaks (COH Landmark #08L193)",
        "category": "Gilded Age Mansion",
        "address": "2223 Inwood Dr",
        "neighborhood": "River Oaks",
        "site_hcad_nums": ["0601550450004"],
        "year_built": 1931,
        "year_built_qualifier": "exact",
        "year_demolished": 2019,
        "year_demolished_qualifier": "exact",
        "demolition_cause": "Demolished under August 2019 demolition permit because pre-2010 standard Landmark designation (without Protected Landmark covenant) permitted demolition after a waiting period.",
        "replaced_by": "2020 replacement residence at 2223 Inwood Dr",
        "architect": "William D. Bordeaux",
        "style": "French Manorial / Norman Revival Brick Residence",
        "sanborn_material": "brick",
        "sanborn_material_label": "Pink - Two-Story Brick Veneer Manorial Residence",
        "stories": 2,
        "height_m": 9.5,
        "width_m": 18.0,
        "depth_m": 16.0,
        "angle": 0.0,
        "blurb": "Designed in 1931 by architect William D. Bordeaux at 2223 Inwood Drive in River Oaks Country Club Estates and designated City of Houston Landmark #08L193 in 2008, the Tuttle House was demolished in 2019, illustrating the legal vulnerability of non-Protected landmarks.",
    },
    "coh_lm_232": {
        "id": "lost_coh_lm_mount_carmel_baptist_1928",
        "landmark_code": "08PL57",
        "name": "Mount Carmel Missionary Baptist Church & Parsonage (1928)",
        "subtitle": "Historic Freedmen's Town Brick Sanctuary Built by Fretz Construction Co. (COH Protected Landmark #08PL57)",
        "category": "Neighborhood & Cultural Landmark",
        "address": "1407 Valentine St",
        "neighborhood": "Fourth Ward / Freedmen's Town",
        "site_hcad_nums": [],
        "year_built": 1928,
        "year_built_qualifier": "exact",
        "year_demolished": 2008,
        "year_demolished_qualifier": "exact",
        "demolition_cause": "Demolished on May 16, 2008 under an emergency city dangerous-building abatement order shortly after receiving Protected Landmark status (#08PL57).",
        "replaced_by": "Fourth Ward residential infill on Valentine St",
        "architect": "Fretz Construction Company",
        "builder": "Fretz Construction Company",
        "style": "Twentieth-Century Vernacular Gothic Brick Sanctuary",
        "sanborn_material": "brick",
        "sanborn_material_label": "Pink - Two-Story Red Brick Sanctuary & Adjoining Frame Parsonage",
        "stories": 2,
        "height_m": 10.5,
        "width_m": 16.0,
        "depth_m": 24.0,
        "angle": -34.65,
        "blurb": "Built in 1928 by Fretz Construction Company at 1407 Valentine Street in the Freedmen's Town National Register Historic District for a congregation tracing its roots to Reconstruction, Mount Carmel Missionary Baptist Church and its parsonage were demolished under a controversial municipal dangerous-building order in May 2008 despite Protected Landmark designation #08PL57.",
    },
    "coh_lm_268": {
        "id": "lost_coh_lm_bettes_house_1928",
        "landmark_code": "09L215",
        "name": "T. J. & Ruth Bettes House (1928)",
        "subtitle": "Charles W. Oliver Southern Colonial Residence in River Oaks (COH Landmark #09L215)",
        "category": "Gilded Age Mansion",
        "address": "1059 Kirby Dr",
        "neighborhood": "River Oaks",
        "site_hcad_nums": ["0601530340031"],
        "year_built": 1928,
        "year_built_qualifier": "exact",
        "year_demolished": 2019,
        "year_demolished_qualifier": "exact",
        "demolition_cause": "Demolished following a September 2019 residential demolition permit on Kirby Drive.",
        "replaced_by": "2020 estate residence at 1059 Kirby Dr",
        "architect": "Charles W. Oliver",
        "style": "Southern Colonial Revival",
        "sanborn_material": "brick",
        "sanborn_material_label": "Pink - Two-Story Colonial Revival Masonry & Wood Residence",
        "stories": 2,
        "height_m": 9.2,
        "width_m": 19.0,
        "depth_m": 15.0,
        "angle": 0.0,
        "blurb": "Designed by Houston architect Charles W. Oliver in 1928 for mortgage banker Torrey James Bettes and Ruth Bettes at 1059 Kirby Drive and designated City of Houston Landmark #09L215, the Southern Colonial residence was demolished in 2019 for new estate construction.",
    },
    "coh_lm_354": {
        "id": "lost_coh_lm_lucian_carroll_house_1926",
        "landmark_code": "13L275",
        "name": "Lucian & Kate Carroll House (1926)",
        "subtitle": "Early River Oaks Colonial Revival Residence Designed by Owner Lucian Carroll (COH Landmark #13L275)",
        "category": "Gilded Age Mansion",
        "address": "3247 Inwood Dr",
        "neighborhood": "River Oaks",
        "site_hcad_nums": ["0601310000003"],
        "year_built": 1926,
        "year_built_qualifier": "exact",
        "year_demolished": 2019,
        "year_demolished_qualifier": "circa",
        "demolition_cause": "Cleared under 2019-2020 residential demolition permit for estate redevelopment on Inwood Drive.",
        "replaced_by": "Vacant / redeveloping estate lot at 3247 Inwood Dr",
        "architect": "Lucian Carroll (Lucien Carroll)",
        "style": "Colonial Revival",
        "sanborn_material": "brick",
        "sanborn_material_label": "Pink - Two-Story Early River Oaks Colonial Revival Residence",
        "stories": 2,
        "height_m": 9.0,
        "width_m": 18.0,
        "depth_m": 15.0,
        "angle": 0.0,
        "blurb": "Built in 1926 during the inaugural building phase of River Oaks Country Club Estates at 3247 Inwood Drive and designated City of Houston Landmark #13L275 in 2013, the Lucian and Kate Carroll House was demolished in 2019-2020.",
    },
}

# Cohort 3: All 16 NRHP `is_demolished_nrhp == True` features keyed by `refnum`.
# NOTE: `85002771` (Arthur B. Cohn House, 1711 Rusk Ave) has `relocated_not_demolished=True` because
# the 1905 house designed by Michael DeChaumes was saved and moved in 2003 to 900 Avenida de las Americas
# (Discovery Green); it receives full historical metadata in `overlays["nrhp_properties"]` and
# `search_index.json` (`year_built: 1905`) while being excluded from `overlays["lost_houston"]`.
NRHP_DEMOLISHED_METADATA: dict[str, dict[str, Any]] = {
    "78002944": {
        "id": "lost_nrhp_78002944_turn_verein_almeda",
        "clean_name": "Houston Turn-Verein Clubhouse (Almeda Rd, 1929)",
        "category": "Neighborhood & Cultural Landmark",
        "address": "5202 Almeda Rd",
        "neighborhood": "Museum District",
        "site_hcad_nums": ["0420660000084"],
        "year_built": 1929,
        "year_built_qualifier": "exact",
        "year_demolished": 1993,
        "year_demolished_qualifier": "exact",
        "architect": "Joseph Finger",
        "style": "Art Deco / Zigzag Moderne Brick & Cast Stone Clubhouse",
        "sanborn_material": "brick",
        "sanborn_material_label": "Pink/Blue - Two-Story Art Deco Brick & Cast Stone Clubhouse",
        "stories": 2,
        "height_m": 11.5,
        "width_m": 28.0,
        "depth_m": 38.0,
        "demolition_cause": "Demolished in 1993 despite 1978 National Register listing after years of vacancy along Almeda Road.",
        "replaced_by": "Commercial parcel at 5202 Almeda Rd",
        "blurb": "Designed by architect Joseph Finger in 1929 after the Houston Turn-Verein left its 1913 Prairie Avenue downtown hall, this Art Deco brick and cast-stone clubhouse, ballroom, and bowling center at 5202 Almeda Road was listed on the National Register in March 1978 (#78002944) but demolished in 1993.",
    },
    "78002948": {
        "id": "lost_nrhp_78002948_south_texas_national_bank",
        "clean_name": "South Texas National Bank Building (1910)",
        "category": "Early Skyscraper & Commercial",
        "address": "215 Main St",
        "neighborhood": "Downtown",
        "site_hcad_nums": [],
        "year_built": 1910,
        "year_built_qualifier": "exact",
        "year_demolished": 1983,
        "year_demolished_qualifier": "exact",
        "architect": "C. D. Hill & Company (1922 wings by William Ward Watkin)",
        "style": "Monumental Neoclassical Granite & Ionic Colonnade Bank",
        "sanborn_material": "stone_concrete",
        "sanborn_material_label": "Blue - Monumental Granite Classical Banking Hall",
        "stories": 3,
        "height_m": 16.0,
        "width_m": 24.0,
        "depth_m": 32.0,
        "angle": -34.65,
        "demolition_cause": "Demolished in 1983 for speculative downtown surface parking five years after entering the National Register.",
        "replaced_by": "200 block Main St parking / redevelopment parcel",
        "blurb": "Designed by C. D. Hill & Co. in 1910 with 1922 additions by Rice Institute architect William Ward Watkin, the Neoclassical granite South Texas National Bank at 215 Main Street was listed on the National Register in December 1978 (#78002948) before its 1983 demolition galvanized downtown preservation advocacy.",
    },
    "83004434": {
        "id": "lost_nrhp_83004434_elkins_house",
        "clean_name": "Elkins House (Houston Heights MPS)",
        "category": "Gilded Age Mansion",
        "address": "602 E 9th St",
        "neighborhood": "Houston Heights",
        "site_hcad_nums": ["0202480000015"],
        "year_built": 1905,
        "year_built_qualifier": "circa",
        "year_demolished": 1992,
        "year_demolished_qualifier": "before",
        "architect": "Houston Heights Vernacular Builder",
        "style": "Queen Anne / Late Victorian Cottage",
        "sanborn_material": "wood_frame",
        "sanborn_material_label": "Yellow - Wood Frame Queen Anne Cottage",
        "stories": 1,
        "height_m": 6.0,
        "width_m": 12.0,
        "depth_m": 16.0,
        "demolition_cause": "Demolished prior to construction of a 1993 replacement house, before Municipal Houston Heights Historic District protections existed.",
        "replaced_by": "1993 single-family house at 602 E 9th St",
        "blurb": "Listed individually under the Houston Heights Multiple Resource Area nomination in June 1983 (#83004434), the turn-of-the-century Queen Anne Elkins House at 602 East 9th Street was demolished before 1993 because federal NRHP listing imposes no municipal restriction on private demolition.",
    },
    "83004473": {
        "id": "lost_nrhp_83004473_mcdonald_house",
        "clean_name": "McDonald House (Houston Heights MPS)",
        "category": "Gilded Age Mansion",
        "address": "1801 Ashland St",
        "neighborhood": "Houston Heights",
        "site_hcad_nums": ["1324720010001"],
        "year_built": 1895,
        "year_built_qualifier": "circa",
        "year_demolished": 2006,
        "year_demolished_qualifier": "circa",
        "architect": "Houston Heights Victorian Builder",
        "style": "Queen Anne Victorian",
        "sanborn_material": "wood_frame",
        "sanborn_material_label": "Yellow - Wood Frame Victorian Residence",
        "stories": 2,
        "height_m": 8.5,
        "width_m": 13.0,
        "depth_m": 17.0,
        "demolition_cause": "Demolished c. 2006 for townhome / single-family replatting along 18th & Ashland Streets prior to Houston Heights West HPO protections.",
        "replaced_by": "Rebuilt corner residential lots at 1801 Ashland St",
        "blurb": "Built around 1895 during the opening decade of Omaha & South Texas Land Company's Houston Heights streetcar suburb and listed on the National Register in June 1983 (#83004473), the Victorian McDonald House at 1801 Ashland Street was demolished around 2006.",
    },
    "83004478": {
        "id": "lost_nrhp_83004478_schauer_filling_station",
        "clean_name": "Otto Schauer Filling Station (Houston Heights MPS)",
        "category": "Early Skyscraper & Commercial",
        "address": "1400 Oxford St",
        "neighborhood": "Houston Heights",
        "site_hcad_nums": [],
        "year_built": 1925,
        "year_built_qualifier": "circa",
        "year_demolished": 2013,
        "year_demolished_qualifier": "circa",
        "architect": "Early Neighborhood Automobile Service Design",
        "style": "1920s Domestic-Scale Canopy Filling Station",
        "sanborn_material": "brick",
        "sanborn_material_label": "Pink - Early Brick Neighborhood Service Station & Drive-Under Canopy",
        "stories": 1,
        "height_m": 4.8,
        "width_m": 11.0,
        "depth_m": 13.0,
        "demolition_cause": "Removed around 2013 for residential corner redevelopment at 14th and Oxford Streets.",
        "replaced_by": "Residential construction at 1400 Oxford St",
        "blurb": "Listed on the National Register in June 1983 (#83004478) under the Houston Heights MPS as a rare intact 1920s neighborhood automobile service station at 1400 Oxford Street, the Schauer Filling Station was later cleared for residential redevelopment.",
    },
    "84001761": {
        "id": "lost_nrhp_84001761_dexter_house",
        "clean_name": "W. W. Dexter House (Houston Heights MPS)",
        "category": "Gilded Age Mansion",
        "address": "224 W 17th St",
        "neighborhood": "Houston Heights",
        "site_hcad_nums": ["0201280000016"],
        "year_built": 1895,
        "year_built_qualifier": "circa",
        "year_demolished": 1990,
        "year_demolished_qualifier": "circa",
        "architect": "Houston Heights Victorian Builder",
        "style": "Queen Anne Victorian Residence",
        "sanborn_material": "wood_frame",
        "sanborn_material_label": "Yellow - Two-Story Wood Frame Queen Anne Residence",
        "stories": 2,
        "height_m": 9.0,
        "width_m": 14.0,
        "depth_m": 18.0,
        "demolition_cause": "Destroyed and cleared prior to municipal historic district creation along West 17th Street.",
        "replaced_by": "Later residential construction at 224 W 17th St",
        "blurb": "Home of Houston Heights promoter and publisher W. W. Dexter at 224 West 17th Street and listed on the National Register in May 1984 (#84001761), the c. 1895 Queen Anne residence was lost around 1990.",
    },
    "85002771": {
        "id": "lost_nrhp_85002771_cohn_house_relocated",
        "relocated_not_demolished": True,
        "clean_name": "Arthur B. Cohn House (1905; Relocated 2003 from 1711 Rusk Ave)",
        "category": "Gilded Age Mansion",
        "address": "1711 Rusk Ave",
        "neighborhood": "Downtown / East Downtown",
        "site_hcad_nums": ["0011190000015"],
        "year_built": 1905,
        "year_built_qualifier": "exact",
        "year_demolished": 2003,
        "year_demolished_qualifier": "exact",
        "architect": "Michael DeChaumes",
        "style": "Queen Anne / Classical Revival Residence",
        "local_protection_status_override": "Original 1711 Rusk Ave NRHP site vacated in 2003 when the 1905 house was rescued from convention-district clearance and relocated to Avenida de las Americas at Discovery Green",
        "blurb": "Designed in 1905 by architect Michael DeChaumes for Rice Institute accountant Arthur B. Cohn at 1711 Rusk Avenue (NRHP #85002771), the house was moved in 2003 to Discovery Green rather than demolished.",
    },
    "85003238": {
        "id": "lost_nrhp_85003238_fire_engine_house_no_9",
        "clean_name": "Houston Fire Engine House No. 9 (1899)",
        "category": "Civic & Government",
        "address": "1810-1812 Keene St",
        "neighborhood": "Near Northside / Fifth Ward",
        "site_hcad_nums": ["0031900000003"],
        "year_built": 1899,
        "year_built_qualifier": "exact",
        "year_demolished": 1991,
        "year_demolished_qualifier": "circa",
        "architect": "Olle J. Lorehn / W. A. McMillen (civic fire station program)",
        "style": "Romanesque Revival Two-Story Brick Firehouse with Hose Tower",
        "sanborn_material": "brick",
        "sanborn_material_label": "Pink - Two-Story Brick Municipal Fire Station & Drying Tower",
        "stories": 2,
        "height_m": 11.0,
        "width_m": 14.0,
        "depth_m": 24.0,
        "demolition_cause": "Demolished around 1991 following structural deterioration six years after its 1985 National Register listing.",
        "replaced_by": "Cleared/industrial parcel on Keene St north of Downtown",
        "blurb": "Built in 1899 during Houston's transition to a paid professional fire department at 1810-1812 Keene Street near the H&TC rail yards and listed on the National Register in October 1985 (#85003238), two-story brick Fire Engine House No. 9 was demolished around 1991.",
    },
    "87002245": {
        "id": "lost_nrhp_87002245_austin_copeland_house_ii",
        "clean_name": "Austin Copeland House II (Houston Heights MPS)",
        "category": "Gilded Age Mansion",
        "address": "925 Arlington St",
        "neighborhood": "Houston Heights",
        "site_hcad_nums": [],
        "year_built": 1908,
        "year_built_qualifier": "circa",
        "year_demolished": 1992,
        "year_demolished_qualifier": "circa",
        "architect": "Austin Copeland (master Heights builder)",
        "builder": "Austin Copeland",
        "style": "Late Queen Anne / Early Craftsman",
        "sanborn_material": "wood_frame",
        "sanborn_material_label": "Yellow - Wood Frame Residence by Builder Austin Copeland",
        "stories": 2,
        "height_m": 8.5,
        "width_m": 12.0,
        "depth_m": 16.0,
        "demolition_cause": "Demolished in the early 1990s prior to establishment of the Houston Heights South Historic District.",
        "replaced_by": "Later residential construction at 925 Arlington St",
        "blurb": "Erected c. 1908 by prolific early Houston Heights master builder Austin Copeland at 925 Arlington Street and added to the National Register in January 1988 (#87002245), the residence was subsequently demolished prior to local HPO ordinance protections.",
    },
    "94001015": {
        "id": "lost_nrhp_94001015_west_mansion_clear_lake",
        "clean_name": "James M. & Jessie West Mansion (1930)",
        "category": "Gilded Age Mansion",
        "address": "3303 NASA Rd 1",
        "neighborhood": "Clear Lake / Shoreacres Area",
        "site_hcad_nums": [],
        "year_built": 1930,
        "year_built_qualifier": "exact",
        "year_demolished": 2019,
        "year_demolished_qualifier": "exact",
        "architect": "Joseph Finger",
        "builder": "James Stewart & Company / Bellows",
        "style": "Italian Renaissance Revival Villa (45 Rooms, Tile Roof & Loggias)",
        "sanborn_material": "stone_concrete",
        "sanborn_material_label": "Blue/Pink - Stuccoed Hollow Clay Tile, Limestone & Ludowici Clay Tile Roof",
        "stories": 2,
        "height_m": 12.0,
        "width_m": 52.0,
        "depth_m": 28.0,
        "demolition_cause": "Demolished in April 2019 after hurricane damage and unsuccessful preservation campaigns across Clear Lake.",
        "replaced_by": "Waterfront redevelopment tract at 3303 NASA Rd 1",
        "blurb": "Designed in 1929-1930 by architect Joseph Finger as a 17,000-square-foot Italian Renaissance Revival waterfront estate facing Clear Lake for cattle, timber, and oil magnate James Marion 'Silver Dollar Jim' West and Jessie Dudley West (later home to NASA's Lunar Science Institute), the NRHP-listed villa (#94001015) was demolished in April 2019.",
    },
    "97000541": {
        "id": "lost_nrhp_97000541_morris_mary_johnson_house",
        "clean_name": "Morris & Mary Johnson House (Independence Heights / Blossom Heights)",
        "category": "Neighborhood & Cultural Landmark",
        "address": "3818 Spencer St",
        "neighborhood": "West End / Cottage Grove",
        "site_hcad_nums": [],
        "year_built": 1916,
        "year_built_qualifier": "circa",
        "year_demolished": 2005,
        "year_demolished_qualifier": "circa",
        "architect": "Early Twentieth-Century Craftsman Builder",
        "style": "Craftsman Bungalow",
        "sanborn_material": "wood_frame",
        "sanborn_material_label": "Yellow - One-Story Wood Frame Craftsman Bungalow",
        "stories": 1,
        "height_m": 5.5,
        "width_m": 11.0,
        "depth_m": 15.0,
        "demolition_cause": "Cleared during townhouse and commercial redevelopment along Spencer Street in the 2000s.",
        "replaced_by": "Townhome redevelopment on Spencer St",
        "blurb": "Built around 1916 at 3818 Spencer Street adjacent to the Sessums-James House and listed on the National Register in June 1997 (#97000541), the Morris and Mary Johnson House was demolished around 2005.",
    },
    "97000543": {
        "id": "lost_nrhp_97000543_ella_lewis_store_and_houses",
        "clean_name": "Ella Lewis Store & Rental Houses (Independence Heights MPS)",
        "category": "Neighborhood & Cultural Landmark",
        "address": "3404-3408 Cortlandt St",
        "neighborhood": "Independence Heights",
        "site_hcad_nums": ["1284360010005"],
        "year_built": 1920,
        "year_built_qualifier": "circa",
        "year_demolished": 2008,
        "year_demolished_qualifier": "circa",
        "architect": "Independence Heights Vernacular Builder",
        "style": "Two-Story Commercial Storefront & Shotgun Rental Ensemble",
        "sanborn_material": "wood_frame",
        "sanborn_material_label": "Yellow - Wood Frame Neighborhood Store & Shotgun Rental Dwellings",
        "stories": 2,
        "height_m": 7.8,
        "width_m": 16.0,
        "depth_m": 18.0,
        "demolition_cause": "Lost to neglect and residential replatting in Independence Heights during the late 2000s.",
        "replaced_by": "Replatted residential lots at 3404-3408 Cortlandt St",
        "blurb": "Built c. 1920 by Black entrepreneur Ella Lewis in Texas's first African American-incorporated municipality (incorporated 1915) and listed on the National Register in June 1997 (#97000543), this store and rental-house ensemble at 3404-3408 Cortlandt Street documented women-led business ownership in Independence Heights before its loss c. 2008.",
    },
    "97000545": {
        "id": "lost_nrhp_97000545_general_mercantile_store",
        "clean_name": "Independence Heights General Mercantile Store / Burgess Hall",
        "category": "Neighborhood & Cultural Landmark",
        "address": "7322 N Main St",
        "neighborhood": "Independence Heights",
        "site_hcad_nums": [],
        "year_built": 1920,
        "year_built_qualifier": "circa",
        "year_demolished": 2014,
        "year_demolished_qualifier": "circa",
        "architect": "Independence Heights Commercial Builder",
        "style": "Two-Story Wood-Frame Commercial & Fraternal Hall",
        "sanborn_material": "wood_frame",
        "sanborn_material_label": "Yellow - Two-Story Wood Frame Mercantile Store & Upstairs Meeting Hall",
        "stories": 2,
        "height_m": 8.2,
        "width_m": 14.0,
        "depth_m": 20.0,
        "demolition_cause": "Demolished around 2014 after prolonged storm and structural deterioration along North Main Street.",
        "replaced_by": "Cleared commercial lot at 7322 N Main St",
        "blurb": "Anchoring Independence Heights' historic North Main Street business corridor since c. 1920 as a ground-floor general mercantile and upstairs civic/fraternal hall, NRHP property #97000545 (listed June 1997) was lost around 2014.",
    },
    "97000547": {
        "id": "lost_nrhp_97000547_william_mackey_house",
        "clean_name": "William Mackey House (Independence Heights MPS)",
        "category": "Neighborhood & Cultural Landmark",
        "address": "313 E 37th St",
        "neighborhood": "Independence Heights",
        "site_hcad_nums": ["0210820000024"],
        "year_built": 1915,
        "year_built_qualifier": "circa",
        "year_demolished": 2010,
        "year_demolished_qualifier": "circa",
        "architect": "Independence Heights Vernacular Builder",
        "style": "Modified L-Plan Folk Victorian / Craftsman Cottage",
        "sanborn_material": "wood_frame",
        "sanborn_material_label": "Yellow - One-Story Wood Frame L-Plan Residence",
        "stories": 1,
        "height_m": 5.5,
        "width_m": 11.0,
        "depth_m": 15.0,
        "demolition_cause": "Demolished c. 2010 leaving a vacant residential lot in Independence Heights.",
        "replaced_by": "Vacant residential parcel at 313 E 37th St",
        "blurb": "Home of early Independence Heights civic leader and Supreme Camp of the American Woodmen Camp #272 commander William Mackey at 313 East 37th Street, this c. 1915 residence (NRHP #97000547, listed June 1997) was demolished around 2010.",
    },
    "97000549": {
        "id": "lost_nrhp_97000549_ben_jenetter_cyrus_house",
        "clean_name": "Ben C. & Jenetter Cyrus House (Independence Heights MPS)",
        "category": "Neighborhood & Cultural Landmark",
        "address": "525 E 35th St",
        "neighborhood": "Independence Heights",
        "site_hcad_nums": ["0211030000007"],
        "year_built": 1915,
        "year_built_qualifier": "circa",
        "year_demolished": 2009,
        "year_demolished_qualifier": "circa",
        "architect": "Independence Heights Vernacular Builder",
        "style": "Craftsman Bungalow",
        "sanborn_material": "wood_frame",
        "sanborn_material_label": "Yellow - One-Story Wood Frame Craftsman Bungalow",
        "stories": 1,
        "height_m": 5.5,
        "width_m": 11.0,
        "depth_m": 16.0,
        "demolition_cause": "Recorded by Texas Historical Commission as destroyed/gone circa 2009.",
        "replaced_by": "Later infill lot at 525 E 35th St",
        "blurb": "Built around the 1915 incorporation of Independence Heights for Ben C. and Jenetter Cyrus at 525 East 35th Street and listed on the National Register in June 1997 (#97000549), the Craftsman bungalow was lost circa 2009.",
    },
    "98001344": {
        "id": "lost_nrhp_98001344_sessums_james_house",
        "clean_name": "Alexander Sessums-William James House ('Witch's Hat House')",
        "category": "Gilded Age Mansion",
        "address": "3802 Spencer St",
        "neighborhood": "West End / Blossom Heights",
        "site_hcad_nums": [],
        "year_built": 1896,
        "year_built_qualifier": "circa",
        "year_demolished": 1998,
        "year_demolished_qualifier": "circa",
        "architect": "Late Victorian Builder",
        "style": "Queen Anne Victorian with Octagonal Turret Roof",
        "sanborn_material": "wood_frame",
        "sanborn_material_label": "Yellow - Two-Story Wood Frame Queen Anne Residence with Conical Turret",
        "stories": 2,
        "height_m": 10.0,
        "width_m": 14.0,
        "depth_m": 17.0,
        "demolition_cause": "Lost to townhouse redevelopment at Spencer & Blossom around the time of its November 1998 NRHP review.",
        "replaced_by": "Townhouse development at 3802 Spencer St",
        "blurb": "Known locally for its steep octagonal 'witch's hat' Queen Anne turret at 3802 Spencer Street (c. 1896, NRHP #98001344), the Alexander Sessums-William James House was cleared for townhomes despite preservation efforts.",
    },
}

# Iconic extant buildings explicitly shielded by ZeroExtantCollisionGate even if a curated or
# automated record attempts to reuse an address/parcel without being an earlier pre-construction predecessor.
ICONIC_EXTANT_MONUMENTS: tuple[dict[str, Any], ...] = (
    {"name": "The Rice Hotel", "address": "909 TEXAS AVE", "hcad_num": "0010570000009", "year_built": 1913},
    {"name": "Scanlan Building", "address": "405 MAIN ST", "hcad_num": "0010450000009", "year_built": 1909},
    {"name": "1910 Harris County Courthouse", "address": "301 FANNIN ST", "hcad_num": "0010310000001", "year_built": 1910},
    {"name": "Niels Esperson Building", "address": "808 TRAVIS ST", "hcad_num": "0010810000007", "year_built": 1927},
    {"name": "Gulf Building", "address": "712 MAIN ST", "hcad_num": "0010810000007", "year_built": 1929},
    {"name": "Houston City Hall", "address": "901 BAGBY ST", "hcad_num": "0011510000001", "year_built": 1939},
    {"name": "Julia Ideson Building", "address": "500 MCKINNEY ST", "hcad_num": "0011460000001", "year_built": 1926},
    {"name": "Eldorado Ballroom", "address": "2310 ELGIN ST", "hcad_num": "0190900000001", "year_built": 1939},
    {"name": "NRG Astrodome", "address": "8400 KIRBY DR", "hcad_num": "0440930000004", "year_built": 1965},
    {"name": "1924 Jefferson Davis Hospital", "address": "1101 ELDER ST", "hcad_num": "1248540010001", "year_built": 1924},
    {"name": "Fifth Ward Hotel Building", "address": "1206 NANCE ST", "hcad_num": "", "year_built": 1883},
    {"name": "Arthur B. Cohn House", "address": "1711 RUSK AVE", "hcad_num": "0011190000015", "year_built": 1905},
    {"name": "River Oaks Theatre", "address": "2009 W GRAY ST", "hcad_num": "0560950000012", "year_built": 1939},
    {"name": "Alabama Theatre", "address": "2922 S SHEPHERD DR", "hcad_num": "0600810200001", "year_built": 1939},
)

VALID_DATE_QUALIFIERS: frozenset[str] = frozenset({"exact", "circa", "before", "after"})


def format_demolished_lifespan(
    year_built: int,
    year_demolished: int,
    year_built_qualifier: str = "exact",
    year_demolished_qualifier: str = "exact",
) -> dict[str, str]:
    """Build qualifier-aware lifespan display strings without U+2014/U+2013 dashes (DF-7 / UT-7)."""
    qb = (year_built_qualifier or "exact").strip().lower()
    qd = (year_demolished_qualifier or "exact").strip().lower()
    if qb not in VALID_DATE_QUALIFIERS:
        qb = "exact"
    if qd not in VALID_DATE_QUALIFIERS:
        qd = "exact"

    yb = int(year_built)
    yd = int(year_demolished)

    if qb == "before":
        built_disp = f"On or before {yb}"
        built_short = f"<={yb}"
    elif qb == "circa":
        built_disp = f"c. {yb}"
        built_short = f"c. {yb}"
    elif qb == "after":
        built_disp = f"After {yb}"
        built_short = f">={yb}"
    else:
        built_disp = str(yb)
        built_short = str(yb)

    if qd == "before":
        demo_disp = f"before {yd}"
        demo_short = f"pre-{yd}"
    elif qd == "circa":
        demo_disp = f"c. {yd}"
        demo_short = f"c. {yd}"
    elif qd == "after":
        demo_disp = f"after {yd}"
        demo_short = f"post-{yd}"
    else:
        demo_disp = str(yd)
        demo_short = str(yd)

    if qb == "exact" and qd == "exact":
        lifespan = f"{yb}-{yd}"
    else:
        lifespan = f"{built_short} - {demo_short}"

    return {
        "year_built_qualifier": qb,
        "year_demolished_qualifier": qd,
        "year_built_display": sanitize_atlas_text(built_disp),
        "year_demolished_display": sanitize_atlas_text(demo_disp),
        "lifespan_label": sanitize_atlas_text(lifespan),
    }


def compute_symbol_rank(
    source_cohort: str,
    *,
    category: str = "",
    has_photo: bool = False,
    red_book_category: str = "",
) -> int:
    """Assign progressive WebGL/Canvas label priority rank in {1, 2, 3, 4} (DF-1 / UT-4).

    Rank 1 (visible z >= 12.5): Cohort 1 curated/archival monuments & Cohort 2 demolished COH landmarks.
    Rank 2 (visible z >= 14.5): Cohort 3 demolished NRHP sites + Cohort 4 vanished Red Book churches/schools.
    Rank 3 (visible z >= 15.8): Cohort 4 vanished Red Book residences & photographic sites + named historic teardowns.
    Rank 4 (visible z >= 15.8, lowest symbol-sort priority): Cohort 4 commercial rows & Cohort 5 routine teardowns.
    """
    c = (source_cohort or "").strip()
    if c in ("curated_landmark", "coh_landmark_demolished"):
        return 1
    if c == "nrhp_demolished":
        return 2
    if c == "red_book_1915_vanished":
        rb_cat = (red_book_category or category or "").strip().lower()
        if rb_cat in ("church", "school") or "church" in rb_cat or "school" in rb_cat:
            return 2
        if rb_cat == "residence" or has_photo:
            return 3
        return 4
    return 4


def _signed_ring_area(ring: list[list[float]]) -> float:
    area2 = 0.0
    for i in range(len(ring) - 1):
        x1, y1 = ring[i]
        x2, y2 = ring[i + 1]
        area2 += (x1 * y2) - (x2 * y1)
    return 0.5 * area2


def ensure_valid_ccw_ring_6dp(ring_raw: list[list[float]] | list[tuple[float, float]]) -> list[list[float]]:
    """Return a clean, closed, 6-decimal WGS84 CCW exterior ring with zero duplicate spikes (DF-9 / UT-5)."""
    pts: list[list[float]] = []
    for pt in ring_raw:
        p6 = [round(float(pt[0]), 6), round(float(pt[1]), 6)]
        if not pts or p6 != pts[-1]:
            pts.append(p6)
    if len(pts) >= 2 and pts[0] == pts[-1]:
        pts.pop()
    if len(pts) < 3:
        return []
    pts.append(list(pts[0]))
    if _signed_ring_area(pts) < 0:
        pts = list(reversed(pts))
    poly = Polygon(pts)
    if not poly.is_valid or poly.area <= 0:
        repaired = poly.buffer(0)
        if repaired.geom_type == "MultiPolygon" and len(repaired.geoms) > 0:
            repaired = max(repaired.geoms, key=lambda g: g.area)
        if repaired.geom_type == "Polygon" and not repaired.is_empty:
            ext = [[round(float(x), 6), round(float(y), 6)] for x, y in repaired.exterior.coords]
            dedup: list[list[float]] = []
            for p6 in ext:
                if not dedup or p6 != dedup[-1]:
                    dedup.append(p6)
            if len(dedup) >= 4:
                if dedup[0] != dedup[-1]:
                    dedup.append(list(dedup[0]))
                if _signed_ring_area(dedup) < 0:
                    dedup = list(reversed(dedup))
                return dedup
    return pts


def _make_rect_ring_6dp(
    lon: float,
    lat: float,
    width_m: float,
    depth_m: float,
    angle_deg: float,
) -> list[list[float]]:
    hw = max(2.5, float(width_m) / 2.0)
    hd = max(2.5, float(depth_m) / 2.0)
    rad = math.radians(float(angle_deg))
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    m_lat = 110850.0
    m_lon = 111320.0 * math.cos(math.radians(lat))
    corners = ((-hw, -hd), (hw, -hd), (hw, hd), (-hw, hd))
    raw = []
    for x, y in corners:
        rx = x * cos_a - y * sin_a
        ry = x * sin_a + y * cos_a
        raw.append([lon + rx / m_lon, lat + ry / m_lat])
    return ensure_valid_ccw_ring_6dp(raw)


def _infer_street_angle(neighborhood: str, address: str, lon: float, lat: float) -> float:
    addr_u = (address or "").upper()
    nh_u = (neighborhood or "").upper()
    # Downtown Borden survey & Midtown / Early Fourth Ward oblique blocks
    borden_streets = (
        "MAIN ST", "TRAVIS ST", "MILAM ST", "LOUISIANA ST", "SMITH ST", "BAGBY ST",
        "FANNIN ST", "SAN JACINTO ST", "CAROLINE ST", "AUSTIN ST", "LABRANCH ST",
        "CRAWFORD ST", "JACKSON ST", "CHENEVERT ST", "HAMILTON ST", "CHARTES ST",
        "COMMERCE", "FRANKLIN", "CONGRESS", "PRESTON", "PRAIRIE", "TEXAS AVE", "TEXAS ST",
        "CAPITOL", "RUSK", "WALKER", "MCKINNEY", "LAMAR", "DALLAS", "POLK", "CLAY",
        "BELL", "LEELAND", "PEASE", "JEFFERSON", "CALHOUN", "PIERCE", "GRAY",
        "WEBSTER", "HADLEY", "MCGOWEN", "BREMOND", "DENNIS", "DREW", "TUAM",
        "ELGIN", "STUART", "FRANCIS", "HOLMAN", "BERRY", "ALABAMA", "WINBERN",
        "WHEELER", "BLODGETT", "CLEBURNE", "DOWLING", "EMANCIPATION", "LIVE OAK",
        "ST CHARLES", "DELANO", "PAIGE", "ENNIS", "SAMPSON", "YORK", "VALENTINE",
        "WILSON", "ROBIN", "ANDREWS", "SAULNIER", "HEINER", "FREDERICK",
    )
    if "DOWNTOWN" in nh_u or "MIDTOWN" in nh_u or any(s in addr_u for s in borden_streets):
        if -95.390 <= lon <= -95.335 and 29.718 <= lat <= 29.770:
            return -34.65
    return 0.0


def fit_oriented_ghost_footprint(
    lon: float,
    lat: float,
    width_m: float,
    depth_m: float,
    *,
    parcel_records: list[dict[str, Any]] | None = None,
    pre_demo_ring: list[list[float]] | None = None,
    neighborhood: str = "",
    address: str = "",
    explicit_angle: float | None = None,
    fit_rect_fn: Any = None,
) -> tuple[list[list[float]], float, float, str]:
    """Return `(ccw_ring_6dp, label_lon, label_lat, footprint_provenance)` (DF-9 / UT-5).

    Priority:
      1. Surveyed pre-demolition roof footprint (`ms_osm_pre_demo_footprint`) when valid.
      2. Parcel-fitted oriented ghost footprint inside actual HCAD parcel polygon (`ghost_parcel_aligned`).
      3. Street-grid-aligned centroid ghost footprint (`ghost_street_aligned`).
    """
    if pre_demo_ring:
        clean = ensure_valid_ccw_ring_6dp(pre_demo_ring)
        if len(clean) >= 4:
            p = Polygon(clean)
            if p.is_valid and p.area > 1e-11:
                rp = p.representative_point()
                return clean, round(float(rp.x), 6), round(float(rp.y), 6), "ms_osm_pre_demo_footprint"

    ang = explicit_angle if explicit_angle is not None else _infer_street_angle(neighborhood, address, lon, lat)
    use_lon, use_lat = float(lon), float(lat)
    use_w, use_d = float(width_m), float(depth_m)
    provenance = "ghost_street_aligned"

    if parcel_records:
        rings = [r["ring"] for r in parcel_records if r.get("ring")]
        best_r = max(parcel_records, key=lambda r: float(r.get("max_edge_m") or 0.0))
        if explicit_angle is None and best_r.get("angle") is not None:
            ang = float(best_r["angle"])
            if -38.0 <= ang <= -30.0:
                ang = -34.65
        # Anchor candidate near parcel interior when raw point sits on street centerline (>28m away)
        p_lon, p_lat = float(best_r["lon"]), float(best_r["lat"])
        dist_m = math.hypot((use_lon - p_lon) * 96500.0, (use_lat - p_lat) * 110850.0)
        if dist_m <= 65.0:
            use_lon, use_lat = p_lon, p_lat
        if rings and fit_rect_fn is not None:
            use_lon, use_lat, use_w, use_d = fit_rect_fn(
                use_lon,
                use_lat,
                use_w,
                use_d,
                ang,
                rings,
                setback_m=1.2,
                max_shift_m=28.0,
            )
            provenance = "ghost_parcel_aligned"

    ring = _make_rect_ring_6dp(use_lon, use_lat, use_w, use_d, ang)
    poly = Polygon(ring)
    rp = poly.representative_point() if poly.is_valid and not poly.is_empty else Point(use_lon, use_lat)
    return ring, round(float(rp.x), 6), round(float(rp.y), 6), provenance


def get_all_coh_demolished_landmarks_meta() -> dict[str, dict[str, Any]]:
    """Return canonical metadata for all demolished COH landmarks (Cohort 2 + curated-merged)."""
    merged: dict[str, dict[str, Any]] = dict(COH_DEMOLISHED_LANDMARKS_METADATA)
    for enr in COH_LANDMARK_ENRICHMENTS_ON_CURATED.values():
        cid = str(enr.get("coh_landmark_id") or "").strip()
        if cid and cid not in merged:
            merged[cid] = enr
    return merged


class ZeroExtantCollisionGate:
    """Deterministic accuracy gate rejecting collisions with extant or relocated monuments (DF-2 / MUT-2)."""

    def __init__(
        self,
        *,
        extant_hcads: dict[str, dict[str, Any]] | None = None,
        extant_addrs: dict[str, dict[str, Any]] | None = None,
        relocated_hcads: set[str] | None = None,
        relocated_addrs: set[str] | None = None,
        protected_points: list[tuple[float, float, str, int]] | None = None,
        standing_pre1945_hcads: dict[str, int] | None = None,
        standing_pre1945_polys: list[Polygon] | None = None,
    ) -> None:
        self.extant_hcads: dict[str, dict[str, Any]] = dict(extant_hcads or {})
        self.extant_addrs: dict[str, dict[str, Any]] = dict(extant_addrs or {})
        self.relocated_hcads: set[str] = set(relocated_hcads or set())
        self.relocated_addrs: set[str] = set(relocated_addrs or set())
        self.protected_points: list[tuple[float, float, str, int]] = list(protected_points or [])
        self.standing_pre1945_hcads: dict[str, int] = dict(standing_pre1945_hcads or {})
        self.standing_pre1945_polys: list[Polygon] = list(standing_pre1945_polys or [])
        self._poly_tree = STRtree(self.standing_pre1945_polys) if self.standing_pre1945_polys else None

        for mon in ICONIC_EXTANT_MONUMENTS:
            h = str(mon.get("hcad_num") or "").strip()
            meta = {"label": f"iconic:{mon['name']}", "year_built": int(mon.get("year_built") or 0)}
            if len(h) == 13 and h.isdigit():
                self.extant_hcads.setdefault(h, meta)
            for ak in normalize_houston_address_keys(mon.get("address", "")):
                self.extant_addrs.setdefault(ak, meta)

    @classmethod
    def from_atlas_layers(
        cls,
        overlays: dict[str, Any],
        curated_overrides: dict[str, Any] | None = None,
        cama_by_acct: dict[str, Any] | None = None,
        standing_pre1945_polys: list[Polygon] | None = None,
        post_award_demolished_ids: set[str] | None = None,
        buildings_fc: dict[str, Any] | None = None,
    ) -> ZeroExtantCollisionGate:
        ext_hcads: dict[str, dict[str, Any]] = {}
        ext_addrs: dict[str, dict[str, Any]] = {}
        rel_hcads: set[str] = set()
        rel_addrs: set[str] = set()
        prot_pts: list[tuple[float, float, str, int]] = []
        post_demo = post_award_demolished_ids or set()
        all_coh_demo = get_all_coh_demolished_landmarks_meta()
        coh_lm_demo_hcads = {
            str(h).strip()
            for m in all_coh_demo.values()
            for h in (m.get("site_hcad_nums") or [])
        }
        nrhp_demo_hcads = {
            str(h).strip()
            for m in NRHP_DEMOLISHED_METADATA.values()
            if not m.get("relocated_not_demolished")
            for h in (m.get("site_hcad_nums") or [])
        }

        def _rec_extant(hcad: Any, addr: Any, label: str, yb: int, pt_geom: Any = None) -> None:
            meta = {"label": label, "year_built": int(yb or 0)}
            hs = str(hcad or "").strip()
            if len(hs) == 13 and hs.isdigit():
                prev = ext_hcads.get(hs)
                if prev is None or (yb and (not prev["year_built"] or yb < prev["year_built"])):
                    ext_hcads[hs] = meta
            for ak in normalize_houston_address_keys(addr, include_historic_aliases=False):
                prev_a = ext_addrs.get(ak)
                if prev_a is None or (yb and (not prev_a["year_built"] or yb < prev_a["year_built"])):
                    ext_addrs[ak] = meta
            if isinstance(pt_geom, dict) and pt_geom.get("type") == "Point":
                c = pt_geom.get("coordinates") or []
                if len(c) >= 2:
                    prot_pts.append((float(c[0]), float(c[1]), label, int(yb or 0)))

        # 1. Extant COH designated landmarks
        for feat in overlays.get("landmarks", {}).get("features", []):
            p = feat.get("properties") or {}
            fid = str(p.get("id") or "")
            if p.get("demolished") is True or fid in all_coh_demo:
                continue
            _rec_extant(
                p.get("hcad_num"),
                p.get("address"),
                f"coh_landmark:{fid}:{p.get('name')}",
                int(p.get("year_built") or 0),
                feat.get("geometry"),
            )

        # 2. Good Brick awards (unless explicitly marked demolished after award)
        for feat in overlays.get("good_brick_awards", {}).get("features", []):
            p = feat.get("properties") or {}
            fid = str(p.get("id") or "")
            if fid in post_demo or p.get("demolished") is True:
                continue
            _rec_extant(
                p.get("hcad_num"),
                p.get("address"),
                f"good_brick:{fid}:{p.get('name')}",
                int(p.get("year_built") or 0),
                feat.get("geometry"),
            )

        # 3. Extant NRHP properties (`is_demolished_nrhp == False`) + relocated Cohn House `85002771`
        for feat in overlays.get("nrhp_properties", {}).get("features", []):
            p = feat.get("properties") or {}
            refnum = str(p.get("refnum") or "")
            if refnum == "85002771":
                hs = str(p.get("hcad_num") or "").strip()
                if len(hs) == 13 and hs.isdigit():
                    rel_hcads.add(hs)
                for ak in normalize_houston_address_keys(p.get("address"), include_historic_aliases=False):
                    rel_addrs.add(ak)
                c = (feat.get("geometry") or {}).get("coordinates") or []
                if len(c) >= 2:
                    prot_pts.append((float(c[0]), float(c[1]), "relocated_nrhp:85002771", 1905))
                continue
            if p.get("is_demolished_nrhp") is True:
                continue
            _rec_extant(
                p.get("hcad_num"),
                p.get("address"),
                f"nrhp_extant:{refnum}:{p.get('name')}",
                int(p.get("year_built") or 0),
                feat.get("geometry"),
            )

        # 4. Surviving 1915 Red Book structures
        for feat in overlays.get("red_book_1915", {}).get("features", []):
            p = feat.get("properties") or {}
            if p.get("survival_status") == "surviving_era_structure":
                _rec_extant(
                    p.get("hcad_num"),
                    p.get("modern_address") or p.get("historic_address_1915"),
                    f"red_book_surviving:{p.get('id')}:{p.get('name')}",
                    int(p.get("extant_year_built") or 1915),
                    feat.get("geometry"),
                )

        # 5. Relocated structures overlay (never count moved historic structures as demolished!)
        for feat in overlays.get("relocated_structures", {}).get("features", []):
            p = feat.get("properties") or {}
            # Contractor portfolio overlay combines extant contractor sites & relocated houses;
            # flag explicitly relocated records into relocated sets and all standing ones as extant.
            is_reloc = bool(p.get("was_relocated") or p.get("relocation_year") or p.get("original_location"))
            hs = str(p.get("hcad_num") or "").strip()
            if hs in coh_lm_demo_hcads or hs in nrhp_demo_hcads:
                # e.g. T. J. & Ruth Bettes House (`coh_lm_268`, `0601530340031`) was demolished in 2019
                continue
            if is_reloc:
                if len(hs) == 13 and hs.isdigit():
                    rel_hcads.add(hs)
                for ad_field in (p.get("address"), p.get("original_location")):
                    for ak in normalize_houston_address_keys(ad_field, include_historic_aliases=False):
                        rel_addrs.add(ak)
            else:
                _rec_extant(
                    hs,
                    p.get("address"),
                    f"contractor_extant:{p.get('name')}",
                    int(p.get("year_built") or 0),
                    feat.get("geometry"),
                )

        # 6. Curated overrides with `was_relocated == True`
        for key, ov in (curated_overrides or {}).items():
            if not isinstance(ov, dict):
                continue
            if ov.get("was_relocated") is True:
                k_base = str(key).split("#")[0].strip()
                if len(k_base) == 13 and k_base.isdigit():
                    rel_hcads.add(k_base)
                for ak in normalize_houston_address_keys(ov.get("address"), include_historic_aliases=False):
                    rel_addrs.add(ak)

        # 7. Standing pre-1945 CAMA structures (only those with active 2026 building area > 0)
        standing_pre45: dict[str, int] = {}
        cama_vacant_set: set[str] = set()
        cama_rebuilt_map: dict[str, int] = {}
        for acct, row in (cama_by_acct or {}).items():
            acct_s = str(acct)
            if isinstance(row, dict):
                yr = int(row.get("year_built") or row.get("yr_impr") or 0)
                ar = float(row.get("bld_ar") or 0)
                sc = str(row.get("state_class") or "").strip().upper()
            else:
                yr = int(row[0] or 0)
                ar = float(row[2] or 0)
                sc = str(row[3] or "").strip().upper()
            if ar <= 0.0 and sc in ("C1", "C2", "C3"):
                cama_vacant_set.add(acct_s)
            elif ar > 0.0 and yr >= 2020:
                cama_rebuilt_map[acct_s] = yr
            elif 1836 <= yr <= 1945 and ar > 0 and not sc.startswith("C"):
                standing_pre45[acct_s] = yr

        # 8. Extant structures in buildings.geojson (protects tax-exempt X1 shrines like Wesley Chapel AME
        #    and active O2 renovations like 10 Courtlandt Pl where CAMA yr_impr/bld_ar is 0)
        for bf in (buildings_fc or {}).get("features", []):
            bp = bf.get("properties") or {}
            if bp.get("suppress_only"):
                continue
            hs = str(bp.get("hcad_num") or "").strip()
            ey = int(bp.get("year_built") or 0)
            if ey < MIN_VALID_YEAR or not (len(hs) == 13 and hs.isdigit()):
                continue
            if hs in cama_vacant_set or hs in coh_lm_demo_hcads or hs in nrhp_demo_hcads:
                continue
            if hs in cama_rebuilt_map:
                continue
            if ey <= 1945:
                standing_pre45.setdefault(hs, ey)

        return cls(
            extant_hcads=ext_hcads,
            extant_addrs=ext_addrs,
            relocated_hcads=rel_hcads,
            relocated_addrs=rel_addrs,
            protected_points=prot_pts,
            standing_pre1945_hcads=standing_pre45,
            standing_pre1945_polys=standing_pre1945_polys,
        )

    def check_candidate(
        self,
        *,
        source_cohort: str,
        year_built: int,
        year_demolished: int,
        hcad_nums: list[str] | tuple[str, ...] = (),
        address: str = "",
        lon: float | None = None,
        lat: float | None = None,
        polygon: Polygon | None = None,
        allow_curated_predecessor: bool = False,
    ) -> tuple[bool, str]:
        """Return `(allowed, rejection_reason)`."""
        clean_hcads = [str(h).strip() for h in hcad_nums if len(str(h).strip()) == 13 and str(h).strip().isdigit()]
        addr_keys = normalize_houston_address_keys(address, include_historic_aliases=False)

        # Relocated historic buildings are NEVER demolished in every non-predecessor cohort
        for h in clean_hcads:
            if h in self.relocated_hcads and not allow_curated_predecessor:
                return False, f"relocated_hcad:{h}"
        for ak in addr_keys:
            if ak in self.relocated_addrs and not allow_curated_predecessor:
                return False, f"relocated_address:{ak}"

        def _predecessor_ok(ext_yb: int) -> bool:
            if not allow_curated_predecessor or source_cohort != "curated_landmark":
                return False
            return bool(ext_yb > 0 and int(year_demolished) <= int(ext_yb))

        for h in clean_hcads:
            meta = self.extant_hcads.get(h)
            if meta is not None and not _predecessor_ok(int(meta.get("year_built") or 0)):
                return False, f"extant_hcad:{h}:{meta['label']}"
            pre45_yr = self.standing_pre1945_hcads.get(h)
            if pre45_yr is not None and not _predecessor_ok(pre45_yr):
                # Allow Cohort 2 explicitly verified COH landmark fire/demo on multi-building commercial lot
                # only when year_demolished > pre45_yr and source_cohort == 'coh_landmark_demolished'? No:
                # keep zero ambiguity and reject any standing pre-1945 HCAD collision!
                return False, f"standing_pre1945_hcad:{h}:yr={pre45_yr}"

        for ak in addr_keys:
            meta_a = self.extant_addrs.get(ak)
            if meta_a is not None and not _predecessor_ok(int(meta_a.get("year_built") or 0)):
                return False, f"extant_address:{ak}:{meta_a['label']}"

        # Spatial point proximity (14m) against designated extant / relocated points
        is_designated_demolished = source_cohort in ("coh_landmark_demolished", "nrhp_demolished")
        if lon is not None and lat is not None and not allow_curated_predecessor and not is_designated_demolished:
            f_lon, f_lat = float(lon), float(lat)
            m_lon = 111320.0 * math.cos(math.radians(f_lat))
            for px, py, plabel, pyb in self.protected_points:
                if abs(px - f_lon) > 0.00025 or abs(py - f_lat) > 0.00025:
                    continue
                dist_m = math.hypot((px - f_lon) * m_lon, (py - f_lat) * 110850.0)
                if dist_m <= 14.0:
                    return False, f"extant_point_proximity_14m:{plabel}:{dist_m:.1f}m"

        # Spatial overlap (>20% of candidate footprint area) against standing pre-1945 buildings
        if (
            polygon is not None
            and self._poly_tree is not None
            and not allow_curated_predecessor
            and not is_designated_demolished
            and polygon.is_valid
            and polygon.area > 0
        ):
            for idx in self._poly_tree.query(polygon):
                sp = self.standing_pre1945_polys[int(idx)]
                inter = polygon.intersection(sp).area
                if inter / polygon.area > 0.20:
                    return False, f"standing_pre1945_polygon_overlap:{inter / polygon.area:.2f}"

        return True, "ok"


def sanitize_coh_landmarks_overlay(
    landmarks_fc: dict[str, Any],
    search_items: list[dict[str, Any]] | None = None,
) -> dict[str, int]:
    """Repair upstream COH GIS `'Demolished'` string pollution in `overlays['landmarks']` and `search_index` (DF-3).

    In upstream `coh_landmarks.json`, five landmarks had the literal word `'Demolished'` inside
    `USER_HCAD_NUM`, which previously caused an HCAD join to copy the same `alt_addresses` /
    `haif_threads` / `builder` onto all five across both `overlays.json` and `search_index.json`,
    and wrongly flagged `coh_lm_089` (Fifth Ward Hotel Building, 1206 Nance St, built 1883,
    `USER_DEMOD == ''`, still standing) as demolished while leaving the 8 truly demolished COH
    landmarks unmarked in `search_index.json`.
    """
    all_coh_demo = get_all_coh_demolished_landmarks_meta()
    true_demolished_ids = set(all_coh_demo.keys())
    repaired_hcad = 0
    repaired_status = 0
    repaired_search = 0
    landmark_points: list[tuple[float, float, dict[str, Any], bool]] = []
    landmark_by_id: dict[str, tuple[dict[str, Any], bool]] = {}

    for feat in landmarks_fc.get("features", []):
        p = feat.get("properties") or {}
        fid = str(p.get("id") or "")
        meta = all_coh_demo.get(fid)
        should_be_demo = fid in true_demolished_ids
        raw_h = str(p.get("hcad_num") or "").strip()
        was_polluted = bool(raw_h and (not raw_h.isdigit() or len(raw_h) != 13))
        if was_polluted:
            repaired_hcad += 1
            clean_hcads = [str(x).strip() for x in ((meta or {}).get("site_hcad_nums") or []) if str(x).strip()]
            target_hcad = clean_hcads[0] if clean_hcads else ""
            if should_be_demo:
                if target_hcad:
                    p["site_hcad_num"] = target_hcad
                p["hcad_num"] = ""
            else:
                p["hcad_num"] = target_hcad
            p.pop("alt_addresses", None)
            p.pop("haif_threads", None)

        if bool(p.get("demolished")) != should_be_demo:
            p["demolished"] = True if should_be_demo else False
            repaired_status += 1
        elif should_be_demo:
            p["demolished"] = True

        if should_be_demo:
            cur_h = str(p.get("hcad_num") or "").strip()
            clean_hcads = [str(x).strip() for x in ((meta or {}).get("site_hcad_nums") or []) if str(x).strip()]
            if len(cur_h) == 13 and cur_h.isdigit():
                p["site_hcad_num"] = cur_h
                p["hcad_num"] = ""
                repaired_hcad += 1
            else:
                if clean_hcads and not p.get("site_hcad_num"):
                    p["site_hcad_num"] = clean_hcads[0]
                p["hcad_num"] = ""
            p["city_tax_exemption_eligible"] = False
            yd_p = int((meta or {}).get("year_demolished") or 0)
            if yd_p:
                p["year_demolished"] = yd_p
            p.pop("alt_addresses", None)
            p.pop("haif_threads", None)

        if "JEFFERSON DAVIS" not in str(p.get("name") or "").upper() and isinstance(p.get("alt_names"), list):
            p["alt_names"] = [
                a for a in p["alt_names"] if "JEFFERSON DAVIS HOSPITAL" not in str(a).upper()
            ]

        for k, v in list(p.items()):
            if isinstance(v, str):
                p[k] = sanitize_atlas_text(v)

        if fid:
            landmark_by_id[fid] = (p, was_polluted)
        coords = (feat.get("geometry") or {}).get("coordinates")
        if isinstance(coords, (list, tuple)) and len(coords) >= 2 and isinstance(coords[0], (int, float)):
            landmark_points.append((float(coords[0]), float(coords[1]), p, was_polluted))

    if search_items is not None and (landmark_points or landmark_by_id):
        for it in search_items:
            if not isinstance(it, dict) or it.get("type") != "landmark":
                continue
            matched: tuple[dict[str, Any], bool] | None = None
            it_id = str(it.get("id") or "").strip()
            if it_id and it_id in landmark_by_id:
                matched = landmark_by_id[it_id]
            else:
                try:
                    ix = float(it.get("lon") if it.get("lon") is not None else it.get("lng"))
                    iy = float(it.get("lat"))
                except (TypeError, ValueError):
                    continue
                for lx, ly, lp, l_polluted in landmark_points:
                    if abs(ix - lx) < 1e-5 and abs(iy - ly) < 1e-5:
                        matched = (lp, l_polluted)
                        break
            if matched is None:
                continue
            lp, l_polluted = matched
            fid = str(lp.get("id") or "")
            raw_it_h = str(it.get("hcad_num") or "").strip()
            it_polluted = l_polluted or bool(
                raw_it_h and "#" not in raw_it_h and (not raw_it_h.isdigit() or len(raw_it_h) != 13)
            )
            changed = False
            if it.get("id") != fid and fid:
                it["id"] = fid
                changed = True
            should_be_demo = bool(lp.get("demolished"))
            if bool(it.get("demolished")) != should_be_demo:
                if should_be_demo:
                    it["demolished"] = True
                else:
                    it.pop("demolished", None)
                changed = True
            if should_be_demo:
                meta = all_coh_demo.get(fid)
                yd = int((meta or {}).get("year_demolished") or lp.get("year_demolished") or 0)
                if yd and it.get("year_demolished") != yd:
                    it["year_demolished"] = yd
                    changed = True
                site_h = str(lp.get("site_hcad_num") or "")
                if site_h and it.get("site_hcad_num") != site_h:
                    it["site_hcad_num"] = site_h
                    changed = True
                if it.get("hcad_num") != "":
                    it["hcad_num"] = ""
                    changed = True

            if "JEFFERSON DAVIS" not in str(lp.get("name") or "").upper() and isinstance(it.get("alt_names"), list):
                cleaned_alts = [
                    a for a in it["alt_names"] if "JEFFERSON DAVIS HOSPITAL" not in str(a).upper()
                ]
                if len(cleaned_alts) != len(it["alt_names"]):
                    if cleaned_alts:
                        it["alt_names"] = cleaned_alts
                    else:
                        it.pop("alt_names", None)
                    changed = True

            if it_polluted:
                clean_name = sanitize_atlas_text(str(lp.get("name") or "")).strip()
                clean_addr = sanitize_atlas_text(str(lp.get("address") or "")).strip()
                clean_style = sanitize_atlas_text(str(lp.get("style") or "")).strip()
                clean_arch = sanitize_atlas_text(str(lp.get("architect") or "")).strip()
                yb = int(lp.get("year_built") or 0)
                desig = sanitize_atlas_text(str(lp.get("designation") or "Landmark")).strip()
                sub_parts = [pt for pt in (clean_addr, desig, clean_style, f"Built {yb}" if yb >= 1800 else "") if pt]
                it["hcad_num"] = str(lp.get("hcad_num") or "")
                it["label"] = clean_name or str(it.get("label") or "")
                it["name"] = clean_name
                it["building_name"] = clean_name
                it["address"] = clean_addr
                it["sublabel"] = " • ".join(sub_parts)
                if yb >= 1800:
                    it["year_built"] = yb
                if clean_style:
                    it["style"] = clean_style
                    it["bld_style"] = clean_style
                else:
                    it.pop("style", None)
                    it.pop("bld_style", None)
                if clean_arch:
                    it["architect"] = clean_arch
                else:
                    it.pop("architect", None)
                for bad_k in ("alt_names", "alt_addresses", "builder", "keywords", "landmark_code", "name_source", "landuse_desc"):
                    it.pop(bad_k, None)
                changed = True

            if changed:
                repaired_search += 1

    return {
        "repaired_hcad": repaired_hcad,
        "repaired_status": repaired_status,
        "repaired_search": repaired_search,
    }


def enrich_nrhp_demolished_overlay_and_search(
    nrhp_fc: dict[str, Any],
    search_items: list[dict[str, Any]] | None = None,
) -> int:
    """Populate verified historic `year_built` / `year_demolished` on all 16 Gone NRHP features (UT-6)."""
    updated = 0
    by_nrhp_id: dict[str, dict[str, Any]] = {}
    for feat in nrhp_fc.get("features", []):
        p = feat.get("properties") or {}
        if not p.get("is_demolished_nrhp"):
            continue
        refnum = str(p.get("refnum") or "").strip()
        meta = NRHP_DEMOLISHED_METADATA.get(refnum)
        if not meta:
            continue
        disp = format_demolished_lifespan(
            meta["year_built"],
            meta["year_demolished"],
            meta.get("year_built_qualifier", "exact"),
            meta.get("year_demolished_qualifier", "exact"),
        )
        p["year_built"] = int(meta["year_built"])
        p["year_demolished"] = int(meta["year_demolished"])
        p["year_built_qualifier"] = disp["year_built_qualifier"]
        p["year_demolished_qualifier"] = disp["year_demolished_qualifier"]
        p["year_built_display"] = disp["year_built_display"]
        p["year_demolished_display"] = disp["year_demolished_display"]
        p["lifespan_label"] = disp["lifespan_label"]
        p["architect"] = sanitize_atlas_text(str(meta.get("architect") or ""))
        p["style"] = sanitize_atlas_text(str(meta.get("style") or ""))
        p["name"] = sanitize_atlas_text(str(p.get("name") or "")).replace("--", "-")
        p["address"] = sanitize_atlas_text(str(p.get("address") or "")).replace("--", "-")
        if meta.get("site_hcad_nums") and not p.get("hcad_num"):
            p["hcad_num"] = meta["site_hcad_nums"][0]
        if meta.get("local_protection_status_override"):
            p["local_protection_status"] = sanitize_atlas_text(meta["local_protection_status_override"])
            p["was_relocated"] = True
        for k, v in list(p.items()):
            if isinstance(v, str):
                p[k] = sanitize_atlas_text(v)
        by_nrhp_id[str(p.get("id") or f"nrhp_{refnum}")] = p
        updated += 1

    if search_items is not None:
        for it in search_items:
            if not isinstance(it, dict):
                continue
            sid = str(it.get("id") or "")
            if sid in by_nrhp_id:
                np = by_nrhp_id[sid]
                it["year_built"] = int(np["year_built"])
                it["year_demolished"] = int(np["year_demolished"])
                it["architect"] = np.get("architect") or ""
                it["style"] = np.get("style") or ""
                it["name"] = sanitize_atlas_text(str(it.get("name") or "")).replace("--", "-")
                it["label"] = sanitize_atlas_text(str(it.get("label") or "")).replace("--", "-")
                it["address"] = sanitize_atlas_text(str(it.get("address") or "")).replace("--", "-")

    return updated


def evaluate_cohort5_teardown_candidate(
    acct: str,
    *,
    permit_description: str,
    permit_type: str = "",
    permit_issue_date: str = "",
    permit_address: str = "",
    permit_site_apt: str = "",
    permit_desc: str = "",
    cama_year_built: int = 0,
    cama_year_remodel: int = 0,
    cama_bld_area: float = 0.0,
    cama_state_class: str = "",
    citydir_earliest_year: int = 0,
    pre_demo_survey_year: int = 0,
    was_relocated: bool = False,
    min_historic_year: int = MIN_VALID_YEAR,
    max_historic_year: int = 1940,
) -> tuple[bool, dict[str, Any]]:
    """Two-stage structural validation for Cohort 5 2020-2026 pre-1940 teardowns (DF-5 / UT-2 / MUT-1).

    Stage 0: Reject invalid 13-digit account IDs and relocated historic structures.
    Stage 1: Require a strict 2020-2026 main-structure demolition permit via
             `classify_main_structure_demolition_permit`.
    Stage A: Prove pre-demolition historic age in `[min_historic_year, max_historic_year]`:
             - Either pre-1927 Houston City Directory verification (`citydir_earliest_year`),
             - Pre-demolition surveyed structure year (`pre_demo_survey_year`),
             - Or HCAD retained pre-teardown `yr_impr` on an improved account now cleared to vacant `C*`.
    Stage B: Confirm 2026 CAMA clearance or replacement (NEVER accept active historic buildings that
             pulled a wing/remodel demolition permit while keeping `cama_year_built <= 1945` and `bld_area > 0`):
             - Vacant land clearance: `cama_bld_area <= 0` AND `cama_state_class` in `{"C1", "C2", "C3"}`.
             - Rebuilt replacement: `cama_bld_area > 0` AND `cama_year_built >= permit_year >= 2020`
               AND historic age proved independently by `citydir_earliest_year` or `pre_demo_survey_year`.
    """
    acct_s = str(acct or "").strip()
    if len(acct_s) != 13 or not acct_s.isdigit():
        return False, {"reason": "invalid_hcad"}
    if was_relocated:
        return False, {"reason": "relocated_structure"}

    ok_perm, demo_yr, perm_reason = classify_main_structure_demolition_permit(
        permit_description,
        permit_type,
        permit_issue_date,
        raw_address=permit_address,
        site_apt=permit_site_apt,
        permit_desc=permit_desc,
        min_year=2020,
        max_year=MAX_VALID_CAMA_DATE_YEAR,
    )
    if not ok_perm:
        return False, {"reason": f"permit_rejected:{perm_reason}"}

    sc = str(cama_state_class or "").strip().upper()
    ar = float(cama_bld_area or 0.0)
    cyb = int(cama_year_built or 0)
    cd_yr = int(citydir_earliest_year or 0)
    surv_yr = int(pre_demo_survey_year or 0)

    is_vacant_cleared = ar <= 0.0 and sc in ("C1", "C2", "C3")
    is_rebuilt_after_demo = ar > 0.0 and cyb >= demo_yr and cyb >= 2020

    if not is_vacant_cleared and not is_rebuilt_after_demo:
        return False, {"reason": "stage_b_still_standing_or_unconfirmed"}

    # Determine Stage A historic construction year & qualifier
    hist_yr = 0
    hist_qual = "exact"
    age_source = ""
    provenance_tier = 3
    if min_historic_year <= cd_yr <= max_historic_year:
        hist_yr = cd_yr
        hist_qual = "before"
        age_source = "city_directory"
        provenance_tier = 2
        # If vacant parcel also retained an exact HCAD yr_impr <= cd_yr, prefer exact HCAD year
        if is_vacant_cleared and min_historic_year <= cyb <= cd_yr:
            hist_yr = cyb
            hist_qual = "exact"
            age_source = "hcad_cama_and_city_directory"
    elif min_historic_year <= surv_yr <= max_historic_year:
        hist_yr = surv_yr
        hist_qual = "exact"
        age_source = "pre_demo_survey"
        provenance_tier = 2
    elif is_vacant_cleared and min_historic_year <= cyb <= max_historic_year:
        hist_yr = cyb
        # Uncorroborated rounded decade years (`1910`, `1920`, `1930`) in CAMA are estimated placeholders (UT-4 / DF-5)
        hist_qual = "circa" if (cyb % 10 == 0) else "exact"
        age_source = "hcad_retained_yr_impr_vacant"
        provenance_tier = 3
    else:
        return False, {"reason": "stage_a_no_pre1940_proof"}

    if hist_yr >= demo_yr:
        return False, {"reason": "non_positive_lifespan"}

    return True, {
        "hcad_num": acct_s,
        "year_built": hist_yr,
        "year_built_qualifier": hist_qual,
        "year_demolished": demo_yr,
        "year_demolished_qualifier": "exact",
        "age_source": age_source,
        "provenance_tier": provenance_tier,
        "post_demo_status": "vacant_lot" if is_vacant_cleared else f"rebuilt_{cyb}",
        "cama_replacement_year": cyb if is_rebuilt_after_demo else 0,
        "cama_state_class": sc,
        "cama_remodel_year": int(cama_year_remodel or 0),
    }


def compact_lost_houston_feature(
    raw_props: dict[str, Any],
    ring_coords: list[list[float]],
    *,
    source_cohort: str,
    footprint_provenance: str,
) -> dict[str, Any]:
    """Produce budget-conscious, invariant-compliant Lost Houston GeoJSON Feature (DF-4/DF-7/DF-9)."""
    if source_cohort not in LOST_HOUSTON_VALID_COHORTS:
        raise ValueError(f"Invalid source_cohort={source_cohort!r}")
    fid = str(raw_props["id"]).strip()
    if not fid.startswith("lost_"):
        raise ValueError(f"Lost Houston id must start with 'lost_': {fid!r}")

    yb = int(raw_props["year_built"])
    yd = int(raw_props["year_demolished"])
    disp = format_demolished_lifespan(
        yb,
        yd,
        str(raw_props.get("year_built_qualifier") or "exact"),
        str(raw_props.get("year_demolished_qualifier") or "exact"),
    )
    clean_ring = ensure_valid_ccw_ring_6dp(ring_coords)
    if len(clean_ring) < 4:
        raise ValueError(f"Invalid polygon ring for {fid}")

    name = sanitize_atlas_text(str(raw_props["name"]).strip())
    cat = sanitize_atlas_text(str(raw_props.get("category") or "Neighborhood & Cultural Landmark").strip())
    sig_text = sanitize_atlas_text(
        str(
            raw_props.get("historic_significance")
            or raw_props.get("blurb")
            or raw_props.get("description")
            or ""
        ).strip()
    )
    lon = round(float(raw_props["lon"]), 6)
    lat = round(float(raw_props["lat"]), 6)
    label_lng = round(float(raw_props.get("label_lng", lon)), 6)
    label_lat = round(float(raw_props.get("label_lat", lat)), 6)
    has_photo = bool(raw_props.get("photo_url"))
    rank = int(
        raw_props.get("symbol_rank")
        or compute_symbol_rank(
            source_cohort,
            category=cat,
            has_photo=has_photo,
            red_book_category=str(raw_props.get("red_book_category") or ""),
        )
    )
    site_hcads = [
        str(h).strip()
        for h in (raw_props.get("site_hcad_nums") or [])
        if len(str(h).strip()) == 13 and str(h).strip().isdigit()
    ]
    primary_hcad = str(raw_props.get("hcad_num") or (site_hcads[0] if site_hcads else "")).strip()
    citations = []
    for c in raw_props.get("citations") or []:
        if isinstance(c, dict) and c.get("label"):
            item_c = {"label": sanitize_atlas_text(str(c["label"]))}
            if c.get("url"):
                item_c["url"] = str(c["url"]).strip()
            citations.append(item_c)

    prov_tier = int(
        raw_props.get("provenance_tier")
        or (
            1
            if source_cohort in ("curated_landmark", "coh_landmark_demolished", "nrhp_demolished")
            else (2 if source_cohort == "red_book_1915_vanished" else 3)
        )
    )
    props: dict[str, Any] = {
        "id": fid,
        "overlay_layer": "lost_houston",
        "is_boundary_feature": True,
        "feature_type": "lost_houston",
        "is_demolished": True,
        "source_cohort": source_cohort,
        "source_cohort_label": LOST_HOUSTON_COHORT_LABELS[source_cohort],
        "provenance_tier": prov_tier,
        "symbol_rank": rank,
        "footprint_provenance": footprint_provenance,
        "name": name,
        "building_name": name,
        "subtitle": sanitize_atlas_text(str(raw_props.get("subtitle") or "")),
        "category": cat,
        "type_label": sanitize_atlas_text(f"Lost Houston · {cat} ({disp['lifespan_label']})"),
        "lifespan_label": disp["lifespan_label"],
        "lifespan_years": max(1, yd - yb),
        "year_built": yb,
        "year_demolished": yd,
        "year_built_qualifier": disp["year_built_qualifier"],
        "year_demolished_qualifier": disp["year_demolished_qualifier"],
        "year_built_is_circa": bool(disp["year_built_qualifier"] != "exact"),
        "year_demolished_is_circa": bool(disp["year_demolished_qualifier"] != "exact"),
        "year_built_display": disp["year_built_display"],
        "year_demolished_display": disp["year_demolished_display"],
        "address": sanitize_atlas_text(str(raw_props.get("address") or "")),
        "neighborhood": sanitize_atlas_text(str(raw_props.get("neighborhood") or "Houston")),
        "site_hcad_nums": site_hcads,
        "hcad_num": primary_hcad,
        "architect": sanitize_atlas_text(str(raw_props.get("architect") or "Unknown")),
        "style": sanitize_atlas_text(str(raw_props.get("style") or "Historic Architecture")),
        "sanborn_material": str(raw_props.get("sanborn_material") or "wood_frame"),
        "sanborn_material_label": sanitize_atlas_text(
            str(raw_props.get("sanborn_material_label") or "Historic Construction")
        ),
        "stories": raw_props.get("stories") or 1,
        "height_m": round(float(raw_props.get("height_m") or 5.5), 1),
        "demolition_cause": sanitize_atlas_text(str(raw_props.get("demolition_cause") or "")),
        "replaced_by": sanitize_atlas_text(str(raw_props.get("replaced_by") or "")),
        "source": sanitize_atlas_text(str(raw_props.get("source") or "")),
        "historic_significance": sig_text,
        "citations": citations,
        "lon": lon,
        "lat": lat,
        "label_lng": label_lng,
        "label_lat": label_lat,
    }

    # Include optional heavy properties ONLY when non-empty (DF-4 single narrative key, no triple duplication)
    for opt_k in (
        "builder",
        "builder_source",
        "source_url",
        "sanborn_ref",
        "loc_volume_id",
        "loc_sheet_num",
        "loc_sp",
        "loc_sheet_code",
        "sanborn_sheet_image_url",
        "sanborn_sheet_hires_url",
        "sanborn_sheet_loc_url",
        "photo_url",
        "photo_credit",
        "hist_contract",
        "coh_landmark_id",
        "coh_landmark_code",
        "nrhp_refnum",
        "red_book_id",
        "historic_address_1915",
        "demo_permit_date",
        "demo_permit_desc",
        "age_source",
    ):
        val = raw_props.get(opt_k)
        if val not in (None, "", [], {}):
            props[opt_k] = sanitize_atlas_text(val) if isinstance(val, str) else val

    return {
        "type": "Feature",
        "id": fid,
        "properties": props,
        "geometry": {
            "type": "Polygon",
            "coordinates": [clean_ring],
        },
    }


def build_lost_houston_search_item(feature: dict[str, Any]) -> dict[str, Any]:
    """Build search_index item scoped strictly to `lost_*` including cohort metadata (DF-4 / UT-6 / IT-1)."""
    p = feature["properties"]
    disp_demo = p.get("year_demolished_display") or str(p["year_demolished"])
    return {
        "id": p["id"],
        "type": "lost_houston",
        "overlay_layer": "lost_houston",
        "source_cohort": p["source_cohort"],
        "source_cohort_label": p["source_cohort_label"],
        "cohort_label": p["source_cohort_label"],
        "provenance_tier": int(p.get("provenance_tier") or 1),
        "symbol_rank": int(p.get("symbol_rank") or 1),
        "label": sanitize_atlas_text(f"{p['name']} ({p['lifespan_label']})"),
        "name": p["name"],
        "building_name": p["name"],
        "sublabel": sanitize_atlas_text(
            f"Demolished {disp_demo} • {p['address']} • {p['source_cohort_label']}"
        ),
        "category": sanitize_atlas_text(f"Lost Houston ({p['lifespan_label']})"),
        "address": p["address"],
        "neighborhood": p["neighborhood"],
        "year_built": int(p["year_built"]),
        "year_demolished": int(p["year_demolished"]),
        "lifespan_label": p["lifespan_label"],
        "architect": p.get("architect") or "",
        "builder": p.get("builder") or "",
        "style": p.get("style") or "",
        "use_category": p.get("category") or "",
        "lon": round(float(p["lon"]), 6),
        "lat": round(float(p["lat"]), 6),
    }


def build_lost_houston_photo_record(
    *,
    building_id: str,
    landmark_name: str,
    image_url: str,
    thumb_url: str = "",
    full_url: str = "",
    photo_year: int = 0,
    era_label: str = "",
    caption: str = "",
    attribution: str = "",
    source_url: str = "",
) -> dict[str, Any]:
    """Create a Lost Houston archival photo strictly isolated from extant HCAD/address indexes (DF-6 / SIM-1)."""
    if not str(building_id).startswith("lost_"):
        raise ValueError(f"Lost Houston photo building_id must start with 'lost_': {building_id!r}")
    # Intentionally omit `hcad_num` and `address` and assert `is_lost_houston: True` so clicking
    # an extant 2021 house at the same street address never renders the vanished landmark's photo
    # as its primary standing building header photo. Deduplicate identical URL keys per SIM-1.
    img = str(image_url or "").strip()
    th = str(thumb_url or "").strip()
    fl = str(full_url or "").strip()
    src = str(source_url or "").strip()
    rec: dict[str, Any] = {
        "building_id": building_id,
        "is_lost_houston": True,
        "landmark_name": sanitize_atlas_text(landmark_name),
        "image_url": img,
        "photo_year": int(photo_year or 0),
        "era_label": sanitize_atlas_text(era_label),
        "caption": sanitize_atlas_text(caption),
        "attribution": sanitize_atlas_text(attribution),
    }
    if th and th != img:
        rec["thumb_url"] = th
    if fl and fl != img:
        rec["full_url"] = fl
    if src and src != img:
        rec["source_url"] = src
    return rec


def reconcile_holc_lost_houston_rollups(overlays: dict[str, Any]) -> dict[str, int]:
    """Recompute HOLC 1937 `lost_houston_count` on all 46 zones and 4 grade summaries (DF-10 / IT-2 / REG-4).

    Uses `shape(lf['geometry']).representative_point()` + `zone_poly.intersects(rep)` per zone
    so every zone's `lost_houston_count` matches `build_neighborhood_scorecards.py` (`lhc`) 1:1.
    """
    holc_fc = overlays.get("holc_1937_zones") or {}
    holc_feats = holc_fc.get("features") or []
    lost_feats = (overlays.get("lost_houston") or {}).get("features") or []
    if not holc_feats:
        return {}

    zone_polys: list[Any] = []
    zone_idx_map: list[int] = []
    for i, zf in enumerate(holc_feats):
        zp = zf.get("properties") or {}
        zp["lost_houston_count"] = 0
        g = shape(zf["geometry"])
        if not g.is_empty:
            zone_polys.append(g)
            zone_idx_map.append(i)

    tree = STRtree(zone_polys)
    grade_counts: Counter[str] = Counter()
    assigned = 0

    for lf in lost_feats:
        rep = shape(lf["geometry"]).representative_point()
        matched_any = False
        for q_idx in tree.query(rep, predicate="intersects"):
            zi = zone_idx_map[int(q_idx)]
            zp = holc_feats[zi]["properties"]
            zp["lost_houston_count"] = int(zp.get("lost_houston_count") or 0) + 1
            grade = str(zp.get("holc_grade") or "")
            if grade:
                grade_counts[grade] += 1
            matched_any = True
        if matched_any:
            assigned += 1

    summary_rows = overlays.get("holc_1937_grade_summary")
    if isinstance(summary_rows, list):
        for row in summary_rows:
            if isinstance(row, dict):
                g_letter = str(row.get("grade") or "")
                row["lost_houston_count"] = int(grade_counts.get(g_letter, 0))

    return {
        "assigned_in_holc": assigned,
        **{f"grade_{g}": int(grade_counts.get(g, 0)) for g in ("A", "B", "C", "D")},
    }


class _CrossCohortDedupRegistry:
    """Hierarchical deduplication (`Cohort 1 > Cohort 2 > Cohort 3 > Cohort 4 > Cohort 5`) (UT-1)."""

    def __init__(self) -> None:
        self.ids: set[str] = set()
        self.hcads: set[str] = set()
        self.addr_keys: set[str] = set()
        self.polys: list[Polygon] = []
        self.points: list[tuple[float, float]] = []

    def register(
        self,
        fid: str,
        hcad_nums: list[str] | tuple[str, ...],
        address: str,
        lon: float,
        lat: float,
        poly: Polygon,
        extra_addresses: list[str] | tuple[str, ...] = (),
    ) -> None:
        self.ids.add(fid)
        for h in hcad_nums:
            hs = str(h or "").strip()
            if len(hs) == 13 and hs.isdigit():
                self.hcads.add(hs)
        for ad in (address, *extra_addresses):
            for ak in normalize_houston_address_keys(ad, include_historic_aliases=True):
                self.addr_keys.add(ak)
        self.points.append((float(lon), float(lat)))
        if poly.is_valid and not poly.is_empty:
            self.polys.append(poly)

    def collides(
        self,
        fid: str,
        hcad_nums: list[str] | tuple[str, ...],
        address: str,
        lon: float,
        lat: float,
        poly: Polygon | None = None,
        extra_addresses: list[str] | tuple[str, ...] = (),
        *,
        min_point_sep_m: float = 14.0,
    ) -> str:
        if fid in self.ids:
            return f"dup_id:{fid}"
        for h in hcad_nums:
            hs = str(h or "").strip()
            if len(hs) == 13 and hs.isdigit() and hs in self.hcads:
                return f"dup_hcad:{hs}"
        for ad in (address, *extra_addresses):
            for ak in normalize_houston_address_keys(ad, include_historic_aliases=True):
                if ak in self.addr_keys:
                    return f"dup_addr:{ak}"
        f_lon, f_lat = float(lon), float(lat)
        m_lon = 111320.0 * math.cos(math.radians(f_lat))
        for px, py in self.points:
            if abs(px - f_lon) > 0.00025 or abs(py - f_lat) > 0.00025:
                continue
            d_m = math.hypot((px - f_lon) * m_lon, (py - f_lat) * 110850.0)
            if d_m < min_point_sep_m:
                return f"dup_point:{d_m:.1f}m"
        if poly is not None and poly.is_valid and not poly.is_empty:
            for ep in self.polys:
                if ep.bounds[2] < poly.bounds[0] or ep.bounds[0] > poly.bounds[2]:
                    continue
                if ep.bounds[3] < poly.bounds[1] or ep.bounds[1] > poly.bounds[3]:
                    continue
                if poly.intersects(ep) and poly.intersection(ep).area > 1e-12:
                    return "dup_polygon_overlap"
        return ""


def _load_aligned_footprints_index(
    cache_dir: Path,
    target_hcads: set[str],
    standing_pre1945_hcads: set[str],
) -> tuple[dict[str, dict[str, Any]], list[Polygon]]:
    """Single linear scan over `aligned_ndjson` extracting pre-demo footprints + standing pre-1945 polygons."""
    import orjson

    aligned_dir = cache_dir / "aligned_ndjson"
    by_hcad: dict[str, dict[str, Any]] = {}
    standing_polys: list[Polygon] = []
    if not aligned_dir.exists():
        return by_hcad, standing_polys

    for fp in sorted(aligned_dir.glob("*.geojsonseq")):
        with fp.open("rb") as f:
            for line in f:
                raw = line.strip()
                if not raw:
                    continue
                if raw[:1] == b"\x1e":
                    raw = raw[1:]
                obj = orjson.loads(raw)
                p = obj.get("properties") or {}
                h = str(p.get("hcad_num") or "").strip()
                if not h:
                    continue
                is_tgt = h in target_hcads
                is_st = h in standing_pre1945_hcads
                if not is_tgt and not is_st:
                    continue
                geom = obj.get("geometry") or {}
                coords = geom.get("coordinates") or []
                if not coords:
                    continue
                ring = coords[0][0] if geom.get("type") == "MultiPolygon" else coords[0]
                if not ring or len(ring) < 4:
                    continue
                if is_tgt:
                    area_proxy = len(ring)
                    prev = by_hcad.get(h)
                    p_poly = Polygon(ring)
                    pa = p_poly.area if p_poly.is_valid else 0.0
                    if prev is None or pa > prev["area"]:
                        rp = p_poly.representative_point() if p_poly.is_valid and not p_poly.is_empty else None
                        cx = float(rp.x) if rp else float(ring[0][0])
                        cy = float(rp.y) if rp else float(ring[0][1])
                        by_hcad[h] = {
                            "ring": ring,
                            "lon": cx,
                            "lat": cy,
                            "area": pa or float(area_proxy) * 1e-12,
                            "address": str(p.get("address") or "").strip(),
                            "stories": float(p.get("stories") or 1.0),
                            "height_m": float(p.get("height_m") or 5.5),
                            "use_category": str(p.get("use_category") or ""),
                        }
                if is_st:
                    sp = Polygon(ring)
                    if sp.is_valid and not sp.is_empty:
                        standing_polys.append(sp)

    return by_hcad, standing_polys


def format_teardown_address(raw_addr: Any) -> str:
    """Normalize street typography: collapse double spaces, lowercase numeric ordinals, preserve acronyms."""
    s = sanitize_atlas_text(" ".join(str(raw_addr or "").split()).title())
    s = re.sub(r"\b(Nw|Ne|Sw|Se|Us|Fm|Ih)\b", lambda m: m.group(1).upper(), s)
    s = re.sub(r"\b(\d+)(St|Nd|Rd|Th)\b", lambda m: m.group(1) + m.group(2).lower(), s)
    s = re.sub(r"\bAv$", "Ave", s)
    return s


def _lookup_neighborhood_namer(overlays: dict[str, Any]):
    sn_feats = (overlays.get("super_neighborhoods") or {}).get("features") or []
    wd_feats = (overlays.get("historic_wards") or {}).get("features") or []
    hd_feats = (
        (overlays.get("historic_districts") or overlays.get("coh_historic_districts") or {}).get("features")
        or []
    )

    def _prep(feats: list[dict[str, Any]], *prop_keys: str):
        geoms = []
        names = []
        for f in feats:
            g = shape(f["geometry"])
            if not g.is_empty:
                props = f.get("properties") or {}
                geoms.append(g)
                names.append(next((str(props[k]).strip() for k in prop_keys if props.get(k)), ""))
        return (STRtree(geoms) if geoms else None), geoms, names

    wd_tree, wd_g, wd_n = _prep(wd_feats, "name")
    sn_tree, sn_g, sn_n = _prep(sn_feats, "name", "SNBNAME")
    hd_tree, hd_g, hd_n = _prep(hd_feats, "name")

    def _resolve(lon: float, lat: float, fallback: str = "Houston") -> tuple[str, str]:
        pt = Point(float(lon), float(lat))
        hd_name = ""
        if hd_tree is not None:
            for qi in hd_tree.query(pt):
                if hd_g[int(qi)].covers(pt) and hd_n is not None:
                    hd_name = hd_n[int(qi)] or ""
                    if hd_name:
                        break
        ward_name = ""
        if wd_tree is not None:
            for qi in wd_tree.query(pt):
                if wd_g[int(qi)].covers(pt):
                    ward_name = wd_n[int(qi)] or ""
                    if ward_name:
                        break
        sn_name = ""
        if sn_tree is not None:
            for qi in sn_tree.query(pt):
                if sn_g[int(qi)].covers(pt):
                    sn_name = sn_n[int(qi)] or ""
                    if sn_name:
                        break
        if sn_name:
            sn_title = (
                re.sub(r"\b(Ost|Iah|Fm|Us|Nw|Ne|Sw|Se)\b", lambda m: m.group(1).upper(), sn_name.title())
                if sn_name.isupper()
                else sn_name
            )
        else:
            sn_title = ""
        if ward_name and sn_title and ward_name.lower() not in sn_title.lower():
            return f"{ward_name} / {sn_title}", hd_name
        return ward_name or sn_title or fallback, hd_name

    return _resolve


def build_cohorts_2_to_5(
    *,
    cohort1_features: list[dict[str, Any]],
    overlays: dict[str, Any],
    curated_overrides: dict[str, Any],
    citydir_index: dict[str, Any],
    cache_dir: Path,
    parcel_by_hcad: dict[str, list[dict[str, Any]]],
    fit_rect_fn: Any,
    buildings_fc: dict[str, Any] | None = None,
    max_cohort5: int = 310,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Build Cohorts 2, 3, 4, 5 deterministically on top of Cohort 1 (UT-1..UT-7)."""
    cama_path = cache_dir / "cama_2026_compact.pkl"
    cama_by_acct: dict[str, Any] = (
        pickle.loads(cama_path.read_bytes()) if cama_path.exists() else {}
    )
    citydir_by_hcad = (citydir_index or {}).get("by_hcad") or {}

    # 1. Scan HCAD permits.txt for strict 2020-2026 main-structure demolition candidates first
    raw_demo_permits: dict[str, dict[str, Any]] = {}
    acct_zip = cache_dir / "Real_acct_owner.zip"
    if acct_zip.exists():
        with zipfile.ZipFile(acct_zip) as zf:
            if "permits.txt" in zf.namelist():
                with zf.open("permits.txt") as fp:
                    reader = csv.DictReader(
                        (line.decode("latin1", errors="replace") for line in fp),
                        delimiter="\t",
                        quoting=csv.QUOTE_NONE,
                    )
                    for row in reader:
                        acct = (row.get("acct") or "").strip()
                        if len(acct) != 13 or not acct.isdigit():
                            continue
                        site_num = (row.get("site_num") or "").strip()
                        site_str = (row.get("site_str") or "").strip()
                        addr_p = " ".join(
                            x
                            for x in (
                                site_num,
                                (row.get("site_pfx") or "").strip(),
                                site_str,
                                (row.get("site_tp") or "").strip(),
                                (row.get("site_sfx") or "").strip(),
                            )
                            if x
                        )
                        ok_p, p_yr, _ = classify_main_structure_demolition_permit(
                            row.get("dscr"),
                            row.get("permit_type"),
                            row.get("issue_date"),
                            raw_address=addr_p,
                            site_apt=row.get("site_apt"),
                        )
                        if not ok_p:
                            continue
                        prev = raw_demo_permits.get(acct)
                        if prev is None or p_yr > prev["yr"]:
                            raw_demo_permits[acct] = {
                                "yr": p_yr,
                                "dscr": (row.get("dscr") or "").strip(),
                                "permit_type": (row.get("permit_type") or "").strip(),
                                "issue_date": (row.get("issue_date") or "").strip(),
                                "addr": addr_p,
                                "site_apt": (row.get("site_apt") or "").strip(),
                            }

    # Pre-filter Cohort 5 candidates before scanning aligned_ndjson shards
    c5_candidates: dict[str, dict[str, Any]] = {}
    for acct, pinfo in raw_demo_permits.items():
        crow = cama_by_acct.get(acct)
        if not crow:
            continue
        cd_rec = citydir_by_hcad.get(acct) or {}
        was_reloc = bool((curated_overrides.get(acct) or {}).get("was_relocated"))
        ok_c5, meta_c5 = evaluate_cohort5_teardown_candidate(
            acct,
            permit_description=pinfo["dscr"],
            permit_type=pinfo["permit_type"],
            permit_issue_date=pinfo["issue_date"],
            permit_address=pinfo["addr"],
            permit_site_apt=pinfo["site_apt"],
            cama_year_built=int(crow[0] or 0),
            cama_year_remodel=int(crow[1] or 0),
            cama_bld_area=float(crow[2] or 0.0),
            cama_state_class=str(crow[3] or ""),
            citydir_earliest_year=int(cd_rec.get("e") or 0),
            was_relocated=was_reloc,
        )
        if ok_c5:
            meta_c5["permit"] = pinfo
            meta_c5["citydir_rec"] = cd_rec
            meta_c5["legal_dscr"] = str(crow[5] or "").strip()
            c5_candidates[acct] = meta_c5

    # Gather all target HCAD IDs across Cohorts 2, 3, 4, 5 for single-pass aligned_ndjson lookup
    target_hcads: set[str] = set(c5_candidates.keys())
    for m in COH_DEMOLISHED_LANDMARKS_METADATA.values():
        target_hcads.update(m.get("site_hcad_nums") or [])
    for m in NRHP_DEMOLISHED_METADATA.values():
        target_hcads.update(m.get("site_hcad_nums") or [])
    for rf in overlays.get("red_book_1915", {}).get("features", []):
        rh = str((rf.get("properties") or {}).get("hcad_num") or "").strip()
        if len(rh) == 13 and rh.isdigit():
            target_hcads.add(rh)

    # Standing pre-1945 accounts in inner Houston for polygon overlap shielding
    standing_pre1945_set = {
        str(a)
        for a, r in cama_by_acct.items()
        if 1836 <= int(r[0] or 0) <= 1945 and float(r[2] or 0) > 0 and not str(r[3] or "").startswith("C")
    }
    aligned_by_hcad, standing_polys = _load_aligned_footprints_index(
        cache_dir, target_hcads, standing_pre1945_set
    )

    gate = ZeroExtantCollisionGate.from_atlas_layers(
        overlays,
        curated_overrides=curated_overrides,
        cama_by_acct=cama_by_acct,
        standing_pre1945_polys=standing_polys,
        buildings_fc=buildings_fc,
    )
    resolve_nh = _lookup_neighborhood_namer(overlays)

    dedup = _CrossCohortDedupRegistry()
    # Seed registry with Cohort 1 features (respecting intentional same-lot sequential historic eras already curated)
    for f1 in cohort1_features:
        p1 = f1["properties"]
        dedup.register(
            p1["id"],
            p1.get("site_hcad_nums") or [],
            p1.get("address") or "",
            float(p1["lon"]),
            float(p1["lat"]),
            shape(f1["geometry"]),
        )

    out_features: list[dict[str, Any]] = []
    out_photos: list[dict[str, Any]] = []
    stats: dict[str, Any] = {
        "cohort_counts": Counter({"curated_landmark": len(cohort1_features)}),
        "rejected": Counter(),
    }

    # --- COHORT 2: Demolished City of Houston Designated Landmarks ---
    coh_lm_by_id = {
        str((f.get("properties") or {}).get("id") or ""): f
        for f in overlays.get("landmarks", {}).get("features", [])
    }
    for lm_id, meta in sorted(COH_DEMOLISHED_LANDMARKS_METADATA.items()):
        lm_feat = coh_lm_by_id.get(lm_id)
        if not lm_feat:
            continue
        gc = (lm_feat.get("geometry") or {}).get("coordinates") or []
        if len(gc) < 2 and not meta.get("center"):
            continue
        center_override = meta.get("center")
        if isinstance(center_override, (list, tuple)) and len(center_override) >= 2:
            lon, lat = float(center_override[0]), float(center_override[1])
        else:
            lon, lat = float(gc[0]), float(gc[1])
        hcads = list(meta.get("site_hcad_nums") or [])
        parcels = [] if lm_id == "coh_lm_115" else [r for h in hcads for r in parcel_by_hcad.get(h, [])]
        ring, lbl_lon, lbl_lat, prov = fit_oriented_ghost_footprint(
            lon,
            lat,
            float(meta["width_m"]),
            float(meta["depth_m"]),
            parcel_records=parcels,
            neighborhood=meta["neighborhood"],
            address=meta["address"],
            explicit_angle=meta.get("angle"),
            fit_rect_fn=fit_rect_fn,
        )
        poly = Polygon(ring)
        ok_g, g_reason = gate.check_candidate(
            source_cohort="coh_landmark_demolished",
            year_built=meta["year_built"],
            year_demolished=meta["year_demolished"],
            hcad_nums=hcads,
            address=meta["address"],
            lon=lon,
            lat=lat,
            polygon=poly,
        )
        if not ok_g:
            stats["rejected"][f"c2:{g_reason}"] += 1
            continue
        # Note: Beatty-West (Block 93) shares super-block parcel `1305070010001` with San Jacinto Hotel
        # on a distinct address (`815-817 Main` vs `811 Main`), so check address+point dedup safely.
        dup = dedup.collides(
            meta["id"],
            [] if lm_id == "coh_lm_115" else hcads,
            meta["address"],
            lbl_lon,
            lbl_lat,
            poly,
            min_point_sep_m=10.0,
        )
        if dup:
            stats["rejected"][f"c2:{dup}"] += 1
            continue
        feat = compact_lost_houston_feature(
            {
                **meta,
                "coh_landmark_id": lm_id,
                "coh_landmark_code": meta["landmark_code"],
                "lon": lbl_lon,
                "lat": lbl_lat,
                "label_lng": lbl_lon,
                "label_lat": lbl_lat,
                "source": f"City of Houston Historic Preservation Office Designated Landmark #{meta['landmark_code']} (Demolished)",
                "citations": [
                    {
                        "label": f"City of Houston Designated Landmark #{meta['landmark_code']} Inventory",
                        "url": "https://houstontx.gov/planning/HistoricPres/landmarks.html",
                    },
                    {
                        "label": "Harris County Appraisal District Real Property & Permit Archive",
                        "url": "https://public.hcad.org/",
                    },
                ],
            },
            ring,
            source_cohort="coh_landmark_demolished",
            footprint_provenance=prov,
        )
        out_features.append(feat)
        dedup.register(meta["id"], hcads, meta["address"], lbl_lon, lbl_lat, poly)
        stats["cohort_counts"]["coh_landmark_demolished"] += 1

    # --- COHORT 3: Demolished NRHP Properties (`is_demolished_nrhp == True`, excluding relocated 85002771) ---
    nrhp_by_ref = {
        str((f.get("properties") or {}).get("refnum") or ""): f
        for f in overlays.get("nrhp_properties", {}).get("features", [])
        if (f.get("properties") or {}).get("is_demolished_nrhp")
    }
    for refnum, meta in sorted(NRHP_DEMOLISHED_METADATA.items()):
        if meta.get("relocated_not_demolished"):
            stats["rejected"]["c3:relocated_85002771"] += 1
            continue
        nf = nrhp_by_ref.get(refnum)
        if not nf:
            continue
        np = nf.get("properties") or {}
        gc = (nf.get("geometry") or {}).get("coordinates") or []
        if len(gc) < 2:
            continue
        lon, lat = float(gc[0]), float(gc[1])
        hcads = list(meta.get("site_hcad_nums") or [])
        parcels = [r for h in hcads for r in parcel_by_hcad.get(h, [])]
        ring, lbl_lon, lbl_lat, prov = fit_oriented_ghost_footprint(
            lon,
            lat,
            float(meta["width_m"]),
            float(meta["depth_m"]),
            parcel_records=parcels,
            neighborhood=meta["neighborhood"],
            address=meta["address"],
            explicit_angle=meta.get("angle"),
            fit_rect_fn=fit_rect_fn,
        )
        poly = Polygon(ring)
        ok_g, g_reason = gate.check_candidate(
            source_cohort="nrhp_demolished",
            year_built=meta["year_built"],
            year_demolished=meta["year_demolished"],
            hcad_nums=hcads,
            address=meta["address"],
            lon=lbl_lon,
            lat=lbl_lat,
            polygon=poly,
        )
        if not ok_g:
            stats["rejected"][f"c3:{g_reason}"] += 1
            continue
        dup = dedup.collides(meta["id"], hcads, meta["address"], lbl_lon, lbl_lat, poly)
        if dup:
            stats["rejected"][f"c3:{dup}"] += 1
            continue
        listed_date = str(np.get("listed_date") or np.get("listed_year") or "")
        cits = [
            {
                "label": f"National Register of Historic Places Reference #{refnum} (Listed {listed_date})",
                "url": str(np.get("thc_atlas_url") or f"https://npgallery.nps.gov/AssetDetail/NRIS/{refnum}"),
            }
        ]
        if np.get("nomination_pdf_url"):
            cits.append({"label": f"NRHP Nomination Form PDF (#{refnum})", "url": str(np["nomination_pdf_url"])})
        feat = compact_lost_houston_feature(
            {
                **meta,
                "name": meta["clean_name"],
                "subtitle": f"National Register Listed {listed_date} (NRHP #{refnum}, Demolished After Federal Listing)",
                "nrhp_refnum": refnum,
                "lon": lbl_lon,
                "lat": lbl_lat,
                "label_lng": lbl_lon,
                "label_lat": lbl_lat,
                "source": f"National Register of Historic Places (#{refnum}) & Texas Historical Commission Historic Sites Atlas",
                "source_url": str(np.get("thc_atlas_url") or ""),
                "citations": cits,
            },
            ring,
            source_cohort="nrhp_demolished",
            footprint_provenance=prov,
        )
        out_features.append(feat)
        dedup.register(meta["id"], hcads, meta["address"], lbl_lon, lbl_lat, poly)
        stats["cohort_counts"]["nrhp_demolished"] += 1

    # --- COHORT 4: Vanished 1915 Red Book of Houston Places ---
    rb_feats = (overlays.get("red_book_1915") or {}).get("features") or []
    rb_candidates: list[tuple[tuple[int, int, str], dict[str, Any], list[float]]] = []
    for rf in rb_feats:
        rp = rf.get("properties") or {}
        if rp.get("survival_status") not in ("cleared_or_row", "rebuilt_later_structure"):
            continue
        if rp.get("addr_precision") not in ("PointAddress", "StreetAddress"):
            continue
        cat_rb = str(rp.get("category") or "").strip().lower()
        has_rice_photo = bool(rp.get("rice_photo_url") or rp.get("rice_photo_img_url"))
        sanborn_cov = bool(rp.get("sanborn_1907_covered"))
        # Strict high-evidence selection across all 4 categories:
        #   - all vanished churches & schools
        #   - all photo-documented vanished residences (64 Rice Fondren plates)
        #   - all 1907-Sanborn-covered vanished Black-owned businesses
        if cat_rb in ("church", "school"):
            prio = 0 if cat_rb == "school" else 1
        elif cat_rb == "residence" and has_rice_photo:
            prio = 2
        elif cat_rb == "business" and sanborn_cov:
            prio = 3
        else:
            continue
        gc = (rf.get("geometry") or {}).get("coordinates") or []
        if len(gc) < 2:
            continue
        rb_candidates.append(((prio, 0 if has_rice_photo else 1, str(rp.get("id") or "")), rp, gc))

    rb_candidates.sort(key=lambda t: t[0])
    for _, rp, gc in rb_candidates:
        rb_id = str(rp["id"])
        fid = f"lost_{rb_id}"
        lon, lat = float(gc[0]), float(gc[1])
        cat_rb = str(rp.get("category") or "").lower()
        mod_addr = str(rp.get("modern_address") or rp.get("historic_address_1915") or "").strip()
        hist_addr = str(rp.get("historic_address_1915") or "").strip()
        rh = str(rp.get("hcad_num") or "").strip()
        hcads = [rh] if (len(rh) == 13 and rh.isdigit()) else []

        cd_yr = int(rp.get("citydir_earliest_year") or 0)
        yb = cd_yr if 1865 <= cd_yr <= 1915 else 1915
        ext_repl = int(rp.get("extant_year_built") or 0)
        surv_st = str(rp.get("survival_status") or "")
        if surv_st == "rebuilt_later_structure" and ext_repl >= 1941:
            yd = ext_repl
            yd_q = "before"
            cause = f"Early-twentieth-century structure replaced on or before construction of the {ext_repl} building now on this lot."
            repl_by = sanitize_atlas_text(str(rp.get("extant_building_name") or f"{ext_repl} structure at {mod_addr}"))
        else:
            ward_u = str(rp.get("historic_ward") or "").upper()
            yd = 1962 if any(w in ward_u for w in ("THIRD", "FOURTH", "FIFTH")) else 1968
            yd_q = "circa"
            cause = "Cleared during mid-century freeway right-of-way acquisition, urban renewal, or commercial parcel clearance."
            repl_by = sanitize_atlas_text(str(rp.get("survival_label") or "Cleared parcel / freeway right-of-way"))

        if cat_rb in ("church", "school"):
            w_m, d_m = (15.0, 22.0)
            atlas_cat = "Neighborhood & Cultural Landmark" if cat_rb == "church" else "Civic & Government"
            mat = "wood_frame"
            mat_lbl = f"Yellow - Pre-1915 Historic {cat_rb.title()} Structure"
            stories, h_m = 2, 9.0
            style_s = "Early Twentieth-Century Vernacular Sanctuary" if cat_rb == "church" else "Early Twentieth-Century Schoolhouse"
        elif cat_rb == "business":
            w_m, d_m = (9.5, 16.0)
            atlas_cat = "Early Skyscraper & Commercial"
            mat = "brick" if "MAIN" in mod_addr.upper() or "PRAIRIE" in mod_addr.upper() or "MILAM" in mod_addr.upper() or "TRAVIS" in mod_addr.upper() else "wood_frame"
            mat_lbl = "Pink/Yellow - Pre-1915 Commercial Storefront / Office"
            stories, h_m = 2, 7.5
            style_s = "Early Twentieth-Century Commercial Storefront"
        else:
            w_m, d_m = (10.5, 14.5)
            atlas_cat = "Neighborhood & Cultural Landmark"
            mat = "wood_frame"
            mat_lbl = "Yellow - Pre-1915 Wood Frame Residence"
            stories, h_m = 1, 5.8
            style_s = "Early Twentieth-Century Houston Vernacular / Craftsman Residence"

        nh_lbl = str(rp.get("historic_ward") or rp.get("super_neighborhood") or "").strip()
        if not nh_lbl:
            nh_lbl, _ = resolve_nh(lon, lat)
        parcels = [r for h in hcads for r in parcel_by_hcad.get(h, [])]
        ring, lbl_lon, lbl_lat, prov = fit_oriented_ghost_footprint(
            lon,
            lat,
            w_m,
            d_m,
            parcel_records=parcels,
            neighborhood=nh_lbl,
            address=mod_addr,
            fit_rect_fn=fit_rect_fn,
        )
        poly = Polygon(ring)
        ok_g, g_reason = gate.check_candidate(
            source_cohort="red_book_1915_vanished",
            year_built=yb,
            year_demolished=yd,
            hcad_nums=hcads,
            address=mod_addr,
            lon=lbl_lon,
            lat=lbl_lat,
            polygon=poly,
        )
        if not ok_g:
            stats["rejected"][f"c4:{g_reason}"] += 1
            continue
        extra_addrs = [hist_addr] if hist_addr and hist_addr != mod_addr else []
        dup = dedup.collides(
            fid,
            hcads,
            mod_addr,
            lbl_lon,
            lbl_lat,
            poly,
            extra_addresses=extra_addrs,
            min_point_sep_m=12.0,
        )
        if dup:
            stats["rejected"][f"c4:{dup}"] += 1
            continue

        raw_name = sanitize_atlas_text(str(rp.get("name") or "").strip())
        if cat_rb == "residence" and "RESIDENCE" not in raw_name.upper() and "HOUSE" not in raw_name.upper():
            disp_name = f"{raw_name} Residence (1915 Red Book)"
        elif cat_rb == "business":
            disp_name = f"{raw_name} (1915 Red Book)"
        else:
            disp_name = raw_name

        page_s = str(rp.get("red_book_page") or "").strip()
        page_note = f", {page_s}" if page_s else ""
        addr_note = (
            f"1915 historic address {hist_addr} (modern {mod_addr})"
            if hist_addr and hist_addr.upper() != mod_addr.upper()
            else mod_addr
        )
        occ_bits = [
            s
            for s in (
                str(rp.get("subtitle") or "").strip(),
                (f"Pastor: {rp['pastor_name']}" if rp.get("pastor_name") else ""),
                (f"Lodges/Clubs: {rp['lodges_clubs']}" if rp.get("lodges_clubs") else ""),
            )
            if s
        ]
        occ_tail = f" ({'; '.join(occ_bits)})" if occ_bits else ""
        sig = (
            f"Documented in 'The Red Book of Houston: A Compendium of Social, Professional, Religious, "
            f"Educational and Industrial Interests of Houston's Colored Population' (Sotex Publishing Co., 1915{page_note}) "
            f"at {addr_note} in {nh_lbl}{occ_tail}. Cross-referencing against 2026 HCAD CAMA and GIS records confirms "
            f"the 1915-era structure is gone ({rp.get('survival_label') or surv_st})."
        )

        photo_img = str(rp.get("rice_photo_img_url") or "").strip()
        photo_full = str(rp.get("rice_photo_full_url") or photo_img).strip()
        photo_doc = str(rp.get("rice_photo_url") or "").strip()
        hpl_url = str(rp.get("hpl_book_url") or "https://cdm17006.contentdm.oclc.org/").strip()
        cits = [
            {
                "label": f"The Red Book of Houston (1915{page_note}) - Houston Public Library HMRC",
                "url": hpl_url,
            }
        ]
        if photo_doc:
            cits.append({
                "label": "Rice University Woodson Research Center Red Book Photograph Archive",
                "url": photo_doc,
            })

        feat = compact_lost_houston_feature(
            {
                "id": fid,
                "red_book_id": rb_id,
                "red_book_category": cat_rb,
                "name": disp_name,
                "subtitle": str(rp.get("subtitle") or f"Vanished 1915 Red Book of Houston {cat_rb.title()}"),
                "category": atlas_cat,
                "address": mod_addr,
                "historic_address_1915": hist_addr,
                "neighborhood": nh_lbl,
                "site_hcad_nums": hcads,
                "year_built": yb,
                "year_built_qualifier": "before",
                "year_demolished": yd,
                "year_demolished_qualifier": yd_q,
                "demolition_cause": cause,
                "replaced_by": repl_by,
                "architect": "Early Twentieth-Century Houston Builder",
                "style": style_s,
                "sanborn_material": mat,
                "sanborn_material_label": mat_lbl,
                "stories": stories,
                "height_m": h_m,
                "photo_url": photo_img,
                "photo_credit": "Rice University Fondren Library Woodson Research Center (1915 Red Book Plate)" if photo_img else "",
                "source": f"The Red Book of Houston (1915{page_note}) · HPL HMRC & Rice Woodson Research Center",
                "source_url": photo_doc or hpl_url,
                "historic_significance": sig,
                "citations": cits,
                "lon": lbl_lon,
                "lat": lbl_lat,
                "label_lng": lbl_lon,
                "label_lat": lbl_lat,
            },
            ring,
            source_cohort="red_book_1915_vanished",
            footprint_provenance=prov,
        )
        out_features.append(feat)
        dedup.register(fid, hcads, mod_addr, lbl_lon, lbl_lat, poly, extra_addresses=extra_addrs)
        stats["cohort_counts"]["red_book_1915_vanished"] += 1

        if photo_img:
            out_photos.append(
                build_lost_houston_photo_record(
                    building_id=fid,
                    landmark_name=disp_name,
                    image_url=photo_img,
                    thumb_url=photo_img,
                    full_url=photo_full,
                    photo_year=1915,
                    era_label=f"1915 Red Book of Houston Plate{page_note}",
                    caption=f"{disp_name} ({addr_note}) photographed in The Red Book of Houston (1915).",
                    attribution="Rice University Fondren Library Woodson Research Center / 1915 Red Book of Houston",
                    source_url=photo_doc or photo_img,
                )
            )

    # --- COHORT 5: 2020-2026 Demolished Pre-1940 Historic Teardowns ---
    ranked_c5: list[tuple[tuple[int, int, int, str], str, dict[str, Any]]] = []
    for acct, meta_c5 in c5_candidates.items():
        al = aligned_by_hcad.get(acct)
        parcels = parcel_by_hcad.get(acct, [])
        if not al and not parcels:
            stats["rejected"]["c5:no_geometry"] += 1
            continue
        is_cd = 0 if meta_c5["age_source"] in ("city_directory", "hcad_cama_and_city_directory") else 1
        ranked_c5.append(
            ((is_cd, int(meta_c5["year_built"]), -int(meta_c5["year_demolished"]), acct), acct, meta_c5)
        )
    ranked_c5.sort(key=lambda t: t[0])

    c5_added = 0
    for _, acct, meta_c5 in ranked_c5:
        if c5_added >= max_cohort5:
            break
        al = aligned_by_hcad.get(acct)
        parcels = parcel_by_hcad.get(acct, [])
        pinfo = meta_c5["permit"]
        al_addr = " ".join(str((al or {}).get("address") or "").split())
        raw_addr = al_addr if (al_addr and not re.match(r"^0(?:\b|\s)", al_addr)) else pinfo["addr"]
        addr = format_teardown_address(raw_addr)

        if al:
            raw_lon, raw_lat = float(al["lon"]), float(al["lat"])
        else:
            raw_lon, raw_lat = float(parcels[0]["lon"]), float(parcels[0]["lat"])

        nh_lbl, hd_name = resolve_nh(raw_lon, raw_lat)
        # Only use aligned pre-demo roof polygon when the lot is now vacant (`C1/C2/C3`), because on a
        # rebuilt lot (`rebuilt_2021`) the `aligned_ndjson` polygon may trace the newer replacement house!
        pre_ring = al["ring"] if (al and meta_c5["post_demo_status"] == "vacant_lot") else None
        ring, lbl_lon, lbl_lat, prov = fit_oriented_ghost_footprint(
            raw_lon,
            raw_lat,
            11.0,
            15.0,
            parcel_records=parcels,
            pre_demo_ring=pre_ring,
            neighborhood=nh_lbl,
            address=addr,
            fit_rect_fn=fit_rect_fn,
        )
        poly = Polygon(ring)
        ok_g, g_reason = gate.check_candidate(
            source_cohort="historic_teardown_2020_2026",
            year_built=meta_c5["year_built"],
            year_demolished=meta_c5["year_demolished"],
            hcad_nums=[acct],
            address=addr,
            lon=lbl_lon,
            lat=lbl_lat,
            polygon=poly,
        )
        if not ok_g:
            stats["rejected"][f"c5:{g_reason}"] += 1
            continue
        fid = f"lost_td_{acct}"
        dup = dedup.collides(fid, [acct], addr, lbl_lon, lbl_lat, poly, min_point_sep_m=8.0)
        if dup:
            stats["rejected"][f"c5:{dup}"] += 1
            continue

        yb = int(meta_c5["year_built"])
        yd = int(meta_c5["year_demolished"])
        yb_q = meta_c5["year_built_qualifier"]
        prov_tier = int(meta_c5.get("provenance_tier") or 3)
        age_src = str(meta_c5.get("age_source") or "")
        is_comm = bool(re.search(r"\b(BLDG|BLD|COM|COMM|COMMERCIAL)\b", pinfo["dscr"], re.I)) or meta_c5["cama_state_class"] == "C2"
        btype_noun = "Commercial Building" if is_comm else "Historic Residence"
        yb_prefix = f"Pre-{yb}" if yb_q == "before" else (f"c. {yb}" if yb_q == "circa" else str(yb))
        disp_name = f"{addr} ({yb_prefix} {btype_noun})"

        hd_clause = f" inside {hd_name}" if hd_name else f" in {nh_lbl}"
        cd_rec = meta_c5.get("citydir_rec") or {}
        cd_cite = str(cd_rec.get("c") or "").strip()
        if meta_c5["post_demo_status"] == "vacant_lot":
            post_txt = f"leaving a vacant parcel (2026 HCAD state class {meta_c5['cama_state_class']}, 0 sq ft improvement area)"
            repl_by = f"Vacant lot (2026 HCAD {meta_c5['cama_state_class']})"
        else:
            ry = meta_c5["cama_replacement_year"]
            post_txt = f"replaced by new {ry} construction on the parcel"
            repl_by = f"{ry} replacement structure at {addr}"

        sig = (
            f"Pre-1940 {btype_noun.lower()} ({yb_prefix}){hd_clause} demolished under permit "
            f"'{pinfo['dscr']}' issued {pinfo['issue_date']} on HCAD account {acct}, {post_txt}."
        )
        if cd_cite:
            sig += f" Historical presence attested in {cd_cite}."

        cits = [
            {
                "label": f"HCAD Permit Record ({pinfo['issue_date']}: {pinfo['dscr']}) & 2026 Real Property Roll ({acct})",
                "url": "https://public.hcad.org/",
            }
        ]
        if cd_cite:
            cits.append({
                "label": f"Houston City Directory Verification (earliest {cd_rec.get('e')})",
                "url": str(citydir_index.get("collection_url") or "https://digitalcollections.rice.edu/"),
            })

        stories = max(1, int(round(float((al or {}).get("stories") or 1.0))))
        height_m = max(4.8, float((al or {}).get("height_m") or (8.2 if stories >= 2 else 5.5)))
        feat = compact_lost_houston_feature(
            {
                "id": fid,
                "name": disp_name,
                "subtitle": f"Pre-1940 {btype_noun} Torn Down in {yd} ({pinfo['dscr']})",
                "category": "Early Skyscraper & Commercial" if is_comm else "Neighborhood & Cultural Landmark",
                "provenance_tier": prov_tier,
                "age_source": age_src,
                "address": addr,
                "neighborhood": nh_lbl,
                "site_hcad_nums": [acct],
                "year_built": yb,
                "year_built_qualifier": yb_q,
                "year_demolished": yd,
                "year_demolished_qualifier": "exact",
                "demolition_cause": f"Demolished under permit '{pinfo['dscr']}' issued {pinfo['issue_date']}.",
                "replaced_by": repl_by,
                "architect": "Early Twentieth-Century Houston Builder",
                "style": "Early Twentieth-Century Commercial" if is_comm else ("Late Victorian / Folk Victorian" if yb < 1905 else "Early Twentieth-Century Craftsman / Bungalow"),
                "sanborn_material": "brick" if is_comm else "wood_frame",
                "sanborn_material_label": "Pink - Historic Commercial Masonry" if is_comm else "Yellow - Historic Wood Frame Residence",
                "stories": stories,
                "height_m": height_m,
                "source": f"HCAD Demolition Permit Archive ({pinfo['issue_date']}) & 2026 CAMA Roll",
                "historic_significance": sig,
                "citations": cits,
                "demo_permit_date": pinfo["issue_date"],
                "demo_permit_desc": pinfo["dscr"],
                "lon": lbl_lon,
                "lat": lbl_lat,
                "label_lng": lbl_lon,
                "label_lat": lbl_lat,
            },
            ring,
            source_cohort="historic_teardown_2020_2026",
            footprint_provenance=prov,
        )
        out_features.append(feat)
        dedup.register(fid, [acct], addr, lbl_lon, lbl_lat, poly)
        stats["cohort_counts"]["historic_teardown_2020_2026"] += 1
        c5_added += 1

    return out_features, out_photos, stats
