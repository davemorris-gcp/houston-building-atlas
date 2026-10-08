"""Enrich search_index.json, buildings.geojson, and curated_overrides.json with complete Architectural Style, HCAD Building Class (bld_style), Land Use (use_category / landuse_desc), and verified year_built metadata."""

from collections import Counter
import glob
import json
import os

ROOT = "/usr/local/google/home/davemorris/houston-building-atlas"
SEARCH_INDEX_PATH = os.path.join(ROOT, "app/public/data/search_index.json")
BUILDINGS_PATH = os.path.join(ROOT, "app/public/data/buildings.geojson")
OVERRIDES_PATH = os.path.join(ROOT, "app/public/data/curated_overrides.json")
OVERLAYS_PATH = os.path.join(ROOT, "app/public/data/overlays.json")
SHARD_GLOB = os.path.join(ROOT, "pipeline/cache/aligned_ndjson/*.geojsonseq")

GENERIC_PLACEHOLDER_STYLES = {
    "",
    "Historical / Architectural Structure",
    "Houston Historic Structure",
    "Historic Structure",
    "Structure",
    "None",
    "null",
}

SINGLE_LETTER_GRADES = {
    "A++",
    "A+",
    "A",
    "A-",
    "B+",
    "B",
    "B-",
    "C+",
    "C",
    "C-",
    "D+",
    "D",
    "D-",
    "E+",
    "E",
    "E-",
    "F",
    "X",
}

STRUCTURAL_CLASSES = {
    "Wood or Light Steel",
    "Masonry Bearing",
    "Open Steel Skeleton",
    "Reinforced Concrete",
    "Fireproofed Steel",
    "Mobile Home/Manufactured Housing",
    "Storage Tank",
    "Petrochemical Complex",
    "Educational Campus Structure",
    "Frame Utility Shed",
    "Carport - Residential",
    "Canopy - Residential",
    "Frame Detached Garage",
    "Utility Building - Metal",
    "Portable/Modular Office - Average",
}


def is_specific_style(val: str) -> bool:
  v = (val or "").strip()
  return (
      bool(v)
      and v not in GENERIC_PLACEHOLDER_STYLES
      and v not in SINGLE_LETTER_GRADES
  )


