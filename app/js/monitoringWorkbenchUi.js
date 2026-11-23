/**
 * Pure, Zero-DOM HTML Builders & Parcel-to-Workbench State Mappers for Phase 1 Monitoring
 * (`monitoringWorkbenchUi.js`).
 *
 * Powers:
 *   1. Once-Per-Parcel Significance & Calculated `Act / Watch / Record` Assessment Card
 *      (with role-scoped `Screener` recommendation vs. `Staff` confirmation & 10-Minute Story Check).
 *   2. Side-by-Side Source Conflict Resolver & 1-Click `Approved_Edits` Public Atlas Writeback Card.
 *   3. Strictly `Staff`-gated Strategy, Outreach & 1-Click Watch Person/Firm Card (returns `""`
 *      with zero DOM nodes for `Screener` and unauthenticated visitors).
 *   4. In-App Community Moderation Queue (`Pending_Submissions` + Walk-the-Block `Field_Surveys`).
 *   5. Calm Signed-Out Privacy Gate on the Watchlist tab.
 */

import {
  MONITORING_SCHEMA_CONTRACT,
  classifyProtectionLevel,
  deriveLegacyTierFromParcel,
  explainAttentionLevel,
  migrateLegacyTierToParcelFields,
  normalizeParcelRecord,
  parseDelimitedList,
  parseRelatedHcads,
  resolveCanonicalParcelId,
  resolveEffectiveAreaContext,
  sanitizeMonitoringText,
} from "./parcelMonitoringModel.js?v=20261113c";
import { MODERATION_CATEGORIES, classifyModerationQueueItem } from "./communityModerationQueue.js?v=20261113c";

export { MODERATION_CATEGORIES };

export const STORY_CHECK_STEPS = Object.freeze([
  {
    key: "city_directories_1866_1926",
    legacyKeys: ["city_directories"],
    label: "1. Houston City Directories (1866-1926)",
    placeholder: "e.g. Listed by 1926 Morrison & Fourmy directory with early resident / grocer",
  },
  {
    key: "red_book_1915",
    legacyKeys: ["sanborn_footprint"],
    label: "2. 1915 Red Book & Sanborn Maps",
    placeholder: "e.g. Documented on 1924 Sanborn Vol. 3 Sheet 312 or 1915 Red Book of Houston",
  },
  {
    key: "hcad_deed_history",
    legacyKeys: ["architect_permit"],
    label: "3. Deed Chain, Architect & Original Permit",
    placeholder: "e.g. Pre-2004 Clerk deed chain, Mechanic's Lien, or original architect attribution",
  },
  {
    key: "local_partner_check",
    legacyKeys: ["neighborhood_context"],
    label: "4. Local Partner & Streetscape Context",
    placeholder: "e.g. Intact anchor corner on blockface; neighborhood civic or cultural history",
  },
]);

export const SIGNIFICANCE_BASIS_LABELS = Object.freeze({
  architecture: "Architecture / Style & Integrity",
  person_org_event: "Person, Organization / Architect or Event",
  community_cultural: "Community, Cultural & Streetscape Context",
});

export const INTEGRITY_LABELS = Object.freeze({
  good: "Good (Intact original form & openings)",
  fair: "Fair (Minor / reversible alterations)",
  poor: "Poor (Substantially altered)",
});

export const ADVOCACY_STATUS_LABELS = Object.freeze({
  monitoring: "Monitoring (Active Automated Sweeps)",
  active_advocacy: "Active Advocacy / Outreach & Escalation",
  recording_only: "Recording Only (Survey & Metrics)",
  closed: "Closed Case (Stage 6)",
});

export const OUTCOME_LABELS = Object.freeze({
  "": "(Open / No Terminal Outcome)",
  lost: "Lost - Historic Structure Demolished",
  lost_before_review: "Lost Before Review (Demolished Prior to Assessment)",
  saved: "Saved / Preserved",
  moved: "Relocated / Moved",
  altered: "Substantially Altered",
  no_longer_threatened: "No Longer Threatened / Filing Withdrawn",
  not_relevant: "Not Relevant (Vacant Lot / Post-Cutoff / Mismatched)",
});

