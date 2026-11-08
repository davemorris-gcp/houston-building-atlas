"""Harvest TARO 2.0 (txarchives.org) EAD XML architectural finding aids and HPL ContentDM architectural collections.

Milestone 10 Option 2+ pipeline:
1. Discovers and caches Houston / Harris County architectural and built-environment EAD XML finding
   aids from Texas Archival Resources Online (`https://txarchives.org/api/finding_aid/search/` ->
   raw XML at `https://txarchives.org/admin/<repo>/<filename>`).
2. Discovers and caches all digitized items across the 6 previously untapped Houston Public Library
   ContentDM (`https://cdm17006.contentdm.oclc.org`) architectural collections:
   - `p17006coll199`: MSS 0178 Howard Barnstone Papers
   - `p17006coll175`: MSS 0468 Patrick Lopez Architectural Renderings
   - `p17006coll238`: Architectural Vertical Files
   - `p17006coll220`: RG D 0038 Houston Architectural Survey Photographs
   - `p17006coll41` : MSS 0097 Yolita Schmidt Moderne Style Architecture in Houston Photographs
   - `p17006coll231`: MSS 0086 Lloyd, Morgan, and Jones Architectural Drawings
3. Matches archival folders, drawings, renderings, and survey photographs to Harris County parcels,
   campus buildings, and *Lost Houston* sites via a strict two-tier engine:
   - Tier 1A: Normalized street address (`aligned_ndjson/buildings_*.geojsonseq`, `curated_overrides.json`,
     `buildings.geojson`, `overlays.json`) with chronological replacement-structure protection (DF-6).
   - Tier 1B: Distinctive named Houston landmark / campus building matching.
   - Tier 2 : Unambiguous rare client full-name / surname + era/neighborhood corroboration (excluding
     all `TX Tax Code Sec. 25.025` confidential HCAD accounts).
4. Writes `app/public/data/archival_index.json`, enriches `app/public/data/building_photos.json` with
   verified HPL architectural photographs/renderings, and backfills missing `architect` credits on
   `curated_overrides.json`, `buildings.geojson`, and `search_index.json` within verified career windows.
"""

from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import datetime
import glob
import json
from pathlib import Path
import re
import time
from typing import Any
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from atlas_pipeline.audit_historical_accuracy import (
    ARCHITECT_CAREER_WINDOWS,
    is_architect_within_career_window,
)
from atlas_pipeline.schema import (
    iter_json_or_gzip_shard_keys,
    normalize_standing_hcad_token,
    read_json_or_gzip_path,
    sanitize_atlas_text,
)

USER_AGENT = "PreservationHouston-Atlas-Research/1.0"
TARO_SEARCH_URL = "https://txarchives.org/api/finding_aid/search/?text=Houston"
CDM_BASE_URL = "https://cdm17006.contentdm.oclc.org"

HPL_ARCH_COLLECTIONS: tuple[tuple[str, str, str, bool], ...] = (
    # (collection_alias, display_title, default_architect_if_monograph, iiif_supported)
    # Note: p17006coll199 (Howard Barnstone Papers) mixes Barnstone's own post-1948 designs with his
    # historical architectural survey photographs of 19th/early-20th-century Texas landmarks, so
    # default_architect_if_monograph is left blank unless explicitly credited in item metadata.
    ("p17006coll220", "HPL Houston Architectural Survey Photographs (RG D 0038)", "", False),
    ("p17006coll41", "HPL Yolita Schmidt Moderne Style Architecture Photographs (MSS 0097)", "", True),
    ("p17006coll175", "HPL Patrick Lopez Architectural Renderings (MSS 0468)", "", True),
    ("p17006coll199", "HPL Howard Barnstone Architectural Papers (MSS 0178)", "", True),
    ("p17006coll238", "HPL Architectural Vertical Files", "", False),
    ("p17006coll231", "HPL Lloyd, Morgan & Jones Architectural Drawings (MSS 0086)", "Lloyd, Morgan & Jones", True),
)

# Upper-bound survey completion years for historical HPL photo collections when individual items lack caption dates.
HPL_COLLECTION_MAX_SURVEY_YEAR: dict[str, int] = {
    "hpl:p17006coll220": 1981,
    "hpl:p17006coll41": 1984,
    "hpl:p17006coll238": 1990,
}

# Periodical, article, clipping, and reference-material folders inside an architect's papers must not
# inherit the collection creator as the designing architect of buildings mentioned in articles.
PERIODICAL_OR_CLIPPING_FOLDER_RE = re.compile(
    r"\b(?:"
    r"article\s+on|articles?\s+about|magazine|journal|periodicals?|clippings?|newspaper|"
    r"architectural\s+design,\s*vol|architectural\s+record|progressive\s+architecture|"
    r"texas\s+architect|printed\s+materials?|reference\s+files?|publications?"
    r")\b",
    re.IGNORECASE,
)

REPO_SHORT_LABELS: dict[str, str] = {
    "houpub": "HMRC / Houston Public Library",
    "ricewrc": "Rice Woodson Research Center",
    "uhsc": "UH Special Collections",
    "utaaa": "UT Austin Alexander Architectural Archive",
    "mfah": "MFAH Archives",
    "hcarch": "Harris County Archives",
    "tsusc": "TSU Special Collections",
    "utcah": "UT Briscoe Center",
    "hamtmc": "McGovern Historical Center (TMC Library)",
    "tslac": "Texas State Library and Archives Commission",
    "tyrrell": "Tyrrell Historical Library",
    "utlac": "UT Benson Latin American Collection",
}

KNOWN_COLLECTION_ARCHITECTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bAlfred\s+C\.?\s+Finn\b", re.I), "Alfred C. Finn"),
    (re.compile(r"\bWilliam\s+T\.?\s+Cannady\b", re.I), "William T. Cannady"),
    (re.compile(r"\bDonald\s+Barthelme\b", re.I), "Donald Barthelme, Sr."),
    (re.compile(r"\bMacKie\s*(?:&|and)\s*Kamrath\b|\bKarl\s+Kamrath\b", re.I), "MacKie & Kamrath"),
    (re.compile(r"\bCharles\s+Tapley\b", re.I), "Charles Tapley"),
    (re.compile(r"\bLloyd\s*(?:,\s*Morgan\s*(?:&|and)\s*Jones|(?:&|and)\s*Morgan)\b|\bArthur\s+E\.?\s+Jones\b", re.I), "Lloyd & Morgan"),
    (re.compile(r"\bS\.?\s*I\.?\s*Morris\b|\bSeth\s+Irvin\s+Morris\b", re.I), "S. I. Morris"),
    (re.compile(r"\bWilson,\s*Morris,\s*Crain\s*(?:&|and)\s*Anderson\b", re.I), "Wilson, Morris, Crain & Anderson"),
    (re.compile(r"\bRalph\s+A\.?\s+Anderson\b", re.I), "Ralph A. Anderson, Jr."),
    (re.compile(r"\bWilliam\s+Ward\s+Watkin\b", re.I), "William Ward Watkin"),
    (re.compile(r"\bBurdette\s+Keeland\b", re.I), "Burdette Keeland"),
    (re.compile(r"\bStayton\s+Nunn\b", re.I), "Stayton Nunn"),
    (re.compile(r"\bGerald\s+Moorhead\b", re.I), "Gerald Moorhead"),
    (re.compile(r"\bAnderson\s+Todd\b", re.I), "Anderson Todd"),
    (re.compile(r"\bO\.?\s*Jack\s+Mitchell\b", re.I), "O. Jack Mitchell"),
    (re.compile(r"\bHarrie\s+T\.?\s+Lindeberg\b", re.I), "Harrie T. Lindeberg"),
    (re.compile(r"\bOlmsted\s+Brothers\b", re.I), "Olmsted Brothers"),
    (re.compile(r"\bSally\s+Walsh\b", re.I), "Sally Walsh"),
    (re.compile(r"\bBirdsall\s+P\.?\s+Briscoe\b", re.I), "Birdsall P. Briscoe"),
    (re.compile(r"\bJohn\s+F\.?\s+Staub\b", re.I), "John F. Staub"),
    (re.compile(r"\bKenneth\s+Franzheim\b", re.I), "Kenneth Franzheim"),
    (re.compile(r"\bJoseph\s+Finger\b", re.I), "Joseph Finger"),
    (re.compile(r"\bHoward\s+Barnstone\b", re.I), "Howard Barnstone"),
    (re.compile(r"\bJohn\s+S\.?\s+Chase\b", re.I), "John S. Chase"),
    (re.compile(r"\bLamar\s+Q\.?\s+Cato\b", re.I), "Lamar Q. Cato"),
    (re.compile(r"\bCameron\s+D?\.?\s*Fairchild\b", re.I), "Cameron Fairchild"),
    (re.compile(r"\bCaudill\s*,?\s*Rowlett\s*(?:&|and|,)?\s*Scott\b", re.I), "Caudill Rowlett Scott"),
    (re.compile(r"\bJ\.?\s*Eugene\s+Wukasch\b", re.I), "J. Eugene Wukasch"),
    (re.compile(r"\bHugo\s+V\.?\s+Neuhaus\b", re.I), "Hugo V. Neuhaus, Jr."),
    (re.compile(r"\bCowell\s*(?:&|and)\s*Neuhaus\b", re.I), "Cowell & Neuhaus"),
    (re.compile(r"\bGeorge\s+Pierce\b.*\bAbel\s+B\.?\s+Pierce\b|\bPierce\s*(?:&|and)\s*Pierce\b", re.I), "Pierce & Pierce"),
    (re.compile(r"\bHermon\s+Lloyd\b", re.I), "Hermon Lloyd"),
)

NON_HARRIS_CITY_RE = re.compile(
    r"(?:\b(?:"
    r"Galveston|Dallas|Fort\s+Worth|Austin,\s*Tex|Austin\s+\(Tex|San\s+Antonio|Beaumont|"
    r"Port\s+Arthur|Orange,\s*Tex|Waco|Tyler,\s*Tex|Midland,\s*Tex|Odessa,\s*Tex|"
    r"Corpus\s+Christi|El\s+Paso|Lubbock|Amarillo|Brownsville|Laredo|McAllen|Harlingen|"
    r"Bryan,\s*Tex|College\s+Station|Huntsville,\s*Tex|Brenham|Wharton,\s*Tex|Victoria,\s*Tex|"
    r"Lufkin|Nacogdoches|Texarkana|Sherman,\s*Tex|Denison,\s*Tex|Temple,\s*Tex|Kerrville|"
    r"Seguin|Cuero|Lockhart|Round\s+Rock|San\s+Marcos|New\s+Braunfels|Abilene|San\s+Angelo|"
    r"Wichita\s+Falls|Corsicana|Palestine,\s*Tex|Longview,\s*Tex|Marshall,\s*Tex|Kilgore|"
    r"Freeport,\s*Tex|Lake\s+Jackson|Texas\s+City|Alvin,\s*Tex|Rosenberg,\s*Tex|Richmond,\s*Tex|"
    r"Conroe,\s*Tex|Sugar\s+Land|Fort\s+Sam\s+Houston|"
    r"New\s+York\s+City|New\s+York\s*\(N\.?Y|Manhattan|Brooklyn|New\s+Orleans|Shreveport|"
    r"Baton\s+Rouge|Lake\s+Charles|Tulsa|Oklahoma\s+City|Chicago|Los\s+Angeles|San\s+Francisco|"
    r"Washington,\s*D\.?C|Monterrey|Mexico\s+City|London,\s+England|Paris,\s+France"
    r")\b"
    r"|\((?:N\.?Y\.?|La\.?|Okla\.?|Calif\.?|Colo\.?|Fla\.?|Ill\.?|Mass\.?|Va\.?)\))",
    re.IGNORECASE,
)