def main():
  with open(SEARCH_INDEX_PATH) as f:
    search_index = json.load(f)
  with open(BUILDINGS_PATH) as f:
    buildings_fc = json.load(f)
  with open(OVERRIDES_PATH) as f:
    overrides_data = json.load(f)
  with open(OVERLAYS_PATH) as f:
    overlays_data = json.load(f)

  overrides = overrides_data.get("overrides", {})

  # 1. Collect authoritative architectural styles, years, & architects from COH Landmarks and Good Brick Awards
  landmark_meta_by_hcad = {}
  landmark_meta_by_name = {}
  for feat in overlays_data.get("landmarks", {}).get("features", []):
    p = feat.get("properties") or {}
    hcad = str(p.get("hcad_num") or "").strip()
    name = str(p.get("name") or "").strip().lower()
    rec = {}
    if is_specific_style(p.get("style")):
      rec["style"] = str(p["style"]).strip()
    if p.get("architect"):
      rec["architect"] = str(p["architect"]).strip()
    if p.get("historic_district"):
      rec["historic_district"] = str(p["historic_district"]).strip()
    if int(p.get("year_built") or 0) >= 1820:
      rec["year_built"] = int(p["year_built"])
    if p.get("address"):
      rec["address"] = str(p["address"]).strip()
    lm_code = p.get("plm_num") or p.get("lm_num") or ""
    if lm_code:
      rec["landmark_code"] = str(lm_code).replace("HPO #", "").strip()
    if hcad:
      dest = landmark_meta_by_hcad.setdefault(hcad, {})
      for k, v in rec.items():
        if v and not dest.get(k):
          dest[k] = v
    if name:
      dest = landmark_meta_by_name.setdefault(name, {})
      for k, v in rec.items():
        if v and not dest.get(k):
          dest[k] = v

  for feat in overlays_data.get("good_brick_awards", {}).get("features", []):
    p = feat.get("properties") or {}
    hcad = str(p.get("hcad_num") or "").strip()
    if not hcad:
      continue
    rec = landmark_meta_by_hcad.setdefault(hcad, {})
    if p.get("good_brick_years") and not rec.get("good_brick_years"):
      rec["good_brick_years"] = str(p["good_brick_years"]).strip()
    if p.get("historic_district") and not rec.get("historic_district"):
      rec["historic_district"] = str(p["historic_district"]).strip()
    if int(p.get("year_built") or 0) >= 1820 and not rec.get("year_built"):
      rec["year_built"] = int(p["year_built"])

  # 2. Scan buildings.geojson for metadata by HCAD
  bldg_meta_by_hcad = {}
  for feat in buildings_fc.get("features", []):
    p = feat.get("properties") or {}
    hcad = str(p.get("hcad_num") or "").strip()
    if not hcad:
      continue
    rec = bldg_meta_by_hcad.setdefault(hcad, {})
    st = str(p.get("style") or "").strip()
    bs = str(p.get("bld_style") or "").strip()
    if is_specific_style(st) and not is_specific_style(rec.get("style")):
      rec["style"] = st
    if is_specific_style(bs) and not is_specific_style(rec.get("bld_style")):
      rec["bld_style"] = bs
    elif bs in SINGLE_LETTER_GRADES and not rec.get("hcad_grade"):
      rec["hcad_grade"] = bs
    if p.get("hcad_grade") and not rec.get("hcad_grade"):
      rec["hcad_grade"] = str(p["hcad_grade"]).strip()
    if p.get("use_category") and not rec.get("use_category"):
      rec["use_category"] = str(p["use_category"]).strip()
    if p.get("landuse_desc") and not rec.get("landuse_desc"):
      rec["landuse_desc"] = str(p["landuse_desc"]).strip()
    if p.get("architect") and not rec.get("architect"):
      rec["architect"] = str(p["architect"]).strip()
    if int(p.get("year_built") or 0) >= 1820 and not rec.get("year_built"):
      rec["year_built"] = int(p["year_built"])
    if p.get("address") and not rec.get("address"):
      rec["address"] = str(p["address"]).strip()
    if p.get("historic_district") and not rec.get("historic_district"):
      d = str(p["historic_district"]).strip()
      if d not in ("Outside City District", "Outside Historic District"):
        rec["historic_district"] = d

  # 3. Scan countywide shards for HCAD structural class (bld_style), use_category, landuse_desc, year_built
  target_hcads = set()
  for item in search_index:
    h = str(item.get("hcad_num") or "").strip()
    if h:
      target_hcads.add(h)
  for feat in buildings_fc.get("features", []):
    h = str((feat.get("properties") or {}).get("hcad_num") or "").strip()
    if h:
      target_hcads.add(h)
  for k, ov in overrides.items():
    h = str(ov.get("hcad_num") or k.split("#")[0]).strip()
    if h:
      target_hcads.add(h)

  shard_meta_by_hcad = {}
  class_representatives = {}

  for shard_path in sorted(glob.glob(SHARD_GLOB)):
    with open(shard_path) as f:
      for line in f:
        s = line.lstrip("\x1e").strip()
        if not s:
          continue
        feat = json.loads(s)
        p = feat.get("properties") or {}
        hcad = str(p.get("hcad_num") or "").strip()
        bs = str(p.get("bld_style") or "").strip()
        st = str(p.get("style") or "").strip()
        uc = str(p.get("use_category") or "").strip()
        lu = str(p.get("landuse_desc") or "").strip()
        yr = int(p.get("year_built") or 0)

        if hcad in target_hcads:
          rec = shard_meta_by_hcad.setdefault(hcad, {})
          if is_specific_style(bs) and not is_specific_style(
              rec.get("bld_style")
          ):
            rec["bld_style"] = bs
          elif bs in SINGLE_LETTER_GRADES and not rec.get("hcad_grade"):
            rec["hcad_grade"] = bs
          if is_specific_style(st) and not is_specific_style(rec.get("style")):
            rec["style"] = st
          if uc and not rec.get("use_category"):
            rec["use_category"] = uc
          if lu and not rec.get("landuse_desc"):
            rec["landuse_desc"] = lu
          if yr >= 1820 and not rec.get("year_built"):
            rec["year_built"] = yr
          if p.get("address") and not rec.get("address"):
            rec["address"] = str(p["address"]).strip()

        if is_specific_style(bs) and bs not in ("Residential",):
          reps = class_representatives.setdefault(bs, [])
          if len(reps) < 35:
            addr = str(p.get("address") or "").strip()
            area = float(p.get("bld_area") or 0)
            if (
                addr
                and not addr.startswith("0 ")
                and yr >= 1850
                and area >= 1500
            ):
              if not any(r["hcad_num"] == hcad for r in reps):
                ring = None
                geom = feat.get("geometry") or {}
                if geom.get("type") == "Polygon":
                  ring = (geom.get("coordinates") or [None])[0]
                elif geom.get("type") == "MultiPolygon":
                  ring = ((geom.get("coordinates") or [[None]])[0] or [None])[0]
                if ring and ring[0]:
                  lon, lat = ring[0][0], ring[0][1]
                  dist = p.get("historic_district")
                  dist_clean = (
                      dist
                      if dist
                      not in (
                          "Outside City District",
                          "Outside Historic District",
                          None,
                          "",
                      )
                      else ""
                  )
                  reps.append({
                      "type": "building",
                      "id": p.get("id") or f"hcad_{hcad}",
                      "hcad_num": hcad,
                      "label": p.get("landmark_name") or addr,
                      "sublabel": " • ".join(
                          filter(
                              None,
                              [
                                  dist_clean or uc or "Harris County",
                                  bs,
                                  f"Built {yr}",
                              ],
                          )
                      ),
                      "category": f"Built {yr}",
                      "year_built": yr,
                      "bld_style": bs,
                      "use_category": uc or "Commercial",
                      "landuse_desc": lu,
                      "lon": round(float(lon), 6),
                      "lat": round(float(lat), 6),
                      "zoom": 17.4,
                  })

  # 4. Enrich every entry in search_index.json
  existing_hcads_in_si = set()
  for item in search_index:
    if item.get("type") == "district":
      continue
    hcad = str(item.get("hcad_num") or "").strip()
    if hcad:
      existing_hcads_in_si.add(hcad)
    lbl_lower = str(item.get("label") or "").strip().lower()
    ov = overrides.get(item.get("id") or "") or overrides.get(hcad) or {}
    lm = (
        landmark_meta_by_hcad.get(hcad)
        or landmark_meta_by_name.get(lbl_lower)
        or {}
    )
    bm = bldg_meta_by_hcad.get(hcad, {})
    sm = shard_meta_by_hcad.get(hcad, {})

    # Resolve year_built
    yr = (
        int(ov.get("year_built") or 0)
        or int(item.get("year_built") or 0)
        or int(lm.get("year_built") or 0)
        or int(bm.get("year_built") or 0)
        or int(sm.get("year_built") or 0)
    )
    if yr >= 1820:
      item["year_built"] = yr

    arch = (
        item.get("architect")
        or ov.get("architect")
        or lm.get("architect")
        or bm.get("architect")
        or ""
    )
    if arch and arch.strip().lower() not in ("unknown", "none", "n/a", "null"):
      item["architect"] = arch.strip()
    else:
      arch = ""
      item.pop("architect", None)

    # Prefer true architectural style over structural frame class when available
    arch_st = (
        lm.get("style")
        or (
            ov.get("style")
            if is_specific_style(ov.get("style"))
            and ov.get("style") not in STRUCTURAL_CLASSES
            else ""
        )
        or (
            bm.get("style")
            if is_specific_style(bm.get("style"))
            and bm.get("style") not in STRUCTURAL_CLASSES
            else ""
        )
        or (
            sm.get("style")
            if is_specific_style(sm.get("style"))
            and sm.get("style") not in STRUCTURAL_CLASSES
            else ""
        )
        or ""
    )
    struct_bs = (
        (sm.get("bld_style") if is_specific_style(sm.get("bld_style")) else "")
        or (
            bm.get("bld_style")
            if is_specific_style(bm.get("bld_style"))
            else ""
        )
        or (
            ov.get("bld_style")
            if is_specific_style(ov.get("bld_style"))
            else ""
        )
        or ""
    )

    if arch_st:
      item["style"] = arch_st
      item["bld_style"] = arch_st
    elif struct_bs:
      item["bld_style"] = struct_bs
      if "style" in item and not is_specific_style(item["style"]):
        del item["style"]

    grade = bm.get("hcad_grade") or sm.get("hcad_grade") or ""
    if grade:
      item["hcad_grade"] = grade

    uc = (
        bm.get("use_category")
        or sm.get("use_category")
        or ov.get("use_category")
        or item.get("use_category")
        or "Residential"
    )
    item["use_category"] = uc

    lu = (
        bm.get("landuse_desc")
        or sm.get("landuse_desc")
        or ov.get("landuse_desc")
        or item.get("landuse_desc")
        or ""
    )
    if lu:
      item["landuse_desc"] = lu

    dist = (
        item.get("historic_district")
        or ov.get("historic_district")
        or lm.get("historic_district")
        or bm.get("historic_district")
        or ""
    )
    if dist and dist.strip().lower() not in (
        "outside city district",
        "outside historic district",
        "none",
        "null",
        "n/a",
    ):
      dist = dist.strip()
      item["historic_district"] = dist
    else:
      dist = ""
      item.pop("historic_district", None)

    gb = item.get("good_brick_years") or lm.get("good_brick_years") or ""
    if gb:
      item["good_brick_years"] = gb

    lm_code = item.get("landmark_code") or lm.get("landmark_code") or ""
    if lm_code:
      item["landmark_code"] = lm_code

    # Ensure sublabel cleanly displays address / district / style or building class / year built
    addr = (
        ov.get("address")
        or lm.get("address")
        or bm.get("address")
        or sm.get("address")
        or ""
    )
    disp_style = item.get("style") or item.get("bld_style") or ""
    if disp_style in ("Residential", "Historical / Architectural Structure"):
      disp_style = ""
    parts = []
    if (
        addr
        and addr.lower() != str(item.get("label") or "").strip().lower()
    ):
      parts.append(addr)
    if dist:
      parts.append(dist)
    elif uc:
      parts.append(uc)
    if disp_style:
      parts.append(disp_style)
    if arch:
      parts.append(f"Arch: {arch}")
    if yr >= 1820:
      parts.append(f"Built {yr}")
    if parts:
      item["sublabel"] = " • ".join(parts)

    if yr >= 1820 and (
        not item.get("category")
        or item.get("category")
        in (
            "Contributing",
            "Non-Contributing",
            "Outside Historic District",
            "Residential",
        )
    ):
      item["category"] = f"Built {yr}"

  with open(SEARCH_INDEX_PATH, "w") as f:
    json.dump(search_index, f, separators=(",", ":"))
  print(f"Saved {len(search_index)} clean items to search_index.json.")


if __name__ == "__main__":
  main()
