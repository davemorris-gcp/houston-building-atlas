#!/usr/bin/env python3
"""
Builds the Historical Waterways (Bayous, Streams, Creeks, Buried Gullies & Oxbows)
and Historical Railroads (Pioneer Mainlines, Abandoned Rail-Trails, Streetcar /
Interurban Lines & Historic Train Depots) layers for The Houston Building Atlas.

Sources combined:
1. City of Houston ArcGIS FeatureServers (`services.arcgis.com/NummVBqZSIJKUeVR`):
   - `Water_Line_Texas_ClippedCOH/FeatureServer/0` (USGS NHD flowlines in Houston)
   - `Major_Waterways_November_2019/FeatureServer/29` (Major Regional Waterways)
   - `PWE_COH_Maintained_Waterways/FeatureServer/0` (includes Storm Sewer culverts for
     Slaughterpen Bayou, City Ditch, Yates Gully, Cypress Slough, Pine Gully, Rummel Creek, etc.)
   - `Railroads_November_2019/FeatureServer/4` (US Census TIGER historic rail names)
   - `Columbia_Tap_Trail/FeatureServer/0` & `Harrisburg_Sunset_Trail/FeatureServer/7`
2. USGS National Hydrography Dataset (`hydro.nationalmap.gov/arcgis/rest/services/nhd/MapServer/6`)
3. TxDOT ArcGIS FeatureServers (`services.arcgis.com/KTcxiTD9dsQw4r7Z`):
   - `Texas_Railroads/FeatureServer/0` (Active rail subdivisions, branches, and yards)
   - `Texas_Railroads_Deprecated/FeatureServer/0` (Pulled & Inactive/Abandoned historic tracks)
4. Archival Cartography (1869/1891 Houston Bird's-Eye Views, 1915/1922 USGS Topographic
   Quadrangles, Sanborn Fire Insurance Maps, and Houston Electric Co. 1913–1927 Route Maps):
   - Lost / buried historic gullies (Harris Gully, Pecore / Stude Gully, First Ward / Sawyers Gully,
     Fourth Ward / San Felipe Gully, Second Ward / Frost Town Gully, Quality Hill Slaughterhouse Gully)
   - Pre-channelization natural bayou oxbows (Brady Island 1853/1914 Old Channel, Buffalo Bayou
     Frost Town & Shepherd Loops, White Oak Bayou Stude/Hogg Park Meanders, Turkey Bend Oxbow)
   - Historic Electric Streetcar & Interurban lines (1874–1940) and Historic Train Depots/Junctions.
"""

import json
import math
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path
from shapely.geometry import LineString, MultiLineString, Point, shape, mapping
from shapely.ops import linemerge, unary_union

ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / "pipeline" / "cache"
OVERLAYS_PATH = ROOT / "app" / "public" / "data" / "overlays.json"
SEARCH_INDEX_PATH = ROOT / "app" / "public" / "data" / "search_index.json"

HEADERS = {"User-Agent": "HoustonBuildingAtlas/1.0 (PreservationHouston)"}


def fetch_arcgis_features(url: str, where: str = "1=1", bbox: str = None, out_fields: str = "*") -> list:
    """Fetch all features with pagination from an ArcGIS REST FeatureServer/MapServer layer."""
    all_features = []
    offset = 0
    page_size = 1000
    while True:
        params = {
            "where": where,
            "outFields": out_fields,
            "returnGeometry": "true",
            "outSR": "4326",
            "f": "json",
            "resultOffset": str(offset),
            "resultRecordCount": str(page_size),
        }
        if bbox:
            params["geometry"] = bbox
            params["geometryType"] = "esriGeometryEnvelope"
            params["inSR"] = "4326"
            params["spatialRel"] = "esriSpatialRelIntersects"
        qurl = f"{url}/query?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(qurl, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        feats = data.get("features") or []
        all_features.extend(feats)
        if not data.get("exceededTransferLimit") or len(feats) < page_size:
            break
        offset += len(feats)
    return all_features


def esri_paths_to_shapely(geom_dict: dict):
    """Convert Esri JSON polyline {'paths': [...]} to Shapely LineString / MultiLineString."""
    if not geom_dict:
        return None
    paths = geom_dict.get("paths") or []
    valid_lines = []
    for p in paths:
        if len(p) >= 2:
            pts = [(round(float(pt[0]), 6), round(float(pt[1]), 6)) for pt in p]
            try:
                ln = LineString(pts)
                if ln.length > 0:
                    valid_lines.append(ln)
            except Exception:
                pass
    if not valid_lines:
        return None
    if len(valid_lines) == 1:
        return valid_lines[0]
    try:
        merged = linemerge(valid_lines)
        return merged
    except Exception:
        return MultiLineString(valid_lines)


def round_coords_geom(geom, tol: float = 0.00006, precision: int = 5):
    """Simplify and round geometry coordinates to keep GeoJSON compact and fast."""
    if geom is None or geom.is_empty:
        return None
    if tol > 0 and geom.geom_type in ("LineString", "MultiLineString"):
        geom = geom.simplify(tol, preserve_topology=True)
    m = mapping(geom)

    def _round_seq(seq):
        if not seq:
            return seq
        if isinstance(seq[0], (int, float)):
            return [round(float(seq[0]), precision), round(float(seq[1]), precision)]
        return [_round_seq(item) for item in seq]

    m["coordinates"] = _round_seq(m["coordinates"])
    return m


def approx_length_miles(geom) -> float:
    """Compute approximate length in miles at Houston latitude (~29.76 deg N)."""
    if geom is None or geom.is_empty:
        return 0.0
    # 1 deg lat ~ 69.0 mi, 1 deg lon at 29.76N ~ 60.0 mi -> avg ~ 64.5 mi/deg
    return round(float(geom.length) * 64.5, 2)


# ============================================================================
# CURATED HISTORICAL WATERWAY METADATA & BURIED GULLIES / OXBOWS
# ============================================================================

