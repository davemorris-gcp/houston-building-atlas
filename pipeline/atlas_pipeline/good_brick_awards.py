"""
Extract, geocode, and integrate Preservation Houston Good Brick Award Winners (1979-2026)
into the Houston Building Atlas data model, overlays.json, curated_overrides.json,
buildings.geojson, search_index.json, and Google Sheet.
"""

from __future__ import annotations

import csv
import json
import re
import shutil
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any

import orjson

REPO_ROOT = Path("/usr/local/google/home/davemorris/houston-building-atlas")
CACHE_DIR = REPO_ROOT / "pipeline" / "cache"
DATA_DIR = REPO_ROOT / "app" / "public" / "data"
SCRATCH_RAW_PATH = Path(
    "/usr/local/google/home/davemorris/.gemini/jetski/brain/fa92c33e-21a0-4bf6-bd5e-63aa13580820/scratch/raw_good_brick_awards.json"
)
RAW_AWARDS_CACHE_PATH = CACHE_DIR / "raw_good_brick_awards.json"
RESOLVED_JSON_PATH = CACHE_DIR / "good_brick_awards_resolved.json"
RESOLVED_CSV_PATH = CACHE_DIR / "good_brick_awards_resolved.csv"

# Non-place award patterns (books, publications, documentaries, individual career/service awards, citywide programs)
NON_PLACE_PATTERNS = [
    r"\bfor (?:his|her|their|its) books?\b",
    r"\bfor the books?\b",
    r"\bfor the publication\b",
    r"\bfor its publication\b",
    r"\bpublication of the book\b",
    r"\bby publishing\b",
    r"\bfor (?:its|the) documentary\b",
    r"\bfor the exhibition and catalog\b",
    r"\bModern Mode: Houston Architecture at Mid-Century\b",
    r"\bcraftsperson of the year\b",
    r"\bfor (?:his|her|their) outstanding (?:service|personal|contributions)\b",
    r"\bfor (?:his|her|their) visionary dedication\b",
    r"\bfor leadership in (?:local |historic )?preservation\b",
    r"\bfor (?:his|her) commitment to (?:historic )?preservation\b",
    r"\bfor the advancement of preservation in Katy\b",
    r"\bPreservation Partner in Print Award\b",
    r"\bPreservation Leadership Award in Honor of Jesse H\. Jones\b",
    r"\bOutstanding Leadership in Historic Preservation\b",
    r"\bCommunity Pillar(?:s|\s+Award)?:",
    r"\bfor developing the Houston Heights Design Review Guidelines\b",
    r"\bfor carrying on the traditions and legacy of the park\b",
    r"\bSouthampton Place Centennial Celebration\b",
    r"\bSouthampton Centennial Celebration\b",
    r"\bWith Respect: Preserving Historic Cemeteries\b",
    r"\bconservation of the Weber Iron & Wire Co\.",
    r"\bfourth- and fifth-grade project Houston: Urban Landscapes in Watercolor\b",
    r"\bCollege Park Memorial Cemetery genealogy program\b",
    r"\bsuccessful effort to strengthen protections in Houston's historic districts\b",
    r"\bneighborhood preservation in Woodland Heights\b",
    r"\bMod of the Month program\b",
    r"\befforts in getting the Old Sixth Ward named the city's first protected historic district\b",
    r"\bHistoric Documents Preservation Project\b",
    r"\bhistorical research and assistance in a walking tour of Glenwood Cemetery\b",
    r"\bwork as chairman of the Texas Historical Commission\b",
    r"\bin recognition of more than 50 years of historic preservation\b",
    r"\bMove Home Program, founded in 1996\b",
    r"\bdesignation of the Norhill Historic District\b",
    r"\bconstruction of three compatible infill homes in the Heights\b",
    r"\bTexas Trailblazers Preservation Association\b",
    r"\bAngels of Glenwood Cemetery\b",
    r"\bHouston Municipal Art Conservation Program\b",
    r"\bFacade Grant Program\b",
    r"\bFreedman's Town Master Plan\b",
    r"\bvision in using preservation as a tool for urban revitalization\b",
    r"\bBuffalo Bayou East Sector Redevelopment Plan\b",
    r"\bHarris County Tree Registry\b",
    r"\b1992 Heart of the Park Competition\b",
    r"\b1991 Centennial Projects\b",
    r"\bHistoric Resources Database for the City of Houston\b",
    r"\bHouston Heights Texas Urban Main Street Project\b",
    r"\bAd Hoc Task Force on Planning and Zoning\b",
    r"\bdevelopment of the Texas Limited\b",
    r"\befforts in the Texas Legislature\b",
    r"\bcreating the Houston Archaeological and Historical Commission\b",
    r"\bpreserving the U\.S\.S\. Manassas\b",
    r"\bHome Repair Program\b",
    r"\brestoring a K67 kiosk\b",
    r"^Martha Peterson Award: Anna Mod$",
    r"^President's Award: Gerald D\. Hines$",
    r"^President's Award: Barry Moore, FAIA$",
    r"^President's Award: Bob Fretz, Jr\.$",
    r"^President's Award: The Clayton Family$",
    r"^President's Award: Joella & Stewart Morris$",
    r"^President's Award: Mayor Bill White$",
    r"^President's Awards: Dr\. John P\. McGovern and Jane Blaffer Owen$",
]

