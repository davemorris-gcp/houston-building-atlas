"""Restore all multi-building and storage-tank footprints on overridden parcels.

Fixes two root causes of missing refinery / tank farm / campus footprints:
1. Single-polygon override suppression in curated_overrides.json hiding 4,651
   existing shard footprints across 138 multi-footprint parcels (including 921
   of 928 footprints at ExxonMobil Baytown Refinery).
2. Microsoft US Building Footprints (MSBFP2) skipping circular petrochemical
   storage tanks outside the 610 Loop that are mapped in OpenStreetMap
   (man_made=storage_tank / building=storage_tank) and Esri World_Basemap_v2
   VectorTileServer (Layer "Building"), which is the exact source rendered on
   the World_Dark_Gray_Base / World_Light_Gray_Base basemaps.
"""

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import gzip
import json
import math
from pathlib import Path
import urllib.request
import mapbox_vector_tile
from shapely.geometry import Polygon, shape
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parents[2]
OVERRIDES_PATH = ROOT / "app" / "public" / "data" / "curated_overrides.json"
SHARD_DIR = ROOT / "pipeline" / "cache" / "aligned_ndjson"
OSM_TANKS_PATH = ROOT / "pipeline" / "cache" / "osm_harris_county_tanks.json"
ESRI_BLD_CACHE_PATH = ROOT / "pipeline" / "cache" / "esri_industrial_buildings.json"

INDUSTRIAL_KEYWORDS = (
    "refin",
    "exxon",
    "shell",
    "valero",
    "chevron",
    "lyondell",
    "equistar",
    "kinder",
    "vopak",
    "oiltanking",
    "enterprise",
    "rohm",
    "chemical",
    "terminal",
    "petro",
    "port of houston",
    "stolt",
    "intercontinental",
    "magellan",
    "tank",
    "plant",
    "maritime",
    "turning basin",
    "barbours cut",
    "bayport",
    "anheuser",
)


def deg2num(lat_deg: float, lon_deg: float, zoom: int):
  lat_rad = math.radians(lat_deg)
  n = 2.0**zoom
  xtile = int((lon_deg + 180.0) / 360.0 * n)
  ytile = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
  return (xtile, ytile)


def mvt_coord_to_lon_lat(px, py, xtile, ytile, zoom, extent=4096):
  n = 2.0**zoom
  lon = (xtile + px / extent) / n * 360.0 - 180.0
  lat_rad = math.atan(
      math.sinh(math.pi * (1.0 - 2.0 * (ytile + (extent - py) / extent) / n))
  )
  lat = math.degrees(lat_rad)
  return (round(lon, 5), round(lat, 5))


def round_ring(ring):
  return [[round(float(pt[0]), 5), round(float(pt[1]), 5)] for pt in ring]


def poly_to_coords(poly: Polygon):
  ext = round_ring(poly.exterior.coords)
  holes = [round_ring(inter.coords) for inter in poly.interiors]
  return [ext] + holes


REFINERY_TERMINAL_KEYWORDS = (
    "refin",
    "exxon",
    "shell",
    "valero",
    "chevron",
    "lyondell",
    "equistar",
    "kinder",
    "vopak",
    "oiltanking",
    "enterprise",
    "rohm",
    "chemical",
    "ship channel terminal",
    "galena park terminal",
    "petro",
    "port of houston",
    "stolt",
    "intercontinental",
    "magellan",
    "tank",
    "turning basin",
    "barbours cut",
    "bayport",
    "eco services",
    "stauffer",
)


def is_industrial_override(ov_entry: dict, shard_count: int = 0) -> bool:
  lm = (ov_entry.get("landmark_name") or "").lower()
  if "air terminal" in lm or "airport" in lm:
    return False
  if any(w in lm for w in REFINERY_TERMINAL_KEYWORDS):
    return True
  cat = (ov_entry.get("use_category") or "").lower()
  return "industrial" in cat and shard_count >= 12