WATERWAY_HISTORICAL_DOSSIERS = {
    "Buffalo Bayou": {
        "alt_names": ["Río de los Cíbolos", "Upper & Lower Buffalo Bayou", "Allen's Landing Channel"],
        "waterway_type": "bayou",
        "status": "Natural Meandering & Partially Channelized Tidewater Bayou",
        "watershed": "Buffalo Bayou Watershed",
        "era_notes": "1826 Harrisburg Trading Post · 1836 Allen Brothers Townsite · 1914 Houston Ship Channel · 1940s Addicks & Barker Dams",
        "historic_significance": (
            "Houston's founding waterway and lifeblood. In August 1836, Augustus Chapman Allen and John Kirby Allen "
            "purchased the John Austin survey at the confluence of Buffalo Bayou and White Oak Bayou ('Allen's Landing'), "
            "advertising it as the head of navigation for Galveston Bay steamboats. West of Sabine Street, the bayou cut "
            "a deep wooded ravine through Fourth Ward (Freedmen's Town), First Ward, Sixth Ward, and Memorial Park; east of "
            "Main Street, its loops around Frost Town, Second Ward, and Harrisburg were dredged into the Houston Ship Channel in 1914."
        ),
    },
    "Houston Ship Channel": {
        "alt_names": ["Lower Buffalo Bayou", "Turning Basin to Galveston Bay Navigation Channel"],
        "waterway_type": "bayou",
        "status": "Deep-Draft Dredged Navigation Channel (Opened Nov. 10, 1914)",
        "watershed": "Buffalo Bayou / San Jacinto Estuary",
        "era_notes": "1870s Charles Morgan Cut · 1900 Galveston Hurricane Catalyst · 1914 Deep-Water Opening",
        "historic_significance": (
            "Engineered by dredging and straightening lower Buffalo Bayou from the Magnolia Park Turning Basin past "
            "Brady Island, Harrisburg, Manchester, Pasadena, San Jacinto Battleground, and Morgan's Point into Galveston Bay. "
            "Opened on November 10, 1914 when President Woodrow Wilson fired a celebratory cannon by telegraph, transforming "
            "Houston into a global cotton, oil refining, and petrochemical port."
        ),
    },
    "White Oak Bayou": {
        "alt_names": ["Whiteoak Bayou", "Walker's Creek (1820s Austin Colony Grants)"],
        "waterway_type": "bayou",
        "status": "Channelized Historic Bayou & Greenway Corridor (Confluences at Allen's Landing)",
        "watershed": "White Oak Bayou Watershed",
        "era_notes": "1824 John Austin Grant · 1836 Townsite Confluence · 1891 Heights & 1907 Woodland Heights Bridges · 1960s USACE Channelization",
        "historic_significance": (
            "Known as Walker's Creek in early Mexican-era land grants, White Oak Bayou joins Buffalo Bayou at Allen's Landing. "
            "Its steep bluffs separated Old Sixth Ward and First Ward on the south from Houston Heights, Woodland Heights, and "
            "Germantown on the north. Crossed by the historic Houston Avenue Bridge, Main Street Viaduct (1913), Studemont Bridge, "
            "and MKT Railroad trestle; lined with concrete by the U.S. Army Corps of Engineers in the 1960s after severe 1929 and 1935 floods."
        ),
    },
    "Little White Oak Bayou": {
        "alt_names": ["Little Whiteoak Bayou", "Woodland Park & Hogg Park Ravine"],
        "waterway_type": "bayou",
        "status": "Partially Open Wooded Ravine & Channelized Tributary",
        "watershed": "White Oak Bayou Watershed",
        "era_notes": "1860s Beauchamp Springs · 1903 Woodland Park Pleasure Grounds · 1910 Independence Heights",
        "historic_significance": (
            "Flows south from Northline through Independence Heights (Texas's first African American municipality, incorporated 1915), "
            "Lindale Park, and between Woodland Heights and Germantown/Near Northside before joining White Oak Bayou at Wright-Bembry / Hogg Park. "
            "Its wooded ravine at Woodland Park was developed by William A. Wilson in 1903–1907 as a streetcar nature excursion park with "
            "Beauchamp Springs mineral waters and rustic footbridges."
        ),
    },
    "Brays Bayou": {
        "alt_names": ["Braes Bayou (Historical Spelling)", "Hermann Park & MacGregor Parkway Channel"],
        "waterway_type": "bayou",
        "status": "Channelized Major Bayou (1920s George Kessler Parkway & 1950s USACE Concrete Channel)",
        "watershed": "Brays Bayou Watershed",
        "era_notes": "1836 Battle of San Jacinto March · 1913 George Kessler Park Plan · 1926 MacGregor Parkway · 1950s Channelization",
        "historic_significance": (
            "Historically spelled 'Braes Bayou' (preserved in North and South Braeswood Blvd), this 31-mile bayou flows east through "
            "Bellaire/Meyerland, Hermann Park, the Texas Medical Center, Riverside Terrace, MacGregor Park, Idylwood (Country Club Estates), "
            "and Forest Park Lawndale before entering Buffalo Bayou at historic Harrisburg. Landscape architect George E. Kessler's 1913 "
            "master plan envisioned Brays Bayou as Houston's scenic parkway spine."
        ),
    },
    "Sims Bayou": {
        "alt_names": ["Simms Bayou"],
        "waterway_type": "bayou",
        "status": "Channelized Bayou & Greenway (Project Brays / HCFCD Restored)",
        "watershed": "Sims Bayou Watershed",
        "era_notes": "1830s Sims Survey · 1912 Park Place Streetcar Suburb · 1953 Glenbrook Valley",
        "historic_significance": (
            "Major southern Houston bayou flowing east through Hiram Clarke, South Park, Garden Villas, Reveille, "
            "Glenbrook Valley Historic District (where its curving ravine shaped the 1950s mid-century modern ranch street layout), "
            "Park Place, and Manchester into the Houston Ship Channel."
        ),
    },
    "Hunting Bayou": {
        "alt_names": ["Fifth Ward & Galena Park Bayou"],
        "waterway_type": "bayou",
        "status": "Open & Channelized Bayou",
        "watershed": "Hunting Bayou Watershed",
        "era_notes": "1860s T&NO Railroad · Fifth Ward & Kashmere Gardens · Galena Park",
        "historic_significance": (
            "Rises in historic Fifth Ward / Kashmere Gardens and winds east through Pleasantville, Jacinto City, and "
            "Galena Park before emptying into the Houston Ship Channel near the San Jacinto confluence."
        ),
    },
    "Greens Bayou": {
        "alt_names": ["Green's Bayou"],
        "waterway_type": "bayou",
        "status": "Open & Channelized Bayou",
        "watershed": "Greens Bayou Watershed",
        "era_notes": "1830s Greens Survey · Northeast Houston & Ship Channel Tributary",
        "historic_significance": (
            "Drains 212 square miles of north and northeast Houston, flowing past Aldine, East Houston, and Normandy "
            "southward to the Houston Ship Channel."
        ),
    },
    "Halls Bayou": {
        "alt_names": ["Hall's Bayou"],
        "waterway_type": "bayou",
        "status": "Open & Channelized Tributary of Greens Bayou",
        "watershed": "Greens Bayou Watershed",
        "era_notes": "Northside / Jensen Drive / Tidwell Historic Corridor",
        "historic_significance": (
            "Major tributary of Greens Bayou flowing east across North Houston, Airline Drive, and Homestead Road."
        ),
    },
    "Spring Branch": {
        "alt_names": ["Spring Branch Creek", "Kolbe Settlement Creek"],
        "waterway_type": "creek",
        "status": "Historic Spring-Fed Creek & Tributary of Buffalo Bayou",
        "watershed": "Buffalo Bayou Watershed",
        "era_notes": "1830 Karl Kolbe German Settlement · 1848 St. Peter United Church of Christ · Memorial Villages",
        "historic_significance": (
            "Spring-fed creek that gave its name to the historic 1830s–1840s German farming community of Spring Branch. "
            "Flows southeast through Spring Branch and the Memorial Villages into Buffalo Bayou west of Memorial Park."
        ),
    },
    "Brickhouse Gully": {
        "alt_names": ["Brick House Gully"],
        "waterway_type": "creek",
        "status": "Concrete-Lined Tributary of White Oak Bayou",
        "watershed": "White Oak Bayou Watershed",
        "era_notes": "19th-Century Northwest Prairie Drainage · Oak Forest & Mangum Manor",
        "historic_significance": (
            "Major western tributary of White Oak Bayou draining Spring Branch North, Fairbanks, and Mangum Manor "
            "into White Oak Bayou near Watonga Parkway and Oak Forest."
        ),
    },
    "Cole Creek": {
        "alt_names": ["Cole Creek Tributary"],
        "waterway_type": "creek",
        "status": "Channelized Tributary of White Oak Bayou",
        "watershed": "White Oak Bayou Watershed",
        "era_notes": "Northwest Houston / Carverdale / Fairbanks",
        "historic_significance": (
            "Historic northwest creek flowing east into White Oak Bayou north of Pinemont Drive."
        ),
    },
    "Vogel Creek": {
        "alt_names": ["Vogel's Creek"],
        "waterway_type": "creek",
        "status": "Open & Channelized Tributary of White Oak Bayou",
        "watershed": "White Oak Bayou Watershed",
        "era_notes": "19th-Century German Farmstead Creek · Acres Homes & Inwood",
        "historic_significance": (
            "Named for early German-Texan settlers in northwest Harris County; flows south through Inwood and "
            "west of Acres Homes into White Oak Bayou."
        ),
    },
    "Country Club Bayou": {
        "alt_names": ["East End / Gus Wortham Golf Course Bayou", "vital Third Ward / East End Tributary"],
        "waterway_type": "creek",
        "status": "Partially Culverted & Open Historic Creek (Gus Wortham Park / Wayside)",
        "watershed": "Brays Bayou Watershed",
        "era_notes": "1908 Houston Country Club Course · 1913 Eastwood · 1923 Idylwood",
        "historic_significance": (
            "Flows east from the Third Ward / University of Houston area through the historic 1908 Houston Country Club "
            "(now Gus Wortham Park Golf Course, Houston's oldest 18-hole golf course) where its natural bluffs and ravines "
            "formed the signature water hazards before joining Brays Bayou."
        ),
    },
    "Plum Creek": {
        "alt_names": ["Plum Creek Tributary of Sims Bayou"],
        "waterway_type": "creek",
        "status": "Channelized Historic Creek",
        "watershed": "Sims Bayou Watershed",
        "era_notes": "Reveille, Meadowbrook & Pecan Park Drainage",
        "historic_significance": (
            "Historic southeast Houston creek draining Pecan Park, Golfcrest, and Meadowbrook southward into Sims Bayou "
            "near Broadway and Bellfort."
        ),
    },
    "Pine Gully": {
        "alt_names": ["Pine Gully Ravine"],
        "waterway_type": "creek",
        "status": "Partially Culverted Historic Gully & Tributary",
        "watershed": "Buffalo Bayou / Clear Lake Watershed",
        "era_notes": "Historic Magnolia Park / East End & Clear Lake Ravine",
        "historic_significance": (
            "Historic gully system documented in City of Houston Public Works records with both open channel and "
            "buried storm-sewer segments."
        ),
    },
    "Berry Bayou": {
        "alt_names": ["Berry Creek", "Berry Gully"],
        "waterway_type": "bayou",
        "status": "Channelized Southeast Houston Bayou",
        "watershed": "Sims Bayou Watershed",
        "era_notes": "South Houston, Hobby Airport & Edgebrook Corridor",
        "historic_significance": (
            "Major southeastern tributary of Sims Bayou draining the historic 1920s–1940s aviation corridor around "
            "William P. Hobby Airport (1940 Air Terminal), South Houston, and Edgebrook."
        ),
    },
    "Keegans Bayou": {
        "alt_names": ["Keegan's Bayou"],
        "waterway_type": "bayou",
        "status": "Channelized Tributary of Brays Bayou",
        "watershed": "Brays Bayou Watershed",
        "era_notes": "Southwest Houston / Sharpstown / Braeburn",
        "historic_significance": (
            "Major southwest tributary joining Brays Bayou near Braeburn Country Club and Gessner Road."
        ),
    },
    "Willow Waterhole Bayou": {
        "alt_names": ["Willow Waterhole"],
        "waterway_type": "bayou",
        "status": "Channelized Tributary & Detention Greenway of Brays Bayou",
        "watershed": "Brays Bayou Watershed",
        "era_notes": "19th-Century Prairie Watering Hole · Westbury & Meyerland",
        "historic_significance": (
            "Historic prairie watering hole and tributary of Brays Bayou through Westbury and Meyerland."
        ),
    },
    "Rummel Creek": {
        "alt_names": ["Rummel Creek Storm Culvert & Open Channel"],
        "waterway_type": "creek",
        "status": "Partially Culverted & Open Creek",
        "watershed": "Buffalo Bayou Watershed",
        "era_notes": "Memorial West & Nottingham Forest",
        "historic_significance": (
            "Flows south from I-10 West through Memorial into Buffalo Bayou at Terry Hershey Park; upper reaches "
            "are carried in municipal storm culverts."
        ),
    },
    "Turkey Creek": {
        "alt_names": ["Turkey Run Gully"],
        "waterway_type": "creek",
        "status": "Natural & Channelized Tributary of Buffalo Bayou",
        "watershed": "Buffalo Bayou Watershed",
        "era_notes": "Addicks Reservoir & West Memorial",
        "historic_significance": (
            "Historic western tributary feeding Buffalo Bayou below Addicks Reservoir."
        ),
    },
    "Slaughterpen Bayou": {
        "alt_names": ["Slaughterpen Gully", "East End / Harrisburg Buried Bayou"],
        "waterway_type": "buried_gully",
        "status": "Buried in Municipal Storm Sewer (PWE Storm Culvert)",
        "watershed": "Buffalo Bayou / Ship Channel Watershed",
        "era_notes": "1860s–1900s East End Cattle Slaughterhouses · Buried 1920s–1940s",
        "historic_significance": (
            "Once an open tidal bayou and ravine on Houston's East Side named for the 19th-century cattle pens and "
            "slaughterhouses that lined its banks near Buffalo Bayou. As Second Ward, Eastwood, and Magnolia Park "
            "industrialized, Slaughterpen Bayou was enclosed in underground municipal storm culverts (still tracked by "
            "name as 'Slaughterpen Bayou - Storm Sewer' in City of Houston Public Works GIS)."
        ),
    },
    "City Ditch": {
        "alt_names": ["1870s Municipal Drainage Canal", "City Ditch Storm Sewer"],
        "waterway_type": "buried_gully",
        "status": "Buried 19th-Century Municipal Drainage Canal (PWE Storm Sewer)",
        "watershed": "Buffalo Bayou / Brays Bayou Watershed",
        "era_notes": "1870s–1890s Reconstruction & Gilded Age Municipal Drainage Ditch · Enclosed 20th Century",
        "historic_significance": (
            "Excavated in the late 19th century as one of Houston's earliest engineered municipal drainage canals to drain "
            "the flat coastal prairie south of Downtown, later enclosed into a trunk underground storm sewer ('City Ditch' in COH PWE GIS)."
        ),
    },
    "Yates Gully": {
        "alt_names": ["Yates Gully Storm Culvert"],
        "waterway_type": "buried_gully",
        "status": "Buried Historic Gully (PWE Storm Sewer)",
        "watershed": "Brays / Buffalo Bayou Watershed",
        "era_notes": "Early 20th-Century Ravine Enclosed in Storm Sewer",
        "historic_significance": (
            "Historic natural drainage gully enclosed in City of Houston storm sewers during 20th-century neighborhood development."
        ),
    },
    "Cypress Slough": {
        "alt_names": ["Cypress Slough Storm Sewer"],
        "waterway_type": "buried_gully",
        "status": "Buried Historic Slough (PWE Storm Sewer)",
        "watershed": "White Oak / Greens Bayou Watershed",
        "era_notes": "Prairie Slough Enclosed in Municipal Storm Culvert",
        "historic_significance": (
            "Historic prairie slough now carried underground in City of Houston storm sewer conduits."
        ),
    },
}

