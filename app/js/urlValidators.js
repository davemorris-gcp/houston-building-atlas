/**
 * Pure external URL validators and canonical archival URL builders for The Houston Building Atlas.
 *
 * Shared across the frontend UI and the automated Layer 1 (deterministic URL-pattern) and
 * Layer 3 (live external link health) test suites so broken archive path patterns cannot regress.
 */

/**
 * Known retired, moved, or malformed municipal, county, state, and university URL patterns
 * discovered across Houston and Texas archival portals, paired with actionable replacement hints.
 */
export const KNOWN_DEAD_URL_PATTERNS = [
  {
    id: "ut_pcl_sanborn_grouped_index",
    regex: /maps\.lib\.utexas\.edu\/maps\/sanborn\/texas_cities_/i,
    reason:
      "UT PCL organizes Texas Sanborn city HTML pages by single letter (use https://maps.lib.utexas.edu/maps/sanborn/h.html, while images live under /sanborn/g-i/).",
  },
  {
    id: "harris_county_clerk_mr_maintenance",
    regex: /cclerk\.hctx\.net\/Applications\/WebSearch\/MR\.aspx/i,
    reason:
      "Harris County Clerk MR.aspx redirects to Maintenance.aspx; use MC.aspx (Maps & Condos) or RP.aspx (Real Property).",
  },
  {
    id: "coh_historic_districts_legacy_html",
    regex: /houstontx\.gov\/planning\/HistoricPres\/historic_districts\.html/i,
    reason:
      "COH historic_districts.html returns HTTP 404; use https://www.houstontx.gov/planning/HistoricPres/District_Reference.html.",
  },
  {
    id: "coh_historic_pres_manual_legacy_html",
    regex: /houstontx\.gov\/planning\/HistoricPres\/(?:HistoricPreservationManual\/historic_preservation_manual|hist_pres_main)\.html/i,
    reason:
      "Legacy COH Historic Preservation HTML path returns HTTP 404; use https://www.houstontx.gov/planning/HistoricPres/ or verified brochure PDFs.",
  },
  {
    id: "coh_norhill_redline_wrong_oct_folder",
    regex: /houstontx\.gov\/planning\/Commissions\/docs_pdfs\/hahc\/2026\/Oct_10_2026\/C_Norhill_Redline_Draft_Oct_26\.pdf/i,
    reason:
      "Norhill Oct 2026 Redline Draft lives under /planning/Commissions/hahc_100826/C_Norhill_Redline_Draft_Oct_26.pdf.",
  },
  {
    id: "hpl_legacy_cdm_redirect_path",
    regex: /digital\.houstonlibrary\.org\/cdm\//i,
    reason:
      "Retired HPL ContentDM /cdm/ path; use https://cdm17006.contentdm.oclc.org/digital/collection/...",
  },
  {
    id: "uninterpolated_js_token",
    regex: /(?:[/?&=#]|^)(?:undefined|null|NaN)(?:[/?&#]|$)|\$\{[^}]*\}/,
    reason:
      "URL contains an uninterpolated template token or literal undefined/null/NaN value.",
  },
  {
    id: "thc_nrhp_pdf_unpadded_7digit_refnum",
    regex: /atlas\.thc\.texas\.gov\/NR\/pdfs\/\d{7}\//i,
    reason:
      "2000-2009 NRHP reference numbers on THC Atlas require leading-zero 8-digit padding (e.g. /NR/pdfs/06001063/06001063.pdf).",
  },
  {
    id: "project_row_houses_retired_cultural_district_path",
    regex: /projectrowhouses\.org\/cultural-district(?:\/|$)/i,
    reason:
      "Retired Project Row Houses /cultural-district path returns HTTP 404; use https://projectrowhouses.org/.",
  },
  {
    id: "tsha_nonexistent_subdivision_or_spring_slug",
    regex: /tshaonline\.org\/handbook\/entries\/(?:broadacres-historic-district|oak-forest-tx-harris-county|beauchamp-springs)(?:\/|$)/i,
    reason:
      "Non-existent TSHA Handbook slug (HTTP 404); use verified THC NRHP PDF, COH Super Neighborhood 13, or TSHA white-oak-bayou.",
  },
  {
    id: "txrrhistory_tower_014_missing_page",
    regex: /txrrhistory\.com\/towers\/014\/014\.htm/i,
    reason:
      "txrrhistory.com does not have a dedicated /towers/014/014.htm page (HTTP 404); use http://www.txrrhistory.com/towers/index.htm.",
  },
  {
    id: "coh_ch42_retired_forms_devregs_pdf",
    regex: /houstontx\.gov\/planning\/Forms\/devregs\/(?:SMLS|SMBL)-/i,
    reason:
      "Retired COH /planning/Forms/devregs/ Chapter 42 application PDF path returns HTTP 404; use https://www.houstontx.gov/planning/docs_pdfs/MLS-MBL-Application2026/...",
  },
];

/**
 * Domains protected by interactive browser WAFs (Cloudflare Bot Management, Anubis JS PoW,
 * or stateful Struts JSESSIONID session portals) that return HTTP 403/500/503 to stateless
 * non-browser HTTP clients while working in real interactive browser sessions.
 */
export const BROWSER_CHALLENGE_PROTECTED_HOSTS = new Set([
  "search.hcad.org",
  "www.houstonarchitecture.com",
  "houstonarchitecture.com",
  "www.loc.gov",
  "loc.gov",
  "hdl.loc.gov",
  "maps.lib.utexas.edu",
  "www.texaspublicnotices.com",
  "texaspublicnotices.com",
  "www.theaterdistricthouston.org",
  "theaterdistricthouston.org",
  "vo.ras.dshs.state.tx.us",
  "www.tdlr.texas.gov",
  "tdlr.texas.gov",
  "www.bizapedia.com",
  "bizapedia.com",
  "direct.sos.state.tx.us",
  "houston.novusagenda.com",
  "web.archive.org",
  "texashistory.unt.edu",
]);

/**
 * Deterministically validates an external URL generated or stored by the Houston Building Atlas.
 *
 * @param {string} rawUrl
 * @returns {{ valid: boolean, reason?: string, host?: string, isBrowserChallengeHost?: boolean }}
 */
export function validateExternalAtlasUrl(rawUrl) {
  const s = String(rawUrl || "").trim();
  if (!s) {
    return { valid: false, reason: "Empty URL string." };
  }

  let parsed;
  try {
    parsed = new URL(s);
  } catch (_err) {
    return { valid: false, reason: `Malformed URL syntax: ${s}` };
  }

  if (parsed.protocol !== "https:" && parsed.protocol !== "http:") {
    return { valid: false, reason: `Unsupported URL protocol: ${parsed.protocol}` };
  }

  const host = parsed.hostname.toLowerCase();

  for (const rule of KNOWN_DEAD_URL_PATTERNS) {
    if (rule.regex.test(s)) {
      return {
        valid: false,
        host,
        reason: `[${rule.id}] ${rule.reason}`,
      };
    }
  }

  // Domain-specific structural invariants
  if (host === "arcweb.hcad.org" && parsed.pathname.includes("parcel-viewer")) {
    const acct = parsed.searchParams.get("hcad_num") || parsed.searchParams.get("acct");
    if (acct !== null && !/^\d{13}$/.test(acct)) {
      return {
        valid: false,
        host,
        reason: `HCAD Parcel Viewer hcad_num must be a 13-digit numeric string, got '${acct}'.`,
      };
    }
  }

  if (host === "atlas.thc.texas.gov" && parsed.pathname.startsWith("/Details/")) {
    const detailsId = parsed.pathname.replace("/Details/", "").replace(/\/+$/, "");
    if (!/^\d{9,11}$/.test(detailsId)) {
      return {
        valid: false,
        host,
        reason: `THC Historic Sites Atlas Details ID must be 9-11 digits (e.g. '20' + 8-digit NRHP refnum), got '${detailsId}'.`,
      };
    }
  }

  if (
    (host === "www.google.com" || host === "maps.google.com") &&
    parsed.pathname.startsWith("/maps")
  ) {
    const viewpoint = parsed.searchParams.get("viewpoint") || parsed.searchParams.get("query");
    if (viewpoint && /NaN|undefined|null/i.test(viewpoint)) {
      return {
        valid: false,
        host,
        reason: `Google Maps / Street View coordinate parameter is invalid: '${viewpoint}'.`,
      };
    }
  }

  return {
    valid: true,
    host,
    isBrowserChallengeHost: BROWSER_CHALLENGE_PROTECTED_HOSTS.has(host),
  };
}

/**
 * Builds canonical external research URLs for a Harris County parcel or landmark.
 */
export function buildCanonicalExternalUrls({
  hcadNum = "",
  lat = null,
  lng = null,
  nrhpRefnum = "",
  thcAtlasNum = "",
} = {}) {
  const cleanHcad = String(hcadNum || "").split("#")[0].trim();
  const numLat = Number(lat);
  const numLng = Number(lng);
  const hasCoords =
    Number.isFinite(numLat) &&
    Number.isFinite(numLng) &&
    numLat >= 28.5 &&
    numLat <= 31.0 &&
    numLng >= -96.8 &&
    numLng <= -94.0;

  const rawDigitsRef = String(nrhpRefnum || "").replace(/\D/g, "");
  const cleanRef = rawDigitsRef.length === 7 ? rawDigitsRef.padStart(8, "0") : rawDigitsRef;
  const cleanAtlas = String(thcAtlasNum || "").replace(/\D/g, "");
  const resolvedAtlas =
    cleanAtlas.length >= 9 ? cleanAtlas : cleanRef.length === 8 ? `20${cleanRef}` : "";

  return {
    hcadParcelViewerUrl: /^\d{13}$/.test(cleanHcad)
      ? `https://arcweb.hcad.org/parcel-viewer-v2.0/?hcad_num=${cleanHcad}`
      : null,
    googleMapsUrl: hasCoords
      ? `https://www.google.com/maps/search/?api=1&query=${numLat.toFixed(6)},${numLng.toFixed(6)}`
      : null,
    streetViewUrl: hasCoords
      ? `https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=${numLat.toFixed(6)},${numLng.toFixed(6)}`
      : null,
    thcAtlasUrl: resolvedAtlas ? `https://atlas.thc.texas.gov/Details/${resolvedAtlas}` : null,
    nrhpNominationPdfUrl:
      cleanRef.length === 8
        ? `https://atlas.thc.texas.gov/NR/pdfs/${cleanRef}/${cleanRef}.pdf`
        : null,
  };
}

/**
 * Extracts deduplicated concrete http(s) URLs from arbitrary JSON or text content,
 * preserving balanced parentheses in filenames while excluding template placeholders and XML namespaces.
 *
 * @param {string} text
 * @returns {string[]}
 */
export function extractHttpUrlsFromText(text) {
  const found = new Set();
  const re = /https?:\/\/[^\s"'<>\\`\]]+/g;
  const src = String(text || "");
  let match;
  while ((match = re.exec(src)) !== null) {
    let cleaned = match[0].replace(/[.,;:!?]+$/, "");
    // Trim unmatched trailing ')' while keeping balanced '(Recorded)-c.pdf' parentheses
    while (cleaned.endsWith(")")) {
      const opens = (cleaned.match(/\(/g) || []).length;
      const closes = (cleaned.match(/\)/g) || []).length;
      if (closes > opens) {
        cleaned = cleaned.slice(0, -1).replace(/[.,;:!?]+$/, "");
      } else {
        break;
      }
    }
    if (
      !cleaned.includes("{") &&
      !cleaned.includes("}") &&
      !cleaned.includes("/.../") &&
      !cleaned.includes("schemas.openxmlformats.org") &&
      !cleaned.includes("www.w3.org/") &&
      !cleaned.includes("example-civic-club.org")
    ) {
      found.add(cleaned);
    }
  }
  return Array.from(found);
}