HARRIS_COUNTY_LOCALITY_RE = re.compile(
    r"\b(?:"
    r"Houston|Harris\s+County|River\s+Oaks|Montrose|Westmoreland|Heights|Bellaire|"
    r"West\s+University|Baytown|Pasadena,\s*Tex|La\s+Porte|Deer\s+Park|Seabrook|"
    r"Clear\s+Lake|Humble,\s*Tex|Tomball|Katy,\s*Tex|Memorial|Tanglewood|Sharpstown|"
    r"Glenbrook|Meyerland|Broadacres|Courtlandt|Shadyside"
    r")\b",
    re.IGNORECASE,
)

SUFFIX_MAP: dict[str, str] = {
    "STREET": "ST",
    "ST": "ST",
    "AVENUE": "AVE",
    "AVE": "AVE",
    "AV": "AVE",
    "BOULEVARD": "BLVD",
    "BLVD": "BLVD",
    "DRIVE": "DR",
    "DR": "DR",
    "ROAD": "RD",
    "RD": "RD",
    "LANE": "LN",
    "LN": "LN",
    "COURT": "CT",
    "CT": "CT",
    "PLACE": "PL",
    "PL": "PL",
    "PARKWAY": "PKWY",
    "PKWY": "PKWY",
    "FREEWAY": "FWY",
    "FWY": "FWY",
    "CIRCLE": "CIR",
    "CIR": "CIR",
    "WAY": "WAY",
    "TRAIL": "TRL",
    "TRL": "TRL",
    "TERRACE": "TER",
    "TER": "TER",
    "SPEEDWAY": "SPWY",
    "SPWY": "SPWY",
    "HIGHWAY": "HWY",
    "HWY": "HWY",
    "SQUARE": "SQ",
    "SQ": "SQ",
    "LOOP": "LOOP",
}

DIR_MAP: dict[str, str] = {
    "NORTH": "N",
    "N": "N",
    "SOUTH": "S",
    "S": "S",
    "EAST": "E",
    "E": "E",
    "WEST": "W",
    "W": "W",
}

STREET_SUFFIX_PATTERN = (
    r"(?:Street|St\.?|Avenue|Ave\.?|Boulevard|Blvd\.?|Drive|Dr\.?|Road|Rd\.?|"
    r"Lane|Ln\.?|Court|Ct\.?|Place|Pl\.?|Parkway|Pkwy\.?|Freeway|Fwy\.?|"
    r"Circle|Cir\.?|Way|Trail|Trl\.?|Terrace|Ter\.?|Speedway|Spwy\.?|Square|Sq\.?)"
)

ADDRESS_EXTRACT_RE = re.compile(
    rf"(?<![\d#/-])\b([1-9]\d{{1,4}})(?:\s*-\s*\d{{1,5}})?\s+"
    rf"(?:(North|South|East|West|N\.?|S\.?|E\.?|W\.?)\s+)?"
    rf"([A-Z0-9][A-Za-z0-9'.]*(?:\s+[A-Za-z0-9'.]+){{0,2}})\s+"
    rf"({STREET_SUFFIX_PATTERN})(?![A-Za-z])",
    re.IGNORECASE,
)

# Also catch well-known Houston boulevards often written without suffix in captions (e.g. "2131 San Felipe", "1919 Bolsover")
BARE_HOUSTON_STREET_MAP: dict[str, str] = {
    "SAN FELIPE": "SAN FELIPE ST",
    "WESTHEIMER": "WESTHEIMER RD",
    "BISSONNET": "BISSONNET ST",
    "MONTROSE": "MONTROSE BLVD",
    "MAIN": "MAIN ST",
    "FANNIN": "FANNIN ST",
    "TRAVIS": "TRAVIS ST",
    "LOUISIANA": "LOUISIANA ST",
    "MILAM": "MILAM ST",
    "RUSK": "RUSK ST",
    "CAPITOL": "CAPITOL ST",
    "TEXAS": "TEXAS AVE",
    "PRAIRIE": "PRAIRIE ST",
    "PRESTON": "PRESTON ST",
    "CONGRESS": "CONGRESS ST",
    "WASHINGTON": "WASHINGTON AVE",
    "HOLCOMBE": "HOLCOMBE BLVD",
    "KIRBY": "KIRBY DR",
    "SHEPHERD": "SHEPHERD DR",
    "BUFFALO SPEEDWAY": "BUFFALO SPWY",
    "MEMORIAL": "MEMORIAL DR",
    "BELLAIRE": "BELLAIRE BLVD",
    "RICHMOND": "RICHMOND AVE",
    "ALABAMA": "ALABAMA ST",
    "LOVETT": "LOVETT BLVD",
    "NORTH BOULEVARD": "NORTH BLVD",
    "SOUTH BOULEVARD": "SOUTH BLVD",
    "BROADWAY": "BROADWAY ST",
}

BARE_STREET_RE = re.compile(
    r"(?<![\d#/-])\b([1-9]\d{2,4})\s+(?:(N\.?|S\.?|E\.?|W\.?)\s+)?"
    r"(San\s+Felipe|Westheimer|Bissonnet|Holcombe|Bellaire|Lovett)(?:\s*[.,;)]|$)",
    re.IGNORECASE,
)

GENERIC_LANDMARK_NAMES: frozenset[str] = frozenset({
    "RESIDENCE",
    "HOUSE",
    "OFFICE BUILDING",
    "OFFICE TOWER",
    "RESIDENTIAL OFFICE TOWER",
    "DRAWING OF AN OFFICE TOWER",
    "COMMERCIAL BUILDING",
    "APARTMENT BUILDING",
    "APARTMENTS",
    "TOWNHOUSES",
    "TWO TOWN HOUSES",
    "WAREHOUSE",
    "CHURCH",
    "FIRST BAPTIST CHURCH",
    "FIRST METHODIST CHURCH",
    "FIRST PRESBYTERIAN CHURCH",
    "TRINITY LUTHERAN CHURCH",
    "TRINITY EPISCOPAL CHURCH",
    "ST PAULS METHODIST CHURCH",
    "ELEMENTARY SCHOOL",
    "HIGH SCHOOL",
    "JUNIOR HIGH SCHOOL",
    "FIRE STATION",
    "BRANCH LIBRARY",
    "MEDICAL BUILDING",
    "PROFESSIONAL BUILDING",
    "BANK BUILDING",
    "PARKING GARAGE",
    "SHOPPING CENTER",
    "UNIDENTIFIED",
    "UNTITLED",
    "HOUSTON",
    "HOUSTON TEXAS",
    "RIVER OAKS",
    "MEMORIAL",
    "MONTROSE",
    "DOWNTOWN HOUSTON",
    "RICE UNIVERSITY",
    "UNIVERSITY OF HOUSTON",
    "TEXAS MEDICAL CENTER",
    "BUFFALO BAYOU PARK",
    "HERMANN PARK",
    "MEMORIAL PARK",
    "SAM HOUSTON PARK",
    "SOUTHWESTERN BELL",
    "SOUTHWESTERN BELL TELEPHONE",
    "SOUTHWESTERN BELL TELEPHONE CO",
    "SOUTHWESTERN BELL TELEPHONE COMPANY",
    "HOUSTON LIGHTING AND POWER",
    "ST LUKES",
    "SAINT LUKES",
    "METHODIST HOSPITAL",
    "MEMORIAL HOSPITAL",
})

NON_SURNAME_HOUSE_MODIFIERS: frozenset[str] = frozenset({
    "ANNE", "QUEEN", "VICTORIAN", "COLONIAL", "TUDOR", "MODERNE", "MODERN", "PRAIRIE",
    "CRAFTSMAN", "BUNGALOW", "RANCH", "SPANISH", "MEDITERRANEAN", "GEORGIAN", "GOTHIC",
    "BEACH", "LAKE", "BAY", "RIVER", "COUNTRY", "TOWN", "CITY", "FARM", "RANCH",
    "GUEST", "CLUB", "FIELD", "BATH", "FIRE", "SCHOOL", "COURT", "PARISH", "CHAPTER",
    "COFFEE", "ICE", "POWER", "PUMP", "GATE", "CARRIAGE", "BOARDING", "TENEMENT",
    "ROW", "MANOR", "OPERA", "CUSTOM", "CLEARING", "PACKING", "WARE", "BOAT", "GREEN",
    "TREE", "POOL", "GARDEN", "SUMMER", "WINTER", "WEEKEND", "MODEL", "SPEC", "DUPLEX",
    "APARTMENT", "FRATERNITY", "SORORITY", "RECTORY", "PARSONAGE", "CONVENT",
    "LUTHERAN", "BAPTIST", "METHODIST", "CATHOLIC", "EPISCOPAL", "PRESBYTERIAN",
    "CHRISTIAN", "JEWISH", "FUNERAL", "NURSING", "MATERNITY", "CHILDRENS", "PROTESTANT",
    "SHADYSIDE", "RIVEROAKS", "WESTMORELAND", "COURTLANDT", "BROADACRES",
    "COMPLEX", "BREWERY", "STAR", "EASTERN", "WESTERN", "NORTHERN", "SOUTHERN", "CENTRAL",
    "HOME", "HOMES", "ESTATE", "ESTATES", "HALL", "TOWER", "CENTER", "CENTRE", "LODGE",
    "TEMPLE", "CLINIC", "HOSPITAL", "OFFICE", "SHOP", "STORE",
})

HARRIS_COUNTY_MUNICIPALITIES: frozenset[str] = frozenset({
    "HOUSTON", "HARRIS COUNTY", "BAYTOWN", "PASADENA", "BELLAIRE", "WEST UNIVERSITY",
    "WEST UNIVERSITY PLACE", "LA PORTE", "DEER PARK", "SEABROOK", "HUMBLE", "TOMBALL",
    "KATY", "SPRING", "CYPRESS", "WEBSTER", "SOUTH HOUSTON", "GALENA PARK",
    "JACINTO CITY", "HUNTERS CREEK", "HUNTERS CREEK VILLAGE", "PINEY POINT",
    "PINEY POINT VILLAGE", "BUNKER HILL", "BUNKER HILL VILLAGE", "HEDWIG VILLAGE",
    "HILSHIRE VILLAGE", "SPRING VALLEY", "SPRING VALLEY VILLAGE", "JERSEY VILLAGE",
    "EL LAGO", "TAYLOR LAKE VILLAGE", "NASSAU BAY", "MORGANS POINT", "SHOREACRES",
    "CROSBY", "CHANNELVIEW", "HIGHLANDS", "HUFFMAN", "KINGWOOD", "CLEAR LAKE",
    "ADDICKS", "BARKER", "ALIEF", "HARRISBURG", "INDEPENDENCE HEIGHTS", "GOOSE CREEK",
    "PELLLY", "CEDAR BAYOU", "LYNCHBURG", "HOCKLEY", "WALLER",
})

COMMON_AMBIGUOUS_SURNAMES: frozenset[str] = frozenset({
    "SMITH", "JOHNSON", "WILLIAMS", "JONES", "BROWN", "DAVIS", "MILLER", "WILSON",
    "MOORE", "TAYLOR", "ANDERSON", "THOMAS", "JACKSON", "WHITE", "HARRIS", "MARTIN",
    "THOMPSON", "GARCIA", "MARTINEZ", "ROBINSON", "CLARK", "RODRIGUEZ", "LEWIS", "LEE",
    "WALKER", "HALL", "ALLEN", "YOUNG", "KING", "WRIGHT", "SCOTT", "GREEN", "BAKER",
    "ADAMS", "NELSON", "HILL", "CARTER", "MITCHELL", "ROBERTS", "TURNER", "PHILLIPS",
    "CAMPBELL", "PARKER", "EVANS", "EDWARDS", "COLLINS", "STEWART", "MORRIS", "ROGERS",
    "COOK", "MORGAN", "BELL", "MURPHY", "BAILEY", "COOPER", "RICHARDSON", "COX", "HOWARD",
    "WARD", "WOOD", "WATSON", "BROOKS", "BENNETT", "GRAY", "JAMES", "REYES", "CRUZ",
    "HUGHES", "PRICE", "MYERS", "LONG", "FOSTER", "SANDERS", "ROSS", "MORALES", "POWELL",
    "SULLIVAN", "RUSSELL", "ORTIZ", "JENKINS", "PERRY", "BUTLER", "BARNES", "FISHER",
    "HOUSTON", "TEXAS", "MEMORIAL", "HEIGHTS",
})


