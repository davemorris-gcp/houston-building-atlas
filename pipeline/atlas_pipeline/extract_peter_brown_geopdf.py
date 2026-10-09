"""Extract georeferenced neighborhood labels and vector polygons from the 2009 Peter Brown / Tony Topping GeoPDF.

The 2009 map 'The Neighborhoods of Houston' (Office of Council Member Peter Brown,
produced by Tony Topping, assisted by Ghizal Miri) was exported from ESRI ArcMap 9.3.1.1850
as an ISO 32000 GeoPDF containing:
  1. Embedded EPSG:2278 / WGS84 viewport georeferencing (220 0 obj Main Map, 222 0 obj Kingwood Inset)
  2. Vector annotation layers (/OC4 and /OC15 'internedit Anno', plus /OC3 '<Default>')
     with exact (x, y) PDF page coordinates for ~692 neighborhood labels
  3. Lossless 150 DPI ASCII85+FlateDecode RGB raster strips (/OC2 Image_3..147 at 7070x5331 px,
     and /OC13 Image_149..153 at 1368x1061 px) containing flat-colored neighborhood polygons
     without text labels or highway shields burned in.
"""

from __future__ import annotations

import base64
import json
import math
import os
import re
import zlib
import cv2
import numpy as np
from shapely.geometry import Polygon, mapping
from shapely.validation import make_valid

NON_NEIGHBORHOOD_RGBS = {
    (255, 255, 190),  # Background cream outside Houston
    (225, 225, 225),  # Industrial areas
    (151, 219, 242),  # Water (Bayous, Lake Houston, Galveston Bay)
    (64, 101, 235),   # Water / river channel blue
    (151, 113, 170),  # Commercial / office areas
    (56, 168, 0),     # Parks fill green
    (38, 115, 0),     # Parks border green
    (230, 230, 0),    # Major highway yellow
    (230, 0, 0),      # Major highway red
    (0, 92, 230),     # Major highway blue
    (78, 78, 78),     # Polygon border dark gray
    (104, 104, 104),  # Polygon border medium gray
    (178, 178, 178),  # Polygon border light gray
    (0, 0, 0),        # Black
    (255, 255, 255),  # White
}


def decode_image_strip(raw: bytes, oid: int) -> np.ndarray:
  mo = re.search(
      rf'(?:^|\r|\n){oid}\s+0\s+obj\r?\n(.*?)stream\r?\n'.encode('latin1'),
      raw,
      re.DOTALL,
  )
  if not mo:
    raise ValueError(f'Object {oid} not found in PDF')
  hdr = mo.group(1).decode('latin1')
  w = int(re.search(r'/Width\s+(\d+)', hdr).group(1))
  h = int(re.search(r'/Height\s+(\d+)', hdr).group(1))
  s = mo.end()
  e = raw.find(b'endstream', s)
  chunk = raw[s:e].strip()
  if chunk.endswith(b'~>'):
    chunk = chunk[:-2]
  dec = zlib.decompress(base64.a85decode(chunk))
  return np.frombuffer(dec, dtype=np.uint8).reshape((h, w, 3))[::-1]


def stitch_oc_raster(raw: bytes, content: str, oc_tag: str) -> np.ndarray:
  m_oc = re.search(oc_tag + r'\s+BDC(.*?)EMC', content, re.DOTALL)
  placements = re.findall(
      r'([-\d.]+)\s+0\s+0\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+cm\s+/Image_(\d+)\s+Do',
      m_oc.group(1),
  )
  rows = []
  for i, (_, _, _, _, img_num) in enumerate(placements):
    st = decode_image_strip(raw, int(img_num))
    rows.append(st[:-1] if i < len(placements) - 1 else st)
  return np.vstack(rows)


