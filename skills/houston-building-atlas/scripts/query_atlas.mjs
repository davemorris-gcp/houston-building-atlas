#!/usr/bin/env node
// Copyright 2026 Google LLC
// SPDX-License-Identifier: Apache-2.0
/**
 * Zero-dependency Node.js (ESM) lookup and 2-way contribution script for The Houston Building Atlas.
 * Supports local `app/public/data/` shards or live HTTPS fetches from `atlas_environments.json`,
 * plus moderated 2-way research contributions (`--submit-correction`, `--submit-survey`, `--submit-flag`)
 * for local LLMs (Ollama, LM Studio, llama.cpp), Claude Code, Gemini CLI, and Node.js agents.
 */

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import zlib from "node:zlib";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
export const DEFAULT_BASE_URL = "https://davemorris-gcp.github.io/houston-building-atlas/";
export const DEFAULT_SUBMISSION_WEBHOOK_URL =
  "https://script.google.com/macros/s/AKfycbw1axAzEHZgcIKcdRUAjltDIsq505PJRalUMOfmJJa07qR-RCdsWBAwPv1gjN3sJlEbtg/exec";

export const MIN_VALID_YEAR = 1836;
export const MAX_VALID_YEAR = 2026;

export const FIELD_SURVEY_CSV_COLUMNS = [
  "survey_id",
  "surveyed_at",
  "review_status",
  "hcad_num",
  "address",
  "district_or_corridor",
  "integrity_grade",
  "condition_rating",
  "suggested_contributing",
  "windows_status",
  "cladding_material",
  "porch_integrity",
  "atlas_year_built",
  "observed_year_or_demo_note",
  "photo_url",
  "field_notes",
  "surveyor_name",
  "gps_lat_lng_acc",
];

export const SUGGESTION_CSV_COLUMNS = [
  "status",
  "hcad_num",
  "address",
  "historic_district",
  "hcad_year_built",
  "suggested_year_built",
  "bld_style",
  "architect",
  "source_type",
  "source_citation",
  "source_url",
  "footprint_issue",
  "footprint_notes",
  "photo_url",
  "photo_year",
  "photo_caption",
  "submitter_name",
  "submitter_email",
  "submitted_at",
];

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

export function cleanDashText(val) {
  return String(val ?? "")
    .replace(/ \u2014 /g, " - ")
    .replace(/ \u2013 /g, " - ")
    .replace(/[\u2013\u2014]/g, "-");
}

export function sanitizeSpreadsheetCell(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  let s = cleanDashText(value).replace(/\r\n/g, " ").replace(/\r/g, " ");
  if (!s) return "";
  const firstRaw = s[0];
  const trimmed = s.trimStart();
  const firstTrimmed = trimmed ? trimmed[0] : "";
  if (
    ["=", "+", "-", "@", "\t"].includes(firstRaw) ||
    ["=", "+", "-", "@"].includes(firstTrimmed)
  ) {
    s = `'${s}`;
  }
  return s;
}

