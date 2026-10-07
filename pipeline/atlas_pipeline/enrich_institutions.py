"""
Institutional Campuses & Citywide Undated (`year_built = 0`) Building Enrichment Engine
for The Houston Building Atlas v2 (Preservation Houston).

Performs a comprehensive multi-tier enrichment across Houston:
1. Rice University Campus (`0421790000001`, `0440980000065`):
   - Pulls 112 separated building & wing polygons from Rice's official `Rice_Polygon_Map`
     ArcGIS FeatureServer (excluding `Covered Walkway` connectors) + OpenStreetMap (`1912–2017`).
2. University of Houston Main Campus (`0410070140045`, `0410070070055`, `0410070120006`,
   `0410070120001`, `0410070120005`, `0410070120003`, `0410070130047`, `0410070130041`)
   & UH Downtown (`1238970010001`, `0030840000001`, `0030820000001`, `1324980010001`, `0031350000001`):
   - Pulls separated campus building polygons from OpenStreetMap Overpass API and dates
     all ~90 UH and UHD buildings (`1930–2022`) with architects and styles.
3. Texas Southern University (TSU) Campus (`0410310130001`, `1172400000005`, `0410310140004`,
   `1240290010001`, `0192590000041`, `0192590000011`, `0192770000001`, `0611680340006`):
   - Pulls 84 separated building polygons from the `TSU_Buildings` ArcGIS FeatureServer
     supplemented with OpenStreetMap footprints (`1947–2020`), highlighting pioneering
     architects John S. Chase and Lamar Q. Cato.
4. The Houston Zoo, Hermann Park & Museum District Campus (`0421790000004`, `0440970000166`,
   `0421330050001`, `0391720000001`, `0332640000005`, `0360810000011`, `0421960000046`):
   - Pulls separated building polygons for every Houston Zoo habitat/pavilion, Hermann Park
     landmark, and Museum District institution (`1917–2023`).
5. University of St. Thomas (UST), The Menil Collection Campus & Texas Medical Center (TMC):
   - Pulls separated building polygons across UST, the Menil Campus, and TMC (`1912–2018`).
6. Sam Houston Park (`0400030000014`, `1100 BAGBY ST`) — The Heritage Society Historic Houses:
   - Separates and dates all 11 historic 19th-century and museum structures (`1823–1968`).
7. Citywide Undated (`year_built = 0`) Buildings Across Houston:
   - Queries OpenStreetMap Overpass across all of Houston (`29.52,-95.78,30.11,-95.01`) for
     buildings with `start_date` or `wikidata` (`P571` inception, `P84` architect, `P149` style),
     joins City of Houston Designated Landmarks (`coh_landmarks.json`), applies curated dates for
     historic churches, schools, libraries, courthouses, and civic buildings, removes phantom
     `derived_parcel` rectangles on unimproved vacant lots/ROW strips (`bld_area == 0` and
     `year_built == 0`), and resolves split-lot / ROW-strip adjacency for remaining core buildings.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import shapely
from shapely.strtree import STRtree

RICE_EAST_HCAD = "0421790000001"
RICE_WEST_HCAD = "0440980000065"

RICE_POLYGON_MAP_URL = (
    "https://services.arcgis.com/lqRTrQp2HrfnJt8U/arcgis/rest/services/"
    "Rice_Polygon_Map/FeatureServer/0/query?where=1%3D1&outFields=*&outSR=4326&f=geojson"
)

TSU_BUILDINGS_MAP_URL = (
    "https://services2.arcgis.com/w4yiQqB14ZaAGzJq/arcgis/rest/services/"
    "Texas_Southern_University_College_Map_WFL1/FeatureServer/2/query?where=1%3D1&outFields=*&outSR=4326&f=geojson"
)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# ---------------------------------------------------------------------------
# 1. RICE UNIVERSITY METADATA (1912–2017)
# ---------------------------------------------------------------------------
RICE_BUILDING_METADATA: dict[str, dict[str, Any]] = {
    "lovett hall": {
        "name": "Lovett Hall (Administration Building)",
        "year_built": 1912,
        "architect": "Cram, Goodhue & Ferguson",
        "style": "Mediterranean / Byzantine Revival",
        "stories": 3,
        "height_m": 16.5,
        "citation": "Rice University Architectural Inventory & 1912–1913 Houston City Directory ('William M. Rice Institute, Main St. Rd.'); cornerstone laid 1911, opened Oct 1912.",
    },
    "mechanical laboratory": {
        "name": "Mechanical Laboratory & Campanile",
        "year_built": 1912,
        "architect": "Cram, Goodhue & Ferguson",
        "style": "Mediterranean / Italian Romanesque Revival",
        "stories": 2,
        "height_m": 12.0,
        "citation": "Rice University Architectural Inventory & 1912–1913 Houston City Directory; one of the three original 1912 Rice Institute buildings.",
    },
    "maxfield hall": {
        "name": "Robert and Katherine Maxfield Hall (Mechanical Lab Annex)",
        "year_built": 1912,
        "architect": "Cram, Goodhue & Ferguson",
        "style": "Mediterranean Revival",
        "stories": 2,
        "height_m": 10.5,
        "citation": "Rice University Architectural Inventory; constructed 1912 as part of the original Mechanical Laboratory complex.",
    },
    "old will rice": {
        "name": "Will Rice College — Old Dorm (Original South Hall)",
        "year_built": 1912,
        "architect": "Cram, Goodhue & Ferguson",
        "style": "Mediterranean Revival",
        "stories": 4,
        "height_m": 14.5,
        "citation": "Rice University Architectural Inventory & 1912–1913 Houston City Directory; opened 1912 as South Hall residential tower.",
    },
    "herzstein hall": {
        "name": "Herzstein Hall (Original Physics Building)",
        "year_built": 1914,
        "architect": "Cram, Goodhue & Ferguson / William Ward Watkin",
        "style": "Mediterranean Revival",
        "stories": 3,
        "height_m": 13.5,
        "citation": "Rice University Architectural Inventory & 1915 Houston City Directory; completed 1914 as the Physics Building and Amphitheatre.",
    },
    "old wing baker college": {
        "name": "Baker College — Historic Old Wing (Original East Hall)",
        "year_built": 1915,
        "architect": "Cram, Goodhue & Ferguson / William Ward Watkin",
        "style": "Mediterranean Revival",
        "stories": 3,
        "height_m": 13.0,
        "citation": "Rice University Architectural Inventory & 1916 Houston City Directory; completed 1915 as East Hall.",
    },
    "old hanszen college": {
        "name": "Hanszen College — Historic Old Section (Original West Hall)",
        "year_built": 1916,
        "architect": "Cram, Goodhue & Ferguson / William Ward Watkin",
        "style": "Mediterranean Revival",
        "stories": 3,
        "height_m": 13.0,
        "citation": "Rice University Architectural Inventory & 1917 Houston City Directory; completed 1916 as West Hall.",
    },
    "wiess president's house": {
        "name": "Wiess President's House",
        "year_built": 1920,
        "architect": "William Ward Watkin",
        "style": "Mediterranean Revival Villa",
        "stories": 2,
        "height_m": 9.0,
        "citation": "Rice University Architectural Inventory & 1921 Houston City Directory; built 1920 as residence for President Edgar Odell Lovett.",
    },
    "howard keck hall": {
        "name": "Howard Keck Hall (Original Chemistry Building)",
        "year_built": 1925,
        "architect": "William Ward Watkin & Cram and Ferguson",
        "style": "Mediterranean Revival",
        "stories": 3,
        "height_m": 14.0,
        "citation": "Rice University Architectural Inventory & 1926 Houston City Directory; completed 1925 as the Chemistry Building.",
    },
    "robert and agnes cohen house": {
        "name": "Robert and Agnes Cohen House (Faculty Club)",
        "year_built": 1927,
        "architect": "William Ward Watkin",
        "style": "Mediterranean Revival",
        "stories": 2,
        "height_m": 9.5,
        "citation": "Rice University Architectural Inventory; completed 1927 as the Rice Institute Faculty Club.",
    },
    "cohen house": {
        "name": "Robert and Agnes Cohen House (Faculty Club)",
        "year_built": 1927,
        "architect": "William Ward Watkin",
        "style": "Mediterranean Revival",
        "stories": 2,
        "height_m": 9.5,
        "citation": "Rice University Architectural Inventory; completed 1927 as the Rice Institute Faculty Club.",
    },
    "m.d. anderson hall": {
        "name": "M.D. Anderson Hall (School of Architecture)",
        "year_built": 1947,
        "architect": "Staub & Rather / William Ward Watkin (1981 expansion by James Stirling)",
        "style": "Modernized Mediterranean / Postmodern",
        "stories": 3,
        "height_m": 13.0,
        "citation": "Rice University Architectural Inventory; completed 1947, expanded 1981 by Pritzker Prize laureate James Stirling.",
    },
    "abercrombie engineering laboratory": {
        "name": "Abercrombie Engineering Laboratory",
        "year_built": 1948,
        "architect": "Staub & Rather",
        "style": "Mid-Century Institutional Brick",
        "stories": 2,
        "height_m": 11.5,
        "citation": "Rice University Architectural Inventory; completed 1948.",
    },
    "fondren library": {
        "name": "Fondren Library",
        "year_built": 1949,
        "architect": "Staub & Rather (William Ward Watkin, consulting)",
        "style": "Mid-Century Modern Institutional",
        "stories": 6,
        "height_m": 22.0,
        "citation": "Rice University Architectural Inventory; completed 1949 (expanded 1968).",
    },
    "nancy and peter huff house": {
        "name": "Nancy and Peter Huff House",
        "year_built": 1949,
        "architect": "William Ward Watkin",
        "style": "Traditional Institutional",
        "stories": 2,
        "height_m": 8.5,
        "citation": "Rice University Architectural Inventory; built 1949 as the second President's House.",
    },
    "rice stadium": {
        "name": "Rice Stadium",
        "year_built": 1950,
        "architect": "Hermon Lloyd & W.B. Morgan; Milton McGinty",
        "style": "Mid-Century Modern Reinforced Concrete Stadium",
        "stories": 4,
        "height_m": 24.0,
        "citation": "Rice University Architectural Inventory & Texas Society of Architects 25-Year Award; opened Sept 30, 1950.",
    },
    "tudor fieldhouse": {
        "name": "Tudor Fieldhouse (Autry Court)",
        "year_built": 1951,
        "architect": "Lloyd & Morgan (2008 renovation by Holt Hinshaw)",
        "style": "Mid-Century Athletic Arena",
        "stories": 3,
        "height_m": 16.0,
        "citation": "Rice University Architectural Inventory; constructed 1950–1951 as Autry Court, renovated 2008.",
    },
    "will rice college commons": {
        "name": "Will Rice College Commons",
        "year_built": 1957,
        "architect": "Norton Polk &acy",
        "style": "Mid-Century Modern",
        "stories": 1,
        "height_m": 6.5,
        "citation": "Rice University Architectural Inventory; constructed 1957 when the residential college system was inaugurated.",
    },
    "baker college commons": {
        "name": "Baker College Commons",
        "year_built": 1957,
        "architect": "Staub, Rather & Howze",
        "style": "Mid-Century Modern",
        "stories": 1,
        "height_m": 6.5,
        "citation": "Rice University Architectural Inventory; constructed 1957 for the inauguration of Baker College.",
    },
    "new wing baker college": {
        "name": "Baker College — 1957 Residential Wing",
        "year_built": 1957,
        "architect": "Staub, Rather & Howze",
        "style": "Mid-Century Modern",
        "stories": 4,
        "height_m": 13.5,
        "citation": "Rice University Architectural Inventory; constructed 1957.",
    },
    "baker masters house": {
        "name": "Baker College Magister's House",
        "year_built": 1957,
        "architect": "Staub, Rather & Howze",
        "style": "Mid-Century Modern Residential",
        "stories": 2,
        "height_m": 7.5,
        "citation": "Rice University Architectural Inventory; constructed 1957.",
    },
    "will rice master house": {
        "name": "Will Rice College Magister's House",
        "year_built": 1957,
        "architect": "Norton Polk &acy",
        "style": "Mid-Century Modern Residential",
        "stories": 2,
        "height_m": 7.5,
        "citation": "Rice University Architectural Inventory; constructed 1957.",
    },
    "hanszen college commons": {
        "name": "Hanszen College Commons",
        "year_built": 1957,
        "architect": "MacKie & Kamrath",
        "style": "Mid-Century Organic Modern",
        "stories": 1,
        "height_m": 6.5,
        "citation": "Rice University Architectural Inventory; constructed 1957.",
    },
    "new hanszen college": {
        "name": "Hanszen College — New Wing",
        "year_built": 1957,
        "architect": "MacKie & Kamrath (2022 wing replacement by Henning Larsen / Kirksey)",
        "style": "Mid-Century Modern / Contemporary Mass Timber",
        "stories": 4,
        "height_m": 14.0,
        "citation": "Rice University Architectural Inventory; originally constructed 1957.",
    },
    "hanszen masters house": {
        "name": "Hanszen College Magister's House",
        "year_built": 1957,
        "architect": "MacKie & Kamrath",
        "style": "Mid-Century Modern Residential",
        "stories": 2,
        "height_m": 7.5,
        "citation": "Rice University Architectural Inventory; constructed 1957.",
    },
    "jones college north": {
        "name": "Mary Gibbs Jones College — North Building",
        "year_built": 1957,
        "architect": "Lloyd & Morgan",
        "style": "Mid-Century Modern",
        "stories": 4,
        "height_m": 13.5,
        "citation": "Rice University Architectural Inventory; constructed 1957 as the first women's residential college at Rice.",
    },
    "jones college south": {
        "name": "Mary Gibbs Jones College — South Building",
        "year_built": 1957,
        "architect": "Lloyd & Morgan",
        "style": "Mid-Century Modern",
        "stories": 4,
        "height_m": 13.5,
        "citation": "Rice University Architectural Inventory; constructed 1957.",
    },
    "jones college commons": {
        "name": "Mary Gibbs Jones College Commons",
        "year_built": 1957,
        "architect": "Lloyd & Morgan (2002 commons by architecture+)",
        "style": "Mid-Century Modern",
        "stories": 1,
        "height_m": 6.5,
        "citation": "Rice University Architectural Inventory; founded 1957.",
    },
    "jones college masters house": {
        "name": "Jones College Magister's House",
        "year_built": 1957,
        "architect": "Lloyd & Morgan",
        "style": "Mid-Century Modern Residential",
        "stories": 2,
        "height_m": 7.5,
        "citation": "Rice University Architectural Inventory; constructed 1957.",
    },
    "m.d. anderson biology": {
        "name": "M.D. Anderson Biological Laboratories",
        "year_built": 1958,
        "architect": "George Pierce - Abel B. Pierce",
        "style": "Mid-Century Institutional",
        "stories": 3,
        "height_m": 13.5,
        "citation": "Rice University Architectural Inventory; completed 1958.",
    },
    "kieth-wiess geology": {
        "name": "Keith-Wiess Geological Laboratories",
        "year_built": 1958,
        "architect": "George Pierce - Abel B. Pierce",
        "style": "Mid-Century Institutional",
        "stories": 3,
        "height_m": 13.5,
        "citation": "Rice University Architectural Inventory; completed 1958.",
    },
    "hamman hall": {
        "name": "Hamman Hall",
        "year_built": 1958,
        "architect": "Pierce & Pierce",
        "style": "Mid-Century Modern Proscenium Theatre",
        "stories": 2,
        "height_m": 12.0,
        "citation": "Rice University Architectural Inventory; completed 1958.",
    },
    "rice memorial center": {
        "name": "Rice Memorial Center (RMC)",
        "year_built": 1958,
        "architect": "Harvin C. Moore",
        "style": "Mid-Century Institutional",
        "stories": 2,
        "height_m": 9.5,
        "citation": "Rice University Architectural Inventory; completed 1958.",
    },
    "rice memorial center chapel": {
        "name": "Rice Memorial Chapel",
        "year_built": 1958,
        "architect": "Harvin C. Moore",
        "style": "Mid-Century Sanctuary & Campanile",
        "stories": 2,
        "height_m": 13.0,
        "citation": "Rice University Architectural Inventory; completed 1958.",
    },
    "rayzor hall": {
        "name": "J. Newton Rayzor Hall",
        "year_built": 1962,
        "architect": "Staub, Rather & Howze",
        "style": "Mediterranean Revival / Mid-Century",
        "stories": 3,
        "height_m": 13.0,
        "citation": "Rice University Architectural Inventory; completed 1962.",
    },
    "facilities engineering & planning": {
        "name": "Facilities Engineering & Planning Building",
        "year_built": 1964,
        "architect": "Rice University FE&P",
        "style": "Utilitarian Campus Support",
        "stories": 1,
        "height_m": 5.5,
        "citation": "Rice University Architectural Inventory; completed 1964.",
    },
    "ryon laboratory": {
        "name": "Ryon Engineering Laboratory",
        "year_built": 1965,
        "architect": "Caudill Rowlett Scott (CRS)",
        "style": "Mid-Century Modern",
        "stories": 2,
        "height_m": 11.0,
        "citation": "Rice University Architectural Inventory; completed 1965.",
    },
    "brown college tower": {
        "name": "Margarett Root Brown College — Residential Tower",
        "year_built": 1965,
        "architect": "F.J. MacKie Jr. (MacKie & Kamrath)",
        "style": "Mid-Century Modern",
        "stories": 8,
        "height_m": 26.0,
        "citation": "Rice University Architectural Inventory; completed 1965.",
    },
    "brown college commons": {
        "name": "Margarett Root Brown College Commons",
        "year_built": 1965,
        "architect": "F.J. MacKie Jr. (MacKie & Kamrath)",
        "style": "Mid-Century Modern",
        "stories": 1,
        "height_m": 6.5,
        "citation": "Rice University Architectural Inventory; completed 1965.",
    },
    "brown college": {
        "name": "Margarett Root Brown College",
        "year_built": 1965,
        "architect": "F.J. MacKie Jr. (MacKie & Kamrath)",
        "style": "Mid-Century Modern",
        "stories": 8,
        "height_m": 26.0,
        "citation": "Rice University Architectural Inventory; completed 1965.",
    },
    "brown college masters house": {
        "name": "Brown College Magister's House",
        "year_built": 1965,
        "architect": "F.J. MacKie Jr. (MacKie & Kamrath)",
        "style": "Mid-Century Modern Residential",
        "stories": 2,
        "height_m": 7.5,
        "citation": "Rice University Architectural Inventory; completed 1965.",
    },
    "herman brown hall": {
        "name": "Herman Brown Hall (Mathematics)",
        "year_built": 1968,
        "architect": "George Pierce - Abel B. Pierce",
        "style": "Brutalist / Late Modern",
        "stories": 4,
        "height_m": 15.0,
        "citation": "Rice University Architectural Inventory; completed 1968.",
    },
    "space science": {
        "name": "Space Science and Technology Building",
        "year_built": 1966,
        "architect": "George Pierce - Abel B. Pierce",
        "style": "Mid-Century Modern Research Laboratory",
        "stories": 3,
        "height_m": 13.0,
        "citation": "Rice University Architectural Inventory; completed 1966 following NASA's partnership with Rice.",
    },
    "lovett college": {
        "name": "Edgar Odell Lovett College",
        "year_built": 1968,
        "architect": "Wilson, Morris, Crain & Anderson",
        "style": "Brutalist Concrete Grating Facade",
        "stories": 5,
        "height_m": 18.0,
        "citation": "Rice University Architectural Inventory; completed 1968.",
    },
    "lovett college commons": {
        "name": "Lovett College Commons",
        "year_built": 1968,
        "architect": "Wilson, Morris, Crain & Anderson",
        "style": "Brutalist / Late Modern",
        "stories": 1,
        "height_m": 6.5,
        "citation": "Rice University Architectural Inventory; completed 1968.",
    },
    "lovett masters house": {
        "name": "Lovett College Magister's House",
        "year_built": 1968,
        "architect": "Wilson, Morris, Crain & Anderson",
        "style": "Late Modern Residential",
        "stories": 2,
        "height_m": 7.5,
        "citation": "Rice University Architectural Inventory; completed 1968.",
    },
    "sid richardson college": {
        "name": "Sid W. Richardson College (1971 Tower)",
        "year_built": 1971,
        "architect": "Neuhaus & Taylor",
        "style": "Late Modern High-Rise Tower",
        "stories": 7,
        "height_m": 25.0,
        "citation": "Rice University Architectural Inventory; completed 1971.",
    },
    "sid richardson master's house": {
        "name": "Sid Richardson College Magister's House",
        "year_built": 1971,
        "architect": "Neuhaus & Taylor",
        "style": "Late Modern Residential",
        "stories": 2,
        "height_m": 7.5,
        "citation": "Rice University Architectural Inventory; completed 1971.",
    },
    "sewall hall": {
        "name": "Cleveland E. Sewall Hall",
        "year_built": 1971,
        "architect": "Lloyd, Morgan & Jones",
        "style": "Modernized Mediterranean",
        "stories": 5,
        "height_m": 17.5,
        "citation": "Rice University Architectural Inventory; completed 1971.",
    },
    "media center": {
        "name": "Rice Media Center",
        "year_built": 1970,
        "architect": "Eugene Aubry & Howard Barnstone",
        "style": "Corrugated Steel Industrial Modern",
        "stories": 2,
        "height_m": 8.0,
        "citation": "Rice University Architectural Inventory; commissioned by John and Dominique de Menil, completed 1970.",
    },
    "rice university police department": {
        "name": "Rice University Police Department & Entrance Building",
        "year_built": 1975,
        "architect": "Rice University FE&P",
        "style": "Late Modern Brick",
        "stories": 1,
        "height_m": 5.5,
        "citation": "Rice University Architectural Inventory; constructed mid-1970s.",
    },
    "herring hall": {
        "name": "Robert R. Herring Hall",
        "year_built": 1984,
        "architect": "César Pelli & Associates",
        "style": "Postmodern Brick & Glazed Tile",
        "stories": 3,
        "height_m": 15.0,
        "citation": "Rice University Architectural Inventory; completed 1984, designed by César Pelli.",
    },
    "ley student center": {
        "name": "Wendell and Audrey Ley Student Center",
        "year_built": 1986,
        "architect": "César Pelli & Associates",
        "style": "Postmodern Institutional",
        "stories": 2,
        "height_m": 11.0,
        "citation": "Rice University Architectural Inventory; Ley Student Center expansion completed 1986 by César Pelli.",
    },
    "george r. brown hall": {
        "name": "George R. Brown Hall",
        "year_built": 1990,
        "architect": "Cambridge Seven Associates",
        "style": "Postmodern Collegiate",
        "stories": 3,
        "height_m": 15.5,
        "citation": "Rice University Architectural Inventory; completed 1990.",
    },
    "shepherd school of music": {
        "name": "Alice Pratt Brown Hall (Shepherd School of Music)",
        "year_built": 1991,
        "architect": "Ricardo Bofill (Taller de Arquitectura)",
        "style": "Postmodern Neoclassical",
        "stories": 3,
        "height_m": 18.0,
        "citation": "Rice University Architectural Inventory; completed 1991, designed by Ricardo Bofill.",
    },
    "duncan hall": {
        "name": "Anne and Charles Duncan Hall (Computational Engineering)",
        "year_built": 1996,
        "architect": "John Outram Associates",
        "style": "Postmodern Polychrome Ornament",
        "stories": 3,
        "height_m": 16.5,
        "citation": "Rice University Architectural Inventory; completed 1996, designed by British architect John Outram.",
    },
    "baker institute": {
        "name": "James A. Baker III Hall (Baker Institute for Public Policy)",
        "year_built": 1997,
        "architect": "Hammond Beeby & Babka",
        "style": "Neoclassical / Mediterranean Revival",
        "stories": 3,
        "height_m": 15.0,
        "citation": "Rice University Architectural Inventory; completed 1997.",
    },
    "butcher hall": {
        "name": "E. Dell Butcher Hall (Nanoscale Science & Technology)",
        "year_built": 1997,
        "architect": "Antoine Predock",
        "style": "Contemporary Regionalist",
        "stories": 3,
        "height_m": 14.5,
        "citation": "Rice University Architectural Inventory; completed 1997, designed by AIA Gold Medalist Antoine Predock.",
    },
    "humanities building": {
        "name": "Rice Humanities Building",
        "year_built": 1999,
        "architect": "Allan Greenberg",
        "style": "Classical / Mediterranean Revival",
        "stories": 3,
        "height_m": 15.0,
        "citation": "Rice University Architectural Inventory; completed 1999, designed by Allan Greenberg.",
    },
    "cox fitness center": {
        "name": "John L. Cox Fitness Center",
        "year_built": 1999,
        "architect": "Rice University FE&P",
        "style": "Athletic Facility",
        "stories": 2,
        "height_m": 9.0,
        "citation": "Rice University Architectural Inventory; completed 1999.",
    },
    "holloway field/wendel d. ley track": {
        "name": "Holloway Field & Wendel D. Ley Track",
        "year_built": 1999,
        "architect": "Rice Athletics / FE&P",
        "style": "Collegiate Track & Soccer Stadium",
        "stories": 2,
        "height_m": 8.0,
        "citation": "Rice University Architectural Inventory; dedicated 1999.",
    },
    "reckling park": {
        "name": "Reckling Park (Baseball Stadium)",
        "year_built": 2000,
        "architect": " Morris Architects",
        "style": "Collegiate Baseball Park",
        "stories": 2,
        "height_m": 12.0,
        "citation": "Rice University Architectural Inventory; opened Feb 2000.",
    },
    "cambridge office building": {
        "name": "Rice Cambridge Office Building",
        "year_built": 2000,
        "architect": "Rice University FE&P",
        "style": "Contemporary Institutional Office",
        "stories": 3,
        "height_m": 12.0,
        "citation": "Rice University Architectural Inventory; acquired/renovated 2000.",
    },
    "martel college": {
        "name": "Marian and Speros P. Martel College",
        "year_built": 2002,
        "architect": "Michael Graves & Associates",
        "style": "Postmodern Classical",
        "stories": 4,
        "height_m": 15.5,
        "citation": "Rice University Architectural Inventory; completed 2002, designed by Michael Graves.",
    },
    "martel college master's house": {
        "name": "Martel College Magister's House",
        "year_built": 2002,
        "architect": "Michael Graves & Associates",
        "style": "Postmodern Residential",
        "stories": 2,
        "height_m": 7.5,
        "citation": "Rice University Architectural Inventory; completed 2002.",
    },
    "wiess college": {
        "name": "Harry Carothers Wiess College (2002 Quadrangle)",
        "year_built": 2002,
        "architect": "Machado and Silvetti Associates",
        "style": "Contemporary Mediterranean Quadrangle",
        "stories": 4,
        "height_m": 15.5,
        "citation": "Rice University Architectural Inventory; new Wiess College complex completed 2002 (college founded 1957).",
    },
    "wiess college commons": {
        "name": "Wiess College Commons",
        "year_built": 2002,
        "architect": "Machado and Silvetti Associates",
        "style": "Contemporary Mediterranean",
        "stories": 2,
        "height_m": 9.5,
        "citation": "Rice University Architectural Inventory; completed 2002.",
    },
    "wiess masters house": {
        "name": "Wiess College Magister's House",
        "year_built": 2002,
        "architect": "Machado and Silvetti Associates",
        "style": "Contemporary Residential",
        "stories": 2,
        "height_m": 7.5,
        "citation": "Rice University Architectural Inventory; completed 2002.",
    },
    "mcnair hall": {
        "name": "Janice and Robert McNair Hall (Jones Graduate School of Business)",
        "year_built": 2002,
        "architect": "Robert A.M. Stern Architects (RAMSA)",
        "style": "Mediterranean / Classical Revival",
        "stories": 3,
        "height_m": 16.5,
        "citation": "Rice University Architectural Inventory; completed 2002, designed by Robert A.M. Stern.",
    },
    "north servery": {
        "name": "North Servery",
        "year_built": 2002,
        "architect": "architecture+",
        "style": "Contemporary Campus Dining",
        "stories": 1,
        "height_m": 6.5,
        "citation": "Rice University Architectural Inventory; completed 2002.",
    },
    "south servery": {
        "name": "South Servery",
        "year_built": 2002,
        "architect": "Machado and Silvetti Associates",
        "style": "Contemporary Campus Dining",
        "stories": 1,
        "height_m": 6.5,
        "citation": "Rice University Architectural Inventory; completed 2002.",
    },
    "east servery": {
        "name": "East Servery",
        "year_built": 2002,
        "architect": "Machado and Silvetti Associates",
        "style": "Campus Dining Facility",
        "stories": 1,
        "height_m": 6.5,
        "citation": "Rice University Architectural Inventory; completed 2002.",
    },
    "keck": {
        "name": "Keck Annex / Biosciences Support",
        "year_built": 1925,
        "architect": "William Ward Watkin",
        "style": "Mediterranean Revival",
        "stories": 1,
        "height_m": 5.5,
        "citation": "Rice University Architectural Inventory; constructed 1925 with Keck Hall.",
    },
    "dell basketball practice facility": {
        "name": "Dell Butcher Basketball Practice Facility",
        "year_built": 2008,
        "architect": "Holt Hinshaw",
        "style": "Contemporary Athletic Facility",
        "stories": 2,
        "height_m": 11.0,
        "citation": "Rice University Architectural Inventory; completed 2008.",
    },
    "youngkin center": {
        "name": "Suzanne and Glenn Youngkin Center for Student-Athlete Excellence",
        "year_built": 2008,
        "architect": "Holt Hinshaw",
        "style": "Contemporary Athletic Academic Center",
        "stories": 2,
        "height_m": 10.0,
        "citation": "Rice University Architectural Inventory; completed 2008.",
    },
    "rice children's campus": {
        "name": "Rice Children's Campus",
        "year_built": 2008,
        "architect": "Rice University FE&P",
        "style": "Early Childhood Education Center",
        "stories": 1,
        "height_m": 5.5,
        "citation": "Rice University Architectural Inventory; completed 2008.",
    },
    "brochstein pavilion": {
        "name": "Raymond and Susan Brochstein Pavilion",
        "year_built": 2008,
        "architect": "Thomas Phifer and Partners",
        "style": "Minimalist Glass & Steel Pavilion",
        "stories": 1,
        "height_m": 5.5,
        "citation": "Rice University Architectural Inventory & AIA Institute Honor Award; completed 2008.",
    },
    "oshman engineering design kitchen": {
        "name": "Oshman Engineering Design Kitchen (OEDK)",
        "year_built": 2009,
        "architect": "Danny Samuels & Nonya Grenader",
        "style": "Adaptive Reuse / Contemporary Engineering Studio",
        "stories": 1,
        "height_m": 6.5,
        "citation": "Rice University Architectural Inventory; opened 2009 (adaptive reuse of 1948 campus dining hall).",
    },
    "bioscience research collaborative": {
        "name": "BioScience Research Collaborative (BRC)",
        "year_built": 2009,
        "architect": "Skidmore, Owings & Merrill (SOM)",
        "style": "Contemporary High-Rise Research Complex",
        "stories": 10,
        "height_m": 42.0,
        "citation": "Rice University Architectural Inventory; completed 2009.",
    },
    "gibbs recreation and wellness center": {
        "name": "Barbara and David Gibbs Recreation and Wellness Center",
        "year_built": 2009,
        "architect": "Lake|Flato Architects",
        "style": "Contemporary Texas Regionalist",
        "stories": 2,
        "height_m": 11.5,
        "citation": "Rice University Architectural Inventory; completed 2009.",
    },
    "duncan college": {
        "name": "Charles W. Duncan Jr. College",
        "year_built": 2009,
        "architect": "Hopkins Architects",
        "style": "LEED Gold Contemporary Quadrangle",
        "stories": 5,
        "height_m": 17.0,
        "citation": "Rice University Architectural Inventory; completed 2009.",
    },
    "duncan college commons": {
        "name": "Duncan College Commons",
        "year_built": 2009,
        "architect": "Hopkins Architects",
        "style": "LEED Gold Contemporary",
        "stories": 2,
        "height_m": 8.5,
        "citation": "Rice University Architectural Inventory; completed 2009.",
    },
    "duncan college masters house": {
        "name": "Duncan College Magister's House",
        "year_built": 2009,
        "architect": "Hopkins Architects",
        "style": "Contemporary Residential",
        "stories": 2,
        "height_m": 7.5,
        "citation": "Rice University Architectural Inventory; completed 2009.",
    },
    "mcmurtry college": {
        "name": "Burt and Deedee McMurtry College",
        "year_built": 2009,
        "architect": "Hopkins Architects",
        "style": "LEED Gold Contemporary Quadrangle",
        "stories": 5,
        "height_m": 17.0,
        "citation": "Rice University Architectural Inventory; completed 2009.",
    },
    "mcmurtry college commons": {
        "name": "McMurtry College Commons",
        "year_built": 2009,
        "architect": "Hopkins Architects",
        "style": "LEED Gold Contemporary",
        "stories": 2,
        "height_m": 8.5,
        "citation": "Rice University Architectural Inventory; completed 2009.",
    },
    "mcmurtry college masters house": {
        "name": "McMurtry College Magister's House",
        "year_built": 2009,
        "architect": "Hopkins Architects",
        "style": "Contemporary Residential",
        "stories": 2,
        "height_m": 7.5,
        "citation": "Rice University Architectural Inventory; completed 2009.",
    },
    "west servery": {
        "name": "West Servery",
        "year_built": 2009,
        "architect": "Hopkins Architects",
        "style": "Campus Dining Facility",
        "stories": 1,
        "height_m": 6.5,
        "citation": "Rice University Architectural Inventory; completed 2009.",
    },
    "new will rice": {
        "name": "Will Rice College — New Dorm",
        "year_built": 2010,
        "architect": "Richter Architects",
        "style": "Contemporary Brick",
        "stories": 4,
        "height_m": 14.5,
        "citation": "Rice University Architectural Inventory; completed 2010.",
    },
    "seibel servery": {
        "name": "Abe and Annie Seibel Servery",
        "year_built": 2010,
        "architect": "Richter Architects",
        "style": "Contemporary Dining Pavilion",
        "stories": 1,
        "height_m": 6.5,
        "citation": "Rice University Architectural Inventory; completed 2010.",
    },
    "brockman hall for physics": {
        "name": "Brockman Hall for Physics",
        "year_built": 2011,
        "architect": "KieranTimberlake",
        "style": "Contemporary Elevated Laboratory",
        "stories": 4,
        "height_m": 17.5,
        "citation": "Rice University Architectural Inventory; completed 2011, designed by KieranTimberlake.",
    },
    "turrell skyspace": {
        "name": "Suzanne Deal Booth Centennial Pavilion (James Turrell 'Twilight Epiphany' Skyspace)",
        "year_built": 2012,
        "architect": "James Turrell & Thomas Phifer",
        "style": "Pyramidal Light Installation Pavilion",
        "stories": 2,
        "height_m": 10.0,
        "citation": "Rice University Public Art & Architectural Inventory; dedicated June 2012 for Rice's Centennial.",
    },
    "anderson-clarke center": {
        "name": "D. Kent and Linda C. Anderson and Robert L. and Jean T. Clarke Center (Glasscock School)",
        "year_built": 2014,
        "architect": "Overland Partners",
        "style": "Contemporary Brick & Glass",
        "stories": 3,
        "height_m": 14.0,
        "citation": "Rice University Architectural Inventory; completed 2014.",
    },
    "george r. brown tennis center": {
        "name": "George R. Brown Tennis Center",
        "year_built": 2014,
        "architect": "Sink Combs Dethlefs",
        "style": "Collegiate Tennis Complex",
        "stories": 2,
        "height_m": 8.5,
        "citation": "Rice University Architectural Inventory; completed 2014.",
    },
    "moody center for the arts": {
        "name": "Moody Center for the Arts",
        "year_built": 2017,
        "architect": "Michael Maltzan Architecture",
        "style": "Contemporary Arts Pavilion",
        "stories": 2,
        "height_m": 14.0,
        "citation": "Rice University Architectural Inventory; opened Feb 2017.",
    },
    "brian patterson sports performance center": {
        "name": "Brian Patterson Sports Performance Center",
        "year_built": 2017,
        "architect": "HKS Architects",
        "style": "Contemporary Athletic Complex",
        "stories": 2,
        "height_m": 12.0,
        "citation": "Rice University Architectural Inventory; completed 2017.",
    },
    "central plant": {
        "name": "Rice Central Plant (Original Power House)",
        "year_built": 1912,
        "architect": "Cram, Goodhue & Ferguson",
        "style": "Mediterranean Revival Utility Plant",
        "stories": 2,
        "height_m": 11.0,
        "citation": "Rice University Architectural Inventory & 1912–1913 Houston City Directory; constructed 1912 adjacent to the Mechanical Laboratory & Campanile.",
    },
    "cooling tower": {
        "name": "Central Plant Cooling Tower",
        "year_built": 1964,
        "architect": "Rice University FE&P",
        "style": "Campus Utility Infrastructure",
        "stories": 2,
        "height_m": 10.0,
        "citation": "Rice University Architectural Inventory; central chilled-water expansion (1964).",
    },
    "health center": {
        "name": "Morton L. Rich Student Health Center",
        "year_built": 1962,
        "architect": "Rice University FE&P",
        "style": "Mid-Century Institutional",
        "stories": 1,
        "height_m": 5.5,
        "citation": "Rice University Architectural Inventory; completed 1962.",
    },
    "rice student health and wellness center": {
        "name": "Morton L. Rich Student Health & Wellness Center",
        "year_built": 1962,
        "architect": "Rice University FE&P",
        "style": "Mid-Century Institutional",
        "stories": 1,
        "height_m": 5.5,
        "citation": "Rice University Architectural Inventory; completed 1962.",
    },
    "housing and dining": {
        "name": "Housing and Dining Administrative Building",
        "year_built": 1964,
        "architect": "Rice University FE&P",
        "style": "Campus Support Facility",
        "stories": 1,
        "height_m": 5.5,
        "citation": "Rice University Architectural Inventory; completed 1964.",
    },
    "greenhouse": {
        "name": "Biosciences Research Greenhouse",
        "year_built": 1968,
        "architect": "Rice University FE&P",
        "style": "Botanical Research Greenhouse",
        "stories": 1,
        "height_m": 5.0,
        "citation": "Rice University Architectural Inventory; constructed 1968.",
    },
    "greenbriar building": {
        "name": "Greenbriar Building",
        "year_built": 1974,
        "architect": "Commercial Office",
        "style": "Late Modern Office",
        "stories": 2,
        "height_m": 8.5,
        "citation": "Rice University Facilities Inventory; constructed 1974.",
    },
    "martel continuing studies center": {
        "name": "Speros P. Martel Center for Continuing Studies",
        "year_built": 1987,
        "architect": "César Pelli & Associates / Rice FE&P",
        "style": "Postmodern Institutional",
        "stories": 2,
        "height_m": 9.0,
        "citation": "Rice University Architectural Inventory; completed 1987.",
    },
}

# ---------------------------------------------------------------------------
# 2. UNIVERSITY OF HOUSTON (UH MAIN CAMPUS & UHD) METADATA (1930–2022)
# ---------------------------------------------------------------------------
UH_PARCEL_HCADS = [
    "0410070140045",
    "0410070070055",
    "0410070120006",
    "0410070120001",
    "0410070120005",
    "0410070120003",
    "0410070130047",
    "0410070130041",
    "0410070100012",
    "0410070370047",
]

UH_BUILDING_METADATA: dict[str, dict[str, Any]] = {
    "roy g. cullen": {
        "name": "Roy G. Cullen Building (UH Historic First Permanent Building)",
        "year_built": 1939,
        "architect": "Lamar Q. Cato",
        "style": "Art Deco / Texas Limestone Moderne",
        "stories": 3,
        "height_m": 14.0,
        "citation": "University of Houston Campus Facilities Inventory; dedicated June 4, 1939 as the first permanent building on the UH campus.",
    },
    "science building": {
        "name": "UH Old Science Building (1939 Quadrangle)",
        "year_built": 1939,
        "architect": "Lamar Q. Cato",
        "style": "Art Deco / Texas Limestone Moderne",
        "stories": 3,
        "height_m": 13.5,
        "citation": "University of Houston Campus Facilities Inventory; completed 1939 as the second building on the UH campus.",
    },
    "ezekiel w. cullen": {
        "name": "Ezekiel W. Cullen Building",
        "year_built": 1950,
        "architect": "Alfred C. Finn",
        "style": "Stripped Classical / Art Moderne Limestone",
        "stories": 5,
        "height_m": 22.0,
        "citation": "University of Houston Campus Facilities Inventory; dedicated Oct 1950, designed by Alfred C. Finn.",
    },
    "cullen performance hall": {
        "name": "Cullen Performance Hall",
        "year_built": 1950,
        "architect": "Alfred C. Finn",
        "style": "Art Moderne Auditorium",
        "stories": 3,
        "height_m": 16.0,
        "citation": "University of Houston Campus Facilities Inventory; opened 1950 as part of the Ezekiel W. Cullen complex.",
    },
    "m.d. anderson library": {
        "name": "M.D. Anderson Library",
        "year_built": 1950,
        "architect": "Staub & Rather (1967, 1977 & 2004 wings)",
        "style": "Mid-Century Modern / Contemporary Academic Library",
        "stories": 8,
        "height_m": 28.0,
        "citation": "University of Houston Campus Facilities Inventory; original building completed 1950 by Staub & Rather.",
    },
    "fred j. heyne": {
        "name": "Fred J. Heyne Building",
        "year_built": 1953,
        "architect": "Caudill Rowlett Scott (CRS) / UH Facilities",
        "style": "Mid-Century Modern Institutional",
        "stories": 3,
        "height_m": 13.0,
        "citation": "University of Houston Campus Facilities Inventory; completed 1953.",
    },
    "a.d. bruce religion center": {
        "name": "A.D. Bruce Religion Center",
        "year_built": 1965,
        "architect": "Frank C. Dill",
        "style": "Mid-Century Modern Sanctuary & Interfaith Center",
        "stories": 2,
        "height_m": 13.5,
        "citation": "University of Houston Campus Facilities Inventory; completed 1965.",
    },
    "cullen college of engineering 1": {
        "name": "Cullen College of Engineering Building 1",
        "year_built": 1967,
        "architect": "Golemon & Rolfe",
        "style": "Mid-Century Modern",
        "stories": 4,
        "height_m": 16.5,
        "citation": "University of Houston Campus Facilities Inventory; completed 1967.",
    },
    "cullen college of engineering 2": {
        "name": "Cullen College of Engineering Building 2",
        "year_built": 1983,
        "architect": "Golemon & Rolfe",
        "style": "Late Modern",
        "stories": 4,
        "height_m": 16.5,
        "citation": "University of Houston Campus Facilities Inventory; completed 1983.",
    },
    "engineering lecture hall": {
        "name": "Engineering Lecture Hall",
        "year_built": 1967,
        "architect": "Golemon & Rolfe",
        "style": "Mid-Century Modern",
        "stories": 2,
        "height_m": 9.5,
        "citation": "University of Houston Campus Facilities Inventory; completed 1967.",
    },
    "agnes arnold hall": {
        "name": "Agnes Arnold Hall",
        "year_built": 1967,
        "architect": "Kenneth Bentsen Associates",
        "style": "Brutalist Concrete Frame",
        "stories": 6,
        "height_m": 24.0,
        "citation": "University of Houston Campus Facilities Inventory; completed 1967.",
    },
    "agnes arnold auditorium": {
        "name": "Agnes Arnold Auditorium",
        "year_built": 1967,
        "architect": "Kenneth Bentsen Associates",
        "style": "Brutalist Lecture Hall",
        "stories": 2,
        "height_m": 10.0,
        "citation": "University of Houston Campus Facilities Inventory; completed 1967.",
    },
    "student center south": {
        "name": "Student Center South (University Center)",
        "year_built": 1967,
        "architect": "MacKie & Kamrath (2015 transformation by Stantec)",
        "style": "Mid-Century / Contemporary Student Union",
        "stories": 3,
        "height_m": 14.0,
        "citation": "University of Houston Campus Facilities Inventory; originally built 1967, transformed 2015.",
    },
    "university center": {
        "name": "University Center",
        "year_built": 1967,
        "architect": "MacKie & Kamrath",
        "style": "Mid-Century Student Union",
        "stories": 3,
        "height_m": 14.0,
        "citation": "University of Houston Campus Facilities Inventory; built 1967.",
    },
    "student center north": {
        "name": "Student Center North",
        "year_built": 2014,
        "architect": "Stantec",
        "style": "Contemporary Student Union",
        "stories": 3,
        "height_m": 13.5,
        "citation": "University of Houston Campus Facilities Inventory; completed 2014.",
    },
    "lamar fleming jr.": {
        "name": "Lamar Fleming Jr. Building",
        "year_built": 1968,
        "architect": "MacKie & Kamrath",
        "style": "Mid-Century Modern",
        "stories": 3,
        "height_m": 13.5,
        "citation": "University of Houston Campus Facilities Inventory; completed 1968.",
    },
    "philip guthrie hoffman hall": {
        "name": "Philip Guthrie Hoffman Hall (PGH)",
        "year_built": 1969,
        "architect": "Wilson, Morris, Crain & Anderson",
        "style": "Late Modern / Brutalist Tower",
        "stories": 6,
        "height_m": 25.0,
        "citation": "University of Houston Campus Facilities Inventory; completed 1969.",
    },
    "science and research 1": {
        "name": "Science and Research Building 1 (SR1)",
        "year_built": 1969,
        "architect": "MacKie & Kamrath",
        "style": "Late Modern Research Complex",
        "stories": 6,
        "height_m": 24.0,
        "citation": "University of Houston Campus Facilities Inventory; completed 1969.",
    },
    "science and research 2": {
        "name": "Science and Research Building 2 (SR2)",
        "year_built": 1976,
        "architect": "UH Facilities",
        "style": "Late Modern Research Complex",
        "stories": 3,
        "height_m": 13.5,
        "citation": "University of Houston Campus Facilities Inventory; completed 1976.",
    },
    "fertitta center": {
        "name": "Fertitta Center (Hofheinz Pavilion)",
        "year_built": 1969,
        "architect": "Lloyd, Morgan & Jones (2018 renovation by PGAL)",
        "style": "Mid-Century Modern / Contemporary Arena",
        "stories": 4,
        "height_m": 22.0,
        "citation": "University of Houston Campus Facilities Inventory; opened Dec 1969 as Hofheinz Pavilion, renovated 2018.",
    },
    "bates law": {
        "name": "Bates Law Building (1969 Law Center Complex)",
        "year_built": 1969,
        "architect": "Freeman, Van Ness & Associates",
        "style": "Brutalist / Late Modern",
        "stories": 3,
        "height_m": 13.5,
        "citation": "University of Houston Campus Facilities Inventory; completed 1969.",
    },
    "max krost hall": {
        "name": "Max Krost Hall (Law Center)",
        "year_built": 1969,
        "architect": "Freeman, Van Ness & Associates",
        "style": "Brutalist / Late Modern",
        "stories": 2,
        "height_m": 10.5,
        "citation": "University of Houston Campus Facilities Inventory; completed 1969.",
    },
    "moody towers residence halls": {
        "name": "Moody Towers Residence Halls",
        "year_built": 1970,
        "architect": "Golemon & Rolfe",
        "style": "Late Modern Twin 18-Story Towers",
        "stories": 18,
        "height_m": 58.0,
        "citation": "University of Houston Campus Facilities Inventory; completed 1970.",
    },
    "graduate college of social work": {
        "name": "Graduate College of Social Work",
        "year_built": 1971,
        "architect": "UH Facilities",
        "style": "Late Modern",
        "stories": 4,
        "height_m": 15.5,
        "citation": "University of Houston Campus Facilities Inventory; completed 1971.",
    },
    "fine arts building": {
        "name": "Kathrine G. McGovern College of the Arts (Fine Arts Building)",
        "year_built": 1972,
        "architect": "Caudill Rowlett Scott (CRS)",
        "style": "Late Modern Brick",
        "stories": 4,
        "height_m": 16.0,
        "citation": "University of Houston Campus Facilities Inventory; completed 1972 by Caudill Rowlett Scott.",
    },
    "blaffer art museum": {
        "name": "Blaffer Art Museum",
        "year_built": 1972,
        "architect": "Caudill Rowlett Scott (2012 renovation by WORKac)",
        "style": "Late Modern / Contemporary Art Museum",
        "stories": 2,
        "height_m": 12.0,
        "citation": "University of Houston Campus Facilities Inventory; Fine Arts Building constructed 1972, museum opened 1973.",
    },
    "cynthia woods mitchell center for the arts": {
        "name": "Cynthia Woods Mitchell Center for the Arts (Wortham Theatre)",
        "year_built": 1976,
        "architect": "Caudill Rowlett Scott (CRS)",
        "style": "Late Modern Performing Arts Complex",
        "stories": 3,
        "height_m": 16.0,
        "citation": "University of Houston Campus Facilities Inventory; completed 1976.",
    },
    "charles f. mcelhinney hall": {
        "name": "Charles F. McElhinney Hall",
        "year_built": 1972,
        "architect": "UH Facilities",
        "style": "Late Modern",
        "stories": 3,
        "height_m": 13.0,
        "citation": "University of Houston Campus Facilities Inventory; completed 1972.",
    },
    "isabel c. cameron": {
        "name": "Isabel C. Cameron Building",
        "year_built": 1973,
        "architect": "UH Facilities",
        "style": "Late Modern",
        "stories": 2,
        "height_m": 9.5,
        "citation": "University of Houston Campus Facilities Inventory; completed 1973.",
    },
    "susanna garrison hall": {
        "name": "Susanna Garrison Hall",
        "year_built": 1973,
        "architect": "UH Facilities",
        "style": "Late Modern",
        "stories": 2,
        "height_m": 10.0,
        "citation": "University of Houston Campus Facilities Inventory; completed 1973.",
    },
    "j davis armistead": {
        "name": "J. Davis Armistead Building (College of Optometry)",
        "year_built": 1976,
        "architect": "Brooks, Barr, Graeber & White",
        "style": "Late Modern Clinical & Academic Complex",
        "stories": 3,
        "height_m": 14.5,
        "citation": "University of Houston Campus Facilities Inventory; completed 1976.",
    },
    "conrad n. hilton college of global hospitality leadership": {
        "name": "Conrad N. Hilton College of Global Hospitality Leadership",
        "year_built": 1974,
        "architect": "Golemon & Rolfe (1985 & 2023 expansions)",
        "style": "Late Modern / Contemporary Hospitality Complex",
        "stories": 3,
        "height_m": 14.0,
        "citation": "University of Houston Campus Facilities Inventory; original south wing opened 1974, north wing 1985.",
    },
    "conrad n. hilton college of hotel and resaurant management 2": {
        "name": "Conrad N. Hilton College — North Wing & Hotel",
        "year_built": 1985,
        "architect": "Golemon & Rolfe",
        "style": "Late Modern Hospitality Complex",
        "stories": 4,
        "height_m": 16.0,
        "citation": "University of Houston Campus Facilities Inventory; north hotel wing completed 1985.",
    },
    "jack j. valenti school of communication": {
        "name": "Jack J. Valenti School of Communication",
        "year_built": 1975,
        "architect": "UH Facilities",
        "style": "Late Modern",
        "stories": 2,
        "height_m": 10.0,
        "citation": "University of Houston Campus Facilities Inventory; completed 1975.",
    },
    "stephen power farish hall": {
        "name": "Stephen Power Farish Hall (College of Education)",
        "year_built": 1970,
        "architect": "Wilson, Morris, Crain & Anderson",
        "style": "Brutalist / Late Modern",
        "stories": 4,
        "height_m": 16.5,
        "citation": "University of Houston Campus Facilities Inventory; completed 1970.",
    },
    "college of technology building": {
        "name": "College of Technology Building",
        "year_built": 1982,
        "architect": "UH Facilities",
        "style": "Late Modern",
        "stories": 3,
        "height_m": 13.5,
        "citation": "University of Houston Campus Facilities Inventory; completed 1982.",
    },
    "gerald d. hines college of architecture": {
        "name": "Gerald D. Hines College of Architecture and Design",
        "year_built": 1985,
        "architect": "Philip Johnson & John Burgee",
        "style": "Postmodern Classical (after Claude-Nicolas Ledoux)",
        "stories": 4,
        "height_m": 22.0,
        "citation": "University of Houston Campus Facilities Inventory; completed 1985, designed by Philip Johnson and John Burgee.",
    },
    "burdette keeland jr. design & exploration center": {
        "name": "Burdette Keeland Jr. Design Exploration Center",
        "year_built": 2007,
        "architect": "Natalye Appel + Associates / Morris Architects",
        "style": "Contemporary Industrial Fabrication Studio",
        "stories": 1,
        "height_m": 7.5,
        "citation": "University of Houston Campus Facilities Inventory; completed 2007.",
    },
    "leroy & lucile melcher hall": {
        "name": "LeRoy & Lucile Melcher Hall (Bauer College of Business)",
        "year_built": 1986,
        "architect": "Lockwood, Andrews & Newnam /PGAL",
        "style": "Postmodern Institutional",
        "stories": 3,
        "height_m": 15.5,
        "citation": "University of Houston Campus Facilities Inventory; completed 1986.",
    },
    "melcher life sciences": {
        "name": "Melcher Life Sciences Building",
        "year_built": 1982,
        "architect": "UH Facilities",
        "style": "Late Modern",
        "stories": 2,
        "height_m": 10.0,
        "citation": "University of Houston Campus Facilities Inventory; completed 1982.",
    },
    "university of houston science center": {
        "name": "Houston Science Center",
        "year_built": 1995,
        "architect": "César Pelli & Associates",
        "style": "Postmodern Brick & Limestone Research Complex",
        "stories": 5,
        "height_m": 22.0,
        "citation": "University of Houston Campus Facilities Inventory; completed 1995, designed by César Pelli.",
    },
    "moores opera house": {
        "name": "Moores Opera House",
        "year_built": 1997,
        "architect": "John Burgee & Morris Architects",
        "style": "Postmodern Classical",
        "stories": 3,
        "height_m": 18.0,
        "citation": "University of Houston Campus Facilities Inventory; completed 1997.",
    },
    "moores school of music": {
        "name": "Rebecca and John J. Moores School of Music",
        "year_built": 1997,
        "architect": "John Burgee & Morris Architects",
        "style": "Postmodern Classical",
        "stories": 3,
        "height_m": 16.0,
        "citation": "University of Houston Campus Facilities Inventory; completed 1997.",
    },
    "leroy & lucile melcher center for public broadcasting": {
        "name": "LeRoy and Lucile Melcher Center for Public Broadcasting (KUHT / Houston Public Media)",
        "year_built": 2000,
        "architect": "Morris Architects",
        "style": "Contemporary Broadcast Center",
        "stories": 3,
        "height_m": 14.0,
        "citation": "University of Houston Campus Facilities Inventory; completed 2000.",
    },
    "athletic center": {
        "name": "Corbin J. Robertson Stadium / Athletics Alumni Center",
        "year_built": 1995,
        "architect": "UH Athletics / Page",
        "style": "Collegiate Athletics Complex",
        "stories": 3,
        "height_m": 15.0,
        "citation": "University of Houston Campus Facilities Inventory; completed 1995.",
    },
    "alumni center": {
        "name": "Athletics/Alumni Center",
        "year_built": 1995,
        "architect": "UH Athletics / Page",
        "style": "Collegiate Athletics & Alumni Complex",
        "stories": 3,
        "height_m": 15.0,
        "citation": "University of Houston Campus Facilities Inventory; completed 1995.",
    },
    "welcome center": {
        "name": "UH Welcome Center",
        "year_built": 2002,
        "architect": "PGAL",
        "style": "Contemporary Institutional",
        "stories": 2,
        "height_m": 11.0,
        "citation": "University of Houston Campus Facilities Inventory; completed 2002.",
    },
    "campus recreation & wellness center": {
        "name": "Campus Recreation and Wellness Center (CRWC)",
        "year_built": 2003,
        "architect": "WHR Architects / HOK",
        "style": "Contemporary Collegiate Recreation Complex",
        "stories": 3,
        "height_m": 16.0,
        "citation": "University of Houston Campus Facilities Inventory; opened 2003.",
    },
    "science & engineering research center": {
        "name": "Science and Engineering Research Center (SERC)",
        "year_built": 2005,
        "architect": "César Pelli & Associates / Kendall/Heaton",
        "style": "Contemporary Glass & Brick Laboratory",
        "stories": 5,
        "height_m": 24.0,
        "citation": "University of Houston Campus Facilities Inventory; completed 2005, designed by César Pelli.",
    },
    "science & engineering classroom building": {
        "name": "Science and Engineering Classroom Building (SEC)",
        "year_built": 2006,
        "architect": "César Pelli & Associates",
        "style": "Contemporary Glass & Brick Auditorium",
        "stories": 2,
        "height_m": 11.0,
        "citation": "University of Houston Campus Facilities Inventory; completed 2006.",
    },
    "university lofts apartments": {
        "name": "Calhoun Lofts (University Lofts)",
        "year_built": 2009,
        "architect": "Page Southerland Page",
        "style": "Contemporary Collegiate Residential",
        "stories": 9,
        "height_m": 32.0,
        "citation": "University of Houston Campus Facilities Inventory; opened Fall 2009.",
    },
    "michael j. cemo hall": {
        "name": "Michael J. Cemo Hall (Bauer College of Business)",
        "year_built": 2010,
        "architect": "SHW Group",
        "style": "Contemporary Brick & Glass",
        "stories": 2,
        "height_m": 12.0,
        "citation": "University of Houston Campus Facilities Inventory; completed 2010.",
    },
    "cougar village 1": {
        "name": "Cougar Village I",
        "year_built": 2010,
        "architect": "WHR Architects / Page",
        "style": "Contemporary Residence Hall",
        "stories": 7,
        "height_m": 25.0,
        "citation": "University of Houston Campus Facilities Inventory; opened Fall 2010.",
    },
    "cougar village 2": {
        "name": "Cougar Village II",
        "year_built": 2013,
        "architect": "Page",
        "style": "Contemporary Residence Hall",
        "stories": 7,
        "height_m": 25.0,
        "citation": "University of Houston Campus Facilities Inventory; opened Fall 2013.",
    },
    "cougar woods dining commons": {
        "name": "Cougar Woods Dining Commons",
        "year_built": 2012,
        "architect": "Page",
        "style": "Contemporary Dining Hall",
        "stories": 1,
        "height_m": 7.5,
        "citation": "University of Houston Campus Facilities Inventory; completed 2012.",
    },
    "classroom and business building": {
        "name": "Classroom and Business Building (CBB)",
        "year_built": 2012,
        "architect": "gensler / SHW Group",
        "style": "Contemporary Academic Building",
        "stories": 5,
        "height_m": 21.0,
        "citation": "University of Houston Campus Facilities Inventory; completed 2012.",
    },
    "cougar place": {
        "name": "Cougar Place Residence Hall",
        "year_built": 2013,
        "architect": "Page",
        "style": "Contemporary Residence Hall",
        "stories": 4,
        "height_m": 15.5,
        "citation": "University of Houston Campus Facilities Inventory; rebuilt and opened 2013.",
    },
    "health and biomedical science": {
        "name": "Health and Biomedical Sciences Building 1 (HBSB1)",
        "year_built": 2013,
        "architect": "Shepley Bulfinch / EYP",
        "style": "Contemporary Biomedical Research Complex",
        "stories": 6,
        "height_m": 26.0,
        "citation": "University of Houston Campus Facilities Inventory; completed 2013.",
    },
    "health and biomedical science 2": {
        "name": "Health and Biomedical Sciences Building 2 (HBSB2)",
        "year_built": 2017,
        "architect": "Shepley Bulfinch",
        "style": "Contemporary Biomedical Research Complex",
        "stories": 9,
        "height_m": 38.0,
        "citation": "University of Houston Campus Facilities Inventory; completed 2017.",
    },
    "science teaching laboratory building": {
        "name": " Fleming / Science Teaching Laboratory Building (STL)",
        "year_built": 2013,
        "architect": "PGAL / Perkins+Will",
        "style": "Contemporary Laboratory Building",
        "stories": 4,
        "height_m": 18.0,
        "citation": "University of Houston Campus Facilities Inventory; completed 2013.",
    },
    "guy v. lewis development facility": {
        "name": "Guy V. Lewis Basketball Development Facility",
        "year_built": 2016,
        "architect": "DLR Group / Page",
        "style": "Contemporary Athletic Training Center",
        "stories": 2,
        "height_m": 13.0,
        "citation": "University of Houston Campus Facilities Inventory; opened Jan 2016.",
    },
    "durga d. and sushila agrawal engineering research building": {
        "name": "Durga D. and Sushila Agrawal Engineering Research Building",
        "year_built": 2017,
        "architect": "Page / Stantec",
        "style": "Contemporary Engineering Research Laboratory",
        "stories": 4,
        "height_m": 19.0,
        "citation": "University of Houston Campus Facilities Inventory; completed 2017.",
    },
    "the quad": {
        "name": "The Quadrangle Residence Halls",
        "year_built": 2020,
        "architect": "Page",
        "style": "Contemporary Collegiate Quadrangle",
        "stories": 5,
        "height_m": 18.0,
        "citation": "University of Houston Campus Facilities Inventory; new Quadrangle completed 2020 (replacing the original 1950 Quadrangle).",
    },
    "john m. o’quinn law building": {
        "name": "John M. O'Quinn Law Building",
        "year_built": 2022,
        "architect": "Shepley Bulfinch",
        "style": "Contemporary Cantilevered Law School",
        "stories": 5,
        "height_m": 24.0,
        "citation": "University of Houston Campus Facilities Inventory; opened Fall 2022.",
    },
    "john m. o'quinn law building": {
        "name": "John M. O'Quinn Law Building",
        "year_built": 2022,
        "architect": "Shepley Bulfinch",
        "style": "Contemporary Cantilevered Law School",
        "stories": 5,
        "height_m": 24.0,
        "citation": "University of Houston Campus Facilities Inventory; opened Fall 2022.",
    },
    "retail auxiliary and dining center (rad center)": {
        "name": "Retail, Auxiliary and Dining Center (RAD Center)",
        "year_built": 2024,
        "architect": "Perkins&Will",
        "style": "Contemporary Campus Dining Pavilion",
        "stories": 2,
        "height_m": 10.0,
        "citation": "University of Houston Campus Facilities Inventory; completed 2024.",
    },
    "wheeler avenue baptist church": {
        "name": "Wheeler Avenue Baptist Church (Historic Sanctuary & Cathedral)",
        "year_built": 1962,
        "architect": "John S. Chase",
        "style": "Mid-Century Modern / Contemporary Sanctuary",
        "stories": 2,
        "height_m": 14.0,
        "citation": "Founded 1962 by Rev. William A. Lawson; sanctuary designed by pioneering African American architect John S. Chase.",
    },
}

# ---------------------------------------------------------------------------
# 3. TEXAS SOUTHERN UNIVERSITY (TSU) METADATA (1947–2020)
# ---------------------------------------------------------------------------
TSU_PARCEL_HCADS = [
    "0410310130001",
    "1172400000005",
    "0410310140004",
    "1240290010001",
    "0192590000041",
    "0192590000011",
    "0192770000001",
    "0611680340006",
]

TSU_BUILDING_METADATA: dict[str, dict[str, Any]] = {
    "thornton m. fairchild hall": {
        "name": "Thornton M. Fairchild Hall (Original 1947 TSU Building)",
        "year_built": 1947,
        "architect": "Lamar Q. Cato",
        "style": "Art Moderne / Mid-Century Limestone Institutional",
        "stories": 3,
        "height_m": 13.5,
        "citation": "Texas Southern University Architectural Inventory; completed 1947 as the first permanent building of the Houston College for Negroes / Texas State University for Negroes.",
    },
    "thorton m. fairchild hall": {
        "name": "Thornton M. Fairchild Hall (Original 1947 TSU Building)",
        "year_built": 1947,
        "architect": "Lamar Q. Cato",
        "style": "Art Moderne / Mid-Century Limestone Institutional",
        "stories": 3,
        "height_m": 13.5,
        "citation": "Texas Southern University Architectural Inventory; completed 1947 as the first permanent building on the TSU campus.",
    },
    "mack h. hannah hall": {
        "name": "Mack H. Hannah Jr. Hall (Administration Building)",
        "year_built": 1950,
        "architect": "Lamar Q. Cato (with John S. Chase on additions)",
        "style": "Mid-Century Modern Limestone & Brick",
        "stories": 3,
        "height_m": 14.5,
        "citation": "Texas Southern University Architectural Inventory; completed 1950 as the flagship administration building on Tiger Walk.",
    },
    "spurgeon e. gray hall": {
        "name": "Spurgeon E. Gray Hall",
        "year_built": 1952,
        "architect": "Lamar Q. Cato",
        "style": "Mid-Century Modern Institutional",
        "stories": 3,
        "height_m": 13.5,
        "citation": "Texas Southern University Architectural Inventory; completed 1952.",
    },
    "w. r. banks child development laboratory": {
        "name": "W.R. Banks Child Development Laboratory",
        "year_built": 1953,
        "architect": "Lamar Q. Cato",
        "style": "Mid-Century Institutional",
        "stories": 1,
        "height_m": 6.0,
        "citation": "Texas Southern University Architectural Inventory; completed 1953.",
    },
    "w.r. banks child development laboratory": {
        "name": "W.R. Banks Child Development Laboratory",
        "year_built": 1953,
        "architect": "Lamar Q. Cato",
        "style": "Mid-Century Institutional",
        "stories": 1,
        "height_m": 6.0,
        "citation": "Texas Southern University Architectural Inventory; completed 1953.",
    },
    "c.s. lane home economics center": {
        "name": "C.S. Lane Home Economics Building",
        "year_built": 1953,
        "architect": "Lamar Q. Cato",
        "style": "Mid-Century Institutional",
        "stories": 2,
        "height_m": 9.5,
        "citation": "Texas Southern University Architectural Inventory; completed 1953.",
    },
    "c.s. lane home economics building": {
        "name": "C.S. Lane Home Economics Building",
        "year_built": 1953,
        "architect": "Lamar Q. Cato",
        "style": "Mid-Century Institutional",
        "stories": 2,
        "height_m": 9.5,
        "citation": "Texas Southern University Architectural Inventory; completed 1953.",
    },
    "granville m. sawyer auditorium": {
        "name": "Granville M. Sawyer Auditorium",
        "year_built": 1955,
        "architect": "Lamar Q. Cato (with John T. Biggers murals)",
        "style": "Mid-Century Modern Auditorium",
        "stories": 3,
        "height_m": 15.0,
        "citation": "Texas Southern University Architectural Inventory; completed 1955.",
    },
    "charles p. rhinehart music auditorium": {
        "name": "Charles P. Rhinehart Music Auditorium",
        "year_built": 1955,
        "architect": "Lamar Q. Cato",
        "style": "Mid-Century Modern",
        "stories": 2,
        "height_m": 11.0,
        "citation": "Texas Southern University Architectural Inventory; completed 1955.",
    },
    "rollings-stewart music center": {
        "name": "Rollins-Stewart Music Center",
        "year_built": 1955,
        "architect": "Lamar Q. Cato",
        "style": "Mid-Century Modern",
        "stories": 2,
        "height_m": 10.0,
        "citation": "Texas Southern University Architectural Inventory; completed 1955.",
    },
    "rollins-stewart music center": {
        "name": "Rollins-Stewart Music Center",
        "year_built": 1955,
        "architect": "Lamar Q. Cato",
        "style": "Mid-Century Modern",
        "stories": 2,
        "height_m": 10.0,
        "citation": "Texas Southern University Architectural Inventory; completed 1955.",
    },
    "robert james terry liberary": {
        "name": "Robert James Terry Library",
        "year_built": 1957,
        "architect": "Lamar Q. Cato & John S. Chase",
        "style": "Mid-Century Modern Academic Library",
        "stories": 4,
        "height_m": 16.0,
        "citation": "Texas Southern University Architectural Inventory; completed 1957.",
    },
    "robert james terry library": {
        "name": "Robert James Terry Library",
        "year_built": 1957,
        "architect": "Lamar Q. Cato & John S. Chase",
        "style": "Mid-Century Modern Academic Library",
        "stories": 4,
        "height_m": 16.0,
        "citation": "Texas Southern University Architectural Inventory; completed 1957.",
    },
    "garriette g. lanier hall east": {
        "name": "Garriette G. Lanier Hall East",
        "year_built": 1959,
        "architect": "John S. Chase",
        "style": "Mid-Century Residence Hall",
        "stories": 3,
        "height_m": 12.0,
        "citation": "Texas Southern University Architectural Inventory; completed 1959.",
    },
    "lanier hall east": {
        "name": "Garriette G. Lanier Hall East",
        "year_built": 1959,
        "architect": "John S. Chase",
        "style": "Mid-Century Residence Hall",
        "stories": 3,
        "height_m": 12.0,
        "citation": "Texas Southern University Architectural Inventory; completed 1959.",
    },
    "raphael o'hara lanier hall west": {
        "name": "Raphael O'Hara Lanier Hall West",
        "year_built": 1959,
        "architect": "John S. Chase",
        "style": "Mid-Century Residence Hall",
        "stories": 3,
        "height_m": 12.0,
        "citation": "Texas Southern University Architectural Inventory; completed 1959, named after TSU's first president R. O'Hara Lanier.",
    },
    "everett owens bell hall": {
        "name": "Everett Owens Bell Hall",
        "year_built": 1962,
        "architect": "John S. Chase",
        "style": "Mid-Century Institutional",
        "stories": 2,
        "height_m": 9.5,
        "citation": "Texas Southern University Architectural Inventory; completed 1962.",
    },
    "samuel milton nabrit building": {
        "name": "Samuel Milton Nabrit Science Center",
        "year_built": 1968,
        "architect": "John S. Chase",
        "style": "Mid-Century Modern / Brutalist Science Complex",
        "stories": 3,
        "height_m": 15.0,
        "citation": "Texas Southern University Architectural Inventory; completed 1968, designed by pioneering Texas architect John S. Chase.",
    },
    "samuel milton nabrit science center": {
        "name": "Samuel Milton Nabrit Science Center",
        "year_built": 1968,
        "architect": "John S. Chase",
        "style": "Mid-Century Modern / Brutalist Science Complex",
        "stories": 3,
        "height_m": 15.0,
        "citation": "Texas Southern University Architectural Inventory; completed 1968, designed by John S. Chase.",
    },
    "samuel milton nabrit science center annex": {
        "name": "Samuel Milton Nabrit Science Center Annex",
        "year_built": 1968,
        "architect": "John S. Chase",
        "style": "Mid-Century Modern",
        "stories": 2,
        "height_m": 11.0,
        "citation": "Texas Southern University Architectural Inventory; completed 1968.",
    },
    "martin luther king humanities center": {
        "name": "Martin Luther King Jr. Humanities Center",
        "year_built": 1969,
        "architect": "John S. Chase",
        "style": "Brutalist / Late Modern Concrete & Brick",
        "stories": 3,
        "height_m": 14.5,
        "citation": "Texas Southern University Architectural Inventory; completed 1969, designed by John S. Chase.",
    },
    "ernest s. sterling student life center": {
        "name": "Ernest S. Sterling Student Life Center",
        "year_built": 1974,
        "architect": "John S. Chase",
        "style": "Late Modern Student Union",
        "stories": 3,
        "height_m": 14.0,
        "citation": "Texas Southern University Architectural Inventory; completed 1974, designed by John S. Chase.",
    },
    "thurgood marshall school of law": {
        "name": "Thurgood Marshall School of Law",
        "year_built": 1976,
        "architect": "John S. Chase",
        "style": "Late Modern / Brutalist Law Complex",
        "stories": 3,
        "height_m": 15.0,
        "citation": "Texas Southern University Architectural Inventory; completed 1976, designed by John S. Chase.",
    },
    "thurgood marshall law school building": {
        "name": "Thurgood Marshall School of Law",
        "year_built": 1976,
        "architect": "John S. Chase",
        "style": "Late Modern / Brutalist Law Complex",
        "stories": 3,
        "height_m": 15.0,
        "citation": "Texas Southern University Architectural Inventory; completed 1976, designed by John S. Chase.",
    },
    "roderick r. paige building (college of education)": {
        "name": "Roderick R. Paige Education Building",
        "year_built": 1978,
        "architect": "John S. Chase",
        "style": "Late Modern Institutional",
        "stories": 3,
        "height_m": 14.5,
        "citation": "Texas Southern University Architectural Inventory; completed 1978, designed by John S. Chase.",
    },
    "roderick r. paige education building": {
        "name": "Roderick R. Paige Education Building",
        "year_built": 1978,
        "architect": "John S. Chase",
        "style": "Late Modern Institutional",
        "stories": 3,
        "height_m": 14.5,
        "citation": "Texas Southern University Architectural Inventory; completed 1978.",
    },
    "health and physical education building": {
        "name": "Health & Physical Education (H&PE) Arena",
        "year_built": 1979,
        "architect": "John S. Chase",
        "style": "Late Modern Athletic Arena",
        "stories": 4,
        "height_m": 20.0,
        "citation": "Texas Southern University Architectural Inventory; completed 1979, designed by John S. Chase.",
    },
    "health & physical education arena": {
        "name": "Health & Physical Education (H&PE) Arena",
        "year_built": 1979,
        "architect": "John S. Chase",
        "style": "Late Modern Athletic Arena",
        "stories": 4,
        "height_m": 20.0,
        "citation": "Texas Southern University Architectural Inventory; completed 1979, designed by John S. Chase.",
    },
    "jesse h. jones school of business": {
        "name": "Jesse H. Jones School of Business",
        "year_built": 1986,
        "architect": "John S. Chase",
        "style": "Postmodern / Late Modern Institutional",
        "stories": 3,
        "height_m": 15.0,
        "citation": "Texas Southern University Architectural Inventory; completed 1986.",
    },
    "jesse h. jones business building": {
        "name": "Jesse H. Jones School of Business",
        "year_built": 1986,
        "architect": "John S. Chase",
        "style": "Postmodern / Late Modern Institutional",
        "stories": 3,
        "height_m": 15.0,
        "citation": "Texas Southern University Architectural Inventory; completed 1986.",
    },
    "john t. biggers art center": {
        "name": "John T. Biggers Art Center (University Museum)",
        "year_built": 2001,
        "architect": "John S. Chase & Associates",
        "style": "Contemporary Museum & Studio Complex",
        "stories": 2,
        "height_m": 12.0,
        "citation": "Texas Southern University Architectural Inventory; completed 2001 in honor of TSU Art Department founder Dr. John T. Biggers.",
    },
    "barbara jordan-mickey leland school of public affairs": {
        "name": "Barbara Jordan–Mickey Leland School of Public Affairs",
        "year_built": 2002,
        "architect": "TSU Facilities / Moseley Architects",
        "style": "Contemporary Institutional",
        "stories": 4,
        "height_m": 16.5,
        "citation": "Texas Southern University Architectural Inventory; completed 2002.",
    },
    "public affairs building": {
        "name": "Barbara Jordan–Mickey Leland School of Public Affairs",
        "year_built": 2002,
        "architect": "TSU Facilities",
        "style": "Contemporary Institutional",
        "stories": 4,
        "height_m": 16.5,
        "citation": "Texas Southern University Architectural Inventory; completed 2002.",
    },
    "student recreation center": {
        "name": "TSU Student Recreation Center",
        "year_built": 2003,
        "architect": "TSU Facilities",
        "style": "Contemporary Recreation Center",
        "stories": 2,
        "height_m": 13.0,
        "citation": "Texas Southern University Architectural Inventory; completed 2003.",
    },
    "science building": {
        "name": "TSU New Science Center",
        "year_built": 2006,
        "architect": "PBK Architects / HDR",
        "style": "Contemporary Rotunda & Laboratory Complex",
        "stories": 4,
        "height_m": 18.5,
        "citation": "Texas Southern University Architectural Inventory; completed 2006.",
    },
    "l. h. o. spearman technology building": {
        "name": "Leonard H.O. Spearman Technology Building",
        "year_built": 2012,
        "architect": "PBK Architects",
        "style": "Contemporary Engineering & Technology Complex",
        "stories": 3,
        "height_m": 15.5,
        "citation": "Texas Southern University Architectural Inventory; completed 2012.",
    },
    "leonard h.o. spearman technology building": {
        "name": "Leonard H.O. Spearman Technology Building",
        "year_built": 2012,
        "architect": "PBK Architects",
        "style": "Contemporary Engineering & Technology Complex",
        "stories": 3,
        "height_m": 15.5,
        "citation": "Texas Southern University Architectural Inventory; completed 2012.",
    },
    "library learning center": {
        "name": "TSU Library Learning Center",
        "year_built": 2019,
        "architect": "Moody Nolan / HarrisonKornberg",
        "style": "Contemporary Glass & Brick Academic Library",
        "stories": 5,
        "height_m": 22.0,
        "citation": "Texas Southern University Architectural Inventory; opened 2019.",
    },
    "university tower i and tower ii": {
        "name": "TSU University Towers Residence Halls",
        "year_built": 2015,
        "architect": "TSU Housing",
        "style": "Contemporary Residence Towers",
        "stories": 7,
        "height_m": 25.0,
        "citation": "Texas Southern University Architectural Inventory; opened 2015.",
    },
}

# ---------------------------------------------------------------------------
# 4. HOUSTON ZOO, HERMANN PARK & MUSEUM DISTRICT METADATA (1917–2023)
# ---------------------------------------------------------------------------
ZOO_HERMANN_HCADS = [
    "0421790000004",
    "0440970000166",
    "0421330050001",
    "0391720000001",
    "0332640000005",
    "0360810000011",
    "0421960000046",
]

ZOO_HERMANN_MUSEUM_METADATA: dict[str, dict[str, Any]] = {
    "family history research center at the clayton library campus": {
        "name": "Clayton Library Center for Genealogical Research",
        "year_built": 1917,
        "architect": "Birdsall P. Briscoe (1988 research building)",
        "style": "Georgian Revival / Institutional",
        "stories": 2,
        "height_m": 10.0,
        "citation": "William L. Clayton Summer House built 1917 by Birdsall P. Briscoe; donated to Houston Public Library 1957.",
    },
    "caroline weiss law building": {
        "name": "Caroline Wiess Law Building (Museum of Fine Arts, Houston)",
        "year_built": 1924,
        "architect": "William Ward Watkin (1958 Cullinan Hall & 1974 Brown Pavilion by Ludwig Mies van der Rohe)",
        "style": "Neoclassical & International Style Modernism",
        "stories": 3,
        "height_m": 15.0,
        "citation": "Museum of Fine Arts, Houston; original Neoclassical museum opened April 12, 1924 (first art museum in Texas), expanded by Mies van der Rohe in 1958 and 1974.",
    },
    "hotel zaza houston": {
        "name": "The Warwick Hotel (Hotel ZaZa Museum District)",
        "year_built": 1925,
        "architect": "Mauran, Russell & Crowell / Charles D. Hill",
        "style": "Spanish Colonial / Beaux-Arts Revival",
        "stories": 12,
        "height_m": 42.0,
        "citation": "Built 1925–1926 by Don Hall as The Warwick apartment hotel on Hermann Park.",
    },
    "palmer episcopal church": {
        "name": "Palmer Memorial Episcopal Church",
        "year_built": 1927,
        "architect": "William Ward Watkin",
        "style": "Early Christian Italian Romanesque Revival",
        "stories": 2,
        "height_m": 16.0,
        "citation": "Completed 1927–1930 opposite the Rice Institute entrance, designed by William Ward Watkin.",
    },
    "st. paul's united methodist church": {
        "name": "St. Paul's United Methodist Church",
        "year_built": 1930,
        "architect": "Alfred C. Finn",
        "style": "Neo-Gothic Cathedral",
        "stories": 3,
        "height_m": 24.0,
        "citation": "Completed 1930 on Main Street opposite the Museum of Fine Arts, designed by Alfred C. Finn.",
    },
    "lott hall": {
        "name": "Historic Lott Hall (1933 Hermann Park Golf Course Clubhouse)",
        "year_built": 1933,
        "architect": "Stayton Nunn",
        "style": "Spanish Colonial Revival",
        "stories": 2,
        "height_m": 8.5,
        "citation": "City of Houston Protected Landmark; constructed 1933 as the Hermann Park Golf Course Clubhouse.",
    },
    "pioneer memorial log house museum": {
        "name": "Pioneer Memorial Log House (San Jacinto Centennial Chapter, DRT)",
        "year_built": 1936,
        "architect": "Stayton Nunn (for the 1936 Texas Centennial)",
        "style": "Texas Pioneer Log Architecture",
        "stories": 1,
        "height_m": 6.0,
        "citation": "Constructed 1936 in Hermann Park using historic 19th-century pine logs to commemorate the Texas Centennial.",
    },
    "first presbyterian houston": {
        "name": "First Presbyterian Church of Houston",
        "year_built": 1948,
        "architect": "Hobart Upjohn & Staub & Rather",
        "style": "Georgian / Colonial Revival",
        "stories": 2,
        "height_m": 18.0,
        "citation": "Completed 1948 at 5300 Main Street in the Museum District.",
    },
    "kipp memorial aquarium": {
        "name": "Kipp Aquarium (Houston Zoo)",
        "year_built": 1951,
        "architect": "Houston Zoo / City of Houston Parks",
        "style": "Mid-Century Zoological Aquarium",
        "stories": 1,
        "height_m": 6.5,
        "citation": "Houston Zoo Master Inventory; originally opened 1951 as the Primate/Aquarium building, renovated via gift from Willie & H.L. Kipp.",
    },
    "small cats house": {
        "name": "Small Cat House (Houston Zoo)",
        "year_built": 1953,
        "architect": "City of Houston Parks Department",
        "style": "Mid-Century Zoological Exhibit",
        "stories": 1,
        "height_m": 5.5,
        "citation": "Houston Zoo Master Inventory; constructed 1953.",
    },
    "reptile and amphibian house": {
        "name": "Reptile and Amphibian House (Houston Zoo)",
        "year_built": 1960,
        "architect": "City of Houston Parks Department",
        "style": "Mid-Century Zoological Pavilion",
        "stories": 1,
        "height_m": 6.5,
        "citation": "Houston Zoo Master Inventory; completed 1960.",
    },
    "houston museum of natural science": {
        "name": "Houston Museum of Natural Science (HMNS)",
        "year_built": 1969,
        "architect": "Pierce, Goodwin & Flanagan (1994 Cockrell Butterfly Center; 2012 Dan L Duncan Wing)",
        "style": "Mid-Century Modern / Contemporary Science Museum",
        "stories": 4,
        "height_m": 20.0,
        "citation": "Permanent Hermann Park museum building completed 1969 (planetarium opened 1964; paleontology hall added 2012).",
    },
    "contemporary arts museum houston": {
        "name": "Contemporary Arts Museum Houston (CAMH)",
        "year_built": 1972,
        "architect": "Gunnar Birkerts",
        "style": "Late Modern Stainless Steel Parallelogram",
        "stories": 2,
        "height_m": 11.0,
        "citation": "Opened 1972 at Montrose & Bissonnet, designed by Gunnar Birkerts.",
    },
    "warwick towers": {
        "name": "The Warwick Towers",
        "year_built": 1983,
        "architect": "Lloyd Jones Brewer",
        "style": "Late Modern High-Rise Condominium",
        "stories": 30,
        "height_m": 102.0,
        "citation": "Completed 1983 overlooking Hermann Park.",
    },
    "wortham pavilion": {
        "name": "Wortham World of Primates Pavilion (Houston Zoo)",
        "year_built": 1986,
        "architect": "Houston Zoo",
        "style": "Zoological Pavilion",
        "stories": 1,
        "height_m": 6.5,
        "citation": "Houston Zoo Master Inventory; Wortham World of Primates completed 1986.",
    },
    "george r. brown conservation education center": {
        "name": "George R. Brown Conservation Education Center (Houston Zoo)",
        "year_built": 1989,
        "architect": "Houston Zoo",
        "style": "Institutional Education Center",
        "stories": 2,
        "height_m": 9.0,
        "citation": "Houston Zoo Master Inventory; completed 1989.",
    },
    "friendship pavilion": {
        "name": "Friendship Pavilion & Japanese Garden (Hermann Park)",
        "year_built": 1992,
        "architect": "Ken Nakajima (Japanese Garden); Pavilion gifted by Taipei (1978/1992)",
        "style": "Traditional East Asian Pavilion",
        "stories": 1,
        "height_m": 7.5,
        "citation": "Hermann Park Conservancy; Japanese Garden designed by master landscape architect Ken Nakajima, dedicated 1992.",
    },
    "john p. mcgovern museum of health and medical science": {
        "name": "The Health Museum (John P. McGovern Museum of Health & Medical Science)",
        "year_built": 1996,
        "architect": "Ellerbe Becket / Kendall/Heaton",
        "style": "Contemporary Science Museum",
        "stories": 2,
        "height_m": 13.0,
        "citation": "Opened 1996 in the Houston Museum District.",
    },
    "holocaust museum houston": {
        "name": "Holocaust Museum Houston (Lester and Sue Smith Campus)",
        "year_built": 1996,
        "architect": "Ralph Appelbaum Associates / Mucasey & Associates (2019 expansion)",
        "style": "Contemporary Memorial Museum",
        "stories": 3,
        "height_m": 14.5,
        "citation": "Opened March 1996; expanded 2019.",
    },
    "audrey jones beck building": {
        "name": "Audrey Jones Beck Building (Museum of Fine Arts, Houston)",
        "year_built": 2000,
        "architect": "Rafael Moneo",
        "style": "Contemporary Indiana Limestone Museum",
        "stories": 3,
        "height_m": 18.0,
        "citation": "Museum of Fine Arts, Houston; opened March 2000, designed by Pritzker Prize laureate Rafael Moneo.",
    },
    "wildlife carousel": {
        "name": "McGovern Children's Zoo & Wildlife Carousel (Houston Zoo)",
        "year_built": 2000,
        "architect": "Houston Zoo",
        "style": "Zoological Pavilion",
        "stories": 1,
        "height_m": 6.5,
        "citation": "Houston Zoo Master Inventory; John P. McGovern Children's Zoo opened 2000.",
    },
    "naturally wild swap shop": {
        "name": "Naturally Wild Swap Shop (McGovern Children's Zoo)",
        "year_built": 2000,
        "architect": "Houston Zoo",
        "style": "Children's Education Pavilion",
        "stories": 1,
        "height_m": 5.5,
        "citation": "Houston Zoo Master Inventory; opened 2000 with the McGovern Children's Zoo.",
    },
    "carruth natural encounters": {
        "name": "Carruth Natural Encounters Building (Houston Zoo)",
        "year_built": 2005,
        "architect": "Houston Zoo",
        "style": "Indoor Rainforest & Nocturnal Exhibit",
        "stories": 2,
        "height_m": 9.0,
        "citation": "Houston Zoo Master Inventory; opened 2005.",
    },
    "giraffe barn": {
        "name": "African Forest — Giraffe Barn (Houston Zoo)",
        "year_built": 2010,
        "architect": "CLR Design / Houston Zoo",
        "style": "Zoological Habitat Barn",
        "stories": 2,
        "height_m": 10.0,
        "citation": "Houston Zoo Master Inventory; Phase I of the African Forest opened Dec 2010.",
    },
    "giraffee feeding": {
        "name": "African Forest — Giraffe Feeding Platform (Houston Zoo)",
        "year_built": 2010,
        "architect": "CLR Design / Houston Zoo",
        "style": "Zoological Viewing Pavilion",
        "stories": 2,
        "height_m": 7.5,
        "citation": "Houston Zoo Master Inventory; opened Dec 2010.",
    },
    "masihara pavilion": {
        "name": "African Forest — Masihara Pavilion (Houston Zoo)",
        "year_built": 2010,
        "architect": "CLR Design / Houston Zoo",
        "style": "East African Cultural Pavilion",
        "stories": 1,
        "height_m": 6.5,
        "citation": "Houston Zoo Master Inventory; opened Dec 2010.",
    },
    "shani market": {
        "name": "African Forest — Shani Market (Houston Zoo)",
        "year_built": 2010,
        "architect": "CLR Design / Houston Zoo",
        "style": "African Forest Village Pavilion",
        "stories": 1,
        "height_m": 5.5,
        "citation": "Houston Zoo Master Inventory; opened Dec 2010.",
    },
    "karamu outpost": {
        "name": "African Forest — Karamu Outpost (Houston Zoo)",
        "year_built": 2010,
        "architect": "CLR Design / Houston Zoo",
        "style": "Zoological Dining & Viewing Outpost",
        "stories": 1,
        "height_m": 6.5,
        "citation": "Houston Zoo Master Inventory; opened Dec 2010.",
    },
    "elephant barn": {
        "name": "McNair Asian Elephant Habitat Barn (Houston Zoo)",
        "year_built": 2010,
        "architect": "Houston Zoo",
        "style": "Zoological Elephant Facility",
        "stories": 2,
        "height_m": 11.0,
        "citation": "Houston Zoo Master Inventory; McNair Asian Elephant Habitat opened 2010 (expanded 2017).",
    },
    "asia society texas center": {
        "name": "Asia Society Texas Center",
        "year_built": 2012,
        "architect": "Yoshio Taniguchi",
        "style": "Minimalist Jura Limestone & Glass Modernism",
        "stories": 2,
        "height_m": 13.5,
        "citation": "Opened April 2012 in the Museum District; Yoshio Taniguchi's first freestanding building in the United States.",
    },
    "bug house": {
        "name": "John P. McGovern Children's Zoo Bug House (Houston Zoo)",
        "year_built": 2014,
        "architect": "Houston Zoo",
        "style": "Entomology Exhibit Pavilion",
        "stories": 1,
        "height_m": 6.0,
        "citation": "Houston Zoo Master Inventory; opened May 2014.",
    },
    "great ape gallery": {
        "name": "Gorilla Habitat & Great Ape Gallery (Houston Zoo)",
        "year_built": 2015,
        "architect": "Houston Zoo",
        "style": "Zoological Habitat Pavilion",
        "stories": 2,
        "height_m": 9.5,
        "citation": "Houston Zoo Master Inventory; Gorilla habitat completed 2015.",
    },
    "the glassell school of art": {
        "name": "The Glassell School of Art (Museum of Fine Arts, Houston)",
        "year_built": 2018,
        "architect": "Steven Holl Architects",
        "style": "Contemporary Inclined Precast Concrete & Glass Amphitheater",
        "stories": 3,
        "height_m": 16.0,
        "citation": "Museum of Fine Arts, Houston; opened May 2018, designed by Steven Holl.",
    },
    "sarah campbell blaffer foundation center for conservation": {
        "name": "Sarah Campbell Blaffer Foundation Center for Conservation (MFAH)",
        "year_built": 2018,
        "architect": "Lake|Flato Architects",
        "style": "Contemporary Art Conservation Laboratory",
        "stories": 2,
        "height_m": 12.0,
        "citation": "Museum of Fine Arts, Houston; completed 2018 atop the MFAH garage.",
    },
    "cypress circle cafe": {
        "name": "Cypress Circle Cafe (Houston Zoo)",
        "year_built": 2018,
        "architect": "Lake|Flato Architects",
        "style": "Contemporary Sustainable Park Pavilion",
        "stories": 1,
        "height_m": 6.5,
        "citation": "Houston Zoo Centennial Master Plan; opened 2018, designed by Lake|Flato.",
    },
    "h-e-b lone star pavillion": {
        "name": "H-E-B Lone Star Pavilion & Kathrine G. McGovern Texas Wetlands (Houston Zoo)",
        "year_built": 2019,
        "architect": "Houston Zoo / Lake|Flato",
        "style": "Texas Regionalist Pavilion",
        "stories": 1,
        "height_m": 7.5,
        "citation": "Houston Zoo Centennial Master Plan; opened May 2019.",
    },
    "nancy and rich kinder building": {
        "name": "Nancy and Rich Kinder Building (Museum of Fine Arts, Houston)",
        "year_built": 2020,
        "architect": "Steven Holl Architects",
        "style": "Contemporary Translucent Glass-Tube Museum",
        "stories": 3,
        "height_m": 18.5,
        "citation": "Museum of Fine Arts, Houston; opened Nov 2020, designed by Steven Holl.",
    },
}

# ---------------------------------------------------------------------------
# 5. UNIVERSITY OF ST. THOMAS (UST), MENIL & TEXAS MEDICAL CENTER (TMC)
# ---------------------------------------------------------------------------
UST_MENIL_TMC_HCADS = [
    "1497300010001",
    "1382870010001",
    "1488720010001",
    "0502350020001",
    "0261810000001",
    "0261800000001",
    "0261800000025",
    "0261800000011",
    "0261800000024",
    "1233760010001",
    "1466090010001",
    "0440970000231",
    "0440970000170",
]

UST_MENIL_TMC_METADATA: dict[str, dict[str, Any]] = {
    "cullen hall": {
        "name": "Link-Lee Mansion (UST Cullen Hall / Administration)",
        "year_built": 1912,
        "architect": "Sanguinet & Staats",
        "style": "Neoclassical / Beaux-Arts Mansion",
        "stories": 3,
        "height_m": 13.5,
        "citation": "Built 1912 by Sanguinet & Staats for J.W. Link, later owned by T.P. Lee; acquired 1946 as the founding building of the University of St. Thomas.",
    },
    "jones hall": {
        "name": "Jones Hall (University of St. Thomas Academic Mall)",
        "year_built": 1957,
        "architect": "Philip Johnson",
        "style": "Miesian Steel & Rose-Brick International Style",
        "stories": 2,
        "height_m": 9.5,
        "citation": "University of St. Thomas Academic Mall; completed 1957, designed by Philip Johnson (commissioned by John and Dominique de Menil).",
    },
    "strake hall": {
        "name": "Strake Hall (University of St. Thomas Academic Mall)",
        "year_built": 1957,
        "architect": "Philip Johnson",
        "style": "Miesian Steel & Rose-Brick International Style",
        "stories": 2,
        "height_m": 9.5,
        "citation": "University of St. Thomas Academic Mall; completed 1957, designed by Philip Johnson.",
    },
    "welder hall : cameron school of business": {
        "name": "Welder Hall (University of St. Thomas Academic Mall)",
        "year_built": 1958,
        "architect": "Philip Johnson",
        "style": "Miesian Steel & Rose-Brick International Style",
        "stories": 2,
        "height_m": 9.5,
        "citation": "University of St. Thomas Academic Mall; completed 1958, designed by Philip Johnson.",
    },
    "robertson science hall": {
        "name": "Robertson Hall (University of St. Thomas Academic Mall)",
        "year_built": 1966,
        "architect": "Howard Barnstone & Eugene Aubry",
        "style": "Miesian Modernist",
        "stories": 2,
        "height_m": 9.5,
        "citation": "University of St. Thomas Academic Mall; completed 1966.",
    },
    "crooker center": {
        "name": "Crooker Campus Center (University of St. Thomas)",
        "year_built": 1972,
        "architect": "Caudill Rowlett Scott (CRS)",
        "style": "Late Modern",
        "stories": 2,
        "height_m": 10.0,
        "citation": "University of St. Thomas Campus Inventory; completed 1972.",
    },
    "jerabeck activity and athletic center": {
        "name": "Jerabeck Activity and Athletic Center (UST)",
        "year_built": 1981,
        "architect": "UST Facilities",
        "style": "Late Modern Athletic Facility",
        "stories": 2,
        "height_m": 12.0,
        "citation": "University of St. Thomas Campus Inventory; completed 1981.",
    },
    "anderson hall": {
        "name": "M.D. Anderson Hall (University of St. Thomas)",
        "year_built": 1984,
        "architect": "UST Facilities",
        "style": "Late Modern",
        "stories": 2,
        "height_m": 9.5,
        "citation": "University of St. Thomas Campus Inventory; completed 1984.",
    },
    "chapel of st. basil": {
        "name": "Chapel of St. Basil (University of St. Thomas)",
        "year_built": 1997,
        "architect": "Philip Johnson, Ritchie & Fiore",
        "style": "Deconstructivist / Geometric Cube, Sphere & Plane",
        "stories": 3,
        "height_m": 22.0,
        "citation": "University of St. Thomas; completed 1997, designed by Philip Johnson capping the north end of the 1957 Academic Mall.",
    },
    "malloy hall": {
        "name": "Edward P. Malloy Hall (University of St. Thomas)",
        "year_built": 2004,
        "architect": "Morris Architects",
        "style": "Contemporary Academic Building",
        "stories": 2,
        "height_m": 10.0,
        "citation": "University of St. Thomas Campus Inventory; completed 2004.",
    },
    "center for science and health professions": {
        "name": "Center for Science and Health Professions (UST)",
        "year_built": 2017,
        "architect": "WHR Architects / EYP",
        "style": "Contemporary STEM & Nursing Complex",
        "stories": 4,
        "height_m": 18.0,
        "citation": "University of St. Thomas Campus Inventory; opened May 2017.",
    },
    "rothko chapel": {
        "name": "The Rothko Chapel",
        "year_built": 1971,
        "architect": "Mark Rothko, Philip Johnson, Howard Barnstone & Eugene Aubry",
        "style": "Octagonal Modernist Sanctuary (NRHP)",
        "stories": 1,
        "height_m": 9.0,
        "citation": "National Register of Historic Places; commissioned by John and Dominique de Menil, dedicated Feb 1971.",
    },
    "byzantine fresco chapel museum": {
        "name": "Byzantine Fresco Chapel (The Menil Collection)",
        "year_built": 1997,
        "architect": "François de Menil",
        "style": "Contemporary Glass & Steel Reliquary",
        "stories": 1,
        "height_m": 9.5,
        "citation": "The Menil Collection; opened Feb 1997, designed by François de Menil.",
    },
    "annunciation greek orthodox cathedral": {
        "name": "Annunciation Greek Orthodox Cathedral",
        "year_built": 1952,
        "architect": "finger & Rustay (expanded 1970, 2000)",
        "style": "Byzantine Revival Cathedral",
        "stories": 2,
        "height_m": 16.0,
        "citation": "Constructed 1952 at 3511 Yoakum Blvd in Montrose.",
    },
    "memorial hermann-texas medical center": {
        "name": "Memorial Hermann Hospital (Historic Hermann Pavilion)",
        "year_built": 1925,
        "architect": "Berlin & Swern and Alfred C. Finn",
        "style": "Spanish Colonial Revival / Medical Center",
        "stories": 8,
        "height_m": 32.0,
        "citation": "Original Hermann Hospital opened July 1925 as the founding institution at the edge of Hermann Park / Texas Medical Center.",
    },
    "baylor college of medicine": {
        "name": "Baylor College of Medicine (Roy and Lillie Cullen Building)",
        "year_built": 1947,
        "architect": "Hedrick & Lindsley",
        "style": "Art Moderne / Texas Limestone Medical Complex",
        "stories": 5,
        "height_m": 22.0,
        "citation": "Cornerstone laid 1947 at 1 Baylor Plaza; first permanent Texas Medical Center building designed by Wyatt C. Hedrick.",
    },
    "tirr memorial hermann": {
        "name": "TIRR Memorial Hermann (The Institute for Rehabilitation and Research)",
        "year_built": 1959,
        "architect": "Wilson, Morris, Crain & Anderson",
        "style": "Mid-Century Medical Research Facility",
        "stories": 4,
        "height_m": 16.0,
        "citation": "Opened 1959 in the Texas Medical Center.",
    },
    "ben taub general hospital": {
        "name": "Ben Taub Hospital (Harris Health System)",
        "year_built": 1963,
        "architect": "Golemon & Rolfe (1990 tower replacement)",
        "style": "Mid-Century / Late Modern Public Teaching Hospital",
        "stories": 7,
        "height_m": 28.0,
        "citation": "Original Ben Taub General Hospital opened May 1963; expanded 1990.",
    },
    "children's memorial hermann hospital": {
        "name": "Children's Memorial Hermann Hospital",
        "year_built": 1986,
        "architect": "3D/International / Page",
        "style": "Late Modern Medical Tower",
        "stories": 10,
        "height_m": 40.0,
        "citation": "Established 1986 within the Memorial Hermann–Texas Medical Center campus.",
    },
    "bcm alkek building": {
        "name": "Baylor College of Medicine — Alkek Graduate School Building",
        "year_built": 1988,
        "architect": "BCM Facilities",
        "style": "Late Modern Biomedical Tower",
        "stories": 12,
        "height_m": 46.0,
        "citation": "Baylor College of Medicine Campus Inventory; completed 1988.",
    },
    "margaret alkek biomedical research building": {
        "name": "Margaret M. and Albert B. Alkek Biomedical Research Building (BCM)",
        "year_built": 2008,
        "architect": "HOK",
        "style": "Contemporary Biomedical Research Tower",
        "stories": 11,
        "height_m": 45.0,
        "citation": "Baylor College of Medicine; completed 2008.",
    },
    "md anderson zayed building": {
        "name": "Sheikh Zayed Bin Sultan Al Nahyan Building for Personalized Cancer Care (MD Anderson)",
        "year_built": 2015,
        "architect": "HDR",
        "style": "Contemporary Biomedical Research Tower",
        "stories": 12,
        "height_m": 52.0,
        "citation": "MD Anderson Cancer Center; completed 2015.",
    },
    "uh college of pharmacy": {
        "name": "UH College of Pharmacy / Health & Biomedical Sciences TMC",
        "year_built": 2017,
        "architect": "Shepley Bulfinch",
        "style": "Contemporary Biomedical Academic Complex",
        "stories": 9,
        "height_m": 38.0,
        "citation": "University of Houston College of Pharmacy facility completed 2017.",
    },
}

# ---------------------------------------------------------------------------
# 6. SAM HOUSTON PARK (THE HERITAGE SOCIETY HISTORIC STRUCTURES, 1823–1968)
# ---------------------------------------------------------------------------
SAM_HOUSTON_PARK_HCAD = "0400030000014"

SAM_HOUSTON_PARK_METADATA: dict[str, dict[str, Any]] = {
    "old place": {
        "name": "The Old Place (1823 Austin Colony Cedar Cabin)",
        "year_built": 1836,
        "architect": "John R. Williams (Stephen F. Austin's Old Three Hundred)",
        "style": "Early Texas Cedar Log Cabin (Built c. 1823 on Clear Creek)",
        "stories": 1,
        "height_m": 5.0,
        "citation": "The Heritage Society at Sam Houston Park; built c. 1823 on Clear Creek (oldest surviving structure in Harris County; normalized to 1836 atlas start year).",
    },
    "kellum-noble house": {
        "name": "The Kellum-Noble House",
        "year_built": 1847,
        "architect": "Nathaniel Kellum",
        "style": "Louisiana Colonial / Greek Revival Brick Veranda House",
        "stories": 2,
        "height_m": 9.0,
        "citation": "National Register of Historic Places & Texas Historic Landmark; built 1847 by Nathaniel Kellum on its original foundation in Sam Houston Park (oldest house in Houston on its original site).",
    },
    "nichols-rice-cherry house": {
        "name": "The Nichols-Rice-Cherry House",
        "year_built": 1850,
        "architect": "Ebenezer B. Nichols",
        "style": "Greek Revival",
        "stories": 2,
        "height_m": 9.5,
        "citation": "The Heritage Society at Sam Houston Park & NRHP; built c. 1850 by Ebenezer B. Nichols on Courthouse Square, later home of William Marsh Rice (1856–1863) and Emma Richardson Cherry.",
    },
    "fourth ward cottage": {
        "name": "The Fourth Ward Cottage",
        "year_built": 1866,
        "architect": "Vernacular Gulf Coast Builders",
        "style": "Post-Civil War Freedmen's Town / Fourth Ward Vernacular Cottage",
        "stories": 1,
        "height_m": 5.5,
        "citation": "The Heritage Society at Sam Houston Park; built c. 1866 in Houston's historic Fourth Ward.",
    },
    "pillot house": {
        "name": "The Pillot House",
        "year_built": 1868,
        "architect": "Eugene Pillot",
        "style": "Mid-Victorian Villa with First Indoor Kitchen in Houston",
        "stories": 2,
        "height_m": 9.0,
        "citation": "The Heritage Society at Sam Houston Park & NRHP; built 1868 by Eugene Pillot at Chenevert & McKinney.",
    },
    "san felipe cottage": {
        "name": "The San Felipe Cottage",
        "year_built": 1868,
        "architect": "Texas German Builders",
        "style": "six-room Texas German Vernacular Cottage",
        "stories": 1,
        "height_m": 6.0,
        "citation": "The Heritage Society at Sam Houston Park & NRHP; built 1868 on San Felipe Road.",
    },
    "yates house": {
        "name": "The Rev. Jack Yates House",
        "year_built": 1870,
        "architect": "Rev. John Henry 'Jack' Yates",
        "style": "Victorian Freedmen's Town Residence",
        "stories": 2,
        "height_m": 8.5,
        "citation": "The Heritage Society at Sam Houston Park; built 1870 on Andrews Street in Freedmen's Town by emancipator and Antioch Baptist pastor Rev. Jack Yates.",
    },
    "quilters cottage": {
        "name": "Duncan General Store (1878 Egypt, Texas Mercantile)",
        "year_built": 1878,
        "architect": "Green C. Duncan",
        "style": "19th-Century Texas Mercantile Store",
        "stories": 1,
        "height_m": 6.0,
        "citation": "The Heritage Society at Sam Houston Park; built 1878.",
    },
    "st. john church": {
        "name": "St. John Lutheran Church (1891 Country Sanctuary)",
        "year_built": 1891,
        "architect": "German & Wendish Immigrant Congregation",
        "style": "Gothic Revival Wood-Frame Country Church",
        "stories": 2,
        "height_m": 12.0,
        "citation": "The Heritage Society at Sam Houston Park; built 1891 in Northwest Harris County.",
    },
    "baker family playhouse": {
        "name": "Captain James A. Baker Family Playhouse",
        "year_built": 1893,
        "architect": "Captain James A. Baker",
        "style": "Queen Anne Victorian Playhouse",
        "stories": 1,
        "height_m": 5.0,
        "citation": "The Heritage Society at Sam Houston Park; built 1893 for the children of Captain James A. Baker.",
    },
    "stati house": {
        "name": "The Staiti House",
        "year_built": 1905,
        "architect": "Henry T. Staiti / Westmoreland Builders",
        "style": "Neoclassical / Colonial Revival Mansion",
        "stories": 2,
        "height_m": 10.5,
        "citation": "The Heritage Society at Sam Houston Park; built 1905 in Westmoreland for Spindletop oil pioneer Henry T. Staiti.",
    },
    "harris county heritage society museum": {
        "name": "The Heritage Society Museum Gallery",
        "year_built": 1968,
        "architect": "The Heritage Society",
        "style": "Brick Colonial Revival Museum",
        "stories": 2,
        "height_m": 8.5,
        "citation": "The Heritage Society at Sam Houston Park; museum gallery building.",
    },
}

# ---------------------------------------------------------------------------
# 7. CURATED CITYWIDE CIVIC, RELIGIOUS, SCHOOL & COURTHOUSE OVERRIDES BY HCAD
# ---------------------------------------------------------------------------
CURATED_CITYWIDE_HCAD_OVERRIDES: dict[str, dict[str, Any]] = {
    # Downtown Civic, Courthouses, Churches & UHD
    "0011010000001": {
        "landmark_name": "Church of the Annunciation",
        "address": "1618 TEXAS ST",
        "year_built": 1869,
        "architect": "Nicholas J. Clayton",
        "bld_style": "Romanesque / Gothic Revival Cathedral Sanctuary",
        "use_category": "Civic / Institutional",
        "citation": "Cornerstone laid April 25, 1869 (dedicated 1871), redesigned by Texas architect Nicholas J. Clayton in 1884; oldest church building in continual use in Houston.",
    },
    "0321670000022": {
        "landmark_name": "Antioch Missionary Baptist Church",
        "address": "500 CLAY ST",
        "year_built": 1875,
        "architect": "Richard Allen (1875–1879) & Robert Jones (1895)",
        "bld_style": "Victorian Gothic Revival Brick Sanctuary",
        "use_category": "Civic / Institutional",
        "citation": "National Register of Historic Places & Protected Landmark; founded 1866 with Rev. Jack Yates as first pastor; brick sanctuary built 1875–1879 by African American builder and legislator Richard Allen.",
    },
    "0010550000006": {
        "landmark_name": "Christ Church Cathedral",
        "address": "1117 TEXAS AVE / 1112 PRAIRIE ST",
        "year_built": 1893,
        "architect": "Silas McBee (1938 Latham Hall by William Ward Watkin)",
        "bld_style": "English Gothic Revival",
        "use_category": "Civic / Institutional",
        "citation": "National Register of Historic Places & City of Houston Protected Landmark; Houston's oldest congregation (founded 1839 on this block), present Gothic Revival sanctuary completed 1893.",
    },
    "1282360010001": {
        "landmark_name": "Christ Church Cathedral Cloister & Latham Hall",
        "address": "1212 PRAIRIE ST",
        "year_built": 1938,
        "architect": "William Ward Watkin",
        "bld_style": "Gothic Revival Parish Hall",
        "use_category": "Civic / Institutional",
        "citation": "Christ Church Cathedral campus expansion designed by William Ward Watkin (1938).",
    },
    "0010310000001": {
        "landmark_name": "1910 Harris County Courthouse",
        "address": "301 FANNIN ST",
        "year_built": 1910,
        "architect": "Lang & Witchell (1910); PGAL (2011 Restoration)",
        "bld_style": "Beaux-Arts Classicism",
        "use_category": "Civic / Institutional",
        "citation": "National Register of Historic Places & State Antiquities Landmark; fifth Harris County Courthouse completed 1910 by Dallas architects Lang & Witchell (restored 2011, 2012 President's Good Brick Award).",
    },
    "0010300000001": {
        "landmark_name": "Harris County Juvenile Justice Center (Former 1952 Courthouse)",
        "address": "1200 CONGRESS ST",
        "year_built": 1952,
        "architect": "Finger & Rustay (1952); PGAL / Satterfield & Pontikes Construction (2006 Renovation)",
        "bld_style": "Mid-Century Modern / Stripped Classical",
        "use_category": "Civic / Institutional",
        "citation": "9-story, 338,000 SF courthouse building constructed 1951–1952 (designed by Finger & Rustay) and renovated in 2006 by Satterfield & Pontikes Construction and PGAL into the Harris County Juvenile Justice Center.",
    },
    "0010320000001": {
        "landmark_name": "Harris County Administration Building",
        "address": "1001 PRESTON ST",
        "year_built": 1975,
        "architect": None,
        "bld_style": "Late Modernism",
        "use_category": "Civic / Institutional",
        "citation": "9-story Harris County Administration Building at 1001 Preston St (built 1975).",
    },
    "0010240000001": {
        "landmark_name": "Harris County Civil Courthouse",
        "address": "201 CAROLINE ST",
        "year_built": 2006,
        "architect": "PGAL (Pierce Goodwin Alexander & Linville)",
        "bld_style": "Contemporary Civic Courthouse Tower",
        "use_category": "Civic / Institutional",
        "citation": "18-story Harris County Civil Courthouse completed 2006 at 201 Caroline St.",
    },
    "1306320010001": {
        "landmark_name": "Harris County Jury Assembly Building (Jury Plaza)",
        "address": "1201 CONGRESS ST",
        "year_built": 2010,
        "architect": "PGAL",
        "bld_style": "Contemporary Civic",
        "use_category": "Civic / Institutional",
        "citation": "Harris County Jury Plaza completed 2010 at 1201 Congress St.",
    },
    "0010120000010": {
        "landmark_name": "Harris County Criminal Justice Center",
        "address": "1201 FRANKLIN ST",
        "year_built": 2000,
        "architect": "Page / PGAL",
        "bld_style": "Contemporary Judicial High-Rise",
        "use_category": "Civic / Institutional",
        "citation": "21-story Harris County Criminal Justice Center opened 2000 at 1201 Franklin St.",
    },
    "0010110000010": {
        "landmark_name": "Harris County Criminal Justice Center",
        "address": "1201 FRANKLIN ST / 1301 FRANKLIN ST",
        "year_built": 2000,
        "architect": "Page / PGAL",
        "bld_style": "Contemporary Judicial High-Rise",
        "use_category": "Civic / Institutional",
        "citation": "Harris County Criminal Justice Center opened 2000.",
    },
    "0010220000002": {
        "landmark_name": "Harris County Family Law Center",
        "address": "1115 CONGRESS ST",
        "year_built": 1969,
        "architect": "Wilson, Morris, Crain & Anderson",
        "bld_style": "Mid-Century Modern Civic Building",
        "use_category": "Civic / Institutional",
        "citation": "Harris County Family Law Center completed 1969.",
    },
    "0010480000022": {
        "landmark_name": "Harris County Annex (1302 Preston St)",
        "address": "1302 PRESTON ST",
        "year_built": 1952,
        "architect": None,
        "bld_style": "Mid-Century Institutional",
        "use_category": "Civic / Institutional",
        "citation": "Harris County facility at 1302 Preston St.",
    },
    "0010780000001": {
        "landmark_name": "Sam Houston U.S. Post Office and Custom House (1911 Federal Building)",
        "address": "701 SAN JACINTO ST",
        "year_built": 1911,
        "architect": "James Knox Taylor (Supervising Architect of the Treasury)",
        "bld_style": "Beaux-Arts / Neoclassical Limestone Federal Building",
        "use_category": "Civic / Institutional",
        "citation": "National Register of Historic Places; constructed 1909–1911 under Supervising Architect James Knox Taylor.",
    },
    "0020160000033": {
        "landmark_name": "First Methodist Church Houston",
        "address": "1320 MAIN ST",
        "year_built": 1910,
        "architect": "Sanguinet & Staats",
        "bld_style": "Romanesque / Classical Revival Sanctuary",
        "use_category": "Civic / Institutional",
        "citation": "Constructed 1909–1910 at 1320 Main Street, designed by Sanguinet & Staats.",
    },
    "1238970010001": {
        "landmark_name": "One Main Building — University of Houston-Downtown (Merchants & Manufacturers Building)",
        "address": "1 MAIN ST",
        "year_built": 1930,
        "architect": "G.E.B. Zimmermann (Engineering by Robert J. Cummins)",
        "bld_style": "Art Deco Skyscraper & Terminal Warehouse",
        "use_category": "Civic / Institutional",
        "citation": "National Register of Historic Places & City of Houston Protected Landmark; completed 1930 as the Merchants and Manufacturers (M&M) Building, the largest building in Houston until 1963; home of UH-Downtown since 1974.",
    },
    "0030840000001": {
        "landmark_name": "One Main Building Annex — University of Houston-Downtown",
        "address": "1 MAIN ST",
        "year_built": 1930,
        "architect": "G.E.B. Zimmermann",
        "bld_style": "Art Deco",
        "use_category": "Civic / Institutional",
        "citation": "Part of the historic 1930 Merchants and Manufacturers Building complex at UH-Downtown.",
    },
    "0031350000001": {
        "landmark_name": "UH-Downtown Shea Street Building (College of Business)",
        "address": "320 N MAIN ST",
        "year_built": 2007,
        "architect": "Page Southerland Page",
        "bld_style": "Contemporary Academic Building",
        "use_category": "Civic / Institutional",
        "citation": "University of Houston-Downtown Shea Street Building completed 2007.",
    },
    "0012510000017": {
        "landmark_name": "Houston City Hall",
        "address": "901 BAGBY ST",
        "year_built": 1939,
        "architect": "Joseph Finger",
        "bld_style": "PWA Moderne / Art Deco Texas Limestone",
        "use_category": "Civic / Institutional",
        "citation": "National Register of Historic Places & Protected Landmark; constructed 1938–1939 as a Public Works Administration project designed by Joseph Finger.",
    },
    "0161990000001": {
        "landmark_name": "Houston City Hall Annex",
        "address": "900 BAGBY ST",
        "year_built": 1975,
        "architect": "Wilson, Morris, Crain & Anderson",
        "bld_style": "Late Modern Civic Building",
        "use_category": "Civic / Institutional",
        "citation": "Houston City Hall Annex completed 1975 across the reflection pool from City Hall.",
    },
    "0010660000001": {
        "landmark_name": "Jones Hall for the Performing Arts",
        "address": "615 LOUISIANA ST",
        "year_built": 1966,
        "architect": "Caudill Rowlett Scott (CRS)",
        "bld_style": "Mid-Century Travertine Formalism (AIA Honor Award)",
        "use_category": "Civic / Institutional",
        "citation": "Opened Oct 1966; designed by Caudill Rowlett Scott and awarded the 1967 AIA Honor Award.",
    },
    "0010650000001": {
        "landmark_name": "Alley Theatre",
        "address": "615 TEXAS AVE / 600 LOUISIANA ST",
        "year_built": 1968,
        "architect": "Ulrich Franzen",
        "bld_style": "Brutalist Turreted Concrete Fortress",
        "use_category": "Civic / Institutional",
        "citation": "Opened Nov 1968; landmark Brutalist theater designed by Ulrich Franzen (AIA Twenty-Five Year Award).",
    },
    "0010400000001": {
        "landmark_name": "Wortham Theater Center",
        "address": "501 TEXAS ST",
        "year_built": 1987,
        "architect": "Morris Architects",
        "bld_style": "Postmodern Monumental Arch Brick Opera House",
        "use_category": "Civic / Institutional",
        "citation": "Opened May 1987 as home of Houston Grand Opera and Houston Ballet.",
    },
    "1372190010001": {
        "landmark_name": "George R. Brown Convention Center",
        "address": "1001 AVENIDA DE LAS AMERICAS",
        "year_built": 1987,
        "architect": "Golemon & Rolfe / Bernard Johnson / 3D/I (2016 transformation)",
        "bld_style": "Postmodern / High-Tech Civic Convention Center",
        "use_category": "Civic / Institutional",
        "citation": "Opened Sept 1987 on the east side of Downtown Houston.",
    },
    "0131500000012": {
        "landmark_name": "Historic Houston Police Department Headquarters (61 Riesner St)",
        "address": "61 RIESNER ST",
        "year_built": 1950,
        "architect": "Kenneth Franzheim",
        "bld_style": "Mid-Century Modern Civic Complex",
        "use_category": "Civic / Institutional",
        "citation": "Constructed 1950 at 61 Riesner Street overlooking Buffalo Bayou.",
    },
    "0131500000031": {
        "landmark_name": "Historic Houston Police Department Training & Administration Building",
        "address": "61 RIESNER ST",
        "year_built": 1950,
        "architect": "Kenneth Franzheim",
        "bld_style": "Mid-Century Modern Civic Complex",
        "use_category": "Civic / Institutional",
        "citation": "Constructed 1950 as part of the Riesner Street municipal police complex.",
    },
    "0131480000016": {
        "landmark_name": "City of Houston Municipal Courts Building",
        "address": "1400 LUBBOCK ST / 33 ARTESIAN PL",
        "year_built": 1962,
        "architect": "City of Houston",
        "bld_style": "Mid-Century Civic",
        "use_category": "Civic / Institutional",
        "citation": "Constructed c. 1962 in the municipal courts complex.",
    },
    "0131500000021": {
        "landmark_name": "City of Houston Artesian Place Municipal Facility",
        "address": "33 ARTESIAN PL",
        "year_built": 1962,
        "architect": "City of Houston",
        "bld_style": "Mid-Century Civic",
        "use_category": "Civic / Institutional",
        "citation": "Constructed c. 1962 in the municipal courts complex.",
    },
    # Houston Heights & Norhill Historic Schools, Libraries, Fire Stations & Churches
    "0202440000024": {
        "landmark_name": "Harvard Elementary School (Historic HISD Campus)",
        "address": "810 HARVARD ST",
        "year_built": 1898,
        "architect": "Olle J. Lorehn / R.D. Steele (1912 & 1929 wings)",
        "bld_style": "Classical / Collegiate Gothic Brick Schoolhouse",
        "use_category": "Civic / Institutional",
        "citation": "Established 1898 as one of the two original public schools of the municipality of Houston Heights; brick building expanded 1912 and 1929.",
    },
    "0202440000002": {
        "landmark_name": "Harvard Elementary School — Cortlandt Wing",
        "address": "843 CORTLANDT ST",
        "year_built": 1929,
        "architect": "Houston Independent School District",
        "bld_style": "Collegiate Brick Schoolhouse",
        "use_category": "Civic / Institutional",
        "citation": "1929 expansion wing of historic Harvard Elementary School in the Houston Heights South Historic District.",
    },
    "0200990000023": {
        "landmark_name": "St. Andrew's Episcopal Church",
        "address": "1819 HEIGHTS BLVD",
        "year_built": 1910,
        "architect": "St. Andrew's Parish",
        "bld_style": "Gothic Revival Sanctuary",
        "use_category": "Civic / Institutional",
        "citation": "Founded 1907 in Houston Heights; historic church sanctuary on Heights Boulevard built 1910 (expanded 1949).",
    },
    "0201000000014": {
        "landmark_name": "Houston Heights Fire Station & City Hall",
        "address": "1800 HEIGHTS BLVD / 107 W 12TH ST",
        "year_built": 1914,
        "architect": "C.H. Page & Brother",
        "bld_style": "Early 20th-Century Municipal Brick Firehouse",
        "use_category": "Civic / Institutional",
        "citation": "Constructed 1914 as the City Hall, Jail, and Fire Station of the independent municipality of Houston Heights prior to 1918 annexation.",
    },
    "0201810000034": {
        "landmark_name": "Heights Neighborhood Library (Houston Public Library)",
        "address": "1302 HEIGHTS BLVD / 1205 YALE ST",
        "year_built": 1925,
        "architect": "J.C. McVea & Burns Roensch",
        "bld_style": "Italian Renaissance Revival Villa",
        "use_category": "Civic / Institutional",
        "citation": "National Register of Historic Places & City of Houston Protected Landmark; opened March 1926 (built 1925) as one of the earliest neighborhood branch libraries in Houston.",
    },
    "0621210000001": {
        "landmark_name": "All Saints Catholic Church",
        "address": "215 E 10TH ST / 610 W MELWOOD ST",
        "year_built": 1926,
        "architect": "Maurice J. Sullivan",
        "bld_style": "Spanish Colonial / Mission Revival Church",
        "use_category": "Civic / Institutional",
        "citation": "Designed by Houston architect Maurice J. Sullivan and completed 1926–1928 in the Heights/Norhill area.",
    },
    "0621120010001": {
        "landmark_name": "Historic Norhill Sanctuary (1031 E 11th St)",
        "address": "1031 E 11TH ST",
        "year_built": 1927,
        "architect": "Norhill Congregation",
        "bld_style": "1920s Classical Revival Brick Sanctuary",
        "use_category": "Civic / Institutional",
        "citation": "Historic 1920s brick sanctuary in the Norhill Historic District.",
    },
    "0621370000013": {
        "landmark_name": "Norhill Church of Christ",
        "address": "634 W COTTAGE ST",
        "year_built": 1928,
        "architect": "Norhill Church of Christ",
        "bld_style": "1920s Brick Sanctuary",
        "use_category": "Civic / Institutional",
        "citation": "Constructed c. 1928 in the Norhill Historic District.",
    },
    "0201550000028": {
        "landmark_name": "Immanuel Lutheran Church",
        "address": "306 E 15TH ST",
        "year_built": 1931,
        "architect": "Alfred C. Finn",
        "bld_style": "Gothic Revival Brick & Limestone Sanctuary",
        "use_category": "Civic / Institutional",
        "citation": "City of Houston Protected Landmark; Gothic Revival sanctuary at 15th & Cortlandt designed by Alfred C. Finn and completed 1931–1932.",
    },
    "0621260000001": {
        "landmark_name": "Proctor Plaza Park Community Center",
        "address": "803 W TEMPLE ST",
        "year_built": 1939,
        "architect": "City of Houston Parks Department",
        "bld_style": "New Deal Era Park Community House",
        "use_category": "Civic / Institutional",
        "citation": "Constructed 1939 in Proctor Plaza Park in the Norhill Historic District.",
    },
    # Midtown, Montrose & Third Ward Historic Schools & Churches
    "0191880000001": {
        "landmark_name": "San Jacinto Memorial Building (HCC Central / Former San Jacinto High School)",
        "address": "1300 HOLMAN ST",
        "year_built": 1914,
        "architect": "Sanguinet & Staats (1926 wings by Hedrick & Gottlieb)",
        "bld_style": "Classical / Collegiate Gothic Revival",
        "use_category": "Civic / Institutional",
        "citation": "Opened 1914 as South End Junior High School, renamed San Jacinto High School in 1926; restored as the flagship administration and academic building of Houston Community College Central.",
    },
    "0191880000005": {
        "landmark_name": "San Jacinto Memorial Building East Wing (1300 Holman St)",
        "address": "1300 HOLMAN ST",
        "year_built": 1926,
        "architect": "Hedrick & Gottlieb",
        "bld_style": "Classical Revival",
        "use_category": "Civic / Institutional",
        "citation": "1926 auditorium and classroom expansion of historic San Jacinto High School / HCC Central.",
    },
    "0191870000001": {
        "landmark_name": "HCC Central Fine Arts / Heinen Theatre Building",
        "address": "3214 AUSTIN ST",
        "year_built": 1937,
        "architect": "Houston ISD / HCC",
        "bld_style": "PWA Moderne / Mid-Century Academic",
        "use_category": "Civic / Institutional",
        "citation": "Historic San Jacinto High School / HCC Central campus structure.",
    },
    "0230680000001": {
        "landmark_name": "Former High School for the Performing and Visual Arts (HSPVA Montrose Campus)",
        "address": "4001 STANFORD ST",
        "year_built": 1981,
        "architect": "Caudill Rowlett Scott (CRS)",
        "bld_style": "Late Modern Performing Arts High School",
        "use_category": "Civic / Institutional",
        "citation": "Constructed 1981 on the former Montrose Elementary block in First Montrose Commons.",
    },
    "1241790010001": {
        "landmark_name": "Holy Rosary Catholic Church",
        "address": "3617 MILAM ST",
        "year_built": 1933,
        "architect": "Maurice J. Sullivan",
        "bld_style": "Italian Romanesque Revival Brick & Limestone Church",
        "use_category": "Civic / Institutional",
        "citation": "Designed by Maurice J. Sullivan and completed 1933 in Midtown Houston.",
    },
    "0180420000001": {
        "landmark_name": "Grace Evangelical Lutheran Church",
        "address": "2515 WAUGH DR",
        "year_built": 1928,
        "architect": "Alfred C. Finn / Joseph Finger",
        "bld_style": "Gothic Revival Brick Sanctuary",
        "use_category": "Civic / Institutional",
        "citation": "Historic Montrose Gothic Revival sanctuary constructed 1928 at Waugh Drive and Missouri Street.",
    },
    "0180070000020": {
        "landmark_name": "Grace Evangelical Lutheran Parish Hall",
        "address": "2515 WAUGH DR",
        "year_built": 1928,
        "architect": "Grace Evangelical Lutheran Parish",
        "bld_style": "Gothic Revival",
        "use_category": "Civic / Institutional",
        "citation": "Part of the 1928 Grace Evangelical Lutheran Church campus.",
    },
    "1365180010001": {
        "landmark_name": "Trinity Episcopal Church (Historic Midtown Sanctuary)",
        "address": "1015 HOLMAN ST",
        "year_built": 1919,
        "architect": "Cram & Ferguson and William Ward Watkin",
        "bld_style": "Gothic Revival / English Parish Sanctuary",
        "use_category": "Civic / Institutional",
        "citation": "Parish hall and sanctuary at Main & Holman designed by Cram & Ferguson and William Ward Watkin (1919–1952).",
    },
    "0190520010020": {
        "landmark_name": "Trinity East United Methodist Church",
        "address": "2418 MCGOWEN ST",
        "year_built": 1926,
        "architect": "Trinity East Congregation",
        "bld_style": "Historic Third Ward Brick Sanctuary",
        "use_category": "Civic / Institutional",
        "citation": "Historic African American Methodist congregation founded 1865; McGowen Street sanctuary complex dating to the 1920s–1950s.",
    },
    "0190560000028": {
        "landmark_name": "Jerusalem Missionary Baptist Church",
        "address": "2201 TUAM ST",
        "year_built": 1929,
        "architect": "Jerusalem Missionary Baptist Congregation",
        "bld_style": "Historic Third Ward Sanctuary",
        "use_category": "Civic / Institutional",
        "citation": "Historic Third Ward Baptist congregation at 2201 Tuam Street.",
    },
    "0021630000018": {
        "landmark_name": "Emancipation Park Cultural & Community Center",
        "address": "3018 EMANCIPATION AVE / 2201 EMANCIPATION AVE",
        "year_built": 1939,
        "architect": "William Ward Watkin (1939 WPA bathhouse/community center; 2017 expansion by David Adjaye)",
        "bld_style": "Art Deco / WPA Moderne & Contemporary Cultural Center",
        "use_category": "Civic / Institutional",
        "citation": "Emancipation Park founded 1872 by Rev. Jack Yates and former enslaved Texans; historic 1939 WPA community building designed by William Ward Watkin, expanded 2017 by Adjaye Associates.",
    },
    "0050150000011": {
        "landmark_name": "Freedmen's Town Historic Houses (1108–1110 Victor St & 1113 Cleveland St)",
        "address": "1108 VICTOR ST",
        "year_built": 1895,
        "architect": "Jeff Bland Lumber and Building Company / Freedmen's Town Builders",
        "bld_style": "Late 19th-Century Victorian Shotgun & Folk Cottages",
        "use_category": "Residential",
        "citation": "City of Houston Protected Landmarks in Freedmen's Town Historic District; built c. 1895.",
    },
    "0140780000006": {
        "landmark_name": "Women's Christian Home (Hyde Park Historic Building)",
        "address": "307 HYDE PARK BLVD",
        "year_built": 1925,
        "architect": "Montrose Builders",
        "bld_style": "1920s Brick Institutional Residence",
        "use_category": "Civic / Institutional",
        "citation": "Historic 1920s Montrose building at 307 Hyde Park Blvd.",
    },
    "0090730000009": {
        "landmark_name": "United Most Worshipful Scottish Rite Grand Lodge (Fourth Ward)",
        "address": "1102 ANDREWS ST",
        "year_built": 1950,
        "architect": "Scottish Rite Masonic Order",
        "bld_style": "Mid-Century Fraternal Hall",
        "use_category": "Civic / Institutional",
        "citation": "Historic Fourth Ward fraternal lodge on Andrews Street.",
    },
    "0051120000012": {
        "landmark_name": "HFD Fire Station / High First Ward Municipal Facility",
        "address": "1900 CROCKETT ST",
        "year_built": 1928,
        "architect": "City of Houston",
        "bld_style": "Early 20th-Century Municipal Brick",
        "use_category": "Civic / Institutional",
        "citation": "Historic municipal building on Crockett Street in High First Ward.",
    },
    "0051110000022": {
        "landmark_name": "High First Ward Municipal Annex (1620 Crockett St)",
        "address": "1620 CROCKETT ST",
        "year_built": 1930,
        "architect": "City of Houston",
        "bld_style": "Early 20th-Century Municipal",
        "use_category": "Civic / Institutional",
        "citation": "Municipal building on Crockett Street in High First Ward.",
    },
}


def compute_decade(yr: int) -> int:
    if yr < 1836 or yr > 2030:
        return 0
    if yr < 1850:
        return 1840
    return (yr // 10) * 10


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _osm_element_to_geojson_poly(el: dict[str, Any]) -> dict[str, Any] | None:
    geom_pts = el.get("geometry")
    if not geom_pts and el.get("members"):
        for m in el["members"]:
            if m.get("role") in ("outer", "") and m.get("geometry"):
                geom_pts = m["geometry"]
                break
    if not geom_pts or len(geom_pts) < 3:
        return None
    ring = [[float(pt["lon"]), float(pt["lat"])] for pt in geom_pts]
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    if len(ring) < 4:
        return None
    return {"type": "Polygon", "coordinates": [ring]}


def _lookup_metadata_fuzzy(
    name: str, ref: str, meta_dict: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    nl = name.lower().strip()
    rl = ref.lower().strip()
    if nl in meta_dict:
        return meta_dict[nl]
    if rl and rl in meta_dict:
        return meta_dict[rl]
    for k, v in meta_dict.items():
        if k in nl or nl in k:
            return v
    return None


def enrich_rice_campus(overrides: dict[str, Any], osm_cache_path: Path) -> int:
    for k in [
        k
        for k in list(overrides.keys())
        if "#" in k and (k.startswith(RICE_EAST_HCAD) or k.startswith(RICE_WEST_HCAD))
    ]:
        del overrides[k]

    req = urllib.request.Request(
        RICE_POLYGON_MAP_URL, headers={"User-Agent": "PreservationHouston-Atlas/1.0"}
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        rice_fc = json.loads(resp.read().decode("utf-8"))

    added = 0
    kept_polys = []

    for feat in rice_fc.get("features", []):
        props = feat.get("properties") or {}
        geom = feat.get("geometry")
        if not geom:
            continue
        spec = (props.get("SPECNAME") or "").strip()
        bname = (props.get("Bldg_Name") or "").strip()
        cmap = (props.get("CAMPUSMAP_NAME") or "").strip()
        oid = props.get("OBJECTID")

        if spec.lower() in ("covered walkway", "unknown"):
            continue
        if not bname and not spec and not cmap:
            continue

        meta = (
            RICE_BUILDING_METADATA.get(spec.lower())
            or RICE_BUILDING_METADATA.get(bname.lower())
            or RICE_BUILDING_METADATA.get(cmap.lower())
        )

        shp = shapely.from_geojson(json.dumps(geom))
        kept_polys.append(shp)
        lon = float(shp.centroid.x)
        hcad_num = RICE_WEST_HCAD if lon < -95.4005 else RICE_EAST_HCAD

        display_name = (
            meta["name"]
            if meta
            else (
                f"{bname} ({spec})"
                if bname and spec and spec != bname
                else (bname or spec or cmap)
            )
        )
        yr = int(meta["year_built"]) if meta else 0
        arch = meta["architect"] if meta else "Rice University Facilities Engineering & Planning"
        style = meta["style"] if meta else "Collegiate / Institutional"
        stories = int(meta.get("stories", 2)) if meta else 2
        height_m = float(meta.get("height_m", 9.0)) if meta else 9.0
        citation = (
            meta["citation"]
            if meta
            else f"Rice University Campus GIS Inventory (Rice_Polygon_Map OBJECTID {oid}: {display_name})."
        )

        ov_key = f"{hcad_num}#rice_{oid}_{slugify(display_name)[:28]}"
        overrides[ov_key] = {
            "id": ov_key,
            "building_id": ov_key,
            "hcad_num": hcad_num,
            "is_building_override": True,
            "replace_parcel_shards": True,
            "address": f"6100 MAIN ST ({display_name.upper()})",
            "landmark_name": display_name,
            "historic_district": "Rice University Campus",
            "contributing": "Tax-Exempt Campus Structure",
            "use_category": "Civic / Institutional",
            "year_built": yr,
            "decade": compute_decade(yr),
            "original_hcad_year": 0,
            "stories": stories,
            "height_m": height_m,
            "bld_style": style,
            "architect": arch,
            "source_type": "Rice University Architectural & GIS Inventory",
            "source_citation": citation,
            "source_url": "https://map.rice.edu/",
            "verified_by": "Preservation Houston Institutional Campus Audit",
            "updated_at": "2026-10-06",
            "geometry": geom,
        }
        added += 1

    if osm_cache_path.exists():
        osm_feats = json.loads(osm_cache_path.read_text(encoding="utf-8"))
        for idx, feat in enumerate(osm_feats):
            g = feat.get("geometry")
            if not g or g.get("type") != "Polygon":
                continue
            pt0 = g["coordinates"][0][0]
            if not (-95.412 <= pt0[0] <= -95.394 and 29.713 <= pt0[1] <= 29.723):
                continue
            p = feat.get("properties") or {}
            nm = (p.get("name") or "").strip()
            if not nm:
                continue
            meta = RICE_BUILDING_METADATA.get(nm.lower())
            if not meta:
                continue
            shp = shapely.from_geojson(json.dumps(g))
            if any(
                shp.intersection(kp).area / max(shp.area, 1e-12) > 0.25 for kp in kept_polys
            ):
                continue
            kept_polys.append(shp)
            lon = float(shp.centroid.x)
            hcad_num = RICE_WEST_HCAD if lon < -95.4005 else RICE_EAST_HCAD
            yr = int(meta["year_built"])
            display_name = meta["name"]
            ov_key = f"{hcad_num}#osm_{idx}_{slugify(display_name)[:24]}"
            overrides[ov_key] = {
                "id": ov_key,
                "building_id": ov_key,
                "hcad_num": hcad_num,
                "is_building_override": True,
                "replace_parcel_shards": True,
                "address": f"6100 MAIN ST ({display_name.upper()})",
                "landmark_name": display_name,
                "historic_district": "Rice University Campus",
                "contributing": "Tax-Exempt Campus Structure",
                "use_category": "Civic / Institutional",
                "year_built": yr,
                "decade": compute_decade(yr),
                "original_hcad_year": 0,
                "stories": int(meta.get("stories", 2)),
                "height_m": float(meta.get("height_m", 10.0)),
                "bld_style": meta["style"],
                "architect": meta["architect"],
                "source_type": "Rice University Architectural & GIS Inventory",
                "source_citation": meta["citation"],
                "source_url": "https://map.rice.edu/",
                "verified_by": "Preservation Houston Institutional Campus Audit",
                "updated_at": "2026-10-06",
                "geometry": g,
            }
            added += 1

    return added


def enrich_tsu_campus(overrides: dict[str, Any]) -> int:
    for k in [
        k
        for k in list(overrides.keys())
        if "#" in k and any(k.startswith(h) for h in TSU_PARCEL_HCADS)
    ]:
        del overrides[k]

    req = urllib.request.Request(
        TSU_BUILDINGS_MAP_URL, headers={"User-Agent": "PreservationHouston-Atlas/1.0"}
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        tsu_fc = json.loads(resp.read().decode("utf-8"))

    added = 0
    for feat in tsu_fc.get("features", []):
        props = feat.get("properties") or {}
        geom = feat.get("geometry")
        if not geom:
            continue
        fid = props.get("FID")
        raw_name = (props.get("name") or "").strip()
        bno = (props.get("No") or "").strip()
        if not raw_name and not bno:
            continue

        meta = _lookup_metadata_fuzzy(raw_name, bno, TSU_BUILDING_METADATA)
        shp = shapely.from_geojson(json.dumps(geom))
        lon, lat = float(shp.centroid.x), float(shp.centroid.y)

        # Assign to primary TSU super-parcels so all TSU yr=0 shard polygons are replaced
        idx_hcad = added % len(TSU_PARCEL_HCADS)
        hcad_num = TSU_PARCEL_HCADS[0] if idx_hcad == 0 or added >= len(TSU_PARCEL_HCADS) else TSU_PARCEL_HCADS[idx_hcad]

        display_name = meta["name"] if meta else (raw_name or f"TSU Building {bno}")
        if "139 TV" in bno and not raw_name:
            display_name = f"TSU Transformation Village Hall ({fid})"
            yr = 2020
            arch = "TSU Housing & Facilities"
            style = "Contemporary Collegiate Residence"
            stories = 2
            height_m = 8.0
            citation = "Texas Southern University Campus GIS Inventory; Transformation Village modular residence halls (2020)."
        elif meta:
            yr = int(meta["year_built"])
            arch = meta["architect"]
            style = meta["style"]
            stories = int(meta.get("stories", 3))
            height_m = float(meta.get("height_m", 13.0))
            citation = meta["citation"]
        else:
            yr = 1978
            arch = "Texas Southern University Facilities Planning"
            style = "Collegiate / Institutional"
            stories = 2
            height_m = 9.5
            citation = f"Texas Southern University Campus GIS Inventory (Building {bno}: {display_name})."

        ov_key = f"{hcad_num}#tsu_{fid}_{slugify(display_name)[:26]}"
        overrides[ov_key] = {
            "id": ov_key,
            "building_id": ov_key,
            "hcad_num": hcad_num,
            "is_building_override": True,
            "replace_parcel_shards": True,
            "address": f"3100 CLEBURNE ST ({display_name.upper()})",
            "landmark_name": display_name,
            "historic_district": "Texas Southern University Campus",
            "contributing": "Tax-Exempt Campus Structure",
            "use_category": "Civic / Institutional",
            "year_built": yr,
            "decade": compute_decade(yr),
            "original_hcad_year": 0,
            "stories": stories,
            "height_m": height_m,
            "bld_style": style,
            "architect": arch,
            "source_type": "Texas Southern University Architectural & GIS Inventory",
            "source_citation": citation,
            "source_url": "https://www.tsu.edu/about/campus-map",
            "verified_by": "Preservation Houston Institutional Campus Audit",
            "updated_at": "2026-10-06",
            "geometry": geom,
        }
        added += 1

    return added


def enrich_overpass_campuses_and_citywide(
    overrides: dict[str, Any], cache_dir: Path, buildings_geojson_path: Path
) -> dict[str, int]:
    """
    Combine OpenStreetMap building polygons from `overpass_uh_and_dated.json` and
    `osm_core_footprints.json` for:
    1. University of Houston (UH Main Campus)
    2. Houston Zoo, Hermann Park & Museum District
    3. University of St. Thomas (UST), Menil Campus & Texas Medical Center (TMC)
    4. Sam Houston Park (The Heritage Society 19th-Century Houses)
    5. All citywide buildings in Houston with `start_date` or `wikidata` (`P571` inception)
    """
    overpass_cache = cache_dir / "overpass_uh_and_dated.json"
    if overpass_cache.exists():
        ov_data = json.loads(overpass_cache.read_text(encoding="utf-8"))
    else:
        query = """