def extract_grouped_labels(content: str, oc_tag: str) -> list[dict]:
  m_oc = re.search(oc_tag + r'\s+BDC(.*?)EMC', content, re.DOTALL)
  if not m_oc:
    return []
  bts = re.findall(r'BT(.*?)ET', m_oc.group(1), re.DOTALL)
  raw_items = []
  for bt in bts:
    tms = re.findall(
        r'([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+Tm',
        bt,
    )
    strs = re.findall(r'\(([^()]*)\)', bt)
    txt = ' '.join(''.join(strs).split())
    if tms and txt:
      a, b, _, _, x, y = [float(v) for v in tms[-1]]
      fsize = math.hypot(a, b)
      raw_items.append({'txt': txt, 'x': x, 'y': y, 'fsize': fsize})

  grouped = []
  for item in raw_items:
    if grouped:
      prev = grouped[-1]
      dist = math.hypot(prev['last_x'] - item['x'], prev['last_y'] - item['y'])
      dy = prev['last_y'] - item['y']
      dx = abs(prev['last_x'] - item['x'])
      if (
          (2.0 <= dy <= 16.5 and dx <= 52.0) or dist <= 24.0
      ) and abs(prev['fsize'] - item['fsize']) < 0.6:
        sep = '' if prev['txt'].endswith('-') else ' '
        prev['txt'] = (prev['txt'].rstrip() + sep + item['txt'].strip()).strip()
        prev['last_x'] = item['x']
        prev['last_y'] = item['y']
        prev['xs'].append(item['x'])
        prev['ys'].append(item['y'])
        continue
    grouped.append({
        'txt': item['txt'].strip(),
        'last_x': item['x'],
        'last_y': item['y'],
        'xs': [item['x']],
        'ys': [item['y']],
        'fsize': item['fsize'],
    })
  return grouped


