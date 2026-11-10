#!/usr/bin/env python3
# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Zero-dependency CLI and AI Code-Interpreter helper for The Houston Building Atlas.

Queries local `app/public/data/` shards (when available) or live HTTPS shards from
the canonical Atlas base URL (`atlas_environments.json`), joins multi-shard property
records (chronology, overrides, decoded permits, 2005-2026 ownership chain, HPO
district survey rows, TARO/HPL archival citations, HAIF threads, 1866-1926 City
Directories, Lost Houston vanished structures, and active demolition watchlist
status), and outputs a citation-grade Markdown dossier or JSON payload.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
import urllib.error
import urllib.parse
import urllib.request
import zlib

DEFAULT_BASE_URL = "https://davemorris-gcp.github.io/houston-building-atlas/"
USER_AGENT = "HoustonBuildingAtlas-AICompanion/1.0"

STREET_SUFFIX_MAP = {
    "STREET": "ST",
    "AVENUE": "AVE",
    "BOULEVARD": "BLVD",
    "DRIVE": "DR",
    "ROAD": "RD",
    "LANE": "LN",
    "COURT": "CT",
    "PLACE": "PL",
    "PARKWAY": "PKWY",
    "PKY": "PKWY",
    "FREEWAY": "FWY",
    "HIGHWAY": "HWY",
    "CIRCLE": "CIR",
    "TRAIL": "TRL",
    "TERRACE": "TER",
    "SQUARE": "SQ",
    "PLAZA": "PLZ",
    "NORTH": "N",
    "SOUTH": "S",
    "EAST": "E",
    "WEST": "W",
}

CDU_GRADE_LABELS = {
    "EX": "Excellent",
    "VG": "Very Good",
    "GD": "Good",
    "AV": "Average",
    "FR": "Fair",
    "PR": "Poor",
    "UN": "Unsound",
    "VP": "Very Poor",
}


def normalize_address(raw: str) -> str:
    """Normalize a Houston street address for shard and catalog matching."""
    txt = str(raw or "").upper().split(";")[0]
    txt = re.sub(r",\s*HOUSTON\b.*$", "", txt)
    txt = re.sub(r",\s*TX\b.*$", "", txt)
    txt = re.sub(r"[.,#]", " ", txt)
    tokens = [STREET_SUFFIX_MAP.get(tok, tok) for tok in txt.split() if tok]
    return " ".join(tokens)


def parse_street_number_and_name(raw: str) -> tuple[str, str]:
    """Split a normalized street address into `(house_number, street_name)`."""
    norm = normalize_address(raw)
    m = re.match(r"^(\d+)\s+(.*)$", norm)
    if not m:
        return "", norm
    return m.group(1), m.group(2).strip()


def decode_gzip_bytes(raw_bytes: bytes) -> bytes:
    """Decompress either standard RFC 1952 gzip or raw headerless deflate bytes."""
    try:
        return zlib.decompress(raw_bytes, zlib.MAX_WBITS | 32)
    except zlib.error:
        return zlib.decompress(raw_bytes, -zlib.MAX_WBITS)


def decode_hcad_shorthand(text: str) -> str:
    """Translate common HCAD appraiser shorthand and COH permit codes into plain English."""
    s = str(text or "").strip()
    if not s:
        return ""
    notes: list[str] = []
    if re.search(r"\bNC\b", s, re.I):
        notes.append("New Construction (NC)")
    cdu_m = re.search(r"\bCDU\s*(?:@|:|=|\s)\s*(EX|VG|GD|AV|FR|PR|UN|VP)\b", s, re.I)
    if cdu_m:
        code = cdu_m.group(1).upper()
        notes.append(f"Condition/Desirability/Utility rated {CDU_GRADE_LABELS.get(code, code)} ({code})")
    if re.search(r"\bREMOD(?:EL)?\b", s, re.I):
        notes.append("Remodel / Renovation")
    if re.search(r"\bADDN\b|\bADDITION\b", s, re.I):
        notes.append("Building Addition")
    if re.search(r"\bBLD\s+DEMO\s*/\s*SEWER\s+DISC\b", s, re.I):
        notes.append("Stage 1 of 2 Demolition Prep: Sewer Disconnection Plumbing Permit")
    elif re.search(r"\bBLD\s+DEMO\b", s, re.I):
        notes.append("Stage 2 of 2 Structural Building Demolition Permit")
    if re.search(r"\bGARAGE\s+APT\s+ONLY\b", s, re.I):
        notes.append("Accessory Garage Apartment Only (main structure not demolished)")
    elif re.search(r"\bGARAGE\s+ONLY\b", s, re.I):
        notes.append("Accessory Garage Only (main structure not demolished)")
    return "; ".join(notes)


def parse_owner_history_wire(wire: str) -> list[dict[str, str]]:
    """Decode compact `2022-2026~20220311~R2022-141051~OWNER|...` wire string."""
    if not wire or not isinstance(wire, str):
        return []
    tenures: list[dict[str, str]] = []
    for seg in wire.split("|"):
        parts = seg.split("~")
        if len(parts) >= 4:
            tenures.append({
                "roll_years": parts[0].strip(),
                "deed_date": parts[1].strip(),
                "clerk_file_num": parts[2].strip(),
                "owner_name": "~".join(parts[3:]).strip(),
            })
    return tenures


