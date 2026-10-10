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
    """Fetch all features with pagination and disk caching from an ArcGIS REST FeatureServer/MapServer layer."""
    import hashlib

    cache_key = hashlib.sha1(f"{url}|{where}|{bbox}|{out_fields}".encode("utf-8")).hexdigest()[:16]
    cache_file = CACHE_DIR / f"arcgis_cache_{cache_key}.json"
    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

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
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            print(f"   [warn] ArcGIS query timeout/error on {url} (offset={offset}): {e}")
            break
        feats = data.get("features") or []
        all_features.extend(feats)
        if not data.get("exceededTransferLimit") or len(feats) < page_size:
            break
        offset += len(feats)
    if all_features:
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(all_features, f)
        except Exception:
            pass
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


def safe_linemerge(geom):
    """Safely merge connected LineStrings in a MultiLineString without raising on LineString input."""
    if geom is None or geom.is_empty:
        return geom
    if geom.geom_type == "MultiLineString":
        try:
            return linemerge(geom)
        except Exception:
            return geom
    return geom


def catmull_rom_spline(coords: list, subdivisions: int = 8) -> list:
    """Interpolate a smooth centripetal Catmull-Rom curve through control points."""
    if not coords or len(coords) < 3 or subdivisions <= 1:
        return coords
    pts = [(float(p[0]), float(p[1])) for p in coords]
    # Extrapolate endpoints
    p_start = (2 * pts[0][0] - pts[1][0], 2 * pts[0][1] - pts[1][1])
    p_end = (2 * pts[-1][0] - pts[-2][0], 2 * pts[-1][1] - pts[-2][1])
    ext = [p_start] + pts + [p_end]
    out = []
    for i in range(len(pts) - 1):
        p0, p1, p2, p3 = ext[i], ext[i + 1], ext[i + 2], ext[i + 3]
        for s in range(subdivisions):
            t = s / float(subdivisions)
            t2 = t * t
            t3 = t2 * t
            x = 0.5 * (
                (2.0 * p1[0])
                + (-p0[0] + p2[0]) * t
                + (2.0 * p0[0] - 5.0 * p1[0] + 4.0 * p2[0] - p3[0]) * t2
                + (-p0[0] + 3.0 * p1[0] - 3.0 * p2[0] + p3[0]) * t3
            )
            y = 0.5 * (
                (2.0 * p1[1])
                + (-p0[1] + p2[1]) * t
                + (2.0 * p0[1] - 5.0 * p1[1] + 4.0 * p2[1] - p3[1]) * t2
                + (-p0[1] + 3.0 * p1[1] - 3.0 * p2[1] + p3[1]) * t3
            )
            out.append([round(x, 6), round(y, 6)])
    out.append([round(pts[-1][0], 6), round(pts[-1][1], 6)])
    return out


