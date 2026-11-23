/**
 * Pure, Zero-DOM Source Conflict Resolver & 1-Click `Approved_Edits` Writeback Builder.
 *
 * Surfaces side-by-side disagreements across HCAD, Planning Commission Plat filings,
 * Atlas dated building footprints, Curated Overrides (`Approved_Edits`), and Staff/Screener
 * parcel reviews (e.g. `1928 Washington Terrace` vs. `1940 Riverside` on `2802 Wheeler`),
 * and builds formula-safe, ghost-polygon-free `submitAdminApprovedOverride` payloads.
 */

import {
  normalizeHcad13,
  sanitizeMonitoringText,
} from "./parcelMonitoringModel.js?v=20261113c";

const GEOMETRY_TAG_SPAN_RE = /\[GEOMETRY_GEOJSON:\s*\{[\s\S]*?\}\s*\]/gi;

export function stripEmbeddedGeometryTagClient(rawCitation = "") {
  return sanitizeMonitoringText(String(rawCitation || "").replace(GEOMETRY_TAG_SPAN_RE, " "))
    .replace(/\s+/g, " ")
    .trim();
}

function validHistoricYearOrZero(val) {
  if (val === null || val === undefined || val === "") return null;
  const s = String(val).trim();
  if (s === "0" || /^(?:vacant|demolished)/i.test(s)) return 0;
  const m = s.match(/^(\d{4})/);
  if (!m) return null;
  const yr = Number(m[1]);
  return yr >= 1836 && yr <= 2035 ? yr : null;
}

/**
 * Detect side-by-side metadata disagreements across HCAD, Plat, Atlas Footprint,
 * `Approved_Edits`, and active Monitoring Parcel/Review records.
 */
export function detectParcelSourceConflicts({
  hcadNum = "",
  platItem = null,
  review = null,
  parcelRecord = null,
  atlasParcelMeta = null,
  curatedOverride = null,
} = {}) {
  const cleanHcad = normalizeHcad13(
    hcadNum ||
      parcelRecord?.hcad_num ||
      review?.hcad ||
      platItem?.hcad ||
      curatedOverride?.hcad_num ||
      ""
  );

  const yearCandidates = [];
  const pushYear = (source, rawVal) => {
    const yr = validHistoricYearOrZero(rawVal);
    if (yr !== null) {
      yearCandidates.push({
        source,
        value: yr === 0 ? "Demolished / Vacant (0)" : String(yr),
        numericYear: yr,
      });
    }
  };

  if (curatedOverride) {
    pushYear(
      "Approved_Edits Override",
      curatedOverride.suppress_only ? 0 : curatedOverride.year_built
    );
  }
  if (atlasParcelMeta?.oldestYearBuilt) {
    pushYear("Atlas Footprint", atlasParcelMeta.oldestYearBuilt);
  } else if (platItem?.atlas?.earliestYearBuilt) {
    pushYear("Atlas Pre-Screen", platItem.atlas.earliestYearBuilt);
  }
  if (parcelRecord?.hcad_year_built) {
    pushYear("HCAD Roll", parcelRecord.hcad_year_built);
  } else if (curatedOverride?.original_hcad_year) {
    pushYear("HCAD Roll", curatedOverride.original_hcad_year);
  }
  if (parcelRecord?.year_built) {
    pushYear("Monitoring Parcel", parcelRecord.year_built);
  }
  if (review?.yearBuilt) {
    pushYear("Plat Review", review.yearBuilt);
  }

  const distinctYearVals = Array.from(new Set(yearCandidates.map((c) => c.value)));

  // Neighborhood / Subdivision sources
  const areaCandidates = [];
  const pushArea = (source, rawVal) => {
    const s = sanitizeMonitoringText(rawVal);
    if (s && s.toLowerCase() !== "none" && s.toLowerCase() !== "not checked yet") {
      areaCandidates.push({ source, value: s });
    }
  };
  if (platItem?.prevSub) pushArea("Plat Previous Subdiv", platItem.prevSub);
  if (platItem?.superN) pushArea("COH Super Neighborhood", platItem.superN);
  if (parcelRecord?.subdivision_name) pushArea("HCAD / Historic Subdiv", parcelRecord.subdivision_name);
  if (curatedOverride?.historic_district) pushArea("Approved_Edits District", curatedOverride.historic_district);

  const distinctAreaNorms = new Set(
    areaCandidates.map((c) => c.value.toUpperCase().replace(/\s+/g, " "))
  );

  // Standing vs. Demolished / Vacant conflict
  const outcome = String(parcelRecord?.outcome || "").trim().toLowerCase();
  const parcelMarkedDemolished =
    outcome === "lost" ||
    outcome === "lost_before_review" ||
    Boolean(platItem?.atlas?.confirmedVacant);
  const overrideStillStanding = Boolean(
    curatedOverride && !curatedOverride.suppress_only && Number(curatedOverride.year_built) >= 1836
  );
  const unrecordedDemolition =
    parcelMarkedDemolished && (!curatedOverride || !curatedOverride.suppress_only);

  const fields = [];
  if (distinctYearVals.length > 1) {
    const preferredYr =
      yearCandidates.find((c) => c.source === "Plat Review" || c.source === "Monitoring Parcel") ||
      yearCandidates[0];
    fields.push({
      fieldKey: "year_built",
      label: "Year Built",
      valuesBySource: yearCandidates,
      recommendedValue: preferredYr ? preferredYr.value : "",
    });
  }

  if (distinctAreaNorms.size > 1) {
    fields.push({
      fieldKey: "neighborhood_or_subdivision",
      label: "Subdivision / Neighborhood",
      valuesBySource: areaCandidates,
      recommendedValue: areaCandidates[0]?.value || "",
    });
  }

  if (unrecordedDemolition || (parcelMarkedDemolished && overrideStillStanding)) {
    fields.push({
      fieldKey: "standing_status",
      label: "Standing vs. Demolished Status",
      valuesBySource: [
        {
          source: "Monitoring Outcome",
          value: outcome ? outcome.replace(/_/g, " ") : "Confirmed Vacant / Demolished",
        },
        {
          source: "Public Atlas Map",
          value: overrideStillStanding
            ? `Standing Override (${curatedOverride.year_built}${
                curatedOverride.geometry ? ", custom polygon" : ""
              })`
            : "Unsuppressed Footprint on Map",
        },
      ],
      recommendedValue: "Suppress Demolished / Vacant Lot on Public Atlas",
    });
  }

  const resolvedYr =
    validHistoricYearOrZero(review?.yearBuilt) ??
    validHistoricYearOrZero(parcelRecord?.year_built) ??
    validHistoricYearOrZero(platItem?.atlas?.earliestYearBuilt);

  const needsYearSync = Boolean(
    cleanHcad &&
      resolvedYr &&
      resolvedYr >= 1836 &&
      (!curatedOverride || Number(curatedOverride.year_built) !== resolvedYr)
  );

  return {
    hcadNum: cleanHcad,
    canWritebackToAtlas: Boolean(cleanHcad),
    hasConflicts: fields.length > 0,
    needsAtlasSync: fields.length > 0 || needsYearSync || unrecordedDemolition,
    isDemolishedWriteback: parcelMarkedDemolished,
    fields,
  };
}

