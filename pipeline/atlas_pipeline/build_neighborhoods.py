"""Build Multi-Tier Neighborhoods, Super Neighborhoods, Historic Wards & HCAD Subdivisions for The Houston Building Atlas.

Combines 6 authoritative geocoded sources:
  1. 2009 Peter Brown / Tony Topping 'The Neighborhoods of Houston' GeoPDF (661 vectorized polygons)
  2. City of Houston Neighborhoods 2021 (597 Macro / Vernacular / Sub-Neighborhood polygons)
  3. City of Houston Patrol Region 3 Neighborhoods (237 CAD polygons with multi-name NAME_1 / NAME_2 subdivision lists)
  4. City of Houston Civic Clubs (470 HOA / Civic Association polygons) + Cultural & Conservation Districts
  5. City of Houston Super Neighborhoods (88 official macro planning polygons)
  6. City of Houston Historical Ward Boundaries across 5 charter eras (1839, 1866, 1896, 1903, 1920)
  7. HCAD Real_acct_owner.zip (real_acct.txt lgl_2 platted subdivisions + real_neighborhood_code.txt)
"""

from __future__ import annotations

from collections import Counter, defaultdict
import glob
import json
import os
import re
import zipfile
import numpy as np
from shapely import STRtree, points
from shapely.geometry import MultiPolygon, Point, Polygon, mapping, shape
from shapely.ops import unary_union
from shapely.validation import make_valid


def round_coords(geom_dict: dict, decimals: int = 5) -> dict:
  def _rc(c):
    if isinstance(c, (int, float)):
      return round(float(c), decimals)
    return [_rc(x) for x in c]

  return {'type': geom_dict['type'], 'coordinates': _rc(geom_dict['coordinates'])}


CACHE_DIR = '/usr/local/google/home/davemorris/houston-building-atlas/pipeline/cache'
DATA_DIR = '/usr/local/google/home/davemorris/houston-building-atlas/app/public/data'