# Curated lost/buried gullies & pre-channelization oxbows from 1915 USGS Topo / Sanborn / Bird's-Eye Maps
CURATED_BURIED_GULLIES_AND_OXBOWS = [
    {
        "id": "waterway_harris_gully",
        "name": "Harris Gully (Buried Historic Creek - Rice, Hermann Park & TMC)",
        "alt_names": ["Harris Bayou", "Rice Institute & Hermann Park Ravine", "Fannin / Sunset Box Culvert"],
        "waterway_type": "buried_gully",
        "status": "Buried Underground in Twin Box Culverts (Enclosed 1950s–1960s)",
        "watershed": "Brays Bayou Watershed",
        "era_notes": "1912 Rice Institute Campus Plan · 1914 Hermann Park · Enclosed 1950s–1960s · 2001 Tropical Storm Allison",
        "historic_significance": (
            "Houston's most consequential lost waterway. Shown prominently on the 1915 and 1922 USGS Houston Topographic "
            "Quadrangles, Harris Gully rose in West University Place along Sunset Blvd, curved directly through the eastern "
            "edge of the Rice University campus (where Cram, Goodhue & Ferguson sited bridges over its wooded ravine), wound "
            "past the Mecom Fountain site and Hermann Park Golf Course, carved a deep ravine through what became the Texas "
            "Medical Center (TMC), and emptied into Brays Bayou near MacGregor Park. Enclosed in massive underground box culverts "
            "under Sunset, Fannin, and MacGregor in the 1950s–1960s, the buried creek bed still governs TMC flood hydrology "
            "and famously backed up into Medical Center basements during Tropical Storm Allison in June 2001."
        ),
        "coords": [
            [-95.4265, 29.7212],
            [-95.4178, 29.7209],
            [-95.4085, 29.7203],
            [-95.4012, 29.7198],
            [-95.3964, 29.7192],
            [-95.3922, 29.7186],
            [-95.3895, 29.7155],
            [-95.3910, 29.7115],
            [-95.3948, 29.7082],
            [-95.3972, 29.7048],
        ],
    },
    {
        "id": "waterway_pecore_stude_gully",
        "name": "Pecore Gully / Stude Park Ravine (Woodland Heights & Norhill - Buried)",
        "alt_names": ["Woodland Heights West Ravine", "Highland & Bayland Dip Gully"],
        "waterway_type": "buried_gully",
        "status": "Buried / Culverted Ravine (Enclosed c. 1910–1925)",
        "watershed": "White Oak Bayou Watershed",
        "era_notes": "1907 Woodland Heights Plat · 1920 Norhill Addition · Stude Park Outfall",
        "historic_significance": (
            "Natural tributary ravine of White Oak Bayou that drained southwest from North Norhill (near Michaux & Pecore) "
            "across Merrill, Bayland, and Highland Streets down into White Oak Bayou at Stude Park. When William A. Wilson "
            "platted Woodland Heights (1907) and Will Hogg / Varner Realty platted Norhill (1920), portions of this ravine "
            "were graded over and placed into brick/concrete storm culverts, leaving the gentle topographic swales still "
            "visible along Pecore, Bayland, and Highland today."
        ),
        "coords": [
            [-95.3808, 29.7952],
            [-95.3826, 29.7921],
            [-95.3849, 29.7894],
            [-95.3868, 29.7866],
            [-95.3885, 29.7838],
            [-95.3902, 29.7812],
        ],
    },
    {
        "id": "waterway_san_felipe_fourth_ward_gully",
        "name": "San Felipe / Fourth Ward Gully (Freedmen's Town Ravine - Buried)",
        "alt_names": ["Freedmen's Town Gully", "Sabine Street & West Dallas Ravine"],
        "waterway_type": "buried_gully",
        "status": "Buried Historic Ravine (Enclosed Early 20th Century)",
        "watershed": "Buffalo Bayou Watershed",
        "era_notes": "1836 Townsite West Ravine · 1865 Freedmen's Town Founding · 1914 Brick Street Paving",
        "historic_significance": (
            "Deep natural gully that cut northeast across Fourth Ward / Freedmen's Town from near West Gray and Taft, "
            "crossing San Felipe Trail (West Dallas), Andrews, Ruthven, and Valentine Streets before plunging into Buffalo "
            "Bayou near Sabine Street. In the 1860s–1880s, low-lying marshy lots along this gully were sold to formerly "
            "enslaved families who founded Freedmen's Town, bridging the muddy ravine with footbridges and later laying "
            "hand-made manganese brick streets and curbs to channel its runoff."
        ),
        "coords": [
            [-95.3848, 29.7542],
            [-95.3822, 29.7561],
            [-95.3796, 29.7578],
            [-95.3774, 29.7596],
            [-95.3756, 29.7615],
        ],
    },
    {
        "id": "waterway_first_ward_sawyers_gully",
        "name": "Sawyers Gully / First Ward Spring Street Ravine (Buried)",
        "alt_names": ["First Ward Gully", "Sawyer & Edwards Street Ravine"],
        "waterway_type": "buried_gully",
        "status": "Buried Historic Ravine (Enclosed 1900s–1920s)",
        "watershed": "White Oak / Buffalo Bayou Watershed",
        "era_notes": "1856 H&TC Rail Yards · First Ward & Old Sixth Ward Border Ravine",
        "historic_significance": (
            "Historic drainage gully running south from the H&TC / MKT rail corridor near Sawyer and Spring Streets "
            "between First Ward and Old Sixth Ward down into Buffalo Bayou/White Oak Bayou. Shown on 1869 and 1891 "
            "Houston bird's-eye views as a wooded draw crossed by wooden wagon bridges and railroad trestles."
        ),
        "coords": [
            [-95.3802, 29.7765],
            [-95.3792, 29.7732],
            [-95.3779, 29.7704],
            [-95.3765, 29.7678],
        ],
    },
    {
        "id": "waterway_frost_town_caroline_gully",
        "name": "Frost Town & Caroline Street Gully (1836 Townsite East Ravine - Buried)",
        "alt_names": ["Downtown East Gully", "Frost Town Ravine"],
        "waterway_type": "buried_gully",
        "status": "Filled & Culverted 19th-Century Downtown Ravine",
        "watershed": "Buffalo Bayou Watershed",
        "era_notes": "1836 Allen Brothers Townsite · 1838 Frost Town German Settlement · Filled 1870s–1900s",
        "historic_significance": (
            "On Augustus Koch's 1873 Bird's-Eye View of Houston and early 1839–1869 maps, a steep gully cut northward "
            "along the eastern edge of the original 62-block townsite (near Caroline, Austin, and Chenevert Streets) "
            "separating Courthouse Square from the early German immigrant settlement of Frost Town on the bank of Buffalo Bayou. "
            "Bridged by timber trestles in the Republic of Texas era and gradually filled over clay and brick sewer arches "
            "as Downtown expanded eastward."
        ),
        "coords": [
            [-95.3628, 29.7525],
            [-95.3612, 29.7556],
            [-95.3596, 29.7588],
            [-95.3582, 29.7618],
            [-95.3574, 29.7641],
        ],
    },
    {
        "id": "waterway_quality_hill_second_ward_gully",
        "name": "Quality Hill / Second Ward Slaughterhouse Gully (Buried)",
        "alt_names": ["Second Ward East Ravine", "Navigation & Sampson Gully"],
        "waterway_type": "buried_gully",
        "status": "Buried Historic Ravine (Enclosed 1910s–1930s)",
        "watershed": "Buffalo Bayou Watershed",
        "era_notes": "1850s Quality Hill · 1880s Second Ward Industrial Expansion",
        "historic_significance": (
            "Natural ravine in historic Second Ward that drained north across Garrow, Commerce, and Navigation Blvd "
            "into the great bend of Buffalo Bayou east of Quality Hill."
        ),
        "coords": [
            [-95.3448, 29.7505],
            [-95.3435, 29.7542],
            [-95.3422, 29.7578],
            [-95.3412, 29.7611],
        ],
    },
    {
        "id": "waterway_brady_island_oxbow",
        "name": "Buffalo Bayou - Pre-1914 Brady Island Natural Oxbow (Old Channel)",
        "alt_names": ["Brady Island Horseshoe Bend", "Harrisburg Old Bayou Channel"],
        "waterway_type": "historic_oxbow",
        "status": "Historic Natural Bayou Meander (Cut Off by Houston Ship Channel Dredging, 1914)",
        "watershed": "Buffalo Bayou / Houston Ship Channel",
        "era_notes": "1826 John R. Harris Trading Post · 1853 BBB&C Railroad · 1909–1914 Ship Channel Straight Cut",
        "historic_significance": (
            "Before the U.S. Army Corps of Engineers excavated a straight deep-water navigation cut across the neck of "
            "this sharp southern horseshoe bend between 1909 and 1914, all steamboats traveling between Galveston and "
            "Houston had to navigate this tight natural loop around historic Harrisburg and Magnolia Park. Cutting the "
            "straight Ship Channel across the northern neck transformed the peninsula into Brady Island."
        ),
        "coords": [
            [-95.2862, 29.7228],
            [-95.2845, 29.7192],
            [-95.2812, 29.7168],
            [-95.2768, 29.7169],
            [-95.2738, 29.7195],
            [-95.2732, 29.7226],
        ],
    },
    {
        "id": "waterway_frost_town_oxbow",
        "name": "Buffalo Bayou - Pre-1935 Frost Town & McKee Street Natural Meander",
        "alt_names": ["Frost Town Bend", "Second Ward / Fifth Ward Historic Bayou Loop"],
        "waterway_type": "historic_oxbow",
        "status": "Historic Natural Bayou Meander (Straightened After 1929 & 1935 Floods)",
        "watershed": "Buffalo Bayou Watershed",
        "era_notes": "1838 Frost Town · 1910 McKee Street Bridge · 1935 Flood Channel Rectification",
        "historic_significance": (
            "Prior to post-1935 flood-control rectification, Buffalo Bayou made a tight double horseshoe bend immediately "
            "east of Allen's Landing around Frost Town (Second Ward) and the foot of McKee and Hardy Streets. Historic "
            "Survey and Ward boundaries still trace the original 19th-century thalweg of this meander."
        ),
        "coords": [
            [-95.3568, 29.7644],
            [-95.3542, 29.7672],
            [-95.3508, 29.7681],
            [-95.3482, 29.7654],
            [-95.3465, 29.7621],
        ],
    },
    {
        "id": "waterway_shepherd_tinsley_oxbows",
        "name": "Buffalo Bayou - Pre-1950s Shepherd to Sabine Natural Meander Loops",
        "alt_names": ["Cleveland Park & Spotts Park Cut-Off Oxbows", "Fourth Ward & Sixth Ward Bayou Loops"],
        "waterway_type": "historic_oxbow",
        "status": "Cut-Off Natural Bayou Oxbows (Straightened by USACE in 1950s)",
        "watershed": "Buffalo Bayou Watershed",
        "era_notes": "1915 USGS Houston Topo Map · 1924 Memorial Drive · 1950s USACE Channel Straightening",
        "historic_significance": (
            "Between Shepherd Drive and Sabine Street, natural Buffalo Bayou originally wound in deep horseshoe loops "
            "nearly twice as long as its present channel. In the 1950s, the U.S. Army Corps of Engineers cut straight "
            "pilot channels across the necks of the tighter bends near Jackson Hill/Spotts Park and Taft/Eleanor Tinsley "
            "Park to speed floodwaters downstream; the historic 1839–1903 Ward boundary between Fourth Ward and Sixth Ward "
            "still follows the original meandering centerline!"
        ),
        "coords": [
            [-95.4052, 29.7618],
            [-95.4018, 29.7649],
            [-95.3982, 29.7602],
            [-95.3935, 29.7646],
            [-95.3892, 29.7598],
            [-95.3845, 29.7642],
            [-95.3795, 29.7612],
        ],
    },
    {
        "id": "waterway_white_oak_stude_oxbows",
        "name": "White Oak Bayou - Pre-1960s Stude Park & Heights Natural Meanders",
        "alt_names": ["White Oak Bayou Historic Winding Channel", "Woodland Heights / First Ward Oxbow Loops"],
        "waterway_type": "historic_oxbow",
        "status": "Natural Winding Channel Prior to 1960s Concrete Trapezoidal Lining",
        "watershed": "White Oak Bayou Watershed",
        "era_notes": "1907 Woodland Heights · 1915 Stude Park · 1960s USACE Channel Rectification",
        "historic_significance": (
            "Before 1960s USACE flood control projects encased White Oak Bayou in a straightened concrete trapezoidal "
            "channel, the bayou wound in tight, tree-shaded S-curves through Stude Park and Hogg Park between Woodland "
            "Heights and First/Sixth Wards. Several platted subdivision borders in Woodland Heights and First Ward still "
            "follow the pre-1960s natural bank line."
        ),
        "coords": [
            [-95.3985, 29.7828],
            [-95.3945, 29.7801],
            [-95.3910, 29.7829],
            [-95.3865, 29.7788],
            [-95.3818, 29.7804],
            [-95.3768, 29.7745],
            [-95.3712, 29.7752],
            [-95.3662, 29.7702],
        ],
    },
]


# ============================================================================
# CURATED HISTORICAL RAILROAD, STREETCAR & DEPOT METADATA
# ============================================================================