def vectorize_viewport_neighborhoods(
    labels_list: list[dict],
    canvas: np.ndarray,
    vp_bbox: tuple[float, float, float, float],
    gpts: list[tuple[float, float]],
    viewport_name: str,
) -> list[dict]:
  H, W, _ = canvas.shape
  x0_vp, y_top_vp, x1_vp, y_bot_vp = vp_bbox
  w_vp = x1_vp - x0_vp
  h_vp = y_top_vp - y_bot_vp

  def px_to_lonlat(px: float, py: float) -> tuple[float, float]:
    u = px / float(W)
    v = 1.0 - (py / float(H))
    lat = (
        (1 - u) * (1 - v) * gpts[0][0]
        + (1 - u) * v * gpts[1][0]
        + u * v * gpts[2][0]
        + u * (1 - v) * gpts[3][0]
    )
    lon = (
        (1 - u) * (1 - v) * gpts[0][1]
        + (1 - u) * v * gpts[1][1]
        + u * v * gpts[2][1]
        + u * (1 - v) * gpts[3][1]
    )
    return (round(lon, 5), round(lat, 5))

  features = []
  for g in labels_list:
    name = g['txt']
    if name == 'DOWTOWN':
      name = 'DOWNTOWN'
    elif name == 'LANCASTE PLACE':
      name = 'LANCASTER PLACE'
    cx = sum(g['xs']) / len(g['xs'])
    cy = sum(g['ys']) / len(g['ys'])
    px = int(round((cx - x0_vp) / w_vp * W))
    py = int(round((y_top_vp - cy) / h_vp * H))
    if not (0 <= px < W and 0 <= py < H):
      continue
    win = canvas[
        max(0, py - 15) : min(H, py + 16), max(0, px - 15) : min(W, px + 16)
    ].reshape(-1, 3)
    packed = (
        (win[:, 0].astype(int) << 16)
        | (win[:, 1].astype(int) << 8)
        | win[:, 2].astype(int)
    )
    vals, cnts = np.unique(packed, return_counts=True)
    order = np.argsort(-cnts)
    chosen_rgb = None
    for idx in order:
      v = vals[idx]
      rgb = (int((v >> 16) & 255), int((v >> 8) & 255), int(v & 255))
      if rgb not in NON_NEIGHBORHOOD_RGBS and not (rgb[0] == rgb[1] == rgb[2]):
        chosen_rgb = rgb
        break
    if not chosen_rgb:
      continue

    wx0, wy0 = max(0, px - 450), max(0, py - 450)
    wx1, wy1 = min(W, px + 450), min(H, py + 450)
    sub = canvas[wy0:wy1, wx0:wx1]
    mask = (
        (sub[:, :, 0] == chosen_rgb[0])
        & (sub[:, :, 1] == chosen_rgb[1])
        & (sub[:, :, 2] == chosen_rgb[2])
    ).astype(np.uint8)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    num_labels, comp_labels, _, _ = cv2.connectedComponentsWithStats(closed)
    if num_labels <= 1:
      continue
    l_id = comp_labels[py - wy0, px - wx0]
    if l_id == 0:
      ys_nz, xs_nz = np.nonzero(closed)
      if len(xs_nz) == 0:
        continue
      dists = (xs_nz - (px - wx0)) ** 2 + (ys_nz - (py - wy0)) ** 2
      l_id = comp_labels[ys_nz[np.argmin(dists)], xs_nz[np.argmin(dists)]]
    comp_mask = cv2.dilate(
        (comp_labels == l_id).astype(np.uint8),
        np.ones((3, 3), np.uint8),
        iterations=1,
    )
    contours, _ = cv2.findContours(
        comp_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    if not contours:
      continue
    cnt = max(contours, key=cv2.contourArea)
    if len(cnt) < 3:
      continue
    ring = [px_to_lonlat(wx0 + pt[0][0], wy0 + pt[0][1]) for pt in cnt]
    ring.append(ring[0])
    poly = make_valid(Polygon(ring)).simplify(0.00012, preserve_topology=True)
    if poly.is_empty:
      continue
    lon_c, lat_c = px_to_lonlat(px, py)
    features.append({
        'type': 'Feature',
        'properties': {
            'name': name,
            'source': '2009 Peter Brown / Tony Topping Map (GeoPDF)',
            'viewport': viewport_name,
            'rgb_hex': f'#{chosen_rgb[0]:02x}{chosen_rgb[1]:02x}{chosen_rgb[2]:02x}',
            'label_lng': lon_c,
            'label_lat': lat_c,
        },
        'geometry': mapping(poly),
    })
  return features


def extract_peter_brown_geopdf(pdf_path: str, out_geojson_path: str) -> None:
  with open(pdf_path, 'rb') as f:
    raw = f.read()
  m = re.search(rb'1\s+0\s+obj\b.*?stream\r?\n', raw, re.DOTALL)
  content = zlib.decompress(
      raw[m.end() : raw.find(b'endstream', m.end())]
  ).decode('latin1')

  main_canvas = stitch_oc_raster(raw, content, '/OC2')
  kw_canvas = stitch_oc_raster(raw, content, '/OC13')
  main_labels = extract_grouped_labels(content, '/OC4') + extract_grouped_labels(
      content, '/OC3'
  )
  kw_labels = extract_grouped_labels(content, '/OC15')

  main_feats = vectorize_viewport_neighborhoods(
      main_labels,
      main_canvas,
      (27.84032, 2572.31997, 27.84032 + 3393.6393, 2572.31997 - 2558.88382),
      [
          (29.56113, -95.71636),
          (29.9925, -95.70244),
          (29.97476, -95.04526),
          (29.54346, -95.06194),
      ],
      'main',
  )
  kw_feats = vectorize_viewport_neighborhoods(
      kw_labels,
      kw_canvas,
      (2660.1908, 2092.79926, 2660.1908 + 656.6476, 1583.5185),
      [
          (30.01429, -95.25524),
          (30.10009, -95.25208),
          (30.09648, -95.12473),
          (30.01068, -95.128),
      ],
      'kingwood',
  )
  os.makedirs(os.path.dirname(out_geojson_path), exist_ok=True)
  with open(out_geojson_path, 'w') as f:
    json.dump({'type': 'FeatureCollection', 'features': main_feats + kw_feats}, f)
  print(f'Saved {len(main_feats) + len(kw_feats)} features to {out_geojson_path}')