@dataclass
class ArchivalCandidateItem:
    source_kind: str  # "taro" or "hpl_cdm"
    collection_key: str
    collection_title: str
    repository: str
    item_id: str
    title: str
    description: str
    container_or_ref: str
    year: int | None
    date_label: str
    architect: str
    url: str
    image_url: str
    thumb_url: str
    is_visual_photo_or_rendering: bool
    raw_location_text: str


def normalize_street_address_keys(raw_addr: str) -> tuple[str, str]:
    """Return `(with_dir_no_suffix, nodir_no_suffix)` normalized street keys (matching `haif_index` / `norm_addr`)."""
    s = sanitize_atlas_text(str(raw_addr or "")).upper()
    s = re.sub(r"\([^)]*\)", " ", s)
    s = re.sub(r"\b(?:HOUSTON|TEXAS|TX|77\d{3})\b", " ", s)
    s = re.sub(r"\b(?:APT|STE|SUITE|UNIT|BLDG|FL|FLOOR|RM)\b.*$", " ", s)
    s = re.sub(r"[^A-Z0-9\s/]", " ", s)
    toks = [t for t in s.split() if t]
    if len(toks) < 2 or not toks[0].isdigit() or int(toks[0]) <= 0:
        return ("", "")
    num = toks[0]
    rest = toks[1:]
    dir_tok = ""
    if rest and rest[0] in DIR_MAP:
        dir_tok = DIR_MAP[rest[0]]
        rest = rest[1:]
    if rest and rest[-1] in SUFFIX_MAP:
        rest = rest[:-1]
    elif len(rest) >= 2 and rest[-2] in SUFFIX_MAP:
        rest = rest[:-2]
    if not rest:
        return ("", "")
    street_core = " ".join(rest)
    # Normalize "BUFFALO DR" (historical pre-1950s name of Allen Parkway) alias support handled separately if needed
    key_with_dir = f"{num} {dir_tok} {street_core}".strip() if dir_tok else f"{num} {street_core}"
    key_nodir = f"{num} {street_core}"
    return (" ".join(key_with_dir.split()), " ".join(key_nodir.split()))


def extract_candidate_addresses(text: str) -> list[tuple[str, str, str]]:
    """Extract `(display_addr, norm_key, norm_nodir_key)` street addresses from archival text."""
    if not text:
        return []
    cleaned = sanitize_atlas_text(text)
    # Reject explicit "1400 Block Main St" from exact house-number matching
    cleaned_no_block = re.sub(r"\b\d{3,5}\s+Block\s+(?:of\s+)?", " ", cleaned, flags=re.I)

    found: list[tuple[str, str, str]] = []
    seen: set[str] = set()

    for m in ADDRESS_EXTRACT_RE.finditer(cleaned_no_block):
        num, raw_dir, raw_street, raw_suf = m.group(1), m.group(2) or "", m.group(3), m.group(4)
        n_int = int(num)
        # Guard against bare 4-digit years like "1940 Air Terminal" or "1929 Gulf Building" unless real street
        street_up = " ".join(raw_street.upper().split())
        if 1836 <= n_int <= 2026 and street_up in {
            "AIR", "CENSUS", "WORLD", "TEXAS", "HOUSTON", "ANNUAL", "EDITION", "SERIES", "PROJECT", "JOB"
        }:
            continue
        if any(bad in street_up.split() for bad in ("BOX", "FOLDER", "SERIES", "FILE", "ITEM", "SHEET", "PAGE", "DRAWER", "TUBE")):
            continue
        if len(street_up) < 2:
            continue
        dir_clean = DIR_MAP.get(raw_dir.rstrip(".").upper(), "")
        suf_clean = SUFFIX_MAP.get(raw_suf.rstrip(".").upper(), raw_suf.rstrip(".").upper())
        disp = " ".join(p for p in (num, dir_clean, street_up.title(), suf_clean.title()) if p)
        k1, k2 = normalize_street_address_keys(f"{num} {dir_clean} {street_up} {suf_clean}")
        if k1 and k1 not in seen:
            seen.add(k1)
            found.append((disp, k1, k2))

    for m in BARE_STREET_RE.finditer(cleaned_no_block):
        num, raw_dir, bare_st = m.group(1), m.group(2) or "", m.group(3)
        canon_st = BARE_HOUSTON_STREET_MAP.get(" ".join(bare_st.upper().split()), "")
        if not canon_st:
            continue
        dir_clean = DIR_MAP.get(raw_dir.rstrip(".").upper(), "")
        disp = " ".join(p for p in (num, dir_clean, canon_st.title()) if p)
        k1, k2 = normalize_street_address_keys(f"{num} {dir_clean} {canon_st}")
        if k1 and k1 not in seen:
            seen.add(k1)
            found.append((disp, k1, k2))

    return found


def is_non_harris_county_location(text: str) -> bool:
    """Return True if text names an out-of-county/out-of-state city without naming Houston/Harris County."""
    if not text:
        return False
    # Catch explicit `<Town>, Tex.` / `<Town>, Texas` / `<Town>, TX` outside Harris County
    for m_tx in re.finditer(r"\b([A-Z][a-z']+(?:\s+[A-Z][a-z']+){0,2}),\s*(?:Tex\.?|Texas|TX)\b", text):
        words = m_tx.group(1).upper().replace("'", "").split()
        candidates = {" ".join(words[i:]) for i in range(len(words))}
        is_sam_houston = len(words) >= 2 and words[-2:] == ["SAM", "HOUSTON"]
        if is_sam_houston or (
            not (candidates & HARRIS_COUNTY_MUNICIPALITIES)
            and not re.search(r"(?<!Sam\s)\bHouston\b", text, re.I)
        ):
            return True
    if NON_HARRIS_CITY_RE.search(text):
        if not HARRIS_COUNTY_LOCALITY_RE.search(text):
            return True
        # Even if "Houston" appears as collection/architect city, reject if explicit "(N.Y.)" or "Dallas, Tex." is in place/title
        if re.search(
            r",\s*(?:Dallas|Fort\s+Worth|Austin|San\s+Antonio|Galveston|Beaumont|Waco|Tyler|Corpus\s+Christi|"
            r"Midland|El\s+Paso|Shiner|Brenham|Gonzales|Bryan|College\s+Station|Huntsville|Lufkin|New\s+York|New\s+Orleans|Shreveport|Tulsa)\b"
            r"|\((?:N\.?Y\.?|La\.?|Okla\.?|Calif\.?|Colo\.?|Fla\.?|Ill\.?|Mass\.?|Va\.?|Dallas|Galveston|Austin|San\s+Antonio)\)",
            text,
            re.IGNORECASE,
        ):
            return True
    return False


def _clean_architect_label(raw_arch: str) -> str:
    s = sanitize_atlas_text(raw_arch or "").strip(" ,;:-")
    s = re.sub(r",?\s*(?:architects?|archs?\.?|FAIA|AIA)\.?$", "", s, flags=re.I).strip(" ,;:-")
    s = re.sub(r"\s+(?:and|&|with)$", "", s, flags=re.I).strip(" ,;:-")
    s = re.sub(r",\s*\d{4}\.?$", "", s).strip(" ,;:-")
    if s.endswith(".") and not re.search(
        r"(?:\b(?i:sr|jr|inc|co|corp|bros|assoc|ltd)|\b[A-Z])\.$",
        s,
    ):
        s = s[:-1].rstrip(" ,;:-")
    return s


def infer_architect_from_text(text: str, default_architect: str = "") -> str:
    """Extract a clean architect/firm credit from collection title, creator, or caption prose."""
    if not text and not default_architect:
        return ""
    for pat, canon in KNOWN_COLLECTION_ARCHITECTS:
        if pat.search(text):
            return canon
    # Check Yolita Schmidt / HPL caption pattern: "1939 / Herman Lloyd and Harvin Moore, architects" or "Designed by X"
    # Avoid splitting on middle-initial periods like "Maurice J. Sullivan" via `(?<!\b[A-Z])\.\s`.
    m_des = re.search(
        r"(?:Designed\s+by|Architects?\s*:\s*|(?:18|19)\d{2}\s*/\s*)([A-Z][A-Za-z.&,\s'-]{4,65}?)"
        r"(?:,\s*architects?|(?<!\b[A-Z])\.\s|$|;\s|\(\d{4}\))",
        text,
    )
    if m_des:
        cand = _clean_architect_label(m_des.group(1))
        if 4 <= len(cand) <= 60 and not re.search(r"\b(?:unknown|none|houston|texas|street|blvd)\b", cand, re.I):
            return cand
    return _clean_architect_label(default_architect)


def extract_historical_year_from_texts(
    primary_texts: list[str],
    *,
    fallback_date_fields: list[str] | None = None,
    ignore_digitization_years: frozenset[int] = frozenset({2012, 2015, 2019, 2020, 2024, 2025, 2026}),
) -> tuple[int | None, str]:
    """Extract `(historical_year, label)` while ignoring HPL ContentDM batch-digitization timestamps."""
    for txt in primary_texts:
        if not txt:
            continue
        s = sanitize_atlas_text(txt)
        m_circa = re.search(r"\b(?:ca?\.?|circa)\s*(18[4-9]\d|19\d{2}|20[01]\d)s?\b", s, re.I)
        if m_circa:
            yr = int(m_circa.group(1))
            return yr, f"c. {yr}"
        m_decade = re.search(r"\b(18[5-9]0|19\d0)s\b", s)
        for m_yr in re.finditer(r"\b(18[3-9]\d|19\d{2}|20[0-2]\d)\b", s):
            yr = int(m_yr.group(1))
            if yr in ignore_digitization_years and len(s.strip()) <= 10:
                continue
            return yr, str(yr)
        if m_decade:
            dec = int(m_decade.group(1))
            return dec + 5, f"{dec}s"

    for fb in fallback_date_fields or []:
        if not fb:
            continue
        s = sanitize_atlas_text(fb).strip()
        m_iso = re.match(r"^(18[3-9]\d|19\d{2}|20[01]\d)(?:-\d{2}-\d{2})?$", s)
        if m_iso:
            yr = int(m_iso.group(1))
            if yr not in ignore_digitization_years:
                return yr, str(yr)
    return None, ""


def is_houston_architectural_finding_aid(meta: dict[str, Any]) -> bool:
    """Return True if a TARO search API metadata entry is a Houston/Harris County architectural or built-environment aid."""
    repo = str(meta.get("repository") or "").lower()
    title = str(meta.get("title") or "")
    abstract = str(meta.get("abstract") or "")
    subjects = " | ".join(str(x) for x in (meta.get("subject_topics") or []))
    geos = " | ".join(str(x) for x in (meta.get("geographic_areas") or []))
    genres = " | ".join(str(x) for x in (meta.get("genreforms") or []))
    creators = " | ".join(str(x) for x in (meta.get("creators") or []))

    full_blob = f"{title} | {abstract} | {subjects} | {geos} | {genres} | {creators}"
    if not HARRIS_COUNTY_LOCALITY_RE.search(full_blob) and repo not in ("houpub", "hcarch", "mfah"):
        return False

    # Require architectural / built-environment / historic-district / real-estate / civic-planning signal
    arch_re = re.compile(
        r"\b(?:"
        r"architect|architects|architectural|architecture|blueprints?|renderings?|"
        r"historic\s+district|building\s+survey|real\s+estate|subdivision|"
        r"construction|land\s+company|garden\s+club|city\s+planning|ward|"
        r"river\s+oaks|westmoreland|montrose|heights|freedmen"
        r")\b",
        re.IGNORECASE,
    )
    if not arch_re.search(f"{title} | {subjects} | {genres} | {creators}"):
        if repo != "utaaa":
            return False
    # Skip pure oral-history-only audio collections with zero project folders unless architect papers
    if re.search(r"\boral\s+history\s+interview\s+collection\b", title, re.I) and "papers" not in title.lower():
        return False
    return True