function esc(val) {
  return String(val ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function parseStoryCheckJson(raw) {
  const base = {};
  for (const st of STORY_CHECK_STEPS) {
    base[st.key] = { checked: false, note: "" };
  }
  if (!raw) return base;
  try {
    const obj = typeof raw === "string" ? JSON.parse(raw) : raw;
    if (obj && typeof obj === "object") {
      for (const st of STORY_CHECK_STEPS) {
        let entry = obj[st.key];
        if (entry === undefined && Array.isArray(st.legacyKeys)) {
          for (const lk of st.legacyKeys) {
            if (obj[lk] !== undefined) {
              entry = obj[lk];
              break;
            }
          }
        }
        if (entry && typeof entry === "object") {
          base[st.key] = {
            checked: Boolean(entry.checked),
            note: sanitizeMonitoringText(entry.note || ""),
          };
        } else if (typeof entry === "string" && entry.trim()) {
          base[st.key] = { checked: true, note: sanitizeMonitoringText(entry) };
        }
      }
    }
  } catch (_e) {}
  return base;
}

export function serializeStoryCheck(storyObj = {}) {
  const clean = {};
  for (const st of STORY_CHECK_STEPS) {
    const item = storyObj[st.key] || {};
    const note = sanitizeMonitoringText(item.note || "");
    const checked = Boolean(item.checked || note);
    clean[st.key] = { checked, note };
  }
  return JSON.stringify(clean);
}

/**
 * Resolve a unified parcel + decision view-model for any Agenda item or Watchlist dossier,
 * merging authenticated cloud `Parcels`/`Decisions` with legacy Tier 1..4 fallback so the
 * `Act / Watch / Record` badge and Once-Per-Parcel controls always stay synchronized.
 */
export function resolveEffectiveParcelAssessment({
  platItem = null,
  review = null,
  watchProp = null,
  parcelsById = null,
  decisionsByParcelId = null,
  campusIndex = null,
  areasById = null,
  asOfDateCst = "",
} = {}) {
  const rawHcads = parseRelatedHcads([
    review?.hcad || "",
    platItem?.hcad || "",
    watchProp?.hcadAccount || "",
  ]);
  const primaryAddress = sanitizeMonitoringText(
    review?.address ||
      watchProp?.address ||
      platItem?.atlas?.primaryAddress ||
      platItem?.noticeLocation ||
      ""
  );
  const appNo = sanitizeMonitoringText(platItem?.app || watchProp?.appNo || "");
  const fallbackName = sanitizeMonitoringText(
    watchProp?.propertyName || platItem?.name || primaryAddress
  );

  const parcelId = resolveCanonicalParcelId(
    {
      parcelId: watchProp?.parcelId || review?.parcelId || "",
      campusGroupId: watchProp?.campusGroupId || "",
      hcadIds: rawHcads,
      appNo,
      address: primaryAddress,
      name: fallbackName,
    },
    campusIndex
  );

  const lookupMap = (m, k) => {
    if (!m || !k) return null;
    if (m instanceof Map) return m.get(k) || null;
    if (typeof m === "object") return m[k] || null;
    return null;
  };

  const cloudParcel = lookupMap(parcelsById, parcelId);
  const cloudDecision = lookupMap(decisionsByParcelId, parcelId);

  const desigText = sanitizeMonitoringText(
    cloudParcel?.designation_summary ||
      (review?.designation && review.designation !== "Not checked yet" ? review.designation : "") ||
      watchProp?.designation ||
      (Array.isArray(platItem?.atlas?.designations) ? platItem.atlas.designations.join(", ") : "") ||
      platItem?.hd ||
      ""
  );
  const yrBuilt =
    Number(
      cloudParcel?.year_built ||
        review?.yearBuilt ||
        watchProp?.yearBuilt ||
        platItem?.atlas?.earliestYearBuilt ||
        0
    ) || "";

  const atlasKnownHigh = Boolean(
    /\b(?:landmark|good\s+brick|national\s+register|rthl|state\s+antiquities)\b/i.test(desigText)
  );
  const legacyReviewTier = review?.derivedFromParcel ? "" : review?.tier || "";
  const legacySource = watchProp
    ? {
        tier: watchProp.tier || legacyReviewTier,
        trackerStatus: watchProp.trackerStatus || "",
        why: review?.why || "",
        notes: watchProp.notes || "",
        designation: desigText,
        yearBuilt: yrBuilt,
        graduateToLost: Boolean(watchProp.lostHoustonId || watchProp.trackerStatus === "Lost"),
      }
    : {
        tier: legacyReviewTier,
        trackerStatus: "",
        why: review?.why || "",
        followup: review?.followup || "",
        designation: desigText,
        yearBuilt: yrBuilt,
      };

  const defaultStage = platItem ? 3 : watchProp ? 3 : 0;
  const migratedFallback = migrateLegacyTierToParcelFields(legacySource, {
    isWatchlist: Boolean(watchProp),
    atlasKnownHigh,
    defaultThreatStage: defaultStage,
  });
  const hasAuthoritativeCloudRow = Boolean(cloudDecision || cloudParcel);

  const nextHearingDateCst = sanitizeMonitoringText(
    platItem?.hearingDateIso ||
      platItem?.pcDateIso ||
      watchProp?.hearingDateIso ||
      watchProp?.pcMeetingDate ||
      ""
  );

  const rawMerged = {
    parcel_id: parcelId,
    hcad_num: cloudParcel?.hcad_num || rawHcads[0] || "",
    campus_group_id: cloudParcel?.campus_group_id || (parcelId.startsWith("CAMPUS:") ? parcelId : ""),
    related_hcads: cloudParcel?.related_hcads || rawHcads.join("; "),
    primary_address: cloudParcel?.primary_address || primaryAddress,
    building_name: cloudParcel?.building_name || watchProp?.propertyName || platItem?.name || "",
    year_built: yrBuilt,
    hcad_year_built:
      Number(cloudParcel?.hcad_year_built || platItem?.atlas?.earliestYearBuilt || 0) || "",
    subdivision_name:
      cloudParcel?.subdivision_name ||
      platItem?.atlas?.subdivisionMatch ||
      platItem?.prevSub ||
      watchProp?.subdivisionMatch ||
      "",
    area_ids: cloudParcel?.area_ids || "",
    owner_of_record:
      cloudParcel?.owner_of_record || watchProp?.ownerOfRecord || platItem?.atlas?.owner || "",
    land_use_class: cloudParcel?.land_use_class || platItem?.landUse || "",
    review_status:
      cloudDecision?.review_status ||
      cloudParcel?.review_status ||
      watchProp?.reviewStatus ||
      migratedFallback.review_status,
    recommended_significance:
      cloudDecision?.recommended_significance ??
      cloudParcel?.recommended_significance ??
      migratedFallback.recommended_significance,
    significance_level: hasAuthoritativeCloudRow
      ? cloudDecision?.significance_level || cloudParcel?.significance_level || ""
      : watchProp?.significanceLevel || migratedFallback.significance_level,
    significance_basis:
      cloudDecision?.significance_basis ||
      cloudParcel?.significance_basis ||
      (Array.isArray(watchProp?.significanceBasis)
        ? watchProp.significanceBasis.join("; ")
        : ""),
    contributing_override:
      cloudDecision?.contributing_override || cloudParcel?.contributing_override || "",
    integrity: cloudDecision?.integrity || cloudParcel?.integrity || "",
    protection_level:
      cloudParcel?.protection_level ||
      watchProp?.protectionLevel ||
      classifyProtectionLevel(desigText, {
        inHistoricDistrict: Boolean(platItem?.hd || watchProp?.historicDistrict),
      }),
    designation_summary: desigText,
    threat_stage:
      cloudParcel?.threat_stage !== undefined && cloudParcel?.threat_stage !== ""
        ? Number(cloudParcel.threat_stage)
        : watchProp?.threatStage !== undefined
        ? Number(watchProp.threatStage)
        : migratedFallback.threat_stage,
    signal_flags: cloudParcel?.signal_flags || "",
    advocacy_status:
      cloudDecision?.advocacy_status ||
      cloudParcel?.advocacy_status ||
      watchProp?.advocacyStatus ||
      migratedFallback.advocacy_status,
    outcome:
      cloudDecision?.outcome ??
      cloudParcel?.outcome ??
      watchProp?.outcome ??
      migratedFallback.outcome,
    attention_override:
      cloudDecision?.attention_override ?? cloudParcel?.attention_override ?? "",
    watch_property_id: cloudParcel?.watch_property_id || watchProp?.id || "",
    next_hearing_date_cst: nextHearingDateCst,
    added_at: cloudParcel?.added_at || "",
    updated_by: cloudDecision?.decided_by || cloudParcel?.updated_by || "",
    updated_at: cloudDecision?.decided_at || cloudParcel?.updated_at || "",
  };

  const parcel = normalizeParcelRecord(rawMerged, { asOfDateCst, areasById });
  const areaContext = resolveEffectiveAreaContext(parcel.area_ids, areasById);
  const attentionExplanation = explainAttentionLevel(parcel, {
    asOfDateCst,
    nextHearingDateCst,
    areasById,
  });
  const derivedTier = deriveLegacyTierFromParcel(parcel);
  const screeningNotes = sanitizeMonitoringText(
    cloudDecision?.screening_notes || review?.why || watchProp?.notes || ""
  );
  const storyCheck = parseStoryCheckJson(cloudDecision?.story_check_json || "");
  const decidedAt = sanitizeMonitoringText(cloudDecision?.decided_at || "");

  return {
    parcelId,
    parcel,
    decision: cloudDecision || null,
    areaContext,
    attentionLevel: parcel.attention_level,
    attentionExplanation,
    derivedTier,
    screeningNotes,
    storyCheck,
    decidedAt,
  };
}

export function renderAttentionBadgeHtml(
  attentionLevel,
  { compact = false, overridden = false } = {}
) {
  const lvl = String(attentionLevel || "record").toLowerCase();
  const label =
    lvl === "act"
      ? compact
        ? "ACT"
        : "ACT · Escalate"
      : lvl === "watch"
      ? compact
        ? "WATCH"
        : "WATCH · Monitor"
      : compact
      ? "RECORD"
      : "RECORD · Log";
  const cls = lvl === "act" ? "att-act" : lvl === "watch" ? "att-watch" : "att-record";
  return `<span class="att-badge ${cls}${compact ? " compact" : ""}" data-attention-badge="${esc(lvl)}">${esc(label)}${overridden ? " *" : ""}</span>`;
}

export function renderThreatStagePillHtml(threatStage) {
  const st = Math.max(0, Math.min(6, Math.trunc(Number(threatStage) || 0)));
  const stageName = MONITORING_SCHEMA_CONTRACT.enums.threatStages[String(st)] || "None";
  const cls = st >= 4 && st <= 5 ? "critical" : st === 3 ? "strong" : st === 6 ? "" : "watch";
  return `<span class="chip ${cls}" title="Canonical Threat Escalation Ladder Stage ${st} of 6">Stage ${st}: ${esc(stageName)}</span>`;
}

export function renderProtectionPillHtml(protectionLevel) {
  const p = String(protectionLevel || "none").toLowerCase();
  if (p === "binding") {
    return `<span class="chip ok" title="Protected Landmark or COH Historic District - binding HAHC Certificate of Appropriateness review">Protection: Binding (Ch. 33 HAHC)</span>`;
  }
  if (p === "limited") {
    return `<span class="chip watch" title="City Landmark (90-day waiver), National Register, or RTHL - non-binding or time-limited delay">Protection: Limited / Advisory</span>`;
  }
  return `<span class="chip" title="No municipal demolition review protection">Protection: Unprotected</span>`;
}

export function renderParcelSignificanceCardHtml({
  assessment,
  role = "public",
  materialChanges = [],
  contextMode = "agenda",
} = {}) {
  if (!assessment || !assessment.parcel) return "";
  const p = assessment.parcel;
  const isStaff = role === "staff";
  const isScreener = role === "screener";
  const isSignedIn = isStaff || isScreener;

  const basesSelected = new Set(parseDelimitedList(p.significance_basis));
  const storySteps = STORY_CHECK_STEPS.map((st) => ({
    ...st,
    ...(assessment.storyCheck?.[st.key] || { checked: false, note: "" }),
  }));
  const storyCompletedCount = storySteps.filter((s) => s.checked || s.note).length;

  const areaStatus = assessment.areaContext?.priorityStatus || "outside";
  const areaCutoff = assessment.areaContext?.ageRuleCutoffYear || 1976;
  const areaPill =
    areaStatus === "priority"
      ? `<span class="chip strong">Priority Area (&lt;= ${areaCutoff})</span>`
      : areaStatus === "candidate"
      ? `<span class="chip watch">Candidate Area (&lt;= ${areaCutoff})</span>`
      : "";
  const contribLabel =
    p.contributing_status === "contributing"
      ? "Contributing"
      : p.contributing_status === "non_contributing"
      ? "Non-Contributing"
      : "Outside Tracked Area";

  const hasPendingScreenerRec = Boolean(
    p.recommended_significance && p.recommended_significance !== p.significance_level
  );

  const sigChoices = [
    ["high", "High Significance"],
    ["moderate", "Moderate Significance"],
    ["needs_research", "Needs Research"],
    ["not_significant", "Not Significant"],
  ];

  const activeScreenerVal =
    p.review_status === "needs_research" && !p.recommended_significance
      ? "needs_research"
      : p.recommended_significance || "";
  const activeStaffVal =
    p.review_status === "needs_research" && !p.significance_level
      ? "needs_research"
      : p.significance_level || "";

  return `
    <section class="parcel-sig-card" id="parcelSignificanceCard" data-parcel-id="${esc(assessment.parcelId)}">
      <div class="psc-head">
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          ${renderAttentionBadgeHtml(assessment.attentionLevel, {
            overridden: Boolean(p.attention_override),
          })}
          <span class="psc-explain">${esc(assessment.attentionExplanation)}</span>
        </div>
        <div class="chips" style="margin-top:0">
          ${renderThreatStagePillHtml(p.threat_stage)}
          ${renderProtectionPillHtml(p.protection_level)}
          ${areaPill}
          <span class="chip">${esc(contribLabel)}</span>
          <span class="chip" title="Canonical Parcel / Campus Key">${esc(assessment.parcelId)}</span>
        </div>
      </div>

      ${
        Array.isArray(materialChanges) && materialChanges.length > 0
          ? `<div class="psc-recheck-banner" data-parcel-recheck-banner="1">
              <b>⚡ Needs Re-Check Since Prior Cycle:</b> ${esc(materialChanges.join("; "))}. Parcel significance carries forward automatically; confirm whether advocacy status or attention should change.
            </div>`
          : ""
      }

      ${
        !isSignedIn
          ? `<div class="psc-public-note">
              <span><b>Once-Per-Parcel Significance &amp; Story Check:</b> Public permit and agenda signals are shown above. Sign in as Preservation Staff or Screener to record parcel significance, 10-minute story checks, and cross-cycle notes.</span>
              <button type="button" class="btn sm primary" data-open-staff-auth="1">🔒 Sign In</button>
            </div>`
          : `
            ${
              isStaff && hasPendingScreenerRec
                ? `<div class="psc-screener-rec-banner" id="screenerRecBanner">
                    <div>
                      <b>Screener Recommendation Pending Confirmation:</b> Recommended <b>${esc(p.recommended_significance.replace(/_/g, " "))}</b>${p.updated_by ? ` (${esc(p.updated_by)})` : ""}.
                    </div>
                    <button type="button" class="btn sm ok" id="approveScreenerSigBtn" data-approve-rec-sig="${esc(p.recommended_significance)}">✓ Confirm "${esc(p.recommended_significance.replace(/_/g, " "))}"</button>
                  </div>`
                : ""
            }

            <div class="psc-body">
              <div class="field" style="margin-bottom:10px">
                <label>
                  ${
                    isStaff
                      ? `Parcel Significance <span class="hint">assessed once per parcel; carries across future plat cycles</span>`
                      : `Recommend Parcel Significance <span class="hint">Screener recommendation${p.significance_level ? ` · Staff confirmed: ${esc(p.significance_level.replace(/_/g, " "))}` : ""}</span>`
                  }
                </label>
                <div class="psc-sig-btns" role="group" aria-label="Parcel significance">
                  ${sigChoices
                    .map(([val, lbl]) => {
                      const isOn = isStaff ? activeStaffVal === val : activeScreenerVal === val;
                      const attr = isStaff
                        ? `data-parcel-conf-sig="${val}"`
                        : `data-parcel-rec-sig="${val}"`;
                      return `<button type="button" class="psc-sig-btn ${isOn ? "on" : ""} sig-${val}" ${attr}>${esc(lbl)}</button>`;
                    })
                    .join("")}
                </div>
              </div>

              <div class="grid3" style="margin-bottom:8px">
                <div class="field" style="margin-bottom:4px">
                  <label>Significance Basis</label>
                  <div class="psc-basis-wrap">
                    ${Object.entries(SIGNIFICANCE_BASIS_LABELS)
                      .map(
                        ([bk, blbl]) =>
                          `<label class="psc-basis-chip ${basesSelected.has(bk) ? "on" : ""}">
                            <input type="checkbox" data-parcel-basis="${bk}" ${basesSelected.has(bk) ? "checked" : ""}>
                            <span>${esc(blbl)}</span>
                          </label>`
                      )
                      .join("")}
                  </div>
                </div>
                <div class="field" style="margin-bottom:4px">
                  <label for="psc-integrity">Physical Integrity</label>
                  <select id="psc-integrity" data-parcel-field="integrity">
                    <option value="">(Not graded yet)</option>
                    ${Object.entries(INTEGRITY_LABELS)
                      .map(
                        ([ik, ilbl]) =>
                          `<option value="${ik}" ${p.integrity === ik ? "selected" : ""}>${esc(ilbl)}</option>`
                      )
                      .join("")}
                  </select>
                </div>
                ${
                  isStaff
                    ? `<div class="field" style="margin-bottom:4px">
                        <label for="psc-contrib-ov">Contributing Status</label>
                        <select id="psc-contrib-ov" data-parcel-field="contributing_override">
                          <option value="" ${!p.contributing_override ? "selected" : ""}>Auto (${esc(contribLabel)})</option>
                          <option value="contributing" ${p.contributing_override === "contributing" ? "selected" : ""}>Override: Contributing</option>
                          <option value="non_contributing" ${p.contributing_override === "non_contributing" ? "selected" : ""}>Override: Non-Contributing</option>
                          <option value="outside_area" ${p.contributing_override === "outside_area" ? "selected" : ""}>Override: Outside Area</option>
                        </select>
                      </div>`
                    : ""
                }
              </div>

              ${
                isStaff
                  ? `<div class="grid3" style="margin-bottom:8px">
                      <div class="field" style="margin-bottom:4px">
                        <label for="psc-advocacy">Advocacy Status</label>
                        <select id="psc-advocacy" data-parcel-field="advocacy_status">
                          ${Object.entries(ADVOCACY_STATUS_LABELS)
                            .map(
                              ([ak, albl]) =>
                                `<option value="${ak}" ${p.advocacy_status === ak ? "selected" : ""}>${esc(albl)}</option>`
                            )
                            .join("")}
                        </select>
                      </div>
                      <div class="field" style="margin-bottom:4px">
                        <label for="psc-outcome">Case Outcome <span class="hint">separates Lost vs. Not Relevant</span></label>
                        <select id="psc-outcome" data-parcel-field="outcome">
                          ${Object.entries(OUTCOME_LABELS)
                            .map(
                              ([ok, olbl]) =>
                                `<option value="${ok}" ${p.outcome === ok ? "selected" : ""}>${esc(olbl)}</option>`
                            )
                            .join("")}
                        </select>
                      </div>
                      <div class="field" style="margin-bottom:4px">
                        <label for="psc-att-override">Attention Override</label>
                        <select id="psc-att-override" data-parcel-field="attention_override">
                          <option value="" ${!p.attention_override ? "selected" : ""}>Auto-Calculated (${esc(assessment.attentionLevel.toUpperCase())})</option>
                          <option value="act" ${p.attention_override === "act" ? "selected" : ""}>Force ACT</option>
                          <option value="watch" ${p.attention_override === "watch" ? "selected" : ""}>Force WATCH</option>
                          <option value="record" ${p.attention_override === "record" ? "selected" : ""}>Force RECORD</option>
                        </select>
                      </div>
                    </div>`
                  : ""
              }

              ${
                contextMode === "watchlist"
                  ? ""
                  : `<div class="field" style="margin-bottom:8px">
                      <label for="parcelScreeningNotesInput">Cross-Cycle Parcel Notes <span class="hint">uncapped; follows this parcel across future filings</span></label>
                      <textarea id="parcelScreeningNotesInput" rows="2" placeholder="Record architectural context, streetscape observations, or why this parcel matters...">${esc(assessment.screeningNotes || "")}</textarea>
                    </div>`
              }

              <details class="psc-story-drawer" id="storyCheckDrawer" ${storyCompletedCount > 0 ? "open" : ""}>
                <summary>
                  <span>⏱ 10-Minute Story Check (${storyCompletedCount}/4 steps checked)</span>
                  <span style="font-size:12px;color:var(--muted);font-weight:400">Sanborn · City Directories · Architect/Permit · Blockface</span>
                </summary>
                <div class="psc-story-grid">
                  ${storySteps
                    .map(
                      (st) => `
                      <div class="psc-story-row">
                        <label class="psc-story-chk">
                          <input type="checkbox" data-story-check="${esc(st.key)}" ${st.checked ? "checked" : ""}>
                          <span>${esc(st.label)}</span>
                        </label>
                        <input type="text" data-story-note="${esc(st.key)}" value="${esc(st.note)}" placeholder="${esc(st.placeholder)}">
                      </div>`
                    )
                    .join("")}
                </div>
              </details>
            </div>
          `
      }
    </section>
  `;
}

export function renderSourceConflictWritebackCardHtml({
  conflictReport,
  role = "public",
  defaultAddress = "",
  defaultBuildingName = "",
  defaultArchitect = "",
  defaultDistrict = "",
  defaultYearBuilt = "",
} = {}) {
  if (!conflictReport || !conflictReport.canWritebackToAtlas) return "";
  const isStaff = role === "staff";
  if (!conflictReport.hasConflicts && !isStaff) return "";

  const recYr =
    conflictReport.fields.find((f) => f.fieldKey === "year_built")?.recommendedValue ||
    defaultYearBuilt ||
    "";
  const numericDefaultYr = /^\d{4}$/.test(String(recYr)) ? String(recYr) : "";

  return `
    <section class="conflict-card ${conflictReport.hasConflicts ? "has-conflict" : ""}" id="sourceConflictWritebackCard">
      <div class="cc-head">
        <div>
          <b>${
            conflictReport.hasConflicts
              ? `⚖️ Source Disagreement Detected Across Records (${conflictReport.fields.length} field${conflictReport.fields.length === 1 ? "" : "s"})`
              : `🗺️ Public Atlas Curated Override Sync (HCAD ${esc(conflictReport.hcadNum)})`
          }</b>
          <div style="font-size:12px;color:var(--muted)">
            ${
              conflictReport.hasConflicts
                ? "Compare HCAD, Plat, Atlas footprint, and Approved_Edits values side-by-side and publish verified facts back to the public map."
                : "Publish verified construction year, architect, building name, or vacant-lot suppression directly to Approved_Edits."
            }
          </div>
        </div>
      </div>

      ${
        conflictReport.fields.length > 0
          ? `<div class="cc-diff-list">
              ${conflictReport.fields
                .map(
                  (f) => `
                  <div class="cc-diff-row" data-conflict-field="${esc(f.fieldKey)}">
                    <div class="cc-diff-lbl">${esc(f.label)}</div>
                    <div class="cc-diff-pills">
                      ${f.valuesBySource
                        .map(
                          (vs) =>
                            `<button type="button" class="assist-pill" data-pick-conflict-field="${esc(f.fieldKey)}" data-pick-conflict-val="${esc(vs.numericYear !== undefined ? String(vs.numericYear) : vs.value)}" title="Click to use ${esc(vs.source)} value"><b>${esc(vs.source)}:</b> ${esc(vs.value)}</button>`
                        )
                        .join("")}
                    </div>
                  </div>`
                )
                .join("")}
            </div>`
          : ""
      }

      ${
        isStaff
          ? `<details ${conflictReport.hasConflicts ? "open" : ""} style="margin-top:8px">
              <summary style="cursor:pointer;font-size:12.5px;font-weight:600;color:var(--survey)">
                1-Click Update Public Atlas (Approved_Edits)
              </summary>
              <div class="grid3" style="margin-top:8px">
                <div class="field" style="margin-bottom:6px">
                  <label for="wb-year">Verified Year Built</label>
                  <input id="wb-year" type="text" inputmode="numeric" value="${esc(numericDefaultYr)}" placeholder="e.g. 1928">
                </div>
                <div class="field" style="margin-bottom:6px">
                  <label for="wb-name">Building / Historic Name</label>
                  <input id="wb-name" type="text" value="${esc(defaultBuildingName)}" placeholder="Optional historic name">
                </div>
                <div class="field" style="margin-bottom:6px">
                  <label for="wb-arch">Architect / Style</label>
                  <input id="wb-arch" type="text" value="${esc(defaultArchitect)}" placeholder="Optional architect">
                </div>
              </div>
              <div class="grid2" style="margin-top:4px">
                <div class="field" style="margin-bottom:6px">
                  <label for="wb-cite">Verification Citation</label>
                  <input id="wb-cite" type="text" placeholder="e.g. Verified via 1928 Houston City Directory &amp; Sanborn Vol. 3">
                </div>
                <div class="field" style="margin-bottom:6px;justify-content:flex-end">
                  <label style="display:inline-flex;gap:6px;align-items:center;cursor:pointer;margin-top:18px">
                    <input id="wb-suppress-vacant" type="checkbox" ${conflictReport.isDemolishedWriteback ? "checked" : ""}>
                    <span>Suppress as Demolished / Vacant Lot on Public Map (clears stale polygons)</span>
                  </label>
                </div>
              </div>
              <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;margin-top:4px">
                <input type="hidden" id="wb-hcad" value="${esc(conflictReport.hcadNum)}">
                <input type="hidden" id="wb-addr" value="${esc(defaultAddress)}">
                <input type="hidden" id="wb-dist" value="${esc(defaultDistrict)}">
                <span id="wb-status-msg" style="font-size:12px;color:var(--muted)">Writes directly to Approved_Edits and live map overrides.</span>
                <button type="button" class="btn sm primary" id="publishApprovedOverrideBtn">✓ Publish to Public Atlas (Approved_Edits)</button>
              </div>
            </details>`
          : ""
      }
    </section>
  `;
}

/**
 * Strictly `Staff`-only Strategy, Outreach & 1-Click Watch Person / Firm Card.
 * Returns `""` (zero DOM nodes) whenever `role !== "staff"`.
 */
export function renderStaffOnlyStrategyCardHtml({
  parcelId = "",
  strategyRecord = null,
  candidateEntities = [],
  watchedEntities = [],
  role = "public",
} = {}) {
  if (role !== "staff" || !parcelId) return "";
  const st = strategyRecord || {};
  const watchedKeys = new Set(
    (Array.isArray(watchedEntities) ? watchedEntities : [])
      .filter((w) => w && w.active !== false && String(w.active).toLowerCase() !== "false")
      .map((w) => sanitizeMonitoringText(w.entity_name || "").toUpperCase())
      .filter(Boolean)
  );
  const cleanCandidates = Array.from(
    new Set(
      (Array.isArray(candidateEntities) ? candidateEntities : [])
        .map((c) => sanitizeMonitoringText(c))
        .filter((c) => c && c.length >= 3 && !/^see hcad|^not recorded|^none/i.test(c))
    )
  );
  const activeWatched = (Array.isArray(watchedEntities) ? watchedEntities : []).filter(
    (w) => w && w.active !== false && String(w.active).toLowerCase() !== "false"
  );

  return `
    <section class="staff-strategy-card" id="staffOnlyStrategyCard" data-staff-only-card="1" data-parcel-id="${esc(parcelId)}">
      <div class="ssc-head">
        <div>
          <b>🔒 Staff-Only Advocacy Strategy, Contacts &amp; Watched Entities</b>
          <span class="chip" style="background:#FEF3C7;color:#92400E;border:1px solid #FDE68A;margin-left:6px">Staff Restricted Vault</span>
        </div>
        <span style="font-size:11.5px;color:var(--muted)">Stored in isolated Staff_Only workbook · Hidden from Screeners &amp; public bundle</span>
      </div>
      <div class="grid2" style="margin-top:8px">
        <div class="field" style="margin-bottom:6px">
          <label for="so-strategy-notes">Private Advocacy Strategy &amp; Negotiation Notes</label>
          <textarea id="so-strategy-notes" rows="2" placeholder="Confidential partner notes, acquisition leads, council office discussions...">${esc(st.strategy_notes ?? st.private_strategy_notes ?? "")}</textarea>
        </div>
        <div class="field" style="margin-bottom:6px">
          <label for="so-outreach-log">Owner / Partner Outreach &amp; Relationship Notes</label>
          <textarea id="so-outreach-log" rows="2" placeholder="Direct owner/attorney contacts, outreach dates, confidential status...">${esc(st.owner_relationship_notes ?? st.owner_outreach_log ?? "")}</textarea>
        </div>
      </div>
      <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:8px">
        <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
          <label for="so-contacts" style="font-size:12px;font-weight:600">Key Contact Phone / Email:</label>
          <input id="so-contacts" type="text" value="${esc(st.contact_notes ?? st.sensitive_contacts ?? "")}" placeholder="Optional private contact info" style="padding:4px 8px;font-size:12.5px;border:1px solid var(--line);border-radius:4px;min-width:210px;background:#fff">
        </div>
        <button type="button" class="btn sm primary" id="saveStaffStrategyBtn" data-parcel-id="${esc(parcelId)}">💾 Save Staff-Only Notes</button>
      </div>

      <div class="ssc-entities-bar">
        <div style="font-size:12px;font-weight:600;margin-bottom:4px">
          👁️ 1-Click Watch Repeat Developer / Firm / Principal (Flags Future Plat &amp; Permit Filings)
        </div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">
          ${
            cleanCandidates.length > 0
              ? cleanCandidates
                  .map((ent) => {
                    const isWatched = watchedKeys.has(ent.toUpperCase());
                    return `<button type="button" class="assist-pill ${isWatched ? "ok" : ""}" data-watch-entity-toggle="${esc(ent)}" title="${isWatched ? "Already on Staff Watched Entities list" : "Click to flag future filings by this developer/firm"}">${isWatched ? "✓ Watching: " : "+ Watch Firm: "}${esc(ent)}</button>`;
                  })
                  .join("")
              : `<span style="font-size:12px;color:var(--muted)">Add developer, owner, or applicant names to enable 1-click firm watch.</span>`
          }
          <input id="so-custom-entity-input" type="text" placeholder="Add developer / LLC / architect..." style="padding:4px 8px;font-size:12px;border:1px solid var(--line);border-radius:4px;width:205px;background:#fff">
          <button type="button" class="btn sm" id="addCustomWatchedEntityBtn">+ Watch</button>
        </div>
        ${
          activeWatched.length > 0
            ? `<div style="margin-top:6px;display:flex;gap:5px;flex-wrap:wrap;align-items:center">
                <span style="font-size:11.5px;color:var(--muted)">Active Watched Firms (${activeWatched.length}):</span>
                ${activeWatched
                  .slice(0, 18)
                  .map(
                    (w) =>
                      `<span class="doc-pill" style="font-size:11.5px"><span>${esc(w.entity_name)}</span><button type="button" data-remove-watched-entity="${esc(w.entity_id || w.entity_name)}" title="Stop watching ${esc(w.entity_name)}" aria-label="Remove ${esc(w.entity_name)}">×</button></span>`
                  )
                  .join("")}
              </div>`
            : ""
        }
      </div>
    </section>
  `;
}

export function renderSignedOutWatchlistGateHtml() {
  return `
    <div class="wl-card" id="watchlistSignedOutGate" data-signed-out-watchlist-gate="1" style="max-width:680px;margin:18px auto;padding:22px 24px">
      <h2 style="font-size:19px;margin:0 0 8px">🔒 Staff &amp; Screener Sign-In Required for Investigative Watchlist Dossiers</h2>
      <p style="font-size:13.5px;color:var(--muted);margin:0 0 14px;line-height:1.55">
        Raw municipal and state public filings remain openly accessible without signing in across the
        <b>Biweekly Plat &amp; Variance Review</b> tab, the <b>What's New &amp; Daily Log</b> briefing, and the
        <b>Active Permit &amp; Demolition Alerts</b> layer on the main Houston Building Atlas map.
        Internal Preservation Houston Watchlist dossiers, parcel significance assessments, and screening notes
        are synced privately through Google Workspace for authorized Staff and Screeners.
      </p>
      <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center">
        <button type="button" class="btn primary" data-open-staff-auth="1">🔒 Sign In as Staff or Screener</button>
        <button type="button" class="btn" data-switch-public-mode="agenda">Browse Public Plat Agendas</button>
        <a class="btn" href="index.html#layers=permit_watch,plat_watch,hahc_coa">Open Public Permit Alerts Map ↗</a>
      </div>
    </div>
  `;
}

export function renderModerationQueueListHtml(items = [], selectedQueueId = "") {
  if (!Array.isArray(items) || items.length === 0) {
    return `<div class="list-empty">✓ Zero pending community submissions or field surveys match this filter.</div>`;
  }
  return items
    .map((raw) => {
      const it = raw && raw.queueId && raw.category ? raw : classifyModerationQueueItem(raw);
      const title = it.address || it.buildingName || (it.hcadNum ? `HCAD ${it.hcadNum}` : it.surveyId || "Submission");
      const badgeCls = it.isDisputeFlag
        ? "strong"
        : it.isVacantReport
        ? "critical"
        : it.isFieldSurvey
        ? "ok"
        : "hearing";
      return `<button type="button" class="row" data-mod-queue-id="${esc(it.queueId)}" aria-current="${String(it.queueId === selectedQueueId)}">
        <div class="top">
          <span class="nm">${esc(title)}</span>
          <span class="app">${esc(it.submittedAt ? it.submittedAt.slice(0, 10) : it.sourceTab)}</span>
        </div>
        <div class="sub">${esc([it.buildingName, it.hcadNum ? `HCAD ${it.hcadNum}` : "", it.historicDistrict].filter(Boolean).join(" · ") || it.sourceType)}</div>
        <div class="chips">
          <span class="chip ${badgeCls}">${esc(it.categoryBadge)}</span>
          ${it.suggestedYearBuilt !== "" ? `<span class="chip">Year: ${esc(it.suggestedYearBuilt === "0" ? "Vacant (0)" : it.suggestedYearBuilt)}</span>` : ""}
          ${it.hasCustomGeom ? `<span class="chip watch">Custom Polygon</span>` : ""}
        </div>
      </button>`;
    })
    .join("");
}

export function renderModerationQueueDetailHtml(rawItem) {
  if (!rawItem) {
    return `<div class="list-empty">Select a pending community submission or Walk-the-Block survey on the left to review and publish without opening Google Sheets.</div>`;
  }
  const it = rawItem.queueId && rawItem.category ? rawItem : classifyModerationQueueItem(rawItem);
  const mapHref = it.hcadNum
    ? `index.html?hcad=${encodeURIComponent(it.hcadNum.split("#")[0])}&q=${encodeURIComponent(it.address || it.hcadNum)}`
    : "";

  return `
    <div class="wl-card mod-queue-card" id="moderationDetailCard" data-mod-queue-id="${esc(it.queueId)}">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap;margin-bottom:10px">
        <div>
          <h2 style="margin:0 0 4px">${esc(it.address || it.buildingName || it.hcadNum || "Community Submission")}</h2>
          <div class="chips">
            <span class="chip hearing">${esc(it.sourceTab)} · Row #${esc(it.rowNumber)}</span>
            <span class="chip strong">${esc(it.categoryBadge)}</span>
            ${it.submittedBy ? `<span class="chip">Submitted by: ${esc(it.submittedBy)}</span>` : ""}
            ${it.submittedAt ? `<span class="chip">${esc(it.submittedAt)}</span>` : ""}
          </div>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          ${mapHref ? `<a class="btn sm" href="${esc(mapHref)}" target="_blank" rel="noopener">🗺️ Inspect Parcel on Atlas Map ↗</a>` : ""}
          ${it.sourceUrl && /^https?:\/\//i.test(it.sourceUrl) ? `<a class="btn sm" href="${esc(it.sourceUrl)}" target="_blank" rel="noopener">🔗 Open Submitted Source Link ↗</a>` : ""}
        </div>
      </div>

      ${
        it.isDisputeFlag
          ? `<div class="history-note" style="background:#FFF8EB;border-color:#E7C68E;color:#784C07;margin-bottom:12px">
              <b>⚑ Dispute / Misattribution Flag (${esc(it.sourceType)}):</b> Clicking <b>${esc(it.primaryApproveLabel)}</b> marks this flag <code>Resolved</code> in <code>Pending_Submissions</code> without writing a blank construction year into <code>Approved_Edits</code> (unless you enter a verified standing year 1836-2035 or check Vacant Lot below).
            </div>`
          : ""
      }

      <div class="grid3">
        <div class="field">
          <label for="mq-hcad">HCAD Account # / Building ID</label>
          <input id="mq-hcad" value="${esc(it.hcadNum)}">
        </div>
        <div class="field">
          <label for="mq-address">Street Address</label>
          <input id="mq-address" value="${esc(it.address)}">
        </div>
        <div class="field">
          <label for="mq-year">Suggested Year Built <span class="hint">(original HCAD: ${esc(it.originalHcadYear || "N/A")})</span></label>
          <input id="mq-year" value="${esc(it.suggestedYearBuilt)}" placeholder="e.g. 1924 (or 0 for Vacant)">
        </div>
      </div>

      <div class="grid3">
        <div class="field">
          <label for="mq-name">Building Name</label>
          <input id="mq-name" value="${esc(it.buildingName)}">
        </div>
        <div class="field">
          <label for="mq-district">Historic District</label>
          <input id="mq-district" value="${esc(it.historicDistrict)}">
        </div>
        <div class="field">
          <label for="mq-contributing">Contributing Status</label>
          <input id="mq-contributing" value="${esc(it.contributing)}">
        </div>
      </div>

      <div class="grid2">
        <div class="field">
          <label for="mq-style">Architectural Style</label>
          <input id="mq-style" value="${esc(it.bldStyle)}">
        </div>
        <div class="field">
          <label for="mq-arch">Architect / Builder</label>
          <input id="mq-arch" value="${esc(it.architect)}">
        </div>
      </div>

      ${
        it.isFieldSurvey
          ? `<div class="history-note" style="margin-bottom:10px">
              <b>🚶 Walk-the-Block Field Survey Metadata:</b>
              Integrity: <b>${esc(it.integrityGrade || "N/A")}</b> · Condition: <b>${esc(it.conditionRating || "N/A")}</b>
              ${it.notes ? `<div style="margin-top:4px">${esc(it.notes)}</div>` : ""}
            </div>`
          : ""
      }

      <div class="field">
        <label for="mq-citation">Evidence / Source Citation (${esc(it.sourceType || "Community")})</label>
        <textarea id="mq-citation" rows="3">${esc(it.sourceCitation)}</textarea>
      </div>

      <div class="grid2">
        <div class="field">
          <label for="mq-url">Evidence URL</label>
          <input id="mq-url" value="${esc(it.sourceUrl)}">
        </div>
        <div class="field">
          <label for="mq-note">Staff Moderation Note (Optional)</label>
          <input id="mq-note" placeholder="Optional internal review note...">
        </div>
      </div>

      <div style="margin:6px 0 12px">
        <label style="display:inline-flex;gap:6px;align-items:center;cursor:pointer;font-size:13px">
          <input type="checkbox" id="mq-suppress-vacant" ${it.isVacantReport ? "checked" : ""}>
          <span>Demolished / Vacant Lot Suppression (sets Year = 0 and strips any stale custom polygon)</span>
        </label>
      </div>

      <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;padding-top:10px;border-top:1px solid var(--line-soft)">
        <div style="display:flex;gap:6px;flex-wrap:wrap">
          <button type="button" class="btn ok" data-mod-decision="Approved">✓ ${esc(it.primaryApproveLabel)}</button>
          <button type="button" class="btn" data-mod-decision="Needs Info">Needs Info</button>
          <button type="button" class="btn" data-mod-decision="On Hold">On Hold</button>
          <button type="button" class="btn" data-mod-decision="Duplicate">Duplicate</button>
        </div>
        <button type="button" class="btn" style="color:var(--t1);border-color:#E5B4B0" data-mod-decision="${it.isFieldSurvey ? "Dismissed" : "Rejected"}">✕ ${it.isFieldSurvey ? "Dismiss Survey" : "Reject"}</button>
      </div>
    </div>
  `;
}
