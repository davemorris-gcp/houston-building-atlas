/**
 * Pure, Zero-DOM View-Model & Payload Builder for the In-App Community Moderation Queue.
 *
 * Unifies moderation across both `Pending_Submissions` and Walk-the-Block `Field_Surveys`
 * so Preservation Staff never need to open the 13-tab Google Sheet:
 *   1. Protects `Pending_Submissions!S:V` `ARRAYFORMULA` columns via row-shift guards and
 *      split-range backend mutations.
 *   2. Routes `PHOTO_MISATTRIBUTION_FLAG`, `CLAIM_DISPUTE_FLAG`, and `NARRATIVE_ERROR_FLAG`
 *      resolutions to `"Resolved"` rather than `"Approved"` unless an explicit standing year
 *      or vacant suppression is promoted, preventing blank-year overrides in `Approved_Edits`.
 *   3. Strips stale custom polygon tags when approving a `Demolished / Vacant Lot` report.
 */

import {
  MONITORING_SCHEMA_CONTRACT,
  sanitizeMonitoringText,
  stripEmbeddedGeometryTag,
} from "./parcelMonitoringModel.js?v=20261113c";

export const DISPUTE_FLAG_TYPES = new Set([
  "PHOTO_MISATTRIBUTION_FLAG",
  "CLAIM_DISPUTE_FLAG",
  "NARRATIVE_ERROR_FLAG",
]);

export const MODERATION_CATEGORIES = Object.freeze([
  { id: "all", label: "All Pending" },
  { id: "archival_edit", label: "Year / Name / Architect" },
  { id: "field_survey", label: "Walk-the-Block Surveys" },
  { id: "footprint_vacant", label: "Footprints & Demolitions" },
  { id: "dispute_flag", label: "Photo & Claim Flags" },
]);

export function isExplicitDemolishedYearText(rawVal) {
  const low = sanitizeMonitoringText(rawVal).toLowerCase();
  if (!low) return false;
  if (/^(?:0|vacant(?:\s+lot)?|demolished)$/.test(low)) return true;
  if (/\bdemolished\b/.test(low) && /\b(?:vacant|cleared|empty|lot|gone|slab)\b/.test(low)) {
    return true;
  }
  if (/\b(?:vacant|cleared|empty)\s+lot\b/.test(low)) return true;
  return false;
}

