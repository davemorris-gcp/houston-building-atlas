"""Apply verified City of Houston Landmark Designation Report PDF enrichments to Atlas data files."""

from __future__ import annotations

import json
import re
from pathlib import Path


def geom_area(geom: dict | None) -> float:
    if not geom:
        return 0.0
    coords = geom.get("coordinates") or []
    ring = (
        coords[0]
        if geom.get("type") == "Polygon" and coords
        else coords[0][0]
        if geom.get("type") == "MultiPolygon" and coords and coords[0]
        else []
    )
    if len(ring) < 3:
        return 0.0
    return 0.5 * abs(
        sum(
            ring[i][0] * ring[(i + 1) % len(ring)][1]
            - ring[(i + 1) % len(ring)][0] * ring[i][1]
            for i in range(len(ring))
        )
    )

CACHE_DIR = Path("pipeline/cache")
DATA_DIR = Path("app/public/data")


def main():
    enriched = json.loads((CACHE_DIR / "coh_landmarks_enriched.json").read_text())
    coh_raw = json.loads((CACHE_DIR / "coh_landmarks.json").read_text())
    overlays_path = DATA_DIR / "overlays.json"
    overrides_path = DATA_DIR / "curated_overrides.json"
    buildings_path = DATA_DIR / "buildings.geojson"
    photos_path = DATA_DIR / "building_photos.json"

    overlays = json.loads(overlays_path.read_text())
    overrides_doc = json.loads(overrides_path.read_text())
    overrides = overrides_doc.get("overrides", {})
    buildings_doc = json.loads(buildings_path.read_text())
    photos_doc = json.loads(photos_path.read_text()) if photos_path.exists() else {"photos": []}
    photos_list = photos_doc.get("photos", [])
    existing_photo_urls = {item.get("image_url") for item in photos_list}

    # Index enriched rows by objectid and by (name, address)
    by_objid = {r["objectid"]: r for r in enriched if r.get("objectid") is not None}
    by_name_addr = {(r["name"], r["address"]): r for r in enriched}

    # 1. Update overlays.json landmarks layer
    landmark_feats = overlays.get("landmarks", {}).get("features", [])
    ov_updated = 0
    for idx, feat in enumerate(landmark_feats):
        p = feat.get("properties") or {}
        raw_p = coh_raw[idx].get("properties", {}) if idx < len(coh_raw) else {}
        objid = raw_p.get("OBJECTID")
        enr = by_objid.get(objid) or by_name_addr.get((p.get("name", ""), p.get("address", "")))
        if not enr:
            continue

        if not p.get("year_built") and enr.get("final_year_built"):
            p["year_built"] = int(enr["final_year_built"])
        if not p.get("architect") and enr.get("final_architect"):
            p["architect"] = enr["final_architect"]
        if not p.get("style") and enr.get("final_style"):
            p["style"] = enr["final_style"]

        if enr.get("lm_num"):
            p["lm_num"] = enr["lm_num"]
        if enr.get("plm_num"):
            p["plm_num"] = enr["plm_num"]
        if enr.get("report_pdf_url"):
            p["report_pdf_url"] = enr["report_pdf_url"]
        if enr.get("secondary_pdf_url"):
            p["secondary_pdf_url"] = enr["secondary_pdf_url"]
        if enr.get("pdf_summary"):
            p["pdf_summary"] = enr["pdf_summary"]
        ov_updated += 1

    overlays_path.write_text(json.dumps(overlays, separators=(",", ":")))
    print(f"Updated {ov_updated} landmark features in overlays.json")

    # Build lookup of existing buildings in buildings.geojson by hcad_num and by normalized address
    def norm_addr(s: str | None) -> str:
        if not s:
            return ""
        s = s.upper().strip()
        s = re.sub(r"[^A-Z0-9\s]", " ", s)
        s = re.sub(
            r"\b(STREET|ST|AVENUE|AVE|BOULEVARD|BLVD|DRIVE|DR|ROAD|RD|LANE|LN|COURT|CT|PLACE|PL|CIRCLE|CIR|PARKWAY|PKWY|FREEWAY|FWY|HIGHWAY|HWY)\b",
            "",
            s,
        )
        return " ".join(s.split())

    blds_by_hcad: dict[str, list[dict]] = {}
    addr_to_hcads: dict[str, set[str]] = {}
    for b in buildings_doc.get("features", []):
        bp = b.get("properties") or {}
        h = str(bp.get("hcad_num") or "").strip()
        if h:
            blds_by_hcad.setdefault(h, []).append(b)
            na = norm_addr(bp.get("address"))
            if na:
                addr_to_hcads.setdefault(na, set()).add(h)

    # 2. Enrich curated_overrides.json & buildings.geojson where landmark has or resolves to a 13-digit HCAD account
    hcad_year_corrections = 0
    hcad_pdf_links_added = 0
    hcad_resolved_by_addr = 0
    photos_added = 0

    for enr in enriched:
        hcad = str(enr.get("hcad_num") or "").strip()
        if not re.match(r"^[0-9]{13}$", hcad):
            na = norm_addr(enr.get("address"))
            if na and len(addr_to_hcads.get(na, set())) == 1:
                hcad = next(iter(addr_to_hcads[na]))
                enr["hcad_num_resolved"] = hcad
                hcad_resolved_by_addr += 1
            else:
                continue

        pdf_url = enr.get("report_pdf_url") or ""
        lm_code = enr.get("plm_num") or enr.get("lm_num") or ""
        gis_yr = int(enr["gis_year_built"]) if enr.get("gis_year_built") else 0
        pdf_yr = int(enr["pdf_year_built"]) if enr.get("pdf_year_built") else 0
        auth_yr = gis_yr or pdf_yr

        blds = blds_by_hcad.get(hcad, [])
        curr_b_yr = int(blds[0]["properties"].get("year_built") or 0) if blds else 0
        ov = overrides.get(hcad)

        if pdf_url:
            hcad_pdf_links_added += 1
            for b in blds:
                b["properties"]["landmark_report_url"] = pdf_url
                if lm_code:
                    b["properties"]["landmark_code"] = lm_code
                if enr.get("pdf_summary") and not b["properties"].get("landmark_summary"):
                    b["properties"]["landmark_summary"] = enr["pdf_summary"]

        if ov:
            if pdf_url and not ov.get("landmark_report_url"):
                ov["landmark_report_url"] = pdf_url
            if lm_code and not ov.get("landmark_code"):
                ov["landmark_code"] = lm_code
            if enr.get("final_architect") and not ov.get("architect"):
                ov["architect"] = enr["final_architect"]
            if enr.get("final_style") and not ov.get("bld_style"):
                ov["bld_style"] = enr["final_style"]
            if enr.get("pdf_summary") and not ov.get("landmark_summary"):
                ov["landmark_summary"] = enr["pdf_summary"]
        elif auth_yr and (
            curr_b_yr in (0, 1900, 1910, 1920, 1930, 1940, 1950)
            and abs(auth_yr - curr_b_yr) >= 2
            or (gis_yr and curr_b_yr and abs(gis_yr - curr_b_yr) >= 3)
        ):
            best_geom = None
            if blds:
                best_b = max(
                    blds,
                    key=lambda x: geom_area(x.get("geometry")),
                )
                best_geom = best_b.get("geometry")
                for b in blds:
                    b["properties"]["original_hcad_year"] = curr_b_yr
                    b["properties"]["year_built"] = auth_yr
                    b["properties"]["is_curated_override"] = True
                    if enr.get("final_architect"):
                        b["properties"]["architect"] = enr["final_architect"]
                    if enr.get("final_style"):
                        b["properties"]["style"] = enr["final_style"]

            cite_code = f"HPO #{lm_code}" if lm_code else "COH Landmark Report"
            cite_summary = enr.get("pdf_summary") or f"Designated City of Houston Landmark ({enr['name']})."
            overrides[hcad] = {
                "hcad_num": hcad,
                "address": enr["address"],
                "landmark_name": enr["name"],
                "year_built": auth_yr,
                "original_hcad_year": curr_b_yr or None,
                "bld_style": enr.get("final_style") or "",
                "architect": enr.get("final_architect") or "",
                "source_type": "COH Landmark Designation Report",
                "source_citation": f"{cite_code}: {cite_summary}"[:360],
                "source_url": pdf_url,
                "landmark_report_url": pdf_url,
                "landmark_code": lm_code,
                "landmark_summary": enr.get("pdf_summary") or "",
                "verified_by": "City of Houston HAHC Landmark Report",
                "verified_at": "2026-10-08",
                **({"geometry": best_geom} if best_geom else {}),
            }
            hcad_year_corrections += 1

        if enr.get("photo_urls"):
            for p_url in enr["photo_urls"]:
                if p_url not in existing_photo_urls:
                    photos_list.append({
                        "hcad_num": hcad,
                        "building_id": blds[0]["properties"].get("id", hcad) if blds else hcad,
                        "landmark_name": enr["name"],
                        "address": enr["address"],
                        "photo_year": None,
                        "era_label": "COH Landmark Archive",
                        "caption": f"{enr['name']} ({enr['address']}) — Official City of Houston Landmark Designation Archive photograph ({lm_code or 'HAHC'}).",
                        "image_url": p_url,
                        "thumb_url": p_url,
                        "credit": "City of Houston Historic Preservation Office (HAHC)",
                        "source_url": pdf_url or p_url,
                    })
                    existing_photo_urls.add(p_url)
                    photos_added += 1

    overrides_doc["overrides"] = overrides
    overrides_path.write_text(json.dumps(overrides_doc, indent=2))
    buildings_path.write_text(json.dumps(buildings_doc, separators=(",", ":")))
    photos_doc["photos"] = photos_list
    photos_path.write_text(json.dumps(photos_doc, indent=2))

    print(f"Attached Landmark Report PDF links to {hcad_pdf_links_added} HCAD accounts")
    print(f"Created {hcad_year_corrections} new verified HCAD year overrides from COH Landmark Reports")
    print(f"Added {photos_added} official COH Landmark photographs to building_photos.json")


if __name__ == "__main__":
    main()