def round_coords_geom(geom, tol: float = 0.000018, precision: int = 5):
    """Simplify and round geometry coordinates to keep GeoJSON compact and smooth."""
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
        "alt_names": ["East End / Gus Wortham Golf Course Bayou", "Third Ward / East End Tributary"],
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
    "Japhet Creek": {
        "alt_names": ["Japhet Ravine", "Fifth Ward Spring-Fed Tributary of Buffalo Bayou"],
        "waterway_type": "creek",
        "status": "Preserved Wooded Spring-Fed Creek & Nature Corridor",
        "watershed": "Buffalo Bayou Watershed",
        "era_notes": "1880s Dan Japhet Homestead · Emilie Street & Japhet Street Ravine",
        "historic_significance": (
            "Historic spring-fed creek on the south edge of Fifth Ward near Emilie and Japhet Streets that flows through "
            "a deep wooded ravine directly into Buffalo Bayou east of Downtown."
        ),
    },
    "Poor Farm Ditch": {
        "alt_names": ["Harris County Poor Farm Drainage Canal (1880s)", "West University & Bellaire Ditch"],
        "waterway_type": "creek",
        "status": "Historic 19th-Century County Poor Farm Drainage Channel (HCFCD Unit D111)",
        "watershed": "Brays Bayou Watershed",
        "era_notes": "1880s Harris County Poor Farm (now Boulevard Oaks / West U / Southside Place) · Brays Bayou Outfall",
        "historic_significance": (
            "Excavated in the late 19th century to drain the 250-acre Harris County Poor Farm and surrounding rice/coastal "
            "prairie southward between West University Place, Southside Place, and Bellaire into Brays Bayou."
        ),
    },
    "Old Buffalo Bayou": {
        "alt_names": ["Buffalo Bayou Cut-Off Natural Oxbows", "Memorial Park & River Oaks Historic Meanders"],
        "waterway_type": "historic_oxbow",
        "status": "Cut-Off Natural Bayou Meander Loops (Surveyed Remnant Channels)",
        "watershed": "Buffalo Bayou Watershed",
        "era_notes": "Pre-20th-Century Natural Bayou Channel · Cut Off by Flood Control & Navigation Straightening",
        "historic_significance": (
            "Surviving cut-off natural oxbow channels along Buffalo Bayou showing the original tight horseshoe meanders "
            "before 20th-century channel straightening."
        ),
    },
    "Old Sims Bayou": {
        "alt_names": ["Sims Bayou Cut-Off Oxbow Loops", "Reveille & Glenbrook Valley Historic Meanders"],
        "waterway_type": "historic_oxbow",
        "status": "Cut-Off Natural Bayou Oxbow Loops (Surveyed Remnant Channels)",
        "watershed": "Sims Bayou Watershed",
        "era_notes": "Pre-1950s Natural Sims Bayou Meanders · Preserved as Oxbow Parkland & Remnant Channels",
        "historic_significance": (
            "Surveyed remnant natural oxbow loops of Sims Bayou that were bypassed when the main flood-control channel "
            "was widened and straightened in the mid-20th century."
        ),
    },
    "Old Turkey Creek": {
        "alt_names": ["Turkey Creek Historic Channel"],
        "waterway_type": "historic_oxbow",
        "status": "Historic Natural Creek Meanders",
        "watershed": "Buffalo Bayou Watershed",
        "era_notes": "Addicks & West Memorial Historic Creek Bed",
        "historic_significance": "Remnant natural meander channel of Turkey Creek prior to flood-control realignment.",
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

# Surveyed lost/buried gullies (from HCFCD M3_HCFCD_Drainage_Network CLOSED CONDUIT) & pre-channelization oxbows (from 1903 Ward Surveys & COH Hydrography)
CURATED_BURIED_GULLIES_AND_OXBOWS = [
    {
        "id": "waterway_harris_gully",
        "name": "Harris Gully (Buried Historic Creek - Rice, Hermann Park & TMC, HCFCD D109-00-00)",
        "alt_names": ["Harris Bayou", "Rice Institute & Hermann Park Ravine", "Fannin / Sunset Box Culvert (D109-00-00)"],
        "waterway_type": "buried_gully",
        "status": "Buried Underground in Twin Box Culverts (HCFCD Closed Conduit Unit D109-00-00, Enclosed 1950s–1960s)",
        "watershed": "Brays Bayou Watershed",
        "era_notes": "1912 Rice Institute Campus Plan · 1914 Hermann Park · Enclosed 1950s–1960s · 2001 Tropical Storm Allison",
        "historic_significance": (
            "Houston's most consequential lost waterway (839-vertex surveyed alignment in HCFCD Unit D109-00-00). Shown "
            "prominently on the 1915 and 1922 USGS Houston Topographic Quadrangles, Harris Gully rose near Rice University "
            "and Sunset Blvd, curved past Hermann Park and the Mecom Fountain site, carved a deep ravine through what became "
            "the Texas Medical Center (TMC), and emptied into Brays Bayou at MacGregor Park. Enclosed in massive underground "
            "box culverts in the 1950s–1960s, the buried creek bed still governs TMC flood hydrology and famously backed up "
            "into Medical Center basements during Tropical Storm Allison in June 2001."
        ),
        "hcfcd_units": ["D109-00-00"],
    },
    {
        "id": "waterway_slaughterpen_yates_culvert",
        "name": "Slaughterpen Bayou & Yates Gully Underground Culvert System (HCFCD D103-00-00 / D103-02-00)",
        "alt_names": ["Slaughterpen Gully", "East End & Eastwood Buried Ravine", "Yates Gully Box Culvert"],
        "waterway_type": "buried_gully",
        "status": "Buried in Municipal Box Culverts (HCFCD Closed Conduit Units D103-00-00 & D103-02-00)",
        "watershed": "Brays / Buffalo Bayou Watershed",
        "era_notes": "1860s–1900s East End Slaughterhouses · 1913 Eastwood & Country Club Place · Enclosed 20th Century",
        "historic_significance": (
            "Surveyed 200-vertex underground box-culvert network (HCFCD Units D103-00-00 and D103-02-00) carrying the "
            "enclosed waters of historic Slaughterpen Bayou and Yates Gully beneath the East End, Eastwood, and Lawndale "
            "toward Brays Bayou."
        ),
        "hcfcd_units": ["D103-00-00", "D103-02-00"],
    },
    {
        "id": "waterway_third_ward_d105_gully",
        "name": "Third Ward, University of Houston & Riverside Buried Gully (HCFCD D105-00-00)",
        "alt_names": ["Third Ward South Ravine", "Calhoun & St. Augustine Underground Culvert (D105-00-00)"],
        "waterway_type": "buried_gully",
        "status": "Buried Historic Ravine (HCFCD Closed Conduit Unit D105-00-00)",
        "watershed": "Brays Bayou Watershed",
        "era_notes": "1920s Riverside Terrace & University of Houston Campus Drainage · Enclosed Mid-20th Century",
        "historic_significance": (
            "Historic natural gully draining southern Third Ward and the University of Houston corridor eastward into "
            "Brays Bayou; enclosed in underground box culverts as HCFCD Closed Conduit Unit D105-00-00."
        ),
        "hcfcd_units": ["D105-00-00"],
    },
    {
        "id": "waterway_fifth_ward_g122_gully",
        "name": "Fifth Ward, Gregg Street & Buck Street Buried Gully (HCFCD G122-00-00 / G122-01-00)",
        "alt_names": ["Fifth Ward South Ravine", "Lyons Avenue & Gregg Street Storm Conduit (G122)"],
        "waterway_type": "buried_gully",
        "status": "Buried Historic Ravine (HCFCD Closed Conduit Units G122-00-00, G122-01-00 & G123-00-00)",
        "watershed": "Buffalo Bayou Watershed",
        "era_notes": "1866 Fifth Ward Founding · 1880s T&NO Rail Yards · Enclosed 20th Century",
        "historic_significance": (
            "Surveyed 129-vertex underground storm conduit (HCFCD Units G122-00-00, G122-01-00, and G123-00-00) following "
            "the historic natural drainage ravine from the heart of Fifth Ward near Lyons Avenue and Gregg Street southward "
            "into Buffalo Bayou."
        ),
        "hcfcd_units": ["G122-00-00", "G122-01-00", "G123-00-00"],
    },
    {
        "id": "waterway_river_oaks_post_oak_gully",
        "name": "River Oaks, Post Oak & Westheimer Buried Gullies (HCFCD W129 / W132 / W133)",
        "alt_names": ["Post Oak Ravine", "Westheimer & Uptown Underground Conduit (W129 / W132 / W133)"],
        "waterway_type": "buried_gully",
        "status": "Buried Historic Ravines (HCFCD Closed Conduit Units W129, W132, W133 & W134)",
        "watershed": "Buffalo Bayou Watershed",
        "era_notes": "1920s–1950s Westheimer & Post Oak Expansion · Enclosed in Box Culverts",
        "historic_significance": (
            "Surveyed western tributary gullies of Buffalo Bayou (HCFCD Closed Conduit Units W129-01-00, W132-00-00, "
            "W133-00-00, and W134-00-00) that were enclosed in underground box culverts as Houston expanded westward "
            "past River Oaks and Post Oak Road."
        ),
        "hcfcd_units": ["W129-01-00", "W129-01-05", "W132-00-00", "W133-00-00", "W134-00-00"],
    },
    {
        "id": "waterway_timbergrove_e107_gully",
        "name": "Timbergrove, Lazybrook & Heights West Buried Gully (HCFCD E107-00-00)",
        "alt_names": ["North Loop West & Ella Buried Tributary", "White Oak Bayou Closed Conduit E107"],
        "waterway_type": "buried_gully",
        "status": "Buried Historic Tributary (HCFCD Closed Conduit Units E107-00-00, E107-02-00 & E107-03-00)",
        "watershed": "White Oak Bayou Watershed",
        "era_notes": "1940s–1950s Timbergrove Manor & Shady Acres Development · Enclosed in Box Culverts",
        "historic_significance": (
            "Surveyed historic tributary gully of White Oak Bayou (HCFCD Closed Conduit Units E107-00-00, E107-02-00, "
            "and E107-03-00) enclosed in underground conduits during the mid-century residential development of Timbergrove "
            "and Lazybrook."
        ),
        "hcfcd_units": ["E107-00-00", "E107-02-00", "E107-03-00"],
    },
    {
        "id": "waterway_little_white_oak_buried_tribs",
        "name": "Little White Oak Bayou Buried Tributary Gullies - Independence Heights & Lindale Park (HCFCD E101)",
        "alt_names": ["Independence Heights & Airline Buried Gullies", "HCFCD Closed Conduit Units E101-06 to E101-14"],
        "waterway_type": "buried_gully",
        "status": "Buried Lateral Gullies (HCFCD Closed Conduit Sub-Units of E101)",
        "watershed": "White Oak Bayou Watershed",
        "era_notes": "1910 Independence Heights · 1920s–1930s Lindale Park & Northside",
        "historic_significance": (
            "Surveyed lateral drainage gullies feeding Little White Oak Bayou across Independence Heights, Northline, and "
            "Lindale Park that were enclosed into underground municipal storm conduits (HCFCD Closed Conduit Units E101-06 "
            "through E101-15)."
        ),
        "hcfcd_units": [
            "E101-06-00",
            "E101-07-00",
            "E101-10-00",
            "E101-10-01",
            "E101-11-00",
            "E101-12-00",
            "E101-13-00",
            "E101-14-00",
            "E101-15-00",
            "E101-15-01",
            "E101-15-02",
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
        "brady_island_survey": True,
    },
    {
        "id": "waterway_frost_town_oxbow",
        "name": "Buffalo Bayou - 1839–1903 Frost Town, McKee St & Quality Hill Natural Meanders",
        "alt_names": ["Frost Town Bend", "1903 Second Ward & Fifth Ward Bayou Boundary Survey"],
        "waterway_type": "historic_oxbow",
        "status": "Surveyed 1903 Bayou Thalweg (Prior to Post-1935 Flood Control Rectification)",
        "watershed": "Buffalo Bayou Watershed",
        "era_notes": "1838 Frost Town · 1903 Aldermanic Ward Charter Survey · 1910 McKee Street Bridge · 1935 Flood Rectification",
        "historic_significance": (
            "Exact surveyed 1903 municipal charter boundary between Second Ward and Fifth Ward, tracing the pre-channelization "
            "centerline (thalweg) of Buffalo Bayou around Frost Town, McKee Street, and Quality Hill before 20th-century "
            "flood-control and barge-channel straightening."
        ),
        "ward_boundary_pair": ("SECOND", "FIFTH"),
    },
    {
        "id": "waterway_shepherd_tinsley_oxbows",
        "name": "Buffalo Bayou - 1839–1903 Shepherd to Sabine Natural Meander Loops",
        "alt_names": ["Cleveland Park & Spotts Park Cut-Off Oxbows", "1903 Fourth Ward & Sixth Ward Bayou Boundary Survey"],
        "waterway_type": "historic_oxbow",
        "status": "Surveyed 1903 Bayou Thalweg (Cut Off by USACE Channel Straightening in 1950s)",
        "watershed": "Buffalo Bayou Watershed",
        "era_notes": "1839–1903 Ward Charter Survey · 1915 USGS Houston Topo Map · 1950s USACE Channel Straightening",
        "historic_significance": (
            "Exact surveyed 1903 municipal charter boundary between Fourth Ward and Sixth Ward along the original meandering "
            "centerline of Buffalo Bayou from Shepherd Drive to Sabine Street. In the 1950s, the U.S. Army Corps of Engineers "
            "cut straight pilot channels across the necks of the tighter bends near Spotts Park and Eleanor Tinsley Park, "
            "leaving the 1903 Ward boundary as an exact historical survey of the lost natural meander loops."
        ),
        "ward_boundary_pair": ("FOURTH", "SIXTH"),
    },
    {
        "id": "waterway_white_oak_stude_oxbows",
        "name": "Little White Oak & Lower White Oak Bayou - 1839–1903 Natural Ravine Meanders",
        "alt_names": ["Woodland Park & Hogg Park 1903 Bayou Survey", "1903 First Ward & Fifth Ward Bayou Boundary Survey"],
        "waterway_type": "historic_oxbow",
        "status": "Surveyed 1903 Bayou Thalweg (Woodland Park, Beauchamp Springs & Hogg Park Ravine)",
        "watershed": "White Oak Bayou Watershed",
        "era_notes": "1839–1903 Ward Charter Survey · 1903 Woodland Park · 1907 Woodland Heights",
        "historic_significance": (
            "Exact 93-vertex surveyed 1903 municipal charter boundary between First Ward and Fifth Ward along the natural "
            "winding channel of Little White Oak Bayou and lower White Oak Bayou through Woodland Park, Beauchamp Springs, "
            "and Wright-Bembry / Hogg Park down to the Buffalo Bayou confluence at Allen's Landing."
        ),
        "ward_boundary_pair": ("FIRST", "FIFTH"),
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
        "name": "Houston Tap & Brazoria Railway ('The Columbia Tap' / I&GN Almeda & Third Ward Line, 1856)",
        "historic_company": "Houston Tap Railroad (City of Houston, 1856) · Houston Tap & Brazoria Ry. ('The Sugar Road', 1856) · I&GN (1871)",
        "charter_year": 1856,
        "opened_year": 1856,
        "modern_operator": "Abandoned Inner-Loop Mainline (Now Columbia Tap Rail-Trail) & Union Pacific Almeda Lead",
        "rail_type": "abandoned_trail",
        "status": "Pioneer 1856 Municipal Railroad (Full Extent: I&GN Depot through EaDo, Third Ward & Almeda Rd to Pierce Jct)",
        "route_summary": "1879 I&GN Depot (Congress & St. Emanuel) south through EaDo & diagonally across Third Ward / TSU to Brays Bayou, then south alongside Almeda Road past Pierce Junction to Brazoria County",
        "historic_significance": (
            "Alarmed that the 1853 BBB&C Railroad at Harrisburg might bypass Houston entirely, Houston voters approved a "
            "municipal property tax in January 1856 to build their own 7-mile 'Houston Tap' railroad south to Pierce Junction "
            "on the BBB&C. Extended by Brazoria County planters later in 1856 as the Houston Tap & Brazoria Railway ('The Sugar "
            "Road') to East Columbia on the Brazos River, it hauled plantation sugar and cotton north alongside Almeda Road "
            "and diagonally across Third Ward into East Downtown before being acquired by the I&GN in 1871–1873. Today its "
            "4-mile inner-city corridor from Dixie Drive to Polk Street forms the Columbia Tap Hike-and-Bike Trail."
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
    "TERMINAL-PASSENGER": {
        "name": "H&TC / Southern Pacific Grand Central Station & Amtrak Passenger Approach (1856–Present)",
        "historic_company": "Houston & Texas Central Railway (1856) · Southern Pacific Lines ('Sunset Limited' & 'Sunbeam')",
        "charter_year": 1856,
        "opened_year": 1860,
        "modern_operator": "Union Pacific Railroad & Amtrak Sunset Limited (902 Washington Ave Passenger Track)",
        "rail_type": "mainline",
        "status": "Active Historic Passenger Rail Approach (Former Grand Central Station Corridor)",
        "route_summary": "Chaney Junction east along Washington Ave and White Oak Bayou past the 1959 Amtrak Station toward the historic Grand Central Station site (POST Houston)",
        "historic_significance": (
            "For over a century, Houston's primary west-side passenger train approach ran along this corridor into "
            "Grand Central Station at Franklin and Washington Avenues (site of the 1886 Victorian depot and 1934 Art Moderne "
            "terminal). In 1959–1960, Southern Pacific relocated passenger service half a mile west along this track to the "
            "902 Washington Ave station (now Houston's Amtrak station) and sold the terminal site for the main U.S. Post Office."
        ),
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
    # 1. Abandoned / Pulled Pioneer Rail Corridors (Surveyed TxDOT Deprecated, HCAD Plat ROWs & OpenStreetMap Geometries)
    {
        "id": "rail_mkt_katy_heights_mainline",
        "name": "Missouri-Kansas-Texas RR ('The Katy' / MKT Inner-Loop Mainline, 1893)",
        "historic_company": "Missouri, Kansas & Texas Railway Co. ('The Katy', 1893–1988) · Pulled 1997 (Now MKT Hike-and-Bike Trail)",
        "charter_year": 1880,
        "opened_year": 1893,
        "modern_operator": "Abandoned 1997 · City of Houston MKT Trail (White Oak Bayou Greenway)",
        "rail_type": "abandoned_trail",
        "status": "Abandoned Pioneer Mainline (Surveyed TxDOT Pulled Mainline & MKT Trail)",
        "route_summary": "Katy & Eureka Yard east along 7th Street through Houston Heights, Shady Acres, Sawyer Yards & Studemont across White Oak Bayou to Downtown MKT Depot (Main & Shea)",
        "historic_significance": (
            "Completed into Houston in April 1893, the Missouri-Kansas-Texas ('The Katy') mainline ran due east from "
            "Eureka Junction through the southern Houston Heights (along 7th Street), crossed White Oak Bayou on a steel "
            "trestle at Studemont/Wright-Bembry Park, and terminated at the MKT Passenger & Freight Depot tucked beneath "
            "the 1913 Main Street Viaduct. After Union Pacific absorbed the Katy in 1988, the inner-loop tracks were pulled "
            "in 1997 and transformed into the MKT Hike-and-Bike Trail."
        ),
        "txdot_pulled_mkt": True,
    },
    {
        "id": "rail_htc_nicholson_heights_spur",
        "name": "Houston & Texas Central / Southern Pacific Heights Industrial Lead (1890s - Now Nicholson Trail)",
        "historic_company": "Houston & Texas Central Railway · Southern Pacific Lines (Heights & Sawyer Industrial Spur)",
        "charter_year": 1856,
        "opened_year": 1894,
        "modern_operator": "Abandoned · City of Houston Nicholson Street Hike-and-Bike Trail",
        "rail_type": "abandoned_trail",
        "status": "Abandoned Historic Industrial Rail Spur (Converted to Nicholson Hike-and-Bike Trail)",
        "route_summary": "North-South through the Houston Heights along Nicholson Street from 7th Street / MKT Junction north across 19th/20th Streets to North Loop",
        "historic_significance": (
            "Constructed in the 1890s as the Houston Heights industrial rail lead branching off the H&TC / MKT corridor "
            "along Nicholson Street to serve early Heights factories, textile mills, lumber yards, and ice plants on the "
            "western side of Houston Heights. Today the preserved right-of-way forms the Nicholson Hike-and-Bike Trail."
        ),
        "osm_trail_names": ["Nicholson Trail"],
    },
    {
        "id": "rail_saap_westpark_blodgett",
        "name": "San Antonio & Aransas Pass Railway ('The SAP' - Westpark, Blodgett & EaDo Mainline, 1886–1990s)",
        "historic_company": "San Antonio & Aransas Pass Railway (Uriah Lott & B.F. Yoakum, 1886–1888) · Southern Pacific (1925–1990s)",
        "charter_year": 1884,
        "opened_year": 1888,
        "modern_operator": "Abandoned / Pulled (Full Extent: Alief, Bellaire & Westpark alongside US-59 through Blodgett into EaDo)",
        "rail_type": "abandoned_trail",
        "status": "Abandoned 19th-Century Mainline (Full Historic Corridor from Westpark through Blodgett to EaDo & Buffalo Bayou)",
        "route_summary": "Alief, Westchase & Bellaire (Tower 104) east along Westpark & alongside the Southwest Freeway (US-59) through Upper Kirby and Blodgett Junction (Tower 12), then northeast across Third Ward into East Downtown (Polk/Dowling Yard) & across Buffalo Bayou",
        "historic_significance": (
            "Built into Houston from the west in 1886–1888 by Uriah Lott and B.F. Yoakum's San Antonio & Aransas Pass "
            "Railway ('The SAP' or 'Davy Crockett Route'), this mainline ran east along the Westpark corridor through "
            "Bellaire (1908) and West University Place, continued alongside the present Southwest Freeway (US-59) to cross "
            "the GH&SA at Blodgett Junction (Tower 12, near Main/Almeda & Wheeler), and curved northeast across Third Ward "
            "to its freight yards and depot near Polk and Dowling (Emancipation) in East Downtown, extending north across "
            "Buffalo Bayou in 1895 to connect with the T&NO and Englewood Yard. After the central Blodgett/Southwest Freeway "
            "segment was severed in the mid-20th century, the eastern end in EaDo operated as industrial leads and warehouse "
            "spurs before the remaining tracks were pulled."
        ),
        "txdot_pulled_saap": True,
    },
    {
        "id": "rail_ghsa_montrose_stella_chaney",
        "name": "GH&SA / Southern Pacific Almeda Road, Blodgett & Montrose Line (Stella to Chaney Jct, 1880–1915)",
        "historic_company": "Galveston, Harrisburg & San Antonio Railway ('Sunset Route', Thomas W. Peirce / Southern Pacific, 1880–1915)",
        "charter_year": 1870,
        "opened_year": 1880,
        "modern_operator": "Montrose Segment Removed July–Fall 1915 (Preserved in Grant St Curve, Rosemont Bridge Piers & Almeda Corridor)",
        "rail_type": "abandoned_trail",
        "status": "Abandoned 1880–1915 Sunset Route Cutoff (Almeda Road Corridor & Diagonal Line Through Montrose)",
        "route_summary": "Stella / Pierce Junction north-northeast alongside Almeda Road past Brays Bayou, Hermann Park & Museum District to Blodgett Junction (Tower 12), then diagonally northwest through First Montrose Commons & Montrose (Tewena Stop, Grant Street & 1880 Rosemont Bridge Piers over Buffalo Bayou) to Chaney Junction",
        "historic_significance": (
            "Built in 1880–1881 so Southern Pacific's Galveston, Harrisburg & San Antonio ('Sunset Route') trains could "
            "reach Houston's northside yards and Grand Central Station without using the rival Columbia Tap, this line "
            "departed the Sunset Route at Stella (just west of Pierce Junction), ran 3.6 miles north-northeast alongside "
            "Almeda Road past Hermann Park and the Museum District to Blodgett Junction (Tower 12, where it crossed the SA&AP), "
            "and cut diagonally northwest across Montrose on a 4-to-5-foot earthen embankment. By 1911–1914, the embankment "
            "blocked 13 Montrose streets and caused severe neighborhood flooding, while the City capped train speeds at 6 mph. "
            "After Southern Pacific completed the Eureka-Stella Cutoff on the west side of town and sold two miles of the "
            "Montrose right-of-way to J.W. Link's Houston Land Corporation in January 1915, the last train ran through "
            "Montrose on Thursday night, July 15, 1915. Even after the tracks were pulled in the fall of 1915, the railroad "
            "left unmistakable imprints across Houston: the sweeping diagonal curve of Grant Street north of Westheimer, "
            "diagonal subdivision plat seams in Blodgett and First Montrose Commons (site of the 'Tewena' flag stop between "
            "Branard and West Main), and the surviving 1880 concrete railroad bridge piers in Buffalo Bayou Park that now "
            "support the Rosemont Pedestrian Bridge."
        ),
        "ghsa_almeda_montrose_corridor": True,
    },
    # 2. Historic Streetcar & Electric Interurban Lines (1874–1940)
    {
        "id": "rail_galveston_houston_interurban",
        "name": "Galveston-Houston Electric Railway ('The Interurban', 1911–1936)",
        "historic_company": "Galveston-Houston Electric Railway Co. (Stone & Webster Management, 1911–1936)",
        "charter_year": 1907,
        "opened_year": 1911,
        "modern_operator": "Removed 1936 (1006–1008 Texas Ave at Fannin via Jackson, Pierce & Sampson + Straight GH&H / HL&P Interurban Corridor)",
        "rail_type": "streetcar_interurban",
        "status": "Historic High-Speed Electric Interurban Railway (Operated Dec. 1911 – Oct. 1936)",
        "route_summary": "Downtown Interurban Terminal at 1006–1008 Texas Ave (near Texas & Fannin) east on Texas Ave, south on Jackson St, and southeast on Pierce & Sampson Streets into the straight Interurban corridor parallel to the GH&H through Park Place, South Houston, Genoa, Webster & Dickinson to Galveston",
        "historic_significance": (
            "Opened on December 5, 1911 by Stone & Webster from its Downtown Houston terminal at 1006–1008 Texas Avenue "
            "(between Main and Fannin Streets), the Galveston-Houston Electric Railway was a marvel of early-20th-century "
            "electric transit: a 50-mile, grade-separated, catenary-powered line that whisked passengers between Downtown "
            "Houston and Galveston Island in 75 minutes at speeds exceeding 60 mph - winning the 'Electric Traction Speed Cup' "
            "in 1925 and 1926 as the fastest interurban in North America. It directly spurred the development of Park Place "
            "(1912), South Houston, and Glenbrook Valley before making its final run on October 31, 1936."
        ),
        "interurban_corridor": True,
    },
    {
        "id": "rail_heights_blvd_streetcar",
        "name": "Houston Heights Boulevard Electric Streetcar Line (1891–1937)",
        "historic_company": "Omaha & South Texas Land Co. (1891) · Houston City Street Railway · Houston Electric Co. (Route #2)",
        "charter_year": 1891,
        "opened_year": 1892,
        "modern_operator": "Removed 1937 (Preserved as Heights Boulevard Esplanade & 19th Street Commercial Spine)",
        "rail_type": "streetcar_interurban",
        "status": "Historic Electric Streetcar Line (1891–1937)",
        "route_summary": "Downtown Market Square west via Washington Ave, north across White Oak Bayou up the center esplanade of Heights Boulevard (4th St to 20th St) and along W. 19th/20th Street",
        "historic_significance": (
            "When Oscar Martin Carter and the Omaha & South Texas Land Company founded Houston Heights in 1891 as a standalone "
            "streetcar suburb 62 feet above Downtown Houston's yellow fever marshes, they built a wide, tree-lined 120-foot "
            "boulevard with a twin-track electric streetcar running up its landscaped center median from 4th Street to "
            "20th Street (extending along 19th Street). The streetcar median is why Heights Boulevard has its iconic "
            "60-foot parkway esplanade today."
        ),
        "osm_streetcar_legs": [
            {
                "waypoints": [
                    [-95.3632, 29.7632],
                    [-95.3976, 29.7695],
                    [-95.3978, 29.8030],
                    [-95.4085, 29.8029],
                ],
                "streets": {"Washington Avenue", "Heights Boulevard", "West 19th Street", "West 20th Street"},
            }
        ],
        "coords": [],
    },
    {
        "id": "rail_woodland_heights_norhill_streetcar",
        "name": "Woodland Heights, Houston Avenue & Watson Street Streetcar Line (1907–1938)",
        "historic_company": "Houston Electric Company (Stone & Webster - Routes #5 Watson & #6 Houston Ave) & William A. Wilson Realty Co.",
        "charter_year": 1907,
        "opened_year": 1907,
        "modern_operator": "Removed 1938 (Preserved along Houston Ave to Bayland Gate, White Oak Dr & Watson St to Merrill St)",
        "rail_type": "streetcar_interurban",
        "status": "Historic Electric Streetcar Line (1907–1938)",
        "route_summary": "Downtown via Washington Ave & north up Houston Avenue across White Oak Bayou past the Woodland Heights Gates at Bayland Ave, plus the Route #5 Watson Street branch via White Oak Dr & Watson St to Merrill St",
        "historic_significance": (
            "To market Woodland Heights in October 1907 as 'A Miniature City in a Forest of Magnificent Pines and Oaks - Twenty "
            "Minutes from Main Street', developer William A. Wilson partnered with the Houston Electric Company on the Houston "
            "Avenue streetcar line, which stopped at the ornamental stone gates spanning Bayland Avenue at Houston Avenue and "
            "continued north to North Norhill. A second branch (Route #5 'Watson') turned west from Houston Avenue along White Oak "
            "Drive and ran north directly through the interior of Woodland Heights up Watson Street to Merrill Street."
        ),
        "osm_streetcar_legs": [
            {
                "waypoints": [
                    [-95.3665, 29.7648],
                    [-95.3723, 29.7662],
                    [-95.3723, 29.7810],
                    [-95.3809, 29.7810],
                    [-95.3809, 29.7889],
                ],
                "streets": {"Washington Avenue", "Houston Avenue", "White Oak Drive", "Watson Street", "Merrill Street"},
            },
            {
                "waypoints": [
                    [-95.3723, 29.7810],
                    [-95.3721, 29.7868],
                    [-95.3721, 29.7901],
                ],
                "streets": {"Houston Avenue"},
            },
        ],
        "coords": [],
    },
    {
        "id": "rail_studewood_norhill_streetcar",
        "name": "Studewood & Norhill Electric Streetcar Line (Route #4, 1910–1938)",
        "historic_company": "Houston Electric Company (Stone & Webster - Route #4 Studewood)",
        "charter_year": 1910,
        "opened_year": 1910,
        "modern_operator": "Removed 1938 (Preserved along White Oak Dr, Usener St & Studewood St through Woodland Heights & Norhill)",
        "rail_type": "streetcar_interurban",
        "status": "Historic Electric Streetcar Line (1910–1938)",
        "route_summary": "Houston Avenue west along White Oak Drive & Usener Street across Stude Park, then due north up Studewood Street along the western border of Woodland Heights and Norhill to 20th/30th Streets",
        "historic_significance": (
            "Operated by the Houston Electric Company as the Studewood Line (Route #4), this streetcar branched west off "
            "Houston Avenue along White Oak Drive and Usener Street above Stude Park and ran north up Studewood Street, "
            "providing direct streetcar service along the western edge of Woodland Heights, Norhill (1920), and North Norhill "
            "(1924) and fostering the historic corner commercial districts at Studewood & White Oak and Studewood & 11th/14th."
        ),
        "osm_streetcar_legs": [
            {
                "waypoints": [
                    [-95.3723, 29.7810],
                    [-95.3809, 29.7810],
                    [-95.3879, 29.7808],
                    [-95.3879, 29.8038],
                ],
                "streets": {"White Oak Drive", "Usener Street", "Studewood Street", "Studemont Street"},
            }
        ],
        "coords": [],
    },
    {
        "id": "rail_montrose_courtlandt_streetcar",
        "name": "Montrose (Roseland St), Avondale, Courtlandt Place & Mandell Electric Streetcar Lines (1906–1937)",
        "historic_company": "Houston Electric Company (Routes #22 Montrose & #23 Mandell) & Houston Land Corp. (J.W. Link, 1911–1912)",
        "charter_year": 1906,
        "opened_year": 1912,
        "modern_operator": "Removed March 1937 (Tuam, Fairview, Taft, Hawthorne & Roseland St to Richmond Ave + Mandell St to Richmond Ave)",
        "rail_type": "streetcar_interurban",
        "status": "Historic Electric Streetcar Line (1906–1937 · Montrose Branch Opened Aug. 18, 1912)",
        "route_summary": "Downtown southwest via Louisiana, Tuam & Fairview Streets through Avondale & Courtlandt Place, south on Taft, west on Hawthorne, and south down Roseland Street (one block east of Montrose Blvd) to Richmond Avenue, plus the Mandell branch via Fairview & Mandell Street to Richmond Avenue",
        "historic_significance": (
            "Extended into the South End to serve Courtlandt Place (1906) and Avondale (1907). When lumberman John Wiley Link "
            "and the Houston Land Corporation platted the master-planned suburb of Montrose in October 1911 with wide, "
            "palm-lined esplanades along Montrose Boulevard designed as a showplace parkway for automobiles, Link deliberately "
            "kept streetcar tracks off Montrose Boulevard itself. Instead, he partnered with the Houston Electric Company to "
            "route the Montrose Streetcar Line (opened August 18, 1912) via Taft and Hawthorne Streets and south down "
            "Roseland Street (one block east of Montrose Boulevard) through First Montrose Commons, terminating at Richmond "
            "Avenue (the southern boundary of the 1911 Montrose plat), while the Mandell Line (Route #23) branched west "
            "along Fairview Street and ran south down Mandell Street to Richmond Avenue."
        ),
        "osm_streetcar_legs": [
            {
                "waypoints": [
                    [-95.36550, 29.75750],
                    [-95.37815, 29.74508],
                    [-95.38397, 29.74831],
                    [-95.38570, 29.74822],
                    [-95.38593, 29.74250],
                    [-95.38992, 29.74238],
                    [-95.38982, 29.73447],
                ],
                "streets": {
                    "Louisiana Street",
                    "Tuam Street",
                    "Fairview Street",
                    "Taft Street",
                    "Hawthorne Street",
                    "Roseland Street",
                },
            },
            {
                "waypoints": [
                    [-95.38570, 29.74822],
                    [-95.40080, 29.74530],
                    [-95.39949, 29.73447],
                ],
                "streets": {"Fairview Street", "Mandell Street"},
            },
        ],
        "coords": [],
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
        "route_summary": "Downtown Main Street south through Midtown, Blodgett, Museum District, Hermann Park & Rice Institute to Holcombe/Bellaire Boulevard (connecting with the Bellaire Streetcar Line)",
        "historic_significance": (
            "Begun as a mule-drawn streetcar line in 1874 and electrified on June 12, 1891, the South End Main Street line "
            "was Houston's busiest transit artery, carrying students to the opening of Rice Institute in 1912, families to "
            "Hermann Park and the Houston Zoo, and passengers transferring at Holcombe/Bellaire Boulevard to the Westmoreland "
            "and Bellaire trolley. It made the final run of Houston's historic streetcar era on the night of June 8, 1940 - "
            "and 64 years later, the METRORail Red Line opened along the exact same Main Street corridor."
        ),
        "osm_streetcar_legs": [
            {
                "waypoints": [
                    [-95.35920, 29.76420],
                    [-95.37120, 29.74600],
                    [-95.38950, 29.72150],
                    [-95.39750, 29.71550],
                    [-95.40512, 29.70623],
                ],
                "streets": {"Main Street", "Fannin Street"},
            }
        ],
        "coords": [],
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
        "route_summary": "South End / Main Street junction at Holcombe Boulevard due west down the center esplanade of West Holcombe & Bellaire Boulevard through Southside Place & West University Place into the City of Bellaire",
        "historic_significance": (
            "Burlington Railroad vice president William Wright Baldwin purchased the 9,449-acre Rice ranch in 1908 to develop "
            "the town of Bellaire and surrounding 'Westmoreland Farms' citrus/truck-garden estates. In December 1910, Baldwin "
            "opened the 'Toonerville Trolley' down the broad grassy center esplanade of Holcombe and Bellaire Boulevards, "
            "connecting directly with the South End / Main Street streetcar line at Main & Holcombe and running west to "
            "Bellaire Boulevard & South Rice Avenue."
        ),
        "osm_streetcar_legs": [
            {
                "waypoints": [
                    [-95.40512, 29.70623],
                    [-95.43368, 29.70600],
                    [-95.46500, 29.70580],
                ],
                "streets": {"Main Street", "West Holcombe Boulevard", "Holcombe Boulevard", "Bellaire Boulevard"},
            }
        ],
        "coords": [],
    },
    {
        "id": "rail_harrisburg_eastwood_streetcar",
        "name": "Harrisburg, Eastwood & Magnolia Park Electric Streetcar Line (1892–1939)",
        "historic_company": "Houston City Street Railway (1892) · Houston Electric Company (Route #13 Port Houston)",
        "charter_year": 1892,
        "opened_year": 1892,
        "modern_operator": "Removed 1939 (Now Served by METRORail Green Line along Harrisburg Blvd)",
        "rail_type": "streetcar_interurban",
        "status": "Historic East End Electric Streetcar Spine (1892–1939)",
        "route_summary": "Downtown east via Preston/Congress & Harrisburg Boulevard through Second Ward, Eastwood (1913), Country Club Place & Magnolia Park to Harrisburg",
        "historic_significance": (
            "Electrified in 1892 along Harrisburg Road between Houston and historic Harrisburg, this streetcar line enabled "
            "William A. Wilson to develop Eastwood (1913) and Country Club Place as Craftsman and Four-Square streetcar "
            "suburbs for managers and workers along the newly opened Houston Ship Channel."
        ),
        "osm_streetcar_legs": [
            {
                "waypoints": [
                    [-95.3622, 29.7608],
                    [-95.34851, 29.75310],
                    [-95.2788, 29.7275],
                ],
                "streets": {"Preston Street", "Congress Street", "Harrisburg Boulevard"},
            }
        ],
        "coords": [],
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
        "name": "Galveston-Houston Electric Interurban Downtown Terminal Site (1006–1008 Texas Ave, 1911–1936)",
        "historic_company": "Galveston-Houston Electric Railway Co. (Stone & Webster)",
        "charter_year": 1911,
        "opened_year": 1911,
        "modern_operator": "Historic Site at 1006–1008 Texas Ave (at Fannin St), Downtown Houston",
        "rail_type": "depot",
        "status": "Historic Interurban Terminal Site (1911–1936)",
        "route_summary": "1006–1008 Texas Avenue between Main & Fannin Streets, Downtown Houston",
        "historic_significance": (
            "Northern passenger station and ticket terminal of the Galveston-Houston Electric Railway ('The Interurban') at "
            "1006–1008 Texas Avenue near Fannin Street, where hourly high-speed electric interurban parlor and commuter cars "
            "departed for Galveston Island between December 1911 and October 1936."
        ),
        "coords": [-95.36185, 29.75953],
    },
    {
        "id": "depot_tower_12_blodgett",
        "name": "Blodgett Junction & Interlocker Tower 12 Site (1888 / 1903–1915 - GH&SA & SA&AP Crossing)",
        "historic_company": "Texas Railroad Commission Interlocker #12 (GH&SA 'Sunset Route' & San Antonio & Aransas Pass Ry.)",
        "charter_year": 1888,
        "opened_year": 1903,
        "modern_operator": "Historic Site near Wheeler/Blodgett, Main/Fannin & US-59 / Spur 527 (South End of Montrose / Museum District)",
        "rail_type": "depot",
        "status": "Historic Railroad Diamond & Interlocking Tower Site (1888 Crossing · Tower 12 Operated 1903–1915)",
        "route_summary": "Blodgett / Wheeler St between Main/Fannin & Almeda Rd on the south edge of Montrose",
        "historic_significance": (
            "Established in 1888 where the San Antonio & Aransas Pass Railway ('The SAP') crossed the 1880 Galveston, "
            "Harrisburg & San Antonio (Southern Pacific) line from Stella to Chaney Junction on the southern edge of Montrose. "
            "Commissioned by the Railroad Commission of Texas on July 4, 1903 as Interlocker Tower 12 with connecting wye "
            "tracks in its southeast and northwest quadrants, it controlled trains entering Montrose and East Downtown until "
            "Southern Pacific abandoned the Montrose embankment north of Blodgett in July 1915."
        ),
        "coords": [-95.38260, 29.72960],
    },
    {
        "id": "depot_tower_134_pierce_jct",
        "name": "Pierce Junction & Interlocker Tower 134 (1856 / 1880 - First Railroad Junction in Texas)",
        "historic_company": "Buffalo Bayou, Brazos & Colorado Ry. (1853) · Houston Tap & Brazoria Ry. (1856) · GH&SA & I&GN",
        "charter_year": 1856,
        "opened_year": 1856,
        "modern_operator": "Union Pacific Railroad (Almeda Road & Glidden Subdivision Crossing, South Houston)",
        "rail_type": "depot",
        "status": "Historic Birthplace of Texas Railroad Junctions (Established Oct. 1856)",
        "route_summary": "Almeda Road at the BBB&C / GH&SA Sunset Route crossing (6.5 miles south of Downtown)",
        "historic_significance": (
            "The first railroad junction in Texas, created in October 1856 when the municipal Houston Tap Railroad built "
            "south from Houston along Almeda Road to 'tap' the 1853 Buffalo Bayou, Brazos & Colorado Railway. Later named "
            "for GH&SA president and Arcola sugar planter Thomas W. Peirce ('Pierce Junction'), it was also the point (at "
            "nearby 'Stella', 1,000 feet west) where the 1880 GH&SA line diverged north alongside Almeda Road toward Blodgett "
            "and Montrose."
        ),
        "coords": [-95.39606, 29.67198],
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
        "name": "Chaney Junction (1860s–1890s H&TC, GH&SA & T&NO West Loop Junction - Tower 108)",
        "historic_company": "Houston & Texas Central Ry. · Galveston, Harrisburg & San Antonio Ry. · Texas & New Orleans RR",
        "charter_year": 1856,
        "opened_year": 1880,
        "modern_operator": "Union Pacific Railroad (Washington Ave & Studemont / Sawyer Yards)",
        "rail_type": "depot",
        "status": "Active Historic Railroad Wye & Junction",
        "route_summary": "Washington Ave & Sawyer/Studemont between Old Sixth Ward and Sawyer Yards",
        "historic_significance": (
            "Historic railroad wye where the 1880–1915 GH&SA line through Montrose met the 1856 H&TC mainline, splitting "
            "trains between the passenger approach into Grand Central Station along White Oak Bayou and the northern freight "
            "bypass loop to Hardy Street Shops and Englewood Yard."
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
    if s.lower() == "old buffalo bayou":
        return "Old Buffalo Bayou"
    if s.lower() == "old sims bayou":
        return "Old Sims Bayou"
    if s.lower() == "old turkey creek":
        return "Old Turkey Creek"
    if "buffalo bayou" in s.lower():
        return "Buffalo Bayou"
    return s


def load_ward_1903_shared_boundaries() -> dict:
    """Load exact 1903 Aldermanic Ward boundaries (which follow the pre-channelization bayou centerlines)."""
    ward_path = CACHE_DIR / "coh_wards_1903.geojson"
    if not ward_path.exists():
        return {}
    with open(ward_path, "r", encoding="utf-8") as f:
        feats = json.load(f).get("features") or []
    polys = {}
    for feat in feats:
        w = (feat.get("properties") or {}).get("WARD")
        if w:
            polys[w] = shape(feat["geometry"]).buffer(0)
    out = {}
    for w1, w2 in [("FOURTH", "SIXTH"), ("SECOND", "FIFTH"), ("FIRST", "FIFTH")]:
        if w1 in polys and w2 in polys:
            inter = safe_linemerge(polys[w1].boundary.intersection(polys[w2].boundary))
            if inter and not inter.is_empty:
                out[(w1, w2)] = inter
    return out


def build_waterways_collection() -> list:
    print("1. Building High-Resolution Historical Waterways from OpenStreetMap, USGS NHD, COH GIS & 1903 Ward Surveys...")
    grouped_lines = {}  # name -> {"osm": [], "nhd": [], "coh": [], "pwe": [], "is_culvert": False, "category": "Minor"}

    # 1A. OpenStreetMap High-Resolution Waterways Cache (single centerline, aerial-traced)
    osm_cache_path = CACHE_DIR / "osm_waterways_and_rail_trails.json"
    if osm_cache_path.exists():
        with open(osm_cache_path, "r", encoding="utf-8") as f:
            osm_els = json.load(f).get("elements") or []
        osm_count = 0
        for el in osm_els:
            tags = el.get("tags") or {}
            wtype = tags.get("waterway")
            raw_nm = tags.get("name") or ""
            geom = el.get("geometry") or []
            if wtype in ("river", "stream", "canal", "drain", "ditch") and raw_nm and len(geom) >= 2:
                if raw_nm.strip().lower().startswith("unnamed"):
                    continue
                nm = normalize_waterway_name(raw_nm)
                if not nm:
                    continue
                ln = LineString([(round(float(pt["lon"]), 6), round(float(pt["lat"]), 6)) for pt in geom])
                if ln.length > 0:
                    rec = grouped_lines.setdefault(
                        nm, {"osm": [], "nhd": [], "coh": [], "pwe": [], "is_culvert": False, "category": "Minor"}
                    )
                    rec["osm"].append(ln)
                    osm_count += 1
        print(f"   OpenStreetMap High-Res Waterways: {osm_count} ways")

    # 1B. COH Water_Line_Texas_ClippedCOH
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
        rec = grouped_lines.setdefault(
            nm, {"osm": [], "nhd": [], "coh": [], "pwe": [], "is_culvert": False, "category": cat}
        )
        rec["coh"].append(g)
        if cat == "Major":
            rec["category"] = "Major"

    # 1C. USGS NHD Flowline
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
        rec = grouped_lines.setdefault(
            nm, {"osm": [], "nhd": [], "coh": [], "pwe": [], "is_culvert": False, "category": "Minor"}
        )
        rec["nhd"].append(g)
        if ftype == 428:
            rec["is_culvert"] = True

    # 1D. COH Public Works Maintained Waterways (captures buried storm-sewer waterways like Slaughterpen Bayou, City Ditch, Yates Gully, Cypress Slough)
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
        rec = grouped_lines.setdefault(
            nm, {"osm": [], "nhd": [], "coh": [], "pwe": [], "is_culvert": False, "category": "Minor"}
        )
        rec["pwe"].append(g)
        if ltype == "Storm Sewer":
            rec["is_culvert"] = True

    waterway_features = []
    idx = 1
    for nm, info in sorted(grouped_lines.items()):
        # Choose a SINGLE authoritative source per waterway so competing centerlines from 3 datasets never overlap/crisscross!
        if nm in ("Slaughterpen Bayou", "City Ditch", "Yates Gully", "Cypress Slough") and info["pwe"]:
            chosen_geoms = info["pwe"]
            source_label = "City of Houston Public Works Storm Sewer GIS Archive"
        elif info["osm"]:
            chosen_geoms = list(info["osm"])
            source_label = "OpenStreetMap High-Resolution Hydrography + USGS NHD Archive"
            # If NHD or COH has major unmapped reaches outside a ~120m buffer of OSM (e.g. Houston Ship Channel lower bay reach), append only non-overlapping segments
            osm_union = safe_linemerge(unary_union(chosen_geoms))
            fallback_pool = info["nhd"] or info["coh"]
            if fallback_pool:
                fb_union = safe_linemerge(unary_union(fallback_pool))
                if fb_union.length > osm_union.length * 1.35:
                    diff = fb_union.difference(osm_union.buffer(0.0012))
                    if not diff.is_empty and diff.length > 0.005:
                        chosen_geoms.append(diff)
        elif info["nhd"]:
            chosen_geoms = info["nhd"]
            source_label = "USGS National Hydrography Dataset (NHD)"
        elif info["coh"]:
            chosen_geoms = info["coh"]
            source_label = "City of Houston Hydrography GIS Archive"
        elif info["pwe"]:
            chosen_geoms = info["pwe"]
            source_label = "City of Houston Public Works Waterways GIS Archive"
        else:
            continue

        try:
            merged = safe_linemerge(unary_union(chosen_geoms))
        except Exception:
            merged = chosen_geoms[0]

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

        # Keep full smooth resolution (tol=0.000018 ~ 1.8m) so natural meanders are never angular
        geom_json = round_coords_geom(merged, tol=0.000018, precision=5)
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
            "source": source_label,
        }
        waterway_features.append({"type": "Feature", "properties": props, "geometry": geom_json})
        idx += 1

    # 1E. Append Surveyed Buried Gullies (HCFCD Closed Conduits) & Pre-Channelization Natural Bayou Oxbows (1903 Ward Surveys & COH Hydrography)
    ward_1903_bounds = load_ward_1903_shared_boundaries()
    hcfcd_conduits_by_unit = {}
    hcfcd_cache_path = CACHE_DIR / "hcfcd_closed_conduits.json"
    if hcfcd_cache_path.exists():
        with open(hcfcd_cache_path, "r", encoding="utf-8") as f:
            for cf in json.load(f):
                u = ((cf.get("attributes") or {}).get("UnitNumber") or "").strip()
                g = esri_paths_to_shapely(cf.get("geometry"))
                if u and g is not None:
                    hcfcd_conduits_by_unit.setdefault(u, []).append(g)

    brady_oxbow_geoms = []
    brady_cache_path = CACHE_DIR / "coh_brady_island_oxbow.json"
    if brady_cache_path.exists():
        with open(brady_cache_path, "r", encoding="utf-8") as f:
            for bf in json.load(f):
                g = esri_paths_to_shapely(bf.get("geometry"))
                if g is not None:
                    brady_oxbow_geoms.append(g)

    for item in CURATED_BURIED_GULLIES_AND_OXBOWS:
        ward_pair = item.get("ward_boundary_pair")
        if ward_pair and ward_pair in ward_1903_bounds:
            raw_geom = ward_1903_bounds[ward_pair]
            if raw_geom.geom_type == "LineString":
                ln = LineString(catmull_rom_spline(list(raw_geom.coords), subdivisions=4))
            else:
                ln = safe_linemerge(
                    MultiLineString([LineString(catmull_rom_spline(list(g.coords), subdivisions=4)) for g in raw_geom.geoms])
                )
            src_label = "1903 City of Houston Aldermanic Ward Charter Survey & 1915 USGS Houston Topo Quad"
        elif item.get("hcfcd_units"):
            unit_geoms = []
            for u in item["hcfcd_units"]:
                unit_geoms.extend(hcfcd_conduits_by_unit.get(u, []))
            if not unit_geoms:
                continue
            ln = safe_linemerge(unary_union(unit_geoms))
            src_label = "Harris County Flood Control District (HCFCD M3 Closed Conduit Survey) & 1915 USGS Houston Topo Quad"
        elif item.get("brady_island_survey") and brady_oxbow_geoms:
            ln = safe_linemerge(unary_union(brady_oxbow_geoms))
            src_label = "City of Houston Hydrography GIS Survey (Pre-1914 Brady Island Channel)"
        else:
            continue

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
            "source": src_label,
        }
        waterway_features.append(
            {
                "type": "Feature",
                "properties": props,
                "geometry": round_coords_geom(ln, tol=0.00001, precision=5),
            }
        )

    # 1F. Recover Countywide HCFCD Open Tributary Creeks, Historic Natural Channels & Unnamed USGS NHD / COH Tributary Streams
    from shapely.ops import nearest_points
    from shapely.strtree import STRtree

    HCFCD_WATERSHED_MAP = {
        "A": ("Clear Creek", "Clear Creek Watershed (HCFCD Unit A)"),
        "B": ("Armand Bayou", "Armand Bayou Watershed (HCFCD Unit B)"),
        "C": ("Sims Bayou", "Sims Bayou Watershed (HCFCD Unit C)"),
        "D": ("Brays Bayou", "Brays Bayou Watershed (HCFCD Unit D)"),
        "E": ("White Oak Bayou", "White Oak Bayou Watershed (HCFCD Unit E)"),
        "F": ("Galveston Bay Coastal", "Galveston Bay Coastal Watershed (HCFCD Unit F)"),
        "G": ("San Jacinto River", "San Jacinto River Watershed (HCFCD Unit G)"),
        "H": ("Hunting Bayou", "Hunting Bayou Watershed (HCFCD Unit H)"),
        "I": ("Vince Bayou", "Vince Bayou Watershed (HCFCD Unit I)"),
        "J": ("Spring Creek", "Spring Creek Watershed (HCFCD Unit J)"),
        "K": ("Cypress Creek", "Cypress Creek Watershed (HCFCD Unit K)"),
        "L": ("Little Cypress Creek", "Little Cypress Creek Watershed (HCFCD Unit L)"),
        "M": ("Willow Creek", "Willow Creek Watershed (HCFCD Unit M)"),
        "N": ("Carpenters Bayou", "Carpenters Bayou Watershed (HCFCD Unit N)"),
        "O": ("Spring Gully", "Spring Gully Watershed (HCFCD Unit O)"),
        "P": ("Greens & Halls Bayou", "Greens Bayou Watershed (HCFCD Unit P)"),
        "Q": ("Cedar Bayou", "Cedar Bayou Watershed (HCFCD Unit Q)"),
        "R": ("Jackson Bayou", "Jackson Bayou Watershed (HCFCD Unit R)"),
        "S": ("Luce Bayou", "Luce Bayou Watershed (HCFCD Unit S)"),
        "T": ("Cane Island Branch", "Cane Island Branch Watershed (HCFCD Unit T)"),
        "U": ("Addicks / Langham Creek", "Addicks Reservoir Watershed (HCFCD Unit U)"),
        "V": ("Barker / Upper Buffalo Bayou", "Barker Reservoir Watershed (HCFCD Unit V)"),
        "W": ("Buffalo Bayou", "Buffalo Bayou Watershed (HCFCD Unit W)"),
    }

    existing_bufs = []
    existing_parents = []
    for wf in waterway_features:
        g = shape(wf["geometry"])
        existing_bufs.append(g.buffer(0.00042))
        existing_parents.append((g, wf["properties"]["name"], wf["properties"]["watershed"]))
    buf_tree = STRtree(existing_bufs)
    base_parent_geoms = [p[0] for p in existing_parents]
    base_parent_tree = STRtree(base_parent_geoms)

    def subtract_existing(geom, tree, bufs):
        idxs = tree.query(geom)
        if len(idxs) == 0:
            return geom
        sub_u = unary_union([bufs[i] for i in idxs])
        return geom.difference(sub_u)

    def snap_tributary_endpoints(geom, p_tree, p_geoms, max_snap_deg=0.00068):
        """Snap tributary endpoints that were clipped by parent bayou buffers directly onto the parent centerline."""
        if geom is None or geom.is_empty:
            return geom
        parts = [geom] if isinstance(geom, LineString) else list(getattr(geom, "geoms", []))
        snapped_parts = []
        for part in parts:
            if not isinstance(part, LineString) or len(part.coords) < 2:
                continue
            coords = list(part.coords)
            pt0 = Point(coords[0])
            pt1 = Point(coords[-1])
            idx0 = int(p_tree.nearest(pt0))
            idx1 = int(p_tree.nearest(pt1))
            p0 = p_geoms[idx0]
            p1 = p_geoms[idx1]
            d0 = pt0.distance(p0)
            d1 = pt1.distance(p1)
            # Skip short parallel chord fragments whose both ends hug the same parent waterway
            if d0 <= max_snap_deg and d1 <= max_snap_deg and idx0 == idx1 and part.length < 0.0022:
                continue
            if 0 < d0 <= max_snap_deg:
                s0 = nearest_points(pt0, p0)[1]
                coords = [(s0.x, s0.y)] + coords
            if 0 < d1 <= max_snap_deg:
                s1 = nearest_points(pt1, p1)[1]
                coords = coords + [(s1.x, s1.y)]
            if len(coords) >= 2:
                snapped_parts.append(LineString(coords))
        if not snapped_parts:
            return LineString()
        return snapped_parts[0] if len(snapped_parts) == 1 else MultiLineString(snapped_parts)

    m3_cache_path = CACHE_DIR / "hcfcd_m3_all_existing.json"
    if m3_cache_path.exists():
        with open(m3_cache_path, "r", encoding="utf-8") as f:
            m3_all = json.load(f)
    else:
        m3_all = fetch_arcgis_features(
            "https://services2.arcgis.com/nLl0k0Mja5hnSeSl/arcgis/rest/services/M3_HCFCD_Drainage_Network/FeatureServer/0",
            where="Type NOT IN ('PROPOSED', 'Proposed', 'CONNECTOR')",
            out_fields="UnitNumber,Type,MainStem_or_Trib,Drains_To_Sub_Reach,Wtsh_Unit,Comments",
        )
        with open(m3_cache_path, "w", encoding="utf-8") as f:
            json.dump(m3_all, f)

    m3_groups = {}
    for f in m3_all:
        a = f.get("attributes") or {}
        wtsh = (a.get("Wtsh_Unit") or "W").strip()
        u = (a.get("UnitNumber") or "").strip() or f"HIST-{wtsh}"
        t = (a.get("Type") or "OPEN").strip().upper()
        # Skip generic suburban street-grid storm sewer pipes (curated historic buried gullies are built in Step 1D)
        if t in ("CLOSED CONDUIT", "STORM SEWER"):
            continue
        wtype = "historic_oxbow" if t == "HISTORICAL" else "creek"
        drains_to = (a.get("Drains_To_Sub_Reach") or "").strip().split("_")[0]
        g = esri_paths_to_shapely(f.get("geometry"))
        if g is not None and not g.is_empty:
            rec = m3_groups.setdefault((u, wtype, wtsh), {"geoms": [], "drains_to": drains_to})
            rec["geoms"].append(g)

    added_m3_count = 0
    new_m3_bufs = []
    for (u, wtype, wtsh), rec in sorted(m3_groups.items()):
        merged = safe_linemerge(unary_union(rec["geoms"]))
        if not merged or merged.is_empty:
            continue
        diff = subtract_existing(merged, buf_tree, existing_bufs)
        if diff.is_empty:
            continue
        diff = snap_tributary_endpoints(diff, base_parent_tree, base_parent_geoms)
        if diff.is_empty:
            continue
        length_mi = approx_length_miles(diff)
        if length_mi < 0.08:
            continue
        geom_json = round_coords_geom(diff, tol=0.00002, precision=5)
        if not geom_json:
            continue

        parent_bayou, wtsh_label = HCFCD_WATERSHED_MAP.get(
            wtsh, ("Houston Bayou", f"HCFCD Watershed {wtsh}")
        )
        drains_note = f" (draining into HCFCD Unit {rec['drains_to']})" if rec["drains_to"] else ""
        slug_u = re.sub(r"[^a-z0-9]+", "_", u.lower()).strip("_")

        if wtype == "historic_oxbow":
            feat_name = f"{parent_bayou} Historic Natural Channel ({u})"
            status_str = f"Historic Natural Stream Channel (HCFCD Archive {u})"
            sig_str = (
                f"Surveyed historic natural stream channel in the {wtsh_label} preserved in the Harris County Flood "
                f"Control District (HCFCD) historical drainage archive."
            )
        else:
            feat_name = f"{parent_bayou} Tributary ({u})"
            status_str = f"Historic & Flood-Control Tributary Creek (HCFCD Unit {u})"
            sig_str = (
                f"Natural and flood-control tributary stream (HCFCD Unit {u}) in the {wtsh_label}{drains_note}. "
                f"Part of Harris County's lateral creek and drainage network feeding {parent_bayou}."
            )

        waterway_features.append(
            {
                "type": "Feature",
                "properties": {
                    "id": f"waterway_hcfcd_{slug_u}_{wtype}",
                    "name": feat_name,
                    "alt_names": [f"HCFCD Unit {u}", f"{parent_bayou} Tributary {u}"],
                    "waterway_type": wtype,
                    "status": status_str,
                    "watershed": wtsh_label,
                    "era_notes": f"HCFCD Unit {u} · {wtsh_label}",
                    "historic_significance": sig_str,
                    "length_miles": length_mi,
                    "source": "Harris County Flood Control District (HCFCD M3 Drainage Network Survey)",
                    "is_minor_trib": True,
                },
                "geometry": geom_json,
            }
        )
        new_m3_bufs.append(diff.buffer(0.00042))
        existing_parents.append((diff, parent_bayou, wtsh_label))
        added_m3_count += 1

    # Also recover unnamed USGS NHD / City of Houston hydrography stream reaches (e.g., pond/ravine tributaries on basemap)
    all_bufs = existing_bufs + new_m3_bufs
    all_buf_tree = STRtree(all_bufs)
    parent_geoms = [p[0] for p in existing_parents]
    parent_tree = STRtree(parent_geoms)

    coh_all_path = CACHE_DIR / "coh_water_lines_all.json"
    if coh_all_path.exists():
        with open(coh_all_path, "r", encoding="utf-8") as f:
            coh_all_feats = json.load(f)
    else:
        coh_all_feats = fetch_arcgis_features(
            "https://services.arcgis.com/NummVBqZSIJKUeVR/arcgis/rest/services/Water_Line_Texas_ClippedCOH/FeatureServer/0",
            where="1=1",
        )
        with open(coh_all_path, "w", encoding="utf-8") as f:
            json.dump(coh_all_feats, f)

    coh_unnamed_rc = {}
    for f in coh_all_feats:
        a = f.get("attributes") or {}
        if (a.get("NAME") or "").strip():
            continue
        rc = (a.get("ReachCode") or "").strip()
        g = esri_paths_to_shapely(f.get("geometry"))
        if rc and g is not None and not g.is_empty:
            coh_unnamed_rc.setdefault(rc, []).append(g)

    added_nhd_count = 0
    for rc, geoms in sorted(coh_unnamed_rc.items()):
        merged = safe_linemerge(unary_union(geoms))
        if not merged or merged.is_empty:
            continue
        diff = subtract_existing(merged, all_buf_tree, all_bufs)
        if diff.is_empty:
            continue
        diff = snap_tributary_endpoints(diff, parent_tree, parent_geoms)
        if diff.is_empty:
            continue
        length_mi = approx_length_miles(diff)
        if length_mi < 0.08:
            continue
        geom_json = round_coords_geom(diff, tol=0.00002, precision=5)
        if not geom_json:
            continue

        nearest_idx = parent_tree.nearest(diff)
        _, p_name, p_wtsh = existing_parents[int(nearest_idx)]
        short_parent = p_name.split(" (")[0].split(" - ")[0].replace(" Tributary", "")
        short_rc = rc[-6:] if len(rc) >= 6 else rc

        waterway_features.append(
            {
                "type": "Feature",
                "properties": {
                    "id": f"waterway_nhd_{rc}",
                    "name": f"{short_parent} Tributary Stream (NHD #{short_rc})",
                    "alt_names": [f"USGS NHD Reach {rc}", f"{short_parent} Natural Ravine / Tributary"],
                    "waterway_type": "creek",
                    "status": f"Natural Tributary Stream & Ravine (USGS NHD Reach {rc})",
                    "watershed": p_wtsh,
                    "era_notes": f"USGS National Hydrography Dataset (Reach {rc}) & City of Houston Hydrography",
                    "historic_significance": (
                        f"Natural tributary stream and drainage ravine (USGS NHD Reach {rc}) in the {p_wtsh}, "
                        f"feeding {short_parent} and preserved on USGS topographic maps and City of Houston hydrography."
                    ),
                    "length_miles": length_mi,
                    "source": "USGS National Hydrography Dataset (NHD) & City of Houston Hydrography",
                    "is_minor_trib": True,
                },
                "geometry": geom_json,
            }
        )
        added_nhd_count += 1

    print(
        f"   Recovered +{added_m3_count} HCFCD M3 open/historic tributaries and +{added_nhd_count} unnamed NHD tributary streams"
    )
    print(f"   -> Built {len(waterway_features)} total Historical Waterway features")
    return waterway_features


def build_osm_streetcar_router():
    """Build a fast spatial-grid-indexed Dijkstra router over cached OpenStreetMap street centerlines."""
    import heapq

    street_cache = CACHE_DIR / "osm_streetcar_streets.json"
    if not street_cache.exists():
        return None
    with open(street_cache, "r", encoding="utf-8") as f:
        els = json.load(f).get("elements") or []

    adj = {}
    grid = {}

    def snap_node(lon, lat):
        k = (round(float(lon), 5), round(float(lat), 5))
        gx, gy = int(k[0] * 1200), int(k[1] * 1200)
        grid.setdefault((gx, gy), set()).add(k)
        return k

    for el in els:
        tags = el.get("tags") or {}
        nm = tags.get("name") or ""
        geom = el.get("geometry") or []
        if len(geom) < 2:
            continue
        pts = [snap_node(p["lon"], p["lat"]) for p in geom]
        for i in range(len(pts) - 1):
            u, v = pts[i], pts[i + 1]
            if u == v:
                continue
            d = math.hypot(u[0] - v[0], u[1] - v[1])
            adj.setdefault(u, []).append((v, d, nm))
            adj.setdefault(v, []).append((u, d, nm))

    for (gx, gy), cell_nodes in list(grid.items()):
        nbrs = set()
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                nbrs |= grid.get((gx + dx, gy + dy), set())
        for u in cell_nodes:
            for v in nbrs:
                if u < v:
                    d = math.hypot(u[0] - v[0], u[1] - v[1])
                    if d <= 0.00075:
                        adj.setdefault(u, []).append((v, d * 2.5, "intersection"))
                        adj.setdefault(v, []).append((u, d * 2.5, "intersection"))

    def nearest_node(lon, lat, preferred_streets=None):
        best = None
        best_d = 1e9
        for u, edges in adj.items():
            if preferred_streets and not any(st in preferred_streets for _, _, st in edges):
                continue
            d = math.hypot(u[0] - lon, u[1] - lat)
            if d < best_d:
                best_d = d
                best = u
        if best is None or best_d > 0.003:
            for u in adj.keys():
                d = math.hypot(u[0] - lon, u[1] - lat)
                if d < best_d:
                    best_d = d
                    best = u
        return best

    def route_legs(legs: list):
        lines = []
        for leg in legs:
            waypoints = leg["waypoints"]
            preferred_streets = leg.get("streets")
            full_coords = []
            for i in range(len(waypoints) - 1):
                s = nearest_node(waypoints[i][0], waypoints[i][1], preferred_streets)
                t = nearest_node(waypoints[i + 1][0], waypoints[i + 1][1], preferred_streets)
                pq = [(0.0, s)]
                dist = {s: 0.0}
                prev = {}
                while pq:
                    cost, u = heapq.heappop(pq)
                    if u == t:
                        break
                    if cost > dist.get(u, 1e9):
                        continue
                    for v, d, st in adj.get(u, []):
                        penalty = 1.0 if (not preferred_streets or st in preferred_streets) else 10.0
                        nc = cost + d * penalty
                        if nc < dist.get(v, 1e9):
                            dist[v] = nc
                            prev[v] = u
                            heapq.heappush(pq, (nc, v))
                if t not in prev and s != t:
                    seg = [s, t]
                else:
                    seg = []
                    cur = t
                    while cur != s:
                        seg.append(cur)
                        cur = prev[cur]
                    seg.append(s)
                    seg.reverse()
                if full_coords and seg[0] == full_coords[-1]:
                    full_coords.extend(seg[1:])
                else:
                    full_coords.extend(seg)
            if len(full_coords) >= 2:
                lines.append(LineString(full_coords))
        if not lines:
            return None
        return safe_linemerge(unary_union(lines))

    return route_legs


def build_railroads_collection() -> list:
    print("2. Fetching Historical Railroads from TxDOT, OpenStreetMap Rail-Trails & Streetcar Centerlines...")
    railroad_features = []

    # Load OpenStreetMap rail-trails from cache
    osm_trails_by_name = {}
    osm_cache_path = CACHE_DIR / "osm_waterways_and_rail_trails.json"
    if osm_cache_path.exists():
        with open(osm_cache_path, "r", encoding="utf-8") as f:
            for el in json.load(f).get("elements") or []:
                tags = el.get("tags") or {}
                nm = tags.get("name") or ""
                geom = el.get("geometry") or []
                if nm and len(geom) >= 2 and ("Trail" in nm or "Rail" in nm):
                    ln = LineString([(round(float(pt["lon"]), 6), round(float(pt["lat"]), 6)) for pt in geom])
                    if ln.length > 0:
                        osm_trails_by_name.setdefault(nm, []).append(ln)

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

        if subdiv == "POPP":
            # In Harris County, TxDOT's POPP subdivision is the active southern Almeda Road segment of the 1856 Columbia Tap
            subdiv = "COLUMBIA TAP INDUSTRIAL LEAD"

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
        if key != "subdiv_COLUMBIA TAP INDUSTRIAL LEAD":
            rec["geoms"].append(g)

    # 2B. Fetch Harrisburg-Sunset Trail from OSM + COH ArcGIS (Columbia Tap uses TxDOT Deprecated OBJECTID 10456 below)

    hb_sunset_geoms = list(osm_trails_by_name.get("Harrisburg Hike & Bike Trail", []))
    if not hb_sunset_geoms:
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
    pulled_mkt_geoms = []
    pulled_saap_geoms = []
    pulled_spur_geoms = []

    # Surveyed SA&AP ('The SAP') OBJECTIDs across Westpark/Bellaire AND Third Ward/EaDo (where TxDOT classified the eastern half as Spur Line after the US-59 cut)
    SAAP_DEPRECATED_OIDS = {
        10548, 515, 1502, 1503, 1775,
        2320, 2074, 2073, 2463, 2449, 2442, 2441,
        2402, 2395, 2396, 2384, 2373, 2368,
        2213, 2203, 2204, 2208, 2196, 2193,
        477, 10208, 207, 323, 2706,
    }
    # Surveyed Columbia Tap / I&GN OBJECTIDs along Almeda Rd, Third Ward, and East Downtown
    COLUMBIA_TAP_DEPRECATED_OIDS = {10456, 10457, 2410, 2382}

    for f in dep_feats:
        attr = f.get("attributes") or {}
        oid = attr.get("OBJECTID")
        g = esri_paths_to_shapely(f.get("geometry"))
        if g is None:
            continue
        rtyp = (attr.get("RR_TYP") or "").strip()
        if oid in SAAP_DEPRECATED_OIDS:
            pulled_saap_geoms.append(g)
        elif oid in COLUMBIA_TAP_DEPRECATED_OIDS:
            rec = grouped_rr.setdefault(
                "subdiv_COLUMBIA TAP INDUSTRIAL LEAD",
                {"meta": dict(TXDOT_SUBDIV_TO_HISTORICAL_RR["COLUMBIA TAP INDUSTRIAL LEAD"]), "geoms": []},
            )
            rec["geoms"].append(g)
        elif rtyp == "Main Line" and g.bounds[1] >= 29.760 and g.bounds[0] < -95.355:
            pulled_mkt_geoms.append(g)
        else:
            pulled_spur_geoms.append(g)

    # Connect the 1856 Columbia Tap / I&GN continuously from OBJECTID 10456 (-95.34623, 29.74734) through
    # East Downtown (Velasco / St. Emanuel) to the 1879 I&GN Depot at Congress & St. Emanuel (-95.35220, 29.75950)
    if "subdiv_COLUMBIA TAP INDUSTRIAL LEAD" in grouped_rr:
        ct_geoms = grouped_rr["subdiv_COLUMBIA TAP INDUSTRIAL LEAD"]["geoms"]
        ct_geoms.append(
            LineString(
                [
                    (-95.346227, 29.747337),
                    (-95.35101, 29.74968),
                    (-95.35218, 29.75331),
                    (-95.35221, 29.75365),
                    (-95.35220, 29.75950),
                ]
            )
        )

    # Connect the central inner-loop segment of the San Antonio & Aransas Pass ('The SAP') from Shepherd & US-59
    # alongside the Southwest Freeway (Colby Court / South End Villa / MacGregor's Blodgett Park plat ROWs) through
    # Blodgett Junction (Tower 12) and Third Ward into the surveyed EaDo SA&AP tracks (OBJECTID 2320), plus short
    # grade-crossing bridges across Polk St and Capitol Ave in EaDo
    if pulled_saap_geoms:
        pulled_saap_geoms.append(
            LineString(
                [
                    (-95.40808, 29.73019),  # Eastern end of OID 10548 at Shepherd & Southwest Freeway
                    (-95.39850, 29.72975),  # Alongside US-59 / Mandell-Dunlavy
                    (-95.39028, 29.72936),  # Colby Court / Roseland south ROW
                    (-95.38698, 29.72937),  # South End Villa / Spur 527 ROW
                    (-95.38408, 29.72939),  # MacGregor's Blodgett Park west edge
                    (-95.38260, 29.72960),  # Blodgett Junction (Tower 12 - crossing GH&SA line)
                    (-95.38080, 29.73062),  # MacGregor's Blodgett Park diagonal ROW
                    (-95.37937, 29.73243),  # Blodgett / Wheeler northeast ROW
                    (-95.37537, 29.73334),  # Crossing Almeda Rd in Third Ward
                    (-95.37050, 29.73555),  # Third Ward northeast alignment
                    (-95.36569, 29.73781),  # Exact western vertex of OID 2320 in Third Ward / EaDo
                ]
            )
        )
        pulled_saap_geoms.append(LineString([(-95.35218, 29.75331), (-95.35224, 29.75435)]))
        pulled_saap_geoms.append(LineString([(-95.35031, 29.75813), (-95.34962, 29.75913), (-95.34936, 29.75756)]))

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
            merged = safe_linemerge(unary_union(geoms))
        except Exception:
            merged = geoms[0]

        length_mi = approx_length_miles(merged)
        if length_mi < 0.08:
            continue

        geom_json = round_coords_geom(merged, tol=0.00002, precision=5)
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
            "source": "TxDOT Rail Archive, OpenStreetMap & Preservation Houston Historical Research",
        }
        railroad_features.append({"type": "Feature", "properties": props, "geometry": geom_json})
        idx += 1

    # 2D. Append Surveyed Abandoned Pioneer Rail Corridors & Street-Aligned Historic Streetcar / Interurban Lines
    streetcar_router = build_osm_streetcar_router()
    for item in CURATED_ABANDONED_RAILS_AND_STREETCARS:
        if item.get("txdot_pulled_mkt") and pulled_mkt_geoms:
            ln = safe_linemerge(unary_union(pulled_mkt_geoms))
        elif item.get("txdot_pulled_saap") and pulled_saap_geoms:
            ln = safe_linemerge(unary_union(pulled_saap_geoms))
        elif item.get("ghsa_almeda_montrose_corridor"):
            # 1880-1915 GH&SA / Southern Pacific Line from Stella / Pierce Junction north-northeast alongside Almeda Road
            # past Brays Bayou, Hermann Park & Museum District to Blodgett Junction (Tower 12), then diagonally northwest
            # through First Montrose Commons & Montrose along surveyed HCAD plat seams, the curve of Grant Street, and
            # the 1880 Rosemont Bridge piers across Buffalo Bayou to Chaney Junction
            ln = LineString(
                [
                    (-95.40550, 29.67650),  # Stella Junction on GH&SA Sunset Route (0.2 mi west of Pierce Junction)
                    (-95.39606, 29.67198),  # Pierce Junction (Tower 134) at Almeda Road
                    (-95.39391, 29.67816),  # Alongside Almeda Road (north of Holly Hall)
                    (-95.39132, 29.68551),  # Alongside Almeda Road (south of Old Spanish Trail)
                    (-95.38946, 29.69094),  # Alongside Almeda Road at OST
                    (-95.38815, 29.69592),  # Alongside Almeda Road crossing Brays Bayou
                    (-95.38637, 29.70010),  # Alongside Almeda Road at North MacGregor / Hermann Park SE corner
                    (-95.38482, 29.70556),  # Alongside Almeda Road at Holcombe Blvd (east edge of Hermann Park)
                    (-95.38270, 29.71146),  # Alongside Almeda Road along Hermann Park
                    (-95.38055, 29.71759),  # Alongside Almeda Road at Hermann Dr / Museum District
                    (-95.37916, 29.72164),  # Alongside Almeda Road at Binz / Southmore
                    (-95.37790, 29.72566),  # Entering MacGregor's Blodgett Park right-of-way from Almeda Road
                    (-95.38046, 29.72820),  # Curving northwest toward Blodgett Junction
                    (-95.38260, 29.72960),  # Blodgett Junction (Tower 12 - diamond crossing with SA&AP)
                    (-95.38360, 29.73035),  # MacGregor's Blodgett Park surveyed NW diagonal ROW
                    (-95.38508, 29.73126),  # Fitze Homestead / South End Villa surveyed plat seam
                    (-95.38653, 29.73253),  # Fitze Homestead surveyed diagonal ROW
                    (-95.38794, 29.73457),  # Crossing Richmond Avenue (entering First Montrose Commons / Montrose)
                    (-95.38875, 29.73660),  # 'Tewena' commuter flag stop (between Branard & West Main, east of Jack St)
                    (-95.38958, 29.73832),  # Crossing West Alabama Street (Lockhart, Connor & Barziza plat corner)
                    (-95.39020, 29.74245),  # Crossing Hawthorne Street
                    (-95.39052, 29.74467),  # Entering Grant Street at Westheimer Road
                    (-95.39056, 29.74651),  # Grant Street at Hyde Park Blvd
                    (-95.39085, 29.74703),  # Grant Street surveyed curve
                    (-95.39104, 29.74759),  # Grant Street at Fairview St
                    (-95.39120, 29.74821),  # Grant Street at California / Welch St
                    (-95.39129, 29.74898),  # Grant Street at Indiana / Peden St
                    (-95.39134, 29.75044),  # Grant Street north through Hyde Park
                    (-95.39118, 29.75198),  # North end of Grant Street at Bomar / West Gray
                    (-95.39145, 29.75650),  # Parallel to Montrose Blvd / Crocker through Rosemont Heights
                    (-95.39175, 29.76100),  # Approaching Allen Parkway & Buffalo Bayou
                    (-95.39180, 29.76214),  # South abutment of 1880 GH&SA Railroad Bridge (Rosemont Pedestrian Bridge)
                    (-95.39131, 29.76319),  # North abutment of 1880 GH&SA Railroad Bridge over Buffalo Bayou
                    (-95.39180, 29.76665),  # Along Studemont right-of-way south of Washington Ave
                    (-95.39141, 29.76979),  # Approaching Chaney Junction wye
                    (-95.38860, 29.77120),  # Chaney Junction (Tower 108 at Washington Ave & Studemont/Sawyer)
                ]
            )
        elif item.get("interurban_corridor"):
            # Route Downtown approach from Interurban Terminal at 1006-1008 Texas Ave (at Fannin St) east on Texas Ave,
            # south on Jackson St (across the 2 blocks later closed by Daikin Park), and southeast on Pierce & Sampson
            # Streets directly into the straight GH&H / Interurban right-of-way at Sampson St (-95.34324, 29.74601)
            interurban_parts = [
                LineString(
                    [
                        (-95.36185, 29.75953),  # 1006-1008 Texas Ave (at Fannin St) Interurban Terminal
                        (-95.35651, 29.75632),  # Texas Ave & Jackson St
                        (-95.36484, 29.74586),  # Jackson St & Pierce St
                        (-95.35595, 29.74051),  # Pierce St at Emancipation / Dowling
                        (-95.34988, 29.73759),  # Pierce St & Sampson St
                        (-95.34324, 29.74601),  # Sampson St & GH&H / Interurban private right-of-way
                    ]
                )
            ]
            ghh_rec = grouped_rr.get("subdiv_GALVESTON (UP)")
            if ghh_rec and ghh_rec["geoms"]:
                from shapely.affinity import translate
                ghh_merged = safe_linemerge(unary_union(ghh_rec["geoms"]))
                ghh_shifted = translate(ghh_merged, xoff=-0.00035, yoff=-0.00025)
                # Keep segments from Sampson Street (-95.34324, 29.74601) southeast toward Galveston
                for sub_g in (ghh_shifted.geoms if ghh_shifted.geom_type == "MultiLineString" else [ghh_shifted]):
                    coords = [c for c in sub_g.coords if c[0] >= -95.34330]
                    if len(coords) >= 2:
                        interurban_parts.append(LineString(coords))
            # Bridge the two short street-crossing gaps along the Galveston corridor near Harrisburg
            interurban_parts.append(LineString([(-95.28887, 29.72330), (-95.28865, 29.72294)]))
            interurban_parts.append(LineString([(-95.26290, 29.69225), (-95.26234, 29.69164)]))
            ln = safe_linemerge(unary_union(interurban_parts)) if interurban_parts else None
        elif item.get("osm_trail_names"):
            trail_lns = []
            for tname in item["osm_trail_names"]:
                trail_lns.extend(osm_trails_by_name.get(tname, []))
            ln = safe_linemerge(unary_union(trail_lns)) if trail_lns else None
        elif item.get("osm_streetcar_legs") and streetcar_router is not None:
            ln = streetcar_router(item["osm_streetcar_legs"])
            if item["id"] == "rail_westmoreland_bellaire_streetcar" and ln is not None:
                # Ensure exact vertex continuity with the South End / Main Street line at Main & Holcombe (-95.40485, 29.70643)
                ln = safe_linemerge(
                    unary_union([LineString([(-95.40485, 29.70643), ln.coords[0]]), ln])
                )
        else:
            ln = None

        if ln is None or ln.is_empty:
            continue
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
            "source": "Houston Electric Co. Archival Route Maps (1891–1936), OpenStreetMap Street Centerlines & TxDOT Archive",
        }
        railroad_features.append(
            {
                "type": "Feature",
                "properties": props,
                "geometry": round_coords_geom(ln, tol=0.000012, precision=5),
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
        if p.get("is_minor_trib") and float(p.get("length_miles") or 0) < 0.45:
            continue
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