# Curated location resolutions for place-based awards
CURATED_AWARD_LOCATIONS: list[tuple[int, str, dict[str, Any]]] = [
    # 2026
    (2026, "Lily Barfield", {
        "address": "109 STRATFORD ST",
        "hcad_num": "0261330000018",
        "project_name": "Stewart House / The Marlene",
        "historic_district": "Avondale East",
        "location_source": "COH Historic Preservation & CultureMap Houston (109 Stratford St)",
    }),
    (2026, "Lee Ann and James Badum", {
        "address": "3767 UNIVERSITY BLVD",
        "hcad_num": "0560670000038",
        "project_name": "Jahn House (Southside Place)",
        "location_source": "HCAD Ownership Record (James P. & Lee Ann Badum, 3767 University Blvd)",
    }),
    (2026, "Houston Zoo for restoring its reflection pool", {
        "address": "6200 HERMANN PARK DR",
        "hcad_num": "0402290010001",
        "project_name": "Houston Zoo Reflection Pool & Colonnades",
        "lat": 29.7158,
        "lng": -95.3905,
        "location_source": "Houston Zoo Campus / Hermann Park Historic Core",
    }),
    (2026, "Jennifer and Jarrett Ellzey", {
        "address": "2 WAVERLY CT",
        "hcad_num": "0523010000002",
        "project_name": "S.I. Morris House (Waverly Court)",
        "location_source": "COH Landmark & HCAD Ownership Record (2 Waverly Ct)",
    }),
    (2026, "Jennifer Nelsen and Vinod Pathrose", {
        "address": "1816 HAVER ST",
        "hcad_num": "0382500020002",
        "project_name": "Pathrose-Nelsen Bungalow (Cherryhurst)",
        "location_source": "HCAD Ownership Record (Vinod K. Pathrose, 1816 Haver St)",
    }),
    (2026, "Scarlet Capital for rehabilitating the San Jacinto Warehouse", {
        "address": "1101 N SAN JACINTO ST",
        "hcad_num": "0020040000001",
        "project_name": "San Jacinto Warehouse",
        "lat": 29.7681,
        "lng": -95.3545,
        "location_source": "THC Historic Tax Credit & COH Landmark Filing (1101 Providence / N. San Jacinto St)",
    }),
    (2026, "Fan and Peter Morris for stewardship of the Carpenter House", {
        "address": "5330 MANDELL ST",
        "hcad_num": "0541400000009",
        "project_name": "Carpenter House (15 Cherokee Pl / 5330 Mandell St)",
        "location_source": "Preservation Houston 2024 Good Brick Tour & HCAD Ownership Record",
    }),
    (2026, "Culinary Khancepts for rehabilitating the River Oaks Theatre", {
        "address": "2009 W GRAY ST",
        "hcad_num": "0442250000170",
        "project_name": "River Oaks Theatre",
        "lat": 29.7530,
        "lng": -95.4091,
        "location_source": "COH Protected Landmark (River Oaks Theatre, 2009 W Gray St)",
    }),
    (2026, "Southampton Place for restoring Southampton", {
        "address": "2101 SUNSET BLVD",
        "project_name": "Southampton Place Historic Concrete Street Markers",
        "lat": 29.7230,
        "lng": -95.4112,
        "location_source": "Southampton Place Civic Association Esplanade (Sunset Blvd & Rice Blvd)",
    }),
    (2026, "Holly and Greg Suellentrop", {
        "address": "2911 JULIAN ST",
        "hcad_num": "0400480000011",
        "project_name": "Klunkert Farmhouse",
        "historic_district": "Norhill",
        "location_source": "COH Protected Landmark (Klunkert Farmhouse, 2911 Julian St)",
    }),
    (2026, "Donna and Jim Bennett", {
        "address": "2728 COLUMBIA ST",
        "hcad_num": "0202330000014",
        "project_name": "Lund House & Modern Print Shop",
        "historic_district": "Houston Heights South",
        "lat": 29.7824,
        "lng": -95.3941,
        "location_source": "Modern Print Shop / Houston Heights South Historic District",
    }),
    (2026, "St. Elizabeth Hospital", {
        "address": "4514 LYONS AVE",
        "hcad_num": "0212420000001",
        "project_name": "St. Elizabeth Hospital / St. Elizabeth Place",
        "lat": 29.7764,
        "lng": -95.3245,
        "location_source": "COH Landmark & NRHP Listing (St. Elizabeth Hospital, 4514 Lyons Ave)",
    }),
    (2026, "Hamilton Shirts", {
        "address": "5700 RICHMOND AVE",
        "project_name": "Hamilton Shirts Workshop & Showroom",
        "lat": 29.7318,
        "lng": -95.4792,
        "location_source": "Hamilton Shirts Flagship & Custom Shirtmakers (5700 Richmond Ave)",
    }),
    # 2025
    (2025, "Julia W. Long for rehabilitating the Warshaw House", {
        "address": "5203 CAVERSHAM DR",
        "hcad_num": "0952180000001",
        "project_name": "Warshaw House (Meyerland)",
        "location_source": "HCAD Ownership Record (James & Julia Long, 5203 Caversham Dr)",
    }),
    (2025, "Julia and Thomas Pascal Will Robinson", {
        "address": "201 WESTMORELAND ST",
        "hcad_num": "0370320000010",
        "project_name": "Waldo Mansion",
        "historic_district": "Westmoreland",
        "location_source": "HCAD Ownership Record (Thomas P. Robinson, 201 Westmoreland St)",
    }),
    (2025, "The Church at 1548 Heights", {
        "address": "1548 HEIGHTS BLVD",
        "hcad_num": "0201180000021",
        "project_name": "The Church at 1548 Heights (Heights First Baptist Sanctuary)",
        "historic_district": "Houston Heights East",
        "location_source": "Explicit Address in Name & HCAD Parcel (1548 Heights Blvd)",
    }),
    (2025, "Charles Stava and Jacob Garber-Stava", {
        "address": "503 AVONDALE ST",
        "hcad_num": "0551960000005",
        "project_name": "Martha Perlitz House",
        "historic_district": "Avondale West",
        "location_source": "COH Protected Landmark (Martha Schuhmacher Perlitz House, 503 Avondale St)",
    }),
    (2025, "Diane and Ray Krueger", {
        "address": "3535 W 12TH ST",
        "hcad_num": "0410460010017",
        "project_name": "Big Three Industries Building",
        "lat": 29.7915,
        "lng": -95.4388,
        "location_source": "Houston Mod & Architectural Record (Big Three Industries HQ, 3535 W 12th St)",
    }),
    (2025, "Mary Patton for rehabilitating the Herzog House", {
        "address": "2523 MARONEAL ST",
        "hcad_num": "0620280240016",
        "project_name": "Herzog House (Braeswood)",
        "location_source": "HCAD Ownership Record (Mary Patton Trust, 2523 Maroneal St)",
    }),
    (2025, "Annise Parker and Kathy Hubbard", {
        "address": "428 WESTMORELAND ST",
        "hcad_num": "0370340000003",
        "project_name": "Elbert C. Crawford House",
        "historic_district": "Westmoreland",
        "location_source": "COH Protected Landmark (Elbert C. Crawford House, 428 Westmoreland St)",
    }),
    (2025, "Buffalo Soldiers National Museum", {
        "address": "3816 CAROLINE ST",
        "hcad_num": "0130730000019",
        "project_name": "Buffalo Soldiers National Museum (Houston Light Guard Armory)",
        "location_source": "COH Protected Landmark (Houston Light Guard Armory, 3816-3820 Caroline St)",
    }),
    (2025, "Lighthouse House", {
        "address": "2018 KANE ST",
        "hcad_num": "0052000000006",
        "project_name": "The Lighthouse House",
        "historic_district": "Old Sixth Ward",
        "location_source": "Preservation Houston 2025 Good Brick Tour (2018 Kane St)",
    }),
    (2025, "Brochsteins", {
        "address": "11530 MAIN ST",
        "hcad_num": "0431870000010",
        "project_name": "Brochsteins Architectural Woodwork Campus",
        "lat": 29.6625,
        "lng": -95.4295,
        "location_source": "Brochsteins Historic Main Street Facility (11530 Main St)",
    }),
    # 2024
    (2024, "Style in Steel Townhouse", {
        "address": "4158 MEYERWOOD DR",
        "hcad_num": "0921540000007",
        "project_name": "Style in Steel Townhouse",
        "location_source": "Preservation Houston 2024 Good Brick Gallery & 2025 Tour (4158 Meyerwood Dr)",
    }),
    (2024, "Minchen House", {
        "address": "1753 NORTH BLVD",
        "hcad_num": "0630600150016",
        "project_name": "Minchen House",
        "historic_district": "Boulevard Oaks",
        "location_source": "Preservation Houston 2024 Good Brick Gallery (1753 North Blvd)",
    }),
    (2024, "Dan Tidwell", {
        "address": "2219 DECATUR ST",
        "hcad_num": "0051810000012",
        "project_name": "Tidwell Victorian Cottage (2219 Decatur St)",
        "historic_district": "Old Sixth Ward",
        "location_source": "Preservation Houston 2024 Good Brick Tour & HCAD Ownership (2219 Decatur St)",
    }),
    (2024, "The Houston Fire Museum", {
        "address": "2403 MILAM ST",
        "hcad_num": "0141410000001",
        "project_name": "Houston Fire Museum (Fire Station No. 7)",
        "location_source": "COH Landmark & NRHP Listing (Fire Station No. 7, 2403 Milam St)",
    }),
    (2024, "FW Heritage, LLC", {
        "address": "1507 ALAMO ST",
        "hcad_num": "0200130000012",
        "project_name": "1507 Alamo Street Folk Victorian Cottage",
        "historic_district": "First Ward",
        "location_source": "Preservation Houston 2024 Good Brick Gallery & 2025 Tour (1507 Alamo St)",
    }),
    (2024, "St. Mark", {
        "address": "600 PECORE ST",
        "hcad_num": "0371650000001",
        "project_name": "St. Mark's United Methodist Church",
        "historic_district": "Woodland Heights",
        "location_source": "HCAD & Woodland Heights Landmark (600 Pecore St)",
    }),
    (2024, "Hollyfield Laundry", {
        "address": "1731 WESTHEIMER RD",
        "hcad_num": "0382400000001",
        "project_name": "Hollyfield Laundry Building (Barcelona Wine Bar)",
        "location_source": "Montrose Commercial Landmark (1731 Westheimer Rd)",
    }),
    (2024, "Imperial Laundry", {
        "address": "3401 HARRISBURG BLVD",
        "hcad_num": "0030440000001",
        "project_name": "Imperial Laundry Building",
        "location_source": "Preservation Houston 2024 Good Brick Gallery (3401 Harrisburg Blvd)",
    }),
    (2024, "Knapp Chevrolet", {
        "address": "815 HOUSTON AVE",
        "hcad_num": "0052320000001",
        "project_name": "Knapp Chevrolet Building",
        "lat": 29.7676,
        "lng": -95.3717,
        "location_source": "Preservation Houston 2024 Good Brick Gallery (815-1230 Houston Ave)",
    }),
    (2024, "Eldorado Ballroom", {
        "address": "2310 ELGIN ST",
        "hcad_num": "0190900000001",
        "project_name": "Eldorado Ballroom",
        "location_source": "COH Protected Landmark & 2024 Good Brick Tour (2310 Elgin St)",
    }),
    # 2023
    (2023, "Jacquelyn & Collin Cox", {
        "address": "3428 PIPING ROCK LN",
        "hcad_num": "0601460200009",
        "project_name": "Alfred E. Reidel House",
        "location_source": "COH Landmark & Houston Chronicle (3428 Piping Rock Ln)",
    }),
    (2023, "Divya Pande & Nakul Gupta", {
        "address": "600 AVONDALE ST",
        "hcad_num": "0261320000004",
        "project_name": "Pande-Gupta Residence (600 Avondale St)",
        "historic_district": "Avondale East",
        "location_source": "HCAD Ownership Record (Divya R. Pande, 600 Avondale St)",
    }),
    (2023, "Houston Post building", {
        "address": "2410 POLK ST",
        "hcad_num": "0020810000001",
        "project_name": "Houston Post Building / Printhouse",
        "lat": 29.7471,
        "lng": -95.3518,
        "location_source": "East End Redevelopment (Former 1955 Houston Post HQ, 2410 Polk St)",
    }),
    (2023, "Catherine & Matt Matthews", {
        "address": "1307 DENMAN ST",
        "hcad_num": "0601560500028",
        "project_name": "Christ Church Cathedral Deanery (River Oaks)",
        "location_source": "HCAD Ownership Record (Katherine L. Matthews, 1307 Denman St / 3023 Del Monte Dr)",
    }),
    (2023, "Jean & William Frazer", {
        "address": "353 WESTMINSTER DR",
        "hcad_num": "0823830000029",
        "project_name": "Frazer Modernist Residence (353 Westminster Dr)",
        "location_source": "HCAD Ownership Record (William R. & Jean N. Frazer, 353 Westminster Dr)",
    }),
    (2023, "Barbara Jordan Post Office", {
        "address": "401 FRANKLIN ST",
        "hcad_num": "0010010000013",
        "project_name": "POST Houston (Barbara Jordan Post Office)",
        "lat": 29.7662,
        "lng": -95.3662,
        "location_source": "Downtown Landmark (POST Houston, 401 Franklin St)",
    }),
    (2023, "Fraga Family", {
        "address": "15 ALTIC ST",
        "hcad_num": "0160440000008",
        "project_name": "Lupe & Joe Z. Fraga House (Reiler-Fraga House)",
        "location_source": "COH Protected Landmark (Reiler-Fraga House, 15 Altic St)",
    }),
    # 2022
    (2022, "Jan Rynda Greer", {
        "address": "1802 HARVARD ST",
        "hcad_num": "0201010000014",
        "project_name": "Mansfield House",
        "historic_district": "Houston Heights East",
        "location_source": "Preservation Houston Good Brick Tour & HCAD Ownership (1802 Harvard St)",
    }),
    (2022, "Linda & John Thomas", {
        "address": "3239 LOCKE LN",
        "hcad_num": "0601480240002",
        "project_name": "Virgil & Doris Childress House",
        "location_source": "COH Landmark & HCAD Ownership (John C. Thomas, 3239 Locke Ln)",
    }),
    (2022, "Lott Hall", {
        "address": "6201 HERMANN PARK DR",
        "hcad_num": "0402290010001",
        "project_name": "Lott Hall (Hermann Park Clubhouse)",
        "lat": 29.7135,
        "lng": -95.3900,
        "location_source": "Hermann Park Conservancy (Lott Hall, 6201 Hermann Park Dr)",
    }),
    (2022, "Maxfield Hall", {
        "address": "6100 MAIN ST",
        "hcad_num": "0391880000001",
        "project_name": "Maxfield Hall (Mechanical Laboratory, Rice University)",
        "lat": 29.7196,
        "lng": -95.3999,
        "location_source": "Rice University Historic Core (Maxfield Hall)",
    }),
    (2022, "Cameron Iron Works", {
        "address": "711 MILBY ST",
        "hcad_num": "0030480000001",
        "project_name": "Cameron Iron Works Building",
        "location_source": "COH Protected Landmark (Cameron Iron Works, 711 Milby St)",
    }),
    (2022, "Brian Miksch and Karen Sonnier", {
        "address": "126 NORTH ST",
        "hcad_num": "0152150050003",
        "project_name": "B.J. Witt House",
        "historic_district": "Germantown",
        "location_source": "Germantown Historic District / Grota Home Addition",
    }),
    (2022, "Nicole J. Simien", {
        "address": "1914 GANO ST",
        "hcad_num": "0090480000029",
        "project_name": "Simien Victorian Residence (1914 Gano St)",
        "location_source": "HCAD Ownership Record (Nicole Simien, 1914 Gano St)",
    }),
    (2022, "Bruce Boatner", {
        "address": "1918 CROCKETT ST",
        "hcad_num": "0051390000001",
        "project_name": "1918 Crockett Street Craftsman House",
        "historic_district": "First Ward",
        "location_source": "Preservation Houston 2022 Good Brick Tour (1918 Crockett St)",
    }),
    (2022, "Star Engraving", {
        "address": "3201 ALLEN PKWY",
        "hcad_num": "0401230000044",
        "project_name": "Star Engraving Co. Building",
        "location_source": "COH Protected Landmark (Star Engraving Co., 3201 Allen Pkwy)",
    }),
    (2022, "Gribble Stamp", {
        "address": "121 ST EMANUEL ST",
        "hcad_num": "0020110000005",
        "project_name": "Gribble Stamp & Stencil Co. Building (Soccer Shots Houston)",
        "lat": 29.7548,
        "lng": -95.3512,
        "location_source": "East End Commercial Directory (Gribble Stamp & Stencil Co., 121 St. Emanuel St)",
    }),
    (2022, "Carter-Milroy-Canfield Tenant Houses", {
        "address": "519 W 13TH ST",
        "hcad_num": "0201600000006",
        "project_name": "Carter-Milroy-Canfield Tenant Houses & Freedmen's Town Shotgun",
        "historic_district": "Houston Heights West",
        "lat": 29.7942,
        "lng": -95.4058,
        "location_source": "COH Landmark (Carter-Milroy-Canfield Tenant Houses, 519-525 W 13th St)",
    }),
    (2022, "La Colombe", {
        "address": "3410 MONTROSE BLVD",
        "hcad_num": "0140650000001",
        "project_name": "La Colombe d'Or / Walter W. Fondren Mansion",
        "location_source": "COH Protected Landmark (Fondren Mansion, 3410 Montrose Blvd)",
    }),
    (2022, "St. Paul", {
        "address": "5501 MAIN ST",
        "hcad_num": "0392050000001",
        "project_name": "St. Paul's United Methodist Church",
        "location_source": "Museum District Landmark (5501 Main St)",
    }),
    (2022, "Mary & John Steen", {
        "address": "1419 KIRBY DR",
        "hcad_num": "0601530360005",
        "project_name": "Mr. & Mrs. Ralph M. Henderson House",
        "location_source": "COH Protected Landmark (Ralph M. Henderson House, 1419 Kirby Dr)",
    }),
    (2022, "Rothko Chapel", {
        "address": "1404 SUL ROSS ST",
        "hcad_num": "0502350010001",
        "project_name": "Rothko Chapel",
        "location_source": "NRHP Landmark & HCAD Record (Rothko Chapel, 1404 Sul Ross St / 3900 Yupon St)",
    }),
    (2022, "Glassell School of Art and Nancy & Rich Kinder Building", {
        "address": "5500 MAIN ST",
        "hcad_num": "0132240000001",
        "project_name": "Glassell School of Art & Nancy and Rich Kinder Building (MFAH)",
        "lat": 29.7262,
        "lng": -95.3905,
        "location_source": "Museum of Fine Arts, Houston Campus (5500 Main St / 5101 Montrose Blvd)",
    }),
    # 2021
    (2021, "Cara & Ken Moczulski", {
        "address": "1 REMINGTON LN",
        "hcad_num": "0502260000001",
        "project_name": "F.A. Heitmann House (Shadyside)",
        "lat": 29.7235,
        "lng": -95.3924,
        "location_source": "Shadyside Architectural Survey (F.A. Heitmann House, 1926 William Ward Watkin)",
    }),
    (2021, "Kellum-Noble House", {
        "address": "212 DALLAS ST",
        "hcad_num": "0400030000013",
        "project_name": "Kellum-Noble House (Sam Houston Park)",
        "location_source": "COH Protected Landmark (Kellum-Noble House, 212 Dallas St)",
    }),
    (2021, "Joseph A. Tennant House", {
        "address": "1505 NORTH BLVD",
        "hcad_num": "0530390000008",
        "project_name": "Joseph A. Tennant House",
        "historic_district": "Broadacres",
        "location_source": "COH Landmark & HCAD Ownership (Michael G. & Kaitlyn Scheurich, 1505 North Blvd)",
    }),
    (2021, "Congregation Beth Yeshurun", {
        "address": "4525 BEECHNUT ST",
        "hcad_num": "0820680000028",
        "project_name": "Congregation Beth Yeshurun Sanctuary",
        "lat": 29.6894,
        "lng": -95.4552,
        "location_source": "HCAD Campus Record (Congregation Beth Yeshurun, 4525 Beechnut St)",
    }),
    (2021, "Medical Towers building", {
        "address": "1709 DRYDEN RD",
        "hcad_num": "0552000000033",
        "project_name": "Medical Towers Building (Westin Houston Medical Center)",
        "lat": 29.7099,
        "lng": -95.4017,
        "location_source": "Texas Medical Center Landmark (1709 Dryden Rd)",
    }),
    (2021, "Hulsey-Davis House", {
        "address": "1216 WRIGHTWOOD ST",
        "hcad_num": "0371590000008",
        "project_name": "Hulsey-Davis House",
        "historic_district": "Norhill",
        "location_source": "COH Protected Landmark (Hulsey-Davis House, 1216 Wrightwood St)",
    }),
    (2021, "FW Heritage, LLC for rehabilitating a Folk Victorian cottage", {
        "address": "1810 SUMMER ST",
        "hcad_num": "0051290000003",
        "project_name": "1810 Summer Street Folk Victorian Cottage",
        "historic_district": "High First Ward",
        "location_source": "High First Ward Historic District (1810 Summer St, c. 1907)",
    }),
    (2021, "Maria & Richard Neel", {
        "address": "1314 GLENBROOKE DR",
        "hcad_num": "0822210060009",
        "project_name": "Neel Mid-Century Modern Residence (Meadow Creek Village)",
        "lat": 29.6738,
        "lng": -95.2498,
        "location_source": "Meadow Creek Village Mid-Century Modern Survey",
    }),
    (2021, "Hilary & James Newman", {
        "address": "2915 SOUTHMORE BLVD",
        "hcad_num": "0193100000014",
        "project_name": "Newman Tudor Revival Residence (Riverside Terrace)",
        "location_source": "HCAD Ownership Record (James E. & Hilary H. Newman, 2915 Southmore Blvd)",
    }),
    (2021, "Lennie Waite & Matt Hoffman", {
        "address": "1617 KIPLING ST",
        "hcad_num": "0522230000006",
        "project_name": "Waite-Hoffman Residence (Mandell Place)",
        "location_source": "Preservation Houston 2022 Good Brick Tour (1617 Kipling St)",
    }),
    (2021, "Peter F. Tamborello House", {
        "address": "1813 GENTRY ST",
        "hcad_num": "0090800000010",
        "project_name": "Peter F. Tamborello House",
        "location_source": "COH Protected Landmark (Peter F. Tamborello House, 1813 Gentry St)",
    }),
    (2021, "Buffalo Bayou Park", {
        "address": "105 SABINE ST",
        "project_name": "Buffalo Bayou Park",
        "lat": 29.7618,
        "lng": -95.3752,
        "location_source": "Buffalo Bayou Partnership Park Core (105 Sabine St)",
    }),
    # 2020
    (2020, "Apollo Mission Control Center", {
        "address": "2101 E NASA PKWY",
        "project_name": "Apollo Mission Control Center (Building 30, NASA JSC)",
        "lat": 29.5583,
        "lng": -95.0899,
        "location_source": "National Historic Landmark (NASA Johnson Space Center, 2101 E NASA Pkwy)",
    }),
    (2020, "Houston Bar Association Building", {
        "address": "723 MAIN ST",
        "hcad_num": "0010800000010",
        "project_name": "Houston Bar Association Building (AC Hotel Downtown)",
        "location_source": "Downtown Historic District (AC Hotel by Marriott, 723 Main St)",
    }),
    (2020, "Petroleum Building", {
        "address": "1314 TEXAS ST",
        "hcad_num": "0011050000006",
        "project_name": "The Petroleum Building (Cambria Hotel Downtown)",
        "location_source": "COH Protected Landmark (Petroleum Building, 1314 Texas Ave)",
    }),
    (2020, "David Powell for restoring a Folk Victorian house", {
        "address": "2409 FREEMAN ST",
        "hcad_num": "0090810000005",
        "project_name": "Near Northside Folk Victorian Cottage",
        "lat": 29.7820,
        "lng": -95.3622,
        "location_source": "Near Northside Historic Preservation Survey",
    }),
    (2020, "Karen & Neal Dikeman for restoring two historic houses", {
        "address": "908 SABINE ST",
        "hcad_num": "0300430000015",
        "project_name": "908 Sabine St & 2214 Kane St Historic Houses",
        "historic_district": "Old Sixth Ward",
        "location_source": "HCAD Ownership Record (Neal Dikeman, 908 Sabine St) & 2022 Good Brick Tour",
    }),
    (2020, "Phil Neisel for restoring the Benjamin Reisner House", {
        "address": "2006 DECATUR ST",
        "hcad_num": "0052010000005",
        "project_name": "Benjamin Reisner House",
        "historic_district": "Old Sixth Ward",
        "location_source": "Preservation Houston 2022 Good Brick Tour & HCAD Ownership (Philip C. Neisel, 2006 Decatur St)",
    }),
    (2020, "Parra Design Group for rehabilitating the Milton & Carrie Curtis House", {
        "address": "2316 HARLEM ST",
        "hcad_num": "0131200000004",
        "project_name": "Milton & Carrie Curtis House",
        "location_source": "Preservation Houston 2022 Good Brick Tour (2316 Harlem St, Built 1953)",
    }),
    (2020, "W-K-M Company headquarters", {
        "address": "220 ROBERTS ST",
        "hcad_num": "0021530000001",
        "project_name": "W-K-M Company Headquarters (Roberts Industrial Center)",
        "lat": 29.7495,
        "lng": -95.3442,
        "location_source": "East End Industrial Landmark (220 Roberts St)",
    }),
    (2020, "Radom Capital LLC for rehabilitating a mid-century modern retail center", {
        "address": "714 YALE ST",
        "hcad_num": "0202300000001",
        "project_name": "Yale Street Mid-Century Retail Center",
        "historic_district": "Houston Heights South",
        "location_source": "Radom Capital Heights Retail Adaptive Reuse (714 Yale St)",
    }),
    (2020, "Quality Laundry Building", {
        "address": "1110 W GRAY ST",
        "hcad_num": "0261760000001",
        "project_name": "Quality Laundry Building",
        "location_source": "COH Protected Landmark (Quality Laundry Building, 1110 W Gray St)",
    }),
    (2020, "Christ Church Cathedral", {
        "address": "1117 TEXAS AVE",
        "hcad_num": "0010620000001",
        "project_name": "Christ Church Cathedral Sanctuary",
        "location_source": "COH Landmark & NRHP Listing (Christ Church Cathedral, 1117 Texas Ave)",
    }),
    (2020, "South Main Baptist Church", {
        "address": "4100 MAIN ST",
        "hcad_num": "0141910000001",
        "project_name": "South Main Baptist Church Sanctuary",
        "location_source": "Preservation Houston 2024 Good Brick Tour (4100 Main St)",
    }),
    (2020, "Four Square Design Studio", {
        "address": "1912 DECATUR ST",
        "hcad_num": "0052090000003",
        "project_name": "First Ward Folk Victorian House",
        "historic_district": "Old Sixth Ward",
        "location_source": "First Ward / Old Sixth Ward Historic Restoration",
    }),
    (2020, "Laura Carrera & Andres Utting", {
        "address": "841 ARLINGTON ST",
        "hcad_num": "0202450000027",
        "project_name": "Carrera-Utting Craftsman Bungalow",
        "historic_district": "Norhill",
        "location_source": "HCAD Ownership Record (Andres A. Utting, 841 Arlington St)",
    }),
    (2020, "Jerry Hooker & Jacob Sudhoff", {
        "address": "6 COURTLANDT PL",
        "hcad_num": "0102490000003",
        "project_name": "C.L. Neuhaus House",
        "historic_district": "Courtlandt Place",
        "location_source": "NRHP & COH Landmark (C.L. Neuhaus House, 6 Courtlandt Place)",
    }),
    (2020, "Blue Triangle", {
        "address": "3005 MCGOWEN ST",
        "hcad_num": "0190150000001",
        "project_name": "Blue Triangle Community Center (John Biggers Mural)",
        "location_source": "COH Landmark (Blue Triangle YWCA / Community Center, 3005 McGowen St)",
    }),
    (2020, "Asia Society Texas Center", {
        "address": "1370 SOUTHMORE BLVD",
        "hcad_num": "0391930010001",
        "project_name": "Asia Society Texas Center",
        "location_source": "Museum District Architectural Landmark (1370 Southmore Blvd)",
    }),
    # 2019
    (2019, "Jay Hurt", {
        "address": "4 SHADOW LAWN ST",
        "hcad_num": "0523230010001",
        "project_name": "Arthur J. Hurt Colonial Revival Residence",
        "historic_district": "Shadow Lawn",
        "location_source": "HCAD Ownership Record (Arthur J. Hurt III, 4 Shadow Lawn St)",
    }),
    (2019, "Nancy & Jim Butler", {
        "address": "1609 KIRBY DR",
        "hcad_num": "0601530370002",
        "project_name": "Butler Tudor Revival Residence (River Oaks)",
        "location_source": "HCAD Ownership Record (James H. & Nancy S. Butler, 1609 Kirby Dr)",
    }),
    (2019, "Nicole & Mark Hochglaube", {
        "address": "3515 FANNIN ST",
        "hcad_num": "0141700000001",
        "project_name": "Maria Boswell Flake Home",
        "lat": 29.7397,
        "lng": -95.3768,
        "location_source": "NRHP & Houston Chronicle (Maria Boswell Flake Home, 3515 Fannin St / 1103 Berry St)",
    }),
    (2019, "FW Heritage, LLC for the rescue and restoration of a Queen Anne-style house", {
        "address": "2008 CROCKETT ST",
        "hcad_num": "0051440000004",
        "project_name": "2008 Crockett Street Queen Anne House",
        "historic_district": "High First Ward",
        "location_source": "High First Ward Historic District (2008 Crockett St)",
    }),
    (2019, "SITE Gallery Houston at The Silos", {
        "address": "1502 SAWYER ST",
        "hcad_num": "0401760010002",
        "project_name": "SITE Gallery Houston at The Silos on Sawyer",
        "lat": 29.7725,
        "lng": -95.3879,
        "location_source": "Sawyer Yards Arts Campus (Riviana Rice Silos, 1502 Sawyer St)",
    }),
    (2019, "Extending Arms of Christ mosaic", {
        "address": "6565 FANNIN ST",
        "hcad_num": "0410100020031",
        "project_name": "Extending Arms of Christ Mosaic (Houston Methodist Hospital)",
        "lat": 29.7105,
        "lng": -95.3992,
        "location_source": "Houston Methodist Hospital Campus (6565 Fannin St)",
    }),
    (2019, "Temple of Rest Mausoleum", {
        "address": "1207 W DALLAS ST",
        "hcad_num": "0401190000001",
        "project_name": "Temple of Rest Mausoleum at Beth Israel Cemetery",
        "lat": 29.7568,
        "lng": -95.3802,
        "location_source": "Beth Israel Cemetery Historic Core (1207 W Dallas St)",
    }),
    (2019, "Rebirth of Our Nationality mural", {
        "address": "5900 CANAL ST",
        "project_name": "The Rebirth of Our Nationality Mural (Leo Tanguma, 1973)",
        "lat": 29.7438,
        "lng": -95.3148,
        "location_source": "East End Cultural District (5900 Canal St)",
    }),
    (2019, "Emancipation Park", {
        "address": "3018 DOWLING ST",
        "hcad_num": "0191150000001",
        "project_name": "Emancipation Park",
        "location_source": "COH Protected Landmark (Emancipation Park, 3018 Emancipation Ave / Dowling St)",
    }),
    # 2018
    (2018, "Melrose Building", {
        "address": "1121 WALKER ST",
        "hcad_num": "0010980000001",
        "project_name": "Melrose Building (Le Méridien Houston Downtown)",
        "location_source": "COH Protected Landmark (Melrose Building, 1121 Walker St)",
    }),
    (2018, "Revive Development", {
        "address": "1123 E 11TH ST",
        "hcad_num": "0202490000014",
        "project_name": "1123 E. 11th Street Bungalow",
        "historic_district": "Norhill",
        "location_source": "Preservation Houston 2018 Good Brick Gallery (1123 E 11th St)",
    }),
    (2018, "Texas Company Building", {
        "address": "720 SAN JACINTO ST",
        "hcad_num": "0010790000001",
        "project_name": "The Texas Company (Texaco) Building",
        "location_source": "COH Protected Landmark (Texas Company Building, 720 San Jacinto St)",
    }),
    (2018, "Sampson Lofts", {
        "address": "910 SAMPSON ST",
        "hcad_num": "0400860000010",
        "project_name": "Sampson Lofts (Waddell's Furniture Warehouse)",
        "lat": 29.7478,
        "lng": -95.3424,
        "location_source": "NRHP & East End Warehouse District (910 Sampson St)",
    }),
    (2018, "George & Emma Westfall House", {
        "address": "303 HAWTHORNE ST",
        "hcad_num": "0370290000003",
        "project_name": "George & Emma Westfall House",
        "historic_district": "Westmoreland",
        "location_source": "Preservation Houston 2018 Good Brick Gallery (303 Hawthorne St)",
    }),
    (2018, "Ferdinand G. Schoellkopf House", {
        "address": "2119 LUBBOCK ST",
        "hcad_num": "0051930000012",
        "project_name": "Ferdinand G. Schoellkopf House",
        "historic_district": "Old Sixth Ward",
        "location_source": "Preservation Houston 2018 Good Brick Gallery & Tour (2119 Lubbock St)",
    }),
    (2018, "Ewart H. Lightfoot House", {
        "address": "3702 AUDUBON PL",
        "hcad_num": "0261410000017",
        "project_name": "Ewart H. Lightfoot House",
        "historic_district": "Audubon Place",
        "location_source": "Preservation Houston 2018 Good Brick Gallery & Tour (3702 Audubon Pl)",
    }),
    (2018, "Minnie & Joseph Blazek House", {
        "address": "319 W 15TH ST",
        "hcad_num": "0201440000009",
        "project_name": "Minnie & Joseph Blazek House",
        "historic_district": "Houston Heights West",
        "location_source": "Preservation Houston 2018 Good Brick Gallery (319 W 15th St)",
    }),
    (2018, "Milby High School", {
        "address": "1601 BROADWAY ST",
        "hcad_num": "0162780000001",
        "project_name": "Charles H. Milby High School",
        "lat": 29.7145,
        "lng": -95.2768,
        "location_source": "HISD Historic Campus (1601 Broadway St)",
    }),
    (2018, "Axelrad Building", {
        "address": "1517 ALABAMA ST",
        "hcad_num": "0141740000006",
        "project_name": "Axelrad Beer Garden (1915 Grocery Building)",
        "location_source": "Midtown Landmark (1517 Alabama St)",
    }),
    (2018, "Chas Haynes", {
        "address": "3709 LA BRANCH ST",
        "hcad_num": "0141800000010",
        "project_name": "1921 Gulf Service Station (Retrospect Coffee Bar)",
        "lat": 29.7368,
        "lng": -95.3746,
        "location_source": "National Trust for Historic Places (3709 La Branch St)",
    }),
    (2018, "Brochstein Pavilion", {
        "address": "6100 MAIN ST",
        "hcad_num": "0391880000001",
        "project_name": "Raymond & Susan Brochstein Pavilion at Rice University",
        "lat": 29.7176,
        "lng": -95.4015,
        "location_source": "Rice University Central Quadrangle (6100 Main St)",
    }),
    # 2017
    (2017, "Gov. William P. Hobby House", {
        "address": "2115 GLEN HAVEN BLVD",
        "hcad_num": "0550130000005",
        "project_name": "Gov. William P. Hobby House",
        "location_source": "Preservation Houston 2017 Good Brick Gallery (2115 Glen Haven Blvd)",
    }),
    (2017, "Fire Station No. 2", {
        "address": "317 SAMPSON ST",
        "hcad_num": "0030370000026",
        "project_name": "Fire Station No. 2",
        "location_source": "COH Landmark & 2017 Good Brick Tour (317 Sampson St)",
    }),
    (2017, "Antonio Herrada & Peter Boyle", {
        "address": "2006 CROCKETT ST",
        "hcad_num": "0051440000005",
        "project_name": "2006 Crockett Street Victorian Cottage",
        "historic_district": "High First Ward",
        "location_source": "Preservation Houston 2017 Good Brick Gallery & HCAD Ownership (2006 Crockett St)",
    }),
    (2017, "Lin Chong & Dominic Yap", {
        "address": "3005 HOUSTON AVE",
        "hcad_num": "0372670000004",
        "project_name": "3005 Houston Avenue Bungalow",
        "historic_district": "Germantown",
        "location_source": "Preservation Houston 2017 Good Brick Gallery (3005 Houston Ave)",
    }),
    (2017, "Fire Station No. 3", {
        "address": "1919 HOUSTON AVE",
        "hcad_num": "0051470000011",
        "project_name": "Fire Station No. 3",
        "location_source": "COH Landmark (Fire Station No. 3, 1919 Houston Ave)",
    }),
    (2017, "Dentler Building", {
        "address": "1809 SUMMER ST",
        "hcad_num": "0051300000010",
        "project_name": "George H. Dentler & Sons Building",
        "historic_district": "High First Ward",
        "location_source": "Preservation Houston 2017 Good Brick Tour & HCAD Ownership (1809 Summer St)",
    }),
    (2017, "FW Heritage, LLC, for the renovation and restoration of a historic Folk Victorian", {
        "address": "2207 KEENE ST",
        "hcad_num": "0090620000010",
        "project_name": "2207 Keene Street Folk Victorian House",
        "location_source": "Preservation Houston 2017 Good Brick Gallery (2207 Keene St)",
    }),
    (2017, "FW Heritage, LLC, for the renovation and sympathetic addition", {
        "address": "1815 SABINE ST",
        "hcad_num": "0051280000013",
        "project_name": "1815 Sabine Street Historic Cottage",
        "historic_district": "High First Ward",
        "location_source": "High First Ward Historic District (1815 Sabine St)",
    }),
    (2017, "BCN Taste & Tradition", {
        "address": "4206 ROSELAND ST",
        "hcad_num": "0230810000002",
        "project_name": "BCN Taste & Tradition Restaurant (1920 Montrose House)",
        "location_source": "HCAD Ownership Record (Fivos Kazilas, 4206 Roseland St)",
    }),
    (2017, "DeLuxe Theater", {
        "address": "3303 LYONS AVE",
        "hcad_num": "0131580000018",
        "project_name": "DeLuxe Theater",
        "lat": 29.7753,
        "lng": -95.3375,
        "location_source": "Fifth Ward Cultural District (3303 Lyons Ave)",
    }),
    (2017, "Wolters High School", {
        "address": "204 IVY AVE",
        "project_name": "Wolters High School (Deer Park ISD)",
        "lat": 29.7042,
        "lng": -95.1238,
        "location_source": "Deer Park ISD Historic Campus (204 Ivy Ave, Deer Park)",
    }),
    (2017, "Buffalo Bayou Park Cistern", {
        "address": "105 SABINE ST",
        "project_name": "The Buffalo Bayou Park Cistern (1926)",
        "lat": 29.7623,
        "lng": -95.3774,
        "location_source": "Buffalo Bayou Partnership (105 Sabine St)",
    }),
    (2017, "New Hope Housing at Brays Crossing", {
        "address": "6311 GULF FWY",
        "hcad_num": "1374310010001",
        "project_name": "New Hope Housing at Brays Crossing",
        "lat": 29.7024,
        "lng": -95.3082,
        "location_source": "New Hope Housing Campus (6311 Gulf Fwy)",
    }),
    # 2016
    (2016, "Victor Neuhaus, Jr. House", {
        "address": "2910 LAZY LANE BLVD",
        "hcad_num": "0601510000002",
        "project_name": "Hugo Victor Neuhaus, Jr. House",
        "location_source": "COH Protected Landmark (Hugo Victor Neuhaus Jr. House, 2910 Lazy Lane Blvd)",
    }),
    (2016, "Gottlieb Eisele House", {
        "address": "716 SABINE ST",
        "hcad_num": "1268530020001",
        "project_name": "Gottlieb Eisele House (Sabine Street Cottages)",
        "historic_district": "Old Sixth Ward",
        "location_source": "Preservation Houston 2016 Good Brick Tour & HCAD Ownership (Lee Roeder, 716 Sabine St)",
    }),
    (2016, "Kinneymorrow Architecture", {
        "address": "2219 KANE ST",
        "hcad_num": "0051800000015",
        "project_name": "2219 Kane Street Victorian Cottage",
        "historic_district": "Old Sixth Ward",
        "location_source": "Preservation Houston 2017 Good Brick Tour & HCAD Ownership (MTMK LLC, 2219 Kane St)",
    }),
    (2016, "Dallas McNamara", {
        "address": "1603 CHERRYHURST ST",
        "hcad_num": "0382430000007",
        "project_name": "McNamara Craftsman Bungalow & Art Space",
        "location_source": "HCAD Ownership Record (1603 Cherryhurst St)",
    }),
    (2016, "Oriental Textile Mill", {
        "address": "2201 LAWRENCE ST",
        "hcad_num": "0200490000001",
        "project_name": "Oriental Textile Mill (Heights Clock Tower)",
        "location_source": "COH Protected Landmark (Oriental Textile Mill, 2201 Lawrence St)",
    }),
    (2016, "Dittman Bakery Building", {
        "address": "1814 WASHINGTON AVE",
        "hcad_num": "0052310000001",
        "project_name": "Dittman Bakery Building (B&B Butchers & Restaurant)",
        "location_source": "Old Sixth Ward / Washington Corridor Landmark (1814 Washington Ave)",
    }),
    (2016, "Lee Davis Library", {
        "address": "8060 SPENCER HWY",
        "project_name": "Lee Davis Library (San Jacinto College Central Campus)",
        "lat": 29.6612,
        "lng": -95.1165,
        "location_source": "San Jacinto College Central Campus (8060 Spencer Hwy, Pasadena)",
    }),
    (2016, "Bender High School", {
        "address": "611 HIGGINS ST",
        "hcad_num": "0172220000011",
        "project_name": "Charles Bender High School Performing Arts Center",
        "lat": 29.9968,
        "lng": -95.2635,
        "location_source": "City of Humble Landmark (611 Higgins St / 110 Avenue C, Humble)",
    }),
    (2016, "H.M. Harrell, Jr. Residence", {
        "address": "4012 WILLOWICK RD",
        "hcad_num": "0601590560024",
        "project_name": "Mr. & Mrs. H.M. Harrell, Jr. Residence (Harwood Taylor, 1963)",
        "location_source": "River Oaks Architectural Survey & 2018 Good Brick Tour (4012 Willowick Rd / 67 Tiel Way)",
    }),
    (2016, "Drew Bacon", {
        "address": "309 SAMPSON ST",
        "hcad_num": "0030370000008",
        "project_name": "309 Sampson Street Folk Victorian House",
        "location_source": "Preservation Houston 2017 Good Brick Tour & HCAD Ownership (Andrew G. Bacon, 309 Sampson St)",
    }),
    (2016, "Jim Reeder & Eric Nevil", {
        "address": "3229 GROVELAND LN",
        "hcad_num": "0601400000004",
        "project_name": "3229 Groveland Lane (1947 John Staub Residence)",
        "location_source": "Preservation Houston 2016 Good Brick Tour (3229 Groveland Ln)",
    }),
    (2016, "Henderson-Scurlock House", {
        "address": "3663 DEL MONTE DR",
        "hcad_num": "0601610760003",
        "project_name": "Henderson-Scurlock House",
        "location_source": "COH Protected Landmark (Henderson-Scurlock House, 3663 Del Monte Dr)",
    }),
    (2016, "Bendit House", {
        "address": "4111 DRUMMOND ST",
        "hcad_num": "0730040310003",
        "project_name": "Bendit House (1953 Lars Bang Residence)",
        "location_source": "Preservation Houston 2016 Good Brick Tour & HCAD Ownership (Steven & Martha Curry, 4111 Drummond St)",
    }),
    (2016, "Hirzel-von Haxthausen House", {
        "address": "2120 SABINE ST",
        "hcad_num": "0051910000001",
        "project_name": "Hirzel-von Haxthausen House",
        "historic_district": "Old Sixth Ward",
        "location_source": "COH Protected Landmark (The Hirzel-von Haxthausen House, 2120 Sabine St)",
    }),
    (2016, "McGovern Centennial Gardens", {
        "address": "1500 HERMANN DR",
        "project_name": "McGovern Centennial Gardens & Cherie Flores Garden Pavilion",
        "lat": 29.7219,
        "lng": -95.3887,
        "location_source": "Hermann Park Conservancy (1500 Hermann Dr)",
    }),
    # 2015
    (2015, "San Jacinto High School", {
        "address": "1300 HOLMAN ST",
        "hcad_num": "0141760000001",
        "project_name": "San Jacinto Memorial Building (Former San Jacinto High School, HCC Central)",
        "location_source": "Houston Community College Central Campus (1300 Holman St)",
    }),
    (2015, "Robert E. Lee Elementary School", {
        "address": "2101 SOUTH ST",
        "hcad_num": "0031940000001",
        "project_name": "Leonel J. Castillo Community Center (Former Robert E. Lee Elementary School)",
        "location_source": "COH Protected Landmark (2101 South St)",
    }),
    (2015, "Sam Houston Park", {
        "address": "1100 BAGBY ST",
        "hcad_num": "0400030000014",
        "project_name": "The Heritage Society at Sam Houston Park",
        "location_source": "COH Landmark (Sam Houston Park, 1100 Bagby St)",
    }),
    (2015, "BBVA Compass Stadium", {
        "address": "2200 TEXAS AVE",
        "hcad_num": "1338050010001",
        "project_name": "Shell Energy Stadium (BBVA Compass Stadium)",
        "lat": 29.7522,
        "lng": -95.3524,
        "location_source": "East Downtown Stadium Campus (2200 Texas Ave)",
    }),
    (2015, "Mount Carmel High School", {
        "address": "6700 MOUNT CARMEL ST",
        "hcad_num": "0830440000001",
        "project_name": "Cristo Rey Jesuit College Preparatory (Former Mount Carmel High School)",
        "lat": 29.6788,
        "lng": -95.3065,
        "location_source": "Garden Villas Historic Campus (6700 Mount Carmel St)",
    }),
    (2015, "Henshaw House", {
        "address": "7112 NEWCASTLE ST",
        "hcad_num": "0410820000001",
        "project_name": "Henshaw House at Nature Discovery Center (Russ Pitman Park)",
        "lat": 29.7026,
        "lng": -95.4508,
        "location_source": "City of Bellaire Landmark (7112 Newcastle St)",
    }),
    # 2014
    (2014, "Trinity Episcopal Church", {
        "address": "1015 HOLMAN ST",
        "hcad_num": "0141530000001",
        "project_name": "Trinity Episcopal Church",
        "location_source": "Midtown Landmark (Trinity Episcopal Church, 1015 Holman St)",
    }),
    (2014, "Sylvan Beach Pavilion", {
        "address": "1 SYLVAN BEACH DR",
        "project_name": "Sylvan Beach Pavilion (La Porte)",
        "lat": 29.6517,
        "lng": -95.0114,
        "location_source": "NRHP Landmark (Sylvan Beach Park, La Porte)",
    }),
    (2014, "Edith L. Moore Log Cabin", {
        "address": "440 WILCHESTER BLVD",
        "hcad_num": "0420940000025",
        "project_name": "Edith L. Moore Log Cabin & Sanctuary (Houston Audubon)",
        "lat": 29.7760,
        "lng": -95.5653,
        "location_source": "COH Protected Landmark (Edith L. Moore Log Cabin, 440 Wilchester Blvd)",
    }),
    (2014, "Discovery Green", {
        "address": "1500 MCKINNEY ST",
        "hcad_num": "0011640000001",
        "project_name": "Discovery Green Conservancy",
        "lat": 29.7534,
        "lng": -95.3596,
        "location_source": "Downtown Park Campus (1500 McKinney St)",
    }),
    (2014, "Bethel Park", {
        "address": "801 ANDREWS ST",
        "hcad_num": "0021590000001",
        "project_name": "Bethel Park (Former Bethel Missionary Baptist Church)",
        "historic_district": "Freedmen's Town",
        "lat": 29.7562,
        "lng": -95.3775,
        "location_source": "COH Protected Landmark (Bethel Baptist Church Park, 801 Andrews St)",
    }),
    # 2012
    (2012, "Katie & Nick Johnson", {
        "address": "1659 SOUTH BLVD",
        "hcad_num": "0530380000001",
        "project_name": "Johnson Residence (1659 South Blvd)",
        "historic_district": "Boulevard Oaks",
        "location_source": "HCAD Ownership Record (Nick H. & Katherine P. Johnson, 1659 South Blvd)",
    }),
    (2012, "Oak Forest Neighborhood Library", {
        "address": "1349 W 43RD ST",
        "hcad_num": "0771640010001",
        "project_name": "Oak Forest Neighborhood Library",
        "lat": 29.8286,
        "lng": -95.4320,
        "location_source": "Houston Public Library Branch (1349 W 43rd St)",
    }),
    (2012, "Lynn & Ty Kelly", {
        "address": "2300 PINE VALLEY DR",
        "hcad_num": "0601560480001",
        "project_name": "George V. Rotan Home",
        "location_source": "COH Protected Landmark (George V. Rotan Home, 2300 Pine Valley Dr)",
    }),
    (2012, "Houston Permitting Office", {
        "address": "1002 WASHINGTON AVE",
        "hcad_num": "0010010000030",
        "project_name": "Houston Permitting Center (1924 Historic Warehouse)",
        "lat": 29.7675,
        "lng": -95.3688,
        "location_source": "City of Houston Permitting Center (1002 Washington Ave)",
    }),
    (2012, "Glenwood Cemetery", {
        "address": "2525 WASHINGTON AVE",
        "hcad_num": "0401730000001",
        "project_name": "Historic Glenwood Cemetery",
        "lat": 29.7690,
        "lng": -95.3855,
        "location_source": "NRHP Historic Cemetery (2525 Washington Ave)",
    }),
    (2012, "Nancy & Walter Bratic", {
        "address": "3362 DEL MONTE DR",
        "hcad_num": "0601320000014",
        "project_name": "Bratic Residence (3362 Del Monte Dr)",
        "location_source": "HCAD Ownership Record (Walter & Nancy Bratic, 3362 Del Monte Dr)",
    }),
    (2012, "Fred Sharifi", {
        "address": "1831 COLQUITT ST",
        "hcad_num": "1456070010001",
        "project_name": "Sharifi Montrose Commercial Building",
        "location_source": "HCAD Ownership Record (Fred & Soody Sharifi Partnership, 1831 Colquitt St)",
    }),
    (2012, "Paula & Sam Douglass", {
        "address": "3452 DEL MONTE DR",
        "hcad_num": "0601460190005",
        "project_name": "Douglass Residence (River Oaks)",
        "location_source": "River Oaks Historic Survey (1936 Residence)",
    }),
    (2012, "Henry Stude Garage", {
        "address": "14 REMINGTON LN",
        "hcad_num": "0502260000012",
        "project_name": "Henry W. Stude House & Garage (Shadyside)",
        "location_source": "COH Landmark (Henry W. Stude House, 14 Remington Ln)",
    }),
    (2012, "Minnette & Peter Boesel", {
        "address": "4509 WALKER ST",
        "hcad_num": "0351550000001",
        "project_name": "Boesel Eastwood Historic Duplex",
        "location_source": "Eastwood Historic District Survey",
    }),
    (2012, "Pioneer Log Cabin Museum", {
        "address": "6510 MACGREGOR WAY",
        "project_name": "Pioneer Memorial Log House Museum (Hermann Park)",
        "lat": 29.7131,
        "lng": -95.3853,
        "location_source": "San Jacinto Chapter DRT Museum in Hermann Park (6510 MacGregor Way)",
    }),
    (2012, "historic county courthouse", {
        "address": "301 FANNIN ST",
        "hcad_num": "0010320000001",
        "project_name": "1910 Harris County Courthouse",
        "location_source": "COH Landmark & NRHP Listing (1910 Harris County Courthouse, 301 Fannin St)",
    }),
    (2012, "Julia Ideson Building", {
        "address": "500 MCKINNEY ST",
        "hcad_num": "0011480000019",
        "project_name": "Julia Ideson Building (Houston Public Library)",
        "location_source": "COH Protected Landmark (Julia Ideson Building, 500 McKinney St)",
    }),
    # 2011
    (2011, "Carol & Mike Linn", {
        "address": "2 REMINGTON LN",
        "hcad_num": "0502260000002",
        "project_name": "Linn Residence (Shadyside)",
        "lat": 29.7240,
        "lng": -95.3921,
        "location_source": "Shadyside Historic Survey (2 Remington Ln)",
    }),
    (2011, "Saul Obregon and Ruben Obregon", {
        "address": "1511 EVERETT ST",
        "hcad_num": "0031590000010",
        "project_name": "William L. Shipp House",
        "location_source": "COH Protected Landmark & HCAD Ownership (Ruben Obregon, 1511 Everett St)",
    }),
    (2011, "Market Square Park", {
        "address": "301 MILAM ST",
        "hcad_num": "0010350000001",
        "project_name": "Market Square Park",
        "historic_district": "Main Street Market Square",
        "location_source": "Downtown Historic District (Market Square Park, 301 Milam St)",
    }),
    (2011, "Gregory School", {
        "address": "1300 VICTOR ST",
        "hcad_num": "0050180000019",
        "project_name": "The African American Library at the Gregory School",
        "historic_district": "Freedmen's Town",
        "location_source": "COH Protected Landmark (Gregory School, 1300 Victor St)",
    }),
    (2011, "Thomas J. Bath & Dan Hawkins", {
        "address": "200 WESTMORELAND ST",
        "hcad_num": "0370350000008",
        "project_name": "200 Westmoreland Street Residence",
        "historic_district": "Westmoreland",
        "location_source": "Westmoreland Historic District (200 Westmoreland St, Built 1910)",
    }),
    (2011, "Bennie & David Ansell", {
        "address": "707 SABINE ST",
        "hcad_num": "0052250000032",
        "project_name": "Ansell Residence (707 Sabine St)",
        "historic_district": "Old Sixth Ward",
        "location_source": "HCAD Ownership Record (David & Benedikte Ansell, 707 Sabine St)",
    }),
    (2011, "Farnsworth & Chambers", {
        "address": "2999 S WAYSIDE DR",
        "hcad_num": "0410070350021",
        "project_name": "Farnsworth & Chambers (Gragg) Building — Houston Parks & Recreation HQ",
        "location_source": "COH Protected Landmark (Farnsworth & Chambers Building, 2999 S Wayside Dr)",
    }),
    (2011, "Beer Can House", {
        "address": "222 MALONE ST",
        "hcad_num": "0540320000005",
        "project_name": "The Beer Can House (John Milkovisch Folk Art Landmark)",
        "location_source": "Orange Show Center for Visionary Art (222 Malone St)",
    }),
    (2011, "Taryn Kinney & Michael Morrow", {
        "address": "1817 KANE ST",
        "hcad_num": "0052260000014",
        "project_name": "Kinney-Morrow 1876 Cottage",
        "historic_district": "Old Sixth Ward",
        "location_source": "Old Sixth Ward Historic District (1817 Kane St)",
    }),
    (2011, "SILCO, Inc.", {
        "address": "1501 COMMERCE ST",
        "hcad_num": "0010120000005",
        "project_name": "1910 Nabisco Bakery Building (Downtown)",
        "lat": 29.7627,
        "lng": -95.3545,
        "location_source": "Downtown Warehouse District (1501 Commerce St)",
    }),
    (2011, "Kate McCormick & Champ Warren", {
        "address": "3740 CARLON ST",
        "hcad_num": "0560580000012",
        "project_name": "Warren-McCormick Residence (Southside Place)",
        "location_source": "Preservation Houston Good Brick Tour & HCAD Ownership (Champ D. Warren III, 3740 Carlon St)",
    }),
    (2011, "National Cash Register Co.", {
        "address": "515 CAROLINE ST",
        "hcad_num": "0010530000008",
        "project_name": "National Cash Register Co. Building",
        "location_source": "COH Protected Landmark & HCAD Ownership (Deborah Keyser, 515 Caroline St)",
    }),
    (2011, "Brennan's of Houston", {
        "address": "3300 SMITH ST",
        "hcad_num": "0141490000001",
        "project_name": "Brennan's of Houston (Junior League Building, 1929 John Staub)",
        "location_source": "Midtown Landmark (3300 Smith St)",
    }),
    # 2010
    (2010, "1940 Air Terminal Museum", {
        "address": "8325 TRAVELAIR ST",
        "hcad_num": "0432070000001",
        "project_name": "1940 Air Terminal Museum (Houston Municipal Air Terminal)",
        "lat": 29.6508,
        "lng": -95.2842,
        "location_source": "COH Protected Landmark & NRHP Listing (8325 Travelair St)",
    }),
    # 2009
    (2009, "John H. Reagan High School", {
        "address": "413 E 13TH ST",
        "hcad_num": "0201690000001",
        "project_name": "Heights High School (John H. Reagan High School)",
        "historic_district": "Houston Heights East",
        "location_source": "COH Landmark & NRHP Listing (413 E 13th St)",
    }),
    (2009, "Wharton Elementary School", {
        "address": "900 W GRAY ST",
        "hcad_num": "0360040000001",
        "project_name": "Clarence R. Wharton Dual Language Academy (1929)",
        "location_source": "HISD Historic School Campus (900 W Gray St)",
    }),
    # 2008
    (2008, "Tradition Bank Plaza", {
        "address": "5020 MONTROSE BLVD",
        "hcad_num": "0502250000002",
        "project_name": "The Plaza Hotel (Tradition Bank Plaza)",
        "location_source": "Museum District Landmark (5020 Montrose Blvd)",
    }),
    (2008, "Bartlett Lofts", {
        "address": "2414 COMMERCE ST",
        "hcad_num": "0020180000001",
        "project_name": "Bartlett Lofts (Texas Staple Company Warehouse)",
        "location_source": "East End Warehouse Landmark (2414 Commerce St)",
    }),
    (2008, "13 Celsius", {
        "address": "3000 CAROLINE ST",
        "hcad_num": "0130500000006",
        "project_name": "13 Celsius Wine Bar (Jennings Cleaning Building)",
        "location_source": "Midtown Landmark (3000 Caroline St)",
    }),
    (2008, "West University Elementary School", {
        "address": "3756 UNIVERSITY BLVD",
        "hcad_num": "0560220000001",
        "project_name": "West University Elementary School",
        "location_source": "West University Place Historic School Campus (3756 University Blvd)",
    }),
    (2008, "St. Anne Catholic Church", {
        "address": "2140 WESTHEIMER RD",
        "hcad_num": "0401540000001",
        "project_name": "St. Anne Catholic Church & 1929 Parish Hall",
        "location_source": "Montrose Landmark (2140 Westheimer Rd)",
    }),
    (2008, "Founders Memorial Park", {
        "address": "1217 W DALLAS ST",
        "hcad_num": "0401190000002",
        "project_name": "Founders Memorial Park & Cemetery",
        "lat": 29.7575,
        "lng": -95.3792,
        "location_source": "Historic Founders Cemetery (1217 W Dallas St)",
    }),
    # 2007
    (2007, "Seal McDougle Memorial Park", {
        "address": "21022 ALDINE WESTFIELD RD",
        "project_name": "Seal McDougle Memorial Park and Cemetery (Spring)",
        "lat": 30.0468,
        "lng": -95.3620,
        "location_source": "Post Wood MUD Historic Cemetery (Spring, TX)",
    }),
    (2007, "Mary Wood & Hugo V. Neuhaus, Jr. House", {
        "address": "2910 LAZY LANE BLVD",
        "hcad_num": "0601510000002",
        "project_name": "Mary Wood & Hugo V. Neuhaus, Jr. House",
        "location_source": "COH Protected Landmark (2910 Lazy Lane Blvd)",
    }),
    # 2006
    (2006, "Byrd's Department Store", {
        "address": "420 MAIN ST",
        "hcad_num": "0010360000006",
        "project_name": "Byrd's Department Store Building",
        "historic_district": "Main Street Market Square",
        "lat": 29.7611,
        "lng": -95.3621,
        "location_source": "Main Street Market Square Historic District (420 Main St)",
    }),
    (2006, "Jefferson Davis Hospital", {
        "address": "1101 ELDER ST",
        "hcad_num": "0052380000026",
        "project_name": "Elder Street Artist Lofts (1924 Jefferson Davis Hospital)",
        "location_source": "COH Protected Landmark & NRHP Listing (1101 Elder St)",
    }),
    (2006, "St. Thomas High School", {
        "address": "4500 MEMORIAL DR",
        "hcad_num": "0401290000003",
        "project_name": "St. Thomas High School Main Building",
        "location_source": "Historic Campus (4500 Memorial Dr)",
    }),
    (2006, "E.R. and Ann Taylor Park", {
        "address": "1829 PIERCE ST",
        "hcad_num": "0451880000261",
        "project_name": "E.R. and Ann Taylor Park (Taylor-Stevenson Ranch)",
        "lat": 29.6225,
        "lng": -95.3985,
        "location_source": "COH Protected Landmark (Taylor-Stevenson Ranch, 11787 Almeda Rd / 1829 Pierce St)",
    }),
    (2006, "Heart of the Park", {
        "address": "6001 FANNIN ST",
        "project_name": "Hermann Park — Heart of the Park",
        "lat": 29.7185,
        "lng": -95.3903,
        "location_source": "Hermann Park Conservancy (6001 Fannin St)",
    }),
    # 2005
    (2005, "22nd Street Lofts", {
        "address": "2201 LAWRENCE ST",
        "hcad_num": "0200490000001",
        "project_name": "22nd Street Lofts (Oriental Textile Mill)",
        "location_source": "COH Protected Landmark (Oriental Textile Mill, 2201 Lawrence St)",
    }),
    (2005, "Rosecroft", {
        "address": "4516 WALKER ST",
        "hcad_num": "0351560000004",
        "project_name": '"Rosecroft" Craftsman Residence (Eastwood)',
        "location_source": "Eastwood Historic Survey (4516 Walker St)",
    }),
    (2005, "Willow Street Pump Station", {
        "address": "811 N SAN JACINTO ST",
        "hcad_num": "0010010000020",
        "project_name": "Willow Street Pump Station (UHD)",
        "lat": 29.7669,
        "lng": -95.3571,
        "location_source": "COH Landmark (Willow Street Pump Station, 811 N San Jacinto St)",
    }),
    (2005, "Hotel Icon", {
        "address": "220 MAIN ST",
        "hcad_num": "0010200000001",
        "project_name": "Hotel Icon (1911 Union National Bank Building)",
        "historic_district": "Main Street Market Square",
        "location_source": "COH Protected Landmark (Union National Bank Building, 220 Main St)",
    }),
    (2005, "Summer Street Project", {
        "address": "1802 SUMMER ST",
        "hcad_num": "0051290000005",
        "project_name": "Avenue CDC Summer Street Historic Cottages",
        "historic_district": "High First Ward",
        "location_source": "HCAD Ownership Record (Avenue CDC, 1802-1918 Summer St)",
    }),
    (2005, "John P. McGovern Campus", {
        "address": "2450 HOLCOMBE BLVD",
        "hcad_num": "0410190020001",
        "project_name": "TMC John P. McGovern Campus (Former Nabisco Houston Bakery)",
        "lat": 29.7042,
        "lng": -95.3982,
        "location_source": "Texas Medical Center Landmark (2450 Holcombe Blvd)",
    }),
    (2005, "Menil House", {
        "address": "3363 SAN FELIPE ST",
        "hcad_num": "0601590580001",
        "project_name": "Dominique & John de Menil House (1951 Philip Johnson)",
        "location_source": "River Oaks Architectural Landmark (3363 San Felipe St)",
    }),
    # 2004
    (2004, "Humble Building", {
        "address": "1212 MAIN ST",
        "hcad_num": "1210850000002",
        "project_name": "The Humble Oil Building",
        "location_source": "COH Protected Landmark & NRHP Listing (1212 Main St)",
    }),
    (2004, "Trinity Episcopal Church", {
        "address": "1015 HOLMAN ST",
        "hcad_num": "0141530000001",
        "project_name": "Trinity Episcopal Church",
        "location_source": "Midtown Landmark (1015 Holman St)",
    }),
    (2004, "Villa Serena", {
        "address": "2700 ALBANY ST",
        "hcad_num": "0220310000001",
        "project_name": "Villa Serena (Former DePelchin Faith Home)",
        "lat": 29.7524,
        "lng": -95.3835,
        "location_source": "NRHP Landmark (DePelchin Faith Home, 2700 Albany St)",
    }),
    (2004, "The Magnolia", {
        "address": "1100 TEXAS AVE",
        "hcad_num": "0010700000006",
        "project_name": "Magnolia Hotel (1926 Houston Post-Dispatch Building)",
        "location_source": "COH Protected Landmark (Post-Dispatch Building, 609 Fannin St / 1100 Texas Ave)",
    }),
    (2004, "JPMorgan Chase Building", {
        "address": "712 MAIN ST",
        "hcad_num": "0010810000007",
        "project_name": "JPMorgan Chase Building (1929 Gulf Building)",
        "location_source": "COH Protected Landmark (Gulf Building, 712 Main St)",
    }),
    # 2003
    (2003, "Keck Hall", {
        "address": "6100 MAIN ST",
        "hcad_num": "0391880000001",
        "project_name": "Keck Hall (1925 Chemistry Building, Rice University)",
        "lat": 29.7191,
        "lng": -95.3999,
        "location_source": "Rice University Historic Core (Keck Hall)",
    }),
    # 2001
    (2001, "McGovern Lake", {
        "address": "6001 FANNIN ST",
        "project_name": "McGovern Lake in Hermann Park",
        "lat": 29.7175,
        "lng": -95.3895,
        "location_source": "Hermann Park Conservancy (McGovern Lake)",
    }),
    # 1999
    (1999, "1500 block of Michigan Street", {
        "address": "1500 MICHIGAN ST",
        "project_name": "1500 Block of Michigan Street Streetscape (Cherryhurst)",
        "lat": 29.7445,
        "lng": -95.3982,
        "location_source": "Cherryhurst Historic Neighborhood (1500 Michigan St)",
    }),
    # 1997
    (1997, "Project Row Houses", {
        "address": "2521 HOLMAN ST",
        "hcad_num": "0190970000001",
        "project_name": "Project Row Houses (Historic Third Ward Shotgun Houses)",
        "lat": 29.7361,
        "lng": -95.3642,
        "location_source": "Third Ward Cultural District (2521 Holman St)",
    }),
    (1997, "Winlow Westheimer District", {
        "address": "1915 WESTHEIMER RD",
        "project_name": "Winlow Westheimer District",
        "lat": 29.7429,
        "lng": -95.4065,
        "location_source": "Winlow Place / Westheimer Commercial District (1915 Westheimer Rd)",
    }),
    (1997, "Heights Fire Station", {
        "address": "107 W 12TH ST",
        "hcad_num": "0201880000001",
        "project_name": "Houston Heights Fire Station & Donovan Park Playground",
        "historic_district": "Houston Heights South",
        "location_source": "COH Landmark (Heights Fire Station, 107 W 12th St & Donovan Park)",
    }),
    # 1996
    (1996, "Jack Yates House", {
        "address": "1100 BAGBY ST",
        "hcad_num": "0400030000014",
        "project_name": "Rev. Jack Yates House (1870, Sam Houston Park)",
        "lat": 29.7600,
        "lng": -95.3712,
        "location_source": "The Heritage Society at Sam Houston Park (1100 Bagby St)",
    }),
    (1996, "Friedman Clock Tower", {
        "address": "301 MILAM ST",
        "hcad_num": "0010350000001",
        "project_name": "Louis & Anne Friedman Clock Tower at Market Square",
        "historic_district": "Main Street Market Square",
        "location_source": "Market Square Park (301 Milam St)",
    }),
    (1996, "700 block of Silver Street", {
        "address": "707 SILVER ST",
        "hcad_num": "0052010000001",
        "project_name": "700 Block of Silver Street Historic Buildings",
        "historic_district": "Old Sixth Ward",
        "location_source": "Old Sixth Ward Historic District (700 Silver St)",
    }),
    (1996, "Sam Houston Monument", {
        "address": "6001 FANNIN ST",
        "project_name": "Sam Houston Monument (1924, Hermann Park)",
        "lat": 29.7212,
        "lng": -95.3905,
        "location_source": "Hermann Park Entrance Circle (6001 Fannin St)",
    }),
    # 1994
    (1994, "Evergreen Negro Cemetery", {
        "address": "5000 LOCKWOOD DR",
        "project_name": "Evergreen Negro Cemetery (Fifth Ward)",
        "lat": 29.7995,
        "lng": -95.3175,
        "location_source": "Historic Fifth Ward Cemetery (Lockwood Dr & Liberty Rd)",
    }),
    (1994, "Lovett Hall", {
        "address": "6100 MAIN ST",
        "hcad_num": "0391880000001",
        "project_name": "Lovett Hall (1912 Administration Building, Rice University)",
        "lat": 29.7183,
        "lng": -95.3989,
        "location_source": "Rice University Academic Quadrangle (Lovett Hall)",
    }),
    (1994, "Randall Davis", {
        "address": "711 WILLIAM ST",
        "hcad_num": "0020160000001",
        "project_name": "Dakota Lofts (Bering Cortes Hardware Warehouse)",
        "lat": 29.7646,
        "lng": -95.3522,
        "location_source": "Downtown Warehouse District Adaptive Reuse (711 William St)",
    }),
    # 1993
    (1993, "U.S.S. Texas", {
        "address": "3523 INDEPENDENCE PKWY S",
        "hcad_num": "0410020010065",
        "project_name": "Battleship Texas (U.S.S. Texas, 1914)",
        "lat": 29.7559,
        "lng": -95.0898,
        "location_source": "San Jacinto Battleground State Historic Site",
    }),
    # 1992
    (1992, "Evergreen Cemetery", {
        "address": "3100 ALTIC ST",
        "project_name": "Historic Evergreen Cemetery (East End)",
        "lat": 29.7392,
        "lng": -95.3238,
        "location_source": "East End Historic Cemetery (3100 Altic St)",
    }),
    (1992, "Pillot Building", {
        "address": "1014 PRAIRIE ST",
        "hcad_num": "0010590000005",
        "project_name": "C.G. Pillot Building (Main Street/Market Square)",
        "historic_district": "Main Street Market Square",
        "location_source": "COH Landmark (Pillot Building, 1014 Prairie St)",
    }),
    # 1991
    (1991, "Constance Houston Thompson", {
        "address": "1303 BAYOU ST",
        "project_name": "The Houston Place (Joshua Houston Family Home, 1911)",
        "lat": 29.7720,
        "lng": -95.3375,
        "location_source": "Gregory School Archives (Joshua Houston Family Collection, 1303 Bayou St)",
    }),
    (1991, "Christ Church Cathedral", {
        "address": "1117 TEXAS AVE",
        "hcad_num": "0010620000001",
        "project_name": "Christ Church Cathedral",
        "location_source": "COH Landmark & NRHP Listing (1117 Texas Ave)",
    }),
]