# Mapping TxDOT SUBDIV / BRNCH / RR_ABRVN to Historical Pioneer Railroad Companies
TXDOT_SUBDIV_TO_HISTORICAL_RR = {
    "GLIDDEN": {
        "name": "Buffalo Bayou, Brazos & Colorado Ry. (BBB&C, 1853) / GH&SA 'Sunset Route'",
        "historic_company": "Buffalo Bayou, Brazos & Colorado Railway (1850) · Galveston, Harrisburg & San Antonio Ry. (1870) · Southern Pacific",
        "charter_year": 1850,
        "opened_year": 1853,
        "modern_operator": "Union Pacific Railroad (Glidden Subdivision) & Amtrak Sunset Limited",
        "rail_type": "mainline",
        "status": "Active Historic Mainline (First Operating Railroad in Texas, 1853)",
        "route_summary": "Harrisburg / East End west along Brays Bayou, Westpark, and US-90A through Stafford, Sugar Land, Richmond & Alleyton to San Antonio",
        "historic_significance": (
            "The oldest railroad in Texas and the oldest western component of the Southern Pacific system. Chartered on "
            "February 11, 1850 by San Jacinto hero Gen. Sidney Sherman and Boston capitalists, the BBB&C began laying track "
            "westward from Harrisburg on Buffalo Bayou in 1851 and ran its first train 20 miles to Stafford's Point in August "
            "1853. Reorganized in 1870 by Thomas W. Peirce as the Galveston, Harrisburg & San Antonio Railway (GH&SA), it "
            "completed the transcontinental 'Sunset Route' in 1883."
        ),
    },
    "EUREKA": {
        "name": "Houston & Texas Central Railway (H&TC, 1856 - North Mainline)",
        "historic_company": "Houston & Texas Central Railway (Chartered 1848 as Galveston & Red River Ry.) · Southern Pacific",
        "charter_year": 1848,
        "opened_year": 1856,
        "modern_operator": "Union Pacific Railroad (Eureka / Navasota Subdivision)",
        "rail_type": "mainline",
        "status": "Active Historic Mainline (Houston's Pioneer Northbound Trunk Line)",
        "route_summary": "Downtown Houston / Washington Ave & Chaney Junction northwest through Eureka Junction, Fairbanks, Cypress, Hockley & Hempstead to Dallas",
        "historic_significance": (
            "Spearheaded by Houston merchant Paul Bremond and Ebenezer Allen, the H&TC broke ground in Houston on January 1, "
            "1853 and opened its first 25 miles to Cypress in July 1856, reaching Hempstead before the Civil War, Dallas in "
            "1872, and the Red River in 1873. The H&TC made Houston the undisputed railroad hub of the Texas interior and "
            "established the massive Hardy Street Locomotive Shops and Grand Central Station."
        ),
    },
    "NAVASOTA": {
        "name": "Houston & Texas Central Railway (H&TC, 1856) / I&GN Navasota Cutoff",
        "historic_company": "Houston & Texas Central Railway (1856) / International & Great Northern RR",
        "charter_year": 1856,
        "opened_year": 1858,
        "modern_operator": "Union Pacific Railroad (Navasota Subdivision)",
        "rail_type": "mainline",
        "status": "Active Historic Mainline",
        "route_summary": "Northwest Harris County through Cypress, Hockley, and Waller toward Navasota and College Station",
        "historic_significance": (
            "Northern Harris County trunk line carrying historic H&TC and I&GN agricultural and passenger traffic between "
            "Houston, Hempstead, Navasota, and Central/North Texas."
        ),
    },
    "GALVESTON (UP)": {
        "name": "Galveston, Houston & Henderson Railroad (GH&H, 1853 / Opened 1860)",
        "historic_company": "Galveston, Houston & Henderson Railroad (1853) · Joint I&GN / MKT / MoPac Line",
        "charter_year": 1853,
        "opened_year": 1860,
        "modern_operator": "Union Pacific Railroad (Galveston Subdivision)",
        "rail_type": "mainline",
        "status": "Active Historic Mainline (Houston's First Rail Link to Galveston Bay)",
        "route_summary": "Downtown / East End & Harrisburg southeast along State Hwy 3 (Old Galveston Rd) through Dumont, Genoa, Ellington, Webster & League City to Galveston",
        "historic_significance": (
            "Chartered in 1853 and completed across the first Galveston Bay trestle bridge into Houston in 1860, the GH&H "
            "linked Buffalo Bayou steamboats and interior Texas railroads directly to the deep-water port of Galveston. "
            "In January 1863, Gen. John Bankhead Magruder used the GH&H to rush Confederate troops and artillery from Houston "
            "to recapture Galveston. Later jointly owned by Jay Gould's I&GN and the MKT ('The Katy')."
        ),
    },
    "BEAUMONT (UP)": {
        "name": "Texas & New Orleans Railroad (T&NO, 1856 / Opened 1860) & Gulf Coast Lines",
        "historic_company": "Texas & New Orleans Railroad (1856) · Beaumont, Sour Lake & Western Ry. (1904) · Southern Pacific / MoPac",
        "charter_year": 1856,
        "opened_year": 1860,
        "modern_operator": "Union Pacific Railroad (Beaumont / Lafayette Subdivision) & Amtrak Sunset Limited",
        "rail_type": "mainline",
        "status": "Active Historic Mainline (Eastern Spine of the Sunset Route)",
        "route_summary": "Fifth Ward / Englewood Yard northeast toward Liberty, Beaumont, Orange, and New Orleans",
        "historic_significance": (
            "Chartered in 1856 as the Sabine & Galveston Bay Railroad & Lumber Company and renamed the Texas & New Orleans "
            "Railroad in 1859, the T&NO completed its line from Houston to Orange in 1860. Combined with B.F. Yoakum's "
            "1904 Beaumont, Sour Lake & Western Railway (Gulf Coast Lines), it anchored Houston's eastern rail trade in "
            "East Texas pine lumber, Spindletop/Sour Lake crude oil, and transcontinental passenger trains."
        ),
    },
    "PALESTINE": {
        "name": "International & Great Northern Railroad (I&GN, 1871 - North Mainline)",
        "historic_company": "Houston & Great Northern RR (1866) · International & Great Northern RR (1873) · Missouri Pacific RR (MoPac, 1925)",
        "charter_year": 1866,
        "opened_year": 1871,
        "modern_operator": "Union Pacific Railroad (Palestine Subdivision)",
        "rail_type": "mainline",
        "status": "Active Historic Mainline",
        "route_summary": "Fifth Ward / Hardy Yards north through Aldine, Westfield, and Spring to Palestine, Longview & St. Louis",
        "historic_significance": (
            "Chartered in 1866 as the Houston & Great Northern Railroad and consolidated in 1873 into the International & "
            "Great Northern Railroad (I&GN), this line ran due north from Houston through Spring and Palestine to connect "
            "with Jay Gould's Texas & Pacific and Iron Mountain systems to St. Louis. Absorbed by Missouri Pacific ('MoPac') "
            "in 1925, it operated the famed 'Texas Eagle' passenger streamliner."
        ),
    },
    "LUFKIN": {
        "name": "Houston East & West Texas Railway (HE&WT - 'The Rabbit', 1875)",
        "historic_company": "Houston East & West Texas Railway (Paul Bremond, 1875) · Southern Pacific (1899)",
        "charter_year": 1875,
        "opened_year": 1877,
        "modern_operator": "Union Pacific Railroad (Lufkin Subdivision)",
        "rail_type": "mainline",
        "status": "Active Historic Mainline (Originally 3-Foot Narrow Gauge Timber Line)",
        "route_summary": "Fifth Ward / Tower 26 northeast along US-59 / Eastex corridor through Humble, New Caney & Cleveland to Lufkin and Shreveport",
        "historic_significance": (
            "Founded in 1875 by Houston railroad pioneer Paul Bremond and built as a 3-foot narrow-gauge railway deep into "
            "the Piney Woods of East Texas to haul virgin yellow pine timber to Houston's sawmills and lumber yards. "
            "Affectionately nicknamed 'The Rabbit' (and jokingly 'Hell Either Way Taken' from its initials HE&WT), its entire "
            "192-mile track from Houston to Shreveport was widened from narrow gauge to standard gauge in a single day on "
            "July 29, 1894."
        ),
    },
    "MYKAWA": {
        "name": "Gulf, Colorado & Santa Fe Railway (GC&SF - 'The Santa Fe', 1880)",
        "historic_company": "Gulf, Colorado & Santa Fe Railway (1873 / Houston Line 1880) · Atchison, Topeka & Santa Fe Ry.",
        "charter_year": 1873,
        "opened_year": 1880,
        "modern_operator": "BNSF Railway (Mykawa Subdivision)",
        "rail_type": "mainline",
        "status": "Active Historic Mainline",
        "route_summary": "East End / New South Yard south along Mykawa Road past Hobby Airport & Pearland to Alvin and Galveston",
        "historic_significance": (
            "Originally organized by Galveston merchants in 1873 to bypass Houston's yellow fever quarantines, the Gulf, "
            "Colorado & Santa Fe built a branch from Alvin north into Houston along Mykawa Road in 1880–1882 after acquiring "
            "the Atchison, Topeka & Santa Fe backing. Mykawa Road and the surrounding rice-farming settlement were named "
            "in 1906 for Japanese agriculturalist Shinpei Mykawa along the Santa Fe tracks."
        ),
    },
    "HOUSTON (BNSF)": {
        "name": "Trinity & Brazos Valley Ry. ('The Boll Weevil' / Burlington-Rock Island RR, 1907)",
        "historic_company": "Trinity & Brazos Valley Railway (1902/1907) · Burlington-Rock Island RR (1930) · Fort Worth & Denver / CRI&P",
        "charter_year": 1902,
        "opened_year": 1907,
        "modern_operator": "BNSF Railway (Houston / Teague Subdivision)",
        "rail_type": "mainline",
        "status": "Active Historic Mainline (Route of the 1936 Sam Houston Zephyr)",
        "route_summary": "Belt Junction in North Houston northwest along West Montgomery / SH-249 through Rosslyn, Tomball & Dobbin to Waxahachie and Dallas/Fort Worth",
        "historic_significance": (
            "Built into Houston in 1907 by B.F. Yoakum as the Trinity & Brazos Valley Railway (nicknamed 'The Boll Weevil') "
            "and reorganized in 1930 as the joint Burlington-Rock Island Railroad. On October 1, 1936, this track launched "
            "Texas into the streamlined age with the stainless-steel 'Sam Houston Zephyr' and 'Texas Rocket', covering the "
            "250 miles between Houston's Union Station and Dallas in just 4 hours at speeds up to 95 mph."
        ),
    },
    "HOUSTON EAST BELT": {
        "name": "Houston Belt & Terminal Railway - East Belt Line (HB&T, 1905)",
        "historic_company": "Houston Belt & Terminal Railway Co. (Organized 1905 by Santa Fe, Rock Island, Frisco & Gulf Coast Lines)",
        "charter_year": 1905,
        "opened_year": 1907,
        "modern_operator": "Union Pacific & BNSF Railway (Houston East Belt Subdivision)",
        "rail_type": "mainline",
        "status": "Active Historic Belt Railway (Built Union Station in 1911)",
        "route_summary": "Eastern perimeter belt linking Belt Junction, Settegast Yard, Fifth Ward, East End, Union Station (501 Crawford) & New South Yard",
        "historic_significance": (
            "Organized on August 31, 1905 by railroad financier B.F. Yoakum so four competing trunk railroads (GC&SF, "
            "Rock Island, T&BV, and StLB&M) could share a unified belt line around Houston and a grand central passenger "
            "terminal - Union Station (completed 1911, now the lobby of Daikin Park / Minute Maid Park)."
        ),
    },
    "HOUSTON WEST BELT": {
        "name": "Houston Belt & Terminal Railway - West Belt & Downtown Viaduct Line (HB&T, 1905)",
        "historic_company": "Houston Belt & Terminal Railway Co. (1905)",
        "charter_year": 1905,
        "opened_year": 1908,
        "modern_operator": "Union Pacific & BNSF Railway (Houston West Belt Subdivision)",
        "rail_type": "mainline",
        "status": "Active Historic Belt Railway",
        "route_summary": "Northside / Belt Junction south along Hardy/Maury, East Downtown (Union Station approach), and south to Congress & New South Yards",
        "historic_significance": (
            "Core inner-loop freight and passenger approach track of the 1905 Houston Belt & Terminal Railway connecting "
            "northbound trunk lines directly into Union Station and the southside classification yards."
        ),
    },
    "TERMINAL": {
        "name": "Southern Pacific / H&TC & T&NO Houston Terminal & Hardy/Englewood Yard Mainline",
        "historic_company": "Houston & Texas Central Ry. (1856) · Texas & New Orleans RR (1860) · Southern Pacific Terminal",
        "charter_year": 1856,
        "opened_year": 1860,
        "modern_operator": "Union Pacific Railroad (Houston Terminal Subdivision & Amtrak Sunset Limited)",
        "rail_type": "mainline",
        "status": "Active Historic Trunk Corridor (Hardy Street Shops, Warehouse District & Englewood Yard)",
        "route_summary": "East-West spine along White Oak & Buffalo Bayous through Chaney Jct, Washington Ave Amtrak Station, Hardy Street Yards, Fifth Ward (Tower 26) & Englewood Yard",
        "historic_significance": (
            "The historic heart of Southern Pacific's Houston operations, linking the H&TC from the west with the T&NO to "
            "the east directly along the north bank of White Oak and Buffalo Bayous past the 1880s Hardy Street Locomotive "
            "Shops, the Warehouse District (Nance/Sterrett/Rothwell), Tower 26, and the vast Englewood Classification Yard."
        ),
    },
    "KATY EUREKA INDUSTRIAL LEAD": {
        "name": "Missouri-Kansas-Texas Railroad ('The Katy' / MKT Eureka Lead, 1893)",
        "historic_company": "Missouri, Kansas & Texas Railway Co. of Texas ('The Katy', 1893)",
        "charter_year": 1880,
        "opened_year": 1893,
        "modern_operator": "Union Pacific Railroad (Katy Eureka Industrial Lead)",
        "rail_type": "mainline",
        "status": "Active Western Segment of the 1893 MKT ('Katy') Mainline",
        "route_summary": "Katy & Addicks east along I-10 / Old Katy Road into Eureka Yard & Washington Ave",
        "historic_significance": (
            "Built into Houston in April 1893 from Smithville and Sealy through the town of Katy (named for the M-K-T "
            "Railroad), giving Houston direct rail competition to St. Louis and Kansas City. While the eastern leg from "
            "Eureka Yard through the Heights to Downtown was converted into the MKT Hike-and-Bike Trail in the late 1990s, "
            "this western segment into Eureka Yard remains an active industrial rail lead."
        ),
    },
    "COLUMBIA TAP INDUSTRIAL LEAD": {
        "name": "Houston Tap & Brazoria Railway ('The Sugar Road' / Columbia Tap, 1856)",
        "historic_company": "Houston Tap Railroad (City of Houston, 1856) · Houston Tap & Brazoria Ry. (1856) · I&GN (1871)",
        "charter_year": 1856,
        "opened_year": 1856,
        "modern_operator": "Union Pacific (Southern Industrial Stub) & City of Houston (Columbia Tap Rail-Trail)",
        "rail_type": "abandoned_trail",
        "status": "Pioneer 1856 Municipal Railroad (Now Columbia Tap Rail-Trail & Southern Stub)",
        "route_summary": "East Downtown (Walker & Velasco) diagonally southwest through Third Ward, TSU, and Brays Bayou to Pierce Junction & Brazoria County",
        "historic_significance": (
            "Alarmed that the 1853 BBB&C Railroad at Harrisburg might bypass Houston entirely, Houston voters approved a "
            "municipal property tax in January 1856 to build their own 7-mile 'Houston Tap' railroad south to Pierce Junction "
            "on the BBB&C. Extended by Brazoria County planters later in 1856 as the Houston Tap & Brazoria Railway ('The Sugar "
            "Road') to East Columbia on the Brazos River, it hauled plantation sugar and cotton into Houston before being "
            "acquired by the I&GN in 1871. The inner-city corridor through Third Ward and EaDo is now the Columbia Tap Trail."
        ),
    },
    "CLINTON INDUSTRIAL LEAD": {
        "name": "Texas Transportation Company / Charles Morgan's Clinton Railroad (1866 / Opened 1876)",
        "historic_company": "Texas Transportation Company (Chartered 1866 by Charles Morgan) · T&NO / Southern Pacific (1896)",
        "charter_year": 1866,
        "opened_year": 1876,
        "modern_operator": "Union Pacific Railroad (Clinton Industrial Lead)",
        "rail_type": "mainline",
        "status": "Active Historic Port Rail Line",
        "route_summary": "Fifth Ward east along Clinton Drive parallel to the north bank of Buffalo Bayou to Clinton Docks & Galena Park",
        "historic_significance": (
            "Built in 1876 by shipping magnate Charles Morgan after he dredged the first 9-foot ship channel up Buffalo "
            "Bayou to his new deep-water steamship wharves at 'Clinton' (7 miles east of Downtown). Morgan built this "
            "7.9-mile railroad from Fifth Ward to Clinton so freight cars from the H&TC and T&NO could load directly onto "
            "his Morgan Line steamships."
        ),
    },
    "BAYTOWN": {
        "name": "Houston North Shore Railway (1925 Electric Interurban to Baytown)",
        "historic_company": "Houston North Shore Railway (Chartered 1925 by Harry K. Johnson) · Missouri Pacific Lines (1927)",
        "charter_year": 1925,
        "opened_year": 1927,
        "modern_operator": "Union Pacific Railroad (Baytown Subdivision)",
        "rail_type": "mainline",
        "status": "Active Industrial Rail Line (Built 1927 as Electric Interurban Railway)",
        "route_summary": "Fifth Ward / Lyons Ave east across Greens Bayou, Channelview, and San Jacinto River to Highlands and Baytown",
        "historic_significance": (
            "Texas's last electric interurban railway built. Opened in 1927 from Fifth Ward (Lyons & McCarty) east to the "
            "boomtown refineries of Goose Creek, Pelly, and Baytown, running electric interurban passenger cars in partnership "
            "with Houston Electric Co. streetcars until passenger service ended in 1948 (converted to diesel freight in 1961)."
        ),
    },
    "STRANG": {
        "name": "Galveston, Harrisburg & San Antonio Ry. - La Porte & Seabrook Branch (1893)",
        "historic_company": "La Porte, Houston & Northern RR (1892) · Galveston, Houston & Northern Ry. · GH&SA / Southern Pacific",
        "charter_year": 1892,
        "opened_year": 1893,
        "modern_operator": "Union Pacific Railroad (Strang / Barbours Cut Subdivision)",
        "rail_type": "mainline",
        "status": "Active Historic Bayshore Rail Line",
        "route_summary": "Harrisburg southeast through Pasadena, Deer Park, Strang (La Porte) & Sylvan Beach to Seabrook",
        "historic_significance": (
            "Built in 1892–1894 to connect Houston and Harrisburg to the new resort town of La Porte and the Sylvan Beach "
            "Pavilion on Galveston Bay; famous in the 1890s–1920s for weekend excursion trains before becoming a vital "
            "petrochemical and Barbours Cut container port corridor."
        ),
    },
    "POPP": {
        "name": "Houston & Texas Central / Hempstead-Austin Cutoff & Belt Connector",
        "historic_company": "Houston & Texas Central Ry. / Southern Pacific",
        "charter_year": 1856,
        "opened_year": 1895,
        "modern_operator": "Union Pacific Railroad (Popp Subdivision)",
        "rail_type": "mainline",
        "status": "Active Historic Connector",
        "route_summary": "Northwest Houston junction connector between Eureka and Hardy / West Belt corridors",
        "historic_significance": "Historic H&TC / Southern Pacific junction track linking Eureka and Northside rail corridors.",
    },
}