# Curated historical, colloquial, and constituent subdivision aliases for Houston neighborhoods
CURATED_NEIGHBORHOOD_ALIASES: dict[str, list[str]] = {
    'MONTROSE': [
        'Neartown',
        'The Heart of Houston',
        'Hyde Park',
        'Westmoreland',
        'Avondale',
        'Courtlandt Place',
        'Audubon Place',
        'Cherryhurst',
        'Winlow Place',
        'Mandell Place',
        'Lancaster Place',
        'Richmond Place',
        'Rossmoyne',
        'First Montrose Commons',
        'Vermont Commons',
    ],
    'NEARTOWN - MONTROSE': [
        'Montrose',
        'Neartown',
        'Hyde Park',
        'Westmoreland',
        'Avondale',
        'Courtlandt Place',
        'Audubon Place',
        'Cherryhurst',
        'Winlow Place',
        'Mandell Place',
    ],
    'EAST DOWNTOWN': [
        'EaDo',
        'Old Chinatown',
        'East End Chinatown',
        'St. Emanuel Corridor',
        'Warehouse District South',
    ],
    'EAST DOWNTOWN HOUSTON': [
        'EaDo',
        'East Downtown',
        'Old Chinatown',
        'St. Emanuel Corridor',
    ],
    'DOWNTOWN': [
        'Downtown Houston',
        '1836 Original Townsite',
        'Market Square',
        'Main Street Corridor',
        'Theater District',
        'Skyline District',
        'Warehouse District',
    ],
    'DOWNTOWN HOUSTON': [
        'Downtown',
        '1836 Original Townsite',
        'Market Square',
        'Theater District',
        'Skyline District',
    ],
    'FOURTH WARD': [
        "Freedmen's Town",
        'Historic Fourth Ward',
        'San Felipe District',
        'Allen Parkway Village Area',
    ],
    'OLD SIXTH WARD': [
        'Sixth Ward',
        'Historic Sixth Ward',
        'Vinegar Hill',
        'Sabine District',
    ],
    'SIXTH WARD': [
        'Old Sixth Ward',
        'Historic Sixth Ward',
        'Vinegar Hill',
    ],
    'FIRST WARD': [
        'High First Ward',
        'First Ward Arts District',
        'Sawyer Yards',
        'Washington Avenue East',
    ],
    'SECOND WARD': [
        'Segundo Barrio',
        'Frost Town',
        "Schrimpf's Field",
        'East End',
        'Navigation Esplanade',
    ],
    'THIRD WARD': [
        'Greater Third Ward',
        'Emancipation Corridor',
        'Dowling Street Corridor',
        'The Tre',
        'Cuney Homes',
        'Project Row Houses Area',
    ],
    'GREATER THIRD WARD': [
        'Third Ward',
        'Emancipation Corridor',
        'The Tre',
        'University Oaks',
    ],
    'FIFTH WARD': [
        'Greater Fifth Ward',
        'The Nickel',
        'Lyons Avenue Corridor',
        'Frenchtown',
        'St. Elizabeth Place',
    ],
    'GREATER FIFTH WARD': [
        'Fifth Ward',
        'The Nickel',
        'Lyons Avenue Corridor',
        'Frenchtown',
    ],
    'HOUSTON HEIGHTS': [
        'The Heights',
        'Greater Heights',
        'Heights East',
        'Heights West',
        'Heights South',
        'Brunner',
        'Heights Boulevard',
    ],
    'GREATER HEIGHTS': [
        'The Heights',
        'Houston Heights',
        'Woodland Heights',
        'Norhill',
        'Brookesmith',
        'Sunset Heights',
        'Shady Acres',
        'Timbergrove',
    ],
    'WOODLAND HEIGHTS': [
        'Germantown',
        'Watson Addition',
        'Bayland Park Area',
        'White Oak Bayou Corridor',
    ],
    'NORHILL': [
        'Norhill Historic District',
        'North Norhill',
        'East Norhill',
        'West Norhill',
        'Proctor Plaza',
    ],
    'NEAR NORTHSIDE': [
        'Northside Village',
        'Fifth Ward North',
        'Quitman Corridor',
        'Ryon',
        'Irvington',
        'Lindale Park',
    ],
    'NORTHSIDE VILLAGE': [
        'Near Northside',
        'Ryon',
        'Irvington',
        'Lindale Park',
        'Germantown',
    ],
    'RIVERSIDE TERRACE': [
        'Riverside',
        'MacGregor / Brays Bayou',
        'Historical Jewish Riverside',
        'South MacGregor',
    ],
    'MUSEUM DISTRICT': [
        'The Museum District',
        'Museum Park',
        'Binz',
        'Hermann Park Area',
        'South End',
        'Caroline Boulevard District',
    ],
    'THE MUSEUM DISTRICT': [
        'Museum District',
        'Museum Park',
        'Binz',
        'Hermann Park Area',
    ],
    'BOULEVARD OAKS': [
        'Broadacres',
        'Vassar Place',
        'Ormond Place',
        'Chevy Chase',
        'Edgemont',
        'Cherokee',
        'North Boulevard / South Boulevard',
    ],
    'RIVER OAKS': [
        'River Oaks Country Club Estates',
        'Tall Timbers',
        'Homewoods',
        'Country Club Estates',
    ],
    'EASTWOOD': [
        'Greater Eastwood',
        'Country Club Place',
        'Broadmoor',
        'Lawndale / Boheme',
    ],
    'MAGNOLIA PARK': [
        'Original Town of Magnolia Park',
        'Central Park',
        'Hidalgo Park',
        '75th Street / Canal Corridor',
    ],
    'GLENBROOK VALLEY': [
        'Glenbrook Valley Historic District',
        'Hobby Mid-Century Modern District',
    ],
    'IDYLWOOD': [
        'Idylwood Civic Club',
        'Spurlock Addition',
        'Villa de Matel / Forest Park Area',
    ],
    'INDEPENDENCE HEIGHTS': [
        'Historic Independence Heights',
        'First African American Municipality in Texas (1915)',
    ],
    'ACRES HOMES': [
        'Acres Home',
        '44 Acres',
        'Highland Acre Homes',
    ],
    'SHARPSTOWN': [
        'Mahatma Gandhi District',
        'Southwest Chinatown',
        'Asiatown',
        'Bellaire Boulevard Corridor',
        'Sharpstown Country Club Estates',
    ],
    'MEYERLAND': [
        'Meyerland Area',
        'Marilyn Estates',
        'Barkley Square',
        'Braesmont',
    ],
    'UPTOWN': [
        'The Galleria',
        'Greater Uptown',
        'Post Oak Corridor',
        'Uptown-Galleria',
    ],
    'UNIVERSITY PLACE': [
        'Rice Village',
        'Southgate',
        'Southampton',
        'Boulevard Oaks',
        'Sunset Terrace',
        'Montclair',
    ],
    'RICE MILITARY': [
        'Washington Corridor',
        'Camp Logan',
        'Memorial Park North',
        'Brunner',
    ],
    'GARDEN OAKS': [
        'Garden Oaks Sec 1-5',
        'Pinemont / Shepherd North',
    ],
    'OAK FOREST': [
        'Oak Forest Sec 1-18',
        'Candlelight Estates',
    ],
    'LAZYBROOK': [
        'Lazy Brook',
        'Lazybrook / Timbergrove',
    ],
    'TIMBERGROVE MANOR': [
        'Timbergrove',
        'Lazybrook / Timbergrove',
    ],
}

WARD_METADATA = {
    'FIRST': {
        'name': 'First Ward',
        'color': '#38BDF8',
        'alt_names': ['High First Ward', 'First Ward Arts District', 'Sawyer Yards', 'Germantown'],
        'description': 'Northwest quadrant of original 1839-1920 Houston, north of Congress Ave / Buffalo Bayou and west of Main St.',
    },
    'SECOND': {
        'name': 'Second Ward',
        'color': '#F59E0B',
        'alt_names': ['Segundo Barrio', 'Frost Town', "Schrimpf's Field", 'East End'],
        'description': 'Northeast quadrant of original 1839-1920 Houston, north of Congress Ave and east of Main St to Buffalo Bayou.',
    },
    'THIRD': {
        'name': 'Third Ward',
        'color': '#EC4899',
        'alt_names': ['Greater Third Ward', 'Emancipation Corridor', 'Dowling Street Corridor', 'The Tre'],
        'description': 'Southeast quadrant of original 1839-1920 Houston, south of Congress Ave and east of Main St.',
    },
    'FOURTH': {
        'name': 'Fourth Ward',
        'color': '#34D399',
        'alt_names': ["Freedmen's Town", 'Historic Fourth Ward', 'San Felipe District', 'South End'],
        'description': 'Southwest quadrant of original 1839-1920 Houston, south of Congress Ave / Buffalo Bayou and west of Main St.',
    },
    'FIFTH': {
        'name': 'Fifth Ward',
        'color': '#A855F7',
        'alt_names': ['Greater Fifth Ward', 'The Nickel', 'Lyons Avenue Corridor', 'Frenchtown'],
        'description': 'Established in 1866 northeast of Buffalo Bayou and east of White Oak Bayou.',
    },
    'SIXTH': {
        'name': 'Sixth Ward',
        'color': '#FB923C',
        'alt_names': ['Old Sixth Ward', 'Historic Sixth Ward', 'Vinegar Hill'],
        'description': 'Established in 1876 north of Buffalo Bayou and west of White Oak Bayou (carved from the original Fourth Ward).',
    },
}