# Fallback coordinates for explicit street addresses that use alternate corner addresses or were demolished/exempt
EXPLICIT_ADDRESS_COORDS_FALLBACK: dict[str, tuple[float, float, str]] = {
    "8325 TRAVELAIR ST": (29.6508, -95.2842, "0432070000001"),
    "3415 MAIN ST": (29.7393, -95.3775, "0141530000001"),  # Trinity Episcopal Church (1015 Holman / 3415 Main)
    "6621 MAIN ST": (29.7138, -95.3982, "0402280000001"),  # Palmer Memorial Episcopal Church
    "4912 MAIN ST": (29.7295, -95.3855, "0132220000001"),  # Lawndale Art Center
    "6510 LAWNDALE ST": (29.7224, -95.3055, "0410050000001"),  # Villa de Matel
    "6411 FANNIN ST": (29.7135, -95.3958, "0402290020001"),  # Hermann Hospital 1925 Building
    "3517 AUSTIN ST": (29.7380, -95.3732, "0141760000002"),  # Heinen Theater / Temple Beth Israel
    "202 TRAVIS ST": (29.7634, -95.3627, "0010210000007"),  # 1884 Old Cotton Exchange Building
    "2201 FANNIN ST": (29.7478, -95.3702, ""),  # Allen Paul House (2201 Fannin St)
    "2503 HOLMAN ST": (29.7362, -95.3645, "0190970000001"),  # Project Row Houses Core House
    "2521 HOLMAN ST": (29.7361, -95.3642, "0190970000001"),  # Project Row Houses
    "3617 TRAVIS ST": (29.7385, -95.3776, "0141840000001"),  # Holy Rosary Catholic Church
    "1150 BISSONNET ST": (29.7258, -95.3920, "0502260000015"),  # 1150 Bissonnet St
    "1805 LUBBOCK ST": (29.7667, -95.3765, "0052240000010"),  # 1805 Lubbock St (Old Sixth Ward)
    "5306 INSTITUTE LN": (29.7242, -95.3948, "0502260000018"),  # 5306 Institute Ln
    "1701 KIPLING ST": (29.7405, -95.4024, "0522230000001"),  # 1701 Kipling St
    "807 TAFT ST": (29.7575, -95.3842, "0220360000008"),  # 807 Taft St (Antone's Import Co.)
    "3401 ALLEN PKWY": (29.7595, -95.3952, "0401230000040"),  # Rein Co. Building (3401 Allen Pkwy)
    "1111 SAULNIER ST": (29.7548, -95.3792, "0021650000008"),  # 1111 Saulnier St (Freedmen's Town)
    "2619 N CALUMET DR": (29.7192, -95.3665, "0193050000012"),  # 2619 N Calumet Dr (Riverside Terrace)
    "900 W GRAY ST": (29.75340, -95.38950, "0400860000001"),  # Clarence R. Wharton Dual Language Academy (1929)
    "1417 CONGRESS AVE": (29.759886, -95.356475, "0010250000013"),  # Palace Hotel (216 La Branch / 1417 Congress)
    "3843 N BRAESWOOD BLVD": (29.691293, -95.438834, "0901520000001"),  # 3843 N Braeswood Blvd (Built 1968)
    "2940 LAZY LN": (29.758050, -95.420925, "0601510000005"),  # 2940 Lazy Lane Blvd (River Oaks)
    "1003 ISABELLA AVE": (29.73475, -95.37920, "0141450000001"),  # 1003 Isabella St (Midtown)
    "430 LAMAR AVE": (29.75955, -95.37025, "0011000000001"),  # Federal Land Bank Building (430 Lamar St)
}