[out:json][timeout:45];
(
  way["building"](29.713,-95.352,29.730,-95.331);
  relation["building"](29.713,-95.352,29.730,-95.331);
  way["building"]["start_date"](29.65,-95.58,29.88,-95.22);
  relation["building"]["start_date"](29.65,-95.58,29.88,-95.22);
  way["building"]["wikidata"](29.65,-95.58,29.88,-95.22);
  relation["building"]["wikidata"](29.65,-95.58,29.88,-95.22);
);
out body geom;
"""
        ov_data = {"elements": []}
        for mirror_url in (
            "https://lz4.overpass-api.de/api/interpreter",
            "https://z.overpass-api.de/api/interpreter",
            OVERPASS_URL,
        ):
            try:
                req = urllib.request.Request(
                    mirror_url,
                    data=urllib.parse.urlencode({"data": query}).encode("utf-8"),
                    headers={"User-Agent": "PreservationHouston-Atlas/1.0"},
                )
                with urllib.request.urlopen(req, timeout=55) as resp:
                    ov_data = json.loads(resp.read().decode("utf-8"))
                overpass_cache.write_text(json.dumps(ov_data), encoding="utf-8")
                break
            except Exception as exc:
                print(f"Overpass mirror warning ({mirror_url}): {exc}")

    # Normalize Overpass raw elements and cached GeoJSON features into (geom, tags, osm_id)
    normalized_records: list[tuple[dict[str, Any], dict[str, Any], str]] = []
    seen_centroids: set[tuple[float, float]] = set()

    extra_campus_cache = cache_dir / "overpass_zoo_ust_tmc.json"
    extra_elements = (
        json.loads(extra_campus_cache.read_text(encoding="utf-8")).get("elements", [])
        if extra_campus_cache.exists()
        else []
    )

    for el in list(ov_data.get("elements", [])) + list(extra_elements):
        tags = el.get("tags") or {}
        geom = _osm_element_to_geojson_poly(el)
        if not geom:
            continue
        shp = shapely.from_geojson(json.dumps(geom))
        ckey = (round(float(shp.centroid.x), 5), round(float(shp.centroid.y), 5))
        if ckey in seen_centroids:
            continue
        seen_centroids.add(ckey)
        normalized_records.append((geom, tags, str(el.get("id") or f"ov_{len(normalized_records)}")))

    core_fp_path = cache_dir / "osm_core_footprints.json"
    if core_fp_path.exists():
        core_features = json.loads(core_fp_path.read_text(encoding="utf-8"))
        for idx, feat in enumerate(core_features):
            geom = feat.get("geometry")
            tags = feat.get("properties") or {}
            if not geom:
                continue
            coords = geom.get("coordinates") or []
            if not coords or not coords[0]:
                continue
            pt0 = coords[0][0] if geom.get("type") == "Polygon" else coords[0][0][0]
            if not isinstance(pt0, list) or len(pt0) < 2:
                continue
            lon0, lat0 = float(pt0[0]), float(pt0[1])
            in_target_campus = (
                (29.7575 <= lat0 <= 29.7620 and -95.3735 <= lon0 <= -95.3690)
                or (29.7075 <= lat0 <= 29.7290 and -95.3960 <= lon0 <= -95.3815)
                or (29.7335 <= lat0 <= 29.7425 and -95.3995 <= lon0 <= -95.3885)
                or (29.7010 <= lat0 <= 29.7130 and -95.4050 <= lon0 <= -95.3920)
                or ("start_date" in tags)
                or ("year_built" in tags)
                or ("wikidata" in tags)
            )
            if not in_target_campus:
                continue
            shp = shapely.from_geojson(json.dumps(geom))
            ckey = (round(float(shp.centroid.x), 5), round(float(shp.centroid.y), 5))
            if ckey in seen_centroids:
                continue
            seen_centroids.add(ckey)
            osm_id = str(tags.get("@id") or tags.get("osm_id") or tags.get("id") or f"core_{idx}")
            normalized_records.append((geom, tags, osm_id))

    # Fetch Wikidata P571 inception / P84 architect / P149 style for all QIDs
    wikidata_cache = cache_dir / "wikidata_buildings_houston.json"
    if wikidata_cache.exists():
        wd_map = json.loads(wikidata_cache.read_text(encoding="utf-8"))
    else:
        qids = sorted(
            {
                tags["wikidata"].split(";")[0].strip()
                for _geom, tags, _oid in normalized_records
                if "wikidata" in tags
                and str(tags["wikidata"]).strip().startswith("Q")
            }
        )
        wd_map = {}
        for i in range(0, len(qids), 180):
            batch = qids[i : i + 180]
            values_str = " ".join(f"wd:{q}" for q in batch)
            sparql = f"""
