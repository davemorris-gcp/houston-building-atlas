/**
 * Pure archival record lookup, chronological replacement-structure guards, and HTML card helpers
 * for TARO 2.0 (txarchives.org), HPL ContentDM architectural collections, and USCO post-1926
 * Houston City Directory copyright clearance metadata.
 */

import { isClaimSuppressed } from "./curatedEdits.js?v=20261110a";

export const HPL_SURVEY_CEILINGS = Object.freeze({
  "hpl:p17006coll220": 1981,
  "hpl:p17006coll41": 1984,
  "hpl:p17006coll238": 1990,
});

const MONTH_NAMES_UTC = Object.freeze([
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
]);

function escapeHtml(raw) {
  return String(raw ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function escapeHtmlAttr(raw) {
  return escapeHtml(raw);
}

/**
 * Normalize a situs street address for `archival_index.json["by_addr"]` / `haif_index` lookups.
 */
export function normalizeArchivalLookupAddress(addr) {
  if (!addr) return "";
  const s = String(addr)
    .toUpperCase()
    .replace(/[^A-Z0-9\s]/g, " ")
    .replace(/\b(?:STE|SUITE|APT|UNIT|BLDG|FL|FLOOR)\b.*$/, "");
  const parts = s.trim().split(/\s+/);
  if (!parts.length || !/^\d+$/.test(parts[0]) || Number(parts[0]) <= 0) return "";
  const stripTail = new Set([
    "HOUSTON",
    "TX",
    "TEXAS",
    "BAYTOWN",
    "PASADENA",
    "BELLAIRE",
    "ST",
    "STREET",
    "AVE",
    "AVENUE",
    "BLVD",
    "BOULEVARD",
    "RD",
    "ROAD",
    "DR",
    "DRIVE",
    "LN",
    "LANE",
    "WAY",
    "PKWY",
    "PARKWAY",
    "FWY",
    "FREEWAY",
    "HWY",
    "HIGHWAY",
    "CT",
    "COURT",
    "PL",
    "PLACE",
    "CIR",
    "CIRCLE",
    "SQ",
    "SQUARE",
    "TER",
    "TERRACE",
    "TRL",
    "TRAIL",
    "LOOP",
    "N",
    "S",
    "E",
    "W",
    "NORTH",
    "SOUTH",
    "EAST",
    "WEST",
  ]);
  while (
    parts.length > 2 &&
    (/^\d+$/.test(parts[parts.length - 1]) || stripTail.has(parts[parts.length - 1]))
  ) {
    parts.pop();
  }
  const dirMap = { NORTH: "N", SOUTH: "S", EAST: "E", WEST: "W" };
  if (parts.length >= 3 && dirMap[parts[1]]) {
    parts[1] = dirMap[parts[1]];
  }
  return parts.join(" ");
}

/**
 * Resolve up to 6 deduplicated TARO 2.0 / HPL ContentDM archival records for an inspected feature,
 * enforcing `lost_*` isolation, replacement-building and sub-building chronological ceilings, and
 * 1-click claim suppression (`field: "archival_record"`).
 */
export function resolveArchivalRecords(archivalIndex, props = {}, options = {}) {
  if (!archivalIndex || !archivalIndex.items) return [];
  const itemsDict = archivalIndex.items;
  const collsDict = archivalIndex.collections || {};
  const byKey = archivalIndex.by_key || {};
  const byAddr = archivalIndex.by_addr || {};
  const globalRejections = options.globalRejections || null;
  const normalizeAddress =
    typeof options.normalizeAddress === "function"
      ? options.normalizeAddress
      : normalizeArchivalLookupAddress;

  const rawId = String(props.id || "").trim();
  const baseId = rawId.replace(/#(?:fp|aux)_\d+$/, "");
  const hcad = String(props.hcad_num || "").trim();
  const isLost =
    Boolean(props.is_lost_houston) ||
    rawId.startsWith("lost_") ||
    String(props.type || "") === "lost_houston";
  const suppressKey = isLost ? baseId || rawId : hcad || baseId || rawId;
  const buildingYear = Number(props.year_built) || 0;

  const candidateEntries = [];
  const appendIds = (arr, viaAddr = false) => {
    if (!Array.isArray(arr)) return;
    for (const aid of arr) {
      candidateEntries.push({ aid: String(aid), viaAddr });
    }
  };

  appendIds(byKey[rawId], false);
  if (baseId && baseId !== rawId) appendIds(byKey[baseId], false);
  const isIndirectParcelLookup = Boolean(rawId && rawId !== hcad && baseId !== hcad);
  if (!isLost && hcad) appendIds(byKey[hcad], isIndirectParcelLookup);

  if (!isLost && props.address) {
    const normA = normalizeAddress(props.address);
    if (normA) {
      appendIds(byAddr[normA], true);
      const parts = normA.split(" ");
      if (parts.length >= 3 && ["N", "S", "E", "W", "NE", "NW", "SE", "SW"].includes(parts[1])) {
        const noDir = `${parts[0]} ${parts.slice(2).join(" ")}`;
        appendIds(byAddr[noDir], true);
      }
    }
  }

  const out = [];
  const seenKeys = new Set();
  for (const { aid, viaAddr } of candidateEntries) {
    const rec = itemsDict[aid];
    if (!rec) continue;
    const title = String(rec.t || "Archival Architectural Record").trim();
    const itemYear = Number(rec.y) || 0;
    const surveyCeiling = HPL_SURVEY_CEILINGS[rec.c] || 0;
    if (viaAddr && !isLost && buildingYear > 0 && itemYear > 0 && buildingYear > itemYear + 20) {
      continue;
    }
    if (
      viaAddr &&
      !isLost &&
      buildingYear > 0 &&
      itemYear === 0 &&
      surveyCeiling > 0 &&
      buildingYear > surveyCeiling + 5
    ) {
      continue;
    }
    if (
      isClaimSuppressed(
        { hcadNum: suppressKey, field: "archival_record", value: title },
        globalRejections
      )
    ) {
      continue;
    }
    const coll = collsDict[rec.c] || {};
    const url = String(rec.u || coll.url || "").trim();
    const boxFolder = String(rec.b || "").trim();
    const dedupKey = `${url}|${title.toLowerCase()}|${boxFolder.toLowerCase()}`;
    if (!url || seenKeys.has(dedupKey)) continue;
    seenKeys.add(dedupKey);

    const isHpl =
      String(rec.c || "").startsWith("hpl:") ||
      coll.kind === "hpl_cdm" ||
      coll.source === "hpl_cdm";
    out.push({
      id: aid,
      suppressKey,
      title,
      url,
      boxFolder,
      date: String(rec.d || (itemYear > 0 ? itemYear : "")).trim(),
      year: itemYear,
      architect: String(rec.a || "").trim(),
      excerpt: String(rec.e || "").trim(),
      thumbUrl: String(rec.img || "").trim(),
      matchTier: String(rec.m || "address"),
      repoLabel: String(
        coll.repo ||
          (isHpl
            ? "Houston Public Library Digital Archives"
            : "Texas Archival Resources Online (TARO)")
      ),
      collectionTitle: String(coll.title || "Architectural Archive Collection"),
      sourceType: isHpl ? "hpl_cdm" : "taro",
    });
    if (out.length >= 6) break;
  }
  return out;
}

/**
 * Build the History-tab `#inspector-archival-card` HTML string from resolved archival records.
 */
export function buildArchivalDrawingsCardHtml(archivalRecords = []) {
  if (!Array.isArray(archivalRecords) || archivalRecords.length === 0) return "";
  return `<div class="archival-drawings-card" id="inspector-archival-card">
    <div class="archival-drawings-header">
      <span class="archival-drawings-kicker">&#128220; Archival Drawings &amp; Finding Aids (TARO &amp; HPL)</span>
      <span class="archival-drawings-count-badge">${archivalRecords.length} ${
        archivalRecords.length === 1 ? "Record" : "Records"
      }</span>
    </div>
    <div class="archival-record-list">
      ${archivalRecords
        .map(
          (rec) => `<div class="archival-record-item">
            <div class="archival-record-top">
              <span class="archival-repo-pill">${escapeHtml(rec.repoLabel)}</span>
              ${
                rec.boxFolder
                  ? `<span class="archival-box-pill mono">${escapeHtml(rec.boxFolder)}</span>`
                  : ""
              }
              ${rec.date ? `<span class="archival-record-date">${escapeHtml(rec.date)}</span>` : ""}
            </div>
            <div class="archival-record-body">
              ${
                rec.thumbUrl
                  ? `<a
                      href="${escapeHtmlAttr(rec.url)}"
                      target="_blank"
                      rel="noopener noreferrer"
                      class="archival-thumb-link"
                      title="View digitized archival item in ${escapeHtmlAttr(rec.repoLabel)}"
                    >
                      <img
                        src="${escapeHtmlAttr(rec.thumbUrl)}"
                        alt="${escapeHtmlAttr(rec.title)}"
                        loading="lazy"
                        class="archival-thumb-img"
                        data-archival-thumb="1"
                      />
                    </a>`
                  : ""
              }
              <div class="archival-record-main">
                <div class="archival-record-title">${escapeHtml(rec.title)}</div>
                <div class="archival-record-collection">Collection: ${escapeHtml(
                  rec.collectionTitle
                )}</div>
                ${
                  rec.architect
                    ? `<div class="archival-record-arch-row">
                        <span class="archival-record-arch-label">Architect:</span>
                        <button
                          type="button"
                          class="inspector-filter-chip archival-arch-chip"
                          data-filter-chip="${escapeHtmlAttr(rec.architect)}"
                          data-filter-label="Architect: ${escapeHtmlAttr(rec.architect)}"
                          title="Filter map to works by ${escapeHtmlAttr(rec.architect)}"
                        >${escapeHtml(rec.architect)} &#128269;</button>
                      </div>`
                    : ""
                }
                ${
                  rec.excerpt
                    ? `<div class="archival-record-excerpt">${escapeHtml(rec.excerpt)}</div>`
                    : ""
                }
              </div>
            </div>
            <div class="archival-record-actions">
              <a
                href="${escapeHtmlAttr(rec.url)}"
                target="_blank"
                rel="noopener noreferrer"
                class="archival-record-btn primary"
                title="Open ${escapeHtmlAttr(rec.collectionTitle)} in ${escapeHtmlAttr(
                  rec.repoLabel
                )}"
              >
                ${
                  rec.sourceType === "hpl_cdm"
                    ? "View HPL Archival Item"
                    : "Open TARO Finding Aid"
                } &#8599;
              </a>
              <button
                type="button"
                class="quick-flag-btn"
                data-quick-flag="claim"
                data-flag-hcad="${escapeHtmlAttr(rec.suppressKey || "")}"
                data-flag-field="archival_record"
                data-flag-value="${escapeHtmlAttr(rec.title)}"
                data-flag-provenance="${escapeHtmlAttr(
                  `${rec.repoLabel}: ${rec.collectionTitle}`
                )}"
                title="Flag this automated archival finding-aid match as incorrect; hides locally and queues for Preservation Houston review"
              >&#9873; Flag Match</button>
            </div>
          </div>`
        )
        .join("")}
    </div>
  </div>`;
}