def parse_award_type(raw_text: str) -> tuple[str, str]:
    prefixes = [
        "Martha Peterson Award:",
        "President’s Award:",
        "President's Award:",
        "President's Awards:",
        "Pier & Beam Future Landmark Award:",
        "Legacy Business Award:",
        "Stewart Title Award:",
        "H-E-B Award:",
        "North Houston Bank Preservation Partner in Print Award:",
        "North Houston Bank Award:",
        "Community Pillar Award:",
        "Community Pillars:",
        "Preservation Program Award:",
        "Preservation Partner in Print Award:",
        "Preservation Leadership Award in Honor of Jesse H. Jones:",
        "Outstanding Leadership in Historic Preservation:",
    ]
    for p in prefixes:
        if raw_text.startswith(p):
            award_type = p.rstrip(":").replace("’", "'")
            if award_type == "President's Awards":
                award_type = "President's Award"
            if award_type == "Community Pillars":
                award_type = "Community Pillar Award"
            return award_type, raw_text[len(p) :].strip()
    return "Good Brick Award", raw_text.strip()


def is_non_place_entry(raw_text: str) -> bool:
    for pat in NON_PLACE_PATTERNS:
        if re.search(pat, raw_text, re.I):
            return True
    return False


def normalize_street_address(addr: str) -> str:
    s = addr.upper().strip()
    s = s.replace("MIAN ST", "MAIN ST")
    s = re.sub(r"[.,]", "", s)
    s = re.sub(r"^(\d+)\s*-\s*\d+\s+", r"\1 ", s)
    replacements = [
        (r"\bSTREET\b", "ST"),
        (r"\bAVENUE\b", "AVE"),
        (r"\bBOULEVARD\b", "BLVD"),
        (r"\bDRIVE\b", "DR"),
        (r"\bROAD\b", "RD"),
        (r"\bLANE\b", "LN"),
        (r"\bCOURT\b", "CT"),
        (r"\bPLACE\b", "PL"),
        (r"\bPARKWAY\b", "PKWY"),
        (r"\bFREEWAY\b", "FWY"),
        (r"\bHIGHWAY\b", "HWY"),
        (r"\bCIRCLE\b", "CIR"),
    ]
    tokens = s.split()
    if len(tokens) >= 3:
        num = tokens[0]
        middle = tokens[1:-1]
        suffix = tokens[-1]
        for pat, rep in replacements:
            suffix = re.sub(pat, rep, suffix)
        if len(middle) >= 2 and middle[0] in {"NORTH", "SOUTH", "EAST", "WEST"}:
            middle[0] = middle[0][0]
        s = " ".join([num] + middle + [suffix])
    elif len(tokens) == 2 and tokens[0].isdigit():
        known_suffixes = {
            "WILLOWEND": "WILLOWEND DR",
            "SAWYER": "SAWYER ST",
            "WESTMINSTER": "WESTMINSTER DR",
            "NANCE": "NANCE ST",
        }
        if tokens[1] in known_suffixes:
            s = f"{tokens[0]} {known_suffixes[tokens[1]]}"
    return re.sub(r"\s+", " ", s).strip()