export function formatCsvRows(records, columns) {
  const escapeCell = (rawVal) => {
    const safe = sanitizeSpreadsheetCell(rawVal);
    if (/[,"\n\r]/.test(safe)) {
      return `"${safe.replace(/"/g, '""')}"`;
    }
    return safe;
  };
  const lines = [columns.join(",")];
  for (const rec of records || []) {
    if (!rec || typeof rec !== "object") continue;
    lines.push(columns.map((col) => escapeCell(rec[col] ?? "")).join(","));
  }
  return lines.join("\n");
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

function defaultLocalLedgerPath() {
  const envPath = String(process.env.ATLAS_CONTRIBUTIONS_FILE || "").trim();
  if (envPath) return envPath;
  return path.join(os.homedir(), ".houston_building_atlas", "pending_contributions.json");
}

export class AtlasNodeClient {
  constructor({ dataDir = null, baseUrl = null, webhookUrl = null, ledgerPath = null } = {}) {
    this.dataDir = this._discoverLocalDataDir(dataDir);
    this.cache = new Map();
    this.baseUrl = this._resolveBaseUrl(baseUrl);
    this.webhookUrl = this.resolveSubmissionWebhookUrl(webhookUrl);
    this.ledgerPath = ledgerPath || defaultLocalLedgerPath();
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

  resolveSubmissionWebhookUrl(explicitUrl = null) {
    const raw = String(explicitUrl || process.env.ATLAS_SUBMISSION_WEBHOOK_URL || "").trim();
    if (raw) return raw;
    if (this.dataDir) {
      const envPath = path.join(this.dataDir, "atlas_environments.json");
      if (fs.existsSync(envPath)) {
        try {
          const doc = JSON.parse(fs.readFileSync(envPath, "utf8"));
          const wh = String(doc.submission_webhook_url || "").trim();
          if (wh) return wh;
        } catch (_e) {}
      }
    }
    return DEFAULT_SUBMISSION_WEBHOOK_URL;
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

  getLocalContributions() {
    if (!this.ledgerPath || !fs.existsSync(this.ledgerPath)) return [];
    try {
      const data = JSON.parse(fs.readFileSync(this.ledgerPath, "utf8"));
      return Array.isArray(data) ? data.filter((r) => r && typeof r === "object") : [];
    } catch (_e) {
      return [];
    }
  }

  _appendLocalContribution(record) {
    const existing = this.getLocalContributions();
    existing.push(record);
    fs.mkdirSync(path.dirname(this.ledgerPath), { recursive: true });
    fs.writeFileSync(this.ledgerPath, JSON.stringify(existing, null, 2), "utf8");
    return this.ledgerPath;
  }

  static _tagAgentCitation(citation, agentTool) {
    const cleanCit = cleanDashText(citation || "").trim();
    const cleanTool = cleanDashText(agentTool || "").trim();
    if (cleanTool && !cleanCit.includes(`[AI_AGENT:${cleanTool}]`)) {
      return `[AI_AGENT:${cleanTool}] ${cleanCit}`.trim();
    }
    return cleanCit;
  }

  async _postToWebhook(payload, { dryRun = false } = {}) {
    if (dryRun) {
      return {
        ok: true,
        mode: "dry_run",
        queued_in_moderation_sheet: false,
        saved_locally: false,
        webhook_url: this.webhookUrl,
        payload,
      };
    }
    const savedPath = this._appendLocalContribution(payload);
    try {
      const resp = await fetch(this.webhookUrl, {
        method: "POST",
        headers: {
          "Content-Type": "text/plain;charset=utf-8",
          "User-Agent": "HoustonBuildingAtlas-AICompanion/2.0",
        },
        body: JSON.stringify(payload),
      });
      const respText = await resp.text();
      return {
        ok: resp.ok,
        mode: "webhook_post",
        http_status: resp.status,
        queued_in_moderation_sheet: resp.ok,
        saved_locally: true,
        local_ledger_path: savedPath,
        webhook_url: this.webhookUrl,
        webhook_response: respText.slice(0, 300),
        payload,
      };
    } catch (err) {
      return {
        ok: false,
        mode: "local_fallback",
        queued_in_moderation_sheet: false,
        saved_locally: true,
        local_ledger_path: savedPath,
        webhook_url: this.webhookUrl,
        error: String(err?.message || err),
        payload,
      };
    }
  }

  async submitCorrection(payload, { dryRun = false } = {}) {
    if (!payload || typeof payload !== "object") {
      throw new Error("Contribution payload must be a JSON object.");
    }
    const hcadNum = cleanDashText(payload.hcad_num || payload.hcad || "").trim();
    const address = cleanDashText(payload.address || "").trim();
    const buildingName = cleanDashText(payload.building_name || "").trim();
    const altNames = cleanDashText(payload.alt_names || "").trim();
    const historicDistrict = cleanDashText(payload.historic_district || "").trim();
    const contributing = cleanDashText(payload.contributing || "").trim();
    const bldStyle = cleanDashText(payload.bld_style || payload.style || "").trim();
    const architect = cleanDashText(payload.architect || "").trim();
    const builder = cleanDashText(payload.builder || "").trim();
    const permitInfo = cleanDashText(payload.permit_info || "").trim();
    const sourceType = cleanDashText(payload.source_type || "ARCHIVAL_CITATION").trim();
    const agentTool = cleanDashText(payload.agent_tool || "").trim();
    const sourceCitation = AtlasNodeClient._tagAgentCitation(payload.source_citation || "", agentTool);
    const sourceUrl = cleanDashText(payload.source_url || "").trim();
    let footprintIssue = cleanDashText(payload.footprint_issue || "").trim();
    let footprintNotes = cleanDashText(payload.footprint_notes || "").trim();
    const photoUrl = cleanDashText(payload.photo_url || "").trim();
    const photoYear = cleanDashText(payload.photo_year || "").trim();
    const photoCaption = cleanDashText(payload.photo_caption || "").trim();
    const submitterName = cleanDashText(payload.submitter_name || payload.submitter || "").trim();
    const submitterEmail = cleanDashText(payload.submitter_email || "").trim();
    const demolishedOrVacant = Boolean(payload.demolished_or_vacant);
    const suppressOnly = Boolean(payload.suppress_only);

    let rawSuggestedYear = payload.suggested_year_built;
    if (rawSuggestedYear === undefined || rawSuggestedYear === null || rawSuggestedYear === "") {
      rawSuggestedYear = payload.year_built;
    }
    let suggestedYear = "";
    if (rawSuggestedYear !== undefined && rawSuggestedYear !== null && String(rawSuggestedYear).trim() !== "") {
      const yr = Number.parseInt(String(rawSuggestedYear).trim(), 10);
      if (!Number.isInteger(yr) || yr < MIN_VALID_YEAR || yr > MAX_VALID_YEAR) {
        throw new Error(`Suggested year built must be an integer between ${MIN_VALID_YEAR} and ${MAX_VALID_YEAR}.`);
      }
      suggestedYear = yr;
    }

    if (demolishedOrVacant) {
      footprintIssue = "DEMOLISHED_OR_VACANT_LOT";
      const tag = "[STATUS: DEMOLISHED / VACANT LOT]";
      if (!footprintNotes.includes(tag)) {
        footprintNotes = `${tag} ${footprintNotes}`.trim();
      }
    }

    const geomRaw = payload.geometry_geojson || "";
    const geometryGeojson =
      geomRaw && typeof geomRaw === "object" ? JSON.stringify(geomRaw) : String(geomRaw || "").trim();

    const hasAnyCorrection = Boolean(
      suggestedYear !== "" ||
        suppressOnly ||
        demolishedOrVacant ||
        buildingName ||
        altNames ||
        historicDistrict ||
        contributing ||
        bldStyle ||
        architect ||
        builder ||
        permitInfo ||
        footprintIssue ||
        footprintNotes ||
        geometryGeojson ||
        photoUrl ||
        sourceCitation
    );
    if (!hasAnyCorrection) {
      throw new Error("Please provide at least one research correction field.");
    }
    if (!hcadNum && !address) {
      throw new Error("Please provide either an `hcad_num` or a street `address` for the property.");
    }
    if (!sourceCitation && !sourceUrl) {
      throw new Error("Please provide a `source_citation` or `source_url` so Preservation Staff can verify provenance.");
    }

    const record = {
      status: "Pending",
      submitted_at: String(payload.submitted_at || new Date().toISOString()),
      hcad_num: hcadNum,
      address,
      building_name: buildingName,
      alt_names: altNames,
      historic_district: historicDistrict,
      contributing,
      hcad_year_built: payload.hcad_year_built ?? "",
      suggested_year_built: suggestedYear,
      suppress_only: suppressOnly ? "TRUE" : "FALSE",
      bld_style: bldStyle,
      architect,
      builder,
      permit_info: permitInfo,
      source_type: sourceType,
      source_citation: sourceCitation,
      source_url: sourceUrl,
      footprint_issue: footprintIssue,
      footprint_notes: footprintNotes,
      geometry_geojson: geometryGeojson,
      photo_url: photoUrl,
      photo_year: photoYear,
      photo_caption: photoCaption,
      submitter_name: submitterName,
      submitter_email: submitterEmail,
    };
    return this._postToWebhook(record, { dryRun });
  }

  async submitFieldSurvey(payload, { dryRun = false } = {}) {
    if (!payload || typeof payload !== "object") {
      throw new Error("Field survey payload must be a JSON object.");
    }
    const hcadNum = cleanDashText(payload.hcad_num || payload.hcad || "").trim();
    const address = cleanDashText(payload.address || "").trim();
    if (!hcadNum && !address) {
      throw new Error("Field survey requires an `hcad_num` or street `address`.");
    }
    const integrityGrade = cleanDashText(payload.integrity_grade || "").trim();
    const conditionRating = cleanDashText(payload.condition_rating || "").trim();
    const fieldNotes = cleanDashText(payload.field_notes || "").trim();
    const observedYearNote = cleanDashText(
      payload.observed_year_or_demo_note || payload.observed_year_note || ""
    ).trim();
    if (!integrityGrade && !conditionRating && !fieldNotes && !observedYearNote) {
      throw new Error(
        "Field survey requires at least one observation (`integrity_grade`, `condition_rating`, `field_notes`, or `observed_year_or_demo_note`)."
      );
    }

    const nowIso = String(payload.surveyed_at || new Date().toISOString());
    const surveyId = String(payload.survey_id || `SRV-${Date.now()}`);
    const districtOrCorridor = cleanDashText(
      payload.district_or_corridor || payload.historic_district || ""
    ).trim();
    const suggestedContributing = cleanDashText(
      payload.suggested_contributing || payload.contributing || ""
    ).trim();
    const windowsStatus = cleanDashText(payload.windows_status || "").trim();
    const claddingMaterial = cleanDashText(payload.cladding_material || "").trim();
    const porchIntegrity = cleanDashText(payload.porch_integrity || "").trim();
    const atlasYearBuilt = cleanDashText(payload.atlas_year_built || "").trim();
    const photoUrl = cleanDashText(payload.photo_url || "").trim();
    const surveyorName = cleanDashText(payload.surveyor_name || payload.submitter || "").trim();
    const gpsLatLngAcc = cleanDashText(payload.gps_lat_lng_acc || payload.gps_coords || "").trim();
    const agentTool = cleanDashText(payload.agent_tool || "").trim();

    const embeddedMeta = {
      survey_id: surveyId,
      integrity_grade: integrityGrade,
      condition_rating: conditionRating,
      windows_status: windowsStatus,
      cladding_material: claddingMaterial,
      porch_integrity: porchIntegrity,
      gps_lat_lng_acc: gpsLatLngAcc,
      ...(agentTool ? { agent_tool: agentTool } : {}),
    };
    const summaryParts = [
      `[Field Survey ${surveyId}]`,
      integrityGrade ? `Integrity: ${integrityGrade}` : "",
      conditionRating ? `Condition: ${conditionRating}` : "",
      suggestedContributing ? `Contributing: ${suggestedContributing}` : "",
      windowsStatus ? `Windows: ${windowsStatus}` : "",
      claddingMaterial ? `Cladding: ${claddingMaterial}` : "",
      porchIntegrity ? `Porch: ${porchIntegrity}` : "",
      fieldNotes ? `Notes: ${fieldNotes}` : "",
      `[FIELD_SURVEY_JSON:${JSON.stringify(embeddedMeta)}]`,
    ].filter(Boolean);
    const sourceCitation = AtlasNodeClient._tagAgentCitation(summaryParts.join(" | "), agentTool);

    const record = {
      action: "field_survey",
      status: "Pending",
      review_status: "Unreviewed",
      survey_id: surveyId,
      surveyed_at: nowIso,
      hcad_num: hcadNum,
      address,
      district_or_corridor: districtOrCorridor,
      integrity_grade: integrityGrade,
      condition_rating: conditionRating,
      suggested_contributing: suggestedContributing,
      windows_status: windowsStatus,
      cladding_material: claddingMaterial,
      porch_integrity: porchIntegrity,
      atlas_year_built: atlasYearBuilt,
      observed_year_or_demo_note: observedYearNote,
      photo_url: photoUrl,
      field_notes: fieldNotes,
      surveyor_name: surveyorName,
      gps_lat_lng_acc: gpsLatLngAcc,
      source_type: "FIELD_SURVEY_OBSERVATION",
      source_citation: sourceCitation,
      source_url: photoUrl,
      submitter_name: surveyorName,
    };
    return this._postToWebhook(record, { dryRun });
  }

  async submitQuickFlag(payload, { dryRun = false } = {}) {
    if (!payload || typeof payload !== "object") {
      throw new Error("Quick flag payload must be a JSON object.");
    }
    const flagType = String(payload.flag_type || "claim_dispute").trim().toLowerCase();
    const hcadNum = cleanDashText(payload.hcad_num || payload.hcad || "").trim();
    const address = cleanDashText(payload.address || "").trim();
    const buildingName = cleanDashText(payload.building_name || "").trim();
    const notes = cleanDashText(payload.notes || payload.source_citation || "").trim();
    const sourceUrl = cleanDashText(payload.source_url || "").trim();
    const submitterName = cleanDashText(payload.submitter_name || payload.submitter || "").trim();
    const submitterEmail = cleanDashText(payload.submitter_email || "").trim();
    const agentTool = cleanDashText(payload.agent_tool || "").trim();

    if (flagType === "photo_misattribution") {
      const photoUrl = cleanDashText(payload.photo_url || "").trim();
      const reason = cleanDashText(payload.reason || "wrong_building").trim();
      const citation = `[PHOTO_FLAG:${reason}] Flagged photo: ${photoUrl || "Current thumbnail"} - ${notes || "Flagged for curator review"}`;
      return this.submitCorrection(
        {
          hcad_num: hcadNum,
          address,
          building_name: buildingName,
          photo_url: photoUrl,
          source_type: "PHOTO_MISATTRIBUTION_FLAG",
          source_citation: citation,
          source_url: sourceUrl || photoUrl,
          submitter_name: submitterName,
          submitter_email: submitterEmail,
          agent_tool,
        },
        { dryRun }
      );
    }

    if (flagType === "narrative_error") {
      const excerpt = cleanDashText(payload.excerpt || payload.disputed_value || "").trim();
      const citation = `[NARRATIVE_FLAG] Excerpt: "${excerpt || "Summary narrative"}" - Correction: ${notes || "Flagged for curator verification"}`;
      return this.submitCorrection(
        {
          hcad_num: hcadNum,
          address,
          building_name: buildingName,
          source_type: "NARRATIVE_ERROR_FLAG",
          source_citation: citation,
          source_url: sourceUrl,
          submitter_name: submitterName,
          submitter_email: submitterEmail,
          agent_tool,
        },
        { dryRun }
      );
    }

    const disputedField = cleanDashText(payload.disputed_field || "year_built").trim();
    const disputedValue = cleanDashText(payload.disputed_value || "").trim();
    const suggestedValue = cleanDashText(payload.suggested_value || "").trim();
    const citation =
      `[CLAIM_DISPUTE:${disputedField}] Current value: "${disputedValue}"` +
      (suggestedValue ? ` -> Suggested: "${suggestedValue}"` : "") +
      ` - Evidence: ${notes || "Disputed for curator review"}`;

    const corrPayload = {
      hcad_num: hcadNum,
      address,
      building_name: buildingName,
      source_type: "CLAIM_DISPUTE_FLAG",
      source_citation: citation,
      source_url: sourceUrl,
      submitter_name: submitterName,
      submitter_email: submitterEmail,
      agent_tool: agentTool,
    };
    if (disputedField === "year_built" && suggestedValue) {
      const parsedYr = Number.parseInt(suggestedValue, 10);
      if (Number.isInteger(parsedYr)) corrPayload.suggested_year_built = parsedYr;
    } else if (disputedField === "architect" && suggestedValue) {
      corrPayload.architect = suggestedValue;
    } else if (disputedField === "style" && suggestedValue) {
      corrPayload.bld_style = suggestedValue;
    }
    return this.submitCorrection(corrPayload, { dryRun });
  }

  async submitContributionJson(jsonStrOrPath, { dryRun = false } = {}) {
    const raw = String(jsonStrOrPath || "").trim();
    if (!raw) throw new Error("Empty contribution JSON input.");
    const payload =
      !raw.startsWith("{") && fs.existsSync(raw)
        ? JSON.parse(fs.readFileSync(raw, "utf8"))
        : JSON.parse(raw);
    if (!payload || typeof payload !== "object") {
      throw new Error("Contribution JSON must decode to an object.");
    }
    const action = String(payload.action || payload.kind || "").trim().toLowerCase();
    if (action === "field_survey" || payload.source_type === "FIELD_SURVEY_OBSERVATION") {
      return this.submitFieldSurvey(payload, { dryRun });
    }
    if (action === "quick_flag" || payload.flag_type) {
      return this.submitQuickFlag(payload, { dryRun });
    }
    return this.submitCorrection(payload, { dryRun });
  }

  exportContributionsCsv({ kind = "suggestions", records = null } = {}) {
    const allRecs = records !== null ? records : this.getLocalContributions();
    if (kind === "surveys") {
      const filtered = allRecs.filter(
        (r) => r.action === "field_survey" || r.source_type === "FIELD_SURVEY_OBSERVATION"
      );
      return formatCsvRows(filtered, FIELD_SURVEY_CSV_COLUMNS);
    }
    const filtered = allRecs.filter(
      (r) => r.action !== "field_survey" && r.source_type !== "FIELD_SURVEY_OBSERVATION"
    );
    return formatCsvRows(filtered, SUGGESTION_CSV_COLUMNS);
  }
}

function getArgValue(args, flag, defaultVal = "") {
  const idx = args.indexOf(flag);
  if (idx >= 0 && idx + 1 < args.length) return args[idx + 1];
  return defaultVal;
}

async function runCli() {
  const args = process.argv.slice(2);
  const dryRun = args.includes("--dry-run");
  const client = new AtlasNodeClient({
    dataDir: getArgValue(args, "--data-dir", null),
    baseUrl: getArgValue(args, "--base-url", null),
    webhookUrl: getArgValue(args, "--webhook-url", null),
    ledgerPath: getArgValue(args, "--ledger-file", null),
  });

  if (args.includes("--list-contributions")) {
    console.log(JSON.stringify(client.getLocalContributions(), null, 2));
    return;
  }

  const exportCsvKind = getArgValue(args, "--export-contributions-csv", "");
  if (exportCsvKind) {
    console.log(client.exportContributionsCsv({ kind: exportCsvKind }));
    return;
  }

  const contributeJsonArg = getArgValue(args, "--contribute-json", "");
  if (contributeJsonArg) {
    const res = await client.submitContributionJson(contributeJsonArg, { dryRun });
    console.log(JSON.stringify(res, null, 2));
    return;
  }

  if (args.includes("--submit-correction")) {
    const res = await client.submitCorrection(
      {
        hcad_num: getArgValue(args, "--hcad", ""),
        address: getArgValue(args, "--address", ""),
        building_name: getArgValue(args, "--building-name", ""),
        alt_names: getArgValue(args, "--alt-names", ""),
        historic_district: getArgValue(args, "--historic-district", ""),
        contributing: getArgValue(args, "--contributing", ""),
        hcad_year_built: getArgValue(args, "--hcad-year-built", ""),
        suggested_year_built: getArgValue(args, "--year-built", ""),
        suppress_only: args.includes("--suppress-only"),
        demolished_or_vacant: args.includes("--demolished-vacant"),
        bld_style: getArgValue(args, "--style", ""),
        architect: getArgValue(args, "--architect", ""),
        builder: getArgValue(args, "--builder", ""),
        permit_info: getArgValue(args, "--permit-info", ""),
        source_type: getArgValue(args, "--source-type", "ARCHIVAL_CITATION"),
        source_citation: getArgValue(args, "--source-citation", ""),
        source_url: getArgValue(args, "--source-url", ""),
        footprint_issue: getArgValue(args, "--footprint-issue", ""),
        footprint_notes: getArgValue(args, "--footprint-notes", ""),
        geometry_geojson: getArgValue(args, "--geometry-geojson", ""),
        photo_url: getArgValue(args, "--photo-url", ""),
        photo_year: getArgValue(args, "--photo-year", ""),
        photo_caption: getArgValue(args, "--photo-caption", ""),
        submitter_name: getArgValue(args, "--submitter", ""),
        submitter_email: getArgValue(args, "--submitter-email", ""),
        agent_tool: getArgValue(args, "--agent-tool", ""),
      },
      { dryRun }
    );
    console.log(JSON.stringify(res, null, 2));
    return;
  }

  if (args.includes("--submit-survey")) {
    const res = await client.submitFieldSurvey(
      {
        hcad_num: getArgValue(args, "--hcad", ""),
        address: getArgValue(args, "--address", ""),
        district_or_corridor: getArgValue(args, "--historic-district", ""),
        integrity_grade: getArgValue(args, "--integrity-grade", ""),
        condition_rating: getArgValue(args, "--condition-rating", ""),
        suggested_contributing: getArgValue(args, "--contributing", ""),
        windows_status: getArgValue(args, "--windows-status", ""),
        cladding_material: getArgValue(args, "--cladding-material", ""),
        porch_integrity: getArgValue(args, "--porch-integrity", ""),
        atlas_year_built: getArgValue(args, "--hcad-year-built", ""),
        observed_year_or_demo_note: getArgValue(args, "--observed-year-note", ""),
        photo_url: getArgValue(args, "--photo-url", ""),
        field_notes: getArgValue(args, "--field-notes", ""),
        surveyor_name: getArgValue(args, "--submitter", ""),
        gps_lat_lng_acc: getArgValue(args, "--gps-coords", ""),
        agent_tool: getArgValue(args, "--agent-tool", ""),
      },
      { dryRun }
    );
    console.log(JSON.stringify(res, null, 2));
    return;
  }

  if (args.includes("--submit-flag")) {
    const res = await client.submitQuickFlag(
      {
        flag_type: getArgValue(args, "--flag-type", "claim_dispute"),
        hcad_num: getArgValue(args, "--hcad", ""),
        address: getArgValue(args, "--address", ""),
        building_name: getArgValue(args, "--building-name", ""),
        disputed_field: getArgValue(args, "--disputed-field", "year_built"),
        disputed_value: getArgValue(args, "--disputed-value", ""),
        suggested_value: getArgValue(args, "--suggested-value", ""),
        photo_url: getArgValue(args, "--photo-url", ""),
        notes: getArgValue(args, "--source-citation", ""),
        source_url: getArgValue(args, "--source-url", ""),
        submitter_name: getArgValue(args, "--submitter", ""),
        submitter_email: getArgValue(args, "--submitter-email", ""),
        agent_tool: getArgValue(args, "--agent-tool", ""),
      },
      { dryRun }
    );
    console.log(JSON.stringify(res, null, 2));
    return;
  }

  const hcadVal = getArgValue(args, "--hcad", "");
  if (hcadVal) {
    const dossier = await client.buildHcadDossier(hcadVal);
    console.log(JSON.stringify(dossier, null, 2));
    return;
  }

  const addrVal = getArgValue(args, "--address", "");
  if (addrVal) {
    const norm = normalizeAddress(addrVal);
    const m = norm.match(/^(\d+)\s+(.*)$/);
    if (m) {
      const houseNum = m[1];
      const street = m[2];
      const shard =
        (await client.loadJson(`address_shards/addr_${houseNum.slice(0, 2).padStart(2, "0")}.json.gz`, {})) || {};
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
    console.log(JSON.stringify({ query: addrVal, error: "No match found" }, null, 2));
    return;
  }
  console.log(
    "Usage: node query_atlas.mjs --hcad <13-digit-hcad> | --address <street-address> | --submit-correction | --submit-survey | --submit-flag"
  );
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  runCli();
}