PTRA_HISTORICAL_INFO = {
    "name": "Port Terminal Railroad Association (PTRA - North & South Ship Channel Belt, 1924)",
    "historic_company": "Port Terminal Railroad Association (Organized June 1924 by Port of Houston & 6 Trunk Railroads)",
    "charter_year": 1924,
    "opened_year": 1924,
    "modern_operator": "Port Terminal Railroad Association (UP / BNSF / KCS / Port of Houston)",
    "rail_type": "mainline",
    "status": "Active Historic Port Belt Line",
    "route_summary": "North Side Main & South Side Main along both banks of the Houston Ship Channel from Turning Basin to Pasadena & Jacinto Port",
    "historic_significance": (
        "Established on June 11, 1924 by an agreement between the Harris County Houston Ship Channel Navigation District "
        "and every major trunk railroad entering Houston so that no single railroad could monopolize access to the "
        "wharves, grain elevators, cotton compresses, and oil refineries lining the Houston Ship Channel."
    ),
}

# Curated Abandoned / Rail-Trail Corridors, Historic Streetcar / Interurban Lines, and Historic Depots
CURATED_ABANDONED_RAILS_AND_STREETCARS = [
    # 1. Abandoned / Pulled Pioneer Rail Corridors
    {
        "id": "rail_mkt_katy_heights_mainline",
        "name": "Missouri-Kansas-Texas RR ('The Katy' / MKT Inner-Loop Mainline & Heights Spur, 1893)",
        "historic_company": "Missouri, Kansas & Texas Railway Co. ('The Katy', 1893–1988) · Pulled 1997 (Now MKT Hike-and-Bike Trail)",
        "charter_year": 1880,
        "opened_year": 1893,
        "modern_operator": "Abandoned 1997 · City of Houston MKT Trail (White Oak Bayou Greenway)",
        "rail_type": "abandoned_trail",
        "status": "Abandoned Pioneer Mainline (Converted to MKT Hike-and-Bike Trail)",
        "route_summary": "Eureka Yard east along 7th Street through Houston Heights, Shady Acres, Sawyer Yards & Studemont across White Oak Bayou to Downtown MKT Depot (Main & Shea)",
        "historic_significance": (
            "Completed into Houston in April 1893, the Missouri-Kansas-Texas ('The Katy') mainline ran due east from "
            "Eureka Junction through the southern Houston Heights (along 7th Street), crossed White Oak Bayou on a steel "
            "trestle at Studemont/Wright-Bembry Park, and terminated at the MKT Passenger & Freight Depot tucked beneath "
            "the 1913 Main Street Viaduct. After Union Pacific absorbed the Katy in 1988, the inner-loop tracks were pulled "
            "in 1997 and transformed into the MKT Hike-and-Bike Trail - preserving the historic 1893 railroad grade and "
            "bayou bridge."
        ),
        "coords": [
            [-95.4384, 29.7838],
            [-95.4245, 29.7834],
            [-95.4144, 29.7833],
            [-95.4046, 29.7833],
            [-95.3982, 29.7828],
            [-95.3885, 29.7753],
            [-95.3802, 29.7753],
            [-95.3769, 29.7753],
            [-95.3705, 29.7738],
            [-95.3644, 29.7723],
            [-95.3594, 29.7675],
        ],
    },
    {
        "id": "rail_saap_westpark_blodgett",
        "name": "San Antonio & Aransas Pass Railway ('The SAP' - Blodgett & Westpark Line, 1886)",
        "historic_company": "San Antonio & Aransas Pass Railway (Uriah Lott, 1886) · Southern Pacific (1925–1990s)",
        "charter_year": 1884,
        "opened_year": 1886,
        "modern_operator": "Abandoned 1990s (Now Westpark Tollway / METRO Transit Corridor & Southwest Trail)",
        "rail_type": "abandoned_trail",
        "status": "Abandoned 19th-Century Mainline (Tracks Pulled 1990s)",
        "route_summary": "Blodgett Depot (Almeda & Blodgett in Third Ward/Museum District) west along Blodgett/US-59 & Westpark Drive through Upper Kirby, Greenway Plaza & Bellaire toward Eagle Lake & San Antonio",
        "historic_significance": (
            "Built eastward into Houston in 1886–1887 by Uriah Lott's San Antonio & Aransas Pass Railway ('The SAP' or "
            "'Davy Crockett Route'), this line entered south Houston parallel to Westpark Drive, crossed Kirby and Shepherd, "
            "and ran along the Blodgett Street alignment between the Museum District and Third Ward to the SA&AP Blodgett "
            "Depot near Almeda Road. It spurred the early industrial and residential development of Bellaire (1908), "
            "West University Place, and Upper Kirby before Southern Pacific abandoned the inner-loop tracks in the 1990s."
        ),
        "coords": [
            [-95.5120, 29.7235],
            [-95.4850, 29.7248],
            [-95.4570, 29.7258],
            [-95.4476, 29.7270],
            [-95.4295, 29.7282],
            [-95.4175, 29.7290],
            [-95.4050, 29.7275],
            [-95.3920, 29.7262],
            [-95.3805, 29.7248],
            [-95.3715, 29.7238],
        ],
    },
    {
        "id": "rail_htc_grand_central_approach",
        "name": "H&TC / Southern Pacific Grand Central Station Passenger Approach (1856–1960)",
        "historic_company": "Houston & Texas Central Railway (1856) · Southern Pacific Lines (Closed 1959, Pulled 1960)",
        "charter_year": 1856,
        "opened_year": 1860,
        "modern_operator": "Abandoned & Removed 1960 (Site of Barbara Jordan Post Office / POST Houston)",
        "rail_type": "abandoned_trail",
        "status": "Demolished Historic Passenger Terminal Tracks (1856–1960)",
        "route_summary": "Chaney Junction / Washington Ave east along the south bank of White Oak Bayou into Grand Central Station (901 Franklin Ave at Bagby)",
        "historic_significance": (
            "For over a century, Houston's primary west-side passenger train approach ran along the south bank of White Oak "
            "Bayou directly to Grand Central Station at Franklin and Washington Avenues (where the 1886 Victorian depot and "
            "1934 Art Moderne terminal stood). In 1959–1960, Southern Pacific relocated passenger trains half a mile west to "
            "the modest 902 Washington Ave Amtrak station and sold the 16-acre Grand Central terminal complex for the main "
            "U.S. Post Office (now POST Houston)."
        ),
        "coords": [
            [-95.3885, 29.7695],
            [-95.3812, 29.7689],
            [-95.3752, 29.7676],
            [-95.3708, 29.7668],
            [-95.3665, 29.7658],
        ],
    },
    # 2. Historic Streetcar & Electric Interurban Lines (1874–1940)
    {
        "id": "rail_galveston_houston_interurban",
        "name": "Galveston-Houston Electric Railway ('The Interurban', 1911–1936)",
        "historic_company": "Galveston-Houston Electric Railway Co. (Stone & Webster Management, 1911–1936)",
        "charter_year": 1907,
        "opened_year": 1911,
        "modern_operator": "Removed 1936 (Right-of-Way Preserved as HL&P / CenterPoint High-Voltage Transmission Corridor)",
        "rail_type": "streetcar_interurban",
        "status": "Historic High-Speed Electric Interurban Railway (Operated Dec. 1911 – Oct. 1936)",
        "route_summary": "Downtown Interurban Terminal (Pierce & Travis) southeast through Midtown, Eastwood/Lawndale, Park Place, South Houston, Genoa, Webster, League City & Dickinson to Galveston",
        "historic_significance": (
            "Opened on December 5, 1911 by Stone & Webster, the Galveston-Houston Electric Railway was a marvel of "
            "early-20th-century electric transit: a 50-mile, grade-separated, catenary-powered line that whisked passengers "
            "between Downtown Houston and Galveston Island in 75 minutes at speeds exceeding 60 mph - winning the 'Electric "
            "Traction Speed Cup' in 1925 and 1926 as the fastest interurban in North America. It directly spurred the "
            "development of Park Place (1912), South Houston, and Glenbrook Valley before closing on October 31, 1936."
        ),
        "coords": [
            [-95.3698, 29.7502],
            [-95.3615, 29.7448],
            [-95.3485, 29.7362],
            [-95.3312, 29.7235],
            [-95.3125, 29.7085],
            [-95.2862, 29.6872],
            [-95.2580, 29.6625],
            [-95.2285, 29.6345],
            [-95.1820, 29.5890],
        ],
    },
    {
        "id": "rail_heights_blvd_streetcar",
        "name": "Houston Heights Boulevard Electric Streetcar Line (1891–1937)",
        "historic_company": "Omaha & South Texas Land Co. (1891) · Houston City Street Railway · Houston Electric Co.",
        "charter_year": 1891,
        "opened_year": 1892,
        "modern_operator": "Removed 1937 (Preserved as Heights Boulevard Esplanade & 19th Street Commercial Spine)",
        "rail_type": "streetcar_interurban",
        "status": "Historic Electric Streetcar Line (1891–1937)",
        "route_summary": "Downtown Market Square west via Washington Ave, north across White Oak Bayou up the center esplanade of Heights Boulevard (4th St to 20th St) and along 19th Street",
        "historic_significance": (
            "When Oscar Martin Carter and the Omaha & South Texas Land Company founded Houston Heights in 1891 as a standalone "
            "streetcar suburb 62 feet above Downtown Houston's yellow fever marshes, they built a wide, tree-lined 120-foot "
            "boulevard with a twin-track electric streetcar running up its landscaped center median from 4th Street to "
            "20th Street (extending along 19th Street). The streetcar median is why Heights Boulevard has its iconic "
            "60-foot parkway esplanade today."
        ),
        "coords": [
            [-95.3632, 29.7632],
            [-95.3715, 29.7662],
            [-95.3825, 29.7681],
            [-95.3948, 29.7695],
            [-95.3976, 29.7752],
            [-95.3976, 29.7815],
            [-95.3977, 29.7905],
            [-95.3978, 29.8038],
            [-95.4085, 29.8029],
        ],
    },
    {
        "id": "rail_woodland_heights_norhill_streetcar",
        "name": "Woodland Heights, Bayland Avenue & Norhill Streetcar Line (1907–1938)",
        "historic_company": "Houston Electric Company (Stone & Webster) & William A. Wilson Realty Co.",
        "charter_year": 1907,
        "opened_year": 1907,
        "modern_operator": "Removed 1938 (Preserved along Houston Ave, Bayland Ave, Euclid St & Michaux St)",
        "rail_type": "streetcar_interurban",
        "status": "Historic Electric Streetcar Line (1907–1938)",
        "route_summary": "Downtown north up Houston Avenue across White Oak Bayou & Woodland Park, east on Bayland Avenue through Woodland Heights to Euclid/Michaux, and north into Norhill",
        "historic_significance": (
            "To market Woodland Heights in 1907 as 'A Miniature City in a Forest of Magnificent Pines and Oaks - Twenty "
            "Minutes from Main Street', developer William A. Wilson partnered with the Houston Electric Company to run "
            "streetcars north on Houston Avenue past Woodland Park, turning east down Bayland Avenue and north up Euclid and "
            "Michaux Streets into Woodland Terrace and Norhill. The streetcar tracks on Bayland and Euclid explain the wide "
            "right-of-way and corner neighborhood commercial buildings along Bayland, Euclid, and Michaux."
        ),
        "coords": [
            [-95.3660, 29.7620],
            [-95.3728, 29.7662],
            [-95.3728, 29.7765],
            [-95.3729, 29.7848],
            [-95.3879, 29.7866],
            [-95.3832, 29.7866],
            [-95.3832, 29.7918],
            [-95.3806, 29.7918],
            [-95.3806, 29.7985],
        ],
    },
    {
        "id": "rail_montrose_courtlandt_streetcar",
        "name": "Montrose, Avondale & Courtlandt Place Electric Streetcar Line (1906–1937)",
        "historic_company": "Houston Electric Company & Montrose Land Co. (J.W. Link, 1911)",
        "charter_year": 1906,
        "opened_year": 1906,
        "modern_operator": "Removed 1937 (Montrose Blvd, Westheimer & Fairview Streetcar Suburb Spine)",
        "rail_type": "streetcar_interurban",
        "status": "Historic Electric Streetcar Line (1906–1937)",
        "route_summary": "Downtown southwest via Milam/Louisiana & Tuam/Fairview through Avondale, Courtlandt Place, and down Montrose Boulevard to Bissonnet",
        "historic_significance": (
            "Extended into the South End to serve Avondale (1907) and Courtlandt Place (1906), and rebuilt by lumberman "
            "John Wiley Link in 1911 down the palm-lined esplanades of Montrose Boulevard to sell lots in his master-planned "
            "suburb of Montrose."
        ),
        "coords": [
            [-95.3655, 29.7575],
            [-95.3735, 29.7478],
            [-95.3815, 29.7458],
            [-95.3912, 29.7456],
            [-95.3913, 29.7345],
            [-95.3914, 29.7258],
        ],
    },
    {
        "id": "rail_south_end_main_st_streetcar",
        "name": "South End / Main Street, Hermann Park & Rice Institute Streetcar Line (1891–1940)",
        "historic_company": "Houston City Street Railway (1874) · Houston Electric Company (Electrified 1891, Closed June 1940)",
        "charter_year": 1874,
        "opened_year": 1891,
        "modern_operator": "Removed June 1940 (Now Reborn along the Same Corridor as METRORail Red Line)",
        "rail_type": "streetcar_interurban",
        "status": "Historic Flagship Streetcar Spine (Last Houston Streetcar Line to Close, June 1940)",
        "route_summary": "Downtown Main Street south through Midtown, Eagle/Holman, Museum District, Hermann Park & Rice University to Sunset Blvd",
        "historic_significance": (
            "Begun as a mule-drawn streetcar line in 1874 and electrified on June 12, 1891, the South End Main Street line "
            "was Houston's busiest transit artery, carrying students to the opening of Rice Institute in 1912 and families to "
            "Hermann Park and the Houston Zoo. It made the final run of Houston's historic streetcar era on the night of "
            "June 8, 1940 - and 64 years later, the METRORail Red Line opened along the exact same Main Street corridor."
        ),
        "coords": [
            [-95.3592, 29.7642],
            [-95.3638, 29.7572],
            [-95.3712, 29.7460],
            [-95.3792, 29.7338],
            [-95.3895, 29.7215],
            [-95.3975, 29.7155],
        ],
    },
    {
        "id": "rail_westmoreland_bellaire_streetcar",
        "name": "Westmoreland & Bellaire Boulevard Streetcar ('Westmoreland Farms Line', 1904 / 1910–1927)",
        "historic_company": "South End Land Co. (1902) & Westmoreland Railroad Co. (William Wright Baldwin, 1910)",
        "charter_year": 1904,
        "opened_year": 1910,
        "modern_operator": "Removed 1927 (Preserved as the Wide Center Esplanade of Bellaire/Holcombe Boulevard)",
        "rail_type": "streetcar_interurban",
        "status": "Historic Streetcar & Suburban Trolley Line (1904–1927)",
        "route_summary": "Midtown / Westmoreland Historic District (Hawthorne & Mason) south and west down the center esplanade of Holcombe & Bellaire Boulevard into the City of Bellaire",
        "historic_significance": (
            "Burlington Railroad vice president William Wright Baldwin purchased the 9,449-acre Rice ranch in 1908 to develop "
            "the town of Bellaire and surrounding 'Westmoreland Farms' citrus/truck-garden estates. In December 1910, Baldwin "
            "opened the 'Toonerville Trolley' down the broad grassy center esplanade of Bellaire Boulevard, linking his earlier "
            "1902 Westmoreland Addition in Midtown to Bellaire Boulevard & South Rice Avenue."
        ),
        "coords": [
            [-95.3782, 29.7438],
            [-95.3862, 29.7418],
            [-95.3882, 29.7285],
            [-95.3910, 29.7062],
            [-95.4150, 29.7058],
            [-95.4420, 29.7055],
            [-95.4650, 29.7052],
        ],
    },
    {
        "id": "rail_harrisburg_eastwood_streetcar",
        "name": "Harrisburg, Eastwood & Magnolia Park Electric Streetcar Line (1892–1939)",
        "historic_company": "Houston City Street Railway (1892) · Houston Electric Company",
        "charter_year": 1892,
        "opened_year": 1892,
        "modern_operator": "Removed 1939 (Now Served by METRORail Green Line along Harrisburg Blvd)",
        "rail_type": "streetcar_interurban",
        "status": "Historic East End Electric Streetcar Spine (1892–1939)",
        "route_summary": "Downtown east via Capitol/Rusk & Harrisburg Boulevard through Second Ward, Eastwood (1913), Country Club Place & Magnolia Park to Harrisburg",
        "historic_significance": (
            "Electrified in 1892 along Harrisburg Road between Houston and historic Harrisburg, this streetcar line enabled "
            "William A. Wilson to develop Eastwood (1913) and Country Club Place as Craftsman and Four-Square streetcar "
            "suburbs for managers and workers along the newly opened Houston Ship Channel."
        ),
        "coords": [
            [-95.3622, 29.7592],
            [-95.3515, 29.7532],
            [-95.3412, 29.7478],
            [-95.3275, 29.7405],
            [-95.3115, 29.7322],
            [-95.2915, 29.7218],
        ],
    },
]