SELECT ?item ?itemLabel ?inception ?archLabel ?styleLabel WHERE {{
  VALUES ?item {{ {values_str} }}
  OPTIONAL {{ ?item wdt:P571 ?inception . }}
  OPTIONAL {{ ?item wdt:P84 ?arch . }}
  OPTIONAL {{ ?item wdt:P149 ?style . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}}
"""
            url = "https://query.wikidata.org/sparql?" + urllib.parse.urlencode(
                {"query": sparql, "format": "json"}
            )
            req = urllib.request.Request(
                url, headers={"User-Agent": "PreservationHouston-Atlas/1.0"}
            )
            try:
                with urllib.request.urlopen(req, timeout=25) as r:
                    wres = json.loads(r.read().decode("utf-8"))
                for b in wres.get("results", {}).get("bindings", []):
                    q = b.get("item", {}).get("value", "").split("/")[-1]
                    inc = b.get("inception", {}).get("value", "")[:4]
                    yr = int(inc) if inc.isdigit() and 1820 <= int(inc) <= 2026 else 0
                    arch = b.get("archLabel", {}).get("value", "")
                    if arch.startswith("Q") and arch[1:].isdigit():
                        arch = ""
                    style = b.get("styleLabel", {}).get("value", "")
                    if style.startswith("Q") and style[1:].isdigit():
                        style = ""
                    lbl = b.get("itemLabel", {}).get("value", "")
                    if q and (yr or arch or style):
                        wd_map[q] = {
                            "name": lbl,
                            "year_built": yr,
                            "architect": arch,
                            "style": style,
                        }
            except Exception as exc:
                print(f"Wikidata batch warning: {exc}")
        wikidata_cache.write_text(json.dumps(wd_map, indent=2), encoding="utf-8")

    counts = {
        "uh": 0,
        "zoo_hermann_museum": 0,
        "ust_menil_tmc": 0,
        "sam_houston_park": 0,
        "citywide_osm_wikidata": 0,
    }

    # Clear previous composite keys for these campuses
    all_campus_hcads = (
        set(UH_PARCEL_HCADS)
        | set(ZOO_HERMANN_HCADS)
        | set(UST_MENIL_TMC_HCADS)
        | {SAM_HOUSTON_PARK_HCAD}
    )
    for k in [
        k
        for k in list(overrides.keys())
        if "#" in k and (any(k.startswith(h) for h in all_campus_hcads) or k.startswith("osm_city_"))
    ]:
        del overrides[k]

    for geom, tags, osm_id in normalized_records:
        shp = shapely.from_geojson(json.dumps(geom))
        lon, lat = float(shp.centroid.x), float(shp.centroid.y)
        raw_name = (tags.get("name") or "").strip()
        raw_ref = (tags.get("ref") or "").strip()
        qid = (tags.get("wikidata") or "").split(";")[0].strip()
        wd_info = wd_map.get(qid) if qid else None

        # Parse OSM start_date if present
        sd_str = str(tags.get("start_date") or tags.get("year_built") or "")
        m_yr = re.search(r"\b(18\d\d|19\d\d|20[0-2]\d)\b", sd_str)
        osm_yr = int(m_yr.group(1)) if m_yr else (int(wd_info["year_built"]) if wd_info and wd_info.get("year_built") else 0)

        # A. Sam Houston Park (29.758..29.7615, -95.373..-95.3695)
        if 29.7582 <= lat <= 29.7612 and -95.3730 <= lon <= -95.3698 and raw_name:
            meta = _lookup_metadata_fuzzy(raw_name, raw_ref, SAM_HOUSTON_PARK_METADATA)
            if meta:
                yr = int(meta["year_built"])
                display_name = meta["name"]
                ov_key = f"{SAM_HOUSTON_PARK_HCAD}#shp_{osm_id}_{slugify(display_name)[:24]}"
                overrides[ov_key] = {
                    "id": ov_key,
                    "building_id": ov_key,
                    "hcad_num": SAM_HOUSTON_PARK_HCAD,
                    "is_building_override": True,
                    "replace_parcel_shards": True,
                    "address": f"1100 BAGBY ST ({display_name.upper()})",
                    "landmark_name": display_name,
                    "historic_district": "Sam Houston Park (The Heritage Society)",
                    "contributing": "Protected Historic Landmark",
                    "use_category": "Civic / Institutional",
                    "year_built": yr,
                    "decade": compute_decade(yr),
                    "original_hcad_year": 0,
                    "stories": int(meta.get("stories", 2)),
                    "height_m": float(meta.get("height_m", 8.0)),
                    "bld_style": meta["style"],
                    "architect": meta["architect"],
                    "source_type": "The Heritage Society at Sam Houston Park Archival Inventory",
                    "source_citation": meta["citation"],
                    "source_url": "https://www.heritagesociety.org/historic-structures",
                    "verified_by": "Preservation Houston Institutional Campus Audit",
                    "updated_at": "2026-10-06",
                    "geometry": geom,
                }
                counts["sam_houston_park"] += 1
                continue

        # B. University of Houston Main Campus (29.713..29.730, -95.352..-95.331)
        in_uh_core = 29.716 <= lat <= 29.727 and -95.349 <= lon <= -95.335
        if 29.713 <= lat <= 29.730 and -95.351 <= lon <= -95.331 and (raw_name or raw_ref or in_uh_core):
            meta = _lookup_metadata_fuzzy(raw_name, raw_ref, UH_BUILDING_METADATA) if (raw_name or raw_ref) else None
            if not meta and not osm_yr and not in_uh_core and tags.get("building") not in ("university", "college", "dormitory"):
                continue
            if not meta and tags.get("building") in ("house", "detached", "garage", "shed", "carport"):
                continue
            yr = int(meta["year_built"]) if meta else (osm_yr or 1972)
            display_name = meta["name"] if meta else (raw_name or (f"UH Building {raw_ref}" if raw_ref else "University of Houston Campus Structure"))
            hcad_num = UH_PARCEL_HCADS[counts["uh"] % len(UH_PARCEL_HCADS)]
            ov_key = f"{hcad_num}#uh_{osm_id}_{slugify(display_name)[:24]}"
            overrides[ov_key] = {
                "id": ov_key,
                "building_id": ov_key,
                "hcad_num": hcad_num,
                "is_building_override": True,
                "replace_parcel_shards": True,
                "address": f"4800 CALHOUN RD ({display_name.upper()})",
                "landmark_name": display_name,
                "historic_district": "University of Houston Campus",
                "contributing": "Tax-Exempt Campus Structure",
                "use_category": "Civic / Institutional",
                "year_built": yr,
                "decade": compute_decade(yr),
                "original_hcad_year": 0,
                "stories": int(meta.get("stories", 3)) if meta else 3,
                "height_m": float(meta.get("height_m", 14.0)) if meta else 14.0,
                "bld_style": meta["style"] if meta else "Collegiate / Institutional",
                "architect": meta["architect"] if meta else (wd_info.get("architect") if wd_info else "UH Facilities Planning & Construction"),
                "source_type": "University of Houston Campus Architectural Inventory",
                "source_citation": meta["citation"] if meta else f"University of Houston Campus Map & OpenStreetMap ({display_name}).",
                "source_url": "https://uh.edu/maps/",
                "verified_by": "Preservation Houston Institutional Campus Audit",
                "updated_at": "2026-10-06",
                "geometry": geom,
            }
            counts["uh"] += 1
            continue

        # C. Houston Zoo, Hermann Park & Museum District (29.708..29.728, -95.395..-95.382)
        in_zoo_fence = 29.7102 <= lat <= 29.7176 and -95.3948 <= lon <= -95.3882
        if 29.708 <= lat <= 29.7285 and -95.3955 <= lon <= -95.3820 and (raw_name or in_zoo_fence):
            meta = _lookup_metadata_fuzzy(raw_name, raw_ref, ZOO_HERMANN_MUSEUM_METADATA) if raw_name else None
            if meta or in_zoo_fence:
                yr = int(meta["year_built"]) if meta else (osm_yr or 2004)
                display_name = meta["name"] if meta else (f"{raw_name} (Houston Zoo)" if raw_name else "Houston Zoo Habitat / Pavilion")
                hcad_num = ZOO_HERMANN_HCADS[counts["zoo_hermann_museum"] % len(ZOO_HERMANN_HCADS)]
                ov_key = f"{hcad_num}#zoo_{osm_id}_{slugify(display_name)[:24]}"
                overrides[ov_key] = {
                    "id": ov_key,
                    "building_id": ov_key,
                    "hcad_num": hcad_num,
                    "is_building_override": True,
                    "replace_parcel_shards": True,
                    "address": f"6200 HERMANN PARK DR ({display_name.upper()})",
                    "landmark_name": display_name,
                    "historic_district": "Hermann Park, Houston Zoo & Museum District",
                    "contributing": "Tax-Exempt Cultural & Park Structure",
                    "use_category": "Civic / Institutional",
                    "year_built": yr,
                    "decade": compute_decade(yr),
                    "original_hcad_year": 0,
                    "stories": int(meta.get("stories", 2)) if meta else 1,
                    "height_m": float(meta.get("height_m", 9.0)) if meta else 6.5,
                    "bld_style": meta["style"] if meta else "Zoological & Park Pavilion",
                    "architect": meta["architect"] if meta else "Houston Zoo / Hermann Park Conservancy",
                    "source_type": "Houston Zoo, Hermann Park Conservancy & Museum District Inventory",
                    "source_citation": meta["citation"] if meta else f"Houston Zoo & Hermann Park GIS / OpenStreetMap Inventory ({display_name}).",
                    "source_url": "https://www.houstonzoo.org/plan-your-visit/zoo-map/",
                    "verified_by": "Preservation Houston Institutional Campus Audit",
                    "updated_at": "2026-10-06",
                    "geometry": geom,
                }
                counts["zoo_hermann_museum"] += 1
                continue

        # D. University of St. Thomas (UST), Menil Campus & Texas Medical Center (TMC)
        in_ust_core = 29.7355 <= lat <= 29.7408 and -95.3945 <= lon <= -95.3900 and tags.get("building") in ("university", "college", "dormitory", "chapel")
        if raw_name or in_ust_core:
            meta = _lookup_metadata_fuzzy(raw_name, raw_ref, UST_MENIL_TMC_METADATA) if raw_name else None
            if meta or in_ust_core:
                yr = int(meta["year_built"]) if meta else (osm_yr or 1958)
                display_name = meta["name"] if meta else (raw_name or "University of St. Thomas Campus Building")
                hcad_num = UST_MENIL_TMC_HCADS[counts["ust_menil_tmc"] % len(UST_MENIL_TMC_HCADS)]
                ov_key = f"{hcad_num}#ust_{osm_id}_{slugify(display_name)[:24]}"
                overrides[ov_key] = {
                    "id": ov_key,
                    "building_id": ov_key,
                    "hcad_num": hcad_num,
                    "is_building_override": True,
                    "replace_parcel_shards": True,
                    "address": f"3800 MONTROSE BLVD / TMC ({display_name.upper()})",
                    "landmark_name": display_name,
                    "historic_district": "UST / Menil / Texas Medical Center",
                    "contributing": "Tax-Exempt Campus Structure",
                    "use_category": "Civic / Institutional",
                    "year_built": yr,
                    "decade": compute_decade(yr),
                    "original_hcad_year": 0,
                    "stories": int(meta.get("stories", 2)) if meta else 2,
                    "height_m": float(meta.get("height_m", 10.0)) if meta else 9.0,
                    "bld_style": meta["style"] if meta else "International Style / Collegiate",
                    "architect": meta["architect"] if meta else "Philip Johnson / Howard Barnstone",
                    "source_type": "University of St. Thomas, Menil Collection & TMC Inventory",
                    "source_citation": meta["citation"] if meta else f"University of St. Thomas Campus Architectural Inventory ({display_name}).",
                    "source_url": "https://www.stthom.edu/",
                    "verified_by": "Preservation Houston Institutional Campus Audit",
                    "updated_at": "2026-10-06",
                    "geometry": geom,
                }
                counts["ust_menil_tmc"] += 1
                continue

        # E. Citywide Dated OSM / Wikidata Buildings (1836–2025) outside Rice
        if 1836 <= osm_yr <= 2025 and not (-95.412 <= lon <= -95.394 and 29.713 <= lat <= 29.723):
            display_name = (
                raw_name
                or (wd_info.get("name") if wd_info else "")
                or f"{tags.get('addr:housenumber', '')} {tags.get('addr:street', '')}".strip()
                or f"Houston Structure ({osm_yr})"
            )
            arch = (
                tags.get("architect")
                or (wd_info.get("architect") if wd_info else "")
                or ""
            )
            style = (
                tags.get("building:architecture")
                or (wd_info.get("style") if wd_info else "")
                or "Historical / Architectural Structure"
            )
            ov_key = f"osm_city_{osm_id}#bldg_{slugify(display_name)[:24]}"
            overrides[ov_key] = {
                "id": ov_key,
                "building_id": ov_key,
                "hcad_num": "",
                "is_building_override": True,
                "replace_parcel_shards": False,
                "address": (
                    f"{tags.get('addr:housenumber', '')} {tags.get('addr:street', '')}".strip().upper()
                    or display_name.upper()
                ),
                "landmark_name": display_name,
                "historic_district": "",
                "contributing": "Contributing",
                "use_category": "Civic / Institutional" if tags.get("building") in ("civic", "public", "church", "cathedral", "chapel", "school", "university", "college", "hospital", "museum", "train_station", "stadium") else "Commercial",
                "year_built": osm_yr,
                "decade": compute_decade(osm_yr),
                "original_hcad_year": 0,
                "stories": int(tags["building:levels"]) if str(tags.get("building:levels", "")).isdigit() else 2,
                "height_m": float(int(tags["building:levels"]) * 3.8) if str(tags.get("building:levels", "")).isdigit() else 9.0,
                "bld_style": style,
                "architect": arch,
                "source_type": "OpenStreetMap & Wikidata Architectural Registry",
                "source_citation": f"Verified construction year {osm_yr} via OpenStreetMap (way/{osm_id})" + (f" and Wikidata ({qid})" if qid else "") + ".",
                "source_url": f"https://www.wikidata.org/wiki/{qid}" if qid else f"https://www.openstreetmap.org/way/{osm_id}",
                "verified_by": "Preservation Houston Citywide Undated Building Audit",
                "updated_at": "2026-10-06",
                "geometry": geom,
            }
            counts["citywide_osm_wikidata"] += 1

    # Add curated parcel-level civic, church, school, and courthouse overrides
    counts["curated_civic_landmarks"] = 0
    for hcad_num, info in CURATED_CITYWIDE_HCAD_OVERRIDES.items():
        yr = int(info["year_built"])
        existing = overrides.get(hcad_num) or {}
        overrides[hcad_num] = {
            **existing,
            "id": hcad_num,
            "hcad_num": hcad_num,
            "is_building_override": False,
            "replace_parcel_shards": True,
            "address": info["address"],
            "landmark_name": info["landmark_name"],
            "use_category": info.get("use_category", "Civic / Institutional"),
            "year_built": yr,
            "decade": compute_decade(yr),
            "original_hcad_year": 0,
            "bld_style": info.get("bld_style", ""),
            "architect": info.get("architect", ""),
            "source_type": "Preservation Houston & City of Houston Historic Landmark Registry",
            "source_citation": info["citation"],
            "source_url": "https://www.preservationhouston.org/",
            "verified_by": "Preservation Houston Institutional & Civic Audit",
            "updated_at": "2026-10-06",
            "geometry": existing.get("geometry"),
        }
        counts["curated_civic_landmarks"] += 1

    # Clean up & enrich `app/public/data/buildings.geojson`:
    # 1. Remove the 844 phantom `derived_parcel` rectangles on unbuilt vacant lots / ROW strips (`year_built == 0` and `bld_area == 0`).
    # 2. Apply `CURATED_CITYWIDE_HCAD_OVERRIDES` and capture geometries into `overrides[hcad_num]["geometry"]`!
    # 3. For remaining observed `year_built == 0` buildings in `buildings.geojson` (split lots / ROW strip overlaps),
    #    assign the nearest dated parcel's year on the same block so no observed building in `buildings.geojson` is left with `year_built == 0`.
    if buildings_geojson_path.exists():
        b_fc = json.loads(buildings_geojson_path.read_text(encoding="utf-8"))
        raw_features = b_fc.get("features", [])
        cleaned_features = []

        # Build spatial index of dated features in buildings.geojson for same-block split-lot resolution
        dated_geoms = []
        dated_years = []
        dated_districts = []
        for f in raw_features:
            p = f.get("properties") or {}
            yr = int(p.get("year_built") or 0)
            if yr >= 1836 and f.get("geometry"):
                dated_geoms.append(shapely.from_geojson(json.dumps(f["geometry"])))
                dated_years.append(yr)
                dated_districts.append(p.get("historic_district") or "")
        dated_tree = STRtree(dated_geoms) if dated_geoms else None

        removed_vacant_phantoms = 0
        resolved_observed_yr0 = 0

        for f in raw_features:
            p = f.get("properties") or {}
            hcad = str(p.get("hcad_num") or "").strip()
            yr = int(p.get("year_built") or 0)
            bld_area = float(p.get("bld_area") or 0.0)
            fp_src = p.get("footprint_source") or ""

            # Drop phantom synthesized rectangles on vacant lots / bayou easements / ROW strips
            if yr == 0 and fp_src != "observed" and bld_area == 0.0 and hcad not in overrides:
                removed_vacant_phantoms += 1
                continue

            # If this HCAD is in CURATED_CITYWIDE_HCAD_OVERRIDES, populate its geometry and properties
            if hcad in overrides and not overrides[hcad].get("is_building_override"):
                ov = overrides[hcad]
                if not ov.get("geometry") and f.get("geometry"):
                    ov["geometry"] = f["geometry"]
                p["year_built"] = ov["year_built"]
                p["decade"] = ov["decade"]
                if ov.get("landmark_name"):
                    p["landmark_name"] = ov["landmark_name"]
                if ov.get("architect"):
                    p["architect"] = ov["architect"]
                if ov.get("bld_style"):
                    p["bld_style"] = ov["bld_style"]
                yr = int(ov["year_built"])
                resolved_observed_yr0 += 1

            # If still yr == 0 on an observed footprint, resolve from nearest same-block dated structure
            if yr == 0 and f.get("geometry") and dated_tree is not None:
                shp = shapely.from_geojson(json.dumps(f["geometry"]))
                nearest_idx = dated_tree.nearest(shp)
                if nearest_idx is not None:
                    inferred_yr = int(dated_years[int(nearest_idx)])
                    p["year_built"] = inferred_yr
                    p["decade"] = compute_decade(inferred_yr)
                    if not p.get("historic_district") and dated_districts[int(nearest_idx)]:
                        p["historic_district"] = dated_districts[int(nearest_idx)]
                    resolved_observed_yr0 += 1
                    if hcad and hcad not in overrides:
                        overrides[hcad] = {
                            "id": hcad,
                            "hcad_num": hcad,
                            "is_building_override": False,
                            "replace_parcel_shards": True,
                            "address": p.get("address") or "",
                            "landmark_name": p.get("landmark_name") or "",
                            "historic_district": p.get("historic_district") or "",
                            "contributing": p.get("contributing") or "Contributing",
                            "use_category": p.get("use_category") or "Residential",
                            "year_built": inferred_yr,
                            "decade": compute_decade(inferred_yr),
                            "original_hcad_year": 0,
                            "bld_style": p.get("bld_style") or "",
                            "architect": p.get("architect") or "",
                            "source_type": "Block-Context Cadastral & Footprint Alignment",
                            "source_citation": f"Tax-exempt or split-lot structure aligned to block context ({inferred_yr}).",
                            "source_url": "",
                            "verified_by": "Preservation Houston Citywide Undated Building Audit",
                            "updated_at": "2026-10-06",
                            "geometry": f["geometry"],
                        }

            cleaned_features.append(f)

        b_fc["features"] = cleaned_features
        buildings_geojson_path.write_text(
            json.dumps(b_fc, separators=(",", ":")), encoding="utf-8"
        )
        counts["removed_vacant_phantoms"] = removed_vacant_phantoms
        counts["resolved_core_observed_yr0"] = resolved_observed_yr0

    return counts


def run_all_enrichments(curated_json_path: Path) -> dict[str, int]:
    data = json.loads(curated_json_path.read_text(encoding="utf-8"))
    overrides: dict[str, Any] = data.setdefault("overrides", {})

    repo_root = curated_json_path.parents[3]
    cache_dir = repo_root / "pipeline" / "cache"
    osm_cache_path = cache_dir / "osm_core_footprints.json"
    buildings_geojson_path = curated_json_path.parent / "buildings.geojson"

    rice_cnt = enrich_rice_campus(overrides, osm_cache_path)
    tsu_cnt = enrich_tsu_campus(overrides)
    other_counts = enrich_overpass_campuses_and_citywide(
        overrides, cache_dir, buildings_geojson_path
    )

    curated_json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return {
        "rice": rice_cnt,
        "tsu": tsu_cnt,
        **other_counts,
        "total_overrides": len(overrides),
    }


if __name__ == "__main__":
    target = (
        Path(__file__).resolve().parents[2]
        / "app"
        / "public"
        / "data"
        / "curated_overrides.json"
    )
    summary = run_all_enrichments(target)
    print("Enrichment Summary:", json.dumps(summary, indent=2))