def _local_tag(elem: ET.Element) -> str:
    return elem.tag.split("}")[-1].lower()


def _elem_text(elem: ET.Element | None) -> str:
    if elem is None:
        return ""
    return sanitize_atlas_text(" ".join("".join(elem.itertext()).split()))


def parse_taro_ead_xml(
    xml_bytes: bytes,
    *,
    repo_code: str,
    ead_filename: str,
    fallback_repo_name: str = "",
    fallback_title: str = "",
    fallback_display_url: str = "",
) -> list[ArchivalCandidateItem]:
    """Parse a raw TARO EAD XML finding aid into folder/item `ArchivalCandidateItem` records."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []

    ead_stem = ead_filename[:-4] if ead_filename.lower().endswith(".xml") else ead_filename
    coll_key = f"taro:{repo_code}/{ead_stem}"
    repo_label = (
        REPO_SHORT_LABELS.get(repo_code.lower())
        or sanitize_atlas_text(fallback_repo_name)
        or repo_code.upper()
    )
    display_url = fallback_display_url or f"https://txarchives.org/{repo_code}/finding_aids/{ead_filename}"
    if not display_url.startswith("http"):
        display_url = f"https://{display_url.lstrip('/')}"

    # Extract collection-level title and origination creator from <archdesc><did>
    coll_title = sanitize_atlas_text(fallback_title)
    origination_text = ""
    archdesc = None
    for ch in root.iter():
        if _local_tag(ch) == "archdesc":
            archdesc = ch
            break
    if archdesc is not None:
        for ch in archdesc:
            if _local_tag(ch) == "did":
                for sub in ch:
                    st = _local_tag(sub)
                    if st == "unittitle" and not coll_title:
                        coll_title = _elem_text(sub)
                    elif st == "origination":
                        origination_text = _elem_text(sub)
                break

    coll_architect = infer_architect_from_text(f"{coll_title} | {origination_text}")

    items: list[ArchivalCandidateItem] = []
    comp_tags = {"c", *(f"c{i:02d}" for i in range(1, 13))}

    def _walk_component(comp: ET.Element, parent_series: str, idx_path: str) -> None:
        level = (comp.attrib.get("level") or "").lower()
        did = None
        scopecontent_txt = ""
        child_comps: list[ET.Element] = []

        for ch in comp:
            lt = _local_tag(ch)
            if lt == "did" and did is None:
                did = ch
            elif lt == "scopecontent" and not scopecontent_txt:
                scopecontent_txt = _elem_text(ch)[:280]
            elif lt in comp_tags:
                child_comps.append(ch)
            elif lt in ("dsc", "c01", "c02"):
                for sub in ch:
                    if _local_tag(sub) in comp_tags:
                        child_comps.append(sub)

        unittitle = ""
        unitdate = ""
        unitid = ""
        containers: list[str] = []
        geog_notes: list[str] = []

        if did is not None:
            for sub in did:
                st = _local_tag(sub)
                if st == "unittitle":
                    unittitle = _elem_text(sub)
                    for inner in sub:
                        if _local_tag(inner) == "unitdate" and not unitdate:
                            unitdate = _elem_text(inner)
                elif st == "unitdate":
                    unitdate = _elem_text(sub)
                elif st == "unitid":
                    unitid = _elem_text(sub)
                elif st == "container":
                    ctype = sanitize_atlas_text(sub.attrib.get("type") or sub.attrib.get("label") or "Box").title()
                    cval = _elem_text(sub)
                    if cval:
                        containers.append(f"{ctype} {cval}")
                elif st in ("physloc", "geogname"):
                    gval = _elem_text(sub)
                    if gval:
                        geog_notes.append(gval)

        cur_series = parent_series
        if level in ("series", "subseries", "collection", "recordgrp", "subgrp") and unittitle:
            cur_series = unittitle

        # Emit leaf or file/item-level components that carry a meaningful project/folder title
        is_candidate_node = (not child_comps) or level in ("file", "item")
        if is_candidate_node and unittitle and len(unittitle) >= 4:
            loc_context = " | ".join(p for p in (unittitle, scopecontent_txt, " ".join(geog_notes), cur_series) if p)
            if not is_non_harris_county_location(loc_context):
                yr, date_lbl = extract_historical_year_from_texts(
                    [unitdate, unittitle],
                    ignore_digitization_years=frozenset({2024, 2025, 2026}),
                )
                ref_parts: list[str] = []
                if unitid and len(unitid) <= 32 and unitid.lower() not in unittitle.lower():
                    ref_parts.append(f"Job/ID {unitid}")
                if containers:
                    ref_parts.append(", ".join(containers[:3]))
                container_str = " · ".join(ref_parts)
                is_periodical_folder = bool(PERIODICAL_OR_CLIPPING_FOLDER_RE.search(f"{unittitle} | {cur_series}"))
                default_arch_for_folder = "" if is_periodical_folder else coll_architect
                item_arch = infer_architect_from_text(f"{unittitle} | {scopecontent_txt}", default_arch_for_folder)

                items.append(
                    ArchivalCandidateItem(
                        source_kind="taro",
                        collection_key=coll_key,
                        collection_title=coll_title or f"TARO {repo_code.upper()} {ead_stem}",
                        repository=repo_label,
                        item_id=f"{coll_key}#{idx_path}",
                        title=unittitle[:200],
                        description=scopecontent_txt[:240],
                        container_or_ref=container_str[:120],
                        year=yr,
                        date_label=date_lbl or sanitize_atlas_text(unitdate)[:32],
                        architect=item_arch,
                        url=display_url,
                        image_url="",
                        thumb_url="",
                        is_visual_photo_or_rendering=False,
                        raw_location_text=loc_context[:400],
                    )
                )

        for i, child in enumerate(child_comps, 1):
            _walk_component(child, cur_series, f"{idx_path}.{i}" if idx_path else str(i))

    if archdesc is not None:
        for ch in archdesc:
            if _local_tag(ch) == "dsc":
                idx = 0
                for sub in ch:
                    lt = _local_tag(sub)
                    if lt in comp_tags:
                        idx += 1
                        _walk_component(sub, "", str(idx))
                    else:
                        for sub2 in sub:
                            if _local_tag(sub2) in comp_tags:
                                idx += 1
                                _walk_component(sub2, "", str(idx))
    return items


def harvest_taro_collections(
    cache_dir: Path,
    *,
    offline: bool = False,
    max_workers: int = 4,
) -> tuple[dict[str, dict[str, str]], list[ArchivalCandidateItem]]:
    """Fetch/load Houston architectural TARO EAD XML finding aids and parse candidate folder items."""
    taro_dir = cache_dir / "taro_ead_cache"
    taro_dir.mkdir(parents=True, exist_ok=True)
    catalog_path = taro_dir / "search_catalog.json"

    if catalog_path.exists() and (offline or catalog_path.stat().st_size > 200):
        selected_meta = json.loads(catalog_path.read_text(encoding="utf-8"))
    elif offline:
        selected_meta = []
    else:
        req = urllib.request.Request(TARO_SEARCH_URL, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            all_results = json.loads(resp.read().decode("utf-8", errors="replace"))
        seen_keys: set[str] = set()
        selected_meta = []
        for entry in all_results:
            if not isinstance(entry, dict):
                continue
            if not is_houston_architectural_finding_aid(entry):
                continue
            repo = str(entry.get("repository") or "").strip()
            fn = str(entry.get("filename") or "").strip()
            xml_rel = str(entry.get("xml") or "").strip()
            if not repo or not fn or not xml_rel:
                continue
            ukey = f"{repo}/{fn}"
            if ukey in seen_keys:
                continue
            seen_keys.add(ukey)
            selected_meta.append({
                "repository": repo,
                "repository_name": sanitize_atlas_text(str(entry.get("repository_name") or "")),
                "filename": fn,
                "title": sanitize_atlas_text(str(entry.get("title") or "")),
                "display_site": str(entry.get("display_site") or f"txarchives.org/{repo}/finding_aids/{fn}"),
                "xml": xml_rel,
            })
        catalog_path.write_text(json.dumps(selected_meta, indent=2), encoding="utf-8")

    def _fetch_one_xml(meta_item: dict[str, str]) -> tuple[dict[str, str], bytes | None]:
        repo = meta_item["repository"]
        fn = meta_item["filename"]
        local_xml = taro_dir / f"{repo}__{fn}"
        if local_xml.exists() and local_xml.stat().st_size > 100:
            return meta_item, local_xml.read_bytes()
        if offline:
            return meta_item, None
        xml_url = meta_item["xml"]
        if not xml_url.startswith("http"):
            xml_url = f"https://{xml_url.lstrip('/')}"
        for attempt in range(3):
            try:
                time.sleep(0.12)
                req = urllib.request.Request(xml_url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=25) as r:
                    raw = r.read()
                if raw.lstrip().startswith(b"<") and b"<html" not in raw[:300].lower():
                    local_xml.write_bytes(raw)
                    return meta_item, raw
                break
            except Exception:  # pragma: no cover - transient network
                time.sleep(0.5 * (attempt + 1))
        return meta_item, None

    collections_meta: dict[str, dict[str, str]] = {}
    all_items: list[ArchivalCandidateItem] = []

    ordered_meta = sorted(
        selected_meta,
        key=lambda m: (str(m.get("repository") or ""), str(m.get("filename") or "")),
    )

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        for meta_item, xml_bytes in pool.map(_fetch_one_xml, ordered_meta):
            if not xml_bytes:
                continue
            repo = meta_item["repository"]
            fn = meta_item["filename"]
            stem = fn[:-4] if fn.lower().endswith(".xml") else fn
            ckey = f"taro:{repo}/{stem}"
            disp_url = meta_item.get("display_site") or f"https://txarchives.org/{repo}/finding_aids/{fn}"
            if not disp_url.startswith("http"):
                disp_url = f"https://{disp_url.lstrip('/')}"

            parsed = parse_taro_ead_xml(
                xml_bytes,
                repo_code=repo,
                ead_filename=fn,
                fallback_repo_name=meta_item.get("repository_name", ""),
                fallback_title=meta_item.get("title", ""),
                fallback_display_url=disp_url,
            )
            if parsed:
                collections_meta[ckey] = {
                    "title": parsed[0].collection_title,
                    "repo": parsed[0].repository,
                    "architect": infer_architect_from_text(parsed[0].collection_title),
                    "url": disp_url,
                    "kind": "taro",
                }
                all_items.extend(parsed)

    return collections_meta, all_items


def build_hpl_cdm_urls(coll: str, ptr: int, *, iiif_supported: bool, has_jp2: bool) -> tuple[str, str, str]:
    """Build verified HTTP 200 `(source_url, image_url, thumb_url)` for an HPL ContentDM item.

    Empirical verification (`2026-11-08`):
    - `/digital/iiif/{coll}/{ptr}/full/960,/0/default.jpg` returns `200 image/jpeg` on open IIIF
      collections (`p17006coll41`, `p17006coll175`, `p17006coll199`, `p17006coll231`), whereas
      `p17006coll220` and `p17006coll238` return `HTTP 501 Not Implemented` on `/digital/iiif/...`.
    - `/digital/api/singleitem/image/{coll}/{ptr}/default.jpg` returns `200 image/jpeg` across ALL 6
      collections (including `p17006coll220` and `p17006coll238`).
    """
    source_url = f"{CDM_BASE_URL}/digital/collection/{coll}/id/{ptr}"
    if not has_jp2:
        return source_url, "", ""
    single_img = f"{CDM_BASE_URL}/digital/api/singleitem/image/{coll}/{ptr}/default.jpg"
    if iiif_supported:
        thumb_img = f"{CDM_BASE_URL}/digital/iiif/{coll}/{ptr}/full/960,/0/default.jpg"
    else:
        thumb_img = single_img
    return source_url, single_img, thumb_img


def parse_hpl_cdm_record(
    coll: str,
    coll_title: str,
    default_arch: str,
    iiif_supported: bool,
    info: dict[str, Any],
) -> ArchivalCandidateItem | None:
    """Normalize a raw ContentDM `dmGetItemInfo` JSON object into an `ArchivalCandidateItem`."""
    raw_ptr = info.get("dmrecord") or info.get("pointer")
    if raw_ptr is None or not str(raw_ptr).strip().isdigit():
        return None
    ptr = int(str(raw_ptr).strip())

    title = sanitize_atlas_text(str(info.get("title") or "").strip())
    descri = sanitize_atlas_text(str(info.get("descri") or "").strip())
    captio = sanitize_atlas_text(str(info.get("captio") or "").strip())
    place = sanitize_atlas_text(str(info.get("place") or "").strip())
    relatij = sanitize_atlas_text(str(info.get("relatij") or "").strip())
    creato = sanitize_atlas_text(str(info.get("creato") or "").strip())
    publis = sanitize_atlas_text(str(info.get("publis") or "").strip())
    identi = sanitize_atlas_text(str(info.get("identi") or "").split(";")[0].strip())
    item_type = str(info.get("type") or "").strip().lower()
    find_file = str(info.get("find") or "").strip().lower()

    if not title:
        return None

    loc_blob = " | ".join(p for p in (place, relatij, captio, descri, title, publis) if p)
    if is_non_harris_county_location(loc_blob):
        return None

    has_jp2 = find_file.endswith((".jp2", ".jpg", ".jpeg", ".png", ".tif", ".tiff"))
    source_url, image_url, thumb_url = build_hpl_cdm_urls(
        coll, ptr, iiif_supported=iiif_supported, has_jp2=has_jp2
    )

    yr, date_lbl = extract_historical_year_from_texts(
        [
            captio,
            str(info.get("dateb") or ""),
            str(info.get("date") or ""),
            str(info.get("era") or ""),
            descri,
            title,
        ],
        fallback_date_fields=[
            str(info.get("relatim") or ""),
            str(info.get("datea") or ""),
            str(info.get("year") or ""),
        ],
    )

    arch = ""
    if (
        creato
        and coll != "p17006coll199"
        and not re.search(r"\b(?:unknown|unidentified|houston\s+post|chronicle|photographer)\b", creato, re.I)
    ):
        arch = _clean_architect_label(creato.split(";")[0].strip())
    if not arch:
        arch = infer_architect_from_text(f"{captio} | {descri} | {title}", default_arch)

    # Distinguish true exterior/rendering/elevation visuals suitable for `#inspector-photo-card`
    # from text newspaper clippings or narrow interior furniture details
    combined_lower = f"{title} {descri} {captio}".lower()
    is_clipping_or_text = (
        item_type == "text"
        or "news clipping" in combined_lower
        or "newspaper" in combined_lower
    )
    is_narrow_interior = bool(
        re.match(r"^(?:interior|office\s+interior|sun\s+room|window\s+of|stair\s+detail|kitchen|bathroom|bedroom)\b", title.lower())
    )
    is_visual_card = bool(has_jp2 and image_url and not is_clipping_or_text and not is_narrow_interior)

    desc_combined = captio or descri
    if len(desc_combined) > 260:
        desc_combined = desc_combined[:257].rsplit(" ", 1)[0].rstrip(".,;: ") + "..."
    return ArchivalCandidateItem(
        source_kind="hpl_cdm",
        collection_key=f"hpl:{coll}",
        collection_title=coll_title,
        repository="HMRC / Houston Public Library Digital Archives",
        item_id=f"hpl:{coll}/{ptr}",
        title=title[:200],
        description=desc_combined,
        container_or_ref=identi[:80] or f"{coll} #{ptr}",
        year=yr,
        date_label=date_lbl,
        architect=arch,
        url=source_url,
        image_url=image_url,
        thumb_url=thumb_url,
        is_visual_photo_or_rendering=is_visual_card,
        raw_location_text=loc_blob[:450],
    )


def harvest_hpl_architectural_collections(
    cache_dir: Path,
    *,
    offline: bool = False,
) -> tuple[dict[str, dict[str, str]], list[ArchivalCandidateItem]]:
    """Fetch/load all 6 untapped HPL ContentDM architectural collections into `pipeline/cache/`."""
    cache_path = cache_dir / "hpl_arch_collections_cache.json"
    raw_by_coll: dict[str, list[dict[str, Any]]] = {}

    if cache_path.exists() and (offline or cache_path.stat().st_size > 200):
        raw_by_coll = json.loads(cache_path.read_text(encoding="utf-8"))
    elif not offline:
        for coll, _title, _arch, _iiif in HPL_ARCH_COLLECTIONS:
            ptrs: list[int] = []
            start = 1
            page_size = 200
            while True:
                q_url = (
                    f"{CDM_BASE_URL}/digital/bl/dmwebservices/index.php"
                    f"?q=dmQuery/{coll}/0/title/nosort/{page_size}/{start}/1/0/0/0/json"
                )
                req = urllib.request.Request(q_url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=25) as r:
                    d = json.loads(r.read().decode("utf-8", errors="replace"))
                recs = d.get("records") or []
                if not recs:
                    break
                for rec in recs:
                    p = rec.get("pointer")
                    if p is not None and str(p).isdigit():
                        ptrs.append(int(p))
                total = int((d.get("pager") or {}).get("total") or 0)
                start += len(recs)
                if start > total or len(recs) < page_size:
                    break
                time.sleep(0.2)

            def _fetch_info(p_id: int) -> dict[str, Any] | None:
                info_url = f"{CDM_BASE_URL}/digital/bl/dmwebservices/index.php?q=dmGetItemInfo/{coll}/{p_id}/json"
                for attempt in range(3):
                    try:
                        time.sleep(0.15)
                        req_i = urllib.request.Request(info_url, headers={"User-Agent": USER_AGENT})
                        with urllib.request.urlopen(req_i, timeout=20) as ri:
                            obj = json.loads(ri.read().decode("utf-8", errors="replace"))
                        if isinstance(obj, dict) and obj.get("title"):
                            obj.setdefault("dmrecord", str(p_id))
                            return obj
                        return None
                    except Exception:  # pragma: no cover
                        time.sleep(0.5 * (attempt + 1))
                return None

            coll_infos: list[dict[str, Any]] = []
            with ThreadPoolExecutor(max_workers=4) as pool:
                for res in pool.map(_fetch_info, ptrs):
                    if res is not None:
                        coll_infos.append(res)
            raw_by_coll[coll] = coll_infos

        cache_path.write_text(json.dumps(raw_by_coll, separators=(",", ":")), encoding="utf-8")

    collections_meta: dict[str, dict[str, str]] = {}
    parsed_items: list[ArchivalCandidateItem] = []
    seen_hpl_ids: set[str] = set()

    for coll, coll_title, def_arch, iiif_ok in HPL_ARCH_COLLECTIONS:
        ckey = f"hpl:{coll}"
        collections_meta[ckey] = {
            "title": coll_title,
            "repo": "HMRC / Houston Public Library Digital Archives",
            "architect": def_arch,
            "url": f"{CDM_BASE_URL}/digital/collection/{coll}",
            "kind": "hpl_cdm",
        }
        for info in raw_by_coll.get(coll, []):
            item = parse_hpl_cdm_record(coll, coll_title, def_arch, iiif_ok, info)
            if item is not None and item.item_id not in seen_hpl_ids:
                seen_hpl_ids.add(item.item_id)
                parsed_items.append(item)

    return collections_meta, parsed_items


def _normalize_landmark_key(name: str) -> str:
    s = sanitize_atlas_text(str(name or "")).upper()
    s = re.sub(r"^(?:THE|HISTORIC)\s+", "", s)
    s = re.sub(r"\([^)]*\)", " ", s)
    s = re.sub(r"['\u2019]S\b", "S", s)
    s = re.sub(r"[^A-Z0-9]+", " ", s).strip()
    return " ".join(s.split())


def _extract_client_residence_surname(title: str) -> tuple[str, str]:
    """Extract `(full_client_phrase, surname)` from titles like 'Dr. & Mrs. Irving Schweppe Residence' or 'O. J. Lottman Residence'."""
    t = sanitize_atlas_text(title).strip()
    m = re.match(
        r"^(?:House\s+of\s+|Residence\s+of\s+)?"
        r"(?:(?:Mr\.?|Mrs\.?|Ms\.?|Dr\.?|Col\.?|Judge|Gov\.?|Prof\.?|Rev\.?)(?:\s*(?:&|and)\s*(?:Mrs\.?|Mr\.?|Dr\.?))?\s+)?"
        r"((?:[A-Z]\.|[A-Z][A-Za-z']+)(?:\s+(?:[A-Z]\.|[A-Z][A-Za-z']+)){0,3})\s+"
        r"(?:Residence|House)(?:\s*(?:[,(\[.]|$))",
        t,
    )
    if not m:
        m2 = re.match(
            r"^House\s+of\s+([A-Z][A-Za-z.']+(?:\s+(?:and|&)\s+[A-Z][A-Za-z.']+)?(?:\s+[A-Z][A-Za-z.']+){1,3})$",
            t,
            re.IGNORECASE,
        )
        if not m2:
            return ("", "")
        phrase = " ".join(m2.group(1).split())
    else:
        phrase = " ".join(m.group(1).split())

    toks = [re.sub(r"[^A-Za-z]", "", w).upper() for w in phrase.split()]
    toks = [w for w in toks if w and w not in {"JR", "SR", "II", "III", "IV", "MR", "MRS", "DR", "AND"}]
    if not toks:
        return ("", "")
    surname = toks[-1]
    if (
        len(surname) < 4
        or surname in COMMON_AMBIGUOUS_SURNAMES
        or surname in NON_SURNAME_HOUSE_MODIFIERS
        or any(t in NON_SURNAME_HOUSE_MODIFIERS for t in toks)
    ):
        return ("", "")
    # Require at least a given name or initial alongside the surname so bare "Brown Residence" never guesses
    if len(toks) < 2:
        return ("", "")
    return (phrase.upper(), surname)


def load_confidential_hcad_set(output_dir: Path) -> set[str]:
    """Load all `TX Tax Code Sec. 25.025` confidential HCAD accounts (`conf == 1`) fail-closed from `permits/`."""
    conf_set: set[str] = set()
    permits_dir = output_dir / "permits"
    if not permits_dir.exists():
        return conf_set
    for _key, shard_path in iter_json_or_gzip_shard_keys(permits_dir, "permits_", r"[A-Za-z0-9_]+"):
        shard = read_json_or_gzip_path(shard_path)
        if not isinstance(shard, dict):
            raise ValueError(
                f"Fail-closed TX Tax Code Sec. 25.025 guard: expected JSON object in {shard_path}, got {type(shard).__name__}"
            )
        for acct, rec in shard.items():
            if isinstance(rec, dict) and rec.get("conf") in (1, True):
                raw_acct = str(acct).strip()
                if raw_acct:
                    conf_set.add(raw_acct)
                norm_acct = normalize_standing_hcad_token(raw_acct)
                if norm_acct:
                    conf_set.add(norm_acct)
    return conf_set


def _is_confidential_target(
    tkey: str,
    confidential_hcads: set[str],
    *,
    explicit_hcad: str = "",
    raw_props: dict[str, Any] | None = None,
) -> bool:
    """Return True if a candidate parcel/building carries a `TX Tax Code Sec. 25.025` confidential flag or HCAD."""
    if raw_props:
        if raw_props.get("conf") in (1, True) or raw_props.get("confidential") is True:
            return True
    if not confidential_hcads:
        return False
    for raw_tok in (explicit_hcad, tkey, tkey.split("#")[0]):
        if not raw_tok:
            continue
        s = str(raw_tok).strip()
        if s in confidential_hcads:
            return True
        norm = normalize_standing_hcad_token(s)
        if norm and norm in confidential_hcads:
            return True
    for m13 in re.finditer(r"(?<!\d)(\d{13})(?!\d)", str(tkey)):
        if m13.group(1) in confidential_hcads:
            return True
    return False


_CHURCH_FACILITY_RE = re.compile(r"\b(?:CHURCH|SANCTUARY|CHAPEL|PARISH|SYNAGOGUE|CATHEDRAL)\b")
_MEDICAL_FACILITY_RE = re.compile(r"\b(?:HOSPITAL|MEDICAL\s+CENTER|CLINIC|INFIRMARY)\b")
_AUX_SUBBUILDING_RE = re.compile(r"#aux_\d+$", re.IGNORECASE)


def run_archive_harvest(
    *,
    cache_dir: Path,
    output_dir: Path,
    apply: bool = False,
    offline: bool = False,
    cache_bust: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Harvest TARO 2.0 + HPL ContentDM architectural collections, match to parcels, and optionally apply."""
    effective_generated_at = sanitize_atlas_text(generated_at or datetime.date.today().isoformat())
    taro_colls, taro_items = harvest_taro_collections(cache_dir, offline=offline)
    hpl_colls, hpl_items = harvest_hpl_architectural_collections(cache_dir, offline=offline)

    all_colls = {**taro_colls, **hpl_colls}
    all_candidates: list[ArchivalCandidateItem] = []
    seen_cand_sigs: set[tuple[str, str, str, str]] = set()
    # Prioritize digitized visual HPL items first while deduplicating identical records
    for cand in (*hpl_items, *taro_items):
        sig = (
            cand.collection_key,
            cand.url.strip(),
            cand.title.strip().lower(),
            cand.container_or_ref.strip().lower(),
        )
        if sig in seen_cand_sigs:
            continue
        seen_cand_sigs.add(sig)
        all_candidates.append(cand)

    confidential_hcads = load_confidential_hcad_set(output_dir)

    # 1. Build lookup indexes across overrides, buildings.geojson, overlays (landmarks, good_brick, lost_houston), and countywide shards
    overrides_path = output_dir / "curated_overrides.json"
    overrides_doc = json.loads(overrides_path.read_text(encoding="utf-8")) if overrides_path.exists() else {"overrides": {}}
    overrides: dict[str, dict[str, Any]] = overrides_doc.get("overrides", {})

    buildings_path = output_dir / "buildings.geojson"
    buildings_fc = json.loads(buildings_path.read_text(encoding="utf-8")) if buildings_path.exists() else {"type": "FeatureCollection", "features": []}

    overlays_path = output_dir / "overlays.json"
    overlays_doc = json.loads(overlays_path.read_text(encoding="utf-8")) if overlays_path.exists() else {}

    search_idx_path = output_dir / "search_index.json"
    search_idx: list[dict[str, Any]] = json.loads(search_idx_path.read_text(encoding="utf-8")) if search_idx_path.exists() else []

    photos_path = output_dir / "building_photos.json"
    photos_doc = json.loads(photos_path.read_text(encoding="utf-8")) if photos_path.exists() else {"photos": []}

    # Address -> list of `(target_key, year_built, is_lost_houston, display_addr, display_name)`
    addr_to_targets: dict[str, list[tuple[str, int, bool, str, str]]] = defaultdict(list)
    landmark_name_to_targets: dict[str, list[tuple[str, int, bool, str, str]]] = defaultdict(list)
    # Client surname -> list of `(target_key, year_built, full_name_blob, display_addr)`
    client_surname_index: dict[str, list[tuple[str, int, str, str]]] = defaultdict(list)
    # Known standing building years by target key / parcel stem so `yr == 0` overlay registrations inherit known dates
    known_target_years: dict[str, int] = {}

    def _record_known_year(key: str, yr_val: int) -> None:
        if not key or yr_val <= 0:
            return
        prev = known_target_years.get(key, 0)
        known_target_years[key] = min(prev, yr_val) if prev > 0 else yr_val

    def _register_target(
        tkey: str,
        addr: str,
        yr: int,
        names: list[str],
        *,
        is_lost: bool = False,
        explicit_hcad: str = "",
        raw_props: dict[str, Any] | None = None,
    ) -> None:
        if not is_lost and _is_confidential_target(
            tkey,
            confidential_hcads,
            explicit_hcad=explicit_hcad,
            raw_props=raw_props,
        ):
            return
        if not is_lost and yr > 0:
            _record_known_year(tkey, yr)
            _record_known_year(tkey.split("#")[0], yr)
            norm_hc = normalize_standing_hcad_token(explicit_hcad or tkey)
            if norm_hc:
                _record_known_year(norm_hc, yr)
        disp_name = names[0] if names else ""
        if addr:
            k1, k2 = normalize_street_address_keys(addr)
            for ak in (k1, k2):
                if ak:
                    tup = (tkey, yr, is_lost, addr, disp_name)
                    if tup not in addr_to_targets[ak]:
                        addr_to_targets[ak].append(tup)
        for nm in names:
            nk = _normalize_landmark_key(nm)
            nk_words = nk.split()
            is_ambiguous_bare_house = (
                len(nk_words) == 2
                and nk_words[0] in COMMON_AMBIGUOUS_SURNAMES
                and nk_words[1] in {"RESIDENCE", "HOUSE", "HOME", "BUILDING"}
            )
            if (
                nk
                and len(nk) >= 7
                and nk not in GENERIC_LANDMARK_NAMES
                and len(nk_words) >= 2
                and not is_ambiguous_bare_house
            ):
                tup = (tkey, yr, is_lost, addr, nm)
                if tup not in landmark_name_to_targets[nk]:
                    landmark_name_to_targets[nk].append(tup)
            # Also index historic house client names from landmark names (e.g., "Dr. Irving Schweppe House")
            _phr, sur = _extract_client_residence_surname(nm)
            if sur and not is_lost:
                ctup = (tkey, yr, nk, addr)
                if ctup not in client_surname_index[sur]:
                    client_surname_index[sur].append(ctup)

    # Index Lost Houston demolished landmarks first (strictly isolated to `lost_*` IDs!)
    lost_fc = (overlays_doc.get("lost_houston") or {}).get("features") or []
    for feat in lost_fc:
        p = feat.get("properties") or {}
        lid = str(p.get("id") or p.get("lost_id") or "").strip()
        if not lid:
            continue
        if not lid.startswith("lost_"):
            lid = f"lost_{lid}"
        nm = str(p.get("name") or p.get("landmark_name") or p.get("building_name") or "").strip()
        alts = p.get("alt_names") if isinstance(p.get("alt_names"), list) else []
        yr = int(p.get("year_built") or 0)
        addr = str(p.get("address") or "").strip()
        _register_target(lid, addr, yr, [x for x in [nm, *alts] if x], is_lost=True)

    # Index curated_overrides.json and collect suppressed shard HCAD mappings
    suppressed_shard_to_override: dict[str, str] = {}
    for k, ov in overrides.items():
        if not isinstance(ov, dict) or ov.get("suppress_only"):
            continue
        for sup_hc in ov.get("suppress_shard_hcads") or []:
            sup_s = str(sup_hc).strip()
            if sup_s:
                suppressed_shard_to_override[sup_s] = k
        yr = int(ov.get("year_built") or 0)
        addr = str(ov.get("address") or "").strip()
        names = [
            str(ov.get(f) or "").strip()
            for f in ("building_name", "landmark_name", "name")
            if ov.get(f)
        ]
        if isinstance(ov.get("alt_names"), list):
            names.extend(str(a).strip() for a in ov["alt_names"] if a)
        _register_target(
            k,
            addr,
            yr,
            names,
            is_lost=False,
            explicit_hcad=str(ov.get("hcad_num") or ""),
            raw_props=ov,
        )

    # Index COH Landmarks & Good Brick Awards
    for layer_key in ("landmarks", "good_brick_awards"):
        for feat in (overlays_doc.get(layer_key) or {}).get("features") or []:
            p = feat.get("properties") or {}
            tkey = str(p.get("building_id") or p.get("hcad_num") or p.get("USER_HCAD_NUM") or "").strip()
            if not tkey:
                continue
            if tkey in suppressed_shard_to_override:
                tkey = suppressed_shard_to_override[tkey]
            yr = int(p.get("year_built") or p.get("building_year_built") or p.get("USER_YR_BUILT") or 0)
            addr = str(p.get("address") or p.get("site_address") or p.get("USER_ADDRESS") or "").strip()
            names = [
                str(p.get(f) or "").strip()
                for f in ("landmark_name", "project_name", "building_name", "USER_LANDMARK", "name")
                if p.get(f)
            ]
            _register_target(
                tkey,
                addr,
                yr,
                names,
                is_lost=False,
                explicit_hcad=str(p.get("hcad_num") or p.get("USER_HCAD_NUM") or ""),
                raw_props=p,
            )

    # Index Historic Core buildings.geojson
    for feat in buildings_fc.get("features", []):
        p = feat.get("properties") or {}
        tkey = str(p.get("hcad_num") or p.get("id") or "").strip()
        if not tkey or tkey in suppressed_shard_to_override or tkey.split("#")[0] in suppressed_shard_to_override:
            continue
        yr = int(p.get("year_built") or 0)
        addr = str(p.get("address") or "").strip()
        names = [str(p.get("building_name") or "").strip()] if p.get("building_name") else []
        if isinstance(p.get("alt_names"), list):
            names.extend(str(a).strip() for a in p["alt_names"] if a)
        _register_target(
            tkey,
            addr,
            yr,
            names,
            is_lost=False,
            explicit_hcad=str(p.get("hcad_num") or ""),
            raw_props=p,
        )

    # Scan countywide `aligned_ndjson/buildings_*.geojsonseq` shards ONLY for candidate street keys!
    # Collecting candidate street keys first lets us scan 1.51M buildings in ~4s with tiny memory.
    candidate_addr_keys: set[str] = set()
    item_extracted_addrs: dict[str, list[tuple[str, str, str]]] = {}
    for item in all_candidates:
        addrs = extract_candidate_addresses(f"{item.title} | {item.raw_location_text}")
        if addrs:
            item_extracted_addrs[item.item_id] = addrs
            for _disp, k1, k2 in addrs:
                if k1:
                    candidate_addr_keys.add(k1)
                if k2:
                    candidate_addr_keys.add(k2)

    shard_files = sorted(glob.glob(str(cache_dir / "aligned_ndjson" / "buildings_*.geojsonseq")))
    if candidate_addr_keys and shard_files:
        for sf in shard_files:
            with open(sf, encoding="utf-8") as f:
                for line in f:
                    s = line.strip()
                    if not s:
                        continue
                    if s[0] == "\x1e":
                        s = s[1:]
                    # Fast substring pre-check: skip JSON decode if line has no `"address":"`
                    if '"address"' not in s:
                        continue
                    try:
                        feat = json.loads(s)
                    except Exception:
                        continue
                    p = feat.get("properties") or {}
                    addr = str(p.get("address") or "").strip()
                    if not addr:
                        continue
                    k1, k2 = normalize_street_address_keys(addr)
                    if k1 not in candidate_addr_keys and k2 not in candidate_addr_keys:
                        continue
                    tkey = str(p.get("hcad_num") or p.get("id") or "").strip()
                    if (
                        not tkey
                        or tkey in suppressed_shard_to_override
                        or tkey.split("#")[0] in suppressed_shard_to_override
                        or _is_confidential_target(
                            tkey,
                            confidential_hcads,
                            explicit_hcad=str(p.get("hcad_num") or ""),
                            raw_props=p,
                        )
                    ):
                        continue
                    yr = int(p.get("year_built") or 0)
                    bname = str(p.get("building_name") or "").strip()
                    _register_target(
                        tkey,
                        addr,
                        yr,
                        [bname] if bname else [],
                        is_lost=False,
                        explicit_hcad=str(p.get("hcad_num") or ""),
                        raw_props=p,
                    )

    # Also index non-confidential building labels in `search_index.json` for rare client surnames in candidate items.
    candidate_client_surnames: dict[str, tuple[str, str]] = {}
    for item in all_candidates:
        if item.item_id in item_extracted_addrs:
            continue
        phr, sur = _extract_client_residence_surname(item.title)
        if sur:
            candidate_client_surnames[item.item_id] = (phr, sur)

    needed_surnames = {sur for _phr, sur in candidate_client_surnames.values()}
    if needed_surnames and (output_dir / "search_index.json").exists():
        non_building_types = {
            "subdivision",
            "neighborhood",
            "super_neighborhood",
            "historic_district",
            "heritage_district",
            "nrhp_district",
            "council_district",
            "ward",
            "holc_zone",
            "potential_hd",
            "owner_portfolio",
            "lost_houston",
        }
        for entry in search_idx:
            if (
                entry.get("is_neighborhood_entry")
                or entry.get("is_place_boundary")
                or str(entry.get("type") or "") in non_building_types
            ):
                continue
            tkey = str(entry.get("hcad_num") or entry.get("id") or "").strip()
            if (
                not tkey
                or tkey.startswith(("plat_", "nbhd_", "sn_", "ward_", "holc_", "port_", "lost_"))
                or _is_confidential_target(
                    tkey,
                    confidential_hcads,
                    explicit_hcad=str(entry.get("hcad_num") or ""),
                    raw_props=entry,
                )
            ):
                continue
            lbl = _normalize_landmark_key(str(entry.get("label") or entry.get("building_name") or ""))
            if not lbl:
                continue
            yr = int(entry.get("year_built") or 0)
            if yr > 0:
                _record_known_year(tkey, yr)
                _record_known_year(tkey.split("#")[0], yr)
            addr = str(entry.get("address") or "").strip()
            for tok in lbl.split():
                if tok in needed_surnames:
                    ctup = (tkey, yr, lbl, addr)
                    if ctup not in client_surname_index[tok]:
                        client_surname_index[tok].append(ctup)

    # 2. Execute Two-Tier Matching
    matched_items_dict: dict[str, dict[str, Any]] = {}
    matched_colls_used: set[str] = set()
    by_key: dict[str, list[str]] = defaultdict(list)
    by_addr: dict[str, list[str]] = defaultdict(list)
    tier_counts = {"address": 0, "landmark": 0, "client": 0, "skipped_replacement_anachronism": 0}
    target_best_architect: dict[str, tuple[str, int]] = {}  # tkey -> (architect, item_year)
    new_photo_candidates: list[dict[str, Any]] = []

    def _effective_target_year(tk_val: str, raw_yr: int, *, is_lost_flag: bool = False) -> int:
        if raw_yr > 0 or is_lost_flag:
            return raw_yr
        return (
            known_target_years.get(tk_val)
            or known_target_years.get(tk_val.split("#")[0])
            or 0
        )

    # Pre-sort landmark keys longest-first for high-specificity matching
    distinctive_landmark_keys = sorted(
        (k for k in landmark_name_to_targets if len(k) >= 9),
        key=len,
        reverse=True,
    )
    # Word-boundary regex index by rare token for fast landmark matching
    token_to_lm_keys: dict[str, list[str]] = defaultdict(list)
    stop_words = {"THE", "OF", "AND", "AT", "IN", "HOUSTON", "BUILDING", "HOUSE", "CENTER", "HALL", "SCHOOL", "CHURCH", "PARK", "COMPANY", "CO"}
    for lk in distinctive_landmark_keys:
        toks = [t for t in lk.split() if len(t) >= 4 and t not in stop_words]
        if toks:
            # Index by longest token in the landmark name
            anchor_tok = max(toks, key=len)
            token_to_lm_keys[anchor_tok].append(lk)

    for idx_num, item in enumerate(all_candidates, 1):
        matched_targets: list[tuple[str, int, bool, str, str, str]] = []  # (tkey, yr, is_lost, addr, name, tier)
        survey_ceiling_yr = HPL_COLLECTION_MAX_SURVEY_YEAR.get(item.collection_key)

        # Tier 1A: Street Address Match
        addrs = item_extracted_addrs.get(item.item_id) or []
        for _disp, k1, k2 in addrs:
            cands = addr_to_targets.get(k1) or addr_to_targets.get(k2) or []
            if not cands:
                continue
            extant_cands = [c for c in cands if not c[2]]
            lost_cands = [c for c in cands if c[2]]

            valid_extant: list[tuple[str, int, bool, str, str]] = []
            had_anachronism = False
            for c in extant_cands:
                _tk, b_yr, _isl, _ad, _nm = c
                eff_yr = _effective_target_year(_tk, b_yr, is_lost_flag=False)
                if item.year and eff_yr > 0 and eff_yr > item.year + 18:
                    had_anachronism = True
                    continue
                if not item.year and survey_ceiling_yr and eff_yr > 0 and eff_yr > survey_ceiling_yr + 5:
                    had_anachronism = True
                    continue
                if item.architect and eff_yr > 0 and not is_architect_within_career_window(item.architect, eff_yr):
                    had_anachronism = True
                    continue
                valid_extant.append((_tk, eff_yr, _isl, _ad, _nm))

            if valid_extant:
                for c in valid_extant[:3]:
                    matched_targets.append((*c, "address"))
            elif lost_cands:
                for c in lost_cands[:2]:
                    matched_targets.append((*c, "address"))
            elif had_anachronism:
                tier_counts["skipped_replacement_anachronism"] += 1

        # Tier 1B: Named Landmark / Campus Building Match (when no street address matched)
        if not matched_targets:
            norm_title = _normalize_landmark_key(item.title)
            # Strip trailing generic descriptors like "ARCHITECTURAL PLAN", "EXTERIOR OF", "SITE PLAN"
            norm_title_clean = re.sub(
                r"^(?:EXTERIOR\s+OF\s+(?:THE\s+)?|FRONT\s+EXTERIOR\s+OF\s+(?:THE\s+)?|RENDERING\s+OF\s+|SITE\s+PLAN\s+FOR\s+)",
                "",
                norm_title,
            )
            norm_title_clean = re.sub(
                r"\s+(?:ARCHITECTURAL\s+PLAN|FLOOR\s+PLAN|SITE\s+PLAN|EXTERIOR|RENDERING|DRAWINGS?|PHOTOGRAPHS?|NEWS\s+CLIPPING)$",
                "",
                norm_title_clean,
            ).strip()

            lm_hits: list[tuple[str, int, bool, str, str]] = []
            if norm_title_clean in landmark_name_to_targets and norm_title_clean not in GENERIC_LANDMARK_NAMES:
                lm_hits = landmark_name_to_targets[norm_title_clean]
            elif len(norm_title_clean) >= 9:
                title_words = sorted(set(norm_title_clean.split()))
                checked_lks: set[str] = set()
                title_is_church = bool(_CHURCH_FACILITY_RE.search(norm_title_clean))
                title_is_medical = bool(_MEDICAL_FACILITY_RE.search(norm_title_clean))
                for tw in title_words:
                    if tw in token_to_lm_keys:
                        for lk in token_to_lm_keys[tw]:
                            if lk in checked_lks or lk in GENERIC_LANDMARK_NAMES:
                                continue
                            checked_lks.add(lk)
                            if f" {lk} " in f" {norm_title_clean} ":
                                cand_list = landmark_name_to_targets[lk]
                                if len(cand_list) <= 3:
                                    for cand_item in cand_list:
                                        cand_nm_up = _normalize_landmark_key(cand_item[4])
                                        cand_is_church = bool(_CHURCH_FACILITY_RE.search(cand_nm_up))
                                        cand_is_medical = bool(_MEDICAL_FACILITY_RE.search(cand_nm_up))
                                        if (title_is_church and cand_is_medical and not cand_is_church) or (
                                            title_is_medical and cand_is_church and not cand_is_medical
                                        ):
                                            continue
                                        lm_hits.append(cand_item)

            if lm_hits and len({c[0].split("#")[0] for c in lm_hits}) <= 2:
                for c in lm_hits[:2]:
                    _tk, b_yr, is_lost, _ad, _nm = c
                    eff_yr = _effective_target_year(_tk, b_yr, is_lost_flag=is_lost)
                    if not is_lost and item.year and eff_yr > 0 and eff_yr > item.year + 20:
                        continue
                    if not is_lost and not item.year and survey_ceiling_yr and eff_yr > 0 and eff_yr > survey_ceiling_yr + 5:
                        continue
                    if not is_lost and item.architect and eff_yr > 0 and not is_architect_within_career_window(item.architect, eff_yr):
                        continue
                    matched_targets.append((_tk, eff_yr, is_lost, _ad, _nm, "landmark"))

        # Tier 2: Unambiguous Rare Client Residence Match
        if not matched_targets and item.item_id in candidate_client_surnames:
            full_phr, sur = candidate_client_surnames[item.item_id]
            cands = client_surname_index.get(sur) or []
            # Filter by era (`±10` years if item has year) and architect career window
            filtered_cands: list[tuple[str, int, str, str]] = []
            for tk, b_yr, nm_blob, ad in cands:
                eff_yr = _effective_target_year(tk, b_yr, is_lost_flag=False)
                if item.year and eff_yr > 0 and abs(eff_yr - item.year) > 10:
                    continue
                if item.architect and eff_yr > 0 and not is_architect_within_career_window(item.architect, eff_yr):
                    continue
                # Require multi-token given-name/initial corroboration on a unique parcel
                given_toks = [w for w in full_phr.split() if len(w) >= 3 and w != sur]
                has_given_corrob = any(g in nm_blob for g in given_toks)
                if has_given_corrob:
                    filtered_cands.append((tk, eff_yr, nm_blob, ad))
            unique_parcels = {c[0].split("#")[0] for c in filtered_cands}
            if len(unique_parcels) == 1 and filtered_cands:
                tk, b_yr, nm_blob, ad = filtered_cands[0]
                matched_targets.append((tk, b_yr, False, ad, nm_blob.title(), "client"))

        if not matched_targets:
            continue

        short_id = f"a{len(matched_items_dict) + 1}"
        tier_used = matched_targets[0][5]
        attached_any = False

        for tk, b_yr, is_lost, ad, nm, m_tier in matched_targets:
            if not is_lost and _is_confidential_target(tk, confidential_hcads):
                continue
            if short_id not in by_key[tk] and len(by_key[tk]) < 8:
                by_key[tk].append(short_id)
                attached_any = True
            base_hcad = tk.split("#")[0]
            if not is_lost and base_hcad != tk and short_id not in by_key[base_hcad] and len(by_key[base_hcad]) < 8:
                by_key[base_hcad].append(short_id)
                attached_any = True
            if not is_lost and ad:
                ak1, _ak2 = normalize_street_address_keys(ad)
                if ak1 and short_id not in by_addr[ak1] and len(by_addr[ak1]) < 8:
                    by_addr[ak1].append(short_id)
                    attached_any = True

            # Track Tier 1 verified architect attribution for backfill (requires explicit item.year and b_yr within <=10y delta)
            if (
                not is_lost
                and m_tier in ("address", "landmark")
                and item.architect
                and item.year
                and b_yr > 0
                and is_architect_within_career_window(item.architect, b_yr)
                and abs(item.year - b_yr) <= 10
                and tk not in target_best_architect
                and not _AUX_SUBBUILDING_RE.search(tk)
            ):
                target_best_architect[tk] = (
                    sanitize_atlas_text(item.architect),
                    int(item.year),
                )

            # Queue verified visual HPL ContentDM photographs/renderings for `building_photos.json`
            if item.is_visual_photo_or_rendering and m_tier in ("address", "landmark"):
                clean_hcad = "" if is_lost else normalize_standing_hcad_token(base_hcad)
                arch_suffix = (
                    f" (Architect: {item.architect})."
                    if item.architect and item.architect.lower() not in item.description.lower()
                    else "."
                )
                base_caption = f"{item.title}. {item.description}".strip(". ")
                max_base_len = max(40, 300 - len(arch_suffix))
                if len(base_caption) > max_base_len:
                    base_caption = (
                        base_caption[: max_base_len - 3].rsplit(" ", 1)[0].rstrip(".,;: ") + "..."
                    )
                new_photo_candidates.append({
                    "hcad_num": clean_hcad,
                    "building_id": tk,
                    "is_lost_houston": bool(is_lost),
                    "landmark_name": sanitize_atlas_text(nm or item.title),
                    "address": sanitize_atlas_text(ad),
                    "photo_year": item.year or (b_yr if b_yr > 0 else None),
                    "era_label": sanitize_atlas_text(
                        f"{item.date_label} HPL Archive" if item.date_label else "HPL Architectural Archive"
                    ),
                    "caption": sanitize_atlas_text(f"{base_caption}{arch_suffix}"),
                    "image_url": item.image_url,
                    "thumb_url": item.thumb_url or item.image_url,
                    "credit": sanitize_atlas_text(f"{item.collection_title} - Houston Public Library"),
                    "source_url": item.url,
                })

        if attached_any:
            tier_counts[tier_used] += 1
            matched_colls_used.add(item.collection_key)
            compact_rec: dict[str, Any] = {
                "c": item.collection_key,
                "t": sanitize_atlas_text(item.title),
                "u": item.url,
                "m": tier_used,
            }
            if item.container_or_ref:
                compact_rec["b"] = sanitize_atlas_text(item.container_or_ref)
            if item.date_label:
                compact_rec["d"] = sanitize_atlas_text(item.date_label)
            if item.year:
                compact_rec["y"] = int(item.year)
            if item.architect:
                compact_rec["a"] = sanitize_atlas_text(item.architect)
            if item.description:
                compact_rec["e"] = sanitize_atlas_text(item.description[:180])
            if item.thumb_url:
                compact_rec["img"] = item.thumb_url
            matched_items_dict[short_id] = compact_rec

    used_collections_payload = {
        ck: all_colls[ck] for ck in sorted(matched_colls_used) if ck in all_colls
    }

    archival_index_payload = {
        "version": 1,
        "generated_at": effective_generated_at,
        "taro_collections_scanned": len(taro_colls),
        "hpl_collections_scanned": len(hpl_colls),
        "collections": used_collections_payload,
        "items": matched_items_dict,
        "by_key": dict(sorted(by_key.items())),
        "by_addr": dict(sorted(by_addr.items())),
    }

    ov_arch_added = 0
    bld_arch_added = 0
    sidx_arch_added = 0
    photos_added = 0

    if apply:
        # 1. Write `app/public/data/archival_index.json`
        arch_idx_path = output_dir / "archival_index.json"
        arch_idx_path.write_text(
            sanitize_atlas_text(json.dumps(archival_index_payload, separators=(",", ":"))),
            encoding="utf-8",
        )

        # 2. Enrich `building_photos.json` (deduplicating by `image_url` and capping per parcel at 3 HPL collection photos)
        existing_photos: list[dict[str, Any]] = list(photos_doc.get("photos") or [])
        seen_photo_urls = {str(p.get("image_url") or "").strip() for p in existing_photos}
        per_target_photo_count: dict[str, int] = defaultdict(int)

        def _parcel_cap_key(p_obj: dict[str, Any]) -> str:
            if not p_obj.get("is_lost_houston"):
                hc = normalize_standing_hcad_token(
                    str(p_obj.get("hcad_num") or p_obj.get("building_id") or "")
                )
                if hc:
                    return f"hcad:{hc}"
                ak1, _ = normalize_street_address_keys(str(p_obj.get("address") or ""))
                if ak1:
                    return f"addr:{ak1}"
            return f"id:{str(p_obj.get('building_id') or '').strip()}"

        for existing_p in existing_photos:
            ex_pk = _parcel_cap_key(existing_p)
            ex_src = f"{existing_p.get('source_url') or ''} {existing_p.get('image_url') or ''}"
            if ex_pk and "p17006coll" in ex_src:
                per_target_photo_count[ex_pk] += 1
        for cand_p in new_photo_candidates:
            u = cand_p["image_url"]
            pk = _parcel_cap_key(cand_p)
            if not u or u in seen_photo_urls:
                continue
            if per_target_photo_count[pk] >= 3:
                continue
            seen_photo_urls.add(u)
            per_target_photo_count[pk] += 1
            existing_photos.append(cand_p)
            photos_added += 1

        if photos_added > 0:
            photos_doc["photos"] = existing_photos
            photos_path.write_text(
                sanitize_atlas_text(json.dumps(photos_doc, separators=(",", ":"))),
                encoding="utf-8",
            )

        # 3. Backfill missing `architect` on `curated_overrides.json`, `buildings.geojson`, and `search_index.json`
        def _resolve_backfill_architect(entity_id: str, hcad_num: str, year_built: int) -> str:
            if year_built <= 0:
                return ""
            if _AUX_SUBBUILDING_RE.search(entity_id):
                hit = target_best_architect.get(entity_id)
            else:
                base_id = entity_id.split("#")[0]
                hit = (
                    target_best_architect.get(entity_id)
                    or (target_best_architect.get(base_id) if base_id else None)
                    or (target_best_architect.get(hcad_num) if hcad_num else None)
                )
            if not hit:
                return ""
            arch_name, item_yr = hit[0], int(hit[1] or 0)
            if (
                item_yr > 0
                and abs(item_yr - year_built) <= 10
                and is_architect_within_career_window(arch_name, year_built)
            ):
                return arch_name
            return ""

        for k, ov in overrides.items():
            if not isinstance(ov, dict) or ov.get("suppress_only") or ov.get("architect"):
                continue
            arch_name = _resolve_backfill_architect(
                k,
                str(ov.get("hcad_num") or "").strip(),
                int(ov.get("year_built") or 0),
            )
            if arch_name:
                ov["architect"] = arch_name
                ov_arch_added += 1

        if ov_arch_added > 0:
            overrides_path.write_text(
                sanitize_atlas_text(json.dumps(overrides_doc, separators=(",", ":"))),
                encoding="utf-8",
            )

        for feat in buildings_fc.get("features", []):
            p = feat.get("properties") or {}
            if p.get("architect"):
                continue
            arch_name = _resolve_backfill_architect(
                str(p.get("id") or "").strip(),
                str(p.get("hcad_num") or "").strip(),
                int(p.get("year_built") or 0),
            )
            if arch_name:
                p["architect"] = arch_name
                bld_arch_added += 1

        if bld_arch_added > 0:
            buildings_path.write_text(
                sanitize_atlas_text(json.dumps(buildings_fc, separators=(",", ":"))),
                encoding="utf-8",
            )

        for entry in search_idx:
            if entry.get("is_neighborhood_entry") or entry.get("is_place_boundary") or entry.get("architect"):
                continue
            arch_name = _resolve_backfill_architect(
                str(entry.get("id") or "").strip(),
                str(entry.get("hcad_num") or "").strip(),
                int(entry.get("year_built") or 0),
            )
            if arch_name:
                entry["architect"] = arch_name
                sidx_arch_added += 1

        if sidx_arch_added > 0:
            search_idx_path.write_text(
                sanitize_atlas_text(json.dumps(search_idx, separators=(",", ":"))),
                encoding="utf-8",
            )

        if cache_bust:
            from atlas_pipeline.schema import (
                ACTIVE_TAX_YEAR,
                sync_repo_tax_year_and_cache_bust,
                update_pmtiles_manifest_v3,
            )

            repo_root = output_dir.parents[2]
            update_pmtiles_manifest_v3(output_dir / "pmtiles_manifest.json", cache_bust=cache_bust)
            sync_repo_tax_year_and_cache_bust(
                repo_root,
                tax_year=ACTIVE_TAX_YEAR,
                cache_bust=cache_bust,
            )

    return {
        "apply": bool(apply),
        "taro_collections_harvested": len(taro_colls),
        "taro_candidate_folders": len(taro_items),
        "hpl_collections_harvested": len(hpl_colls),
        "hpl_candidate_items": len(hpl_items),
        "matched_archival_items": len(matched_items_dict),
        "matched_collections_used": len(used_collections_payload),
        "matched_property_keys": len(by_key),
        "matched_street_addresses": len(by_addr),
        "tier_counts": tier_counts,
        "photos_added_to_building_photos": photos_added if apply else len(new_photo_candidates),
        "overrides_architect_backfilled": ov_arch_added,
        "buildings_geojson_architect_backfilled": bld_arch_added,
        "search_index_architect_backfilled": sidx_arch_added,
    }