def clean_title(s: str) -> str:
  if not s:
    return ''
  s = ' '.join(s.replace('_', ' ').split())
  # Preserve Roman numerals or acronyms
  words = []
  for w in s.split(' '):
    wu = w.upper().strip('.,')
    if wu in ('I', 'II', 'III', 'IV', 'V', 'VI', 'NW', 'NE', 'SW', 'SE', 'OST', 'TMC', 'U', 'OF', 'H', 'RO', 'R.O.'):
      words.append(wu if wu != 'OF' else 'of')
    elif wu.startswith("MC") and len(wu) > 2:
      words.append('Mc' + wu[2:].capitalize())
    else:
      words.append(w.capitalize())
  res = ' '.join(words)
  res = re.sub(r"\bFreedmen'S\b", "Freedmen's", res)
  res = re.sub(r"\bHunter'S\b", "Hunter's", res)
  res = re.sub(r'\bUniv\.\s+of\s+Houston\b', 'University of Houston', res, flags=re.I)
  res = re.sub(r'\bR\.O\.\s+Center\b', 'River Oaks Center', res, flags=re.I)
  res = re.sub(r'\bCherry-Hurst\b', 'Cherryhurst', res, flags=re.I)
  return res


def clean_civic_club_name(raw_name: str) -> str:
  s = raw_name.strip()
  # Strip civic club / HOA suffixes
  s = re.sub(
      r'\b(CIVIC\s+CLUB|CIVIC\s+ASSOCIATION|CIVIC\s+ASSN\.?|HOMEOWNERS\s+ASSOCIATION|HOME\s+OWNERS\s+ASSOCIATION|HOMEOWNERS\s+ASSN\.?|PROPERTY\s+OWNERS\s+ASSOCIATION|PROPERTY\s+OWNERS\s+ASSN\.?|COMMUNITY\s+ASSOCIATION|COMMUNITY\s+IMPROVEMENT\s+ASSN\.?|IMPROVEMENT\s+ASSOCIATION|WOMENS\s+CLUB|OWNERS\s+ASSN\.?|ASSN\.?|INC\.?)\b.*$',
      '',
      s,
      flags=re.I,
  )
  s = re.sub(r'\b(SECTIONS?\s+[0-9IVX]+.*|SEC\.?\s+[0-9IVX]+.*)$', '', s, flags=re.I)
  s = s.strip(' ,.-/()')
  return clean_title(s)


def normalize_hcad_subdivision(lgl_2: str) -> str:
  if not lgl_2:
    return ''
  s = lgl_2.strip().upper()
  # Ignore pure acreage, metes-and-bounds, or condo interest lines
  if any(
      s.startswith(p)
      for p in ('TR ', 'TRS ', 'ABST ', 'A-', 'lt ', 'LTS ', 'BLK ', '.0', '.1', '.2', '.3', '0.', 'ALL OF', 'UND ', 'INT ')
  ):
    return ''
  # Strip section, replat, amendment, and U/R suffixes
  s = re.sub(r'\b(SEC|SECTION|PT|PART|PARTIAL|REPLAT|R/P|AMEND|AMENDED|AMD|U/R|UR|EXT|EXTENSION|PH|PHASE|ADDN|ADDITION|SUBD|SUBDIVISION|T/H|CONDO|CONDOMINIUM|BLDG|BLK|LT|LTS)\b.*$', '', s)
  s = re.sub(r'\s+#?\d+[A-Z]?\s*$', '', s)
  s = s.strip(' ,.-/#&()')
  if len(s) < 3 or s.isdigit():
    return ''
  return clean_title(s)


def load_hcad_subdivisions() -> dict[str, str]:
  """Load HCAD account -> normalized platted subdivision name from Real_acct_owner.zip."""
  zip_path = os.path.join(CACHE_DIR, 'Real_acct_owner.zip')
  hcad_to_sub: dict[str, str] = {}
  if not os.path.exists(zip_path):
    return hcad_to_sub
  with zipfile.ZipFile(zip_path) as zf:
    with zf.open('real_acct.txt') as f:
      _ = f.readline()
      for line in f:
        parts = line.decode('latin1', errors='replace').rstrip('\r\n').split('\t')
        if len(parts) > 67:
          acct = parts[0].strip()
          lgl2 = parts[67].strip()
          if acct and lgl2:
            sub = normalize_hcad_subdivision(lgl2)
            if sub:
              hcad_to_sub[acct] = sub
  print(f'Loaded {len(hcad_to_sub):,} normalized HCAD platted subdivision names from real_acct.txt')
  return hcad_to_sub


def build_historic_wards() -> list[dict]:
  era_files = [
      (1839, 'coh_wards_1839.geojson'),
      (1866, 'coh_wards_1866.geojson'),
      (1896, 'coh_wards_1896.geojson'),
      (1903, 'coh_wards_1903.geojson'),
      (1920, 'coh_wards_1920.geojson'),
  ]
  ward_features = []
  for era, fname in era_files:
    fpath = os.path.join(CACHE_DIR, fname)
    if not os.path.exists(fpath):
      continue
    with open(fpath) as f:
      fc = json.load(f)
    for feat in fc.get('features', []):
      if not feat.get('geometry'):
        continue
      props = feat.get('properties') or {}
      raw_ward = str(props.get('WARD') or '').strip().upper()
      if raw_ward not in WARD_METADATA:
        continue
      meta = WARD_METADATA[raw_ward]
      geom = make_valid(shape(feat['geometry'])).simplify(0.00008, preserve_topology=True)
      if geom.is_empty:
        continue
      rep = geom.representative_point()
      slug = raw_ward.lower()
      ward_features.append({
          'type': 'Feature',
          'properties': {
              'id': f'ward_{era}_{slug}',
              'name': meta['name'],
              'ward_key': raw_ward,
              'era': era,
              'display_title': f"{meta['name']} ({era} Boundary)",
              'alt_names': meta['alt_names'],
              'color': meta['color'],
              'description': meta['description'],
              'label_lng': round(rep.x, 5),
              'label_lat': round(rep.y, 5),
          },
          'geometry': round_coords(mapping(geom), 5),
      })
  print(f'Built {len(ward_features)} Historic Ward features across 5 eras (1839-1920)')
  return ward_features


