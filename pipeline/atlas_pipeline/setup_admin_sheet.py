"""Configure the Houston Building Atlas Google Sheet with:
1. Executive Admin Hub & Interactive Quick-Filter Workbench (`Admin_Hub_&_Quick_Filter`)
2. Comprehensive Administrator Operations & Maintenance Manual (`Instructions_&_Apps_Script`)
3. Helper Triage, Direct Map Link, and HCAD Link Columns across Data Tabs
4. Native BasicFilters, 12 Pre-Built Saved Filter Views, Dropdown Validation & Conditional Formatting.
"""

from __future__ import annotations

import json
from pathlib import Path

APPS_SCRIPT_CODE = """function isAuthorizedSheetEditor_(payload) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var allowedEmails = [String(ss.getOwner().getEmail() || '').toLowerCase().trim()];
  var editors = ss.getEditors();
  for (var i = 0; i < editors.length; i++) {
    allowedEmails.push(String(editors[i].getEmail() || '').toLowerCase().trim());
  }
  // 1. Check Google-signed OAuth / ID Token via Google tokeninfo
  var idToken = String(payload.google_id_token || '').trim();
  if (idToken) {
    var resp = UrlFetchApp.fetch('https://oauth2.googleapis.com/tokeninfo?id_token=' + encodeURIComponent(idToken), { muteHttpExceptions: true });
    if (resp.getResponseCode() === 200) {
      var info = JSON.parse(resp.getContentText());
      var tokenEmail = String(info.email || '').toLowerCase().trim();
      if (tokenEmail && allowedEmails.indexOf(tokenEmail) !== -1) {
        return { authorized: true, email: tokenEmail };
      }
    }
  }
  // 2. Check Sheet Editor Email + Private Sheet Passkey (from Cell B6 of Instructions_&_Apps_Script)
  var claimedEmail = String(payload.admin_email || payload.verified_by || '').toLowerCase().trim();
  var passkey = String(payload.admin_passkey || '').trim();
  var cfgSheet = ss.getSheetByName('Instructions_&_Apps_Script');
  var expectedPasskey = cfgSheet ? String(cfgSheet.getRange('B6').getValue() || '').trim() : '';
  if (expectedPasskey && passkey === expectedPasskey && (!claimedEmail || allowedEmails.indexOf(claimedEmail) !== -1)) {
    return { authorized: true, email: claimedEmail || ss.getOwner().getEmail() };
  }
  return { authorized: false, error: 'Google account is not an Editor on this Sheet or invalid passkey.' };
}

function doGet(e) {
  // Serves ONLY the Approved_Edits tab as CSV so the Sheet itself can remain Restricted!
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName('Approved_Edits');
  var values = sheet ? sheet.getDataRange().getValues() : [];
  var csv = values.map(function(row) {
    return row.map(function(cell) {
      var s = String(cell === null || cell === undefined ? '' : cell);
      return (s.indexOf(',') !== -1 || s.indexOf('"') !== -1 || s.indexOf('\\n') !== -1) ? '"' + s.replace(/"/g, '""') + '"' : s;
    }).join(',');
  }).join('\\n');
  return ContentService.createTextOutput(csv).setMimeType(ContentService.MimeType.CSV);
}

function doPost(e) {
  var lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    var payload = JSON.parse(e.postData.contents || '{}');
    if (payload.action === 'verify_admin') {
      return ContentService.createTextOutput(JSON.stringify(isAuthorizedSheetEditor_(payload))).setMimeType(ContentService.MimeType.JSON);
    }
    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var sheet = ss.getSheetByName('Pending_Submissions') || ss.insertSheet('Pending_Submissions');
    var isAdminApprove = (payload.action === 'admin_approve');
    var authResult = isAdminApprove ? isAuthorizedSheetEditor_(payload) : { authorized: false };
    var finalStatus = (isAdminApprove && authResult.authorized) ? 'Approved' : 'Pending';
    var rawBldName = String(payload.building_name || '').trim();
    var nameParts = rawBldName ? rawBldName.split(/[;|\\/]/).map(function(s) { return s.trim(); }).filter(Boolean) : [];
    var primaryName = nameParts.length > 0 ? nameParts[0] : '';
    var altNames = nameParts.length > 1 ? nameParts.slice(1).join('; ') : String(payload.alt_names || '').trim();
    var row = [
      finalStatus,
      String(payload.hcad_num || '').trim(),
      String(payload.address || '').trim(),
      Number(payload.suggested_year_built || payload.year_built || 0) || '',
      Number(payload.hcad_year_built || payload.original_hcad_year || 0) || '',
      String(payload.historic_district || '').trim(),
      String(payload.contributing || '').trim(),
      String(payload.source_type || 'Houston City Directory').trim(),
      String(payload.source_citation || '').trim(),
      String(payload.source_url || '').trim(),
      authResult.authorized ? ('Preservation Houston (' + authResult.email + ')') : '',
      String(payload.submitter_name || payload.submitted_by || authResult.email || 'Community Member').trim(),
      new Date().toISOString().slice(0, 10),
      authResult.authorized ? 'Approved directly via Staff Admin Mode' : 'Community submission via Houston Building Atlas',
      primaryName,
      altNames,
      String(payload.bld_style || '').trim(),
      String(payload.architect || '').trim()
    ];
    // Find last populated row in Column B (hcad_num) so ARRAYFORMULA helper columns (S:V) never offset appendRow
    var colB = sheet.getRange('B:B').getValues();
    var lastRow = 1;
    for (var r = colB.length - 1; r >= 0; r--) {
      if (String(colB[r][0] || '').trim() !== '') {
        lastRow = r + 1;
        break;
      }
    }
    sheet.getRange(lastRow + 1, 1, 1, row.length).setValues([row]);
    return ContentService.createTextOutput(JSON.stringify({ ok: true, status: finalStatus, authorized: authResult.authorized })).setMimeType(ContentService.MimeType.JSON);
  } finally {
    lock.releaseLock();
  }
}"""


def rgb(hex_str: str) -> dict[str, float]:
    h = hex_str.lstrip("#")
    return {
        "red": round(int(h[0:2], 16) / 255.0, 4),
        "green": round(int(h[2:4], 16) / 255.0, 4),
        "blue": round(int(h[4:6], 16) / 255.0, 4),
    }