CURATED_HISTORIC_DEPOTS = [
    {
        "id": "depot_union_station",
        "name": "Union Station (1911 - Houston Belt & Terminal Ry. Passenger Terminal)",
        "historic_company": "Houston Belt & Terminal Railway (Santa Fe, MoPac / I&GN, Rock Island, Frisco & FW&D)",
        "charter_year": 1905,
        "opened_year": 1911,
        "modern_operator": "Preserved as Grand Lobby of Daikin Park / Minute Maid Park (501 Crawford St, Built 1911)",
        "rail_type": "depot",
        "status": "Surviving Historic Passenger Terminal (NRHP Listed · Built 1911 by Warren & Wetmore)",
        "route_summary": "501 Crawford St at Texas Ave, East Downtown",
        "historic_significance": (
            "Designed by New York architects Warren & Wetmore (architects of New York's Grand Central Terminal) in the "
            "Classical Revival style and opened on March 1, 1911 with three stories (three more added in 1912). Union Station "
            "served as Houston's grand gateway for the Santa Fe Texas Chief, Missouri Pacific Texas Eagle, and Burlington-Rock "
            "Island Sam Houston Zephyr until Amtrak departed in 1974. Adaptively preserved in 2000 as the main entrance to the "
            "Astros ballpark."
        ),
        "coords": [-95.3563, 29.7568],
    },
    {
        "id": "depot_grand_central_station",
        "name": "Grand Central Station Site (1886 / 1934 H&TC & Southern Pacific Terminal)",
        "historic_company": "Houston & Texas Central Railway · Southern Pacific Lines (Sunset Route & Sunbeam)",
        "charter_year": 1856,
        "opened_year": 1886,
        "modern_operator": "Demolished 1960 for Barbara Jordan Post Office (Now POST Houston, 401/901 Franklin Ave)",
        "rail_type": "depot",
        "status": "Historic Passenger Terminal Site (1886 Victorian Depot · 1934 Art Moderne Terminal · Demolished 1960)",
        "route_summary": "Washington Ave & Franklin Ave on the south bank of White Oak Bayou",
        "historic_significance": (
            "Site of four generations of H&TC and Southern Pacific passenger depots beginning in the 1860s, including the "
            "grand 1886 Victorian clock-tower station and Wyatt C. Hedrick's $1.2 million pink-granite Art Moderne Grand "
            "Central Station (opened Sept. 1, 1934). Home of the famed 'Sunset Limited' and the streamlined 'Sunbeam' and "
            "'Hustler' to Dallas until Southern Pacific closed the terminal in 1959 and sold the site for the Downtown "
            "Post Office."
        ),
        "coords": [-95.3675, 29.7662],
    },
    {
        "id": "depot_mkt_katy_station",
        "name": "Missouri-Kansas-Texas ('The Katy') Passenger & Freight Depot Site (1893 / 1914)",
        "historic_company": "Missouri, Kansas & Texas Railway Co. of Texas ('The Katy')",
        "charter_year": 1893,
        "opened_year": 1893,
        "modern_operator": "Site at N. Main Street Viaduct & White Oak Bayou (MKT Trail Terminus)",
        "rail_type": "depot",
        "status": "Historic Depot Site (Passenger Service 1893–1958)",
        "route_summary": "101 N. Main St / Shea St beneath the west side of the 1913 Main Street Viaduct at White Oak Bayou",
        "historic_significance": (
            "Houston passenger and freight terminus of 'The Katy' (MKT Railroad) from 1893 until 1958. When the City of "
            "Houston constructed the concrete Main Street Viaduct across White Oak Bayou in 1913, the Katy built a new "
            "two-story depot in 1914 with a second-floor waiting room opening directly onto the viaduct deck while trains "
            "boarded at track level along the bayou bank below."
        ),
        "coords": [-95.3596, 29.7676],
    },
    {
        "id": "depot_ign_station",
        "name": "International & Great Northern (I&GN) Passenger & Freight Depot Site (1879)",
        "historic_company": "International & Great Northern Railroad (Jay Gould) · Missouri Pacific Lines",
        "charter_year": 1871,
        "opened_year": 1879,
        "modern_operator": "Historic Site at Congress Ave & St. Emanuel (East Downtown) and 600 N. San Jacinto (Warehouse District)",
        "rail_type": "depot",
        "status": "Historic 19th-Century Depot Site (Passenger Trains Moved to Union Station After 1911)",
        "route_summary": "Congress Ave at St. Emanuel St / 600 N. San Jacinto St",
        "historic_significance": (
            "Original Houston passenger and freight terminal of the International & Great Northern Railroad (later MoPac), "
            "connecting northbound trains to Palestine/St. Louis with the Columbia Tap and Galveston, Houston & Henderson lines."
        ),
        "coords": [-95.3522, 29.7595],
    },
    {
        "id": "depot_amtrak_sp_1959",
        "name": "Southern Pacific 1959 Passenger Station / Houston Amtrak Station",
        "historic_company": "Southern Pacific Railroad (Built 1959 to Replace Grand Central Station) · Amtrak (1971–Present)",
        "charter_year": 1959,
        "opened_year": 1959,
        "modern_operator": "Amtrak (Sunset Limited Station, 902 Washington Ave)",
        "rail_type": "depot",
        "status": "Active Historic Mid-Century Passenger Station (Built 1959)",
        "route_summary": "902 Washington Ave at Elder St (First Ward / Old Sixth Ward edge)",
        "historic_significance": (
            "Built by Southern Pacific in 1959 when the 1934 Grand Central Station was sold for the Downtown Post Office. "
            "Still serves Amtrak's tri-weekly transcontinental 'Sunset Limited' between New Orleans, Houston, San Antonio, "
            "El Paso, and Los Angeles."
        ),
        "coords": [-95.3702, 29.7677],
    },
    {
        "id": "depot_harrisburg_bbbc",
        "name": "Harrisburg Depot & BBB&C Locomotive Shops Site (1853 - Cradle of Texas Railroading)",
        "historic_company": "Buffalo Bayou, Brazos & Colorado Railway (Gen. Sidney Sherman, 1853) · GH&SA / Southern Pacific",
        "charter_year": 1850,
        "opened_year": 1853,
        "modern_operator": "Texas Historical Marker Site at Broadway & Frio St in Historic Harrisburg",
        "rail_type": "depot",
        "status": "Historic Birthplace of Texas Railroading (1853 Terminal & Shops Site)",
        "route_summary": "Broadway St & Frio St at Buffalo Bayou, Harrisburg",
        "historic_significance": (
            "Here in 1851–1853, Gen. Sidney Sherman unloaded the first steam locomotive in Texas ('General Sherman') from a "
            "schooner on Buffalo Bayou and established the eastern terminus, roundhouse, and machine shops of the Buffalo "
            "Bayou, Brazos & Colorado Railway - the first operating railroad in Texas and the oldest component of the "
            "Southern Pacific system."
        ),
        "coords": [-95.2788, 29.7165],
    },
    {
        "id": "depot_hardy_street_shops",
        "name": "Hardy Street Locomotive Shops & Roundhouse Site (1880s–1960s, H&TC / Southern Pacific)",
        "historic_company": "Houston & Texas Central Railway · Texas & New Orleans RR · Southern Pacific Lines",
        "charter_year": 1856,
        "opened_year": 1882,
        "modern_operator": "Redeveloped as Hardy Yards (1550 Leona St / Hardy & Nance, Near Northside)",
        "rail_type": "depot",
        "status": "Historic Steam Locomotive Repair Shops & Roundhouse Site",
        "route_summary": "Hardy St, Maury St & Leona St north of Nance/Rothwell (Near Northside / Fifth Ward)",
        "historic_significance": (
            "For nearly ninety years, the sprawling 47-acre Hardy Street Yards housed the principal locomotive roundhouses, "
            "boiler shops, foundries, and passenger car works of the H&TC and Southern Pacific in Texas - employing thousands "
            "of machinists and anchors of the Near Northside, Ryon Addition, and Fifth Ward neighborhoods."
        ),
        "coords": [-95.3552, 29.7745],
    },
    {
        "id": "depot_interurban_terminal",
        "name": "Galveston-Houston Electric Interurban Downtown Terminal Site (1911–1936)",
        "historic_company": "Galveston-Houston Electric Railway Co. (Stone & Webster)",
        "charter_year": 1911,
        "opened_year": 1911,
        "modern_operator": "Historic Site at Pierce St & Travis/Milam St, Downtown",
        "rail_type": "depot",
        "status": "Historic Interurban Terminal Site (1911–1936)",
        "route_summary": "Pierce St between Travis & Milam St, Downtown Houston",
        "historic_significance": (
            "Northern terminal of the Galveston-Houston Electric Railway ('The Interurban'), where hourly high-speed "
            "electric parlor and commuter cars departed for Galveston between 1911 and 1936."
        ),
        "coords": [-95.3702, 29.7505],
    },
    {
        "id": "depot_tower_26",
        "name": "Tower 26 Historic Railroad Interlocking Junction (1906 - H&TC, T&NO & HE&WT Crossing)",
        "historic_company": "Texas Railroad Commission Interlocker #26 (Southern Pacific / H&TC / T&NO / HE&WT 'Rabbit')",
        "charter_year": 1902,
        "opened_year": 1906,
        "modern_operator": "Union Pacific Englewood / Hardy Junction (West St & Carr St, Fifth Ward)",
        "rail_type": "depot",
        "status": "Historic Railroad Diamond & Interlocking Junction (Commissioned 1906)",
        "route_summary": "Carr St & West St just west of Englewood Yard in Fifth Ward",
        "historic_significance": (
            "Commissioned by the Railroad Commission of Texas in 1902–1906, Tower 26 controlled one of the most famous "
            "and heavily trafficked railroad junctions in the Southwest, where the H&TC, T&NO, HE&WT ('The Rabbit'), and "
            "HB&T lines converged on the eastern edge of Fifth Ward."
        ),
        "coords": [-95.3359, 29.7766],
    },
    {
        "id": "depot_eureka_junction",
        "name": "Eureka Junction & Eureka Yard (1860s H&TC & 1893 MKT 'Katy' Junction)",
        "historic_company": "Houston & Texas Central Ry. (1856) & Missouri-Kansas-Texas RR (1893) - Interlocker Tower 13",
        "charter_year": 1856,
        "opened_year": 1893,
        "modern_operator": "Union Pacific Railroad (Eureka Junction at Washington Ave & I-10/Cottage Grove)",
        "rail_type": "depot",
        "status": "Active Historic Railroad Junction (Commissioned as Tower 13 in 1903)",
        "route_summary": "Eureka St & Washington Ave / Katy Rd (Cottage Grove / West End)",
        "historic_significance": (
            "Historic western gateway junction where the 1856 Houston & Texas Central mainline from Hempstead/Dallas and "
            "the 1893 Missouri-Kansas-Texas ('The Katy') mainline from Katy converged before entering Houston."
        ),
        "coords": [-95.4242, 29.7834],
    },
    {
        "id": "depot_chaney_junction",
        "name": "Chaney Junction (1860s–1890s H&TC & T&NO West Loop Junction - Tower 108)",
        "historic_company": "Houston & Texas Central Ry. · Texas & New Orleans RR · Southern Pacific",
        "charter_year": 1856,
        "opened_year": 1880,
        "modern_operator": "Union Pacific Railroad (Washington Ave & Studemont / Sawyer Yards)",
        "rail_type": "depot",
        "status": "Active Historic Railroad Wye & Junction",
        "route_summary": "Washington Ave & Sawyer/Studemont between Old Sixth Ward and Sawyer Yards",
        "historic_significance": (
            "Historic railroad wye where trains on the H&TC and GH&SA Sunset Route split between the passenger tracks "
            "into Grand Central Station along White Oak Bayou and the freight bypass loop around the north side of Downtown "
            "to Hardy Street Shops and Englewood Yard."
        ),
        "coords": [-95.3886, 29.7712],
    },
]


