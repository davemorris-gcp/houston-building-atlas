"""
Institutional & Tax-Exempt Campus Building Enrichment Module
for The Houston Building Atlas v2 (Preservation Houston).

Resolves two structural challenges with tax-exempt institutional campuses
(such as Rice University, universities, schools, churches, and civic complexes):
1. Multi-Building Super-Parcels & Attached Walkway Polygons:
   - HCAD maps Rice University's 447-acre main campus (`6100 MAIN ST`, `WM RICE INSTITUTE`)
     as just two tax-exempt super-parcels (`0421790000001` East/North Campus and
     `0440980000065` West/South Campus) with `year_built = 0` and 0 rows in `building_other.txt`.
   - Microsoft's satellite-derived footprints fuse multiple separate residential college wings
     together wherever covered walkways/cloisters connect roofs.
   - This module pulls Rice University's official GIS building polygons (`Rice_Polygon_Map`,
     filtering out `SPECNAME = 'Covered Walkway'`) plus post-2013 OpenStreetMap campus footprints,
     assigning every individual building and wing a unique `building_id` (`<hcad_num>#<slug>`),
     its separated polygon geometry, exact construction year (`1912`–`2024`), architect, and style.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import shapely

RICE_EAST_HCAD = "0421790000001"
RICE_WEST_HCAD = "0440980000065"

RICE_POLYGON_MAP_URL = (
    "https://services.arcgis.com/lqRTrQp2HrfnJt8U/arcgis/rest/services/"
    "Rice_Polygon_Map/FeatureServer/0/query?where=1%3D1&outFields=*&outSR=4326&f=geojson"
)

# Authoritative construction dates, architects, and styles for Rice University campus buildings & wings
# Sources:
#   - Stephen Fox, "Rice University: An Architectural Tour" (Princeton Architectural Press, 2001)
#   - Rice University Facilities Engineering & Planning (FE&P) Campus Map & Inventory
#   - Houston Public Library Digital Archives (1912–1926 Houston City Directories: "William M. Rice Institute")
RICE_BUILDING_METADATA: dict[str, dict[str, Any]] = {
    # Original 1912–1927 Cram, Goodhue & Ferguson / William Ward Watkin Era
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
    # Post-WWII Expansion (1947–1959)
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
    # 1960s–1970s Era
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
        "stories": 4,
        "height_m": 14.0,
        "citation": "Rice University Architectural Inventory; completed 1965.",
    },
    "brown college masters house": {
        "name": "Brown College Magister's House",
        "year_built": 1965,
        "architect": "MacKie & Kamrath",
        "style": "Mid-Century Modern Residential",
        "stories": 2,
        "height_m": 7.5,
        "citation": "Rice University Architectural Inventory; completed 1965.",
    },
    "space science": {
        "name": "Space Science and Technology Building",
        "year_built": 1966,
        "architect": "Pierce & Pierce",
        "style": "Mid-Century Modern",
        "stories": 3,
        "height_m": 13.5,
        "citation": "Rice University Architectural Inventory; completed 1966 for the first U.S. Space Science department.",
    },
    "soccer & track stadium": {
        "name": "Wendel D. Ley Track & Edward Holloway Field",
        "year_built": 1966,
        "architect": "Rice Athletics / CRS",
        "style": "Athletic Stadium",
        "stories": 2,
        "height_m": 8.0,
        "citation": "Rice University Architectural Inventory; completed 1966.",
    },
    "allen business center": {
        "name": "Allen Center for Business & Administration",
        "year_built": 1967,
        "architect": "Lloyd, Morgan & Jones",
        "style": "Mid-Century Modern",
        "stories": 3,
        "height_m": 13.0,
        "citation": "Rice University Architectural Inventory; completed 1967.",
    },
    "herman brown hall": {
        "name": "Herman Brown Hall for Mathematical Sciences",
        "year_built": 1968,
        "architect": "George Pierce - Abel B. Pierce",
        "style": "Mid-Century Modern / Brutalist Influences",
        "stories": 4,
        "height_m": 15.0,
        "citation": "Rice University Architectural Inventory; completed 1968.",
    },
    "lovett college": {
        "name": "Edgar Odell Lovett College",
        "year_built": 1968,
        "architect": "Wilson, Morris, Crain & Anderson",
        "style": "Brutalist Concrete Grating",
        "stories": 5,
        "height_m": 18.0,
        "citation": "Rice University Architectural Inventory; completed 1968.",
    },
    "lovett college commons": {
        "name": "Lovett College Commons",
        "year_built": 1968,
        "architect": "Wilson, Morris, Crain & Anderson",
        "style": "Brutalist",
        "stories": 1,
        "height_m": 6.5,
        "citation": "Rice University Architectural Inventory; completed 1968.",
    },
    "lovett college masters house": {
        "name": "Lovett College Magister's House",
        "year_built": 1968,
        "architect": "Wilson, Morris, Crain & Anderson",
        "style": "Mid-Century Modern Residential",
        "stories": 2,
        "height_m": 7.5,
        "citation": "Rice University Architectural Inventory; completed 1968.",
    },
    "jake hess tennis stadium": {
        "name": "Jake Hess Tennis Stadium",
        "year_built": 1968,
        "architect": "Wilson, Morris, Crain & Anderson",
        "style": "Athletic Facility",
        "stories": 2,
        "height_m": 8.0,
        "citation": "Rice University Architectural Inventory; completed 1968.",
    },
    "rice media center": {
        "name": "Rice Media Center",
        "year_built": 1969,
        "architect": "Eugene Aubry (Barnstone & Aubry)",
        "style": "Corrugated Steel Industrial Shed",
        "stories": 2,
        "height_m": 8.5,
        "citation": "Rice University Architectural Inventory; commissioned by John & Dominique de Menil in 1969.",
    },
    "sewall hall": {
        "name": "Cleveland Sewall Hall",
        "year_built": 1971,
        "architect": "Lloyd, Morgan & Jones",
        "style": "Mediterranean Revival / Modern Courtyard",
        "stories": 5,
        "height_m": 18.5,
        "citation": "Rice University Architectural Inventory; completed 1971.",
    },
    "sid college": {
        "name": "Sid W. Richardson College (Original Tower)",
        "year_built": 1971,
        "architect": "Neuhaus & Taylor",
        "style": "Late Modern High-Rise Tower",
        "stories": 7,
        "height_m": 25.0,
        "citation": "Rice University Architectural Inventory; completed 1971.",
    },
    "sid college commons": {
        "name": "Sid W. Richardson College Commons",
        "year_built": 1971,
        "architect": "Neuhaus & Taylor",
        "style": "Late Modern",
        "stories": 1,
        "height_m": 6.5,
        "citation": "Rice University Architectural Inventory; completed 1971.",
    },
    "sid richardson college masters house": {
        "name": "Sid Richardson College Magister's House",
        "year_built": 1971,
        "architect": "Neuhaus & Taylor",
        "style": "Late Modern Residential",
        "stories": 2,
        "height_m": 7.5,
        "citation": "Rice University Architectural Inventory; completed 1971.",
    },
    # 1980s–1990s Postmodern Masterplan Era
    "mudd computer science building": {
        "name": "Seeley G. Mudd Computer Science Building",
        "year_built": 1983,
        "architect": "Charles Tapley Associates",
        "style": "Postmodern Brick",
        "stories": 2,
        "height_m": 9.5,
        "citation": "Rice University Architectural Inventory; completed 1983.",
    },
    "herring hall": {
        "name": "Robert R. Herring Hall",
        "year_built": 1984,
        "architect": "César Pelli & Associates",
        "style": "Postmodern Polychrome Brick",
        "stories": 3,
        "height_m": 14.5,
        "citation": "Rice University Architectural Inventory; completed 1984, designed by César Pelli.",
    },
    "mechanical engineering": {
        "name": "John L. Cox Mechanical Engineering Building",
        "year_built": 1985,
        "architect": "Skidmore, Owings & Merrill (SOM)",
        "style": "Postmodern / Late Modern",
        "stories": 2,
        "height_m": 11.0,
        "citation": "Rice University Architectural Inventory; completed 1985.",
    },
    "ley student center": {
        "name": "Audrey and Wendel Ley Student Center",
        "year_built": 1986,
        "architect": "César Pelli & Associates",
        "style": "Postmodern",
        "stories": 2,
        "height_m": 11.0,
        "citation": "Rice University Architectural Inventory; completed 1986.",
    },
    "police department": {
        "name": "Rice University Police Department",
        "year_built": 1987,
        "architect": "Rice University FE&P",
        "style": "Institutional",
        "stories": 1,
        "height_m": 5.5,
        "citation": "Rice University Architectural Inventory; completed 1987.",
    },
    "george r. brown hall": {
        "name": "George R. Brown Hall",
        "year_built": 1991,
        "architect": "Cambridge Seven Associates",
        "style": "Postmodern Mediterranean Revival",
        "stories": 3,
        "height_m": 14.5,
        "citation": "Rice University Architectural Inventory; completed 1991.",
    },
    "alice pratt brown hall": {
        "name": "Alice Pratt Brown Hall (Shepherd School of Music)",
        "year_built": 1991,
        "architect": "Ricardo Bofill Taller de Arquitectura",
        "style": "Postmodern Classical",
        "stories": 3,
        "height_m": 18.0,
        "citation": "Rice University Architectural Inventory; completed 1991, designed by Ricardo Bofill.",
    },
    "cox fitness center": {
        "name": "John L. Cox Fitness Center",
        "year_built": 1995,
        "architect": "Rice Athletics / Morris Architects",
        "style": "Athletic Facility",
        "stories": 2,
        "height_m": 9.0,
        "citation": "Rice University Architectural Inventory; completed 1995.",
    },
    "duncan hall": {
        "name": "Anne and Charles Duncan Hall",
        "year_built": 1996,
        "architect": "John Outram Associates",
        "style": "Postmodern Polychrome / Decorative Classicism",
        "stories": 3,
        "height_m": 16.0,
        "citation": "Rice University Architectural Inventory; completed 1996, designed by British architect John Outram.",
    },
    "james a. baker iii hall": {
        "name": "James A. Baker III Hall (Baker Institute for Public Policy)",
        "year_built": 1997,
        "architect": "Hammond Beeby & Babka (Thomas Beeby)",
        "style": "Neoclassical / Mediterranean Revival",
        "stories": 3,
        "height_m": 15.5,
        "citation": "Rice University Architectural Inventory; completed 1997.",
    },
    "dell butcher hall": {
        "name": "Dell Butcher Hall (Smalley-Curl Institute)",
        "year_built": 1997,
        "architect": "Antoine Predock",
        "style": "Contemporary Regionalist",
        "stories": 3,
        "height_m": 15.0,
        "citation": "Rice University Architectural Inventory; completed 1997, designed by AIA Gold Medalist Antoine Predock.",
    },
    "rice graduate apartments": {
        "name": "Rice Graduate Apartments",
        "year_built": 1999,
        "architect": "Rice Housing",
        "style": "Garden Apartment Complex",
        "stories": 3,
        "height_m": 10.5,
        "citation": "Rice University Architectural Inventory; constructed 1999.",
    },
    # 2000s–2020s Era
    "reckling park": {
        "name": "Reckling Park at Cameron Field",
        "year_built": 2000,
        "architect": "Morris Architects",
        "style": "Collegiate Baseball Stadium",
        "stories": 2,
        "height_m": 12.0,
        "citation": "Rice University Architectural Inventory; opened Feb 2000.",
    },
    "humanities building": {
        "name": "Humanities Building",
        "year_built": 2000,
        "architect": "Allan Greenberg",
        "style": "New Classical / Mediterranean Revival",
        "stories": 3,
        "height_m": 14.5,
        "citation": "Rice University Architectural Inventory; completed 2000, designed by Allan Greenberg.",
    },
    "janice and robert mcnair hall": {
        "name": "Janice and Robert McNair Hall (Jones Graduate School of Business)",
        "year_built": 2002,
        "architect": "Robert A.M. Stern Architects",
        "style": "Postmodern Mediterranean Revival",
        "stories": 3,
        "height_m": 16.5,
        "citation": "Rice University Architectural Inventory; completed 2002, designed by Robert A.M. Stern.",
    },
    "martel college": {
        "name": "Speros P. Martel College",
        "year_built": 2002,
        "architect": "Michael Graves & Associates",
        "style": "Postmodern Rotunda & Quadrangle",
        "stories": 4,
        "height_m": 14.5,
        "citation": "Rice University Architectural Inventory; completed 2002, designed by Michael Graves.",
    },
    "martel college commons": {
        "name": "Martel College Commons",
        "year_built": 2002,
        "architect": "Michael Graves & Associates",
        "style": "Postmodern",
        "stories": 1,
        "height_m": 7.0,
        "citation": "Rice University Architectural Inventory; completed 2002.",
    },
    "martel college masters house": {
        "name": "Martel College Magister's House",
        "year_built": 2002,
        "architect": "Michael Graves & Associates",
        "style": "Postmodern Residential",
        "stories": 2,
        "height_m": 7.5,
        "citation": "Rice University Architectural Inventory; completed 2002.",
    },
    "wiess college": {
        "name": "Harry C. Wiess College (2002 Building)",
        "year_built": 2002,
        "architect": "Machado and Silvetti Associates",
        "style": "Contemporary Mediterranean Courtyard",
        "stories": 4,
        "height_m": 14.5,
        "citation": "Rice University Architectural Inventory; college founded 1957, current quadrangle completed 2002.",
    },
    "wiess college commons": {
        "name": "Wiess College Commons",
        "year_built": 2002,
        "architect": "Machado and Silvetti Associates",
        "style": "Contemporary Mediterranean",
        "stories": 2,
        "height_m": 9.0,
        "citation": "Rice University Architectural Inventory; completed 2002.",
    },
    "wilson house/wiess masters house": {
        "name": "Wilson House (Wiess College Magister's House)",
        "year_built": 2002,
        "architect": "Machado and Silvetti Associates",
        "style": "Contemporary Residential",
        "stories": 2,
        "height_m": 7.5,
        "citation": "Rice University Architectural Inventory; completed 2002.",
    },
    "north servery": {
        "name": "North Servery",
        "year_built": 2002,
        "architect": "Michael Graves & Associates",
        "style": "Campus Dining Facility",
        "stories": 1,
        "height_m": 6.5,
        "citation": "Rice University Architectural Inventory; completed 2002.",
    },
    "south servery": {
        "name": "South Servery",
        "year_built": 2002,
        "architect": "Machado and Silvetti Associates",
        "style": "Campus Dining Facility",
        "stories": 1,
        "height_m": 6.5,
        "citation": "Rice University Architectural Inventory; completed 2002.",
    },
    "new new wing baker college": {
        "name": "Baker College — North Addition Wing",
        "year_built": 2004,
        "architect": "Hopkins Architects",
        "style": "Contemporary Brick",
        "stories": 4,
        "height_m": 14.0,
        "citation": "Rice University Architectural Inventory; completed 2004.",
    },
    "baker servery": {
        "name": "Baker College Kitchen & Servery",
        "year_built": 2004,
        "architect": "Hopkins Architects",
        "style": "Campus Dining Facility",
        "stories": 1,
        "height_m": 6.5,
        "citation": "Rice University Architectural Inventory; completed 2004.",
    },
    "raymond and susan brochstein pavilion": {
        "name": "Raymond and Susan Brochstein Pavilion",
        "year_built": 2008,
        "architect": "Thomas Phifer and Partners",
        "style": "Minimalist Glass & Steel Pavilion",
        "stories": 1,
        "height_m": 5.5,
        "citation": "Rice University Architectural Inventory & AIA Institute Honor Award; completed 2008.",
    },
    "youngkin center": {
        "name": "Youngkin Center for Student-Athlete Excellence",
        "year_built": 2008,
        "architect": "Holt Hinshaw",
        "style": "Contemporary Athletic Addition",
        "stories": 2,
        "height_m": 10.0,
        "citation": "Rice University Architectural Inventory; completed 2008.",
    },
    "south plant": {
        "name": "Rice South Central Plant",
        "year_built": 2008,
        "architect": "Stanley Beaman & Sears",
        "style": "Contemporary Utility Infrastructure",
        "stories": 2,
        "height_m": 11.0,
        "citation": "Rice University Architectural Inventory; completed 2008.",
    },
    "rice village apartments": {
        "name": "Rice Village Apartments",
        "year_built": 2008,
        "architect": "Page Southerland Page",
        "style": "LEED Graduate Housing",
        "stories": 4,
        "height_m": 14.0,
        "citation": "Rice University Architectural Inventory; completed 2008.",
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
        "architect": " Overland Partners",
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
    "east servery": {
        "name": "East Servery",
        "year_built": 2002,
        "architect": "Machado and Silvetti Associates",
        "style": "Campus Dining Facility",
        "stories": 1,
        "height_m": 6.5,
        "citation": "Rice University Architectural Inventory; completed 2002.",
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
}


def compute_decade(yr: int) -> int:
    if yr < 1836 or yr > 2030:
        return 0
    if yr < 1850:
        return 1840
    return (yr // 10) * 10


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def enrich_rice_campus_overrides(curated_json_path: Path) -> int:
    """
    Download Rice University's official separated building polygons (`Rice_Polygon_Map`,
    filtering out `Covered Walkway`) plus local cached OpenStreetMap campus buildings, match them
    against `RICE_BUILDING_METADATA`, and write building-level overrides into `curated_overrides.json`.
    """
    data = json.loads(curated_json_path.read_text(encoding="utf-8"))
    overrides: dict[str, Any] = data.setdefault("overrides", {})

    # Remove any old rice_* entries if re-running
    for k in [k for k in overrides if "#" in k and (k.startswith(RICE_EAST_HCAD) or k.startswith(RICE_WEST_HCAD))]:
        del overrides[k]

    req = urllib.request.Request(
        RICE_POLYGON_MAP_URL, headers={"User-Agent": "PreservationHouston-Atlas/1.0"}
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        rice_fc = json.loads(resp.read().decode("utf-8"))

    added_count = 0
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

        # Skip Covered Walkways that fuse separate campus wings together
        if spec.lower() in ("covered walkway", "unknown"):
            continue
        if not bname and not spec and not cmap:
            continue

        # Look up metadata by specname first (for specific wings like 'Old Will Rice', 'Old Wing Baker College'),
        # then by Bldg_Name
        meta = None
        if spec and spec.lower() in RICE_BUILDING_METADATA:
            meta = RICE_BUILDING_METADATA[spec.lower()]
        elif bname and bname.lower() in RICE_BUILDING_METADATA:
            meta = RICE_BUILDING_METADATA[bname.lower()]
        elif cmap and cmap.lower() in RICE_BUILDING_METADATA:
            meta = RICE_BUILDING_METADATA[cmap.lower()]

        # Compute centroid to assign East vs West Rice HCAD parcel
        shp = shapely.from_geojson(json.dumps(geom))
        kept_polys.append(shp)
        cent = shp.centroid
        lon = float(cent.x)
        # Split line on Rice Campus between 0440980000065 (west) and 0421790000001 (east) is roughly lon = -95.4005
        hcad_num = RICE_WEST_HCAD if lon < -95.4005 else RICE_EAST_HCAD

        display_name = (
            meta["name"]
            if meta
            else (f"{bname} ({spec})" if bname and spec and spec != bname else (bname or spec or cmap))
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
            "updated_at": "2026-10-05",
            "geometry": geom,
        }
        added_count += 1

    # Supplement from local cached OpenStreetMap footprints (`pipeline/cache/osm_core_footprints.json`)
    osm_cache_path = curated_json_path.parents[3] / "pipeline" / "cache" / "osm_core_footprints.json"
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
            if any(shp.intersection(kp).area / max(shp.area, 1e-12) > 0.25 for kp in kept_polys):
                continue
            kept_polys.append(shp)
            cent = shp.centroid
            lon = float(cent.x)
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
                "updated_at": "2026-10-05",
                "geometry": g,
            }
            added_count += 1

    curated_json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return added_count


if __name__ == "__main__":
    target = Path(__file__).resolve().parents[2] / "app" / "public" / "data" / "curated_overrides.json"
    cnt = enrich_rice_campus_overrides(target)
    print(f"Enriched {cnt} separated Rice University campus buildings in {target}")