class AtlasDataClient:
    """Reads Houston Building Atlas JSON and `.json.gz` shards from local disk or HTTPS."""

    def __init__(self, data_dir: Path | None = None, base_url: str | None = None) -> None:
        self.data_dir = self._discover_local_data_dir(data_dir)
        self._cache: dict[str, Any] = {}
        self.base_url = self._resolve_base_url(base_url)

    @staticmethod
    def _discover_local_data_dir(explicit_dir: Path | None) -> Path | None:
        if explicit_dir and explicit_dir.is_dir():
            return explicit_dir
        env_dir = os.environ.get("ATLAS_DATA_DIR", "").strip()
        if env_dir and Path(env_dir).is_dir():
            return Path(env_dir)
        here = Path(__file__).resolve()
        for parent in [here.parent, *here.parents]:
            cand1 = parent / "app" / "public" / "data"
            if cand1.is_dir():
                return cand1
            cand2 = parent / "data"
            if (cand2 / "overlays.json").is_file():
                return cand2
        return None

    def _resolve_base_url(self, explicit_url: str | None) -> str:
        raw = explicit_url or os.environ.get("ATLAS_BASE_URL", "").strip()
        if raw:
            return raw if raw.endswith("/") else f"{raw}/"
        if self.data_dir and (self.data_dir / "atlas_environments.json").is_file():
            try:
                env_doc = json.loads((self.data_dir / "atlas_environments.json").read_text(encoding="utf-8"))
                canon = str(env_doc.get("canonical_base_url") or "").strip()
                if canon:
                    return canon if canon.endswith("/") else f"{canon}/"
            except (OSError, ValueError):
                pass
        return DEFAULT_BASE_URL

    def load_json(self, rel_path: str, default: Any = None) -> Any:
        """Load a plain `.json` or compressed `.json.gz` relative to `app/public/data/`."""
        if rel_path in self._cache:
            return self._cache[rel_path]

        is_gz = rel_path.endswith(".gz")
        if self.data_dir:
            local_file = self.data_dir / rel_path
            if local_file.is_file():
                try:
                    raw = local_file.read_bytes()
                    text = decode_gzip_bytes(raw).decode("utf-8") if is_gz else raw.decode("utf-8")
                    val = json.loads(text)
                    self._cache[rel_path] = val
                    return val
                except (OSError, ValueError, zlib.error):
                    pass

        url = f"{self.base_url}public/data/{rel_path.lstrip('/')}"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read()
                text = decode_gzip_bytes(raw).decode("utf-8") if is_gz else raw.decode("utf-8")
                val = json.loads(text)
                self._cache[rel_path] = val
                return val
        except (urllib.error.URLError, OSError, ValueError, zlib.error):
            return default

    def lookup_address(self, address_query: str) -> dict[str, Any]:
        """Resolve a Houston street address across shards, overrides, Watchlist, and Lost Houston."""
        norm_full = normalize_address(address_query)
        house_num, street_name = parse_street_number_and_name(address_query)
        matched_hcads: list[str] = []
        address_shard_hits: list[dict[str, Any]] = []
        lost_hits: list[dict[str, Any]] = []
        watchlist_hits: list[dict[str, Any]] = []

        # 1. Check 2-digit address shard (`address_shards/addr_<2digits>.json.gz`)
        if house_num:
            prefix2 = house_num[:2].zfill(2)
            shard = self.load_json(f"address_shards/addr_{prefix2}.json.gz", {})
            if isinstance(shard, dict):
                street_key = re.sub(r"\s+", " ", street_name).strip()
                for cand_street, rows in shard.items():
                    cand_norm = re.sub(r"^\d+\s+", "", normalize_address(cand_street)).strip()
                    if cand_norm == street_key or street_key in cand_norm:
                        for row in rows or []:
                            if isinstance(row, list) and len(row) >= 5 and str(row[0]) == house_num:
                                hcad = str(row[4]).strip()
                                address_shard_hits.append({
                                    "address": f"{house_num} {cand_norm}",
                                    "lng": row[1],
                                    "lat": row[2],
                                    "shard_year": row[3],
                                    "hcad_num": hcad,
                                })
                                if hcad and hcad not in matched_hcads:
                                    matched_hcads.append(hcad)

        # 2. Check curated_overrides.json (handles alternate/cultural addresses like 333 S Jensen Dr)
        overrides_doc = self.load_json("curated_overrides.json", {})
        overrides = (
            overrides_doc.get("overrides")
            if isinstance(overrides_doc, dict) and "overrides" in overrides_doc
            else overrides_doc
        ) or {}
        for key, ov in overrides.items():
            if not isinstance(ov, dict):
                continue
            ov_addr = normalize_address(ov.get("address") or "")
            if ov_addr and (ov_addr == norm_full or (house_num and street_name and ov_addr.startswith(f"{house_num} ") and street_name in ov_addr)):
                base_hcad = str(ov.get("hcad_num") or key).split("#")[0].strip()
                if re.match(r"^\d{13}$", base_hcad) and base_hcad not in matched_hcads:
                    matched_hcads.append(base_hcad)

        # 3. Check Property Watchlist (primary and secondary perimeter addresses)
        wl_doc = self.load_json("property_watchlist.json", {})
        for wp in (wl_doc.get("watchedProperties") if isinstance(wl_doc, dict) else None) or []:
            if not isinstance(wp, dict) or wp.get("removedFromWatchlist"):
                continue
            all_addrs = [wp.get("address") or ""] + list(wp.get("secondaryAddresses") or [])
            if any(normalize_address(a) == norm_full for a in all_addrs if a):
                watchlist_hits.append(wp)
                whcad = str(wp.get("hcadAccount") or "").strip()
                if re.match(r"^\d{13}$", whcad) and whcad not in matched_hcads:
                    matched_hcads.append(whcad)

        # 4. Check Lost Houston (`overlays.json -> lost_houston`)
        overlays = self.load_json("overlays.json", {})
        lost_fc = ((overlays.get("lost_houston") if isinstance(overlays, dict) else None) or {}).get("features") or []
        for feat in lost_fc:
            props = feat.get("properties") or {}
            laddr = normalize_address(props.get("address") or "")
            lhcad = str(props.get("hcad_num") or "").strip()
            if (laddr and laddr == norm_full) or (lhcad and lhcad in matched_hcads):
                lost_hits.append(props)
                if re.match(r"^\d{13}$", lhcad) and lhcad not in matched_hcads:
                    matched_hcads.append(lhcad)

        dossiers = [self.build_hcad_dossier(h) for h in matched_hcads[:5]]
        return {
            "query": address_query,
            "normalized_address": norm_full,
            "matched_hcads": matched_hcads,
            "address_shard_hits": address_shard_hits,
            "dossiers": dossiers,
            "lost_houston_matches": lost_hits,
            "watchlist_matches": watchlist_hits,
        }

    def build_hcad_dossier(self, raw_hcad: str) -> dict[str, Any]:
        """Assemble a complete multi-shard property research dossier for a 13-digit HCAD account."""
        hcad = str(raw_hcad or "").split("#")[0].strip().zfill(13)
        prefix3 = hcad[:3]

        overrides_doc = self.load_json("curated_overrides.json", {})
        overrides = (
            overrides_doc.get("overrides")
            if isinstance(overrides_doc, dict) and "overrides" in overrides_doc
            else overrides_doc
        ) or {}
        override = overrides.get(hcad) or {}

        perm_shard = self.load_json(f"permits/permits_{prefix3}.json.gz", {}) or {}
        perm_rec = perm_shard.get(hcad) if isinstance(perm_shard, dict) else None
        if not isinstance(perm_rec, dict):
            perm_rec = {}

        own_shard = self.load_json(f"owner_history/owners_{prefix3}.json.gz", {}) or {}
        own_wire = own_shard.get(hcad, "") if isinstance(own_shard, dict) else ""
        ownership_chain = parse_owner_history_wire(own_wire)

        # HPO District Survey rows
        hpo_doc = self.load_json("hpo_district_surveys.json", {}) or {}
        hpo_ids = (hpo_doc.get("by_hcad") or {}).get(hcad) or []
        hpo_items = hpo_doc.get("items") or {}
        district_order = hpo_doc.get("district_order") or []
        districts_meta = hpo_doc.get("districts") or {}
        hpo_rows: list[dict[str, Any]] = []
        for sid in hpo_ids:
            row = hpo_items.get(sid)
            if isinstance(row, dict):
                d_idx = row.get("d")
                d_name = (
                    district_order[d_idx]
                    if isinstance(d_idx, int) and 0 <= d_idx < len(district_order)
                    else ""
                )
                d_info = districts_meta.get(d_name) or {}
                hpo_rows.append({
                    "district_name": d_name,
                    "address": row.get("a") or "",
                    "contributing_status": {
                        "C": "Contributing",
                        "NC": "Non-Contributing",
                        "PC": "Potentially Contributing",
                    }.get(str(row.get("st") or ""), str(row.get("st") or "")),
                    "survey_year": row.get("y"),
                    "style": row.get("sy") or "",
                    "legal_description": row.get("lg") or "",
                    "design_guidelines_url": d_info.get("design_guidelines_url") or "",
                    "inventory_pdf_url": d_info.get("inventory_pdf_url") or "",
                })

        # Archival citations (TARO 2.0 + HPL ContentDM)
        arch_doc = self.load_json("archival_index.json", {}) or {}
        arch_ids = (arch_doc.get("by_key") or {}).get(hcad) or []
        arch_items = arch_doc.get("items") or {}
        arch_cols = arch_doc.get("collections") or {}
        archival_citations: list[dict[str, Any]] = []
        for aid in arch_ids:
            aitem = arch_items.get(aid)
            if isinstance(aitem, dict):
                col = arch_cols.get(aitem.get("c") or "") or {}
                archival_citations.append({
                    "collection_id": aitem.get("c") or "",
                    "repository": col.get("repo") or col.get("repository") or aitem.get("c") or "Archive",
                    "collection_title": col.get("title") or "",
                    "item_title": aitem.get("t") or "",
                    "box_folder": aitem.get("b") or "",
                    "date": aitem.get("d") or "",
                    "url": aitem.get("u") or col.get("url") or "",
                })

        # 1866-1926 City Directory index
        cdir_doc = self.load_json("citydir_index.json", {}) or {}
        cdir_hit = (cdir_doc.get("by_hcad") or {}).get(hcad)
        absent_1926 = hcad in set(cdir_doc.get("absent_through_1926") or [])

        # HAIF threads
        haif_doc = self.load_json("haif_index.json", {}) or {}
        haif_tids = (haif_doc.get("by_key") or {}).get(hcad) or []
        haif_threads_map = haif_doc.get("threads") or {}
        haif_threads: list[dict[str, Any]] = []
        for tid in haif_tids[:8]:
            tmeta = haif_threads_map.get(str(tid))
            if isinstance(tmeta, dict):
                haif_threads.append({
                    "title": tmeta.get("t") or "",
                    "forum": tmeta.get("f") or "",
                    "url": tmeta.get("u") or "",
                    "wayback_url": tmeta.get("w") or "",
                })

        # Overlay designations (Landmarks, NRHP, THC Markers, Good Brick, Lost Houston)
        overlays = self.load_json("overlays.json", {}) or {}
        designations: list[dict[str, Any]] = []
        lost_records: list[dict[str, Any]] = []
        for lkey in ("landmarks", "nrhp_properties", "thc_markers", "good_brick_awards"):
            fc = (overlays.get(lkey) or {}).get("features") or []
            for feat in fc:
                props = feat.get("properties") or {}
                fhcad = str(props.get("hcad_num") or "").split("#")[0].strip()
                if fhcad == hcad:
                    designations.append({"layer": lkey, **props})

        for feat in ((overlays.get("lost_houston") or {}).get("features") or []):
            props = feat.get("properties") or {}
            if str(props.get("hcad_num") or "").strip() == hcad:
                lost_records.append(props)

        # Active permit alerts & Watchlist
        alerts_doc = self.load_json("active_permit_alerts.json", {}) or {}
        active_alerts = [
            al
            for al in (alerts_doc.get("alerts") or [])
            if isinstance(al, dict) and str(al.get("hcad_num") or "").split("#")[0].strip() == hcad
        ]
        wl_doc = self.load_json("property_watchlist.json", {}) or {}
        watchlist_entry = next(
            (
                wp
                for wp in (wl_doc.get("watchedProperties") or [])
                if isinstance(wp, dict) and str(wp.get("hcadAccount") or "").strip() == hcad
            ),
            None,
        )

        # Decoded permits & extra features
        decoded_permits: list[dict[str, Any]] = []
        for p in (perm_rec.get("p") or [])[:25]:
            if isinstance(p, dict):
                raw_desc = str(p.get("d") or "")
                decoded_permits.append({
                    "id": p.get("id") or "",
                    "date": p.get("dt") or "",
                    "type": p.get("tp") or "",
                    "category": p.get("cat") or "",
                    "verbatim_description": raw_desc,
                    "plain_english": decode_hcad_shorthand(raw_desc),
                })

        raw_owners = [str(o).strip() for o in (perm_rec.get("co") or []) if str(o).strip()]
        raw_owner_str = "; ".join(raw_owners)
        is_confidential = bool(
            perm_rec.get("conf") == 1
            or perm_rec.get("confidential")
            or override.get("conf") == 1
            or override.get("confidential")
            or re.search(r"(?:^confidential\b|25\.025)", raw_owner_str, re.I)
        )
        year_built = override.get("year_built") or perm_rec.get("yr")
        year_source = (
            override.get("year_source")
            or ("Curated Archival Override (Tier 2)" if override.get("year_built") else "HCAD Appraisal Roll (Tier 3)")
        )
        address = (
            override.get("address")
            or (designations[0].get("address") if designations else "")
            or (lost_records[0].get("address") if lost_records else "")
            or (watchlist_entry.get("address") if isinstance(watchlist_entry, dict) else "")
            or (hpo_rows[0].get("address") if hpo_rows else "")
        )
        building_name = (
            override.get("building_name")
            or (designations[0].get("name") if designations else "")
            or (lost_records[0].get("name") if lost_records else "")
            or (watchlist_entry.get("propertyName") if isinstance(watchlist_entry, dict) else "")
        )
        if is_confidential and building_name:
            for secret_name in raw_owners + [t.get("owner_name", "") for t in ownership_chain]:
                if (
                    secret_name
                    and not re.match(r"^confidential\b", secret_name, re.I)
                    and secret_name.lower() in building_name.lower()
                ):
                    building_name = address or f"HCAD {hcad}"
                    break
        architect = (
            override.get("architect")
            or (designations[0].get("architect") if designations else "")
            or (lost_records[0].get("architect") if lost_records else "")
        )
        style = (
            override.get("style")
            or (designations[0].get("style") if designations else "")
            or (hpo_rows[0].get("style") if hpo_rows else "")
            or (lost_records[0].get("style") if lost_records else "")
        )
        is_demolished = bool(
            override.get("suppress_only")
            or override.get("is_demolished")
            or lost_records
            or (isinstance(watchlist_entry, dict) and watchlist_entry.get("trackerStatus") == "Lost")
        )

        links = {
            "atlas_overview": f"{self.base_url}#hcad={hcad}",
            "atlas_permits_tab": f"{self.base_url}#hcad={hcad}&tab=permits",
            "atlas_history_tab": f"{self.base_url}#hcad={hcad}&tab=history",
            "thc_survey_form": f"{self.base_url}#hcad={hcad}&thcSurvey=1",
            "ch42_calculator": f"{self.base_url}#hcad={hcad}&ch42={hcad}",
            "hcad_portal": "https://search.hcad.org/",
            "county_clerk_rp": "https://www.cclerk.hctx.net/Applications/WebSearch/RP.aspx",
        }
        if lost_records:
            lid = lost_records[0].get("id") or ""
            if lid:
                links["atlas_lost_houston"] = f"{self.base_url}?id={lid}&layers=lost_houston"
        if isinstance(watchlist_entry, dict) and watchlist_entry.get("id"):
            links["plat_monitor_watchlist"] = (
                f"{self.base_url}plat-monitor.html#tab=watchlist&watch={watchlist_entry['id']}"
            )

        return {
            "hcad_num": hcad,
            "building_name": building_name,
            "address": address,
            "year_built": year_built,
            "year_source": year_source,
            "remodel_year": perm_rec.get("remod"),
            "architect": architect,
            "style": style,
            "is_demolished": is_demolished,
            "demolished_date": override.get("demolished_date")
            or (watchlist_entry.get("demolishedDate") if isinstance(watchlist_entry, dict) else None),
            "confidential_sec_25_025": is_confidential,
            "ownership_tier": perm_rec.get("ot") or "",
            "current_owners": [] if is_confidential else (perm_rec.get("co") or []),
            "ownership_chain_2005_2026": [] if is_confidential else ownership_chain,
            "appraiser_note_verbatim": perm_rec.get("note") or "",
            "appraiser_note_plain_english": decode_hcad_shorthand(perm_rec.get("note") or ""),
            "permits": decoded_permits,
            "multi_agency_filings": perm_rec.get("multi_agency_filings") or [],
            "hpo_district_survey_rows": hpo_rows,
            "archival_citations": archival_citations,
            "city_directory_1866_1926": cdir_hit,
            "absent_in_1926_city_directory": absent_1926,
            "haif_threads": haif_threads,
            "designations": designations,
            "lost_houston_records": lost_records,
            "active_permit_alerts": active_alerts,
            "watchlist_entry": watchlist_entry,
            "narrative": override.get("notes") or override.get("history") or "",
            "links": links,
        }

    def search_keyword(self, query: str) -> dict[str, Any]:
        """Search across overrides, landmarks, Lost Houston, watchlist, and search_index.json."""
        q = str(query or "").strip().lower()
        matches: list[dict[str, Any]] = []
        seen_keys: set[str] = set()

        overrides_doc = self.load_json("curated_overrides.json", {})
        overrides = (
            overrides_doc.get("overrides")
            if isinstance(overrides_doc, dict) and "overrides" in overrides_doc
            else overrides_doc
        ) or {}
        for k, ov in overrides.items():
            if not isinstance(ov, dict):
                continue
            hay = " ".join(
                str(ov.get(f) or "")
                for f in ("building_name", "address", "architect", "style", "notes")
            ).lower()
            if q in hay:
                hcad = str(ov.get("hcad_num") or k).split("#")[0].strip()
                if hcad not in seen_keys:
                    seen_keys.add(hcad)
                    matches.append({
                        "kind": "curated_override",
                        "hcad_num": hcad,
                        "name": ov.get("building_name") or "",
                        "address": ov.get("address") or "",
                        "year_built": ov.get("year_built"),
                        "is_demolished": bool(ov.get("suppress_only") or ov.get("is_demolished")),
                        "link": f"{self.base_url}#hcad={hcad}",
                    })

        overlays = self.load_json("overlays.json", {}) or {}
        for lkey in ("landmarks", "lost_houston", "nrhp_properties", "good_brick_awards", "thc_markers"):
            for feat in ((overlays.get(lkey) or {}).get("features") or []):
                props = feat.get("properties") or {}
                hay = " ".join(
                    str(props.get(f) or "")
                    for f in ("name", "address", "architect", "style", "description")
                ).lower()
                if q in hay:
                    ident = str(props.get("id") or props.get("hcad_num") or props.get("name") or "")
                    if ident not in seen_keys:
                        seen_keys.add(ident)
                        hcad = str(props.get("hcad_num") or "").split("#")[0].strip()
                        link = (
                            f"{self.base_url}?id={props.get('id')}&layers=lost_houston"
                            if lkey == "lost_houston" and props.get("id")
                            else f"{self.base_url}#hcad={hcad}"
                            if hcad
                            else self.base_url
                        )
                        matches.append({
                            "kind": lkey,
                            "id": ident,
                            "hcad_num": hcad,
                            "name": props.get("name") or "",
                            "address": props.get("address") or "",
                            "year_built": props.get("year_built") or props.get("built_year"),
                            "demolished_year": props.get("demolished_year"),
                            "link": link,
                        })

        top_dossier = None
        for m in matches:
            hcad_cand = str(m.get("hcad_num") or "")
            if re.match(r"^\d{13}$", hcad_cand):
                top_dossier = self.build_hcad_dossier(hcad_cand)
                break

        return {
            "query": query,
            "match_count": len(matches),
            "matches": matches[:20],
            "top_dossier": top_dossier,
        }

    def lookup_portfolio(self, query: str) -> dict[str, Any]:
        """Look up an architect, builder, or architectural style portfolio."""
        q = str(query or "").strip()
        q_low = re.sub(r"^(arch_|style_)", "", q.lower()).replace("_", " ").strip()
        port_doc = self.load_json("portfolio-metadata.json", {}) or {}
        matched_meta = None
        matched_name = q
        matched_kind = "architect"

        for kind_key, label in (("architects", "architect"), ("styles", "style")):
            bucket = port_doc.get(kind_key) or {}
            for name, meta in bucket.items():
                if name.lower() == q_low or q_low in name.lower():
                    matched_name = name
                    matched_meta = meta
                    matched_kind = label
                    break
            if matched_meta:
                break

        slug_prefix = "style_" if matched_kind == "style" else "arch_"
        slug = slug_prefix + re.sub(r"[^a-z0-9]+", "_", matched_name.lower()).strip("_")

        extant_works: list[dict[str, Any]] = []
        lost_works: list[dict[str, Any]] = []
        seen_extant_hcads: set[str] = set()

        overrides_doc = self.load_json("curated_overrides.json", {})
        overrides = (
            overrides_doc.get("overrides")
            if isinstance(overrides_doc, dict) and "overrides" in overrides_doc
            else overrides_doc
        ) or {}
        suppressed_hcads = {
            str(ov.get("hcad_num") or k).split("#")[0].strip()
            for k, ov in overrides.items()
            if isinstance(ov, dict) and (ov.get("suppress_only") or ov.get("is_demolished"))
        }

        overlays = self.load_json("overlays.json", {}) or {}
        for feat in ((overlays.get("lost_houston") or {}).get("features") or []):
            props = feat.get("properties") or {}
            lhcad = str(props.get("hcad_num") or "").strip()
            if lhcad:
                suppressed_hcads.add(lhcad)
            field_val = str(props.get("style" if matched_kind == "style" else "architect") or "")
            if matched_name.lower() in field_val.lower():
                lid = props.get("id") or ""
                lost_works.append({
                    "id": lid,
                    "name": props.get("name") or "",
                    "address": props.get("address") or "",
                    "built_year": props.get("built_year") or props.get("year_built"),
                    "demolished_year": props.get("demolished_year") or props.get("year_demolished"),
                    "style": props.get("style") or "",
                    "link": f"{self.base_url}?id={lid}&layers=lost_houston",
                })

        for k, ov in overrides.items():
            if not isinstance(ov, dict) or "#" in str(k):
                continue
            hcad = str(ov.get("hcad_num") or k).split("#")[0].strip()
            if hcad in suppressed_hcads:
                continue
            field_val = str(ov.get("style" if matched_kind == "style" else "architect") or "")
            if matched_name.lower() in field_val.lower():
                seen_extant_hcads.add(hcad)
                extant_works.append({
                    "hcad_num": hcad,
                    "name": ov.get("building_name") or "",
                    "address": ov.get("address") or "",
                    "year_built": ov.get("year_built"),
                    "style": ov.get("style") or "",
                    "link": f"{self.base_url}#hcad={hcad}",
                })

        for feat in ((overlays.get("landmarks") or {}).get("features") or []):
            props = feat.get("properties") or {}
            if props.get("demolished"):
                continue
            hcad = str(props.get("hcad_num") or "").split("#")[0].strip()
            if not hcad or hcad in suppressed_hcads or hcad in seen_extant_hcads:
                continue
            field_val = str(props.get("style" if matched_kind == "style" else "architect") or "")
            if matched_name.lower() in field_val.lower():
                seen_extant_hcads.add(hcad)
                extant_works.append({
                    "hcad_num": hcad,
                    "name": props.get("name") or "",
                    "address": props.get("address") or "",
                    "year_built": props.get("year_built"),
                    "style": props.get("style") or "",
                    "link": f"{self.base_url}#hcad={hcad}",
                })

        return {
            "name": matched_name,
            "kind": matched_kind,
            "slug": slug,
            "portfolio_url": f"{self.base_url}#portfolio={slug}",
            "metadata": matched_meta or {},
            "extant_count": len(extant_works),
            "lost_houston_count": len(lost_works),
            "extant_works": sorted(extant_works, key=lambda x: int(x.get("year_built") or 9999)),
            "lost_houston_works": sorted(lost_works, key=lambda x: int(x.get("built_year") or 9999)),
        }

    def lookup_scorecard(self, query: str) -> dict[str, Any]:
        """Look up a neighborhood, historic district, or council district preservation scorecard."""
        q_low = str(query or "").strip().lower()
        sc_doc = self.load_json("neighborhood_scorecards.json", {}) or {}
        matches: list[dict[str, Any]] = []
        seen_keys: set[str] = set()

        # 1. Array schema (`scorecards` + `citywide_scorecard` in neighborhood_scorecards.json)
        card_items: list[dict[str, Any]] = []
        if isinstance(sc_doc.get("citywide_scorecard"), dict):
            card_items.append(sc_doc["citywide_scorecard"])
        if isinstance(sc_doc.get("scorecards"), list):
            card_items.extend(c for c in sc_doc["scorecards"] if isinstance(c, dict))
        for card in card_items:
            cid = str(card.get("id") or "").strip()
            layer_key = str(card.get("ly") or card.get("layer") or "neighborhoods").strip()
            cname = str(card.get("nm") or card.get("name") or cid).strip()
            if not cid:
                continue
            if q_low in cname.lower() or q_low in cid.lower():
                dedup_key = f"{layer_key}:{cid}"
                if dedup_key not in seen_keys:
                    seen_keys.add(dedup_key)
                    matches.append({
                        "layer": layer_key,
                        "id": cid,
                        "name": cname,
                        "scorecard_url": f"{self.base_url}#scorecard={layer_key}:{cid}",
                        "metrics": card,
                    })

        # 2. Nested `layers` dict fallback
        layers = sc_doc.get("layers") or {}
        if isinstance(layers, dict):
            for layer_key, cards in layers.items():
                if not isinstance(cards, dict):
                    continue
                for cid, card in cards.items():
                    if not isinstance(card, dict):
                        continue
                    cname = str(card.get("name") or card.get("nm") or cid)
                    dedup_key = f"{layer_key}:{cid}"
                    if dedup_key not in seen_keys and (q_low in cname.lower() or q_low in cid.lower()):
                        seen_keys.add(dedup_key)
                        matches.append({
                            "layer": layer_key,
                            "id": cid,
                            "name": cname,
                            "scorecard_url": f"{self.base_url}#scorecard={layer_key}:{cid}",
                            "metrics": card,
                        })
        return {
            "query": query,
            "match_count": len(matches),
            "matches": matches[:10],
        }