def normalize_waterway_name(raw_name: str) -> str:
    if not raw_name:
        return ""
    s = re.sub(r"\s+", " ", raw_name.strip())
    # Strip USGS NHD tidal suffixes
    s = re.sub(r"\s+(Above Tidal|Tidal)$", "", s, flags=re.IGNORECASE)
    if s.lower() == "little white oak" or s.lower() == "little whiteoak bayou":
        return "Little White Oak Bayou"
    if s.lower() == "whiteoak bayou":
        return "White Oak Bayou"
    if s.lower() in ("sims bayou", "sims  bayou"):
        return "Sims Bayou"
    if s.lower() in ("carpenter bayou", "carpenters bayou"):
        return "Carpenters Bayou"
    if "houston ship channel" in s.lower():
        return "Houston Ship Channel"
    if "buffalo bayou" in s.lower():
        return "Buffalo Bayou"
    return s


def build_waterways_collection() -> list:
    print("1. Fetching Historical Waterways from COH GIS, USGS NHD & PWE Storm Sewer Archives...")
    grouped_lines = {}  # name -> {"geoms": [], "is_culvert": False, "category": "Minor"}

    # 1A. COH Water_Line_Texas_ClippedCOH
    coh_wl = fetch_arcgis_features(
        "https://services.arcgis.com/NummVBqZSIJKUeVR/arcgis/rest/services/Water_Line_Texas_ClippedCOH/FeatureServer/0",
        where="NAME <> ' ' AND NAME IS NOT NULL",
        out_fields="NAME,CATEGORY",
    )
    print(f"   COH Water_Line_Texas_ClippedCOH: {len(coh_wl)} named segments")
    for f in coh_wl:
        raw_nm = (f.get("attributes") or {}).get("NAME") or ""
        if raw_nm.strip().lower().startswith("unnamed"):
            continue
        nm = normalize_waterway_name(raw_nm)
        if not nm:
            continue
        g = esri_paths_to_shapely(f.get("geometry"))
        if g is None:
            continue
        cat = ((f.get("attributes") or {}).get("CATEGORY") or "Minor").strip()
        rec = grouped_lines.setdefault(nm, {"geoms": [], "is_culvert": False, "category": cat})
        rec["geoms"].append(g)
        if cat == "Major":
            rec["category"] = "Major"

    # 1B. USGS NHD Flowline (captures Little White Oak Bayou, Bering Ditch, Briar Branch, Country Club Bayou, etc.)
    nhd_feats = fetch_arcgis_features(
        "https://hydro.nationalmap.gov/arcgis/rest/services/nhd/MapServer/6",
        where="gnis_name IS NOT NULL",
        bbox="-95.75,29.55,-95.05,30.05",
        out_fields="gnis_name,ftype,fcode",
    )
    print(f"   USGS NHD Flowline: {len(nhd_feats)} named segments")
    for f in nhd_feats:
        attr = f.get("attributes") or {}
        raw_nm = attr.get("gnis_name") or ""
        nm = normalize_waterway_name(raw_nm)
        if not nm:
            continue
        g = esri_paths_to_shapely(f.get("geometry"))
        if g is None:
            continue
        ftype = attr.get("ftype")
        rec = grouped_lines.setdefault(nm, {"geoms": [], "is_culvert": False, "category": "Minor"})
        rec["geoms"].append(g)
        if ftype == 428:
            rec["is_culvert"] = True

    # 1C. COH Public Works Maintained Waterways (captures buried storm-sewer waterways like Slaughterpen Bayou, City Ditch, Yates Gully, Cypress Slough)
    pwe_feats = fetch_arcgis_features(
        "https://services.arcgis.com/NummVBqZSIJKUeVR/arcgis/rest/services/PWE_COH_Maintained_Waterways/FeatureServer/0",
        where="CHANNELNAM <> ' ' AND CHANNELNAM IS NOT NULL",
        out_fields="CHANNELNAM,LINETYPE,BEDMATERIA",
    )
    print(f"   COH PWE Maintained Waterways: {len(pwe_feats)} named segments")
    for f in pwe_feats:
        attr = f.get("attributes") or {}
        raw_nm = (attr.get("CHANNELNAM") or "").strip()
        if not raw_nm or "rail trail" in raw_nm.lower():
            continue
        nm = normalize_waterway_name(raw_nm)
        ltype = (attr.get("LINETYPE") or "").strip()
        g = esri_paths_to_shapely(f.get("geometry"))
        if g is None:
            continue
        rec = grouped_lines.setdefault(nm, {"geoms": [], "is_culvert": False, "category": "Minor"})
        rec["geoms"].append(g)
        if ltype == "Storm Sewer":
            rec["is_culvert"] = True

    waterway_features = []
    idx = 1
    for nm, info in sorted(grouped_lines.items()):
        if not info["geoms"]:
            continue
        try:
            merged = unary_union(info["geoms"])
            if merged.geom_type == "MultiLineString":
                merged = linemerge(merged)
        except Exception:
            merged = info["geoms"][0]

        length_mi = approx_length_miles(merged)
        if length_mi < 0.12 and nm not in WATERWAY_HISTORICAL_DOSSIERS:
            continue

        dossier = WATERWAY_HISTORICAL_DOSSIERS.get(nm, {})
        if dossier.get("waterway_type"):
            wtype = dossier["waterway_type"]
        elif info["is_culvert"] and nm in ("Slaughterpen Bayou", "City Ditch", "Yates Gully", "Cypress Slough"):
            wtype = "buried_gully"
        elif "bayou" in nm.lower() or "river" in nm.lower() or "channel" in nm.lower() or info["category"] == "Major":
            wtype = "bayou"
        else:
            wtype = "creek"

        default_status = (
            "Buried / Enclosed in Municipal Storm Culvert"
            if wtype == "buried_gully"
            else ("Major Historic Bayou & Drainage Corridor" if wtype == "bayou" else "Historic Stream, Creek & Tributary")
        )
        default_sig = (
            f"{nm} is a historic {wtype.replace('_', ' ')} in the Houston / Harris County drainage network "
            f"({length_mi:.1f} miles mapped across USGS National Hydrography and City of Houston archives)."
        )

        geom_json = round_coords_geom(merged, tol=0.00007, precision=5)
        if not geom_json:
            continue

        slug = re.sub(r"[^a-z0-9]+", "_", nm.lower()).strip("_")
        props = {
            "id": f"waterway_{slug}_{idx}",
            "name": nm,
            "alt_names": dossier.get("alt_names", []),
            "waterway_type": wtype,  # 'bayou' | 'creek' | 'buried_gully' | 'historic_oxbow'
            "status": dossier.get("status", default_status),
            "watershed": dossier.get("watershed", "Houston / Galveston Bay Watershed"),
            "era_notes": dossier.get("era_notes", "USGS Historical Topographic & COH Hydrography Archive"),
            "historic_significance": dossier.get("historic_significance", default_sig),
            "length_miles": length_mi,
            "source": "USGS NHD + City of Houston Hydrography & PWE Archives",
        }
        waterway_features.append({"type": "Feature", "properties": props, "geometry": geom_json})
        idx += 1

    # 1D. Append Curated Buried Gullies & Pre-Channelization Natural Oxbows
    for item in CURATED_BURIED_GULLIES_AND_OXBOWS:
        ln = LineString(item["coords"])
        length_mi = approx_length_miles(ln)
        props = {
            "id": item["id"],
            "name": item["name"],
            "alt_names": item.get("alt_names", []),
            "waterway_type": item["waterway_type"],
            "status": item["status"],
            "watershed": item.get("watershed", "Buffalo / Brays / White Oak Bayou Watershed"),
            "era_notes": item.get("era_notes", "1915 USGS Houston Topo Quad & Sanborn Maps"),
            "historic_significance": item["historic_significance"],
            "length_miles": length_mi,
            "source": "1915/1922 USGS Houston Topographic Quadrangle & 1869/1891 Bird's-Eye Cartography",
        }
        waterway_features.append(
            {
                "type": "Feature",
                "properties": props,
                "geometry": round_coords_geom(ln, tol=0.0, precision=5),
            }
        )

    print(f"   -> Built {len(waterway_features)} total Historical Waterway features")
    return waterway_features