def fetch_esri_building_tile(tile_xy):
  x, y = tile_xy
  z = 15
  url = f"https://basemaps.arcgis.com/arcgis/rest/services/World_Basemap_v2/VectorTileServer/tile/{z}/{y}/{x}.pbf"
  rings = []
  try:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
      data = resp.read()
    if data[:2] == b"\x1f\x8b":
      data = gzip.decompress(data)
    dec = mapbox_vector_tile.decode(data)
    bld = dec.get("Building", {})
    ext = bld.get("extent", 4096)
    for feat in bld.get("features", []):
      g = feat.get("geometry", {})
      gtype = g.get("type")
      coords = g.get("coordinates", [])
      if gtype == "Polygon" and coords:
        ring = [
            mvt_coord_to_lon_lat(pt[0], pt[1], x, y, z, ext) for pt in coords[0]
        ]
        if len(ring) >= 4:
          rings.append(ring)
      elif gtype == "MultiPolygon" and coords:
        for sub in coords:
          if sub:
            ring = [
                mvt_coord_to_lon_lat(pt[0], pt[1], x, y, z, ext)
                for pt in sub[0]
            ]
            if len(ring) >= 4:
              rings.append(ring)
  except Exception:
    pass
  return rings


def main():
  with open(OVERRIDES_PATH, "r", encoding="utf-8") as f:
    raw = json.load(f)
  ov_map = raw.get("overrides", raw)

  parcels_with_composite_children = set()
  for k in ov_map:
    if "#" in k:
      parcels_with_composite_children.add(k.split("#")[0])

  parcel_keys = {
      k
      for k, v in ov_map.items()
      if isinstance(v, dict)
      and "#" not in k
      and not v.get("is_building_override")
      and k not in parcels_with_composite_children
  }

  shard_polys = defaultdict(list)
  for shard_name in ["nw_w", "nw_e", "ne", "sw", "se"]:
    shard_file = SHARD_DIR / f"buildings_{shard_name}.geojsonseq"
    if not shard_file.exists():
      continue
    with open(shard_file, "r", encoding="utf-8") as f:
      for line in f:
        line = line.strip().lstrip("\x1e")
        if not line:
          continue
        feat = json.loads(line)
        hcad = str(feat.get("properties", {}).get("hcad_num", "")).strip()
        if hcad in parcel_keys:
          geom = feat.get("geometry")
          if geom and geom.get("type") == "Polygon":
            try:
              p = shape(geom)
              if not p.is_valid:
                p = p.buffer(0)
              if not p.is_empty and p.geom_type == "Polygon":
                shard_polys[hcad].append(p)
            except Exception:
              pass

  multi_hcads = {h: polys for h, polys in shard_polys.items() if len(polys) > 1}
  print(
      f"Collected {sum(len(v) for v in multi_hcads.values())} shard footprints "
      f"across {len(multi_hcads)} multi-footprint parcels in curated_overrides.json."
  )

  # Determine Zoom 15 tiles covering all industrial/refinery/terminal overrides
  needed_tiles = set()
  for h, polys in shard_polys.items():
    if not polys or not is_industrial_override(ov_map.get(h, {}), len(polys)):
      continue
    minx = min(p.bounds[0] for p in polys) - 0.0045
    miny = min(p.bounds[1] for p in polys) - 0.0045
    maxx = max(p.bounds[2] for p in polys) + 0.0045
    maxy = max(p.bounds[3] for p in polys) + 0.0045
    x0, y1 = deg2num(miny, minx, 15)
    x1, y0 = deg2num(maxy, maxx, 15)
    for tx in range(min(x0, x1), max(x0, x1) + 1):
      for ty in range(min(y0, y1), max(y0, y1) + 1):
        needed_tiles.add((tx, ty))

  if ESRI_BLD_CACHE_PATH.exists():
    with open(ESRI_BLD_CACHE_PATH, "r", encoding="utf-8") as f:
      esri_rings = json.load(f)
    print(f"Loaded {len(esri_rings)} cached Esri World_Basemap_v2 Building rings.")
  else:
    print(f"Fetching {len(needed_tiles)} Zoom-15 Esri World_Basemap_v2 vector tiles...")
    esri_rings = []
    with ThreadPoolExecutor(max_workers=12) as ex:
      for tile_rings in ex.map(fetch_esri_building_tile, sorted(needed_tiles)):
        esri_rings.extend(tile_rings)
    with open(ESRI_BLD_CACHE_PATH, "w", encoding="utf-8") as f:
      json.dump(esri_rings, f)
    print(f"Fetched & cached {len(esri_rings)} Esri World_Basemap_v2 Building rings.")

  # Load OpenStreetMap + Esri storage tank / industrial polygons
  candidate_tanks = []
  if OSM_TANKS_PATH.exists():
    with open(OSM_TANKS_PATH, "r", encoding="utf-8") as f:
      osm_res = json.load(f)
    nodes = {
        el["id"]: (el["lon"], el["lat"])
        for el in osm_res.get("elements", [])
        if el.get("type") == "node"
    }
    ways = [
        el for el in osm_res.get("elements", []) if el.get("type") == "way"
    ]
    for w in ways:
      nds = w.get("nodes", [])
      if len(nds) < 4 or nds[0] != nds[-1]:
        continue
      coords = [nodes[nid] for nid in nds if nid in nodes]
      if len(coords) < 4:
        continue
      try:
        p = Polygon(coords)
        if not p.is_valid:
          p = p.buffer(0)
        if p.is_empty or p.geom_type != "Polygon" or p.area > 0.000006:
          continue
        candidate_tanks.append((p, len(coords) >= 8))
      except Exception:
        pass

  for ring in esri_rings:
    if len(ring) < 7:
      continue
    try:
      p = Polygon(ring)
      if not p.is_valid:
        p = p.buffer(0)
      if p.is_empty or p.geom_type != "Polygon" or p.area > 0.000006:
        continue
      candidate_tanks.append((p, True))
    except Exception:
      pass

  print(f"Total candidate OSM + Esri basemap tank/industrial polygons: {len(candidate_tanks)}")

  # Build spatial index of ALL shard footprints across all 5 shards so we never duplicate
  # a building that already exists on a residential/commercial parcel next to a refinery!
  all_county_industrial_geoms = []
  all_county_industrial_hcads = []
  for h, polys in shard_polys.items():
    if is_industrial_override(ov_map.get(h, {}), len(polys)):
      for p in polys:
        all_county_industrial_geoms.append(p)
        all_county_industrial_hcads.append(h)

  ind_tree = STRtree(all_county_industrial_geoms)

  # Also index non-overridden shard footprints near those industrial parcels to avoid duplicating them
  ind_bounds = [p.bounds for p in all_county_industrial_geoms]
  min_ix = min(b[0] for b in ind_bounds) - 0.005
  min_iy = min(b[1] for b in ind_bounds) - 0.005
  max_ix = max(b[2] for b in ind_bounds) + 0.005
  max_iy = max(b[3] for b in ind_bounds) + 0.005

  nearby_other_shard_geoms = []
  for shard_name in ["ne", "se", "sw", "nw_e"]:
    shard_file = SHARD_DIR / f"buildings_{shard_name}.geojsonseq"
    if not shard_file.exists():
      continue
    with open(shard_file, "r", encoding="utf-8") as f:
      for line in f:
        line = line.strip().lstrip("\x1e")
        if not line:
          continue
        feat = json.loads(line)
        ring = feat.get("geometry", {}).get("coordinates", [[]])[0]
        if not ring:
          continue
        lon, lat = ring[0]
        if min_ix <= lon <= max_ix and min_iy <= lat <= max_iy:
          hcad = str(feat.get("properties", {}).get("hcad_num", "")).strip()
          if hcad not in parcel_keys:
            try:
              p = shape(feat["geometry"])
              if p.is_valid and not p.is_empty:
                nearby_other_shard_geoms.append(p)
            except Exception:
              pass

  other_tree = STRtree(nearby_other_shard_geoms)
  added_by_hcad = defaultdict(list)

  for tank, is_circular in candidate_tanks:
    nearest_idx = ind_tree.nearest(tank)
    if nearest_idx is None:
      continue
    dist = tank.distance(all_county_industrial_geoms[nearest_idx])
    max_dist = 0.0090 if is_circular else 0.0028
    if dist > max_dist:
      continue

    # Must not overlap any existing industrial override footprint
    overlaps = False
    for idx in ind_tree.query(tank):
      existing = all_county_industrial_geoms[idx]
      if tank.intersects(existing):
        if tank.intersection(existing).area / min(tank.area, existing.area) > 0.15:
          overlaps = True
          break
    if overlaps:
      continue

    # For rectangular buildings (not circular storage tanks), must not overlap any non-overridden shard footprint
    if not is_circular:
      for idx in other_tree.query(tank):
        existing = nearby_other_shard_geoms[idx]
        if tank.intersects(existing):
          if tank.intersection(existing).area / min(tank.area, existing.area) > 0.15:
            overlaps = True
            break
      if overlaps:
        continue

    h = all_county_industrial_hcads[nearest_idx]
    for prev in added_by_hcad[h]:
      if tank.intersects(prev):
        if tank.intersection(prev).area / min(tank.area, prev.area) > 0.15:
          overlaps = True
          break
    if not overlaps:
      added_by_hcad[h].append(tank)

  print(
      f"Matched +{sum(len(v) for v in added_by_hcad.values())} missing OSM + Esri "
      f"storage tank / refinery footprints across {len(added_by_hcad)} parcels."
  )

  updated_parcels = 0
  total_footprints_now = 0
  for h in parcel_keys:
    base_polys = shard_polys.get(h, [])
    extra_tanks = added_by_hcad.get(h, [])
    combined = base_polys + extra_tanks
    if "keep_shard_footprints" in ov_map[h]:
      del ov_map[h]["keep_shard_footprints"]

    if len(combined) > 1:
      combined.sort(key=lambda g: g.area, reverse=True)
      ov_map[h]["geometry"] = {
          "type": "MultiPolygon",
          "coordinates": [poly_to_coords(p) for p in combined],
      }
      ov_map[h]["footprint_count"] = len(combined)
      updated_parcels += 1
      total_footprints_now += len(combined)
    elif len(combined) == 1:
      ov_map[h]["geometry"] = {
          "type": "Polygon",
          "coordinates": poly_to_coords(combined[0]),
      }
      ov_map[h]["footprint_count"] = 1

  with open(OVERRIDES_PATH, "w", encoding="utf-8") as f:
    json.dump(raw, f, separators=(",", ":"))

  size_kb = OVERRIDES_PATH.stat().st_size / 1024
  print(
      f"Updated {updated_parcels} multi-footprint parcels in curated_overrides.json "
      f"with {total_footprints_now} total building & storage tank polygons "
      f"(file size: {size_kb:.1f} KB)."
  )

  ranked = sorted(
      [
          (h, ov_map[h].get("footprint_count", 1), len(added_by_hcad.get(h, [])))
          for h in parcel_keys
          if ov_map[h].get("footprint_count", 1) > 1
      ],
      key=lambda x: -x[1],
  )
  print("\nTop 20 multi-footprint industrial/refinery/campus parcels now restored:")
  for h, cnt, osm_cnt in ranked[:20]:
    lm = ov_map[h].get("landmark_name", "")
    yr = ov_map[h].get("year_built", "")
    print(f"  {h} | yr={yr} | total={cnt:4d} (+{osm_cnt:3d} tanks) | {lm}")


if __name__ == "__main__":
  main()