def address_core_key(norm_addr: str) -> str:
    """Return street number + street name without trailing ST/AVE/DR/BLVD suffix so AVE vs ST matches."""
    if not norm_addr:
        return ""
    return re.sub(r"\s+(?:ST|AVE|BLVD|DR|RD|LN|CT|PL|PKWY|WAY|CIR|FWY|HWY)$", "", norm_addr).strip()


def parse_citation_metadata(body_text: str) -> dict[str, Any]:
    recipient = ""
    reason = body_text

    if " for " in body_text:
        parts = body_text.split(" for ", 1)
        recipient = parts[0].strip().rstrip(",")
        reason = "For " + parts[1].strip()
    elif "'s " in body_text and re.search(r"'s\s+(?:renovation|restoration|rehabilitation|stewardship|redevelopment)", body_text):
        m = re.search(r"^(.+?)'s\s+((?:renovation|restoration|rehabilitation|stewardship|redevelopment).*)$", body_text)
        if m:
            recipient = m.group(1).strip()
            reason = "For the " + m.group(2).strip()
    elif ", the " in body_text:
        parts = body_text.split(", the ", 1)
        recipient = parts[0].strip()
        reason = "The " + parts[1].strip()
    else:
        recipient = body_text

    m_yr = re.search(r"\((?:c\.?\s*|ca\.?\s*)?(18\d\d|19\d\d|20[012]\d)\b", body_text)
    if not m_yr:
        m_yr = re.search(r"\b(?:c\.?\s*|ca\.?\s*)?(18\d\d|19\d\d)\s+[A-Z]", body_text)
    bld_year = int(m_yr.group(1)) if m_yr else None

    architect_or_style = ""
    m_arch = re.search(r"\((?:c\.?\s*|ca\.?\s*)?(?:18\d\d|19\d\d|20\d\d),\s*([^\)]+)\)", body_text)
    if m_arch:
        architect_or_style = m_arch.group(1).strip()
    else:
        m_style = re.search(
            r"\b(Queen Anne|Folk Victorian|Craftsman|Tudor Revival|Colonial Revival|Mediterranean Revival|Georgian Revival|Gothic Revival|Art Deco|Mid-Century Modern|mid-century modern|Victorian|bungalow|shotgun)\b",
            body_text,
        )
        if m_style:
            architect_or_style = m_style.group(1)

    project_name = ""
    m_proj = re.search(
        r"\b(?:the\s+)(?:historic\s+|former\s+|landmark\s+)*([A-Z][A-Za-z0-9\.\'\-& ]{2,48}?\s+(?:House|Mansion|Building|Cottage|Farmhouse|Hall|Chapel|Theatre|Theater|Ballroom|School|Hospital|Warehouse|Armory|Showroom|Silos|Cistern|Pavilion|Cabin|Station|Lofts|Hotel|Cathedral|Church|Synagogue|Library|Museum|Courthouse|Park|Cemetery|Gardens|Stadium))",
        body_text,
    )
    if m_proj:
        project_name = m_proj.group(1).strip()

    return {
        "recipient": recipient,
        "reason": reason,
        "project_name": project_name,
        "building_year_built": bld_year,
        "architect_or_style": architect_or_style,
    }