function formatVerifiedMonthYear(isoDate) {
  const m = /^(\d{4})-(\d{2})(?:-\d{2})?$/.exec(String(isoDate || "").trim());
  if (!m) return "November 2026";
  const monthIdx = Number(m[2]) - 1;
  if (monthIdx < 0 || monthIdx > 11) return m[1];
  return `${MONTH_NAMES_UTC[monthIdx]} ${m[1]}`;
}

/**
 * Return true when a citydir_index.json record represents a pre-1927 (1866-1926) directory sighting.
 */
export function isPre1927CityDirHit(hit) {
  const earliest = Number(hit?.e) || 0;
  return earliest > 0 && earliest <= 1926;
}

/**
 * Construct an Ancestry Library Edition (dbId=2469) image viewer URL from an imageId.
 */
export function buildAncestryCityDirImageUrl(imageId) {
  const cleanId = String(imageId || "").trim();
  return cleanId
    ? `https://www.ancestry.com/imageviewer/collections/2469/images/${encodeURIComponent(cleanId)}`
    : "";
}

/**
 * Build the Research Kit Step 2 post-1926 USCO copyright clearance guidance note HTML.
 */
export function buildPost1926CityDirNoteHtml(yr, post1926Clearance = null) {
  const spanArr =
    Array.isArray(post1926Clearance?.span) && post1926Clearance.span.length === 2
      ? post1926Clearance.span
      : [1927, 1963];
  const spanLabel = `${Number(spanArr[0]) || 1927}-${Number(spanArr[1]) || 1963}`;
  const rawRenewals = post1926Clearance?.usco_class_r_re_renewals_found;
  const renewalsCount = Number.isFinite(Number(rawRenewals)) ? Number(rawRenewals) : 0;
  const allPublicDomain =
    post1926Clearance?.all_1927_1963_public_domain !== undefined
      ? Boolean(post1926Clearance.all_1927_1963_public_domain)
      : renewalsCount === 0;
  const auditMonthYear = formatVerifiedMonthYear(post1926Clearance?.verified_date);
  const safeYr = escapeHtml(yr || "post-1926");
  const headerBadge = allPublicDomain
    ? `${spanLabel} Verified Public Domain`
    : `${spanLabel} USCO Audit`;
  const clearanceClause = allPublicDomain
    ? `confirmed that all ${spanLabel} <em>Morrison &amp; Fourmy</em> and <em>Polk</em> Houston City Directories carry ${renewalsCount} copyright ${
        renewalsCount === 1 ? "renewal" : "renewals"
      } and are in the public domain`
    : `audited ${spanLabel} <em>Morrison &amp; Fourmy</em> and <em>Polk</em> Houston City Directories (${renewalsCount} copyright ${
        renewalsCount === 1 ? "renewal" : "renewals"
      } recorded)`;
  const ancYears = Number(post1926Clearance?.ancestry_post_1926_distinct_years) || 28;
  const ancSpanArr =
    Array.isArray(post1926Clearance?.ancestry_post_1926_span) &&
    post1926Clearance.ancestry_post_1926_span.length === 2
      ? post1926Clearance.ancestry_post_1926_span
      : [1927, 1968];
  const ancSpanLabel = `${Number(ancSpanArr[0]) || 1927}-${Number(ancSpanArr[1]) || 1968}`;
  const ancSgCount =
    Array.isArray(post1926Clearance?.ancestry_street_guide_years) &&
    post1926Clearance.ancestry_street_guide_years.length > 0
      ? post1926Clearance.ancestry_street_guide_years.length
      : 15;
  const ancUrl =
    post1926Clearance?.ancestry_collection_url || "https://www.ancestry.com/search/collections/2469/";

  return `<div class="rk-subtle-note">
    <strong>Post-1926 Occupant Lookup (${escapeHtml(headerBadge)}):</strong> HPL's open ContentDM <code>citydir</code> collection stops at 1926, though a ${escapeHtml(
      auditMonthYear
    )} U.S. Copyright Office audit ${clearanceClause}. To trace ${safeYr}-present residents by street address, browse ${ancYears} digitized post-1926 Houston editions (${escapeHtml(
    ancSpanLabel
  )}, including ${ancSgCount} with complete <em>Street &amp; Avenue Guides</em>) online in <a href="${escapeHtml(
    ancUrl
  )}" target="_blank" rel="noopener">Ancestry Library Edition (U.S., City Directories, 1822-1999)</a> (free via HPL MY Link or Rice campus Wi-Fi), or consult the print and microfiche <em>Criss-Cross Street &amp; Avenue Guides</em> at HPL's <strong>Houston History Research Center (Julia Ideson Building, 550 McKinney St)</strong>, <strong>Rice University's Fondren Basement Kelley Center</strong> (open-stacks microfiche <code>F394 .H8 P62</code> for 1936-1959, no appointment required), or <strong>Rice University's Woodson Research Center (Fondren Library)</strong> (<code>F394 .H8 M6</code> for 1927-1935).
  </div>`;
}