def build_admin_hub_rows() -> list[list]:
    return [
        [
            "THE HOUSTON BUILDING ATLAS — ADMINISTRATOR HUB, TELEMETRY & QUICK-FILTER WORKBENCH",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
        ],
        [
            "Preservation Houston Platform Administration • Live Google Sheet Control Center, Queue Telemetry & Instant Search Workbench",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
        ],
        ["", "", "", "", "", "", "", "", "", "", "", ""],
        [
            "📊 1. LIVE PLATFORM TELEMETRY & MODERATION QUEUE SUMMARY",
            "Live Count",
            "Recommended SLA / Cadence",
            "How to Triage / Pre-Built Filter View",
            "Quick-Jump Tab Link",
            "",
            "🗺️ QUICK LINKS & EXTERNAL TOOLS",
            "Direct URL",
            "Purpose / Notes",
            "",
            "",
            "",
        ],
        [
            "🚨 Community Submissions Awaiting Review (Pending)",
            '=COUNTIFS(Pending_Submissions!A2:A, "Pending", Pending_Submissions!S2:S, "🚨 Action Needed: Community Review")',
            "Review within 3–5 days",
            'In Pending_Submissions -> Data -> Filter views -> "🚨 1. Action Needed: Community Reviews"',
            '=HYPERLINK("#gid=0", "Open Pending_Submissions ↗")',
            "",
            "Live Houston Building Atlas (GitHub Pages)",
            '=HYPERLINK("https://davemorris-gcp.github.io/houston-building-atlas/", "🚀 Open Live Houston Building Atlas ↗")',
            "Public 3D building atlas synced live with Approved_Edits CSV",
            "",
            "",
            "",
        ],
        [
            "📐 Building Footprint & Geometry Issues Flagged",
            '=COUNTIF(Pending_Submissions!S2:S, "📐 Footprint / Geometry Issue")',
            "Inspect weekly at Zoom 20",
            'In Pending_Submissions -> Data -> Filter views -> "📐 2. Footprint & Geometry Issues"',
            '=HYPERLINK("#gid=0", "Open Footprint Queue ↗")',
            "",
            "Houston Public Library City Directories (1866–1926)",
            '=HYPERLINK("https://cdm17006.contentdm.oclc.org/digital/collection/citydir/search", "📚 Search HPL City Directories (1866–1926) ↗")',
            "Primary archival verification source for pre-1927 construction dates",
            "",
            "",
            "",
        ],
        [
            "🏷️ Building Name & Historical Alias Suggestions",
            '=COUNTIF(Pending_Submissions!S2:S, "🏷️ Building Name / Alias")',
            "Review weekly",
            'In Pending_Submissions -> Data -> Filter views -> "🏷️ 3. Building Name & Alias Suggestions"',
            '=HYPERLINK("#gid=0", "Open Name Queue ↗")',
            "",
            "HCAD Official Parcel Viewer v2.0",
            '=HYPERLINK("https://arcweb.hcad.org/parcel-viewer-v2.0/", "🏛️ Open HCAD GIS Parcel Viewer ↗")',
            "Verify 13-digit HCAD accounts, lot boundaries, and 2026 appraisal data",
            "",
            "",
            "",
        ],
        [
            "📷 Community Historical & Modern Photo Submissions",
            '=COUNTIF(Pending_Submissions!S2:S, "📷 Photo Contribution")',
            "Review weekly",
            'In Pending_Submissions -> Data -> Filter views -> "📷 4. Photo Contributions Queue"',
            '=HYPERLINK("#gid=1351980492", "Open Building_Photos Tab ↗")',
            "",
            "Houston Architecture Info Forum (HAIF)",
            '=HYPERLINK("https://www.houstonarchitecture.com/", "💬 Open HAIF Architecture Forum ↗")',
            "29,467 threads indexed across 97 subforums for building history & photos",
            "",
            "",
            "",
        ],
        [
            "📚 Archival City Directory Batch Candidates (1866–1926)",
            '=COUNTIF(Pending_Submissions!S2:S, "📚 Archival Batch Candidate (1866-1926)")',
            "Batch review by neighborhood",
            'In Pending_Submissions -> Data -> Filter views -> "📚 6. City Directory Batch Candidates"',
            '=HYPERLINK("#gid=0", "Open Directory Candidates ↗")',
            "",
            "City of Houston Historic Preservation (HPO)",
            '=HYPERLINK("https://www.houstontx.gov/planning/HistoricPres/landmarks.html", "📜 Open COH Landmarks Portal ↗")',
            "Official Landmark (LM), Protected Landmark (PLM) & Historic District records",
            "",
            "",
            "",
        ],
        [
            "✅ Approved Overrides in Google Sheet (Live CSV Feed)",
            '=COUNTIF(Pending_Submissions!A2:A, "Approved")',
            "Live on map (<3 min TTL)",
            "Automatically synced to Approved_Edits tab (+1,270 baseline overrides in curated_overrides.json)",
            '=HYPERLINK("#gid=1876369840", "Open Approved_Edits Tab ↗")',
            "",
            "Preservation Houston Good Brick Awards Archive",
            '=HYPERLINK("https://www.preservationhouston.org/awards/past", "★ Open Good Brick Awards Archive ↗")',
            "Official Preservation Houston Good Brick recipients (1979–Present)",
            "",
            "",
            "",
        ],
        [
            "★ Good Brick Award Sites Mapped (1979–2026)",
            "=COUNTA(Good_Brick_Awards!A2:A)",
            "Update annually (Spring/Fall)",
            'In Good_Brick_Awards -> Data -> Filter views -> "Recent Winners (2020–2026)"',
            '=HYPERLINK("#gid=948918462", "Open Good_Brick_Awards Tab ↗")',
            "",
            "Complete Administrator Manual & SOPs",
            '=HYPERLINK("#gid=1038833872", "📖 Open Full Admin Manual & SOPs Tab ↗")',
            "Detailed instructions for all 12 administrative workflows + Apps Script code",
            "",
            "",
            "",
        ],
        [
            "🖼️ Curated Multi-Era Building Photographs",
            "=COUNTA(Building_Photos!A2:A)",
            "Ongoing archival curation",
            'Powers "Photographs Through History" & "Then & Now" curtain slider',
            '=HYPERLINK("#gid=1351980492", "Open Building_Photos Tab ↗")',
            "",
            "COH Landmarks Missing PDFs (Standalone Sheet)",
            '=HYPERLINK("https://docs.google.com/spreadsheets/d/1u1c_kYM5yyoM4aEV9dDJLgY-IkUKlOgce39q5kfKPGs/edit", "📂 Open Standalone TPIA Request Sheet ↗")',
            "Shareable standalone Google Sheet of the 61 missing COH Landmark PDFs",
            "",
            "",
            "",
        ],
        [
            "🏛️ COH Landmarks Missing Designation PDFs (TPIA Tracker)",
            "=COUNTA(Missing_Landmark_PDFs_TPIA!C2:C)",
            "Track HPO / TPIA records request",
            'In Missing_Landmark_PDFs_TPIA -> Data -> Filter views -> "Needs TPIA Request"',
            '=HYPERLINK("#gid=1772154907", "Open Missing_Landmark_PDFs_TPIA ↗")',
            "",
            "Published Live CSV Endpoint (Approved_Edits)",
            '=HYPERLINK("https://docs.google.com/spreadsheets/d/e/2PACX-1vS6oiB-T1JH-oy8coeDD1Gqey_l9rP7Its3NWe7KerNQyBF_Mqrx1NHPLWV8yKTtgS0CTXoJySi3jcO/pub?gid=1876369840&single=true&output=csv", "🔗 Inspect Live Published CSV Feed ↗")',
            "Public read-only CSV feed consumed by the web app on page load",
            "",
            "",
            "",
        ],
        ["", "", "", "", "", "", "", "", "", "", "", ""],
        [
            "⚡ 2. INTERACTIVE QUICK-FILTER & SEARCH WORKBENCH (SEARCH 3,335+ SUBMISSIONS WITHOUT SCROLLING)",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
        ],
        [
            "HOW TO USE: Select a Triage Category (A18), Status (B18), or Historic District (C18) from the dropdowns below, or type any keyword in D18 (e.g. street name, HCAD account, building name, or submitter). Click '✏️ Edit Row #...' in Column A of the results to jump straight to that exact cell in Pending_Submissions!",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
        ],
        [
            "1. Filter by Triage Category ▼",
            "2. Filter by Status ▼",
            "3. Filter by Historic District ▼",
            "4. Keyword Search (Address, HCAD, Building Name, Citation):",
            "5. Max Rows ▼",
            "Matching Rows Found:",
            "Tip: Multi-Admin Safe Filtering",
            "",
            "",
            "",
            "",
            "",
        ],
        [
            "All",
            "Approved",
            "All",
            "",
            50,
            '=COUNTA(IFERROR(FILTER(Pending_Submissions!B2:B, (A18="All")+(Pending_Submissions!S2:S=A18), (B18="All")+(Pending_Submissions!A2:A=B18), (C18="All")+REGEXMATCH(Pending_Submissions!F2:F, "(?i)"&C18), (LEN(D18)=0)+REGEXMATCH(Pending_Submissions!B2:B&" "&Pending_Submissions!C2:C&" "&Pending_Submissions!I2:I&" "&Pending_Submissions!N2:N&" "&Pending_Submissions!O2:O&" "&Pending_Submissions!P2:P, "(?i)"&D18))))',
            "Inside Pending_Submissions, use Data -> Filter views so your filter doesn't affect other admins viewing the sheet!",
            "",
            "",
            "",
            "",
            "",
        ],
        ["", "", "", "", "", "", "", "", "", "", "", ""],
        [
            "Jump to Edit Row in Sheet",
            "Status",
            "Triage Category",
            "HCAD / Building ID",
            "Street Address / Landmark",
            "Verified Year",
            "HCAD Year",
            "Historic District",
            "Source Type",
            "Archival Citation / Issue Details",
            "Open on 3D Map",
            "HCAD Parcel Viewer",
        ],
        [
            '=IFERROR(ARRAY_CONSTRAIN(FILTER({HYPERLINK("#gid=0&range=A"&Pending_Submissions!V2:V, "✏️ Edit Row #"&Pending_Submissions!V2:V), Pending_Submissions!A2:A, Pending_Submissions!S2:S, Pending_Submissions!B2:B, Pending_Submissions!C2:C, Pending_Submissions!D2:D, Pending_Submissions!E2:E, Pending_Submissions!F2:F, Pending_Submissions!H2:H, Pending_Submissions!I2:I, Pending_Submissions!T2:T, Pending_Submissions!U2:U}, (A18="All")+(Pending_Submissions!S2:S=A18), (B18="All")+(Pending_Submissions!A2:A=B18), (C18="All")+REGEXMATCH(Pending_Submissions!F2:F, "(?i)"&C18), (LEN(D18)=0)+REGEXMATCH(Pending_Submissions!B2:B&" "&Pending_Submissions!C2:C&" "&Pending_Submissions!I2:I&" "&Pending_Submissions!N2:N&" "&Pending_Submissions!O2:O&" "&Pending_Submissions!P2:P, "(?i)"&D18)), E18, 12), "No matching submissions found — try changing Status in B18 to All or Pending, or clearing the keyword in D18.")',
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
        ],
    ]