def main() -> None:
    if SCRATCH_RAW_PATH.exists() and not RAW_AWARDS_CACHE_PATH.exists():
        shutil.copy2(SCRATCH_RAW_PATH, RAW_AWARDS_CACHE_PATH)

    raw_entries = json.loads(RAW_AWARDS_CACHE_PATH.read_text(encoding="utf-8"))

    print("Loading buildings.geojson...")
    core_fc = orjson.loads((DATA_DIR / "buildings.geojson").read_bytes())
    addr_to_blds: dict[str, list[dict[str, Any]]] = defaultdict(list)
    core_to_blds: dict[str, list[dict[str, Any]]] = defaultdict(list)
    hcad_to_blds: dict[str, list[dict[str, Any]]] = defaultdict(list)

    def get_feature_centroid(f: dict[str, Any]) -> tuple[float, float]:
        g = f.get("geometry") or {}
        gt = g.get("type")
        coords = g.get("coordinates") or []
        if gt == "Point" and len(coords) >= 2:
            return float(coords[0]), float(coords[1])
        ring = coords[0] if gt == "Polygon" and coords else (coords[0][0] if gt == "MultiPolygon" and coords and coords[0] else [])
        if ring:
            n = max(1, len(ring) - 1)
            cx = sum(pt[0] for pt in ring[:n]) / n
            cy = sum(pt[1] for pt in ring[:n]) / n
            return cx, cy
        return -95.3698, 29.7604

    for f in core_fc.get("features", []):
        p = f.get("properties") or {}
        addr = normalize_street_address(str(p.get("address") or ""))
        hcad = str(p.get("hcad_num") or "").strip()
        if addr:
            addr_to_blds[addr].append(f)
            core_to_blds[address_core_key(addr)].append(f)
        if hcad:
            hcad_to_blds[hcad].append(f)

    print("Loading coh_landmarks.json...")
    lm_list = json.loads((CACHE_DIR / "coh_landmarks.json").read_text(encoding="utf-8"))
    addr_to_lm: dict[str, dict[str, Any]] = {}
    core_to_lm: dict[str, dict[str, Any]] = {}
    for lm in lm_list:
        p = lm.get("properties") or {}
        laddr = normalize_street_address(str(p.get("USER_SITE_ADDRESS") or ""))
        if laddr:
            addr_to_lm[laddr] = lm
            core_to_lm[address_core_key(laddr)] = lm

    street_regex = re.compile(
        r"\b(\d{1,5}(?:-\d{1,5})?\s+(?:[NSEW]\.?\s+)?(?:[A-Z0-9][a-zA-Z0-9\.\'\-]+\s+){0,2}"
        r"(?:Street|St\.?|Avenue|Ave\.?|Boulevard|Blvd\.?|Drive|Dr\.?|Road|Rd\.?|Lane|Ln\.?|Court|Ct\.?|Place|Pl\.?|Way|Circle|Cir\.?|Parkway|Pkwy\.?|Freeway|Fwy\.?|Highway|Hwy\.?|Willowend|Sawyer|Westminster|Nance))\b"
    )

    place_awards: list[dict[str, Any]] = []
    excluded_non_place: list[dict[str, Any]] = []

    for item in raw_entries:
        raw_text = item["raw_text"].strip()
        if (
            re.match(r"^\d{4}\s+to\s+\d{4}$", raw_text)
            or raw_text.startswith("No Good Brick")
            or raw_text.startswith("The Good Brick Awards were first")
        ):
            continue

        award_year = int(item["award_year"])
        award_type, body_text = parse_award_type(raw_text)

        if is_non_place_entry(raw_text):
            excluded_non_place.append({
                "award_year": award_year,
                "award_type": award_type,
                "raw_text": raw_text,
            })
            continue

        meta = parse_citation_metadata(body_text)

        curated_hit = None
        for cyr, csub, cinfo in CURATED_AWARD_LOCATIONS:
            if cyr == award_year and csub.lower() in raw_text.lower():
                curated_hit = cinfo
                break

        raw_addr = ""
        norm_addr = ""
        hcad_num = ""
        project_name = meta["project_name"]
        hist_dist = ""
        lat = None
        lng = None
        loc_source = ""

        if curated_hit:
            raw_addr = curated_hit["address"]
            norm_addr = normalize_street_address(raw_addr)
            hcad_num = curated_hit.get("hcad_num", "")
            if curated_hit.get("project_name"):
                project_name = curated_hit["project_name"]
            hist_dist = curated_hit.get("historic_district", "")
            lat = curated_hit.get("lat")
            lng = curated_hit.get("lng")
            loc_source = curated_hit.get("location_source", "Curated Preservation Houston Research")
        else:
            m_str = street_regex.search(raw_text)
            if m_str and not re.match(r"^(18\d\d|19\d\d|20\d\d)\s+(?:Street\s+Railways|Harris)", m_str.group(1)):
                raw_addr = m_str.group(1)
                norm_addr = normalize_street_address(raw_addr)
                loc_source = "Preservation Houston Award Citation"
            else:
                for lm in lm_list:
                    lp = lm.get("properties") or {}
                    sname = re.sub(r"\s*\(.*?\)", "", str(lp.get("USER_SITE_NAME") or "")).strip()
                    if len(sname) >= 6 and sname.lower() in raw_text.lower():
                        raw_addr = str(lp.get("USER_SITE_ADDRESS") or "")
                        norm_addr = normalize_street_address(raw_addr)
                        hcad_num = str(lp.get("USER_HCAD_NUM") or "").strip()
                        if hcad_num in {"None", "On hold", "Demolished"}:
                            hcad_num = ""
                        if not project_name:
                            project_name = sname
                        loc_source = f"COH Landmark ({sname})"
                        break

        if not project_name:
            project_name = norm_addr.title() if norm_addr else meta["recipient"]

        place_awards.append({
            "award_year": award_year,
            "award_type": award_type,
            "recipient": meta["recipient"],
            "project_name": project_name,
            "reason": meta["reason"],
            "building_year_built": meta["building_year_built"],
            "architect_or_style": meta["architect_or_style"],
            "address": norm_addr,
            "hcad_num": hcad_num,
            "historic_district": hist_dist,
            "lat": lat,
            "lng": lng,
            "location_source": loc_source,
            "raw_citation": raw_text,
        })

    print(f"Parsed {len(place_awards)} place-based Good Brick Awards (excluded {len(excluded_non_place)} non-place entries).")

    # Fast byte-filtered scan of countywide aligned_ndjson/*.geojsonseq for any address core or HCAD outside core
    needed_cores = {
        address_core_key(a["address"])
        for a in place_awards
        if a["address"] and address_core_key(a["address"]) not in core_to_blds
    }
    needed_hcads = {a["hcad_num"] for a in place_awards if a["hcad_num"] and a["hcad_num"] not in hcad_to_blds}
    print(f"Scanning countywide shards for {len(needed_cores)} address cores and {len(needed_hcads)} HCAD IDs outside core...")

    seq_dir = CACHE_DIR / "aligned_ndjson"
    if seq_dir.exists() and (needed_cores or needed_hcads):
        # Build fast regex of street numbers or HCADs to pre-filter raw bytes before JSON parsing
        tokens = set()
        for c in needed_cores:
            if c:
                tokens.add(c.encode("utf-8"))
        for h in needed_hcads:
            if h:
                tokens.add(h.encode("utf-8"))
        fast_re = re.compile(b"(" + b"|".join(re.escape(t) for t in sorted(tokens)) + b")")
        for seq_path in sorted(seq_dir.glob("*.geojsonseq")):
            for raw_line in seq_path.read_bytes().splitlines():
                if not fast_re.search(raw_line):
                    continue
                if raw_line.startswith(b"\x1e"):
                    raw_line = raw_line[1:]
                f = orjson.loads(raw_line)
                p = f.get("properties") or {}
                hcad = str(p.get("hcad_num") or "").strip()
                addr = normalize_street_address(str(p.get("address") or ""))
                ckey = address_core_key(addr)
                if hcad in needed_hcads:
                    hcad_to_blds[hcad].append(f)
                if ckey in needed_cores:
                    addr_to_blds[addr].append(f)
                    core_to_blds[ckey].append(f)

    # Geocode and enrich every place award
    unresolved_coords = []
    for a in place_awards:
        addr = a["address"]
        ckey = address_core_key(addr)
        hcad = a["hcad_num"]

        matched_blds = []
        if hcad and hcad in hcad_to_blds:
            matched_blds = hcad_to_blds[hcad]
        elif addr and addr in addr_to_blds:
            matched_blds = addr_to_blds[addr]
        elif ckey and ckey in core_to_blds:
            matched_blds = core_to_blds[ckey]

        if matched_blds:
            best_f = sorted(
                matched_blds,
                key=lambda f: (
                    0 if int((f.get("properties") or {}).get("year_built") or 0) >= 1836 else 1,
                    int((f.get("properties") or {}).get("year_built") or 9999),
                    -float((f.get("properties") or {}).get("footprint_area_sqft") or 0),
                ),
            )[0]
            bp = best_f.get("properties") or {}
            if not a["hcad_num"] and bp.get("hcad_num"):
                a["hcad_num"] = str(bp["hcad_num"])
            if not a["historic_district"] and bp.get("historic_district") and bp.get("historic_district") != "None":
                a["historic_district"] = str(bp["historic_district"])
            if not a["building_year_built"] and int(bp.get("year_built") or 0) >= 1836:
                a["building_year_built"] = int(bp["year_built"])
            if a["lat"] is None or a["lng"] is None:
                cx, cy = get_feature_centroid(best_f)
                a["lng"] = round(cx, 6)
                a["lat"] = round(cy, 6)

        if (a["lat"] is None or a["lng"] is None) and (addr in addr_to_lm or ckey in core_to_lm):
            lm = addr_to_lm.get(addr) or core_to_lm.get(ckey)
            if lm:
                lp = lm.get("properties") or {}
                cx, cy = get_feature_centroid(lm)
                a["lng"] = round(cx, 6)
                a["lat"] = round(cy, 6)
                if not a["hcad_num"] and lp.get("USER_HCAD_NUM") not in {None, "None", "On hold", "Demolished"}:
                    a["hcad_num"] = str(lp["USER_HCAD_NUM"]).strip()
                if not a["building_year_built"] and int(lp.get("USER_YR_BUILT") or 0) >= 1836:
                    a["building_year_built"] = int(lp["USER_YR_BUILT"])

        if (a["lat"] is None or a["lng"] is None) and addr in EXPLICIT_ADDRESS_COORDS_FALLBACK:
            flat, flng, fhcad = EXPLICIT_ADDRESS_COORDS_FALLBACK[addr]
            a["lat"] = flat
            a["lng"] = flng
            if not a["hcad_num"] and fhcad:
                a["hcad_num"] = fhcad

        if a["lat"] is None or a["lng"] is None:
            unresolved_coords.append(a)

    print(f"Geocoded {len(place_awards) - len(unresolved_coords)}/{len(place_awards)} place awards.")
    for u in unresolved_coords:
        print(f"  [UNRESOLVED] ({u['award_year']}) {u['address']} | {u['project_name']}")

    # Sort newest-first
    place_awards.sort(key=lambda x: (-x["award_year"], x["address"] or x["project_name"]))

    # Save resolved JSON and CSV
    RESOLVED_JSON_PATH.write_bytes(orjson.dumps(place_awards, option=orjson.OPT_INDENT_2))
    csv_cols = [
        "award_year",
        "award_type",
        "recipient",
        "project_name",
        "reason",
        "building_year_built",
        "architect_or_style",
        "address",
        "hcad_num",
        "historic_district",
        "lat",
        "lng",
        "location_source",
        "raw_citation",
    ]
    with open(RESOLVED_CSV_PATH, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=csv_cols)
        writer.writeheader()
        for row in place_awards:
            writer.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in csv_cols})
    print(f"Saved {len(place_awards)} resolved awards to {RESOLVED_CSV_PATH.name}")

    # Group awards by building/site key (preferring hcad_num + exact coordinates for distinct campus buildings, else normalized address)
    site_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for a in place_awards:
        if a["lat"] is None or a["lng"] is None:
            continue
        # For large multi-building campuses (Rice 0391880000001, Hermann Park 0402290010001, Sam Houston Park 0400030000014),
        # group by rounded coordinate + project_name so Lovett Hall, Keck Hall, Maxfield Hall, and Brochstein Pavilion stay distinct!
        if a["hcad_num"] in {"0391880000001", "0402290010001", "0400030000014"} or not a["hcad_num"]:
            skey = f"{a['address']}|{round(a['lat'], 4)},{round(a['lng'], 4)}"
        else:
            skey = f"HCAD:{a['hcad_num']}"
        site_groups[skey].append(a)

    # Build GeoJSON FeatureCollection for overlays.json -> good_brick_awards
    gb_features: list[dict[str, Any]] = []
    hcad_to_awards: dict[str, list[dict[str, Any]]] = defaultdict(list)
    core_addr_to_awards: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for skey, alist in site_groups.items():
        alist_sorted = sorted(alist, key=lambda x: -x["award_year"])
        latest = alist_sorted[0]
        years_asc = sorted({x["award_year"] for x in alist_sorted})
        years_str = ", ".join(str(y) for y in years_asc)

        compact_awards = [
            {
                "award_year": x["award_year"],
                "award_type": x["award_type"],
                "recipient": x["recipient"],
                "project_name": x["project_name"],
                "reason": x["reason"],
                "building_year_built": x["building_year_built"],
                "architect_or_style": x["architect_or_style"],
                "location_source": x["location_source"],
                "raw_citation": x["raw_citation"],
            }
            for x in alist_sorted
        ]

        summary_badge = (
            f"Good Brick Award ({years_str})"
            if len(years_asc) > 1
            else f"{latest['award_year']} {latest['award_type']}"
        )

        hcad = latest["hcad_num"]
        addr = latest["address"]
        if hcad and hcad not in {"0391880000001", "0402290010001", "0400030000014"}:
            hcad_to_awards[hcad].extend(compact_awards)
        if addr and addr not in {"6100 MAIN ST", "6001 FANNIN ST", "1100 BAGBY ST"}:
            core_addr_to_awards[address_core_key(addr)].extend(compact_awards)

        feat_props: dict[str, Any] = {
            "id": f"good_brick_{skey}",
            "is_good_brick": True,
            "name": latest["project_name"] or addr,
            "landmark_name": latest["project_name"] or addr,
            "address": addr,
            "hcad_num": hcad,
            "historic_district": latest["historic_district"] or "",
            "year_built": latest["building_year_built"] or 0,
            "architect": latest["architect_or_style"] or "",
            "good_brick_summary": summary_badge,
            "good_brick_years": years_str,
            "good_brick_latest_year": latest["award_year"],
            "good_brick_count": len(compact_awards),
            "good_brick_recipient": latest["recipient"],
            "good_brick_reason": latest["reason"],
            "good_brick_award_type": latest["award_type"],
            "good_brick_awards": compact_awards,
        }
        gb_features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [latest["lng"], latest["lat"]],
            },
            "properties": feat_props,
        })

    print(f"Built {len(gb_features)} unique Good Brick Award site features for overlays.json.")

    # Update overlays.json
    overlays_path = DATA_DIR / "overlays.json"
    overlays_data = orjson.loads(overlays_path.read_bytes())
    overlays_data["good_brick_awards"] = {
        "type": "FeatureCollection",
        "features": gb_features,
    }
    overlays_path.write_bytes(orjson.dumps(overlays_data))
    print("Updated app/public/data/overlays.json with good_brick_awards FeatureCollection.")

    # Enrich buildings.geojson with good_brick_awards metadata
    enriched_bld_count = 0
    for f in core_fc.get("features", []):
        p = f.get("properties") or {}
        hcad = str(p.get("hcad_num") or "").strip()
        ckey = address_core_key(normalize_street_address(str(p.get("address") or "")))
        matched_aw = hcad_to_awards.get(hcad) or core_addr_to_awards.get(ckey)
        if matched_aw:
            # Deduplicate by (award_year, raw_citation)
            seen = set()
            dedup_aw = []
            for aw in matched_aw:
                k = (aw["award_year"], aw["raw_citation"])
                if k not in seen:
                    seen.add(k)
                    dedup_aw.append(aw)
            dedup_aw.sort(key=lambda x: -x["award_year"])
            yrs = sorted({x["award_year"] for x in dedup_aw})
            yrs_str = ", ".join(str(y) for y in yrs)
            p["good_brick_awards"] = dedup_aw
            p["good_brick_summary"] = (
                f"Good Brick Award ({yrs_str})"
                if len(yrs) > 1
                else f"{dedup_aw[0]['award_year']} {dedup_aw[0]['award_type']}"
            )
            if not p.get("landmark_name") and dedup_aw[0].get("project_name"):
                p["landmark_name"] = dedup_aw[0]["project_name"]
            if int(p.get("year_built") or 0) < 1836 and dedup_aw[0].get("building_year_built"):
                p["year_built"] = int(dedup_aw[0]["building_year_built"])
            if not p.get("architect") and dedup_aw[0].get("architect_or_style"):
                p["architect"] = dedup_aw[0]["architect_or_style"]
            enriched_bld_count += 1

    (DATA_DIR / "buildings.geojson").write_bytes(orjson.dumps(core_fc))
    print(f"Enriched {enriched_bld_count} building footprints in buildings.geojson with Good Brick Award metadata.")

    # Also enrich curated_overrides.json so countywide PMTiles buildings clicked anywhere in Harris County
    # immediately show their Good Brick Award metadata via applyOverrideToProperties!
    overrides_path = DATA_DIR / "curated_overrides.json"
    overrides_doc = orjson.loads(overrides_path.read_bytes())
    overrides_map = overrides_doc.get("overrides") or {}
    added_ov = 0
    for skey, alist in site_groups.items():
        alist_sorted = sorted(alist, key=lambda x: -x["award_year"])
        latest = alist_sorted[0]
        hcad = latest["hcad_num"]
        if not hcad or hcad in {"0391880000001", "0402290010001", "0400030000014"}:
            continue
        compact_awards = [
            {
                "award_year": x["award_year"],
                "award_type": x["award_type"],
                "recipient": x["recipient"],
                "project_name": x["project_name"],
                "reason": x["reason"],
                "building_year_built": x["building_year_built"],
                "architect_or_style": x["architect_or_style"],
                "location_source": x["location_source"],
                "raw_citation": x["raw_citation"],
            }
            for x in alist_sorted
        ]
        yrs = sorted({x["award_year"] for x in compact_awards})
        yrs_str = ", ".join(str(y) for y in yrs)
        summary_badge = (
            f"Good Brick Award ({yrs_str})"
            if len(yrs) > 1
            else f"{latest['award_year']} {latest['award_type']}"
        )
        if hcad in overrides_map:
            overrides_map[hcad]["good_brick_awards"] = compact_awards
            overrides_map[hcad]["good_brick_summary"] = summary_badge
            if not overrides_map[hcad].get("landmark_name") and latest["project_name"]:
                overrides_map[hcad]["landmark_name"] = latest["project_name"]
        else:
            # Find building year from HCAD or citation
            byr = latest["building_year_built"] or 0
            if not byr and hcad in hcad_to_blds and hcad_to_blds[hcad]:
                byr = int((hcad_to_blds[hcad][0].get("properties") or {}).get("year_built") or 0)
            overrides_map[hcad] = {
                "hcad_num": hcad,
                "address": latest["address"],
                "landmark_name": latest["project_name"],
                "historic_district": latest["historic_district"] or "",
                "year_built": byr if byr >= 1836 else 0,
                "architect": latest["architect_or_style"] or "",
                "good_brick_awards": compact_awards,
                "good_brick_summary": summary_badge,
                "source_type": "Preservation Houston Good Brick Award",
                "source_citation": latest["raw_citation"],
                "source_url": "https://www.preservationhouston.org/awards/past",
                "verified_by": "Preservation Houston Good Brick Awards",
            }
            added_ov += 1

    overrides_doc["overrides"] = overrides_map
    overrides_path.write_bytes(orjson.dumps(overrides_doc, option=orjson.OPT_INDENT_2))
    print(f"Updated curated_overrides.json ({added_ov} new Good Brick HCAD entries added, {len(overrides_map)} total).")

    # Update search_index.json so Good Brick Award winners are searchable by project name, recipient, address, or "Good Brick"
    search_path = DATA_DIR / "search_index.json"
    search_items: list[dict[str, Any]] = orjson.loads(search_path.read_bytes())
    # Remove any previous good_brick entries to stay idempotent
    search_items = [item for item in search_items if item.get("type") != "good_brick"]
    for feat in gb_features:
        fp = feat["properties"]
        coords = feat["geometry"]["coordinates"]
        label = fp["landmark_name"] or fp["address"]
        if fp["address"] and fp["address"].lower() not in label.lower():
            label = f"{label} ({fp['address']})"
        sublabel = f"{fp['good_brick_summary']} • {fp['good_brick_recipient']}"
        search_items.append({
            "type": "good_brick",
            "id": fp["id"],
            "hcad_num": fp["hcad_num"],
            "label": label,
            "sublabel": sublabel,
            "category": f"Good Brick ({fp['good_brick_years']})",
            "lon": coords[0],
            "lat": coords[1],
            "zoom": 17.5,
        })
    search_path.write_bytes(orjson.dumps(search_items))
    print(f"Updated search_index.json with {len(gb_features)} Good Brick Award search entries.")

    # Update stats_summary.json
    stats_path = DATA_DIR / "stats_summary.json"
    if stats_path.exists():
        stats_data = orjson.loads(stats_path.read_bytes())
        stats_data["total_good_brick_awards"] = len(place_awards)
        stats_data["total_good_brick_sites"] = len(gb_features)
        stats_path.write_bytes(orjson.dumps(stats_data, option=orjson.OPT_INDENT_2))


if __name__ == "__main__":
    main()
