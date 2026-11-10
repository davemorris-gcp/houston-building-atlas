#!/usr/bin/env node
// Copyright 2026 Google LLC
// SPDX-License-Identifier: Apache-2.0
/**
 * Zero-dependency Node.js (ESM) lookup script for The Houston Building Atlas.
 * Supports local `app/public/data/` shards or live HTTPS fetches from `atlas_environments.json`.
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import zlib from "node:zlib";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
export const DEFAULT_BASE_URL = "https://davemorris-gcp.github.io/houston-building-atlas/";

const STREET_SUFFIX_MAP = {
  STREET: "ST",
  AVENUE: "AVE",
  BOULEVARD: "BLVD",
  DRIVE: "DR",
  ROAD: "RD",
  LANE: "LN",
  COURT: "CT",
  PLACE: "PL",
  PARKWAY: "PKWY",
  PKY: "PKWY",
  FREEWAY: "FWY",
  HIGHWAY: "HWY",
  CIRCLE: "CIR",
  TRAIL: "TRL",
  TERRACE: "TER",
  SQUARE: "SQ",
  PLAZA: "PLZ",
  NORTH: "N",
  SOUTH: "S",
  EAST: "E",
  WEST: "W",
};

const CDU_GRADE_LABELS = {
  EX: "Excellent",
  VG: "Very Good",
  GD: "Good",
  AV: "Average",
  FR: "Fair",
  PR: "Poor",
  UN: "Unsound",
  VP: "Very Poor",
};

function cleanDashText(val) {
  return String(val || "").replace(/[\u2013\u2014]/g, "-");
}

export function normalizeAddress(raw) {
  let txt = String(raw || "").toUpperCase().split(";")[0];
  txt = txt.replace(/,\s*HOUSTON\b.*$/, "").replace(/,\s*TX\b.*$/, "");
  txt = txt.replace(/[.,#]/g, " ");
  const tokens = txt
    .split(/\s+/)
    .filter(Boolean)
    .map((tok) => STREET_SUFFIX_MAP[tok] || tok);
  return tokens.join(" ");
}

export function decodeGzipBuffer(buf) {
  try {
    return zlib.gunzipSync(buf);
  } catch (_e) {
    return zlib.inflateRawSync(buf);
  }
}

export function decodeHcadShorthand(text) {
  const s = String(text || "").trim();
  if (!s) return "";
  const notes = [];
  if (/\bNC\b/i.test(s)) notes.push("New Construction (NC)");
  const cduMatch = s.match(/\bCDU\s*(?:@|:|=|\s)\s*(EX|VG|GD|AV|FR|PR|UN|VP)\b/i);
  if (cduMatch) {
    const code = cduMatch[1].toUpperCase();
    notes.push(`Condition/Desirability/Utility rated ${CDU_GRADE_LABELS[code] || code} (${code})`);
  }
  if (/\bREMOD(?:EL)?\b/i.test(s)) notes.push("Remodel / Renovation");
  if (/\bADDN\b|\bADDITION\b/i.test(s)) notes.push("Building Addition");
  if (/\bBLD\s+DEMO\s*\/\s*SEWER\s+DISC\b/i.test(s)) {
    notes.push("Stage 1 of 2 Demolition Prep: Sewer Disconnection Plumbing Permit");
  } else if (/\bBLD\s+DEMO\b/i.test(s)) {
    notes.push("Stage 2 of 2 Structural Building Demolition Permit");
  }
  if (/\bGARAGE\s+APT\s+ONLY\b/i.test(s)) {
    notes.push("Accessory Garage Apartment Only (main structure not demolished)");
  } else if (/\bGARAGE\s+ONLY\b/i.test(s)) {
    notes.push("Accessory Garage Only (main structure not demolished)");
  }
  return notes.join("; ");
}

export function parseOwnerHistoryWire(wire) {
  if (!wire || typeof wire !== "string") return [];
  const out = [];
  for (const seg of wire.split("|")) {
    const parts = seg.split("~");
    if (parts.length >= 4) {
      out.push({
        roll_years: parts[0].trim(),
        deed_date: parts[1].trim(),
        clerk_file_num: parts[2].trim(),
        owner_name: parts.slice(3).join("~").trim(),
      });
    }
  }
  return out;
}

export class AtlasNodeClient {
  constructor({ dataDir = null, baseUrl = null } = {}) {
    this.dataDir = this._discoverLocalDataDir(dataDir);
    this.cache = new Map();
    this.baseUrl = this._resolveBaseUrl(baseUrl);
  }

  _discoverLocalDataDir(explicitDir) {
    if (explicitDir && fs.existsSync(explicitDir)) return explicitDir;
    if (process.env.ATLAS_DATA_DIR && fs.existsSync(process.env.ATLAS_DATA_DIR)) {
      return process.env.ATLAS_DATA_DIR;
    }
    let cur = __dirname;
    for (let i = 0; i < 5; i += 1) {
      const cand1 = path.join(cur, "app", "public", "data");
      if (fs.existsSync(cand1)) return cand1;
      const cand2 = path.join(cur, "data");
      if (fs.existsSync(path.join(cand2, "overlays.json"))) return cand2;
      const parent = path.dirname(cur);
      if (parent === cur) break;
      cur = parent;
    }
    return null;
  }

  _resolveBaseUrl(explicitUrl) {
    const raw = explicitUrl || process.env.ATLAS_BASE_URL || "";
    if (raw) return raw.endsWith("/") ? raw : `${raw}/`;
    if (this.dataDir) {
      const envPath = path.join(this.dataDir, "atlas_environments.json");
      if (fs.existsSync(envPath)) {
        try {
          const doc = JSON.parse(fs.readFileSync(envPath, "utf8"));
          const canon = String(doc.canonical_base_url || "").trim();
          if (canon) return canon.endsWith("/") ? canon : `${canon}/`;
        } catch (_e) {}
      }
    }
    return DEFAULT_BASE_URL;
  }

  async loadJson(relPath, defaultVal = null) {
    if (this.cache.has(relPath)) return this.cache.get(relPath);
    const isGz = relPath.endsWith(".gz");
    if (this.dataDir) {
      const fullPath = path.join(this.dataDir, relPath);
      if (fs.existsSync(fullPath)) {
        try {
          const raw = fs.readFileSync(fullPath);
          const txt = isGz ? decodeGzipBuffer(raw).toString("utf8") : raw.toString("utf8");
          const parsed = JSON.parse(txt);
          this.cache.set(relPath, parsed);
          return parsed;
        } catch (_e) {}
      }
    }
    const url = `${this.baseUrl}public/data/${relPath.replace(/^\/+/, "")}`;
    try {
      const resp = await fetch(url);
      if (!resp.ok) return defaultVal;
      const arrBuf = await resp.arrayBuffer();
      const buf = Buffer.from(arrBuf);
      const txt = isGz ? decodeGzipBuffer(buf).toString("utf8") : buf.toString("utf8");
      const parsed = JSON.parse(txt);
      this.cache.set(relPath, parsed);
      return parsed;
    } catch (_e) {
      return defaultVal;
    }
  }

  async buildHcadDossier(rawHcad) {
    const hcad = String(rawHcad || "").split("#")[0].trim().padStart(13, "0");
    const prefix3 = hcad.slice(0, 3);
    const overridesDoc = (await this.loadJson("curated_overrides.json", {})) || {};
    const overrides = overridesDoc.overrides || overridesDoc;
    const override = overrides[hcad] || {};

    const permShard = (await this.loadJson(`permits/permits_${prefix3}.json.gz`, {})) || {};
    const permRec = permShard[hcad] || {};
    const ownShard = (await this.loadJson(`owner_history/owners_${prefix3}.json.gz`, {})) || {};
    const ownChain = parseOwnerHistoryWire(ownShard[hcad] || "");
    const overlays = (await this.loadJson("overlays.json", {})) || {};
    const lostRecords = ((overlays.lost_houston || {}).features || [])
      .map((f) => f.properties || {})
      .filter((p) => String(p.hcad_num || "").trim() === hcad);
    const landmarkRecords = ((overlays.landmarks || {}).features || [])
      .map((f) => f.properties || {})
      .filter((p) => String(p.hcad_num || "").split("#")[0].trim() === hcad);

    const rawOwners = Array.isArray(permRec.co)
      ? permRec.co.map((o) => String(o || "").trim()).filter(Boolean)
      : [];
    const rawOwnerStr = rawOwners.join("; ");
    const isConf = Boolean(
      permRec.conf === 1 ||
        permRec.confidential ||
        override.conf === 1 ||
        override.confidential ||
        /(?:^confidential\b|25\.025)/i.test(rawOwnerStr)
    );
    const isDemolished = Boolean(
      override.suppress_only || override.is_demolished || lostRecords.length > 0
    );
    const address = cleanDashText(
      override.address || landmarkRecords[0]?.address || lostRecords[0]?.address || ""
    );
    let buildingName = cleanDashText(
      override.building_name || landmarkRecords[0]?.name || lostRecords[0]?.name || ""
    );
    if (isConf && buildingName) {
      const secretTokens = [...rawOwners, ...ownChain.map((t) => t.owner_name || "")];
      for (const tok of secretTokens) {
        if (tok && !/^confidential\b/i.test(tok) && buildingName.toLowerCase().includes(tok.toLowerCase())) {
          buildingName = address || `HCAD ${hcad}`;
          break;
        }
      }
    }
    return {
      hcad_num: hcad,
      building_name: buildingName,
      address,
      year_built: override.year_built || permRec.yr || null,
      remodel_year: permRec.remod || null,
      architect: cleanDashText(
        override.architect || landmarkRecords[0]?.architect || lostRecords[0]?.architect || ""
      ),
      style: cleanDashText(
        override.style || landmarkRecords[0]?.style || lostRecords[0]?.style || ""
      ),
      is_demolished: isDemolished,
      confidential_sec_25_025: isConf,
      current_owners: isConf ? [] : rawOwners.map(cleanDashText),
      ownership_chain_2005_2026: isConf ? [] : ownChain,
      permits: (permRec.p || []).slice(0, 15).map((p) => ({
        ...p,
        d: cleanDashText(p.d || ""),
        plain_english: cleanDashText(decodeHcadShorthand(p.d || "")),
      })),
      links: {
        atlas_overview: `${this.baseUrl}#hcad=${hcad}`,
        atlas_permits_tab: `${this.baseUrl}#hcad=${hcad}&tab=permits`,
        atlas_history_tab: `${this.baseUrl}#hcad=${hcad}&tab=history`,
        thc_survey_form: `${this.baseUrl}#hcad=${hcad}&thcSurvey=1`,
        ...(lostRecords[0]?.id
          ? { atlas_lost_houston: `${this.baseUrl}?id=${lostRecords[0].id}&layers=lost_houston` }
          : {}),
      },
    };
  }
}

async function runCli() {
  const args = process.argv.slice(2);
  const client = new AtlasNodeClient();
  const hcadIdx = args.indexOf("--hcad");
  const addrIdx = args.indexOf("--address");
  if (hcadIdx >= 0 && args[hcadIdx + 1]) {
    const dossier = await client.buildHcadDossier(args[hcadIdx + 1]);
    console.log(JSON.stringify(dossier, null, 2));
    return;
  }
  if (addrIdx >= 0 && args[addrIdx + 1]) {
    const norm = normalizeAddress(args[addrIdx + 1]);
    const m = norm.match(/^(\d+)\s+(.*)$/);
    if (m) {
      const houseNum = m[1];
      const street = m[2];
      const shard = (await client.loadJson(`address_shards/addr_${houseNum.slice(0, 2).padStart(2, "0")}.json.gz`, {})) || {};
      for (const [candStreet, rows] of Object.entries(shard)) {
        const cleanCand = normalizeAddress(candStreet).replace(/^\d+\s+/, "");
        if (cleanCand === street || cleanCand.includes(street)) {
          for (const row of rows || []) {
            if (String(row[0]) === houseNum && row[4]) {
              const dossier = await client.buildHcadDossier(row[4]);
              console.log(JSON.stringify(dossier, null, 2));
              return;
            }
          }
        }
      }
    }
    console.log(JSON.stringify({ query: args[addrIdx + 1], error: "No match found" }, null, 2));
    return;
  }
  console.log("Usage: node query_atlas.mjs --hcad <13-digit-hcad> | --address <street-address>");
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  runCli();
}