def build_instructions_rows() -> list[list]:
    """Build rows for `Instructions_&_Apps_Script`.
    CRITICAL: Rows 1..6 MUST keep Cell B6 as `PH-Atlas-Editor-2026!` because the deployed
    Google Apps Script reads `cfgSheet.getRange('B6').getValue()`.
    """
    return [
        [
            "Section / Administrative Domain",
            "Detailed Standard Operating Procedures (SOPs), Architecture & Verification Rules",
            "Cadence / Where to Act / CLI & Sheet Reference",
        ],
        [
            "1. Lock Down Google Sheet Access (Restricted to Editors Only)",
            "• Click 'Share' (top right of this Google Sheet) and set General Access to 'Restricted' (Only people with access can open with the link).\n"
            "• Add only Preservation Houston administrators (e.g. Dave, Jim, authorized staff Google accounts) as 'Editors'.\n"
            "• Then click File -> Share -> Publish to web -> change 'Entire Document' to 'Approved_Edits' and 'Web page' to 'Comma-separated values (.csv)'.\n"
            "• Result: Nobody on the public internet can open this workbook or see 'Pending_Submissions', while the public map reads ONLY the 'Approved_Edits' tab!",
            "One-Time Setup / When Adding or Removing Staff Editors\nTab: File -> Share",
        ],
        [
            "2. How Staff Admin Mode Works on the Map (Zero-Cost Google Sheet Editor Auth)",
            "• Public visitors on the map ONLY see the 'Suggest a Correction' button (all admin approval controls are hidden by default).\n"
            "• When a Preservation Houston staff member clicks '🔒 Staff Admin' in the top-right of the Correction Modal on the map, they enter their Google Account Email (which MUST be listed as the Owner or an Editor on this Google Sheet) plus the private Sheet Editor Passkey from Cell B6 below (or a Google OAuth ID token).\n"
            "• The bound Apps Script in Row 5 below checks SpreadsheetApp.getActiveSpreadsheet().getEditors() + getOwner() server-side before allowing any 'admin_approve' edit to go live immediately!",
            "On-Map Admin Approval\nUI: Property Inspector -> Suggest a Correction -> 🔒 Staff Admin",
        ],
        [
            "3. Live CSV Sync URL (Approved_Edits Tab Only)",
            "https://docs.google.com/spreadsheets/d/e/2PACX-1vS6oiB-T1JH-oy8coeDD1Gqey_l9rP7Its3NWe7KerNQyBF_Mqrx1NHPLWV8yKTtgS0CTXoJySi3jcO/pub?gid=1876369840&single=true&output=csv",
            "Configured in app/public/data/curated_overrides.json (googleSheetCsvUrl)",
        ],
        [
            "4. Google Apps Script Code (Copy & Paste into Extensions -> Apps Script -> Deploy as Web App)",
            APPS_SCRIPT_CODE,
            "Extensions -> Apps Script -> Deploy -> Manage deployments (Execute as: Me, Who has access: Anyone)",
        ],
        [
            "5. Sheet Editor Admin Passkey (Private — DO NOT MOVE FROM CELL B6)",
            "PH-Atlas-Editor-2026!",
            "CRITICAL: Keep in Cell B6! Read by Apps Script cfgSheet.getRange('B6').getValue()",
        ],
        [
            "════════════════════════════════════════════════════════════",
            "════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════",
            "════════════════════════════════════════════════════════════",
        ],
        [
            "6. Navigating & Filtering Large Sheets Efficiently (3,300+ Rows)",
            "As Pending_Submissions, Good_Brick_Awards, and Building_Photos grow into thousands of rows, use these three built-in mechanisms instead of manual scrolling:\n\n"
            "A. Executive Workbench & Instant Search (Admin_Hub_&_Quick_Filter Tab):\n"
            "   • Open the first tab ('Admin_Hub_&_Quick_Filter'). Use the dropdowns in Row 19 (Triage Category, Status, Historic District) or type any address, HCAD number, or building name into Cell D19.\n"
            "   • Click the '✏️ Edit Row #...' link in Column A of the search results to jump directly to that exact cell in Pending_Submissions!\n\n"
            "B. Pre-Built Saved Filter Views (Multi-Admin Safe!):\n"
            "   • In Google Sheets, if you click the standard funnel icon on Row 1, it changes the view for EVERY admin currently looking at the sheet.\n"
            "   • Instead, click Data -> Filter views in the top Google Sheets menu and choose one of our 12 pre-configured Filter Views:\n"
            "     1. '🚨 1. Action Needed: Community Reviews (Pending)' — Only human community submissions awaiting review (hides the 3,100+ automated City Directory batch rows!).\n"
            "     2. '📐 2. Footprint & Geometry Issues' — Only roof shape, bridge deck, merged warehouse, or non-building canopy reports.\n"
            "     3. '🏷️ 3. Building Name & Alias Suggestions' — Submissions proposing a primary building name or historical/colloquial alias.\n"
            "     4. '📷 4. Photo Contributions Queue' — Submissions containing a historic or modern photograph URL.\n"
            "     5. '✅ 5. Approved & Live Overrides (Newest First)' — All approved sheet overrides currently live on the map.\n"
            "     6. '📚 6. City Directory Batch Candidates (1866–1926)' — Automated HPL City Directory audit candidates sorted by Historic District & Street Address for neighborhood batch review.\n"
            "     7. '🏛️ 7. Pre-1900 Pioneer Structures' — All Victorian / 19th-century structures (year_built < 1900), sorted oldest first.\n"
            "     8. '⏸️ 8. On Hold / Needs Info / Rejected' — Deferred or rejected items.\n\n"
            "C. Direct Row Action Links (Columns S, T, U on Pending_Submissions):\n"
            "   • Every row in Pending_Submissions automatically computes its Triage Category (Col S), a one-click '🗺️ Open in Atlas ↗' link (Col T) that flies the 3D map directly to that building, and a one-click '🏛️ HCAD Parcel ↗' link (Col U) that opens the official HCAD GIS parcel viewer.",
            "Daily / Weekly Triage\nTabs: Admin_Hub_&_Quick_Filter & Data -> Filter views",
        ],
        [
            "7. Two-Tier Platform Data Architecture (How Edits Go Live)",
            "The Houston Building Atlas uses a zero-cost, two-tier architecture so administrators get BOTH instant live publishing AND rock-solid static performance:\n\n"
            "• TIER 1 — Instant Live Google Sheet Layer (Zero Rebuild, 1–3 Minute Latency):\n"
            "  - Whenever an admin sets Column A (status) to 'Approved' in Pending_Submissions, the formula in Approved_Edits!A2 (=FILTER(...)) automatically copies that row into the Approved_Edits tab.\n"
            "  - Because Approved_Edits is published as a live CSV feed, every visitor's browser fetches those approved rows on startup (app/js/curatedEdits.js) and merges them over the base map in real time!\n"
            "  - What can be overridden live via the Sheet without rebuilding tiles? Year Built (year_built), Primary Building Name (building_name), Historical/Colloquial Aliases (alt_names), Architectural Style (bld_style), Architect (architect), Historic District (historic_district), Contributing Status (contributing), Archival Citation & Link (source_type, source_citation, source_url), and even custom GeoJSON footprints (geometry_geojson).\n\n"
            "• TIER 2 — Repository Static JSON & PMTiles Vector Shards (Periodic Git Sync):\n"
            "  - Baseline curated records live in app/public/data/curated_overrides.json (1,270+ verified buildings), app/public/data/buildings.geojson (11,021 inner-core buildings), app/public/data/overlays.json (Landmarks, Good Brick Awards, Districts, THC Markers, Annexations), app/public/data/building_photos.json, app/public/data/haif_index.json, app/public/data/search_index.json, and the 5 countywide PMTiles vector shards (1,512,020 Harris County building footprints).\n"
            "  - Periodically (e.g. monthly or after major batch reviews), bake the approved Google Sheet rows permanently into the Git repository using:\n"
            "    uv run python -m atlas_pipeline.sync_curated_edits --sheet-csv-url \"<LIVE_CSV_URL>\"",
            "Architecture Reference\nFiles: app/js/curatedEdits.js & pipeline/atlas_pipeline/sync_curated_edits.py",
        ],
        [
            "8. SOP 1 — Moderating Year Built & Archival Corrections (Pending_Submissions)",
            "Follow this verification checklist before changing a row's status from 'Pending' to 'Approved':\n\n"
            "1. Open the Building on the Map & HCAD:\n"
            "   • Click '🗺️ Open in Atlas ↗' (Col T) and '🏛️ HCAD Parcel ↗' (Col U). In the Atlas Property Inspector, click 'Street View Time Machine (2007–Present) ↗' and 'View on Google Maps ↗' to inspect the roof form, fenestration, foundation pier-and-beam vs. slab, and massing.\n\n"
            "2. Verify the Archival Evidence (Cols H, I, J):\n"
            "   • Houston City Directories (1866–1926, HPL ContentDM): Click the ContentDM link in Col J. Confirm that the house number and street name match the extant building and NOT:\n"
            "     (a) A pre-1910 street renumbering (many Downtown, Old Sixth Ward, and Heights streets renumbered between 1895 and 1911), or\n"
            "     (b) An earlier demolished structure on the same lot that was later replaced by a newer building (compare architectural style and Sanborn footprint).\n"
            "   • Why HCAD Dates Are Often Wrong on Pre-1940 Buildings:\n"
            "     - HCAD frequently uses rounded decade placeholders (1920, 1930, 1940, 1950) when pre-computer appraisal cards lacked an exact permit year (e.g., 1127 Key St in Norhill was built in 1928 per City Directories, but HCAD lists 1920).\n"
            "     - Tax-exempt properties (churches, schools, parks, universities, government buildings in HCAD State Classes XJ, X1, XV) default to year_built = 0 in HCAD's main building table.\n"
            "     - Major renovations or adaptive reuse sometimes cause HCAD's effective year (eff_yr) to overwrite date_erected.\n\n"
            "3. Complete Attribution & Approve:\n"
            "   • Ensure Col B (hcad_num) has the full 13-digit HCAD account (including leading zeros, e.g. 0621100000014) or composite building ID (<13_digit_hcad>#<slug>).\n"
            "   • Enter your name/initials in Col K (verified_by) and change Col A (status) dropdown to:\n"
            "     - 'Approved': Verified and immediately published to the live map.\n"
            "     - 'Needs Info': Plausible lead, but requires Sanborn map or deed confirmation before publishing.\n"
            "     - 'On Hold': Deferred for field inspection or HPO records check.\n"
            "     - 'Duplicate' / 'Rejected': Already covered by an existing override or contradicted by architectural/archival evidence.",
            "Weekly (10–15 mins)\nTab: Pending_Submissions (Cols A–U)",
        ],
        [
            "9. SOP 2 — Managing Building Names & Multiple / Historical / Colloquial Aliases (Cols O & P)",
            "Many historic Houston buildings have had multiple names across their lifespan — an original builder/commissioner name, major bank or corporate anchor tenant names, an adaptive-reuse loft/venue name, or a colloquial neighborhood nickname.\n\n"
            "• How the Data Model Works:\n"
            "  - Column O (building_name): Store the single canonical PRIMARY name (preferring the official City of Houston Landmark Designation name, NRHP name, or original historic building name, e.g. 'Gulf Building', 'City National Bank Building', 'The Hogg Building', 'Oriental Textile Mill', 'Stewart House').\n"
            "  - Column P (alt_names): Store all alternate, former-tenant, adaptive-reuse, or colloquial names separated by semicolons (;) or pipes (|), e.g.:\n"
            "    • Gulf Building (712 Main St) -> alt_names: JPMorgan Chase Building; Texas Commerce Bank Building; National Bank of Commerce Building\n"
            "    • City National Bank Building (1001 McKinney St) -> alt_names: First City National Bank; First City Main Building; Texas American Building; 1001 McKinney\n"
            "    • The Hogg Building (401 Louisiana St) -> alt_names: Pappas Building; Armor Building\n"
            "    • Oriental Textile Mill (1801 Nance St) -> alt_names: Heights Clock Tower; 1801 Nance\n"
            "    • Stewart House (3120 Southwest Fwy) -> alt_names: The Marlene; Ada Marlatt House\n\n"
            "• How It Renders in the App:\n"
            "  - Property Inspector Drawer: Displays building_name as the primary header, renders an 'AKA:' badge bar directly underneath, and displays a dedicated 'Building Names & Historical Aliases' card showing both the primary name, all aliases, and name provenance.\n"
            "  - 3D Map Hover Tooltip: Shows the primary building_name plus an 'AKA: ...' subtitle.\n"
            "  - Omni-Search Bar: Indexes BOTH building_name and every entry in alt_names so users can search by any historical or modern name!\n\n"
            "• CLI Pipeline Command (to bake new names from Landmark PDFs, Good Brick, HAIF & Sheet into static files):\n"
            "  uv run python -m atlas_pipeline.enrich_building_names",
            "Ongoing / On Submission\nTab: Pending_Submissions (Col O: building_name, Col P: alt_names)\nScript: pipeline/atlas_pipeline/enrich_building_names.py",
        ],
        [
            "10. SOP 3 — Multi-Building Parcels, Institutional Campuses & Carriage Houses",
            "Harris County Appraisal District (HCAD) assigns one 13-digit account number per tax parcel, NOT per building. When a single parcel contains multiple distinct structures, follow these rules to prevent a single override from mislabeling or hiding sibling buildings:\n\n"
            "1. Single Main Building + Detached Garage / Carriage House (Residential & Landmark Lots):\n"
            "   • When a 13-digit hcad_num is overridden, the map's shard filter hides the raw PMTiles shard footprints for that hcad_num and renders the geometry from curated_overrides.json.\n"
            "   • ALWAYS assign the LARGEST footprint on the parcel (the main house/mansion) to the primary 13-digit '<hcad_num>' key (e.g., C. Milby Dow House at 1305 South Blvd, 0530390000028).\n"
            "   • Assign detached carriage houses, servants' quarters, or historic print shops to composite keys: '<hcad_num>#aux_1', '<hcad_num>#aux_2', or '<hcad_num>#modern_print_shop' (e.g., 0210200000029#modern_print_shop at 301 E 5th St).\n\n"
            "2. Multi-Era Historic Compounds (e.g. Last Concert Café at 1403 Nance St, 1344850010002):\n"
            "   • Use separate composite keys for each structure so each building is individually clickable and color-coded by its own construction decade:\n"
            "     - '1344850010002': Richey-Fant House (Built 1850, Protected Landmark 11PL099)\n"
            "     - '1344850010002#cafe': Last Concert Café Main Building (Built 1949)\n"
            "     - '1344850010002#stage': Last Concert Café Stage & Courtyard (Built 1986)\n\n"
            "3. Large Tax-Exempt Institutional Super-Parcels (Rice, UH, TSU, MFAH, Hermann Park/Zoo, Sam Houston Park):\n"
            "   • Mapped as individual building overrides ('<hcad_num>#<building_slug>') with 'is_building_override': true and 'replace_parcel_shards': true in curated_overrides.json so clicking Fondren Library or Lovett Hall highlights only that building.",
            "When Editing Multi-Building Properties\nFiles: app/public/data/curated_overrides.json & pipeline/atlas_pipeline/enrich_institutions.py",
        ],
        [
            "11. SOP 4 — Triaging Building Footprint, Shape & Non-Building Structure Reports",
            "Users can report geometry errors via Suggest a Correction -> '📐 Building Footprint Shape or Orientation Issue'. Filter these in Pending_Submissions using Filter View '📐 2. Footprint & Geometry Issues'. Click the Zoom-20 Satellite link in Col J to diagnose which of the 4 resolution paths applies:\n\n"
            "• CASE A — Non-Building Structures (Open-Air Bus Canopies, Carports, Freeway Slivers) or Retired Pre-Replat Parcels:\n"
            "  - Example: Open-air METRO bus parking sheds in the Warehouse District (tagged building=carport in OSM with 0 sq ft in HCAD) or retired pre-replat HCAD accounts.\n"
            "  - Fix: Add the HCAD account to curated_overrides.json with '\"suppress_only\": true'. This hides the non-building polygon from buildings.geojson, curated-overrides-src, and the PMTiles shards.\n\n"
            "• CASE B — Merged / Fused Footprints Across Parcel Lines or Bridge Decks (e.g. Elysian Viaduct / Warehouse District):\n"
            "  - Example: Microsoft satellite ML footprints fusing zero-lot-line brick warehouses across adjacent parcels or fusing the Elysian Viaduct roadway deck into 902 Hardy St.\n"
            "  - Fix: Clip the roof footprint against exact HCAD parcel boundaries from Parcels.gdb (EPSG:2278 -> WGS84) using pipeline/atlas_pipeline/fix_warehouse_district.py.\n\n"
            "• CASE C — Straddling Lot-Line Ghost Polygon from Neighboring Shard Account (suppress_shard_hcads):\n"
            "  - Example: Rutherford B. H. Yates House (1314 Andrews St, 0090780000008, 1912) whose western eaves touched neighbor 0090780000007 (2012), causing the PMTiles shard to tag the footprint under the neighbor's HCAD.\n"
            "  - Fix: Add '\"suppress_shard_hcads\": [\"0090780000007\"]' to the override in curated_overrides.json.\n\n"
            "• CASE D — Multi-Footprint Industrial, Refinery, Petrochemical Tank Farm & Terminal Parcels:\n"
            "  - Never overwrite a multi-building refinery or terminal parcel (e.g. ExxonMobil Baytown, Shell Deer Park, Port of Houston) with a single Polygon, or _buildShardLayerFilter will hide the other 50+ buildings/tanks on that HCAD account!\n"
            "  - Always store multi-structure industrial parcels as a 'MultiPolygon' geometry in curated_overrides.json using pipeline/atlas_pipeline/fix_industrial_multi_footprints.py (which merges shard buildings + OpenStreetMap man_made=storage_tank + Esri World_Basemap_v2 circular tank footprints).",
            "Weekly / Monthly Geometry Triage\nScripts: fix_warehouse_district.py & fix_industrial_multi_footprints.py",
        ],
        [
            "12. SOP 5 — Managing Historical & Contemporary Photography (Building_Photos Tab)",
            "The Property Inspector's 'Photographs Through History' timeline and interactive '⇄ Then & Now' comparison slider combine curated entries from the Building_Photos tab with live Wikimedia Commons queries (app/js/photoService.js).\n\n"
            "1. Moderating Community Photo Submissions:\n"
            "   • In Pending_Submissions, select Filter View '📷 4. Photo Contributions Queue'.\n"
            "   • Verify that the image URL is a direct, permanent image file (ending in .jpg/.jpeg/.png/.webp or Wikimedia thumburl, e.g. upload.wikimedia.org, tile.loc.gov, media.invisioncic.com) and accurately depicts the building.\n"
            "   • Copy verified rows into the 'Building_Photos' tab (Columns A–K: hcad_num, building_id, landmark_name, address, photo_year, era_label, caption, image_url, thumb_url, credit, source_url).\n\n"
            "2. Enabling '⇄ Then & Now' Comparison Slider on a Building:\n"
            "   • Whenever a building has >= 2 photographs from different years (e.g. a 1929 HABS/postcard view and a 2024 streetscape view), the UI automatically enables the '⇄ Then & Now' curtain slider comparing the earliest photo ('Then') against the most recent photo ('Now').\n\n"
            "3. Filtering Off-Topic Wikimedia Commons Geo-Matches:\n"
            "   • Clicking '✕ Hide Photo' in the web UI hides an irrelevant Commons photo in local storage; to block an off-topic Commons file or category globally for all users, add the term to evaluateCommonsCandidate() in app/js/photoService.js.\n"
            "   • To sync Building_Photos into app/public/data/building_photos.json, run: uv run python -m atlas_pipeline.seed_building_photos",
            "Weekly / Monthly Photo Curation\nTab: Building_Photos\nFiles: app/js/photoService.js & pipeline/atlas_pipeline/seed_building_photos.py",
        ],
        [
            "13. SOP 6 — Annual Preservation Houston Good Brick Awards Update (Good_Brick_Awards Tab)",
            "All 359 place-based Good Brick Award recipients (1979–2026) are stored in the 'Good_Brick_Awards' tab. When new Good Brick Awards are announced each year:\n\n"
            "1. Append New Award Rows to Good_Brick_Awards (Cols A–N):\n"
            "   • Fill in award_year, award_type, recipient, project_name, reason, building_year_built, architect_or_style, address, hcad_num, historic_district, lat, lng, location_source, raw_citation.\n\n"
            "2. Mandatory Geocoding & Campus Integrity Rules:\n"
            "   • NEVER use raw street-centerline geocoding (e.g. Census TIGER) for lat/lng — centerline geocoding drops pins in traffic medians or on the wrong side of divided boulevards (such as 5500 vs 5501 Main St).\n"
            "   • Always snap lat and lng to the interior representative_point() of the verified building footprint polygon!\n"
            "   • Verify that building_year_built > 0 and hcad_num is the exact 13-digit HCAD account (preserving leading zeros).\n"
            "   • On multi-building campuses (e.g. MFAH, Rice University, Sam Houston Park, St. Paul's UMC, or lots with a carriage house/print shop), map the award to the specific composite building_id ('<hcad_num>#<slug>') in EXPLICIT_OVERRIDE_KEY_MAP in pipeline/atlas_pipeline/good_brick_awards.py.\n"
            "   • If an adaptive-reuse winner has both a historic name and a contemporary project name, enter project_name as 'Historic Name / New Name' or 'New Name (Historic Building Name)' so enrich_building_names.py indexes both!\n\n"
            "3. Rebuild Overlay & Search Index Files:\n"
            "   • Run: uv run python -m atlas_pipeline.good_brick_awards && uv run python -m atlas_pipeline.enrich_building_names",
            "Annually (When Good Brick Winners Are Announced)\nTab: Good_Brick_Awards\nScript: pipeline/atlas_pipeline/good_brick_awards.py",
        ],
        [
            "14. SOP 7 — City of Houston Landmark Designation Reports & TPIA Tracker (Missing_Landmark_PDFs_TPIA Tab)",
            "We have crawled and full-text indexed 448 of the 509 (88.0%) City of Houston Landmark Designation Report PDFs (plus 117 official HPO archival landmark photos) from houstontx.gov and the Internet Archive Wayback Machine.\n\n"
            "• Tracking the Remaining 61 Post-2016 Missing PDFs:\n"
            "  - The 61 designated landmarks (mostly 2016–2023 designations whose PDF links were never uploaded to the City's website) are listed in the 'Missing_Landmark_PDFs_TPIA' tab (and in the standalone Google Sheet: https://docs.google.com/spreadsheets/d/1u1c_kYM5yyoM4aEV9dDJLgY-IkUKlOgce39q5kfKPGs/edit).\n"
            "  - Use Columns G–I on Missing_Landmark_PDFs_TPIA ('TPIA_Request_Status', 'Date_Requested', 'PDF_Received_URL') to track records requests to the City of Houston Historic Preservation Office (historicpreservation@houstontx.gov).\n\n"
            "• Ingesting Newly Received Landmark PDFs:\n"
            "  1. Place new PDF files or URLs into pipeline/cache/coh_landmarks_enriched.json.\n"
            "  2. Run: uv run python -m atlas_pipeline.crawl_landmark_reports\n"
            "  3. Run: uv run python -m atlas_pipeline.apply_landmark_report_enrichments\n"
            "  4. Run: uv run python -m atlas_pipeline.enrich_building_names\n"
            "  5. Update TPIA_Request_Status in Missing_Landmark_PDFs_TPIA to 'Ingested into Atlas'.",
            "As HPO / TPIA Records Arrive\nTab: Missing_Landmark_PDFs_TPIA\nScripts: crawl_landmark_reports.py & apply_landmark_report_enrichments.py",
        ],
        [
            "15. SOP 8 — Houston Architecture Info Forum (HAIF) Integration Maintenance",
            "The Atlas integrates 3,385 Houston Architecture Info Forum (houstonarchitecture.com) discussion threads and 172 archival/construction photographs across 7,741 building footprints (app/public/data/haif_index.json).\n\n"
            "• Technical Gotchas When Refreshing HAIF Data:\n"
            "  - Direct automated HTTP requests to houstonarchitecture.com trigger Cloudflare 403 challenges.\n"
            "  - Instead, pipeline/atlas_pipeline/crawl_haif_forum.py queries the Internet Archive Wayback Machine CDX API (http://web.archive.org/cdx/search/cdx?url=houstonarchitecture.com/topic/*) which indexes 29,467 HAIF threads across 97 subforums.\n"
            "  - HAIF member photographs are hosted on Invision Community's CDN (https://media.invisioncic.com/w329674/... and https://content.invisioncic.com/w329674/...), which serves HTTP 200 image/jpeg directly without Cloudflare blocks.\n"
            "  - Keep Wayback HTML worker concurrency at max_workers=2 with a 0.35s delay to respect Archive.org socket limits.\n\n"
            "• Refresh Commands:\n"
            "  uv run python -m atlas_pipeline.crawl_haif_forum && uv run python -m atlas_pipeline.apply_haif_enrichments && uv run python -m atlas_pipeline.enrich_building_names",
            "Quarterly / Semi-Annually\nScripts: crawl_haif_forum.py & apply_haif_enrichments.py\nData: app/public/data/haif_index.json",
        ],
        [
            "16. SOP 9 — Annual HCAD CAMA & Parcel Shapefile Rollover (Full Countywide Rebuild)",
            "Once per year (typically summer/fall after HCAD certifies the annual appraisal roll), refresh the countywide 1.51M building footprint shards while preserving 100% of Preservation Houston's curated overrides:\n\n"
            "1. Download Latest HCAD Data into pipeline/cache/hcad_full/:\n"
            "   • GIS Parcels (OpenFileGDB Parcels.gdb): https://download.hcad.org/data/GIS/Parcels.zip\n"
            "   • Real Building & Land CAMA (building_res.txt, building_other.txt, extra_features_detail1/2.txt): https://download.hcad.org/data/CAMA/<YEAR>/Real_building_land.zip\n"
            "   • Real Account & Owner CAMA (real_acct.txt, deeds.txt, permits.txt, parcel_tieback.txt): https://download.hcad.org/data/CAMA/<YEAR>/Real_acct_owner.zip\n\n"
            "2. Run the Full Pipeline Sequence (in /usr/local/google/home/davemorris/houston-building-atlas):\n"
            "   a) uv run python -m atlas_pipeline.sync_curated_edits           # Pull latest Approved_Edits from Google Sheet\n"
            "   b) uv run python -m atlas_pipeline.full_build                   # Spatial join Parcels.gdb + MSBFP2 + OSM + Tippecanoe 5 PMTiles shards\n"
            "   c) uv run python -m atlas_pipeline.resolve_undated_countywide   # 4-phase resolution so 0 buildings have year_built = 0\n"
            "   d) uv run python -m atlas_pipeline.fix_industrial_multi_footprints  # Restore MultiPolygon refineries & circular storage tanks\n"
            "   e) uv run python -m atlas_pipeline.fix_warehouse_district       # Clip bridge decks & split zero-lot-line warehouses\n"
            "   f) uv run python -m atlas_pipeline.apply_landmark_report_enrichments\n"
            "   g) uv run python -m atlas_pipeline.apply_haif_enrichments\n"
            "   h) uv run python -m atlas_pipeline.enrich_styles_and_search_index\n"
            "   i) uv run python -m atlas_pipeline.enrich_building_names\n\n"
            "3. Verify Shard Sizes & Cache-Busting Before Deploying:\n"
            "   • Confirm all 5 app/public/data/houston_buildings_*.pmtiles files are < 95 MB (GitHub Pages hard limit is 100 MB per file).\n"
            "   • Bump the '?v=YYYYMMDDx' cache-busting query string in lockstep across app/index.html, app/js/app.js, app/js/mapController.js, and app/js/curatedEdits.js.",
            "Annually (After HCAD Appraisal Roll Certification)\nScripts: pipeline/atlas_pipeline/full_build.py et al.",
        ],
        [
            "17. SOP 10 — Administrator Maintenance Cadence Summary & Guardrails Checklist",
            "• WEEKLY (10–15 mins):\n"
            "  1. Open 'Admin_Hub_&_Quick_Filter' -> check '🚨 Community Submissions Awaiting Review', '📐 Footprint Issues', '🏷️ Name Suggestions', and '📷 Photo Submissions'.\n"
            "  2. Click '✏️ Edit Row #...' (or use Data -> Filter views in Pending_Submissions) to verify citations and change status to 'Approved' or 'Rejected'.\n\n"
            "• MONTHLY (20 mins):\n"
            "  1. Review a batch of '📚 6. City Directory Batch Candidates (1866–1926)' in a target Historic District (e.g., Norhill, Heights, Old Sixth Ward).\n"
            "  2. Copy newly approved community photo rows into the 'Building_Photos' tab.\n"
            "  3. Check 'Missing_Landmark_PDFs_TPIA' for any newly received COH Landmark Designation Report PDFs.\n\n"
            "• QUARTERLY (30 mins):\n"
            "  1. Run sync_curated_edits.py, enrich_building_names.py, and enrich_styles_and_search_index.py to bake live Google Sheet approvals into Git.\n"
            "  2. Refresh HAIF forum threads via crawl_haif_forum.py & apply_haif_enrichments.py.\n\n"
            "• ANNUALLY (1–2 hours):\n"
            "  1. Add the new year's Preservation Houston Good Brick Award recipients to 'Good_Brick_Awards' and run good_brick_awards.py.\n"
            "  2. Download the new HCAD CAMA & Parcels.gdb release and run the full countywide pipeline sequence in Section 16.\n\n"
            "• CRITICAL DATA INTEGRITY GUARDRAILS:\n"
            "  - NEVER format Column B (hcad_num) as a plain number — Harris County HCAD accounts are 13-character strings and ~45% start with a leading zero ('001...' to '099...').\n"
            "  - NEVER move or delete Cell B6 on Instructions_&_Apps_Script (holds the Staff Admin Passkey read by Apps Script).\n"
            "  - NEVER unpublish the 'Approved_Edits' CSV web link (File -> Share -> Publish to web).",
            "Ongoing Governance\nOwner: Preservation Houston Atlas Administrators",
        ],
    ]