def format_dossier_markdown(dossier: dict[str, Any]) -> str:
    """Format a property research dossier as clean citation-grade Markdown."""
    hcad = dossier.get("hcad_num") or ""
    title = dossier.get("building_name") or dossier.get("address") or f"HCAD {hcad}"
    status_badge = (
        f"DEMOLISHED ({dossier.get('demolished_date') or 'Lost Houston'})"
        if dossier.get("is_demolished")
        else "EXTANT"
    )
    lines = [
        f"### {title} ({status_badge})",
        f"- **Street Address:** {dossier.get('address') or 'N/A'}",
        f"- **HCAD Account:** `{hcad}`",
        f"- **Construction Year:** `{dossier.get('year_built') or 'Unknown'}` ({dossier.get('year_source')})",
    ]
    if dossier.get("remodel_year"):
        lines.append(f"- **Remodel / Effective Year:** `{dossier.get('remodel_year')}`")
    if dossier.get("architect"):
        lines.append(f"- **Architect / Builder:** {dossier.get('architect')}")
    if dossier.get("style"):
        lines.append(f"- **Architectural Style:** {dossier.get('style')}")
    if dossier.get("narrative"):
        lines.append(f"- **Archival Summary:** {dossier.get('narrative')}")

    if dossier.get("designations"):
        des_labels = [
            f"{d.get('layer')}: {d.get('name') or ''} ({d.get('protection_tier') or d.get('refnum') or ''})".strip()
            for d in dossier["designations"]
        ]
        lines.append(f"- **Preservation Designations:** {'; '.join(des_labels)}")

    if dossier.get("hpo_district_survey_rows"):
        for r in dossier["hpo_district_survey_rows"]:
            lines.append(
                f"- **COH HPO Historic District Survey:** {r.get('district_name')} - **{r.get('contributing_status')}** (Survey Year: `{r.get('survey_year')}`, Style: {r.get('style')})"
            )

    if dossier.get("city_directory_1866_1926"):
        cd = dossier["city_directory_1866_1926"]
        lines.append(
            f"- **1866-1926 Houston City Directory:** Earliest verified listing `{cd.get('e')}` ({cd.get('c') or ''})"
        )
    elif dossier.get("absent_in_1926_city_directory"):
        lines.append(
            "- **1866-1926 Houston City Directory:** Audited absent through the 1926 directory (lot unimproved in 1926)."
        )

    if dossier.get("confidential_sec_25_025"):
        lines.append(
            "- **Ownership (TX Tax Code Sec. 25.025):** Statutory Confidential Owner (`conf: 1`)."
        )
    elif dossier.get("ownership_chain_2005_2026"):
        lines.append("- **22-Year Certified HCAD Ownership Chain (2005-2026):**")
        for t in dossier["ownership_chain_2005_2026"][:8]:
            clerk = f", Clerk File `{t['clerk_file_num']}`" if t.get("clerk_file_num") else ""
            ddate = f", Deed `{t['deed_date']}`" if t.get("deed_date") else ""
            lines.append(f"  - `{t['roll_years']}`: {t['owner_name']}{ddate}{clerk}")

    if dossier.get("appraiser_note_verbatim"):
        lines.append(
            f"- **HCAD Appraiser Note:** `{dossier['appraiser_note_verbatim']}`"
            + (
                f" -> *Plain English: {dossier['appraiser_note_plain_english']}*"
                if dossier.get("appraiser_note_plain_english")
                else ""
            )
        )

    if dossier.get("multi_agency_filings"):
        lines.append("- **Multi-Agency Demolition / Permit Watch Filings:**")
        for mf in dossier["multi_agency_filings"]:
            lines.append(
                f"  - `{mf.get('date')}` [{mf.get('agency')}] **{mf.get('type')}** ({mf.get('reference')}): {mf.get('summary')}"
            )

    if dossier.get("permits"):
        lines.append("- **Recorded Building Permits:**")
        for p in dossier["permits"][:10]:
            pe = f" (*{p['plain_english']}*)" if p.get("plain_english") else ""
            lines.append(
                f"  - `{p.get('date')}` [{p.get('type')}] `#{p.get('id')}`: `{p.get('verbatim_description')}`{pe}"
            )

    if dossier.get("archival_citations"):
        lines.append("- **Linked TARO 2.0 & Houston Public Library Archival Citations:**")
        for ac in dossier["archival_citations"][:8]:
            bf = f" ({ac['box_folder']})" if ac.get("box_folder") else ""
            lines.append(
                f"  - [{ac.get('repository')}: {ac.get('item_title')}]({ac.get('url')}){bf}"
            )

    links = dossier.get("links") or {}
    lines.extend([
        "- **Verified Deep Links:**",
        f"  - [Open Property in Houston Building Atlas]({links.get('atlas_overview')})",
        f"  - [Open Permits & Appraiser Shorthand Tab]({links.get('atlas_permits_tab')})",
        f"  - [Open 1836-2026 Ownership & Stewardship Tab]({links.get('atlas_history_tab')})",
        f"  - [Open 1-Click Printable THC Historic Resources Survey Form]({links.get('thc_survey_form')})",
    ])
    if links.get("atlas_lost_houston"):
        lines.append(f"  - [View Demolished Footprint in Lost Houston]({links['atlas_lost_houston']})")
    if links.get("plat_monitor_watchlist"):
        lines.append(f"  - [Open 15-Point Watchlist Dossier in Plat & Property Monitor]({links['plat_monitor_watchlist']})")
    return "\n".join(lines).replace("\u2014", "-").replace("\u2013", "-")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Query The Houston Building Atlas across 37 municipal, county, and archival datasets."
    )
    parser.add_argument("--address", help="Look up a Houston street address (e.g. '2000 Smith St')")
    parser.add_argument("--hcad", help="Look up a 13-digit HCAD account number (e.g. '0152300000001')")
    parser.add_argument("--search", help="Keyword search across landmarks, overrides, Lost Houston, and watchlist")
    parser.add_argument("--architect", help="Look up an architect, builder, or architectural style portfolio")
    parser.add_argument("--scorecard", help="Look up a neighborhood, historic district, or council district scorecard")
    parser.add_argument("--watchlist", action="store_true", help="List active and graduated Property Watchlist dossiers")
    parser.add_argument("--data-dir", type=Path, help="Optional path to local app/public/data directory")
    parser.add_argument("--base-url", help="Optional override for canonical Atlas base URL")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON output")
    args = parser.parse_args(argv)

    client = AtlasDataClient(data_dir=args.data_dir, base_url=args.base_url)

    if args.hcad:
        dossier = client.build_hcad_dossier(args.hcad)
        if args.json:
            print(json.dumps(dossier, indent=2, ensure_ascii=False))
        else:
            print(format_dossier_markdown(dossier))
        return 0

    if args.address:
        res = client.lookup_address(args.address)
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            if res["dossiers"]:
                for d in res["dossiers"]:
                    print(format_dossier_markdown(d))
                    print()
            elif res["lost_houston_matches"]:
                for l in res["lost_houston_matches"]:
                    lid = l.get("id") or ""
                    print(
                        f"### {l.get('name')} (Lost Houston: {l.get('built_year')}-{l.get('demolished_year')})\n"
                        f"- **Address:** {l.get('address')}\n"
                        f"- **Architect / Style:** {l.get('architect') or 'N/A'} / {l.get('style') or 'N/A'}\n"
                        f"- **Link:** {client.base_url}?id={lid}&layers=lost_houston"
                    )
            else:
                print(f"No matching HCAD or Lost Houston record found for address: {args.address}")
        return 0

    if args.architect:
        res = client.lookup_portfolio(args.architect)
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            print(f"### Portfolio: {res['name']} ({res['extant_count']} Extant, {res['lost_houston_count']} Lost Houston)")
            print(f"- **Atlas Portfolio Link:** {res['portfolio_url']}")
            for w in res["extant_works"]:
                print(f"  - [Extant {w.get('year_built') or ''}] **{w.get('name') or w.get('address')}** ({w.get('address')}) -> {w.get('link')}")
            for lw in res["lost_houston_works"]:
                print(f"  - [Lost {lw.get('built_year')}-{lw.get('demolished_year')}] **{lw.get('name')}** ({lw.get('address')}) -> {lw.get('link')}")
        return 0

    if args.scorecard:
        res = client.lookup_scorecard(args.scorecard)
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            for m in res["matches"]:
                print(f"### Scorecard: {m['name']} (`{m['layer']}:{m['id']}`)")
                print(f"- **Atlas Scorecard Link:** {m['scorecard_url']}")
        return 0

    if args.watchlist:
        wl_doc = client.load_json("property_watchlist.json", {}) or {}
        props = [
            wp for wp in (wl_doc.get("watchedProperties") or [])
            if isinstance(wp, dict) and not wp.get("removedFromWatchlist")
        ]
        if args.json:
            print(json.dumps(props, indent=2, ensure_ascii=False))
        else:
            print(f"### Preservation Houston 15-Point Property Watchlist ({len(props)} Properties)")
            for wp in props:
                print(
                    f"- [{wp.get('trackerStatus')}] **{wp.get('address')}** ({wp.get('propertyName') or wp.get('platProjectName') or 'Watched Site'}, Built {wp.get('yearBuilt') or 'N/A'}) -> {client.base_url}plat-monitor.html#tab=watchlist&watch={wp.get('id')}"
                )
        return 0

    if args.search:
        res = client.search_keyword(args.search)
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            print(f"### Search Results for '{args.search}' ({res['match_count']} matches)")
            for m in res["matches"][:10]:
                print(f"- [{m.get('kind')}] **{m.get('name')}** ({m.get('address')}, `{m.get('year_built')}`) -> {m.get('link')}")
            if res.get("top_dossier"):
                print("\n---\n")
                print(format_dossier_markdown(res["top_dossier"]))
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
