# Zero-Cost Google Sheets Curation, Moderated User Feedback & City Directory Batch Auditor

This guide explains how **Preservation Houston** administrators can manage property date corrections (such as **`1127 Key St`**, built **`1928`** per Houston City Directories vs. HCAD's rounded placeholder **`1920`**), review end-user correction suggestions, and run the polite **Houston City Directory (1866–1926) Batch Auditor**—all with **$0/month hosting cost** and **no backend database server to maintain**.

---

## 1. How the Zero-Backend Architecture Works

```
┌────────────────────────────────────────────────────────────────────────────┐
│  1. END-USER SUGGESTIONS ("✎ Suggest a Date / Data Correction" Modal)      │
│     User clicks a building -> fills out Proposed Year + Archival Evidence  │
│     -> POSTs to Google Apps Script Web App (`doPost`)                      │
│     -> Appended to Tab 1: `Pending_Submissions` with status = "Pending"    │
└───────────────────────────────────────┬────────────────────────────────────┘
                                        │
                                        ▼
┌────────────────────────────────────────────────────────────────────────────┐
│  2. PRESERVATION HOUSTON STAFF MODERATION (Google Sheet)                   │
│     Staff reviews evidence link (City Directory, Sanborn Map, Deed, etc.)  │
│     -> Changes dropdown in Column A (`status`) from "Pending" to "Approved"│
│     -> Tab 2 (`Approved_Edits`) automatically includes only Approved rows  │
└───────────────────────────────────────┬────────────────────────────────────┘
                                        │
                                        ▼
┌────────────────────────────────────────────────────────────────────────────┐
│  3. LIVE MAP OVERRIDE LAYER (Zero Rebuild Required)                        │
│     - Browser fetches `curated_overrides.json` + Published Google Sheet CSV│
│     - Overridden HCAD accounts replace their PMTiles polygon color,        │
│       3D extrusion, Time-Lapse filter year, search index, and Inspector    │
│       with a gold "✓ Verified by Preservation Houston" provenance card     │
└────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Setting Up the Google Sheet (5 Minutes)

1. Create a new Google Sheet named **`Houston Building Atlas — Curated Overrides & Moderation`**.
2. Create two tabs at the bottom:
   - **`Pending_Submissions`** (where staff enters edits or reviews public submissions)
   - **`Approved_Edits`** (auto-filtered view that is published to the live map as CSV)

### Tab 1: `Pending_Submissions` Columns (Row 1 Headers)

Paste these exact column headers into Row 1 (`A1:N1`) of **`Pending_Submissions`**:

| Col | Header | Description | Example (`1127 Key St`) |
| :--- | :--- | :--- | :--- |
| **A** | `status` | `Pending`, `Approved`, or `Rejected` (Data Validation dropdown) | `Approved` |
| **B** | `hcad_num` | 13-digit HCAD Account Number | `0621100000014` |
| **C** | `address` | Street address | `1127 KEY ST` |
| **D** | `year_built` | Verified construction year | `1928` |
| **E** | `original_hcad_year` | Original HCAD year built | `1920` |
| **F** | `historic_district` | Historic District name (optional) | `Norhill Historic District` |
| **G** | `contributing` | `Contributing` / `Noncontributing` (optional) | `Contributing` |
| **H** | `source_type` | `Houston City Directory`, `Sanborn Fire Insurance Map`, `Building Permit / Deed`, etc. | `Houston City Directory` |
| **I** | `source_citation` | Archival volume, page, or notes | `1928 Houston City Directory (Morrison & Fourmy); lot unimproved through 1926 directory` |
| **J** | `source_url` | Link to digitized archive page (optional) | `https://cdm17006.contentdm.oclc.org/digital/collection/citydir/search/searchterm/1127%20Key` |
| **K** | `verified_by` | Reviewer / archivist credit | `Preservation Houston Archival Review` |
| **L** | `submitted_by` | Submitter name/email (stays private if you only publish A:K) | `Dave Morris` |
| **M** | `submitted_at` | ISO timestamp | `2026-10-05` |
| **N** | `notes` | Internal reviewer notes | `Verified via HPL City Directories` |

### Tab 2: `Approved_Edits` Formula

In cell **`A1`** of **`Approved_Edits`**, copy the headers `A1:K1` from `Pending_Submissions`:
```text
=Pending_Submissions!A1:K1
```
In cell **`A2`** of **`Approved_Edits`**, paste this formula so **only rows where `status` is `"Approved"`** appear (and submitter email in Column L is never exposed publicly):
```text
=IFERROR(FILTER(Pending_Submissions!A2:K, Pending_Submissions!A2:A = "Approved"), "")
```

### Publishing `Approved_Edits` as a Live CSV URL

1. In Google Sheets, click **File → Share → Publish to web**.
2. Under **Link**, change *Entire Document* to **`Approved_Edits`**, and change *Web page* to **`Comma-separated values (.csv)`**.
3. Click **Publish** and copy the resulting `https://docs.google.com/spreadsheets/d/e/.../pub?gid=...&single=true&output=csv` URL.
4. Paste that URL into:
   - `"googleSheetCsvUrl"` in [`app/public/data/curated_overrides.json`](../app/public/data/curated_overrides.json) (for permanent deployment), **or**
   - The **Google Sheet Live Sync Settings (Admin)** panel inside the web app's *"✎ Suggest a Date / Data Correction"* modal for instant testing.

---

## 3. Google Apps Script Webhook for End-User Suggestions (Optional, 2 Minutes)

To let end users submit corrections directly into your **`Pending_Submissions`** tab without giving the public write access to your Google Sheet:

1. In your Google Sheet, open **Extensions → Apps Script**.
2. Replace `Code.gs` with the following script:

```javascript
/**
 * Houston Building Atlas — Moderated User Correction Webhook
 * Appends public suggestions to the `Pending_Submissions` tab with status = "Pending".
 * Public submissions NEVER go live until a Preservation Houston admin sets status = "Approved".
 */
function doPost(e) {
  var lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    var payload = JSON.parse(e.postData.contents || '{}');
    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var sheet = ss.getSheetByName('Pending_Submissions') || ss.insertSheet('Pending_Submissions');

    // Enforce "Pending" status regardless of what the client sent
    var row = [
      'Pending',
      String(payload.hcad_num || '').trim(),
      String(payload.address || '').trim(),
      Number(payload.year_built || 0) || '',
      Number(payload.original_hcad_year || 0) || '',
      String(payload.historic_district || '').trim(),
      String(payload.contributing || '').trim(),
      String(payload.source_type || 'Houston City Directory').trim(),
      String(payload.source_citation || '').trim(),
      String(payload.source_url || '').trim(),
      '', // verified_by (filled in by admin upon approval)
      String(payload.submitted_by || 'Anonymous Community Member').trim(),
      new Date().toISOString(),
      'Community submission via Houston Building Atlas'
    ];
    sheet.appendRow(row);

    return ContentService
      .createTextOutput(JSON.stringify({ ok: true, status: 'Pending' }))
      .setMimeType(ContentService.MimeType.JSON);
  } finally {
    lock.releaseLock();
  }
}
```

3. Click **Deploy → New deployment → Select type: Web app**:
   - **Execute as:** *Me*
   - **Who has access:** *Anyone*
4. Copy the `https://script.google.com/macros/s/.../exec` Web App URL and paste it into `"submissionWebhookUrl"` in [`app/public/data/curated_overrides.json`](../app/public/data/curated_overrides.json).

---

## 4. Polite Houston City Directory (1866–1926) Batch Auditor

The Houston Public Library Digital Archives hosts 39 digitized Houston City Directories (`1866–1926`) on OCLC ContentDM (`https://cdm17006.contentdm.oclc.org/digital/collection/citydir/search`).

We built [`pipeline/atlas_pipeline/city_directory_audit.py`](../pipeline/atlas_pipeline/city_directory_audit.py) to cross-reference suspect rounded-decade HCAD dates (`1890`, `1900`, `1910`, `1920`, `1930`) against the digitized directories without ever overwhelming the library's servers:

- **SQLite Persistent Cache (`pipeline/cache/citydir_cache.sqlite`):** Every volume index, search query, and OCR page response is cached locally in SQLite so no page is ever requested twice across runs.
- **Polite Rate Limiting (`--delay 2.0`):** Enforces a strict minimum pause between live HTTP requests + exponential backoff on transient errors.
- **Two-Way Discrepancy Detection:**
  1. **`EARLIER_THAN_HCAD`:** The property address appears in an earlier Houston City Directory than HCAD's `year_built` (for example, a house HCAD lists as `1920` that is already occupied in the `1908` or `1913` directory), complete with a direct link to the digitized ContentDM page and OCR snippet.
  2. **`ABSENT_THROUGH_1926_PREMATURE_HCAD_1920`:** HCAD lists `1920`, neighboring homes on the same block (`±2`, `±4` house numbers, e.g., `1123`, `1125`, `1129 Key St`) appear in the `1920–1926` directories, yet the target address (`1127 Key St`) is absent through `1926`—proving the structure was built *after* the `1926` directory (c. `1927–1929`, such as `1928`) and HCAD defaulted to `1920`.

### Example Commands

Audit a specific property address:
```bash
uv run --directory pipeline python -m atlas_pipeline.city_directory_audit \
  --address "1127 KEY ST" \
  --delay 2.0
```

Audit a batch of suspect rounded-decade properties in a Historic District (e.g., `Norhill`, `Heights`, `Sixth Ward`):
```bash
uv run --directory pipeline python -m atlas_pipeline.city_directory_audit \
  --district "Norhill" \
  --limit 20 \
  --delay 2.0 \
  --output-csv cache/norhill_audit_candidates.csv
```

Import the generated `cache/norhill_audit_candidates.csv` into your Google Sheet's `Pending_Submissions` tab, review the linked ContentDM directory pages, and set `status` to **`Approved`** to publish them to the live map!