def build_super_neighborhoods() -> list[dict]:
  fpath = os.path.join(CACHE_DIR, 'coh_super_neighborhoods.geojson')
  with open(fpath) as f:
    fc = json.load(f)
  sn_features = []
  for feat in fc.get('features', []):
    if not feat.get('geometry'):
      continue
    props = feat.get('properties') or {}
    snbr_id = int(props.get('POLYID') or 0)
    raw_name = str(props.get('SNBNAME') or '').strip()
    if not raw_name:
      continue
    name = clean_title(raw_name)
    geom = make_valid(shape(feat['geometry'])).simplify(0.00018, preserve_topology=True)
    if geom.is_empty:
      continue
    rep = geom.representative_point()
    aliases = list(CURATED_NEIGHBORHOOD_ALIASES.get(raw_name.upper(), []))
    sn_features.append({
        'type': 'Feature',
        'properties': {
            'id': f'snbr_{snbr_id}',
            'snbr_id': snbr_id,
            'name': name,
            'raw_name': raw_name,
            'alt_names': aliases,
            'info_url': props.get('SnbrInfoUR') or f'https://www.houstontx.gov/superneighborhoods/{snbr_id}.html',
            'label_lng': round(rep.x, 5),
            'label_lat': round(rep.y, 5),
        },
        'geometry': round_coords(mapping(geom), 5),
    })
  sn_features.sort(key=lambda x: x['properties']['snbr_id'])
  print(f'Built {len(sn_features)} COH Super Neighborhood features')
  return sn_features


def norm_key(name: str) -> str:
  s = name.upper().strip()
  s = re.sub(r'\b(HISTORIC\s+DISTRICT|HISTORIC\s+PLACE|NEIGHBORHOOD|VILLAGE|ESTATES|ADDITION|PLACE|AREA|HOUSTON)\b', '', s)
  s = re.sub(r'[^A-Z0-9]+', '', s)
  return s