export function classifyModerationQueueItem(raw = {}) {
  const sourceTab = sanitizeMonitoringText(raw.sourceTab || "Pending_Submissions");
  const srcType = sanitizeMonitoringText(raw.source_type || "").toUpperCase();
  const cite = sanitizeMonitoringText(raw.source_citation || "");
  const sugYrRaw = sanitizeMonitoringText(raw.suggested_year_built ?? "");
  const isFieldSurvey =
    sourceTab === "Field_Surveys" || srcType === "FIELD_SURVEY_OBSERVATION";
  const isDisputeFlag = DISPUTE_FLAG_TYPES.has(srcType);
  const isVacantReport =
    !isDisputeFlag &&
    (isExplicitDemolishedYearText(sugYrRaw) ||
      /\[demolished\s*\/\s*vacant|building\s+demolished/i.test(cite));
  const hasCustomGeom =
    Boolean(raw.geometry_geojson) || cite.includes("[GEOMETRY_GEOJSON:");

  let category = "archival_edit";
  let categoryBadge = "Archival Correction";
  if (isFieldSurvey) {
    category = "field_survey";
    categoryBadge = "Walk-the-Block Survey";
  } else if (isDisputeFlag) {
    category = "dispute_flag";
    categoryBadge = srcType.replace(/_/g, " ");
  } else if (isVacantReport || hasCustomGeom) {
    category = "footprint_vacant";
    categoryBadge = isVacantReport ? "Demolished / Vacant Report" : "Custom Footprint";
  }

  return {
    queueId: sanitizeMonitoringText(
      raw.queueId || `${sourceTab}:${raw.rowNumber || 0}:${raw.hcad_num || raw.survey_id || ""}`
    ),
    sourceTab,
    rowNumber: Number(raw.rowNumber) || 0,
    surveyId: sanitizeMonitoringText(raw.survey_id || ""),
    status: sanitizeMonitoringText(raw.status || "Pending"),
    category,
    categoryBadge,
    isFieldSurvey,
    isDisputeFlag,
    isVacantReport,
    hasCustomGeom,
    primaryApproveLabel: isDisputeFlag
      ? "Mark Flag Resolved"
      : isFieldSurvey
      ? "Promote Survey to Map"
      : "Approve & Publish to Map",
    hcadNum: sanitizeMonitoringText(raw.hcad_num || ""),
    address: sanitizeMonitoringText(raw.address || ""),
    buildingName: sanitizeMonitoringText(raw.building_name || ""),
    altNames: sanitizeMonitoringText(raw.alt_names || ""),
    suggestedYearBuilt: sugYrRaw,
    originalHcadYear: sanitizeMonitoringText(raw.original_hcad_year ?? ""),
    historicDistrict: sanitizeMonitoringText(raw.historic_district || ""),
    contributing: sanitizeMonitoringText(raw.contributing || ""),
    integrityGrade: sanitizeMonitoringText(raw.integrity_grade || ""),
    conditionRating: sanitizeMonitoringText(raw.condition_rating || ""),
    bldStyle: sanitizeMonitoringText(raw.bld_style || ""),
    architect: sanitizeMonitoringText(raw.architect || ""),
    sourceType: sanitizeMonitoringText(raw.source_type || ""),
    sourceCitation: cite,
    sourceUrl: sanitizeMonitoringText(raw.source_url || ""),
    submittedBy: sanitizeMonitoringText(raw.submitted_by || "Community Member"),
    submittedAt: sanitizeMonitoringText(raw.submitted_at || ""),
    notes: sanitizeMonitoringText(raw.notes || ""),
    geometryGeojson: String(raw.geometry_geojson || "").trim(),
  };
}

export function filterModerationQueueItems(
  items = [],
  { category = "all", query = "" } = {}
) {
  const cat = String(category || "all").trim();
  const q = sanitizeMonitoringText(query).toLowerCase();
  return (Array.isArray(items) ? items : [])
    .map((it) => (it && it.queueId && it.category ? it : classifyModerationQueueItem(it)))
    .filter((it) => {
      if (cat !== "all" && it.category !== cat) return false;
      if (!q) return true;
      const hay = [
        it.hcadNum,
        it.address,
        it.buildingName,
        it.historicDistrict,
        it.sourceType,
        it.sourceCitation,
        it.submittedBy,
      ]
        .join(" ")
        .toLowerCase();
      return hay.includes(q);
    });
}

export function buildModerationDecisionPayload(
  rawOrViewItem,
  decision,
  inlineEdits = {}
) {
  const item =
    rawOrViewItem && rawOrViewItem.queueId && rawOrViewItem.category
      ? rawOrViewItem
      : classifyModerationQueueItem(rawOrViewItem);

  const cleanDecision = sanitizeMonitoringText(decision);
  if (
    !["Approved", "Needs Info", "On Hold", "Duplicate", "Rejected", "Resolved", "Dismissed"].includes(
      cleanDecision
    )
  ) {
    throw new Error(`Unsupported moderation decision: ${decision}`);
  }

  const editedYear =
    inlineEdits.suggested_year_built !== undefined
      ? sanitizeMonitoringText(inlineEdits.suggested_year_built)
      : item.suggestedYearBuilt;
  const hasExplicitValidYear =
    editedYear !== "" &&
    Number.isFinite(Number(editedYear)) &&
    Number(editedYear) >= MONITORING_SCHEMA_CONTRACT.minStandingYear &&
    Number(editedYear) <= MONITORING_SCHEMA_CONTRACT.maxStandingYear;
  const suppressOnly =
    inlineEdits.suppress_only !== undefined
      ? Boolean(inlineEdits.suppress_only) ||
        (isExplicitDemolishedYearText(editedYear) &&
          inlineEdits.suggested_year_built !== undefined &&
          !isExplicitDemolishedYearText(item.suggestedYearBuilt))
      : item.isVacantReport || isExplicitDemolishedYearText(editedYear);

  // Dispute flags without an explicit replacement year resolve to "Resolved" so blank-year rows never enter Approved_Edits
  const effectiveDecision =
    cleanDecision === "Approved" && item.isDisputeFlag && !hasExplicitValidYear && !suppressOnly
      ? "Resolved"
      : cleanDecision;

  let finalCitation =
    inlineEdits.source_citation !== undefined
      ? sanitizeMonitoringText(inlineEdits.source_citation)
      : item.sourceCitation;
  let finalGeom =
    inlineEdits.geometry_geojson !== undefined
      ? String(inlineEdits.geometry_geojson).trim()
      : item.geometryGeojson;
  if (suppressOnly) {
    finalGeom = "";
    finalCitation = stripEmbeddedGeometryTag(finalCitation);
  }

  return {
    action: "moderate_submission",
    sourceTab: item.sourceTab,
    rowNumber: item.rowNumber,
    ...(item.sourceTab === "Field_Surveys"
      ? { survey_id: item.surveyId }
      : { expected_hcad_num: item.hcadNum }),
    decision: effectiveDecision,
    hcad_num: sanitizeMonitoringText(inlineEdits.hcad_num ?? item.hcadNum),
    address: sanitizeMonitoringText(inlineEdits.address ?? item.address),
    building_name: sanitizeMonitoringText(inlineEdits.building_name ?? item.buildingName),
    alt_names: sanitizeMonitoringText(inlineEdits.alt_names ?? item.altNames),
    historic_district: sanitizeMonitoringText(
      inlineEdits.historic_district ?? item.historicDistrict
    ),
    contributing: sanitizeMonitoringText(inlineEdits.contributing ?? item.contributing),
    original_hcad_year: item.originalHcadYear,
    suggested_year_built: suppressOnly
      ? 0
      : hasExplicitValidYear
      ? Number(editedYear)
      : editedYear,
    ...(suppressOnly || inlineEdits.suppress_only !== undefined
      ? { suppress_only: suppressOnly }
      : {}),
    bld_style: sanitizeMonitoringText(inlineEdits.bld_style ?? item.bldStyle),
    architect: sanitizeMonitoringText(inlineEdits.architect ?? item.architect),
    source_type: item.sourceType,
    source_citation: finalCitation,
    source_url: sanitizeMonitoringText(inlineEdits.source_url ?? item.sourceUrl),
    geometry_geojson: finalGeom,
    moderation_note: sanitizeMonitoringText(inlineEdits.moderation_note || ""),
  };
}