def build_railroads_collection() -> list:
    print("2. Fetching Historical Railroads from TxDOT, COH TIGER & Rail-Trail Archives...")
    railroad_features = []

    # 2A. TxDOT Texas_Railroads (Harris County CNTY_FIPS='201') grouped by historical corridor
    txdot_rr = fetch_arcgis_features(
        "https://services.arcgis.com/KTcxiTD9dsQw4r7Z/arcgis/rest/services/Texas_Railroads/FeatureServer/0",
        where="CNTY_FIPS='201'",
        out_fields="RR_COMPANY,RR_ABRVN,SUBDIV,BRNCH,YARDNAME,RR_STATUS,RR_TYPE,PASSGR",
    )
    print(f"   TxDOT Texas_Railroads (Harris County): {len(txdot_rr)} track segments")

    grouped_rr = {}  # key -> {"meta": dict, "geoms": []}
    for f in txdot_rr:
        attr = f.get("attributes") or {}
        g = esri_paths_to_shapely(f.get("geometry"))
        if g is None:
            continue
        abrv = (attr.get("RR_ABRVN") or "").strip()
        subdiv = (attr.get("SUBDIV") or "").strip().upper()
        brnch = (attr.get("BRNCH") or "").strip().upper()
        status_raw = (attr.get("RR_STATUS") or "Active").strip()

        if subdiv in TXDOT_SUBDIV_TO_HISTORICAL_RR:
            meta = dict(TXDOT_SUBDIV_TO_HISTORICAL_RR[subdiv])
            key = f"subdiv_{subdiv}"
        elif abrv == "PTRA" or "PTRA" in (attr.get("RR_COMPANY") or ""):
            meta = dict(PTRA_HISTORICAL_INFO)
            key = "rr_ptra"
        elif abrv == "BNSF":
            meta = {
                "name": f"Gulf, Colorado & Santa Fe / Burlington-Rock Island Line ({subdiv or 'Houston Corridor'})",
                "historic_company": "Gulf, Colorado & Santa Fe Ry. (1880) · Trinity & Brazos Valley Ry. (1907)",
                "charter_year": 1880,
                "opened_year": 1882,
                "modern_operator": "BNSF Railway",
                "rail_type": "mainline",
                "status": "Active Historic Freight & Industrial Line",
                "route_summary": f"Houston BNSF Corridor ({subdiv or 'Industrial Lead'})",
                "historic_significance": (
                    "Historic Santa Fe (GC&SF) and Burlington-Rock Island (T&BV) rail corridor serving greater Houston."
                ),
            }
            key = f"bnsf_{subdiv or 'other'}"
        else:
            label_sub = subdiv.title() if subdiv and subdiv != "#N\\A" else "Industrial Lead"
            is_abandoned = "abandon" in status_raw.lower() or "inactive" in status_raw.lower()
            meta = {
                "name": f"Southern Pacific / MoPac Historic {label_sub}",
                "historic_company": "Southern Pacific (T&NO / H&TC / GH&SA) & Missouri Pacific (I&GN / StLB&M)",
                "charter_year": 1880,
                "opened_year": 1895,
                "modern_operator": f"{attr.get('RR_COMPANY') or 'Union Pacific'} ({label_sub})",
                "rail_type": "abandoned_trail" if is_abandoned else "mainline",
                "status": "Inactive / Abandoned Rail Lead" if is_abandoned else "Active Historic Industrial Rail Lead",
                "route_summary": f"Houston {label_sub} rail corridor",
                "historic_significance": (
                    f"Historic rail lead ({label_sub}) constructed during the expansion of Houston's 19th- and "
                    f"20th-century industrial and port rail network."
                ),
            }
            key = f"up_{subdiv or 'other'}"

        rec = grouped_rr.setdefault(key, {"meta": meta, "geoms": []})
        rec["geoms"].append(g)

    # 2B. Fetch Columbia Tap Trail & Harrisburg-Sunset Trail from COH ArcGIS
    col_tap_feats = fetch_arcgis_features(
        "https://services.arcgis.com/NummVBqZSIJKUeVR/arcgis/rest/services/Columbia_Tap_Trail/FeatureServer/0",
        where="1=1",
    )
    col_tap_geoms = [esri_paths_to_shapely(f.get("geometry")) for f in col_tap_feats]
    col_tap_geoms = [g for g in col_tap_geoms if g is not None]
    if col_tap_geoms:
        rec = grouped_rr.setdefault(
            "subdiv_COLUMBIA TAP INDUSTRIAL LEAD",
            {"meta": dict(TXDOT_SUBDIV_TO_HISTORICAL_RR["COLUMBIA TAP INDUSTRIAL LEAD"]), "geoms": []},
        )
        rec["geoms"].extend(col_tap_geoms)

    hb_sunset_feats = fetch_arcgis_features(
        "https://services.arcgis.com/NummVBqZSIJKUeVR/arcgis/rest/services/Harrisburg_Sunset_Trail/FeatureServer/7",
        where="1=1",
    )
    hb_sunset_geoms = [esri_paths_to_shapely(f.get("geometry")) for f in hb_sunset_feats]
    hb_sunset_geoms = [g for g in hb_sunset_geoms if g is not None]
    if hb_sunset_geoms:
        grouped_rr["trail_harrisburg_sunset"] = {
            "meta": {
                "name": "Galveston, Harrisburg & San Antonio ('Harrisburg-Sunset' Historic Corridor, 1853/1880)",
                "historic_company": "Buffalo Bayou, Brazos & Colorado Ry. (1853) · GH&SA 'Sunset Route' · Now Harrisburg-Sunset Rail-Trail",
                "charter_year": 1850,
                "opened_year": 1853,
                "modern_operator": "Abandoned Rail Right-of-Way · City of Houston Harrisburg-Sunset Trail",
                "rail_type": "abandoned_trail",
                "status": "Abandoned Historic Railroad Right-of-Way (Converted to Rail-Trail)",
                "route_summary": "East Downtown / Second Ward southeast along Harrisburg Blvd & Navigation through Eastwood to Harrisburg",
                "historic_significance": (
                    "Historic East End right-of-way linking the 1853 Buffalo Bayou, Brazos & Colorado Railway at Harrisburg "
                    "with Downtown Houston's rail yards; converted into the Harrisburg-Sunset Hike-and-Bike Trail."
                ),
            },
            "geoms": hb_sunset_geoms,
        }

    # 2C. Fetch Pulled & Inactive/Abandoned historic tracks from TxDOT Texas_Railroads_Deprecated
    dep_feats = fetch_arcgis_features(
        "https://services.arcgis.com/KTcxiTD9dsQw4r7Z/arcgis/rest/services/Texas_Railroads_Deprecated/FeatureServer/0",
        where="RR_STATUS IN ('Pulled', 'Inactive/Abandoned')",
        bbox="-95.70,29.58,-95.10,29.98",
        out_fields="OBJECTID,RR_ABRVN,RR_COMPANY,RR_STATUS,RR_TYP",
    )
    print(f"   TxDOT Texas_Railroads_Deprecated (Pulled/Abandoned): {len(dep_feats)} segments")
    pulled_main_geoms = []
    pulled_spur_geoms = []
    for f in dep_feats:
        attr = f.get("attributes") or {}
        g = esri_paths_to_shapely(f.get("geometry"))
        if g is None:
            continue
        rtyp = (attr.get("RR_TYP") or "").strip()
        if rtyp == "Main Line":
            pulled_main_geoms.append(g)
        else:
            pulled_spur_geoms.append(g)

    if pulled_spur_geoms:
        grouped_rr["pulled_historic_spurs"] = {
            "meta": {
                "name": "Pulled Historic Industrial Spurs & Warehouse Sidings (1880s–1970s TxDOT Archive)",
                "historic_company": "Southern Pacific (H&TC / T&NO / GH&SA), MKT, MoPac (I&GN) & Santa Fe Industrial Spurs",
                "charter_year": 1880,
                "opened_year": 1890,
                "modern_operator": "Tracks Pulled / Abandoned (Surveyed in TxDOT Historical Rail Archive)",
                "rail_type": "abandoned_trail",
                "status": "Pulled / Removed Historic Industrial Sidings & Spurs",
                "route_summary": "Historic industrial rail spurs across the Warehouse District, Sawyer Yards, East End, Third Ward, and Ship Channel",
                "historic_significance": (
                    "Before post-WWII trucking replaced boxcar delivery, hundreds of factories, cotton compresses, coffee "
                    "roasters (Cheek-Neal / Maxwell House), lumber yards, and wholesale warehouses in Houston had direct "
                    "railroad spurs running up to their loading docks. These pulled sidings explain why many historic "
                    "Houston industrial buildings have angled walls or curved rear loading bays."
                ),
            },
            "geoms": pulled_spur_geoms,
        }

    # Build GeoJSON features for grouped_rr
    idx = 1
    for key, info in sorted(grouped_rr.items()):
        geoms = info["geoms"]
        if not geoms:
            continue
        try:
            merged = unary_union(geoms)
            if merged.geom_type == "MultiLineString":
                merged = linemerge(merged)
        except Exception:
            merged = geoms[0]

        length_mi = approx_length_miles(merged)
        if length_mi < 0.08:
            continue

        geom_json = round_coords_geom(merged, tol=0.00006, precision=5)
        if not geom_json:
            continue

        meta = info["meta"]
        slug = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
        props = {
            "id": f"rail_{slug}_{idx}",
            "name": meta["name"],
            "historic_company": meta["historic_company"],
            "charter_year": meta.get("charter_year", 1880),
            "opened_year": meta.get("opened_year", 1885),
            "modern_operator": meta["modern_operator"],
            "rail_type": meta["rail_type"],  # 'mainline' | 'abandoned_trail' | 'streetcar_interurban' | 'depot'
            "status": meta["status"],
            "route_summary": meta["route_summary"],
            "historic_significance": meta["historic_significance"],
            "length_miles": length_mi,
            "source": "TxDOT Rail Archive, US Census TIGER & Preservation Houston Historical Research",
        }
        railroad_features.append({"type": "Feature", "properties": props, "geometry": geom_json})
        idx += 1

    # 2D. Append Curated Abandoned Pioneer Rail Corridors & Historic Electric Streetcar / Interurban Lines
    for item in CURATED_ABANDONED_RAILS_AND_STREETCARS:
        ln = LineString(item["coords"])
        # If this is the MKT Heights Mainline, merge in the exact TxDOT pulled mainline segments along 7th St
        if item["id"] == "rail_mkt_katy_heights_mainline" and pulled_main_geoms:
            ln = unary_union([ln] + pulled_main_geoms)
            if ln.geom_type == "MultiLineString":
                ln = linemerge(ln)
        length_mi = approx_length_miles(ln)
        props = {
            "id": item["id"],
            "name": item["name"],
            "historic_company": item["historic_company"],
            "charter_year": item["charter_year"],
            "opened_year": item["opened_year"],
            "modern_operator": item["modern_operator"],
            "rail_type": item["rail_type"],
            "status": item["status"],
            "route_summary": item["route_summary"],
            "historic_significance": item["historic_significance"],
            "length_miles": length_mi,
            "source": "Houston Electric Co. Archival Route Maps (1891–1936) & TxDOT Historical Rail Archive",
        }
        railroad_features.append(
            {
                "type": "Feature",
                "properties": props,
                "geometry": round_coords_geom(ln, tol=0.00004, precision=5),
            }
        )

    # 2E. Append Curated Historic Train Depots, Roundhouses & Interlocking Towers (Point features)
    for dep in CURATED_HISTORIC_DEPOTS:
        pt = Point(dep["coords"][0], dep["coords"][1])
        props = {
            "id": dep["id"],
            "name": dep["name"],
            "historic_company": dep["historic_company"],
            "charter_year": dep["charter_year"],
            "opened_year": dep["opened_year"],
            "modern_operator": dep["modern_operator"],
            "rail_type": "depot",
            "status": dep["status"],
            "route_summary": dep["route_summary"],
            "historic_significance": dep["historic_significance"],
            "length_miles": 0.0,
            "source": "Preservation Houston & Texas Railroad Commission Historical Archives",
        }
        railroad_features.append(
            {
                "type": "Feature",
                "properties": props,
                "geometry": round_coords_geom(pt, tol=0.0, precision=5),
            }
        )

    print(f"   -> Built {len(railroad_features)} total Historical Railroad, Streetcar & Depot features")
    return railroad_features


def update_overlays_and_search_index(waterways: list, railroads: list):
    print("3. Updating app/public/data/overlays.json and app/public/data/search_index.json...")
    with open(OVERLAYS_PATH, "r", encoding="utf-8") as f:
        overlays = json.load(f)

    overlays["historical_waterways"] = {
        "type": "FeatureCollection",
        "features": waterways,
    }
    overlays["historical_railroads"] = {
        "type": "FeatureCollection",
        "features": railroads,
    }

    with open(OVERLAYS_PATH, "w", encoding="utf-8") as f:
        json.dump(overlays, f, separators=(",", ":"))
    size_mb = os.path.getsize(OVERLAYS_PATH) / (1024 * 1024)
    print(f"   Saved {OVERLAYS_PATH} ({size_mb:.2f} MB)")

    # Update search_index.json so users and docents can search any bayou, creek, buried gully, railroad, streetcar, or depot
    with open(SEARCH_INDEX_PATH, "r", encoding="utf-8") as f:
        search_index = json.load(f)

    # Remove any old waterway/railroad entries
    search_index = [
        item
        for item in search_index
        if item.get("type") not in ("historical_waterway", "historical_railroad")
    ]

    added_water = 0
    for feat in waterways:
        p = feat["properties"]
        geom = shape(feat["geometry"])
        rep = geom.representative_point()
        b = geom.bounds  # (minx, miny, maxx, maxy)
        wtype_label = {
            "bayou": "Historic Bayou",
            "creek": "Historic Stream / Creek",
            "buried_gully": "Buried Historic Gully",
            "historic_oxbow": "Historic Bayou Oxbow",
        }.get(p["waterway_type"], "Historic Waterway")
        search_index.append(
            {
                "id": p["id"],
                "type": "historical_waterway",
                "layer_key": "historicalWaterways",
                "label": p["name"],
                "alt_names": p.get("alt_names", []),
                "sublabel": f"{wtype_label} · {p['status']} ({p['length_miles']} mi)",
                "category": wtype_label,
                "lng": round(rep.x, 5),
                "lat": round(rep.y, 5),
                "bbox": [round(b[0], 5), round(b[1], 5), round(b[2], 5), round(b[3], 5)],
            }
        )
        added_water += 1

    added_rail = 0
    for feat in railroads:
        p = feat["properties"]
        geom = shape(feat["geometry"])
        rep = geom.representative_point()
        b = geom.bounds
        rtype_label = {
            "mainline": f"Historic Railroad ({p.get('opened_year', '')})",
            "abandoned_trail": "Abandoned Historic Railroad / Rail-Trail",
            "streetcar_interurban": f"Historic Streetcar / Interurban ({p.get('opened_year', '')})",
            "depot": f"Historic Train Depot / Junction ({p.get('opened_year', '')})",
        }.get(p["rail_type"], "Historic Railroad")
        entry = {
            "id": p["id"],
            "type": "historical_railroad",
            "layer_key": "historicalRailroads",
            "label": p["name"],
            "alt_names": [p["historic_company"], p["modern_operator"]],
            "sublabel": f"{rtype_label} · {p['route_summary']}",
            "category": rtype_label,
            "lng": round(rep.x, 5),
            "lat": round(rep.y, 5),
        }
        if geom.geom_type != "Point":
            entry["bbox"] = [round(b[0], 5), round(b[1], 5), round(b[2], 5), round(b[3], 5)]
        search_index.append(entry)
        added_rail += 1

    with open(SEARCH_INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(search_index, f, separators=(",", ":"))
    print(
        f"   Updated {SEARCH_INDEX_PATH}: +{added_water} waterways, +{added_rail} railroads/streetcars/depots "
        f"(total={len(search_index)} entries)"
    )


if __name__ == "__main__":
    w = build_waterways_collection()
    r = build_railroads_collection()
    update_overlays_and_search_index(w, r)