def build_vernacular_neighborhoods(
    super_nbhds: list[dict], wards_1920: list[dict]
) -> list[dict]:
  """Synthesize vernacular neighborhoods from COH Patrol3, COH 2021, 2009 Peter Brown GeoPDF, and COH Civic Clubs."""
  candidates = []

  # 1. COH Patrol Region 3 Neighborhoods (exact CAD street centerlines + multi-alias NAME_1/NAME_2)
  with open(os.path.join(CACHE_DIR, 'coh_patrol3_neighborhoods.geojson')) as f:
    fc_p3 = json.load(f)
  # Dissolve split parts by CODE_NUM / NAME_LABEL
  p3_groups: dict[str, dict] = {}
  for feat in fc_p3.get('features', []):
    if not feat.get('geometry'):
      continue
    p = feat['properties']
    raw_lbl = str(p.get('NAME_LABEL') or '').strip()
    if not raw_lbl or 'NONE' in raw_lbl or 'OOJ' in raw_lbl or 'ELEMENTARY' in raw_lbl:
      continue
    geom = make_valid(shape(feat['geometry']))
    if geom.is_empty:
      continue
    aliases = set()
    for nf in ('NAME_1', 'NAME_2'):
      val = str(p.get(nf) or '').strip()
      for part in re.split(r'[;,]', val):
        part_c = clean_title(part.strip())
        if part_c and len(part_c) >= 3 and part_c.upper() != raw_lbl.upper() and 'ANX' not in part_c.upper() and 'MUD' not in part_c.upper():
          aliases.add(part_c)
    if raw_lbl not in p3_groups:
      p3_groups[raw_lbl] = {'geoms': [geom], 'aliases': aliases}
    else:
      p3_groups[raw_lbl]['geoms'].append(geom)
      p3_groups[raw_lbl]['aliases'].update(aliases)

  for raw_lbl, info in p3_groups.items():
    u_geom = make_valid(unary_union(info['geoms'])).simplify(0.0001, preserve_topology=True)
    if u_geom.is_empty or u_geom.area < 1e-7:
      continue
    candidates.append({
        'name': clean_title(raw_lbl),
        'raw_name': raw_lbl.upper(),
        'geom': u_geom,
        'alt_names': sorted(info['aliases']),
        'sources': ['COH Neighborhoods GIS (Patrol Region 3)'],
        'priority': 1,
        'tier': 'Neighborhood',
    })

  # 2. COH Neighborhoods 2021 (Neighborhood & Sub Neighborhood within Harris County bbox)
  with open(os.path.join(CACHE_DIR, 'coh_neighborhoods_2021.geojson')) as f:
    fc_21 = json.load(f)
  for feat in fc_21.get('features', []):
    if not feat.get('geometry'):
      continue
    p = feat['properties']
    subtyp = str(p.get('OBJ_SUBTYP') or '')
    if subtyp == 'Macro Neighborhood':
      continue
    raw_name = str(p.get('OBJ_NAME') or '').strip()
    if not raw_name:
      continue
    # Clean "Historic District" suffix on sub-neighborhoods when appropriate, but keep in alt_names
    base_name = re.sub(r'\s+Historic\s+(District|Place)$', '', raw_name, flags=re.I).strip()
    geom = make_valid(shape(feat['geometry'])).simplify(0.0001, preserve_topology=True)
    if geom.is_empty:
      continue
    minx, miny, maxx, maxy = geom.bounds
    if maxy < 29.48 or miny > 30.18 or maxx < -95.95 or minx > -94.90:
      continue
    aliases = []
    if base_name != raw_name:
      aliases.append(raw_name)
    candidates.append({
        'name': clean_title(base_name),
        'raw_name': base_name.upper(),
        'geom': geom,
        'alt_names': aliases,
        'sources': ['COH Neighborhoods (2021)'],
        'priority': 2 if subtyp == 'Sub Neighborhood' else 3,
        'tier': subtyp,
    })

  # 3. 2009 Peter Brown / Tony Topping GeoPDF (661 vectorized polygons)
  with open(os.path.join(CACHE_DIR, 'peter_brown_2009_neighborhoods.geojson')) as f:
    fc_pb = json.load(f)
  for feat in fc_pb.get('features', []):
    if not feat.get('geometry'):
      continue
    p = feat['properties']
    raw_name = str(p.get('name') or '').strip()
    if not raw_name:
      continue
    geom = make_valid(shape(feat['geometry'])).simplify(0.00012, preserve_topology=True)
    if geom.is_empty or geom.area < 1e-7:
      continue
    candidates.append({
        'name': clean_title(raw_name),
        'raw_name': raw_name.upper(),
        'geom': geom,
        'alt_names': [],
        'sources': ['2009 Peter Brown / Tony Topping Map'],
        'priority': 2,
        'tier': 'Kingwood Village' if p.get('viewport') == 'kingwood' else 'Neighborhood',
    })

  # 4. COH Civic Clubs & Conservation/Cultural Districts
  with open(os.path.join(CACHE_DIR, 'coh_civic_clubs.geojson')) as f:
    fc_cc = json.load(f)
  cc_groups: dict[str, dict] = {}
  for feat in fc_cc.get('features', []):
    if not feat.get('geometry'):
      continue
    p = feat['properties']
    raw_cc = str(p.get('CivicName') or '').strip()
    if not raw_cc:
      continue
    cleaned = clean_civic_club_name(raw_cc)
    if len(cleaned) < 3 or cleaned.upper() in ('NONE', 'TEST', 'UNKNOWN') or cleaned[0].isdigit():
      continue
    geom = make_valid(shape(feat['geometry']))
    if geom.is_empty or geom.area < 1e-7:
      continue
    if cleaned.upper() not in cc_groups:
      cc_groups[cleaned.upper()] = {'name': cleaned, 'geoms': [geom], 'raw_cc': {clean_title(raw_cc)}}
    else:
      cc_groups[cleaned.upper()]['geoms'].append(geom)
      cc_groups[cleaned.upper()]['raw_cc'].add(clean_title(raw_cc))

  for k_up, info in cc_groups.items():
    u_geom = make_valid(unary_union(info['geoms'])).simplify(0.0001, preserve_topology=True)
    if u_geom.is_empty or u_geom.area < 1e-7:
      continue
    candidates.append({
        'name': info['name'],
        'raw_name': k_up,
        'geom': u_geom,
        'alt_names': sorted(a for a in info['raw_cc'] if a.upper() != k_up),
        'sources': ['COH Civic Clubs'],
        'priority': 4,
        'tier': 'Civic Club / Neighborhood',
    })

  # Deduplicate candidates that have matching normalized name or high spatial overlap + similar name
  candidates.sort(key=lambda c: (c['priority'], -c['geom'].area))
  merged: list[dict] = []
  for cand in candidates:
    ckey = norm_key(cand['name'])
    cgeom = cand['geom']
    c_cent = cgeom.centroid
    matched_existing = None
    for ex in merged:
      ekey = norm_key(ex['name'])
      # Same name and close/overlapping
      if (ckey and ckey == ekey) or cand['name'].upper() == ex['name'].upper():
        if ex['geom'].distance(cgeom) < 0.025:
          matched_existing = ex
          break
      # Or very high spatial IoU (> 0.65)
      if ex['geom'].intersects(cgeom):
        inter = ex['geom'].intersection(cgeom).area
        union_a = ex['geom'].union(cgeom).area
        if union_a > 0 and (inter / union_a) >= 0.68:
          matched_existing = ex
          break
    if matched_existing is not None:
      for src in cand['sources']:
        if src not in matched_existing['sources']:
          matched_existing['sources'].append(src)
      if cand['name'] != matched_existing['name'] and cand['name'] not in matched_existing['alt_names']:
        matched_existing['alt_names'].append(cand['name'])
      for a in cand['alt_names']:
        if a != matched_existing['name'] and a not in matched_existing['alt_names']:
          matched_existing['alt_names'].append(a)
    else:
      merged.append(cand)

  # Enrich each merged neighborhood with Curated Aliases, Parent Super Neighborhood, and 1920 Historic Ward
  sn_geoms = [shape(f['geometry']) for f in super_nbhds]
  sn_tree = STRtree(sn_geoms)
  w1920_geoms = [shape(f['geometry']) for f in wards_1920]
  w1920_tree = STRtree(w1920_geoms)

  out_features = []
  for idx, item in enumerate(merged):
    name = item['name']
    geom = item['geom']
    rep = geom.representative_point()
    aliases = list(item['alt_names'])
    for cur_a in CURATED_NEIGHBORHOOD_ALIASES.get(name.upper(), []):
      if cur_a.upper() != name.upper() and cur_a not in aliases:
        aliases.append(cur_a)

    # Find parent Super Neighborhood
    parent_sn = None
    for sn_idx in sn_tree.query(rep):
      if sn_geoms[sn_idx].contains(rep) or sn_geoms[sn_idx].intersects(geom):
        parent_sn = super_nbhds[sn_idx]['properties']['name']
        break

    # Find parent 1920 Historic Ward
    parent_ward = None
    for w_idx in w1920_tree.query(rep):
      if w1920_geoms[w_idx].contains(rep):
        parent_ward = wards_1920[w_idx]['properties']['name']
        break

    slug = re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')
    out_features.append({
        'type': 'Feature',
        'properties': {
            'id': f'nbhd_{idx:04d}_{slug}',
            'name': name,
            'tier': item['tier'],
            'alt_names': aliases[:16],
            'sources': item['sources'],
            'super_neighborhood': parent_sn,
            'historic_ward': parent_ward,
            'label_lng': round(rep.x, 5),
            'label_lat': round(rep.y, 5),
            'area_deg2': round(geom.area, 7),
        },
        'geometry': round_coords(mapping(geom.simplify(0.00018, preserve_topology=True)), 5),
    })

  print(f'Synthesized {len(out_features)} deduplicated Vernacular Neighborhood features (from {len(candidates)} raw polygons)')
  return out_features