/**
 * Build a clean `submitAdminApprovedOverride` payload for 1-click Public Atlas writeback.
 * Automatically strips stale `geometry_geojson` and `[GEOMETRY_GEOJSON:...]` tags when
 * suppressing a `Lost` / `Vacant Lot` parcel so ghost polygons never survive on the map.
 */
export function buildApprovedOverrideWritebackPayload({
  hcadNum = "",
  overrideKey = "",
  primaryAddress = "",
  buildingName = "",
  chosenYearBuilt = "",
  hcadYearBuilt = "",
  historicDistrict = "",
  contributing = "",
  architect = "",
  bldStyle = "",
  isDemolishedVacant = false,
  sourceType = "Preservation Houston Archival Review",
  sourceCitation = "",
  sourceUrl = "",
  existingOverride = null,
} = {}) {
  const cleanBaseHcad = normalizeHcad13(hcadNum || String(overrideKey).split("#")[0]);
  const targetKey =
    overrideKey && String(overrideKey).includes("#")
      ? sanitizeMonitoringText(overrideKey)
      : cleanBaseHcad;
  if (!targetKey) {
    throw new Error("A valid 13-digit HCAD account is required to update Approved_Edits.");
  }

  const parsedYr = validHistoricYearOrZero(chosenYearBuilt);
  const suppressVacant = Boolean(isDemolishedVacant || parsedYr === 0);

  if (!suppressVacant && (!parsedYr || parsedYr < 1836)) {
    throw new Error(
      "Enter a verified construction year (1836-2035) or select Demolished / Vacant Lot before publishing to the Public Atlas."
    );
  }

  let cleanCitation = sanitizeMonitoringText(
    sourceCitation || existingOverride?.source_citation || "Verified during Preservation Monitoring review."
  );
  if (suppressVacant) {
    cleanCitation = stripEmbeddedGeometryTagClient(cleanCitation);
  }

  const payload = {
    hcad_num: targetKey,
    address: sanitizeMonitoringText(primaryAddress || existingOverride?.address || "").toUpperCase(),
    building_name: sanitizeMonitoringText(buildingName || existingOverride?.building_name || ""),
    historic_district: sanitizeMonitoringText(
      historicDistrict || existingOverride?.historic_district || ""
    ),
    contributing: suppressVacant
      ? "Non-Contributing"
      : sanitizeMonitoringText(contributing || existingOverride?.contributing || ""),
    hcad_year_built: validHistoricYearOrZero(hcadYearBuilt ?? existingOverride?.original_hcad_year) || 0,
    suggested_year_built: suppressVacant ? 0 : parsedYr,
    suppress_only: suppressVacant,
    bld_style: sanitizeMonitoringText(bldStyle || existingOverride?.bld_style || ""),
    architect: sanitizeMonitoringText(architect || existingOverride?.architect || ""),
    footprint_issue: suppressVacant ? "Building demolished - lot currently vacant" : "",
    footprint_notes: suppressVacant
      ? "Confirmed demolished or vacant during Preservation Monitoring review."
      : "",
    geometry_geojson: suppressVacant
      ? ""
      : existingOverride?.geometry
      ? JSON.stringify(existingOverride.geometry)
      : "",
    source_type: sanitizeMonitoringText(sourceType),
    source_citation: cleanCitation,
    source_url: sanitizeMonitoringText(sourceUrl || existingOverride?.source_url || ""),
  };

  return payload;
}