def format_archive_harvest_cli_summary(stats: dict[str, Any], *, elapsed_s: float) -> str:
    mode_lbl = "APPLIED" if stats.get("apply") else "DRY-RUN"
    tc = stats.get("tier_counts") or {}
    return (
        f"=== Archival Harvest ({mode_lbl}) Complete in {elapsed_s:.1f}s ===\n"
        f"   TARO 2.0 collections / folders : {stats['taro_collections_harvested']:,} / {stats['taro_candidate_folders']:,}\n"
        f"   HPL ContentDM colls / items    : {stats['hpl_collections_harvested']:,} / {stats['hpl_candidate_items']:,}\n"
        f"   Matched archival items         : {stats['matched_archival_items']:,} "
        f"(address={tc.get('address', 0):,}, landmark={tc.get('landmark', 0):,}, client={tc.get('client', 0):,})\n"
        f"   Matched Atlas property keys    : {stats['matched_property_keys']:,} ({stats['matched_street_addresses']:,} street keys)\n"
        f"   HPL photos added / candidates  : {stats['photos_added_to_building_photos']:,}\n"
        f"   Architect backfills (ov/bld/sx): {stats['overrides_architect_backfilled']:,} / "
        f"{stats['buildings_geojson_architect_backfilled']:,} / {stats['search_index_architect_backfilled']:,}"
    )


if __name__ == "__main__":  # pragma: no cover
    import sys

    repo = Path(__file__).resolve().parents[2]
    do_apply = "--apply" in sys.argv
    do_offline = "--offline" in sys.argv
    t0 = time.time()
    res = run_archive_harvest(
        cache_dir=repo / "pipeline" / "cache",
        output_dir=repo / "app" / "public" / "data",
        apply=do_apply,
        offline=do_offline,
    )
    print(format_archive_harvest_cli_summary(res, elapsed_s=time.time() - t0))