def main() -> None:
  hcad_to_sub = load_hcad_subdivisions()
  ward_features = build_historic_wards()
  wards_1920 = [f for f in ward_features if f['properties']['era'] == 1920]
  super_nbhds = build_super_neighborhoods()
  vernacular_nbhds = build_vernacular_neighborhoods(super_nbhds, wards_1920)

  # Build STRtrees for fast point-in-polygon enrichment
  # Sort vernacular neighborhoods from smallest area to largest area so specific neighborhoods (e.g. Westmoreland, Old Sixth Ward) match before huge macro areas
  vernacular_sorted = sorted(vernacular_nbhds, key=lambda f: f['properties']['area_deg2'])
  v_geoms = [shape(f['geometry']) for f in vernacular_sorted]
  v_tree = STRtree(v_geoms)

  sn_geoms = [shape(f['geometry']) for f in super_nbhds]
  sn_tree = STRtree(sn_geoms)

  w1920_geoms = [shape(f['geometry']) for f in wards_1920]
  w1920_tree = STRtree(w1920_geoms)

  # Track building statistics per neighborhood, super neighborhood, and 1920 ward
  nbhd_stats = defaultdict(lambda: {'years': [], 'landmarks': 0, 'good_brick': 0, 'subs': Counter()})
  sn_stats = defaultdict(lambda: {'years': [], 'landmarks': 0, 'good_brick': 0, 'subs': Counter()})
  ward_stats = defaultdict(lambda: {'years': [], 'landmarks': 0, 'good_brick': 0, 'subs': Counter()})

  # 1. Enrich buildings.geojson
  blds_path = os.path.join(DATA_DIR, 'buildings.geojson')
  with open(blds_path) as f:
    blds_fc = json.load(f)

  for feat in blds_fc.get('features', []):
    props = feat['properties']
    geom = shape(feat['geometry'])
    pt = geom.centroid
    hcad = str(props.get('hcad_num') or '').split('#')[0].strip()
    sub = hcad_to_sub.get(hcad) or normalize_hcad_subdivision(str(props.get('subdivision') or ''))
    if sub:
      props['subdivision'] = sub

    # Match vernacular neighborhoods containing pt
    matched_v = []
    for vi in v_tree.query(pt):
      if v_geoms[vi].contains(pt):
        matched_v.append(vernacular_sorted[vi]['properties'])
    if matched_v:
      primary_v = matched_v[0]
      props['neighborhood'] = primary_v['name']
      extra_names = []
      for mv in matched_v[1:]:
        if mv['name'] != primary_v['name'] and mv['name'] not in extra_names:
          extra_names.append(mv['name'])
      for a in primary_v.get('alt_names', [])[:6]:
        if a not in extra_names and a != primary_v['name']:
          extra_names.append(a)
      if extra_names:
        props['neighborhood_aliases'] = extra_names[:8]

    # Match Super Neighborhood
    for sni in sn_tree.query(pt):
      if sn_geoms[sni].contains(pt):
        props['super_neighborhood'] = super_nbhds[sni]['properties']['name']
        break

    # Match 1920 Historic Ward
    for wi in w1920_tree.query(pt):
      if w1920_geoms[wi].contains(pt):
        props['historic_ward'] = wards_1920[wi]['properties']['name']
        break

  with open(blds_path, 'w') as f:
    json.dump(blds_fc, f, separators=(',', ':'))
  print(f'Enriched {len(blds_fc["features"])} buildings in buildings.geojson with neighborhood, ward, and subdivision metadata')

  # 2. Enrich curated_overrides.json
  ov_path = os.path.join(DATA_DIR, 'curated_overrides.json')
  with open(ov_path) as f:
    ov_data = json.load(f)
  overrides_map = ov_data.get('overrides', {})
  for key, rec in overrides_map.items():
    if rec.get('suppress_only'):
      continue
    hcad = str(rec.get('hcad_num') or key).split('#')[0].strip()
    sub = hcad_to_sub.get(hcad) or normalize_hcad_subdivision(str(rec.get('subdivision') or ''))
    if sub:
      rec['subdivision'] = sub
    g_raw = rec.get('geometry')
    if not g_raw:
      continue
    pt = shape(g_raw).centroid
    matched_v = [vernacular_sorted[vi]['properties'] for vi in v_tree.query(pt) if v_geoms[vi].contains(pt)]
    if matched_v:
      primary_v = matched_v[0]
      rec['neighborhood'] = primary_v['name']
      extra_names = [mv['name'] for mv in matched_v[1:] if mv['name'] != primary_v['name']]
      for a in primary_v.get('alt_names', [])[:6]:
        if a not in extra_names and a != primary_v['name']:
          extra_names.append(a)
      if extra_names:
        rec['neighborhood_aliases'] = extra_names[:8]
    for sni in sn_tree.query(pt):
      if sn_geoms[sni].contains(pt):
        rec['super_neighborhood'] = super_nbhds[sni]['properties']['name']
        break
    for wi in w1920_tree.query(pt):
      if w1920_geoms[wi].contains(pt):
        rec['historic_ward'] = wards_1920[wi]['properties']['name']
        break

  with open(ov_path, 'w') as f:
    json.dump(ov_data, f, separators=(',', ':'))
  print(f'Enriched {len(overrides_map)} records in curated_overrides.json')

  # 3. Fast vectorized countywide statistics pass across all 5 .geojsonseq shards
  coord_re = re.compile(r'"coordinates":\s*\[\s*\[\s*(?:\[\s*)?([-\d.]+)\s*,\s*([-\d.]+)')
  yr_re = re.compile(r'"year_built":\s*(\d+)')
  hcad_re = re.compile(r'"hcad_num":\s*"([^"]+)"')
  lm_re = re.compile(r'"is_landmark":\s*true')
  gb_re = re.compile(r'"good_brick":\s*true')

  lons_list = []
  lats_list = []
  yrs_list = []
  hcads_list = []
  lms_list = []
  gbs_list = []

  shard_files = sorted(glob.glob(os.path.join(CACHE_DIR, 'aligned_ndjson', 'buildings_*.geojsonseq')))
  for sf in shard_files:
    with open(sf) as f:
      for line in f:
        mc = coord_re.search(line)
        if not mc:
          continue
        lons_list.append(float(mc.group(1)))
        lats_list.append(float(mc.group(2)))
        my = yr_re.search(line)
        yrs_list.append(int(my.group(1)) if my else 0)
        mh = hcad_re.search(line)
        hcads_list.append(mh.group(1) if mh else '')
        lms_list.append(bool(lm_re.search(line)))
        gbs_list.append(bool(gb_re.search(line)))

  print(f'Loaded {len(lons_list):,} building points from 5 countywide shards; running vectorized spatial join...')
  pts_arr = points(np.array(lons_list, dtype=np.float64), np.array(lats_list, dtype=np.float64))

  def accumulate_tree_stats(poly_tree, poly_feats, stats_dict, id_prop):
    pt_idxs, poly_idxs = poly_tree.query(pts_arr, predicate='intersects')
    for pt_i, poly_i in zip(pt_idxs.tolist(), poly_idxs.tolist()):
      key_id = poly_feats[poly_i]['properties'][id_prop]
      st = stats_dict[key_id]
      yr = yrs_list[pt_i]
      if 1800 <= yr <= 2026:
        st['years'].append(yr)
      if lms_list[pt_i]:
        st['landmarks'] += 1
      if gbs_list[pt_i]:
        st['good_brick'] += 1
      sub = hcad_to_sub.get(hcads_list[pt_i])
      if sub:
        st['subs'][sub] += 1

  accumulate_tree_stats(v_tree, vernacular_sorted, nbhd_stats, 'id')
  accumulate_tree_stats(sn_tree, super_nbhds, sn_stats, 'id')
  accumulate_tree_stats(w1920_tree, wards_1920, ward_stats, 'ward_key')

  def attach_stats(feat_props, st):
    yrs = st['years']
    feat_props['building_count'] = len(yrs)
    if yrs:
      arr = np.array(yrs)
      feat_props['earliest_year'] = int(arr.min())
      feat_props['median_year'] = int(np.median(arr))
      feat_props['pre_1940_count'] = int((arr < 1940).sum())
    else:
      feat_props['earliest_year'] = 0
      feat_props['median_year'] = 0
      feat_props['pre_1940_count'] = 0
    feat_props['landmark_count'] = st['landmarks']
    feat_props['good_brick_count'] = st['good_brick']
    top_subs = [s for s, _ in st['subs'].most_common(8)]
    feat_props['top_subdivisions'] = top_subs
    # Also add top platted subdivisions into alt_names if not already present
    existing_alts = list(feat_props.get('alt_names') or [])
    for s in top_subs[:5]:
      if s.upper() != feat_props['name'].upper() and s not in existing_alts:
        existing_alts.append(s)
    feat_props['alt_names'] = existing_alts[:16]

  for feat in vernacular_nbhds:
    attach_stats(feat['properties'], nbhd_stats[feat['properties']['id']])
  for feat in super_nbhds:
    attach_stats(feat['properties'], sn_stats[feat['properties']['id']])
  for feat in ward_features:
    attach_stats(feat['properties'], ward_stats[feat['properties']['ward_key']])

  # 4. Update overlays.json
  overlays_path = os.path.join(DATA_DIR, 'overlays.json')
  with open(overlays_path) as f:
    overlays = json.load(f)
  overlays['neighborhoods'] = {
      'type': 'FeatureCollection',
      'features': vernacular_nbhds,
  }
  overlays['super_neighborhoods'] = {
      'type': 'FeatureCollection',
      'features': super_nbhds,
  }
  overlays['historic_wards'] = {
      'type': 'FeatureCollection',
      'features': ward_features,
  }
  with open(overlays_path, 'w') as f:
    json.dump(overlays, f, separators=(',', ':'))
  print(f'Updated {overlays_path} ({os.path.getsize(overlays_path)/1e6:.2f} MB)')

  # 5. Enrich search_index.json with building neighborhood/subdivision fields AND searchable Neighborhood / Ward / Super Neighborhood entries
  si_path = os.path.join(DATA_DIR, 'search_index.json')
  with open(si_path) as f:
    si_list = json.load(f)

  # Remove any previous place entries (is_neighborhood_entry)
  si_buildings = [item for item in si_list if not item.get('is_neighborhood_entry')]
  for item in si_buildings:
    hcad = str(item.get('hcad_num') or '').split('#')[0].strip()
    sub = hcad_to_sub.get(hcad) or normalize_hcad_subdivision(str(item.get('subdivision') or ''))
    if sub:
      item['subdivision'] = sub
    lng, lat = item.get('lng'), item.get('lat')
    if lng is not None and lat is not None:
      pt = Point(float(lng), float(lat))
      matched_v = [vernacular_sorted[vi]['properties'] for vi in v_tree.query(pt) if v_geoms[vi].contains(pt)]
      if matched_v:
        primary_v = matched_v[0]
        item['neighborhood'] = primary_v['name']
        extra = [mv['name'] for mv in matched_v[1:] if mv['name'] != primary_v['name']]
        for a in primary_v.get('alt_names', [])[:5]:
          if a not in extra and a != primary_v['name']:
            extra.append(a)
        if extra:
          item['neighborhood_aliases'] = extra[:6]
      for sni in sn_tree.query(pt):
        if sn_geoms[sni].contains(pt):
          item['super_neighborhood'] = super_nbhds[sni]['properties']['name']
          break
      for wi in w1920_tree.query(pt):
        if w1920_geoms[wi].contains(pt):
          item['historic_ward'] = wards_1920[wi]['properties']['name']
          break

  place_entries = []
  # Add 1920 Historic Wards to search index
  for feat in wards_1920:
    p = feat['properties']
    geom = shape(feat['geometry'])
    place_entries.append({
        'id': p['id'],
        'is_neighborhood_entry': True,
        'place_type': 'Historic Ward (1839–1920)',
        'overlay_layer': 'historicWards',
        'name': p['name'],
        'building_name': p['name'],
        'alt_names': p.get('alt_names', []),
        'address': f"Original Houston Ward · {p.get('building_count', 0):,} structures · Median {p.get('median_year') or 'N/A'}",
        'category': 'Historic Ward',
        'year_built': p.get('median_year') or 1900,
        'earliest_year': p.get('earliest_year') or 0,
        'building_count': p.get('building_count') or 0,
        'pre_1940_count': p.get('pre_1940_count') or 0,
        'top_subdivisions': p.get('top_subdivisions', []),
        'lng': p['label_lng'],
        'lat': p['label_lat'],
        'bbox': [round(v, 5) for v in geom.bounds],
    })

  # Add Super Neighborhoods to search index
  for feat in super_nbhds:
    p = feat['properties']
    geom = shape(feat['geometry'])
    place_entries.append({
        'id': p['id'],
        'is_neighborhood_entry': True,
        'place_type': f"Super Neighborhood #{p['snbr_id']}",
        'overlay_layer': 'superNeighborhoods',
        'name': p['name'],
        'building_name': f"{p['name']} (Super Neighborhood #{p['snbr_id']})",
        'alt_names': p.get('alt_names', []),
        'address': f"COH Super Neighborhood #{p['snbr_id']} · {p.get('building_count', 0):,} structures · Median {p.get('median_year') or 'N/A'}",
        'category': 'Super Neighborhood',
        'year_built': p.get('median_year') or 1960,
        'earliest_year': p.get('earliest_year') or 0,
        'building_count': p.get('building_count') or 0,
        'pre_1940_count': p.get('pre_1940_count') or 0,
        'top_subdivisions': p.get('top_subdivisions', []),
        'lng': p['label_lng'],
        'lat': p['label_lat'],
        'bbox': [round(v, 5) for v in geom.bounds],
    })

  # Add Vernacular Neighborhoods to search index
  for feat in vernacular_nbhds:
    p = feat['properties']
    geom = shape(feat['geometry'])
    sn_tag = f" · {p['super_neighborhood']}" if p.get('super_neighborhood') else ''
    place_entries.append({
        'id': p['id'],
        'is_neighborhood_entry': True,
        'place_type': p.get('tier') or 'Neighborhood',
        'overlay_layer': 'neighborhoods',
        'name': p['name'],
        'building_name': p['name'],
        'alt_names': p.get('alt_names', []),
        'super_neighborhood': p.get('super_neighborhood'),
        'historic_ward': p.get('historic_ward'),
        'address': f"Houston Neighborhood{sn_tag} · {p.get('building_count', 0):,} structures · Median {p.get('median_year') or 'N/A'}",
        'category': 'Neighborhood',
        'year_built': p.get('median_year') or 1950,
        'earliest_year': p.get('earliest_year') or 0,
        'building_count': p.get('building_count') or 0,
        'pre_1940_count': p.get('pre_1940_count') or 0,
        'top_subdivisions': p.get('top_subdivisions', []),
        'lng': p['label_lng'],
        'lat': p['label_lat'],
        'bbox': [round(v, 5) for v in geom.bounds],
    })

  combined_si = place_entries + si_buildings
  with open(si_path, 'w') as f:
    json.dump(combined_si, f, separators=(',', ':'))
  print(f'Updated {si_path}: {len(place_entries)} neighborhood/ward entries + {len(si_buildings)} building entries = {len(combined_si)} total ({os.path.getsize(si_path)/1e6:.2f} MB)')


if __name__ == '__main__':
  main()