def main() -> None:
    admin_hub_rows = build_admin_hub_rows()
    instructions_rows = build_instructions_rows()

    # 1. Build values batch JSON for master workbook (1dpd9A5z4SmFkHut-fmBi8deIDGVZ8bDzYVdP88-_ZDs)
    values_batch = [
        # A. Populate Admin_Hub_&_Quick_Filter
        {
            "op": "write",
            "range": "Admin_Hub_&_Quick_Filter!A1",
            "data": admin_hub_rows,
        },
        # B. Populate Instructions_&_Apps_Script (keeping B6 = PH-Atlas-Editor-2026!)
        {
            "op": "write",
            "range": "Instructions_&_Apps_Script!A1",
            "data": instructions_rows,
        },
        # C. Add enrichment + helper ARRAYFORMULA columns O1:V1 on Pending_Submissions
        {
            "op": "write",
            "range": "Pending_Submissions!O1:V1",
            "data": [
                [
                    "building_name",
                    "alt_names",
                    "bld_style",
                    "architect",
                    '={"triage_category"; ARRAYFORMULA(IF(LEN(B2:B)=0, , IF(A2:A="Approved", "✅ Approved & Live", IF(REGEXMATCH(A2:A, "(?i)Rejected|Duplicate"), "🚫 Rejected / Duplicate", IF(REGEXMATCH(A2:A, "(?i)Hold|Needs Info"), "⏸️ On Hold / Needs Info", IF(REGEXMATCH(H2:H & " " & I2:I & " " & N2:N, "(?i)footprint|shape|polygon|viaduct|shed"), "📐 Footprint / Geometry Issue", IF(REGEXMATCH(H2:H & " " & I2:I & " " & N2:N, "(?i)photo|image|wikimedia"), "📷 Photo Contribution", IF((LEN(O2:O)>0) + REGEXMATCH(I2:I, "(?i)BUILDING NAME|ALIAS"), "🏷️ Building Name / Alias", IF(REGEXMATCH(L2:L, "(?i)Batch Auditor|Pipeline"), "📚 Archival Batch Candidate (1866-1926)", "🚨 Action Needed: Community Review")))))))))}',
                    '={"open_in_atlas"; ARRAYFORMULA(IF(LEN(B2:B)=0, , HYPERLINK("https://davemorris-gcp.github.io/houston-building-atlas/#hcad=" & B2:B, "🗺️ Open in Atlas ↗")))}',
                    '={"inspect_hcad"; ARRAYFORMULA(IF(LEN(B2:B)=0, , HYPERLINK("https://arcweb.hcad.org/parcel-viewer-v2.0/?hcad_num=" & LEFT(B2:B, 13), "🏛️ HCAD Parcel ↗")))}',
                    '={"row_num"; ARRAYFORMULA(IF(LEN(B2:B)=0, , ROW(B2:B)))}',
                ]
            ],
        },
        # D. Expand Approved_Edits headers (A1:O1) & formula (A2) so building_name, alt_names, bld_style, architect flow automatically
        {
            "op": "write",
            "range": "Approved_Edits!A1:O2",
            "data": [
                [
                    "status",
                    "hcad_num",
                    "address",
                    "year_built",
                    "original_hcad_year",
                    "historic_district",
                    "contributing",
                    "source_type",
                    "source_citation",
                    "source_url",
                    "verified_by",
                    "building_name",
                    "alt_names",
                    "bld_style",
                    "architect",
                ],
                [
                    '=IFERROR(FILTER({Pending_Submissions!A2:K, Pending_Submissions!O2:R}, Pending_Submissions!A2:A = "Approved"), "")',
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                ],
            ],
        },
        # E. Add Open_in_Atlas & HCAD_Parcel helper ARRAYFORMULA columns to Good_Brick_Awards (O1:P1)
        {
            "op": "write",
            "range": "Good_Brick_Awards!O1:P1",
            "data": [
                [
                    '={"open_in_atlas"; ARRAYFORMULA(IF(LEN(A2:A)=0, , HYPERLINK("https://davemorris-gcp.github.io/houston-building-atlas/#lat=" & K2:K & "&lng=" & L2:L & "&z=18&hcad=" & I2:I, "🗺️ Open Site in Atlas ↗")))}',
                    '={"inspect_hcad"; ARRAYFORMULA(IF(LEN(I2:I)=0, , HYPERLINK("https://arcweb.hcad.org/parcel-viewer-v2.0/?hcad_num=" & LEFT(I2:I, 13), "🏛️ HCAD Parcel ↗")))}',
                ]
            ],
        },
        # F. Add Open_in_Atlas helper ARRAYFORMULA column to Building_Photos (L1)
        {
            "op": "write",
            "range": "Building_Photos!L1",
            "data": [
                [
                    '={"open_in_atlas"; ARRAYFORMULA(IF(LEN(A2:A)=0, , HYPERLINK("https://davemorris-gcp.github.io/houston-building-atlas/#hcad=" & A2:A, "🗺️ Open Building in Atlas ↗")))}'
                ]
            ],
        },
        # G. Add TPIA tracking columns G1:J1 to Missing_Landmark_PDFs_TPIA
        {
            "op": "write",
            "range": "Missing_Landmark_PDFs_TPIA!G1:J1",
            "data": [
                [
                    "TPIA_Request_Status",
                    "Date_Requested",
                    "PDF_Received_URL_or_Notes",
                    '={"Open_in_Atlas"; ARRAYFORMULA(IF(LEN(C2:C)=0, , HYPERLINK("https://davemorris-gcp.github.io/houston-building-atlas/?layers=landmarks", "🗺️ Search on Atlas ↗")))}',
                ]
            ],
        },
        # Default TPIA_Request_Status in G2:G62 to "Needs TPIA Request"
        {
            "op": "write",
            "range": "Missing_Landmark_PDFs_TPIA!G2:G62",
            "data": [["Needs TPIA Request"] for _ in range(61)],
        },
    ]

    Path("/tmp/admin_values_batch.json").write_text(json.dumps(values_batch, indent=2), encoding="utf-8")
    print("Wrote /tmp/admin_values_batch.json")

    # 2. Build raw-batch JSON for styling, tab ordering, BasicFilters, 12 FilterViews, DataValidation & Conditional Formatting
    # Sheet IDs:
    # Admin_Hub_&_Quick_Filter: 1383259584
    # Pending_Submissions: 0
    # Approved_Edits: 1876369840
    # Instructions_&_Apps_Script: 1038833872
    # Good_Brick_Awards: 948918462
    # Building_Photos: 1351980492
    # Missing_Landmark_PDFs_TPIA: 1772154907

    SID_HUB = 1383259584
    SID_PENDING = 0
    SID_APPROVED = 1876369840
    SID_INSTR = 1038833872
    SID_GB = 948918462
    SID_PHOTOS = 1351980492
    SID_TPIA = 1772154907

    c_charcoal = rgb("#272727")
    c_slate = rgb("#333b42")
    c_sage = rgb("#95c959")
    c_sage_light = rgb("#eaf4dc")
    c_white = rgb("#ffffff")
    c_offwhite = rgb("#f8faf5")
    c_filter_bg = rgb("#fff8dc")
    c_green_bg = rgb("#d9ead3")
    c_green_fg = rgb("#274e13")
    c_amber_bg = rgb("#fff2cc")
    c_amber_fg = rgb("#7f6000")
    c_blue_bg = rgb("#cfe2f3")
    c_blue_fg = rgb("#073763")
    c_red_bg = rgb("#f4cccc")
    c_red_fg = rgb("#660000")

    def header_style_req(sheet_id: int, end_col: int, row_idx: int = 0, bg=c_charcoal, fg=c_sage, font_size: int = 10):
        return {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": row_idx,
                    "endRowIndex": row_idx + 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": end_col,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": bg,
                        "textFormat": {
                            "bold": True,
                            "foregroundColor": fg,
                            "fontSize": font_size,
                        },
                        "verticalAlignment": "MIDDLE",
                        "wrapStrategy": "WRAP",
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment,wrapStrategy)",
            }
        }

    def col_width_req(sheet_id: int, start_col: int, end_col: int, px: int):
        return {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": start_col,
                    "endIndex": end_col,
                },
                "properties": {"pixelSize": px},
                "fields": "pixelSize",
            }
        }

    raw_requests = [
        # 1. Order tabs cleanly: Admin_Hub (0), Pending_Submissions (1), Approved_Edits (2), Instructions_&_Apps_Script (3), Good_Brick_Awards (4), Building_Photos (5), Missing_Landmark_PDFs_TPIA (6)
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": SID_HUB,
                    "index": 0,
                    "gridProperties": {"rowCount": 200, "columnCount": 14, "frozenRowCount": 2},
                    "tabColor": c_sage,
                },
                "fields": "index,gridProperties(rowCount,columnCount,frozenRowCount),tabColor",
            }
        },
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": SID_PENDING,
                    "index": 1,
                    "gridProperties": {"rowCount": 4000, "columnCount": 24, "frozenRowCount": 1, "frozenColumnCount": 3},
                    "tabColor": rgb("#f1c232"),
                },
                "fields": "index,gridProperties(rowCount,columnCount,frozenRowCount,frozenColumnCount),tabColor",
            }
        },
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": SID_APPROVED,
                    "index": 2,
                    "gridProperties": {"rowCount": 1500, "columnCount": 18, "frozenRowCount": 1, "frozenColumnCount": 3},
                    "tabColor": rgb("#6aa84f"),
                },
                "fields": "index,gridProperties(rowCount,columnCount,frozenRowCount,frozenColumnCount),tabColor",
            }
        },
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": SID_INSTR,
                    "index": 3,
                    "gridProperties": {"rowCount": 60, "columnCount": 6, "frozenRowCount": 1, "frozenColumnCount": 1},
                    "tabColor": rgb("#4894d5"),
                },
                "fields": "index,gridProperties(rowCount,columnCount,frozenRowCount,frozenColumnCount),tabColor",
            }
        },
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": SID_GB,
                    "index": 4,
                    "gridProperties": {"rowCount": 600, "columnCount": 18, "frozenRowCount": 1, "frozenColumnCount": 2},
                },
                "fields": "index,gridProperties(rowCount,columnCount,frozenRowCount,frozenColumnCount)",
            }
        },
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": SID_PHOTOS,
                    "index": 5,
                    "gridProperties": {"rowCount": 500, "columnCount": 15, "frozenRowCount": 1, "frozenColumnCount": 2},
                },
                "fields": "index,gridProperties(rowCount,columnCount,frozenRowCount,frozenColumnCount)",
            }
        },
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": SID_TPIA,
                    "index": 6,
                    "gridProperties": {"rowCount": 200, "columnCount": 12, "frozenRowCount": 1, "frozenColumnCount": 3},
                },
                "fields": "index,gridProperties(rowCount,columnCount,frozenRowCount,frozenColumnCount)",
            }
        },
        # 2. Style Admin_Hub_&_Quick_Filter
        header_style_req(SID_HUB, 12, row_idx=0, bg=c_charcoal, fg=c_sage, font_size=13),
        header_style_req(SID_HUB, 12, row_idx=1, bg=c_slate, fg=c_white, font_size=10),
        header_style_req(SID_HUB, 10, row_idx=3, bg=c_charcoal, fg=c_sage, font_size=10),
        header_style_req(SID_HUB, 12, row_idx=14, bg=c_charcoal, fg=c_sage, font_size=11),
        header_style_req(SID_HUB, 12, row_idx=15, bg=c_sage_light, fg=c_charcoal, font_size=9),
        header_style_req(SID_HUB, 8, row_idx=16, bg=c_slate, fg=c_white, font_size=10),
        # Highlight interactive filter input cells in Row 19 (index 17)
        {
            "repeatCell": {
                "range": {
                    "sheetId": SID_HUB,
                    "startRowIndex": 17,
                    "endRowIndex": 18,
                    "startColumnIndex": 0,
                    "endColumnIndex": 6,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": c_filter_bg,
                        "textFormat": {"bold": True, "fontSize": 11, "foregroundColor": c_charcoal},
                        "verticalAlignment": "MIDDLE",
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment)",
            }
        },
        # Style Query Results table header (Row 20, index 19)
        header_style_req(SID_HUB, 12, row_idx=19, bg=c_charcoal, fg=c_sage, font_size=10),
        col_width_req(SID_HUB, 0, 1, 290),
        col_width_req(SID_HUB, 1, 2, 130),
        col_width_req(SID_HUB, 2, 3, 220),
        col_width_req(SID_HUB, 3, 4, 340),
        col_width_req(SID_HUB, 4, 5, 230),
        col_width_req(SID_HUB, 5, 6, 130),
        col_width_req(SID_HUB, 6, 7, 260),
        col_width_req(SID_HUB, 7, 8, 230),
        col_width_req(SID_HUB, 8, 9, 250),
        col_width_req(SID_HUB, 9, 10, 380),
        col_width_req(SID_HUB, 10, 12, 165),
        # 3. Data Validation Dropdowns on Admin_Hub_&_Quick_Filter (Row 19, index 17)
        {
            "setDataValidation": {
                "range": {"sheetId": SID_HUB, "startRowIndex": 17, "endRowIndex": 18, "startColumnIndex": 0, "endColumnIndex": 1},
                "rule": {
                    "condition": {
                        "type": "ONE_OF_LIST",
                        "values": [
                            {"userEnteredValue": "All"},
                            {"userEnteredValue": "🚨 Action Needed: Community Review"},
                            {"userEnteredValue": "📐 Footprint / Geometry Issue"},
                            {"userEnteredValue": "🏷️ Building Name / Alias"},
                            {"userEnteredValue": "📷 Photo Contribution"},
                            {"userEnteredValue": "📚 Archival Batch Candidate (1866-1926)"},
                            {"userEnteredValue": "✅ Approved & Live"},
                            {"userEnteredValue": "⏸️ On Hold / Needs Info"},
                            {"userEnteredValue": "🚫 Rejected / Duplicate"},
                        ],
                    },
                    "showCustomUi": True,
                    "strict": False,
                },
            }
        },
        {
            "setDataValidation": {
                "range": {"sheetId": SID_HUB, "startRowIndex": 17, "endRowIndex": 18, "startColumnIndex": 1, "endColumnIndex": 2},
                "rule": {
                    "condition": {
                        "type": "ONE_OF_LIST",
                        "values": [
                            {"userEnteredValue": "All"},
                            {"userEnteredValue": "Pending"},
                            {"userEnteredValue": "Approved"},
                            {"userEnteredValue": "Needs Info"},
                            {"userEnteredValue": "On Hold"},
                            {"userEnteredValue": "Duplicate"},
                            {"userEnteredValue": "Rejected"},
                        ],
                    },
                    "showCustomUi": True,
                    "strict": False,
                },
            }
        },
        {
            "setDataValidation": {
                "range": {"sheetId": SID_HUB, "startRowIndex": 17, "endRowIndex": 18, "startColumnIndex": 2, "endColumnIndex": 3},
                "rule": {
                    "condition": {
                        "type": "ONE_OF_LIST",
                        "values": [
                            {"userEnteredValue": "All"},
                            {"userEnteredValue": "Norhill"},
                            {"userEnteredValue": "Houston Heights"},
                            {"userEnteredValue": "Old Sixth Ward"},
                            {"userEnteredValue": "Main Street"},
                            {"userEnteredValue": "Freedmen"},
                            {"userEnteredValue": "Avondale"},
                            {"userEnteredValue": "Courtlandt"},
                            {"userEnteredValue": "Glenbrook Valley"},
                            {"userEnteredValue": "Boulevard Oaks"},
                            {"userEnteredValue": "Broadacres"},
                            {"userEnteredValue": "Westmoreland"},
                            {"userEnteredValue": "Idylwood"},
                            {"userEnteredValue": "Germantown"},
                        ],
                    },
                    "showCustomUi": True,
                    "strict": False,
                },
            }
        },
        {
            "setDataValidation": {
                "range": {"sheetId": SID_HUB, "startRowIndex": 17, "endRowIndex": 18, "startColumnIndex": 4, "endColumnIndex": 5},
                "rule": {
                    "condition": {
                        "type": "ONE_OF_LIST",
                        "values": [
                            {"userEnteredValue": "25"},
                            {"userEnteredValue": "50"},
                            {"userEnteredValue": "100"},
                            {"userEnteredValue": "250"},
                            {"userEnteredValue": "500"},
                        ],
                    },
                    "showCustomUi": True,
                    "strict": False,
                },
            }
        },
        # 4. Style Instructions_&_Apps_Script (Manual & SOPs)
        header_style_req(SID_INSTR, 3, row_idx=0, bg=c_charcoal, fg=c_sage, font_size=11),
        header_style_req(SID_INSTR, 3, row_idx=6, bg=c_slate, fg=c_sage, font_size=10),
        {
            "repeatCell": {
                "range": {"sheetId": SID_INSTR, "startRowIndex": 1, "endRowIndex": 19, "startColumnIndex": 0, "endColumnIndex": 3},
                "cell": {
                    "userEnteredFormat": {
                        "verticalAlignment": "TOP",
                        "wrapStrategy": "WRAP",
                        "textFormat": {"fontSize": 10},
                    }
                },
                "fields": "userEnteredFormat(verticalAlignment,wrapStrategy,textFormat.fontSize)",
            }
        },
        {
            "repeatCell": {
                "range": {"sheetId": SID_INSTR, "startRowIndex": 1, "endRowIndex": 19, "startColumnIndex": 0, "endColumnIndex": 1},
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": c_offwhite,
                        "textFormat": {"bold": True, "fontSize": 10, "foregroundColor": c_charcoal},
                        "verticalAlignment": "TOP",
                        "wrapStrategy": "WRAP",
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment,wrapStrategy)",
            }
        },
        col_width_req(SID_INSTR, 0, 1, 310),
        col_width_req(SID_INSTR, 1, 2, 760),
        col_width_req(SID_INSTR, 2, 3, 290),
        # 5. Style headers & column widths on Pending_Submissions, Approved_Edits, Good_Brick_Awards, Building_Photos, Missing_Landmark_PDFs_TPIA
        header_style_req(SID_PENDING, 22, row_idx=0, bg=c_charcoal, fg=c_sage, font_size=10),
        col_width_req(SID_PENDING, 14, 18, 190),
        col_width_req(SID_PENDING, 18, 19, 250),
        col_width_req(SID_PENDING, 19, 21, 160),
        col_width_req(SID_PENDING, 21, 22, 85),
        header_style_req(SID_APPROVED, 15, row_idx=0, bg=c_charcoal, fg=c_sage, font_size=10),
        header_style_req(SID_GB, 16, row_idx=0, bg=c_charcoal, fg=c_sage, font_size=10),
        header_style_req(SID_PHOTOS, 12, row_idx=0, bg=c_charcoal, fg=c_sage, font_size=10),
        header_style_req(SID_TPIA, 10, row_idx=0, bg=c_charcoal, fg=c_sage, font_size=10),
        col_width_req(SID_TPIA, 6, 10, 190),
        # 6. Enable BasicFilter across all 4,000 rows and 22 columns of Pending_Submissions, plus Approved_Edits, Good_Brick_Awards, Building_Photos, Missing_Landmark_PDFs_TPIA
        {
            "setBasicFilter": {
                "filter": {
                    "range": {"sheetId": SID_PENDING, "startRowIndex": 0, "endRowIndex": 4000, "startColumnIndex": 0, "endColumnIndex": 22}
                }
            }
        },
        {
            "setBasicFilter": {
                "filter": {
                    "range": {"sheetId": SID_APPROVED, "startRowIndex": 0, "endRowIndex": 1500, "startColumnIndex": 0, "endColumnIndex": 15}
                }
            }
        },
        {
            "setBasicFilter": {
                "filter": {
                    "range": {"sheetId": SID_GB, "startRowIndex": 0, "endRowIndex": 600, "startColumnIndex": 0, "endColumnIndex": 16}
                }
            }
        },
        {
            "setBasicFilter": {
                "filter": {
                    "range": {"sheetId": SID_PHOTOS, "startRowIndex": 0, "endRowIndex": 500, "startColumnIndex": 0, "endColumnIndex": 12}
                }
            }
        },
        {
            "setBasicFilter": {
                "filter": {
                    "range": {"sheetId": SID_TPIA, "startRowIndex": 0, "endRowIndex": 200, "startColumnIndex": 0, "endColumnIndex": 10}
                }
            }
        },
        # 7. Data Validation Dropdowns on Pending_Submissions (Col A: status, Col G: contributing) & Missing_Landmark_PDFs_TPIA (Col G: TPIA_Request_Status)
        {
            "setDataValidation": {
                "range": {"sheetId": SID_PENDING, "startRowIndex": 1, "endRowIndex": 4000, "startColumnIndex": 0, "endColumnIndex": 1},
                "rule": {
                    "condition": {
                        "type": "ONE_OF_LIST",
                        "values": [
                            {"userEnteredValue": "Pending"},
                            {"userEnteredValue": "Approved"},
                            {"userEnteredValue": "Needs Info"},
                            {"userEnteredValue": "On Hold"},
                            {"userEnteredValue": "Duplicate"},
                            {"userEnteredValue": "Rejected"},
                        ],
                    },
                    "showCustomUi": True,
                    "strict": False,
                },
            }
        },
        {
            "setDataValidation": {
                "range": {"sheetId": SID_PENDING, "startRowIndex": 1, "endRowIndex": 4000, "startColumnIndex": 6, "endColumnIndex": 7},
                "rule": {
                    "condition": {
                        "type": "ONE_OF_LIST",
                        "values": [
                            {"userEnteredValue": "Contributing"},
                            {"userEnteredValue": "Non-Contributing"},
                            {"userEnteredValue": "Protected Landmark"},
                            {"userEnteredValue": "Landmark"},
                            {"userEnteredValue": "Outside Historic District"},
                        ],
                    },
                    "showCustomUi": True,
                    "strict": False,
                },
            }
        },
        {
            "setDataValidation": {
                "range": {"sheetId": SID_TPIA, "startRowIndex": 1, "endRowIndex": 200, "startColumnIndex": 6, "endColumnIndex": 7},
                "rule": {
                    "condition": {
                        "type": "ONE_OF_LIST",
                        "values": [
                            {"userEnteredValue": "Needs TPIA Request"},
                            {"userEnteredValue": "Requested from HPO"},
                            {"userEnteredValue": "PDF Received"},
                            {"userEnteredValue": "Ingested into Atlas"},
                            {"userEnteredValue": "On Hold"},
                        ],
                    },
                    "showCustomUi": True,
                    "strict": False,
                },
            }
        },
        # 8. Color-Coded Conditional Formatting on Pending_Submissions Status (Col A)
        {
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [{"sheetId": SID_PENDING, "startRowIndex": 1, "endRowIndex": 4000, "startColumnIndex": 0, "endColumnIndex": 1}],
                    "booleanRule": {
                        "condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": "Approved"}]},
                        "format": {"backgroundColor": c_green_bg, "textFormat": {"bold": True, "foregroundColor": c_green_fg}},
                    },
                },
                "index": 0,
            }
        },
        {
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [{"sheetId": SID_PENDING, "startRowIndex": 1, "endRowIndex": 4000, "startColumnIndex": 0, "endColumnIndex": 1}],
                    "booleanRule": {
                        "condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": "Pending"}]},
                        "format": {"backgroundColor": c_amber_bg, "textFormat": {"bold": True, "foregroundColor": c_amber_fg}},
                    },
                },
                "index": 1,
            }
        },
        {
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [{"sheetId": SID_PENDING, "startRowIndex": 1, "endRowIndex": 4000, "startColumnIndex": 0, "endColumnIndex": 1}],
                    "booleanRule": {
                        "condition": {"type": "TEXT_CONTAINS", "values": [{"userEnteredValue": "Hold"}]},
                        "format": {"backgroundColor": c_blue_bg, "textFormat": {"bold": True, "foregroundColor": c_blue_fg}},
                    },
                },
                "index": 2,
            }
        },
        {
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [{"sheetId": SID_PENDING, "startRowIndex": 1, "endRowIndex": 4000, "startColumnIndex": 0, "endColumnIndex": 1}],
                    "booleanRule": {
                        "condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": "Rejected"}]},
                        "format": {"backgroundColor": c_red_bg, "textFormat": {"bold": True, "foregroundColor": c_red_fg}},
                    },
                },
                "index": 3,
            }
        },
        # 9. Add 12 Pre-Built Saved Filter Views across Pending_Submissions, Good_Brick_Awards, and Missing_Landmark_PDFs_TPIA
        {
            "addFilterView": {
                "filter": {
                    "title": "🚨 1. Action Needed: Community Reviews (Pending)",
                    "range": {"sheetId": SID_PENDING, "startRowIndex": 0, "endRowIndex": 4000, "startColumnIndex": 0, "endColumnIndex": 22},
                    "criteria": {
                        "0": {"condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": "Pending"}]}},
                        "18": {"condition": {"type": "TEXT_CONTAINS", "values": [{"userEnteredValue": "Community Review"}]}},
                    },
                    "sortSpecs": [{"dimensionIndex": 12, "sortOrder": "DESCENDING"}],
                }
            }
        },
        {
            "addFilterView": {
                "filter": {
                    "title": "📐 2. Footprint & Geometry Issues",
                    "range": {"sheetId": SID_PENDING, "startRowIndex": 0, "endRowIndex": 4000, "startColumnIndex": 0, "endColumnIndex": 22},
                    "criteria": {
                        "18": {"condition": {"type": "TEXT_CONTAINS", "values": [{"userEnteredValue": "Footprint"}]}},
                    },
                    "sortSpecs": [{"dimensionIndex": 12, "sortOrder": "DESCENDING"}],
                }
            }
        },
        {
            "addFilterView": {
                "filter": {
                    "title": "🏷️ 3. Building Name & Alias Suggestions",
                    "range": {"sheetId": SID_PENDING, "startRowIndex": 0, "endRowIndex": 4000, "startColumnIndex": 0, "endColumnIndex": 22},
                    "criteria": {
                        "18": {"condition": {"type": "TEXT_CONTAINS", "values": [{"userEnteredValue": "Building Name"}]}},
                    },
                    "sortSpecs": [{"dimensionIndex": 12, "sortOrder": "DESCENDING"}],
                }
            }
        },
        {
            "addFilterView": {
                "filter": {
                    "title": "📷 4. Photo Contributions Queue",
                    "range": {"sheetId": SID_PENDING, "startRowIndex": 0, "endRowIndex": 4000, "startColumnIndex": 0, "endColumnIndex": 22},
                    "criteria": {
                        "18": {"condition": {"type": "TEXT_CONTAINS", "values": [{"userEnteredValue": "Photo"}]}},
                    },
                    "sortSpecs": [{"dimensionIndex": 12, "sortOrder": "DESCENDING"}],
                }
            }
        },
        {
            "addFilterView": {
                "filter": {
                    "title": "✅ 5. Approved & Live Overrides (Newest First)",
                    "range": {"sheetId": SID_PENDING, "startRowIndex": 0, "endRowIndex": 4000, "startColumnIndex": 0, "endColumnIndex": 22},
                    "criteria": {
                        "0": {"condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": "Approved"}]}},
                    },
                    "sortSpecs": [{"dimensionIndex": 12, "sortOrder": "DESCENDING"}],
                }
            }
        },
        {
            "addFilterView": {
                "filter": {
                    "title": "📚 6. City Directory Batch Candidates (1866–1926)",
                    "range": {"sheetId": SID_PENDING, "startRowIndex": 0, "endRowIndex": 4000, "startColumnIndex": 0, "endColumnIndex": 22},
                    "criteria": {
                        "0": {"condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": "Pending"}]}},
                        "18": {"condition": {"type": "TEXT_CONTAINS", "values": [{"userEnteredValue": "Archival Batch Candidate"}]}},
                    },
                    "sortSpecs": [
                        {"dimensionIndex": 5, "sortOrder": "ASCENDING"},
                        {"dimensionIndex": 2, "sortOrder": "ASCENDING"},
                    ],
                }
            }
        },
        {
            "addFilterView": {
                "filter": {
                    "title": "🏛️ 7. Pre-1900 Pioneer Structures",
                    "range": {"sheetId": SID_PENDING, "startRowIndex": 0, "endRowIndex": 4000, "startColumnIndex": 0, "endColumnIndex": 22},
                    "criteria": {
                        "3": {"condition": {"type": "NUMBER_BETWEEN", "values": [{"userEnteredValue": "1820"}, {"userEnteredValue": "1899"}]}},
                    },
                    "sortSpecs": [{"dimensionIndex": 3, "sortOrder": "ASCENDING"}],
                }
            }
        },
        {
            "addFilterView": {
                "filter": {
                    "title": "⏸️ 8. On Hold / Needs Info / Rejected",
                    "range": {"sheetId": SID_PENDING, "startRowIndex": 0, "endRowIndex": 4000, "startColumnIndex": 0, "endColumnIndex": 22},
                    "criteria": {
                        "0": {"hiddenValues": ["Pending", "Approved", ""]},
                    },
                }
            }
        },
        # Good Brick Awards Filter Views
        {
            "addFilterView": {
                "filter": {
                    "title": "★ Recent Good Brick Winners (2020–2026)",
                    "range": {"sheetId": SID_GB, "startRowIndex": 0, "endRowIndex": 600, "startColumnIndex": 0, "endColumnIndex": 16},
                    "criteria": {
                        "0": {"condition": {"type": "NUMBER_GREATER_THAN_EQ", "values": [{"userEnteredValue": "2020"}]}},
                    },
                    "sortSpecs": [{"dimensionIndex": 0, "sortOrder": "DESCENDING"}],
                }
            }
        },
        {
            "addFilterView": {
                "filter": {
                    "title": "🏛️ Pre-1900 Historic Good Brick Sites",
                    "range": {"sheetId": SID_GB, "startRowIndex": 0, "endRowIndex": 600, "startColumnIndex": 0, "endColumnIndex": 16},
                    "criteria": {
                        "5": {"condition": {"type": "NUMBER_BETWEEN", "values": [{"userEnteredValue": "1820"}, {"userEnteredValue": "1899"}]}},
                    },
                    "sortSpecs": [{"dimensionIndex": 5, "sortOrder": "ASCENDING"}],
                }
            }
        },
        # Missing Landmark PDFs TPIA Filter Views
        {
            "addFilterView": {
                "filter": {
                    "title": "📜 1. Needs TPIA Request",
                    "range": {"sheetId": SID_TPIA, "startRowIndex": 0, "endRowIndex": 200, "startColumnIndex": 0, "endColumnIndex": 10},
                    "criteria": {
                        "6": {"condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": "Needs TPIA Request"}]}},
                    },
                }
            }
        },
        {
            "addFilterView": {
                "filter": {
                    "title": "🛡️ 2. Protected Landmarks Only (PLM)",
                    "range": {"sheetId": SID_TPIA, "startRowIndex": 0, "endRowIndex": 200, "startColumnIndex": 0, "endColumnIndex": 10},
                    "criteria": {
                        "1": {"condition": {"type": "NOT_BLANK"}},
                    },
                }
            }
        },
    ]

    Path("/tmp/admin_raw_batch.json").write_text(json.dumps({"requests": raw_requests}, indent=2), encoding="utf-8")
    print("Wrote /tmp/admin_raw_batch.json with", len(raw_requests), "requests")


if __name__ == "__main__":
    main()
