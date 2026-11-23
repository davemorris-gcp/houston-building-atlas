var DQUOTE_ = String.fromCharCode(34);
var SQUOTE_ = String.fromCharCode(39);

var TABLE_REGISTRY_ = {
  Areas: {
    workbook: 'Monitoring_Core',
    sheetName: 'Areas',
    primaryKey: 'area_id',
    headers: [
      'area_id', 'name', 'area_type', 'priority_status', 'age_rule_cutoff_year',
      'dev_period_start', 'dev_period_end', 'dev_period_confidence',
      'assigned_screeners', 'local_partners', 'notes', 'updated_at'
    ]
  },
  Parcels: {
    workbook: 'Monitoring_Core',
    sheetName: 'Parcels',
    primaryKey: 'parcel_id',
    headers: [
      'parcel_id', 'hcad_num', 'campus_group_id', 'related_hcads', 'primary_address',
      'perimeter_addresses', 'building_name', 'year_built', 'hcad_year_built',
      'subdivision_name', 'area_ids', 'owner_of_record', 'land_use_class',
      'review_status', 'recommended_significance', 'significance_level',
      'significance_basis', 'contributing_status', 'contributing_override',
      'integrity', 'protection_level', 'designation_summary', 'threat_stage',
      'signal_flags', 'advocacy_status', 'outcome', 'attention_level',
      'attention_override', 'watch_property_id', 'added_at', 'updated_by', 'updated_at'
    ]
  },
  Decisions: {
    workbook: 'Monitoring_Core',
    sheetName: 'Decisions',
    primaryKey: 'decision_id',
    headers: [
      'decision_id', 'parcel_id', 'hcad_num', 'latest_app_no', 'latest_cycle_key',
      'actor_role', 'review_status', 'recommended_significance', 'significance_level',
      'significance_basis', 'contributing_override', 'integrity', 'standing_status',
      'advocacy_status', 'outcome', 'attention_override', 'screening_notes',
      'story_check_json', 'atlas_writeback_summary', 'decided_by', 'decided_at', 'history_json'
    ]
  },
  Events: {
    workbook: 'Monitoring_Core',
    sheetName: 'Events',
    primaryKey: 'event_id',
    headers: [
      'event_id', 'parcel_id', 'hcad_num', 'threat_stage', 'source_agency',
      'event_kind', 'filing_date', 'detected_at', 'is_historical_baseline',
      'headline', 'summary', 'party_names', 'mismatch_flag', 'source_url', 'raw_ref_id'
    ]
  },
  Watchlist_Dossiers: {
    workbook: 'Monitoring_Core',
    sheetName: 'Watchlist_Dossiers',
    primaryKey: 'watch_property_id',
    headers: [
      'watch_property_id', 'parcel_id', 'hcad_num', 'primary_address', 'property_name',
      'attention_level', 'advocacy_status', 'outcome', 'dossier_json', 'updated_by', 'updated_at'
    ]
  },
  _Monitoring_Roles: {
    workbook: 'Monitoring_Core',
    sheetName: '_Monitoring_Roles',
    primaryKey: 'email',
    hidden: true,
    headers: ['email', 'role', 'assigned_areas', 'display_label', 'active', 'updated_at']
  },
  Staff_Parcel_Strategy: {
    workbook: 'Atlas_Monitoring_Staff_Only',
    sheetName: 'Staff_Parcel_Strategy',
    primaryKey: 'parcel_id',
    headers: [
      'parcel_id', 'hcad_num', 'strategy_notes', 'contact_notes',
      'owner_relationship_notes', 'decision_matrix_json', 'updated_by', 'updated_at'
    ]
  },
  Watched_People_And_Firms: {
    workbook: 'Atlas_Monitoring_Staff_Only',
    sheetName: 'Watched_People_And_Firms',
    primaryKey: 'entity_id',
    headers: [
      'entity_id', 'entity_name', 'normalized_key', 'entity_role',
      'pattern_notes', 'linked_parcel_ids', 'active', 'updated_by', 'updated_at'
    ]
  }
};

var PIPELINE_MACHINE_PARCEL_COLS_ = {
  perimeter_addresses: true, owner_of_record: true, land_use_class: true,
  protection_level: true, designation_summary: true, threat_stage: true,
  signal_flags: true, contributing_status: true, attention_level: true, updated_at: true
};

var STAFF_ONLY_DECISION_COLS_ = {
  significance_level: true, contributing_override: true, advocacy_status: true,
  outcome: true, attention_override: true, atlas_writeback_summary: true
};

var TERMINAL_OUTCOMES_ = {
  lost: true, lost_before_review: true, not_relevant: true,
  saved: true, moved: true, no_longer_threatened: true
};

var ALLOWED_PREF_KEYS_ = {
  actStream: true, actGroupBy: true, lastMode: true, cycleViewMode: true,
  reviewerName: true, inspectorWide: true, ecosystemTheme: true, actRange: true
};

function sanitizeDashesServer_(val) {
  return String(val === null || val === undefined ? '' : val)
    .replace(/ \u2014 /g, ' - ')
    .replace(/\u2014/g, '-')
    .replace(/ \u2013 /g, ' - ')
    .replace(/\u2013/g, '-');
}

function sanitizeSheetFormulaPrefix_(val) {
  var s = sanitizeDashesServer_(val)
    .replace(/[\r\n\t]+/g, ' ')
    .trim();
  if (s && /^[=+\-@']/.test(s)) return SQUOTE_ + s.replace(/^'+/, '');
  return s;
}

function stripLeadingSheetTick_(val) {
  var s = String(val === null || val === undefined ? '' : val);
  return s.charAt(0) === SQUOTE_ ? s.slice(1) : s;
}

function constantTimeEquals_(a, b) {
  var sa = String(a || '');
  var sb = String(b || '');
  if (!sa || !sb || sa.length !== sb.length) return false;
  var diff = 0;
  for (var i = 0; i < sa.length; i++) {
    diff |= (sa.charCodeAt(i) ^ sb.charCodeAt(i));
  }
  return diff === 0;
}

function extractBalancedTagJson_(text, prefix) {
  var s = String(text || '');
  var idx = s.indexOf(prefix);
  if (idx === -1) return null;
  var start = idx + prefix.length;
  while (start < s.length && /\s/.test(s.charAt(start))) start++;
  if (s.charAt(start) !== '{') return null;
  var depth = 0;
  var inStr = false;
  var esc = false;
  for (var i = start; i < s.length; i++) {
    var ch = s.charAt(i);
    if (inStr) {
      if (esc) { esc = false; }
      else if (ch === '\\') { esc = true; }
      else if (ch === DQUOTE_) { inStr = false; }
    } else {
      if (ch === DQUOTE_) { inStr = true; }
      else if (ch === '{') { depth++; }
      else if (ch === '}') {
        depth--;
        if (depth === 0) {
          try { return JSON.parse(s.slice(start, i + 1)); } catch (_e) { return null; }
        }
      }
    }
  }
  return null;
}

function stripTaggedJsonSpanServer_(text, prefix) {
  var s = String(text || '');
  var idx = s.indexOf(prefix);
  if (idx === -1) return s.trim();
  var endBracket = s.indexOf(']', idx);
  var prefixPart = s.slice(0, idx).trim();
  var suffixPart = endBracket !== -1 ? s.slice(endBracket + 1).trim() : '';
  return (prefixPart + (prefixPart && suffixPart ? ' ' : '') + suffixPart).trim();
}

function defaultHostEnv_() {
  return {
    spreadsheetApp: typeof SpreadsheetApp !== 'undefined' ? SpreadsheetApp : null,
    driveApp: typeof DriveApp !== 'undefined' ? DriveApp : null,
    cacheService: typeof CacheService !== 'undefined' ? CacheService : null,
    propertiesService: typeof PropertiesService !== 'undefined' ? PropertiesService : null,
    lockService: typeof LockService !== 'undefined' ? LockService : null,
    urlFetchApp: typeof UrlFetchApp !== 'undefined' ? UrlFetchApp : null,
    contentService: typeof ContentService !== 'undefined' ? ContentService : null,
    nowMs: function() { return new Date().getTime(); }
  };
}

function getScriptCacheSafe_(env) {
  var cs = env && env.cacheService;
  if (cs && typeof cs.getScriptCache === 'function') {
    try { return cs.getScriptCache(); } catch (_e) { return null; }
  }
  return null;
}

function getScriptPropsSafe_(env) {
  var ps = env && env.propertiesService;
  if (ps && typeof ps.getScriptProperties === 'function') {
    try { return ps.getScriptProperties(); } catch (_e) { return null; }
  }
  return null;
}

function getPropString_(env, key) {
  var p = getScriptPropsSafe_(env);
  if (!p || !key) return '';
  try { return String(p.getProperty(key) || '').trim(); } catch (_e) { return ''; }
}

function invalidateCacheKey_(env, key) {
  var c = getScriptCacheSafe_(env);
  if (c && key) {
    try { c.remove(key); } catch (_e) {}
  }
}

function createTextResponse_(env, text, mimeKind) {
  var cs = env && env.contentService;
  var mime = (cs && cs.MimeType && cs.MimeType[mimeKind]) ? cs.MimeType[mimeKind] : (mimeKind === 'CSV' ? 'text/csv' : 'application/json');
  if (cs && typeof cs.createTextOutput === 'function') {
    return cs.createTextOutput(String(text || '')).setMimeType(mime);
  }
  return {
    getMimeType: function() { return mime; },
    getContent: function() { return String(text || ''); }
  };
}

function createJsonResponse_(env, obj) {
  return createTextResponse_(env, JSON.stringify(obj), 'JSON');
}

function sheetToCsvText_(sheet) {
  var values = (sheet && typeof sheet.getDataRange === 'function') ? sheet.getDataRange().getValues() : [];
  var dqRe = new RegExp(DQUOTE_, 'g');
  return values.map(function(row) {
    return row.map(function(cell) {
      var s = String(cell === null || cell === undefined ? '' : cell);
      return (s.indexOf(',') !== -1 || s.indexOf(DQUOTE_) !== -1 || s.indexOf('\n') !== -1) ? DQUOTE_ + s.replace(dqRe, DQUOTE_ + DQUOTE_) + DQUOTE_ : s;
    }).join(',');
  }).join('\n');
}

/* ---------- Declarative Header-Keyed Table Engine ---------- */

function ensureTableSheet_(ss, spec) {
  if (!ss || !spec) return null;
  var sheet = ss.getSheetByName(spec.sheetName);
  if (!sheet && typeof ss.insertSheet === 'function') {
    sheet = ss.insertSheet(spec.sheetName);
    sheet.getRange(1, 1, 1, spec.headers.length).setValues([spec.headers.slice()]);
    if (spec.hidden && typeof sheet.hideSheet === 'function') {
      sheet.hideSheet();
    }
  }
  if (!sheet) return null;
  var vals = (typeof sheet.getDataRange === 'function') ? sheet.getDataRange().getValues() : [];
  if (!vals.length || String(vals[0][0] || '').trim() === '') {
    sheet.getRange(1, 1, 1, spec.headers.length).setValues([spec.headers.slice()]);
  } else {
    var existingHdr = vals[0].map(function(h) { return String(h || '').trim(); });
    var added = false;
    for (var i = 0; i < spec.headers.length; i++) {
      if (existingHdr.indexOf(spec.headers[i]) === -1) {
        existingHdr.push(spec.headers[i]);
        added = true;
      }
    }
    if (added) {
      sheet.getRange(1, 1, 1, existingHdr.length).setValues([existingHdr]);
    }
  }
  return sheet;
}

function readTableObjects_(ss, spec) {
  if (!ss || !spec) return [];
  var sheet = ss.getSheetByName(spec.sheetName);
  if (!sheet || typeof sheet.getDataRange !== 'function') return [];
  var vals = sheet.getDataRange().getValues();
  if (!vals || vals.length < 2) return [];
  var hdr = vals[0].map(function(h) { return String(h || '').trim(); });
  var pkIdx = hdr.indexOf(spec.primaryKey);
  if (pkIdx === -1) return [];
  var out = [];
  for (var r = 1; r < vals.length; r++) {
    var row = vals[r];
    var pkVal = stripLeadingSheetTick_(row[pkIdx]).trim();
    if (!pkVal) continue;
    var obj = {};
    for (var c = 0; c < hdr.length; c++) {
      var colName = hdr[c];
      if (!colName) continue;
      obj[colName] = stripLeadingSheetTick_(row[c]);
    }
    out.push(obj);
  }
  return out;
}

function formatCellForHeader_(colName, rawVal) {
  if (rawVal === null || rawVal === undefined || rawVal === '') return '';
  if (typeof rawVal === 'boolean') return rawVal ? 'true' : 'false';
  if (typeof rawVal === 'number') return isFinite(rawVal) ? rawVal : '';
  var s = sanitizeDashesServer_(rawVal);
  if (colName === 'hcad_num' || (colName === 'parcel_id' && /^\d{10,13}$/.test(s.trim()))) {
    var padded = s.trim();
    while (padded.length < 13 && /^\d+$/.test(padded)) padded = '0' + padded;
    return SQUOTE_ + padded;
  }
  var clean = sanitizeSheetFormulaPrefix_(s);
  if (clean.length > 48500) {
    clean = clean.slice(0, 48500);
  }
  return clean;
}

function upsertTableObjectsBatch_(ss, spec, records) {
  if (!ss || !spec || !Array.isArray(records) || !records.length) {
    return { inserted: 0, updated: 0 };
  }
  var sheet = ensureTableSheet_(ss, spec);
  if (!sheet) return { inserted: 0, updated: 0 };
  var vals = sheet.getDataRange().getValues();
  var hdr = vals.length ? vals[0].map(function(h) { return String(h || '').trim(); }) : spec.headers.slice();
  var pkIdx = hdr.indexOf(spec.primaryKey);
  if (pkIdx === -1) return { inserted: 0, updated: 0 };

  var rowByPk = {};
  var grid = [];
  for (var r = 0; r < vals.length; r++) {
    var copyRow = [];
    for (var c = 0; c < hdr.length; c++) {
      copyRow.push(r === 0 ? hdr[c] : (vals[r][c] !== undefined ? vals[r][c] : ''));
    }
    grid.push(copyRow);
    if (r > 0) {
      var exPk = stripLeadingSheetTick_(copyRow[pkIdx]).trim();
      if (exPk) rowByPk[exPk] = r;
    }
  }
  if (!grid.length) {
    grid.push(hdr.slice());
  }

  var inserted = 0;
  var updated = 0;
  for (var i = 0; i < records.length; i++) {
    var rec = records[i];
    if (!rec || typeof rec !== 'object') continue;
    var pk = stripLeadingSheetTick_(rec[spec.primaryKey]).trim();
    if (!pk) continue;
    var existingRowIdx = rowByPk[pk];
    if (existingRowIdx !== undefined) {
      var target = grid[existingRowIdx];
      for (var hc = 0; hc < hdr.length; hc++) {
        var col = hdr[hc];
        if (Object.prototype.hasOwnProperty.call(rec, col) && rec[col] !== undefined) {
          target[hc] = formatCellForHeader_(col, rec[col]);
        }
      }
      updated++;
    } else {
      var newRow = [];
      for (var nc = 0; nc < hdr.length; nc++) {
        var nCol = hdr[nc];
        newRow.push(formatCellForHeader_(nCol, rec[nCol]));
      }
      rowByPk[pk] = grid.length;
      grid.push(newRow);
      inserted++;
    }
  }

  sheet.getRange(1, 1, grid.length, hdr.length).setValues(grid);
  return { inserted: inserted, updated: updated };
}

/* ---------- Roaming User State CRDT ---------- */

function toSafeEpochMs_(val, fallbackVal) {
  if (typeof val === 'number' && isFinite(val) && val > 0) return Math.floor(val);
  var s = String(val || '').trim();
  if (!s) return fallbackVal;
  var ms = Date.parse(s);
  return isFinite(ms) && ms > 0 ? Math.floor(ms) : fallbackVal;
}

function readFlagRankServer_(r) {
  if (r === 0) return 0;
  if (r === 1) return 1;
  return 2;
}

function sanitizePlainPrefVal_(val) {
  if (typeof val === 'string') return sanitizeDashesServer_(val).replace(/[\r\n\t]+/g, ' ').trim().slice(0, 500);
  if (typeof val === 'boolean' || (typeof val === 'number' && isFinite(val))) return val;
  return '';
}

function normalizeRoamingUserObj_(raw, maxReadCap, maxAuditCap) {
  var readCap = maxReadCap || 300;
  var auditCap = maxAuditCap || 300;
  if (!raw || typeof raw !== 'object') {
    return { schemaVersion: 1, updatedAt: '', caughtUpAt: '', caughtUpUpdatedAt: 0, readLedger: {}, prefs: {}, auditDecisions: {} };
  }
  var cuRaw = String(raw.caughtUpAt || '').trim();
  var cuMs = Date.parse(cuRaw);
  var caughtUpAt = isFinite(cuMs) && cuMs > 0 ? new Date(cuMs).toISOString() : '';
  var caughtUpUpdatedAt = toSafeEpochMs_(raw.caughtUpUpdatedAt, isFinite(cuMs) && cuMs > 0 ? Math.floor(cuMs) : 0);

  var mergedRawLedger = {};
  if (raw.readLedger && typeof raw.readLedger === 'object') {
    for (var lk in raw.readLedger) {
      if (Object.prototype.hasOwnProperty.call(raw.readLedger, lk)) mergedRawLedger[lk] = raw.readLedger[lk];
    }
  }
  if (Array.isArray(raw.readEventIds)) {
    var legTs = isFinite(cuMs) && cuMs > 0 ? Math.floor(cuMs) : 1;
    for (var ri = 0; ri < raw.readEventIds.length; ri++) {
      var rid = String(raw.readEventIds[ri] || '').trim().slice(0, 180);
      if (rid && !mergedRawLedger[rid]) mergedRawLedger[rid] = { r: 1, t: legTs };
    }
  }
  if (Array.isArray(raw.unreadEventIds)) {
    var legUTs = (isFinite(cuMs) && cuMs > 0 ? Math.floor(cuMs) : 1) + 1;
    for (var ui = 0; ui < raw.unreadEventIds.length; ui++) {
      var uid = String(raw.unreadEventIds[ui] || '').trim().slice(0, 180);
      if (uid && !mergedRawLedger[uid]) mergedRawLedger[uid] = { r: 0, t: legUTs };
    }
  }

  var ledgerArr = [];
  for (var id in mergedRawLedger) {
    if (!Object.prototype.hasOwnProperty.call(mergedRawLedger, id)) continue;
    var cleanId = String(id || '').trim().slice(0, 180);
    var ent = mergedRawLedger[id];
    if (!cleanId || !ent || typeof ent !== 'object') continue;
    var rFlag = ent.r === -1 ? -1 : ((ent.r === 0 || ent.read === false) ? 0 : 1);
    var tMs = toSafeEpochMs_(ent.t !== undefined ? ent.t : ent.at, 1);
    ledgerArr.push([cleanId, { r: rFlag, t: tMs }]);
  }
  ledgerArr.sort(function(a, b) {
    if (b[1].t !== a[1].t) return b[1].t - a[1].t;
    var ra = readFlagRankServer_(a[1].r);
    var rb = readFlagRankServer_(b[1].r);
    if (ra !== rb) return ra - rb;
    return a[0] < b[0] ? -1 : (a[0] > b[0] ? 1 : 0);
  });
  var readLedger = {};
  for (var li = 0; li < ledgerArr.length && li < readCap; li++) {
    readLedger[ledgerArr[li][0]] = ledgerArr[li][1];
  }

  var prefs = {};
  var rawPrefs = (raw.prefs && typeof raw.prefs === 'object') ? raw.prefs : {};
  for (var pk in rawPrefs) {
    if (!Object.prototype.hasOwnProperty.call(rawPrefs, pk) || !ALLOWED_PREF_KEYS_[pk]) continue;
    var pv = rawPrefs[pk];
    if (pv && typeof pv === 'object' && ('v' in pv)) {
      prefs[pk] = { v: sanitizePlainPrefVal_(pv.v), t: toSafeEpochMs_(pv.t, 1) };
    } else if (pv !== undefined && pv !== null) {
      prefs[pk] = { v: sanitizePlainPrefVal_(pv), t: 1 };
    }
  }

  var rawAud = (raw.auditDecisions && typeof raw.auditDecisions === 'object') ? raw.auditDecisions : {};
  var audArr = [];
  for (var ak in rawAud) {
    if (!Object.prototype.hasOwnProperty.call(rawAud, ak)) continue;
    var av = rawAud[ak];
    if (!av || typeof av !== 'object' || !av.status) continue;
    var aMs = toSafeEpochMs_(av.at !== undefined ? av.at : (av.updatedAt || av.updated_at || av.decidedAt), 1);
    var cleanAud = { status: String(av.status).slice(0, 40), at: aMs, updated_at: new Date(aMs).toISOString() };
    if (av.hcad_num) cleanAud.hcad_num = String(av.hcad_num).slice(0, 32);
    if (av.category) cleanAud.category = String(av.category).slice(0, 64);
    if (av.suggestedYear !== undefined && av.suggestedYear !== null && av.suggestedYear !== '') {
      var sYr = Number(av.suggestedYear);
      if (isFinite(sYr)) cleanAud.suggestedYear = Math.floor(sYr);
    }
    if (av.note) cleanAud.note = sanitizeDashesServer_(av.note).slice(0, 200);
    audArr.push([String(ak).slice(0, 120), cleanAud]);
  }
  audArr.sort(function(a, b) {
    if (b[1].at !== a[1].at) return b[1].at - a[1].at;
    return a[0] < b[0] ? -1 : (a[0] > b[0] ? 1 : 0);
  });
  var auditDecisions = {};
  for (var ai = 0; ai < audArr.length && ai < auditCap; ai++) {
    auditDecisions[audArr[ai][0]] = audArr[ai][1];
  }

  var maxTs = caughtUpUpdatedAt;
  for (var rk in readLedger) { if (readLedger[rk].t > maxTs) maxTs = readLedger[rk].t; }
  for (var pfk in prefs) { if (prefs[pfk].t > maxTs) maxTs = prefs[pfk].t; }
  for (var adk in auditDecisions) { if (auditDecisions[adk].at > maxTs) maxTs = auditDecisions[adk].at; }
  var expMs = toSafeEpochMs_(raw.updatedAt, 0);
  if (expMs > maxTs) maxTs = expMs;

  return {
    schemaVersion: 1,
    updatedAt: maxTs > 0 ? new Date(maxTs).toISOString() : '',
    caughtUpAt: caughtUpAt,
    caughtUpUpdatedAt: caughtUpUpdatedAt,
    readLedger: readLedger,
    prefs: prefs,
    auditDecisions: auditDecisions
  };
}

function mergeUserRoamingStateServer_(stateA, stateB) {
  var a = normalizeRoamingUserObj_(stateA, 300, 300);
  var b = normalizeRoamingUserObj_(stateB, 300, 300);
  var cuAt = a.caughtUpAt;
  var cuUpd = a.caughtUpUpdatedAt;
  if (b.caughtUpUpdatedAt > a.caughtUpUpdatedAt) {
    cuAt = b.caughtUpAt;
    cuUpd = b.caughtUpUpdatedAt;
  } else if (b.caughtUpUpdatedAt === a.caughtUpUpdatedAt) {
    if (toSafeEpochMs_(b.caughtUpAt, -1) > toSafeEpochMs_(a.caughtUpAt, -1)) cuAt = b.caughtUpAt;
  }
  var mergedLedger = {};
  var k;
  for (k in a.readLedger) { if (Object.prototype.hasOwnProperty.call(a.readLedger, k)) mergedLedger[k] = a.readLedger[k]; }
  for (k in b.readLedger) {
    if (!Object.prototype.hasOwnProperty.call(b.readLedger, k)) continue;
    var ex = mergedLedger[k];
    var inc = b.readLedger[k];
    if (!ex || inc.t > ex.t) mergedLedger[k] = inc;
    else if (inc.t === ex.t) mergedLedger[k] = { r: readFlagRankServer_(inc.r) < readFlagRankServer_(ex.r) ? inc.r : ex.r, t: ex.t };
  }
  var mergedPrefs = {};
  for (k in a.prefs) { if (Object.prototype.hasOwnProperty.call(a.prefs, k)) mergedPrefs[k] = a.prefs[k]; }
  for (k in b.prefs) {
    if (!Object.prototype.hasOwnProperty.call(b.prefs, k)) continue;
    var exP = mergedPrefs[k];
    var incP = b.prefs[k];
    if (!exP || incP.t > exP.t) mergedPrefs[k] = incP;
    else if (incP.t === exP.t && ((typeof incP.v) + ':' + String(incP.v)) > ((typeof exP.v) + ':' + String(exP.v))) mergedPrefs[k] = incP;
  }
  var mergedAudit = {};
  for (k in a.auditDecisions) { if (Object.prototype.hasOwnProperty.call(a.auditDecisions, k)) mergedAudit[k] = a.auditDecisions[k]; }
  for (k in b.auditDecisions) {
    if (!Object.prototype.hasOwnProperty.call(b.auditDecisions, k)) continue;
    var exA = mergedAudit[k];
    var incA = b.auditDecisions[k];
    if (!exA || incA.at > exA.at) mergedAudit[k] = incA;
    else if (incA.at === exA.at && JSON.stringify(incA) > JSON.stringify(exA)) mergedAudit[k] = incA;
  }
  var maxUpd = Math.max(toSafeEpochMs_(a.updatedAt, 0), toSafeEpochMs_(b.updatedAt, 0), cuUpd);
  return normalizeRoamingUserObj_({
    schemaVersion: 1,
    updatedAt: maxUpd > 0 ? new Date(maxUpd).toISOString() : '',
    caughtUpAt: cuAt,
    caughtUpUpdatedAt: cuUpd,
    readLedger: mergedLedger,
    prefs: mergedPrefs,
    auditDecisions: mergedAudit
  }, 300, 300);
}

function readUserRoamingState_(env, ss, rawEmail) {
  var email = String(rawEmail || '').trim().toLowerCase();
  if (!email) return null;
  var cacheKey = 'USER_STATE_V1:' + email;
  var cache = getScriptCacheSafe_(env);
  if (cache) {
    try {
      var cachedStr = cache.get(cacheKey);
      if (cachedStr) return JSON.parse(cachedStr);
    } catch (_e) {}
  }
  var fromProps = null;
  var props = getScriptPropsSafe_(env);
  if (props) {
    try {
      var propStr = props.getProperty(cacheKey);
      if (propStr) fromProps = JSON.parse(propStr);
    } catch (_e2) {}
  }
  var fromSheet = null;
  if (ss && typeof ss.getSheetByName === 'function') {
    var uSheet = ss.getSheetByName('_Staff_User_State');
    if (uSheet && typeof uSheet.getDataRange === 'function') {
      var vals = uSheet.getDataRange().getValues();
      for (var i = 1; i < vals.length; i++) {
        if (stripLeadingSheetTick_(vals[i][0]).trim().toLowerCase() === email) {
          try { fromSheet = JSON.parse(stripLeadingSheetTick_(vals[i][2])); } catch (_e3) {}
          break;
        }
      }
    }
  }
  if (!fromProps && !fromSheet) return null;
  var combined = (fromProps && fromSheet) ? mergeUserRoamingStateServer_(fromProps, fromSheet) : normalizeRoamingUserObj_(fromSheet || fromProps, 300, 300);
  if (cache && combined) {
    try { cache.put(cacheKey, JSON.stringify(combined), 600); } catch (_e4) {}
  }
  return combined;
}

function writeUserRoamingState_(env, ss, rawEmail, incomingRaw) {
  var email = String(rawEmail || '').trim().toLowerCase();
  if (!email) return null;
  var current = readUserRoamingState_(env, ss, email);
  var merged = current ? mergeUserRoamingStateServer_(current, incomingRaw) : normalizeRoamingUserObj_(incomingRaw, 300, 300);
  var fullJson = JSON.stringify(merged);
  if (fullJson.length > 44000) {
    merged = normalizeRoamingUserObj_(merged, 120, 120);
    fullJson = JSON.stringify(merged);
  }
  var cacheKey = 'USER_STATE_V1:' + email;
  var props = getScriptPropsSafe_(env);
  if (props) {
    var propJson = fullJson.length <= 7800 ? fullJson : JSON.stringify(normalizeRoamingUserObj_(merged, 60, 50));
    if (propJson.length <= 8500) {
      try { props.setProperty(cacheKey, propJson); } catch (_ep) {}
    } else {
      try { props.deleteProperty(cacheKey); } catch (_ed) {}
    }
  }
  if (ss && typeof ss.getSheetByName === 'function') {
    var uSheet = ss.getSheetByName('_Staff_User_State');
    if (!uSheet && typeof ss.insertSheet === 'function') {
      uSheet = ss.insertSheet('_Staff_User_State');
      uSheet.getRange(1, 1, 1, 3).setValues([['email', 'updated_at', 'state_json']]);
      if (typeof uSheet.hideSheet === 'function') uSheet.hideSheet();
    }
    if (uSheet) {
      var colA = uSheet.getRange('A:A').getValues();
      var targetRow = 0;
      var lastPopRow = 1;
      for (var r = 1; r < colA.length; r++) {
        var existingEm = stripLeadingSheetTick_(colA[r][0]).trim().toLowerCase();
        if (existingEm !== '') {
          lastPopRow = r + 1;
          if (existingEm === email) { targetRow = r + 1; break; }
        }
      }
      var writeR = targetRow > 0 ? targetRow : (lastPopRow + 1);
      uSheet.getRange(writeR, 1, 1, 3).setValues([[
        sanitizeSheetFormulaPrefix_(email),
        merged.updatedAt || new Date().toISOString(),
        sanitizeSheetFormulaPrefix_(fullJson)
      ]]);
    }
  }
  var cache = getScriptCacheSafe_(env);
  if (cache && fullJson.length <= 90000) {
    try { cache.put(cacheKey, fullJson, 600); } catch (_ec) {}
  }
  return merged;
}

/* ---------- Capability-Scoped Authentication & Role Resolution ---------- */

function readMonitoringRolesMap_(ss) {
  var rows = readTableObjects_(ss, TABLE_REGISTRY_._Monitoring_Roles);
  var byEmail = {};
  for (var i = 0; i < rows.length; i++) {
    var r = rows[i];
    var em = String(r.email || '').trim().toLowerCase();
    if (!em) continue;
    var activeStr = String(r.active === undefined ? 'true' : r.active).trim().toLowerCase();
    if (activeStr === 'false' || activeStr === '0' || activeStr === 'no') continue;
    var role = String(r.role || '').trim().toLowerCase();
    if (role !== 'staff' && role !== 'screener') continue;
    byEmail[em] = {
      email: em,
      role: role,
      assignedAreas: String(r.assigned_areas || '').split(/[;|,]+/).map(function(s) { return s.trim(); }).filter(Boolean),
      displayLabel: String(r.display_label || (role === 'staff' ? 'Preservation Staff' : 'Screener')).trim()
    };
  }
  return byEmail;
}

function collectSheetEditorEmails_(ss) {
  var emails = [];
  if (!ss) return emails;
  if (typeof ss.getOwner === 'function' && ss.getOwner()) {
    var ow = String(ss.getOwner().getEmail() || '').trim().toLowerCase();
    if (ow) emails.push(ow);
  }
  if (typeof ss.getEditors === 'function') {
    var eds = ss.getEditors() || [];
    for (var i = 0; i < eds.length; i++) {
      var em = String(eds[i].getEmail() || '').trim().toLowerCase();
      if (em && emails.indexOf(em) === -1) emails.push(em);
    }
  }
  return emails;
}

function resolveUserRoleEntry_(ss, email) {
  var normEmail = String(email || '').trim().toLowerCase();
  if (!normEmail) return null;
  var rolesMap = readMonitoringRolesMap_(ss);
  if (rolesMap[normEmail]) return rolesMap[normEmail];
  var editors = collectSheetEditorEmails_(ss);
  if (editors.indexOf(normEmail) !== -1) {
    return {
      email: normEmail,
      role: 'staff',
      assignedAreas: [],
      displayLabel: 'Preservation Staff'
    };
  }
  return null;
}

function buildAllowedActionsForActor_(actorType) {
  if (actorType === 'pipeline') {
    return {
      pipeline_pull_monitoring: true,
      pipeline_push_sweep_results: true,
      pipeline_create_drive_backup: true
    };
  }
  if (actorType === 'screener') {
    return {
      verify_admin: true,
      get_user_state: true,
      put_user_state: true,
      pull_monitoring_state: true,
      upsert_parcel_decision: true,
      upsert_watchlist_dossier: true
    };
  }
  if (actorType === 'staff') {
    return {
      verify_admin: true,
      get_user_state: true,
      put_user_state: true,
      pull_monitoring_state: true,
      upsert_parcel_decision: true,
      upsert_watchlist_dossier: true,
      upsert_staff_only: true,
      list_pending_submissions: true,
      moderate_submission: true,
      admin_approve: true,
      pipeline_pull_monitoring: true,
      pipeline_push_sweep_results: true,
      pipeline_create_drive_backup: true
    };
  }
  return {};
}

function resolveMonitoringAuth_(env, ss, payload) {
  var req = payload || {};
  var configuredClientId = getPropString_(env, 'GOOGLE_OAUTH_CLIENT_ID');

  // 1. Scoped Machine Pipeline Token (constant-time check; never grants interactive staff/moderation rights)
  var suppliedPipeToken = String(req.pipeline_token || '').trim();
  if (suppliedPipeToken) {
    var expectedPipeToken = getPropString_(env, 'PIPELINE_SYNC_TOKEN');
    if (expectedPipeToken && constantTimeEquals_(suppliedPipeToken, expectedPipeToken)) {
      return {
        authorized: true,
        actorType: 'pipeline',
        role: 'pipeline',
        email: 'automated-pipeline@houston-building-atlas.local',
        displayLabel: 'Automated Pipeline',
        assignedAreas: [],
        googleClientId: configuredClientId,
        allowedActions: buildAllowedActionsForActor_('pipeline')
      };
    }
    return { authorized: false, actorType: 'public', error: 'Invalid pipeline synchronization token.' };
  }

  // 2. Audience-Verified Google OIDC ID Token (`google_id_token`)
  var idToken = String(req.google_id_token || '').trim();
  if (idToken) {
    var fetcher = env && env.urlFetchApp;
    if (!fetcher || typeof fetcher.fetch !== 'function') {
      return { authorized: false, actorType: 'public', error: 'OIDC verification service unavailable.' };
    }
    var resp = fetcher.fetch('https://oauth2.googleapis.com/tokeninfo?id_token=' + encodeURIComponent(idToken), { muteHttpExceptions: true });
    if (!resp || resp.getResponseCode() !== 200) {
      return { authorized: false, actorType: 'public', error: 'Invalid or expired Google Sign-In token.' };
    }
    var info = {};
    try { info = JSON.parse(resp.getContentText() || '{}'); } catch (_e) {}
    var emailVerified = (info.email_verified === true || String(info.email_verified).toLowerCase() === 'true');
    if (!emailVerified) {
      return { authorized: false, actorType: 'public', error: 'Google account email is not verified.' };
    }
    if (configuredClientId && String(info.aud || '').trim() !== configuredClientId) {
      return { authorized: false, actorType: 'public', error: 'Google Sign-In token audience mismatch.' };
    }
    var expSec = Number(info.exp || 0);
    var nowMs = (env && typeof env.nowMs === 'function') ? env.nowMs() : new Date().getTime();
    if (expSec > 0 && (expSec * 1000) < (nowMs - 30000)) {
      return { authorized: false, actorType: 'public', error: 'Google Sign-In session expired; please sign in again.' };
    }
    var tokenEmail = String(info.email || '').trim().toLowerCase();
    var roleEntry = resolveUserRoleEntry_(ss, tokenEmail);
    if (!roleEntry) {
      return { authorized: false, actorType: 'public', error: 'Google account is not authorized on the Monitoring Roster.' };
    }
    return {
      authorized: true,
      actorType: roleEntry.role,
      role: roleEntry.role,
      email: roleEntry.email,
      displayLabel: roleEntry.displayLabel,
      assignedAreas: roleEntry.assignedAreas,
      googleClientId: configuredClientId,
      allowedActions: buildAllowedActionsForActor_(roleEntry.role)
    };
  }

  // 3. Role-Bound Passkey Verification (never defaults blank email to Owner; Screener secret cannot spoof Staff)
  var claimedEmail = String(req.admin_email || req.verified_by || '').trim().toLowerCase();
  var passkey = String(req.admin_passkey || '').trim();
  if (!claimedEmail || !passkey) {
    return { authorized: false, actorType: 'public', error: 'Please provide both an authorized email and passkey, or use Sign in with Google.' };
  }
  var claimedEntry = resolveUserRoleEntry_(ss, claimedEmail);
  if (!claimedEntry) {
    return { authorized: false, actorType: 'public', error: 'Account email is not authorized on the Monitoring Roster or Sheet Editors list.' };
  }
  var cfgSheet = (ss && typeof ss.getSheetByName === 'function') ? ss.getSheetByName('Instructions_&_Apps_Script') : null;
  var cellPasskey = cfgSheet ? String(cfgSheet.getRange('B6').getValue() || '').trim() : '';
  var staffSecret = getPropString_(env, 'STAFF_SYNC_PASSKEY') || cellPasskey;
  var screenerSecret = getPropString_(env, 'SCREENER_SYNC_PASSKEY');

  if (claimedEntry.role === 'staff') {
    if (staffSecret && constantTimeEquals_(passkey, staffSecret)) {
      return {
        authorized: true,
        actorType: 'staff',
        role: 'staff',
        email: claimedEntry.email,
        displayLabel: claimedEntry.displayLabel,
        assignedAreas: claimedEntry.assignedAreas,
        googleClientId: configuredClientId,
        allowedActions: buildAllowedActionsForActor_('staff')
      };
    }
    return { authorized: false, actorType: 'public', error: 'Invalid Staff Sync Passkey.' };
  }

  if (claimedEntry.role === 'screener') {
    if (screenerSecret && !constantTimeEquals_(screenerSecret, staffSecret) && constantTimeEquals_(passkey, screenerSecret)) {
      return {
        authorized: true,
        actorType: 'screener',
        role: 'screener',
        email: claimedEntry.email,
        displayLabel: claimedEntry.displayLabel,
        assignedAreas: claimedEntry.assignedAreas,
        googleClientId: configuredClientId,
        allowedActions: buildAllowedActionsForActor_('screener')
      };
    }
    return { authorized: false, actorType: 'public', error: 'Screener sign-in requires Sign in with Google or a dedicated Screener passkey.' };
  }

  return { authorized: false, actorType: 'public', error: 'Unauthorized monitoring credentials.' };
}

/* ---------- Server-Side Attention & History Compaction Helpers ---------- */

function classifyProtectionServer_(desig) {
  var s = sanitizeDashesServer_(desig).toLowerCase();
  if (/\bprotected\s+landmark\b|\bplm\b|\bcity\s+historic\s+district\b|\bcoh\s+historic\s+district\b/.test(s) ||
      (/\bhistoric\s+district\b/.test(s) && !/\bnational\s+register\b|\bnrhp\b|\bpotential\b/.test(s))) {
    return 'binding';
  }
  if (/\bcity\s+landmark\b|\bnational\s+register\b|\bnrhp\b|\brecorded\s+texas\s+historic\s+landmark\b|\brthl\b|\bstate\s+antiquities\b|\bsal\b/.test(s)) {
    return 'limited';
  }
  return 'none';
}

function computeContributingServer_(parcel, areasMap) {
  var ov = String(parcel.contributing_override || '').trim().toLowerCase();
  if (ov === 'contributing' || ov === 'non_contributing' || ov === 'outside_area') return ov;
  var out = String(parcel.outcome || '').trim().toLowerCase();
  if (out === 'lost' || out === 'lost_before_review' || out === 'not_relevant') return 'non_contributing';
  var areaIds = String(parcel.area_ids || '').split(/[;|,]+/).map(function(x) { return x.trim(); }).filter(Boolean);
  var maxCutoff = 1976;
  var inArea = false;
  for (var i = 0; i < areaIds.length; i++) {
    inArea = true;
    var aRec = areasMap && areasMap[areaIds[i]];
    if (aRec) {
      var c = Math.max(Number(aRec.age_rule_cutoff_year) || 0, Number(aRec.dev_period_end) || 0);
      if (c > maxCutoff) maxCutoff = c;
    }
  }
  var inDist = /\bhistoric\s+district\b/i.test(String(parcel.designation_summary || ''));
  if (!inArea && !inDist) return 'outside_area';
  var yr = Number(parcel.year_built) || 0;
  return (yr >= 1836 && yr <= maxCutoff) ? 'contributing' : 'non_contributing';
}

function computeAttentionServer_(parcel, areasMap) {
  var attOv = String(parcel.attention_override || '').trim().toLowerCase();
  if (attOv === 'act' || attOv === 'watch' || attOv === 'record') return attOv;
  var adv = String(parcel.advocacy_status || '').trim().toLowerCase();
  var out = String(parcel.outcome || '').trim().toLowerCase();
  if (adv === 'closed' || TERMINAL_OUTCOMES_[out]) return 'record';
  if (adv === 'recording_only') return 'record';

  var st = Math.floor(Number(parcel.threat_stage) || 0);
  var hasActive = (st >= 1 && st <= 5);
  var effSig = String(parcel.significance_level || '').trim().toLowerCase() ||
               String(parcel.recommended_significance || '').trim().toLowerCase() ||
               (String(parcel.review_status || '').trim().toLowerCase() === 'needs_research' ? 'needs_research' : '');
  var prot = String(parcel.protection_level || '').trim().toLowerCase() || classifyProtectionServer_(parcel.designation_summary || '');

  if (effSig === 'high' || effSig === 'moderate' || effSig === 'needs_research') {
    if (hasActive) {
      if (prot === 'binding' && st <= 3) return 'watch';
      return 'act';
    }
    return 'watch';
  }

  var areaIds = String(parcel.area_ids || '').split(/[;|,]+/).map(function(x) { return x.trim(); }).filter(Boolean);
  var isPriority = false;
  for (var i = 0; i < areaIds.length; i++) {
    var aid = areaIds[i];
    var ar = areasMap && areasMap[aid];
    if (aid.indexOf('area:priority:') === 0 || (ar && String(ar.priority_status || '').toLowerCase() === 'priority')) {
      isPriority = true;
      break;
    }
  }
  var contrib = String(parcel.contributing_status || '').trim().toLowerCase() || computeContributingServer_(parcel, areasMap);
  if (isPriority && contrib === 'contributing' && st >= 3 && st <= 5) return 'watch';
  return 'record';
}

function compactHistoryJsonServer_(entries, maxChars) {
  var limit = maxChars || 48000;
  var kept = Array.isArray(entries) ? entries.slice() : [];
  var dropped = 0;
  var encode = function(arr, d) {
    return JSON.stringify(d > 0 ? { truncatedCount: d, entries: arr } : { entries: arr });
  };
  var str = encode(kept, dropped);
  while (str.length > limit && kept.length > 1) {
    kept.shift();
    dropped++;
    str = encode(kept, dropped);
  }
  if (str.length > limit && kept.length === 1) {
    var single = {};
    for (var k in kept[0]) {
      if (Object.prototype.hasOwnProperty.call(kept[0], k)) single[k] = kept[0][k];
    }
    if (typeof single.note === 'string') single.note = single.note.slice(0, Math.max(120, limit - 512));
    str = encode([single], dropped);
  }
  return str;
}

/* ---------- Split-Range Formula-Safe Pending_Submissions Helpers ---------- */

function writePendingRowPreservingFormulas_(sheet, rowIdx, colsAtoR, colWGeom) {
  var row18 = [];
  for (var i = 0; i < 18; i++) {
    row18.push(colsAtoR[i] !== undefined ? colsAtoR[i] : '');
  }
  // Write A:R (cols 1..18) and W (col 23) separately so S:V (cols 19..22) ARRAYFORMULAs are never touched!
  sheet.getRange(rowIdx, 1, 1, 18).setValues([row18]);
  sheet.getRange(rowIdx, 23, 1, 1).setValues([[colWGeom ? sanitizeSheetFormulaPrefix_(colWGeom) : '']]);
}

function openStaffOnlyWorkbookSafe_(env) {
  var staffSheetId = getPropString_(env, 'STAFF_ONLY_SHEET_ID');
  if (!staffSheetId || !env || !env.spreadsheetApp || typeof env.spreadsheetApp.openById !== 'function') {
    return null;
  }
  try {
    return env.spreadsheetApp.openById(staffSheetId);
  } catch (_e) {
    return null;
  }
}

function countPendingModerationItems_(ss) {
  var pendingCount = 0;
  var pSheet = ss ? ss.getSheetByName('Pending_Submissions') : null;
  if (pSheet && typeof pSheet.getDataRange === 'function') {
    var pVals = pSheet.getDataRange().getValues();
    for (var i = 1; i < pVals.length; i++) {
      var st = String(pVals[i][0] || '').trim().toLowerCase();
      var hk = stripLeadingSheetTick_(pVals[i][1]).trim();
      if (st === 'pending' && hk && hk.indexOf('PLAT_REV:') !== 0 && hk.indexOf('WATCH_PROP:') !== 0) {
        pendingCount++;
      }
    }
  }
  var sSheet = ss ? ss.getSheetByName('Field_Surveys') : null;
  if (sSheet && typeof sSheet.getDataRange === 'function') {
    var sVals = sSheet.getDataRange().getValues();
    for (var j = 1; j < sVals.length; j++) {
      var sid = stripLeadingSheetTick_(sVals[j][0]).trim();
      var rev = String(sVals[j][2] || '').trim().toLowerCase();
      if (sid && (rev === 'unreviewed' || rev === 'needs follow-up')) {
        pendingCount++;
      }
    }
  }
  return pendingCount;
}

function listPendingModerationItems_(ss, limitCount) {
  var cap = limitCount || 200;
  var items = [];
  var pSheet = ss ? ss.getSheetByName('Pending_Submissions') : null;
  if (pSheet && typeof pSheet.getDataRange === 'function') {
    var pVals = pSheet.getDataRange().getValues();
    for (var r = 1; r < pVals.length && items.length < cap; r++) {
      var row = pVals[r];
      var st = String(row[0] || '').trim();
      var hcad = stripLeadingSheetTick_(row[1]).trim();
      if (!hcad || hcad.indexOf('PLAT_REV:') === 0 || hcad.indexOf('WATCH_PROP:') === 0) continue;
      if (st.toLowerCase() !== 'pending' && st.toLowerCase() !== 'needs info' && st.toLowerCase() !== 'on hold') continue;
      items.push({
        queueId: 'Pending_Submissions:' + (r + 1) + ':' + hcad,
        sourceTab: 'Pending_Submissions',
        rowNumber: r + 1,
        status: st,
        hcad_num: hcad,
        address: stripLeadingSheetTick_(row[2]),
        suggested_year_built: stripLeadingSheetTick_(row[3]),
        original_hcad_year: stripLeadingSheetTick_(row[4]),
        historic_district: stripLeadingSheetTick_(row[5]),
        contributing: stripLeadingSheetTick_(row[6]),
        source_type: stripLeadingSheetTick_(row[7]),
        source_citation: stripLeadingSheetTick_(row[8]),
        source_url: stripLeadingSheetTick_(row[9]),
        submitted_by: stripLeadingSheetTick_(row[11]),
        submitted_at: stripLeadingSheetTick_(row[12]),
        notes: stripLeadingSheetTick_(row[13]),
        building_name: stripLeadingSheetTick_(row[14]),
        alt_names: stripLeadingSheetTick_(row[15]),
        bld_style: stripLeadingSheetTick_(row[16]),
        architect: stripLeadingSheetTick_(row[17]),
        geometry_geojson: stripLeadingSheetTick_(row[22] || '')
      });
    }
  }
  var sSheet = ss ? ss.getSheetByName('Field_Surveys') : null;
  if (sSheet && typeof sSheet.getDataRange === 'function') {
    var sVals = sSheet.getDataRange().getValues();
    for (var sr = 1; sr < sVals.length && items.length < cap; sr++) {
      var sRow = sVals[sr];
      var surveyId = stripLeadingSheetTick_(sRow[0]).trim();
      var revSt = String(sRow[2] || '').trim();
      if (!surveyId) continue;
      if (revSt.toLowerCase() !== 'unreviewed' && revSt.toLowerCase() !== 'needs follow-up') continue;
      items.push({
        queueId: 'Field_Surveys:' + (sr + 1) + ':' + surveyId,
        sourceTab: 'Field_Surveys',
        rowNumber: sr + 1,
        survey_id: surveyId,
        status: revSt,
        submitted_at: stripLeadingSheetTick_(sRow[1]),
        hcad_num: stripLeadingSheetTick_(sRow[3]),
        address: stripLeadingSheetTick_(sRow[4]),
        historic_district: stripLeadingSheetTick_(sRow[5]),
        integrity_grade: stripLeadingSheetTick_(sRow[6]),
        condition_rating: stripLeadingSheetTick_(sRow[7]),
        contributing: stripLeadingSheetTick_(sRow[8]),
        original_hcad_year: stripLeadingSheetTick_(sRow[12]),
        suggested_year_built: stripLeadingSheetTick_(sRow[13]),
        source_url: stripLeadingSheetTick_(sRow[14]),
        source_citation: stripLeadingSheetTick_(sRow[15]),
        submitted_by: stripLeadingSheetTick_(sRow[16]),
        source_type: 'FIELD_SURVEY_OBSERVATION'
      });
    }
  }
  return items;
}

function upsertApprovedPendingRow_(ss, env, payload, authLabel) {
  var sheet = ss.getSheetByName('Pending_Submissions') || ss.insertSheet('Pending_Submissions');
  var rawBldName = String(payload.building_name || '').trim();
  var nameParts = rawBldName ? rawBldName.split(/[;|]/).map(function(s) { return s.trim(); }).filter(Boolean) : [];
  var primaryName = nameParts.length > 0 ? nameParts[0] : '';
  var altNames = nameParts.length > 1 ? nameParts.slice(1).join('; ') : String(payload.alt_names || '').trim();
  var rawSourceType = String(payload.source_type || 'Preservation Houston Archival Review').trim();
  var isFlagType = /(?:PHOTO_MISATTRIBUTION_FLAG|CLAIM_DISPUTE_FLAG|NARRATIVE_ERROR_FLAG)$/i.test(rawSourceType);

  var rawSuggestedYear = payload.suggested_year_built !== undefined && payload.suggested_year_built !== null && String(payload.suggested_year_built).trim() !== ''
    ? String(payload.suggested_year_built).trim()
    : (payload.year_built !== undefined && payload.year_built !== null ? String(payload.year_built).trim() : '');
  var parsedYr = (rawSuggestedYear !== '' && isFinite(Number(rawSuggestedYear))) ? Math.round(Number(rawSuggestedYear)) : NaN;
  var hasStandingYr = isFinite(parsedYr) && parsedYr >= 1836 && parsedYr <= 2035;

  var citationStr = String(payload.source_citation || '');
  var isExplicitVacant = payload.suppress_only === true ||
    (payload.suppress_only !== false && rawSuggestedYear === '0') ||
    /^vacant(\s+lot)?$|^demolished$|^none$|^n\/a$/i.test(rawSuggestedYear);

  var rawGeomVal = payload.geometry_geojson;
  var geomGeoJsonStr = '';
  if (!isExplicitVacant) {
    if (rawGeomVal && typeof rawGeomVal === 'object') geomGeoJsonStr = JSON.stringify(rawGeomVal);
    else if (typeof rawGeomVal === 'string' && rawGeomVal.trim() !== '') geomGeoJsonStr = rawGeomVal.trim();
  } else {
    // Strip stale custom polygon tags when suppressing a demolished/vacant lot so ghost footprints never persist!
    citationStr = stripTaggedJsonSpanServer_(citationStr, '[GEOMETRY_GEOJSON:');
    if (citationStr.indexOf('[DEMOLISHED / VACANT LOT') === -1) {
      citationStr = ('[DEMOLISHED / VACANT LOT - HIDE FOOTPRINT] ' + citationStr).trim();
    }
  }
  if (geomGeoJsonStr.length > 49000) {
    return { ok: false, error: 'geometry_geojson exceeds Google Sheets 49,000-character limit.' };
  }

  var sheetYearVal = isFlagType ? '' : (isExplicitVacant ? 0 : (hasStandingYr ? parsedYr : ''));
  var finalStatus = (isFlagType && !hasStandingYr && !isExplicitVacant) ? 'Resolved' : 'Approved';
  var cleanKey = stripLeadingSheetTick_(payload.hcad_num || '').trim();
  if (/^\d{10,12}$/.test(cleanKey)) {
    while (cleanKey.length < 13) cleanKey = '0' + cleanKey;
  }
  if (!cleanKey) {
    return { ok: false, error: 'Missing hcad_num for Approved_Edits writeback.' };
  }

  var colsAtoR = [
    finalStatus,
    /^\d{13}$/.test(cleanKey) ? (SQUOTE_ + cleanKey) : sanitizeSheetFormulaPrefix_(cleanKey),
    sanitizeSheetFormulaPrefix_(payload.address || ''),
    sheetYearVal,
    Number(payload.hcad_year_built || payload.original_hcad_year || 0) || '',
    sanitizeSheetFormulaPrefix_(payload.historic_district || ''),
    sanitizeSheetFormulaPrefix_(payload.contributing || ''),
    sanitizeSheetFormulaPrefix_(rawSourceType),
    sanitizeSheetFormulaPrefix_(citationStr),
    sanitizeSheetFormulaPrefix_(payload.source_url || ''),
    sanitizeSheetFormulaPrefix_(authLabel),
    sanitizeSheetFormulaPrefix_(payload.submitter_name || payload.submitted_by || authLabel),
    new Date().toISOString().slice(0, 10),
    sanitizeSheetFormulaPrefix_(payload.moderation_note || 'Approved via Staff In-App Writeback'),
    sanitizeSheetFormulaPrefix_(primaryName),
    sanitizeSheetFormulaPrefix_(altNames),
    sanitizeSheetFormulaPrefix_(payload.bld_style || ''),
    sanitizeSheetFormulaPrefix_(payload.architect || '')
  ];

  var vals = sheet.getDataRange().getValues();
  var targetRow = 0;
  var lastRow = 1;
  for (var r = 1; r < vals.length; r++) {
    var rStatus = String(vals[r][0] || '').trim().toLowerCase();
    var rKey = stripLeadingSheetTick_(vals[r][1]).trim();
    if (rKey !== '') {
      lastRow = r + 1;
      if (rKey === cleanKey && rStatus === 'approved' && finalStatus === 'Approved') {
        targetRow = r + 1;
      }
    }
  }
  var writeRowIdx = targetRow > 0 ? targetRow : (lastRow + 1);
  writePendingRowPreservingFormulas_(sheet, writeRowIdx, colsAtoR, geomGeoJsonStr);
  invalidateCacheKey_(env, 'APPROVED_EDITS_CSV_V1');
  return { ok: true, status: finalStatus, rowNumber: writeRowIdx, upserted: targetRow > 0, hcad_num: cleanKey };
}

/* ---------- Decoupled Rolling Google Drive Backup Snapshot ---------- */

function createDriveBackupSnapshot_(env, ss) {
  var nowIso = new Date().toISOString();
  var stamp = nowIso.replace(/[:.]/g, '-');
  var staffSs = openStaffOnlyWorkbookSafe_(env);
  var snapshot = {
    schemaVersion: '1.0',
    createdAt: nowIso,
    monitoringCore: {
      areas: readTableObjects_(ss, TABLE_REGISTRY_.Areas),
      parcels: readTableObjects_(ss, TABLE_REGISTRY_.Parcels),
      decisions: readTableObjects_(ss, TABLE_REGISTRY_.Decisions),
      events: readTableObjects_(ss, TABLE_REGISTRY_.Events),
      watchlistDossiers: readTableObjects_(ss, TABLE_REGISTRY_.Watchlist_Dossiers),
      roles: readTableObjects_(ss, TABLE_REGISTRY_._Monitoring_Roles)
    },
    staffOnly: staffSs ? {
      parcelStrategy: readTableObjects_(staffSs, TABLE_REGISTRY_.Staff_Parcel_Strategy),
      watchedEntities: readTableObjects_(staffSs, TABLE_REGISTRY_.Watched_People_And_Firms)
    } : null
  };
  var serialized = JSON.stringify(snapshot);
  var folderId = getPropString_(env, 'BACKUP_FOLDER_ID');
  if (folderId && env && env.driveApp && typeof env.driveApp.getFolderById === 'function') {
    try {
      var folder = env.driveApp.getFolderById(folderId);
      var fileName = 'atlas_monitoring_backup_' + stamp + '.json';
      folder.createFile(fileName, serialized, 'application/json');
      var filesIter = folder.getFiles();
      var allFiles = [];
      while (filesIter && typeof filesIter.hasNext === 'function' && filesIter.hasNext()) {
        var f = filesIter.next();
        if (String(f.getName() || '').indexOf('atlas_monitoring_backup_') === 0) {
          allFiles.push(f);
        }
      }
      allFiles.sort(function(a, b) {
        var nameA = String(a.getName() || '');
        var nameB = String(b.getName() || '');
        return nameB < nameA ? -1 : (nameB > nameA ? 1 : 0);
      });
      var pruned = 0;
      for (var p = 60; p < allFiles.length; p++) {
        if (typeof allFiles[p].setTrashed === 'function') {
          allFiles[p].setTrashed(true);
          pruned++;
        }
      }
      return { ok: true, target: 'drive_folder', fileName: fileName, prunedCount: pruned, createdAt: nowIso };
    } catch (_driveErr) {}
  }

  // Fallback vault spreadsheet (`BACKUP_VAULT_SHEET_ID`) using SpreadsheetApp scope when DriveApp is unconfigured
  var backupSheetId = getPropString_(env, 'BACKUP_VAULT_SHEET_ID');
  if (backupSheetId && env && env.spreadsheetApp && typeof env.spreadsheetApp.openById === 'function') {
    var vaultSs = env.spreadsheetApp.openById(backupSheetId);
    var logSheet = vaultSs.getSheetByName('Backup_Snapshots') || vaultSs.insertSheet('Backup_Snapshots');
    var vals = logSheet.getDataRange().getValues();
    if (!vals.length || String(vals[0][0] || '') !== 'snapshot_id') {
      logSheet.getRange(1, 1, 1, 5).setValues([['snapshot_id', 'created_at', 'parcel_count', 'dossier_count', 'summary_json']]);
      vals = [['snapshot_id', 'created_at', 'parcel_count', 'dossier_count', 'summary_json']];
    }
    var summaryMeta = JSON.stringify({
      areas: snapshot.monitoringCore.areas.length,
      parcels: snapshot.monitoringCore.parcels.length,
      decisions: snapshot.monitoringCore.decisions.length,
      events: snapshot.monitoringCore.events.length,
      watchlistDossiers: snapshot.monitoringCore.watchlistDossiers.length,
      staffOnlyIncluded: Boolean(snapshot.staffOnly)
    });
    var nextRow = vals.length + 1;
    logSheet.getRange(nextRow, 1, 1, 5).setValues([[
      'SNAP_' + stamp,
      nowIso,
      snapshot.monitoringCore.parcels.length,
      snapshot.monitoringCore.watchlistDossiers.length,
      sanitizeSheetFormulaPrefix_(serialized.length <= 48000 ? serialized : summaryMeta)
    ]]);
    return { ok: true, target: 'backup_vault_sheet', snapshotId: 'SNAP_' + stamp, createdAt: nowIso };
  }
  return { ok: true, target: 'in_place_sheets_only', createdAt: nowIso };
}

/* ---------- Main Controller Factory ---------- */

function createAppsScriptController_(customEnv) {
  var env = customEnv || defaultHostEnv_();

  function handleDoGet(e) {
    // Always evict retired public plat_sync cache key so stale cached rows never survive cutover
    invalidateCacheKey_(env, 'PLAT_SYNC_CSV_V1');
    var feed = (e && e.parameter && e.parameter.feed) ? String(e.parameter.feed).trim().toLowerCase() : '';
    if (feed && feed !== 'approved_edits') {
      return createJsonResponse_(env, {
        ok: false,
        authorized: false,
        error: 'Retired endpoint: internal monitoring state requires authenticated POST pull_monitoring_state.'
      });
    }
    var ss = env.spreadsheetApp.getActiveSpreadsheet();
    var cache = getScriptCacheSafe_(env);
    if (cache) {
      var cachedApp = cache.get('APPROVED_EDITS_CSV_V1');
      if (cachedApp) return createTextResponse_(env, cachedApp, 'CSV');
    }
    var sheet = ss.getSheetByName('Approved_Edits');
    var csv = sheetToCsvText_(sheet);
    if (cache && csv && csv.length <= 95000) {
      try { cache.put('APPROVED_EDITS_CSV_V1', csv, 180); } catch (_e2) {}
    }
    return createTextResponse_(env, csv, 'CSV');
  }

  function handleDoPost(e) {
    var payload = {};
    try {
      payload = JSON.parse((e && e.postData && e.postData.contents) || '{}');
    } catch (_parseErr) {
      return createJsonResponse_(env, { ok: false, error: 'Malformed JSON request body.' });
    }
    var action = String(payload.action || '').trim();

    // Decoupled Drive backup runs outside the 25s sheet mutation lock so slow Drive I/O never blocks human saves
    if (action === 'pipeline_create_drive_backup') {
      var ssBackup = env.spreadsheetApp.getActiveSpreadsheet();
      var bAuth = resolveMonitoringAuth_(env, ssBackup, payload);
      if (!bAuth.authorized || !bAuth.allowedActions.pipeline_create_drive_backup) {
        return createJsonResponse_(env, { ok: false, authorized: false, error: bAuth.error || 'Unauthorized backup request.' });
      }
      return createJsonResponse_(env, createDriveBackupSnapshot_(env, ssBackup));
    }

    var lock = (env.lockService && typeof env.lockService.getScriptLock === 'function') ? env.lockService.getScriptLock() : null;
    if (lock && typeof lock.waitLock === 'function') {
      lock.waitLock(25000);
    }
    try {
      var ss = env.spreadsheetApp.getActiveSpreadsheet();

      if (action === 'verify_admin') {
        var vAuth = resolveMonitoringAuth_(env, ss, payload);
        if (!vAuth.authorized || !vAuth.allowedActions.verify_admin) {
          return createJsonResponse_(env, { authorized: false, error: vAuth.error || 'Access denied.' });
        }
        return createJsonResponse_(env, {
          ok: true,
          authorized: true,
          email: vAuth.email,
          role: vAuth.role,
          displayLabel: vAuth.displayLabel,
          assignedAreas: vAuth.assignedAreas,
          googleClientId: vAuth.googleClientId || '',
          supportsRoamingSync: true,
          supportsPhase1Monitoring: true,
          userState: readUserRoamingState_(env, ss, vAuth.email)
        });
      }

      if (action === 'get_user_state') {
        var gAuth = resolveMonitoringAuth_(env, ss, payload);
        if (!gAuth.authorized || !gAuth.allowedActions.get_user_state) {
          return createJsonResponse_(env, { ok: false, authorized: false, error: gAuth.error || 'Access denied.' });
        }
        return createJsonResponse_(env, {
          ok: true,
          authorized: true,
          role: gAuth.role,
          supportsRoamingSync: true,
          email: gAuth.email,
          userState: readUserRoamingState_(env, ss, gAuth.email)
        });
      }

      if (action === 'put_user_state') {
        var pAuth = resolveMonitoringAuth_(env, ss, payload);
        if (!pAuth.authorized || !pAuth.allowedActions.put_user_state) {
          return createJsonResponse_(env, { ok: false, authorized: false, error: pAuth.error || 'Access denied.' });
        }
        var savedState = writeUserRoamingState_(env, ss, pAuth.email, payload.user_state || {});
        return createJsonResponse_(env, {
          ok: true,
          authorized: true,
          role: pAuth.role,
          supportsRoamingSync: true,
          email: pAuth.email,
          userState: savedState
        });
      }

      if (action === 'pull_monitoring_state' || action === 'pipeline_pull_monitoring') {
        var pullAuth = resolveMonitoringAuth_(env, ss, payload);
        var reqCap = action === 'pipeline_pull_monitoring' ? 'pipeline_pull_monitoring' : 'pull_monitoring_state';
        if (!pullAuth.authorized || !pullAuth.allowedActions[reqCap]) {
          return createJsonResponse_(env, { ok: false, authorized: false, error: pullAuth.error || 'Access denied.' });
        }
        var areas = readTableObjects_(ss, TABLE_REGISTRY_.Areas);
        var parcels = readTableObjects_(ss, TABLE_REGISTRY_.Parcels);
        var decisions = readTableObjects_(ss, TABLE_REGISTRY_.Decisions);
        var events = readTableObjects_(ss, TABLE_REGISTRY_.Events);
        var rawDossiers = readTableObjects_(ss, TABLE_REGISTRY_.Watchlist_Dossiers);
        var parsedDossiers = [];
        for (var di = 0; di < rawDossiers.length; di++) {
          var dr = rawDossiers[di];
          var dObj = null;
          if (dr.dossier_json) {
            try { dObj = JSON.parse(dr.dossier_json); } catch (_de) { dObj = null; }
          }
          parsedDossiers.push(dObj || dr);
        }
        var respBody = {
          ok: true,
          authorized: true,
          role: pullAuth.role,
          email: pullAuth.email,
          assignedAreas: pullAuth.assignedAreas,
          areas: areas,
          parcels: parcels,
          decisions: decisions,
          events: events,
          watchlistDossiers: parsedDossiers
        };
        if (pullAuth.actorType === 'staff') {
          var staffSs = openStaffOnlyWorkbookSafe_(env);
          respBody.staffOnly = {
            parcelStrategy: staffSs ? readTableObjects_(staffSs, TABLE_REGISTRY_.Staff_Parcel_Strategy) : [],
            watchedEntities: staffSs ? readTableObjects_(staffSs, TABLE_REGISTRY_.Watched_People_And_Firms) : []
          };
          respBody.pendingModerationCount = countPendingModerationItems_(ss);
        } else if (pullAuth.actorType === 'pipeline') {
          // Pipeline receives watched entity names ONLY for automated sweep matching, never strategy/contact notes!
          var pipeStaffSs = openStaffOnlyWorkbookSafe_(env);
          var rawWatched = pipeStaffSs ? readTableObjects_(pipeStaffSs, TABLE_REGISTRY_.Watched_People_And_Firms) : [];
          respBody.watchedEntitiesForMatching = rawWatched.map(function(w) {
            return {
              entity_id: w.entity_id,
              entity_name: w.entity_name,
              normalized_key: w.normalized_key,
              entity_role: w.entity_role,
              active: w.active
            };
          });
        }
        return createJsonResponse_(env, respBody);
      }

      if (action === 'upsert_parcel_decision') {
        var decAuth = resolveMonitoringAuth_(env, ss, payload);
        if (!decAuth.authorized || !decAuth.allowedActions.upsert_parcel_decision) {
          return createJsonResponse_(env, { ok: false, authorized: false, error: decAuth.error || 'Access denied.' });
        }
        var patch = payload.patch || payload.decision || {};
        for (var sc in STAFF_ONLY_DECISION_COLS_) {
          if (Object.prototype.hasOwnProperty.call(STAFF_ONLY_DECISION_COLS_, sc) &&
              patch[sc] !== undefined && decAuth.actorType !== 'staff') {
            return createJsonResponse_(env, {
              ok: false,
              authorized: false,
              forbiddenField: sc,
              error: 'Screener role cannot modify staff-only field: ' + sc
            });
          }
        }
        var parcelId = String(patch.parcel_id || payload.parcel_id || '').trim();
        if (!parcelId) {
          return createJsonResponse_(env, { ok: false, error: 'Missing parcel_id for decision upsert.' });
        }
        var areasList = readTableObjects_(ss, TABLE_REGISTRY_.Areas);
        var areasMap = {};
        for (var ai = 0; ai < areasList.length; ai++) areasMap[areasList[ai].area_id] = areasList[ai];

        var existingParcels = readTableObjects_(ss, TABLE_REGISTRY_.Parcels);
        var existingDecisions = readTableObjects_(ss, TABLE_REGISTRY_.Decisions);
        var curParcel = null;
        var curDec = null;
        for (var pi = 0; pi < existingParcels.length; pi++) {
          if (existingParcels[pi].parcel_id === parcelId) { curParcel = existingParcels[pi]; break; }
        }
        var decId = 'DEC:' + parcelId;
        for (var dci = 0; dci < existingDecisions.length; dci++) {
          if (existingDecisions[dci].decision_id === decId || existingDecisions[dci].parcel_id === parcelId) {
            curDec = existingDecisions[dci];
            break;
          }
        }

        var expUpd = String(payload.expected_updated_at || '').trim();
        if (expUpd && curDec && curDec.decided_at && expUpd !== String(curDec.decided_at).trim()) {
          return createJsonResponse_(env, {
            ok: false,
            conflict: true,
            error: 'Parcel decision was updated by another session; reload latest state before saving.',
            currentParcel: curParcel,
            currentDecision: curDec
          });
        }

        var nowIso = new Date().toISOString();
        var baseP = curParcel || { parcel_id: parcelId, added_at: nowIso };
        var baseD = curDec || { decision_id: decId, parcel_id: parcelId };
        var actorLabel = decAuth.displayLabel || (decAuth.actorType === 'staff' ? 'Preservation Staff' : 'Screener');

        var nextDec = {
          decision_id: decId,
          parcel_id: parcelId,
          hcad_num: patch.hcad_num !== undefined ? patch.hcad_num : (baseD.hcad_num || baseP.hcad_num || ''),
          latest_app_no: patch.latest_app_no !== undefined ? patch.latest_app_no : (baseD.latest_app_no || ''),
          latest_cycle_key: patch.latest_cycle_key !== undefined ? patch.latest_cycle_key : (baseD.latest_cycle_key || ''),
          actor_role: decAuth.actorType,
          review_status: patch.review_status !== undefined ? patch.review_status : (baseD.review_status || baseP.review_status || 'not_reviewed'),
          recommended_significance: patch.recommended_significance !== undefined ? patch.recommended_significance : (baseD.recommended_significance || baseP.recommended_significance || ''),
          significance_level: (decAuth.actorType === 'staff' && patch.significance_level !== undefined) ? patch.significance_level : (baseD.significance_level || baseP.significance_level || ''),
          significance_basis: patch.significance_basis !== undefined ? patch.significance_basis : (baseD.significance_basis || baseP.significance_basis || ''),
          contributing_override: (decAuth.actorType === 'staff' && patch.contributing_override !== undefined) ? patch.contributing_override : (baseD.contributing_override || baseP.contributing_override || ''),
          integrity: patch.integrity !== undefined ? patch.integrity : (baseD.integrity || baseP.integrity || ''),
          standing_status: patch.standing_status !== undefined ? patch.standing_status : (baseD.standing_status || ''),
          advocacy_status: (decAuth.actorType === 'staff' && patch.advocacy_status !== undefined) ? patch.advocacy_status : (baseD.advocacy_status || baseP.advocacy_status || 'monitoring'),
          outcome: (decAuth.actorType === 'staff' && patch.outcome !== undefined) ? patch.outcome : (baseD.outcome || baseP.outcome || ''),
          attention_override: (decAuth.actorType === 'staff' && patch.attention_override !== undefined) ? patch.attention_override : (baseD.attention_override || baseP.attention_override || ''),
          screening_notes: patch.screening_notes !== undefined ? patch.screening_notes : (baseD.screening_notes || ''),
          story_check_json: typeof patch.story_check_json === 'object' && patch.story_check_json !== null ? JSON.stringify(patch.story_check_json) : (patch.story_check_json !== undefined ? patch.story_check_json : (baseD.story_check_json || '')),
          atlas_writeback_summary: (decAuth.actorType === 'staff' && patch.atlas_writeback_summary !== undefined) ? patch.atlas_writeback_summary : (baseD.atlas_writeback_summary || ''),
          decided_by: actorLabel,
          decided_at: nowIso,
          history_json: ''
        };

        var histList = [];
        if (baseD.history_json) {
          try {
            var ph = JSON.parse(baseD.history_json);
            if (Array.isArray(ph)) histList = ph;
            else if (ph && Array.isArray(ph.entries)) histList = ph.entries;
          } catch (_he) {}
        }
        histList.push({
          at: nowIso,
          role: decAuth.actorType,
          by: actorLabel,
          review_status: nextDec.review_status,
          recommended_significance: nextDec.recommended_significance,
          significance_level: nextDec.significance_level,
          advocacy_status: nextDec.advocacy_status,
          outcome: nextDec.outcome,
          note: String(nextDec.screening_notes || '').slice(0, 280)
        });
        nextDec.history_json = compactHistoryJsonServer_(histList, 48000);

        var nextParcel = {};
        for (var bk in baseP) { if (Object.prototype.hasOwnProperty.call(baseP, bk)) nextParcel[bk] = baseP[bk]; }
        var metaCols = ['hcad_num', 'campus_group_id', 'related_hcads', 'primary_address', 'perimeter_addresses', 'building_name', 'year_built', 'hcad_year_built', 'subdivision_name', 'area_ids', 'owner_of_record', 'land_use_class', 'designation_summary', 'threat_stage', 'signal_flags', 'watch_property_id'];
        for (var mi = 0; mi < metaCols.length; mi++) {
          var mc = metaCols[mi];
          if (patch[mc] !== undefined) nextParcel[mc] = patch[mc];
        }
        nextParcel.parcel_id = parcelId;
        nextParcel.review_status = nextDec.review_status;
        nextParcel.recommended_significance = nextDec.recommended_significance;
        nextParcel.significance_level = nextDec.significance_level;
        nextParcel.significance_basis = nextDec.significance_basis;
        nextParcel.contributing_override = nextDec.contributing_override;
        nextParcel.integrity = nextDec.integrity;
        nextParcel.advocacy_status = nextDec.advocacy_status;
        nextParcel.outcome = nextDec.outcome;
        nextParcel.attention_override = nextDec.attention_override;
        nextParcel.protection_level = classifyProtectionServer_(nextParcel.designation_summary || '');
        if (String(nextParcel.advocacy_status || '').toLowerCase() === 'closed' || TERMINAL_OUTCOMES_[String(nextParcel.outcome || '').toLowerCase()]) {
          nextParcel.threat_stage = 6;
        }
        nextParcel.contributing_status = computeContributingServer_(nextParcel, areasMap);
        nextParcel.attention_level = computeAttentionServer_(nextParcel, areasMap);
        nextParcel.added_at = baseP.added_at || nowIso;
        nextParcel.updated_by = actorLabel;
        nextParcel.updated_at = nowIso;

        upsertTableObjectsBatch_(ss, TABLE_REGISTRY_.Parcels, [nextParcel]);
        upsertTableObjectsBatch_(ss, TABLE_REGISTRY_.Decisions, [nextDec]);

        return createJsonResponse_(env, {
          ok: true,
          authorized: true,
          role: decAuth.role,
          parcel: nextParcel,
          decision: nextDec
        });
      }

      if (action === 'upsert_watchlist_dossier') {
        var wlAuth = resolveMonitoringAuth_(env, ss, payload);
        if (!wlAuth.authorized || !wlAuth.allowedActions.upsert_watchlist_dossier) {
          return createJsonResponse_(env, { ok: false, authorized: false, error: wlAuth.error || 'Access denied.' });
        }
        var dObj = payload.dossier || {};
        var wid = String(dObj.id || payload.watch_property_id || '').trim();
        if (!wid) {
          return createJsonResponse_(env, { ok: false, error: 'Missing watchlist dossier id.' });
        }
        if (wlAuth.actorType === 'screener') {
          var exDossiers = readTableObjects_(ss, TABLE_REGISTRY_.Watchlist_Dossiers);
          var existsOnWl = false;
          for (var wi = 0; wi < exDossiers.length; wi++) {
            if (exDossiers[wi].watch_property_id === wid) { existsOnWl = true; break; }
          }
          if (!existsOnWl) {
            return createJsonResponse_(env, { ok: false, authorized: false, error: 'Only Staff can create or promote new Watchlist dossiers.' });
          }
        }
        var dJson = JSON.stringify(dObj);
        if (dJson.length > 48000 && dObj.photoDataUrl) {
          var slim = {};
          for (var dk in dObj) { if (Object.prototype.hasOwnProperty.call(dObj, dk)) slim[dk] = dObj[dk]; }
          slim.photoDataUrl = '';
          dJson = JSON.stringify(slim);
        }
        if (dJson.length > 48500) {
          return createJsonResponse_(env, { ok: false, error: 'Watchlist dossier exceeds 48,500-character cell safety limit.' });
        }
        var nowWlIso = new Date().toISOString();
        var wlRow = {
          watch_property_id: wid,
          parcel_id: String(dObj.parcelId || dObj.parcel_id || dObj.hcadAccount || wid).split(/[;|,]/)[0].trim(),
          hcad_num: String(dObj.hcadAccount || dObj.hcad_num || '').split(/[;|,]/)[0].trim(),
          primary_address: String(dObj.address || ''),
          property_name: String(dObj.propertyName || dObj.address || wid),
          attention_level: String(dObj.attentionLevel || 'watch'),
          advocacy_status: String(dObj.advocacyStatus || 'monitoring'),
          outcome: String(dObj.outcome || ''),
          dossier_json: dJson,
          updated_by: wlAuth.displayLabel || 'Preservation Staff',
          updated_at: dObj.updatedAt || nowWlIso
        };
        upsertTableObjectsBatch_(ss, TABLE_REGISTRY_.Watchlist_Dossiers, [wlRow]);
        return createJsonResponse_(env, { ok: true, authorized: true, watch_property_id: wid, updated_at: wlRow.updated_at });
      }

      if (action === 'upsert_staff_only') {
        var stAuth = resolveMonitoringAuth_(env, ss, payload);
        if (!stAuth.authorized || stAuth.actorType !== 'staff' || !stAuth.allowedActions.upsert_staff_only) {
          return createJsonResponse_(env, { ok: false, authorized: false, error: 'Staff-Only vault access requires verified Staff authorization.' });
        }
        var staffVault = openStaffOnlyWorkbookSafe_(env);
        if (!staffVault) {
          return createJsonResponse_(env, { ok: false, error: 'Staff-Only workbook is not configured in ScriptProperties (STAFF_ONLY_SHEET_ID).' });
        }
        var nowStIso = new Date().toISOString();
        var stratList = Array.isArray(payload.parcelStrategy) ? payload.parcelStrategy : (payload.strategy ? [payload.strategy] : []);
        var entList = Array.isArray(payload.watchedEntities) ? payload.watchedEntities : (payload.watchedEntity ? [payload.watchedEntity] : []);
        for (var si = 0; si < stratList.length; si++) {
          stratList[si].updated_by = 'Preservation Staff';
          stratList[si].updated_at = nowStIso;
        }
        for (var ei = 0; ei < entList.length; ei++) {
          entList[ei].updated_by = 'Preservation Staff';
          entList[ei].updated_at = nowStIso;
        }
        var sRes = stratList.length ? upsertTableObjectsBatch_(staffVault, TABLE_REGISTRY_.Staff_Parcel_Strategy, stratList) : { inserted: 0, updated: 0 };
        var eRes = entList.length ? upsertTableObjectsBatch_(staffVault, TABLE_REGISTRY_.Watched_People_And_Firms, entList) : { inserted: 0, updated: 0 };
        return createJsonResponse_(env, { ok: true, authorized: true, strategyResult: sRes, entityResult: eRes });
      }

      if (action === 'pipeline_push_sweep_results') {
        var pushAuth = resolveMonitoringAuth_(env, ss, payload);
        if (!pushAuth.authorized || !pushAuth.allowedActions.pipeline_push_sweep_results) {
          return createJsonResponse_(env, { ok: false, authorized: false, error: pushAuth.error || 'Unauthorized sweep push.' });
        }
        var isFullSeed = (pushAuth.actorType === 'staff' && payload.allowFullMigrationSeed === true);
        if (Array.isArray(payload.areas) && payload.areas.length) {
          upsertTableObjectsBatch_(ss, TABLE_REGISTRY_.Areas, payload.areas);
        }
        var areasNow = readTableObjects_(ss, TABLE_REGISTRY_.Areas);
        var aMap = {};
        for (var am = 0; am < areasNow.length; am++) aMap[areasNow[am].area_id] = areasNow[am];

        if (Array.isArray(payload.parcels) && payload.parcels.length) {
          var curParcels = readTableObjects_(ss, TABLE_REGISTRY_.Parcels);
          var curById = {};
          for (var cp = 0; cp < curParcels.length; cp++) curById[curParcels[cp].parcel_id] = curParcels[cp];
          var safeParcels = [];
          var sweepTs = new Date().toISOString();
          for (var ip = 0; ip < payload.parcels.length; ip++) {
            var incP = payload.parcels[ip];
            if (!incP || !incP.parcel_id) continue;
            var exP = curById[incP.parcel_id];
            if (!exP || isFullSeed) {
              var seeded = {};
              for (var sk in incP) { if (Object.prototype.hasOwnProperty.call(incP, sk)) seeded[sk] = incP[sk]; }
              seeded.added_at = seeded.added_at || sweepTs;
              seeded.updated_at = seeded.updated_at || sweepTs;
              seeded.contributing_status = computeContributingServer_(seeded, aMap);
              seeded.attention_level = computeAttentionServer_(seeded, aMap);
              safeParcels.push(seeded);
            } else {
              // Preserve all human decision columns saved concurrently at T1 (T0 < T1 < T2)!
              var mergedP = {};
              for (var ek in exP) { if (Object.prototype.hasOwnProperty.call(exP, ek)) mergedP[ek] = exP[ek]; }
              for (var mk in PIPELINE_MACHINE_PARCEL_COLS_) {
                if (Object.prototype.hasOwnProperty.call(PIPELINE_MACHINE_PARCEL_COLS_, mk) && incP[mk] !== undefined) {
                  mergedP[mk] = incP[mk];
                }
              }
              if (String(mergedP.advocacy_status || '').toLowerCase() === 'closed' || TERMINAL_OUTCOMES_[String(mergedP.outcome || '').toLowerCase()]) {
                mergedP.threat_stage = 6;
              }
              mergedP.contributing_status = computeContributingServer_(mergedP, aMap);
              mergedP.attention_level = computeAttentionServer_(mergedP, aMap);
              mergedP.updated_at = sweepTs;
              safeParcels.push(mergedP);
            }
          }
          upsertTableObjectsBatch_(ss, TABLE_REGISTRY_.Parcels, safeParcels);
        }

        if (isFullSeed && Array.isArray(payload.decisions) && payload.decisions.length) {
          upsertTableObjectsBatch_(ss, TABLE_REGISTRY_.Decisions, payload.decisions);
        }

        if (Array.isArray(payload.events) && payload.events.length) {
          upsertTableObjectsBatch_(ss, TABLE_REGISTRY_.Events, payload.events);
        }

        if (Array.isArray(payload.watchlistDossiers) && payload.watchlistDossiers.length) {
          var curWlRows = readTableObjects_(ss, TABLE_REGISTRY_.Watchlist_Dossiers);
          var curWlById = {};
          for (var cw = 0; cw < curWlRows.length; cw++) curWlById[curWlRows[cw].watch_property_id] = curWlRows[cw];
          var outWlRows = [];
          for (var iw = 0; iw < payload.watchlistDossiers.length; iw++) {
            var incW = payload.watchlistDossiers[iw];
            var wid2 = String(incW.watch_property_id || incW.id || '').trim();
            if (!wid2) continue;
            var exWRow = curWlById[wid2];
            if (!exWRow || isFullSeed) {
              var fullDoc = incW.dossier_json ? incW : {
                watch_property_id: wid2,
                parcel_id: String(incW.parcelId || incW.hcadAccount || wid2).split(/[;|,]/)[0].trim(),
                hcad_num: String(incW.hcadAccount || '').split(/[;|,]/)[0].trim(),
                primary_address: String(incW.address || ''),
                property_name: String(incW.propertyName || incW.address || wid2),
                attention_level: String(incW.attentionLevel || 'watch'),
                advocacy_status: String(incW.advocacyStatus || 'monitoring'),
                outcome: String(incW.outcome || ''),
                dossier_json: JSON.stringify(incW).slice(0, 48500),
                updated_by: 'Automated Pipeline',
                updated_at: incW.updatedAt || new Date().toISOString()
              };
              outWlRows.push(fullDoc);
            } else {
              // Merge ONLY automated sweep telemetry & [AUTO-SWEEP] checklist entries onto human dossier!
              var existingDocObj = null;
              try { existingDocObj = JSON.parse(exWRow.dossier_json || '{}'); } catch (_we) { existingDocObj = null; }
              var incomingDocObj = incW.dossier_json ? JSON.parse(incW.dossier_json) : incW;
              if (existingDocObj && incomingDocObj && Array.isArray(incomingDocObj.checklist)) {
                existingDocObj.lastAutoSweepAt = incomingDocObj.lastAutoSweepAt || existingDocObj.lastAutoSweepAt || '';
                existingDocObj.autoSweepSummary = incomingDocObj.autoSweepSummary || existingDocObj.autoSweepSummary || null;
                var incByChk = {};
                for (var ic = 0; ic < incomingDocObj.checklist.length; ic++) {
                  var ir = incomingDocObj.checklist[ic];
                  if (ir && ir.id) incByChk[ir.id] = ir;
                }
                var exList = Array.isArray(existingDocObj.checklist) ? existingDocObj.checklist : [];
                for (var ec = 0; ec < exList.length; ec++) {
                  var er = exList[ec];
                  var matchInc = er && er.id ? incByChk[er.id] : null;
                  if (!matchInc) continue;
                  er.lastAutoSweepAt = matchInc.lastAutoSweepAt || er.lastAutoSweepAt || '';
                  if (!er.humanStatusEdited && matchInc.status) er.status = matchInc.status;
                  var humanChecks = (Array.isArray(er.checks) ? er.checks : []).filter(function(c) {
                    return String((c && c.source) || '').indexOf('[AUTO-SWEEP]') === -1 && String((c && c.result) || '').indexOf('[AUTO-SWEEP]') === -1;
                  });
                  var autoChecks = (Array.isArray(matchInc.checks) ? matchInc.checks : []).filter(function(c) {
                    return String((c && c.source) || '').indexOf('[AUTO-SWEEP]') !== -1 || String((c && c.result) || '').indexOf('[AUTO-SWEEP]') !== -1;
                  });
                  er.checks = autoChecks.concat(humanChecks);
                }
                var mergedJsonStr = JSON.stringify(existingDocObj);
                if (mergedJsonStr.length <= 48500) {
                  exWRow.dossier_json = mergedJsonStr;
                  outWlRows.push(exWRow);
                }
              }
            }
          }
          if (outWlRows.length) upsertTableObjectsBatch_(ss, TABLE_REGISTRY_.Watchlist_Dossiers, outWlRows);
        }

        if (isFullSeed && Array.isArray(payload.roles) && payload.roles.length) {
          upsertTableObjectsBatch_(ss, TABLE_REGISTRY_._Monitoring_Roles, payload.roles);
        }

        return createJsonResponse_(env, { ok: true, authorized: true, actorType: pushAuth.actorType });
      }

      if (action === 'list_pending_submissions') {
        var lpAuth = resolveMonitoringAuth_(env, ss, payload);
        if (!lpAuth.authorized || lpAuth.actorType !== 'staff' || !lpAuth.allowedActions.list_pending_submissions) {
          return createJsonResponse_(env, { ok: false, authorized: false, error: 'Moderation queue requires Staff authorization.' });
        }
        var queueItems = listPendingModerationItems_(ss, Number(payload.limit) || 200);
        return createJsonResponse_(env, { ok: true, authorized: true, count: queueItems.length, items: queueItems });
      }

      if (action === 'moderate_submission') {
        var modAuth = resolveMonitoringAuth_(env, ss, payload);
        if (!modAuth.authorized || modAuth.actorType !== 'staff' || !modAuth.allowedActions.moderate_submission) {
          return createJsonResponse_(env, { ok: false, authorized: false, error: 'Moderation actions require Staff authorization.' });
        }
        var sourceTab = String(payload.sourceTab || 'Pending_Submissions').trim();
        var rowNum = Math.floor(Number(payload.rowNumber) || 0);
        var decision = String(payload.decision || '').trim();
        var allowedDecisions = { 'Approved': true, 'Needs Info': true, 'On Hold': true, 'Duplicate': true, 'Rejected': true, 'Resolved': true, 'Dismissed': true };
        if (!allowedDecisions[decision] || rowNum < 2) {
          return createJsonResponse_(env, { ok: false, error: 'Invalid moderation decision or rowNumber.' });
        }
        var staffLabel = modAuth.displayLabel || 'Preservation Staff';

        if (sourceTab === 'Field_Surveys') {
          var fsSheet = ss.getSheetByName('Field_Surveys');
          if (!fsSheet) return createJsonResponse_(env, { ok: false, error: 'Field_Surveys tab not found.' });
          var fsRow = fsSheet.getRange(rowNum, 1, 1, 18).getValues()[0];
          var fsSurveyId = stripLeadingSheetTick_(fsRow[0]).trim();
          if (payload.survey_id && fsSurveyId !== String(payload.survey_id).trim()) {
            return createJsonResponse_(env, { ok: false, conflict: true, error: 'Field_Surveys row shifted; refresh queue and retry.' });
          }
          if (decision === 'Approved') {
            fsSheet.getRange(rowNum, 3, 1, 1).setValues([['Promoted to Override']]);
            var promoted = upsertApprovedPendingRow_(ss, env, {
              hcad_num: payload.hcad_num || stripLeadingSheetTick_(fsRow[3]),
              address: payload.address || stripLeadingSheetTick_(fsRow[4]),
              historic_district: payload.historic_district || stripLeadingSheetTick_(fsRow[5]),
              contributing: payload.contributing || stripLeadingSheetTick_(fsRow[8]),
              original_hcad_year: payload.original_hcad_year || stripLeadingSheetTick_(fsRow[12]),
              suggested_year_built: payload.suggested_year_built !== undefined ? payload.suggested_year_built : stripLeadingSheetTick_(fsRow[13]),
              suppress_only: payload.suppress_only === true,
              source_type: 'Preservation Houston Field Survey',
              source_citation: payload.source_citation || ('[Promoted Survey ' + fsSurveyId + '] ' + stripLeadingSheetTick_(fsRow[15])),
              source_url: payload.source_url || stripLeadingSheetTick_(fsRow[14]),
              submitter_name: stripLeadingSheetTick_(fsRow[16]) || staffLabel,
              building_name: payload.building_name || '',
              bld_style: payload.bld_style || '',
              architect: payload.architect || '',
              moderation_note: payload.moderation_note || ('Promoted from Field_Surveys ' + fsSurveyId)
            }, staffLabel);
            if (!promoted.ok) return createJsonResponse_(env, promoted);
            return createJsonResponse_(env, { ok: true, sourceTab: 'Field_Surveys', status: 'Promoted to Override', promotedOverride: promoted });
          }
          var mappedSurveySt = decision === 'Rejected' ? 'Dismissed' : (decision === 'Needs Info' ? 'Needs Follow-Up' : decision);
          fsSheet.getRange(rowNum, 3, 1, 1).setValues([[mappedSurveySt]]);
          return createJsonResponse_(env, { ok: true, sourceTab: 'Field_Surveys', status: mappedSurveySt, rowNumber: rowNum });
        }

        var pendSheet = ss.getSheetByName('Pending_Submissions');
        if (!pendSheet) return createJsonResponse_(env, { ok: false, error: 'Pending_Submissions tab not found.' });
        var pRow = pendSheet.getRange(rowNum, 1, 1, 23).getValues()[0];
        var existingHcad = stripLeadingSheetTick_(pRow[1]).trim();
        if (payload.expected_hcad_num && existingHcad !== stripLeadingSheetTick_(payload.expected_hcad_num).trim()) {
          return createJsonResponse_(env, { ok: false, conflict: true, error: 'Pending_Submissions row shifted; refresh queue and retry.' });
        }
        var srcTypeRow = String(payload.source_type || stripLeadingSheetTick_(pRow[7]) || '').trim();
        var isDisputeFlag = /(?:PHOTO_MISATTRIBUTION_FLAG|CLAIM_DISPUTE_FLAG|NARRATIVE_ERROR_FLAG)$/i.test(srcTypeRow);
        var nextYrRaw = payload.suggested_year_built !== undefined ? String(payload.suggested_year_built).trim() : stripLeadingSheetTick_(pRow[3]).trim();
        var hasExplicitPromoteYr = nextYrRaw !== '' && isFinite(Number(nextYrRaw)) && Number(nextYrRaw) >= 1836;
        var effectiveStatus = (decision === 'Approved' && isDisputeFlag && !hasExplicitPromoteYr && payload.suppress_only !== true) ? 'Resolved' : decision;

        var isVacMod = payload.suppress_only === true || nextYrRaw === '0';
        var nextCite = payload.source_citation !== undefined ? String(payload.source_citation) : stripLeadingSheetTick_(pRow[8]);
        var nextGeomW = payload.geometry_geojson !== undefined ? String(payload.geometry_geojson) : stripLeadingSheetTick_(pRow[22] || '');
        if (isVacMod) {
          nextGeomW = '';
          nextCite = stripTaggedJsonSpanServer_(nextCite, '[GEOMETRY_GEOJSON:');
        }
        var updatedColsAtoR = [
          effectiveStatus,
          /^\d{13}$/.test(existingHcad) ? (SQUOTE_ + existingHcad) : sanitizeSheetFormulaPrefix_(payload.hcad_num || existingHcad),
          sanitizeSheetFormulaPrefix_(payload.address !== undefined ? payload.address : stripLeadingSheetTick_(pRow[2])),
          isVacMod ? 0 : (hasExplicitPromoteYr ? Math.round(Number(nextYrRaw)) : (isDisputeFlag ? '' : stripLeadingSheetTick_(pRow[3]))),
          stripLeadingSheetTick_(pRow[4]),
          sanitizeSheetFormulaPrefix_(payload.historic_district !== undefined ? payload.historic_district : stripLeadingSheetTick_(pRow[5])),
          sanitizeSheetFormulaPrefix_(payload.contributing !== undefined ? payload.contributing : stripLeadingSheetTick_(pRow[6])),
          sanitizeSheetFormulaPrefix_(srcTypeRow),
          sanitizeSheetFormulaPrefix_(nextCite),
          sanitizeSheetFormulaPrefix_(payload.source_url !== undefined ? payload.source_url : stripLeadingSheetTick_(pRow[9])),
          sanitizeSheetFormulaPrefix_(staffLabel),
          sanitizeSheetFormulaPrefix_(stripLeadingSheetTick_(pRow[11])),
          stripLeadingSheetTick_(pRow[12]) || new Date().toISOString().slice(0, 10),
          sanitizeSheetFormulaPrefix_(payload.moderation_note || stripLeadingSheetTick_(pRow[13]) || ('Moderated via In-App Queue: ' + effectiveStatus)),
          sanitizeSheetFormulaPrefix_(payload.building_name !== undefined ? payload.building_name : stripLeadingSheetTick_(pRow[14])),
          sanitizeSheetFormulaPrefix_(payload.alt_names !== undefined ? payload.alt_names : stripLeadingSheetTick_(pRow[15])),
          sanitizeSheetFormulaPrefix_(payload.bld_style !== undefined ? payload.bld_style : stripLeadingSheetTick_(pRow[16])),
          sanitizeSheetFormulaPrefix_(payload.architect !== undefined ? payload.architect : stripLeadingSheetTick_(pRow[17]))
        ];
        writePendingRowPreservingFormulas_(pendSheet, rowNum, updatedColsAtoR, nextGeomW);
        invalidateCacheKey_(env, 'APPROVED_EDITS_CSV_V1');
        return createJsonResponse_(env, { ok: true, sourceTab: 'Pending_Submissions', status: effectiveStatus, rowNumber: rowNum });
      }

      var rawSourceType = String(payload.source_type || 'Houston City Directory').trim();

      // Walk-the-Block Field Survey intake (unauthenticated/offline-tolerant community & volunteer survey intake)
      if (action === 'field_survey' || rawSourceType.toUpperCase() === 'FIELD_SURVEY_OBSERVATION') {
        var surveySheet = ss.getSheetByName('Field_Surveys') || ss.insertSheet('Field_Surveys');
        var envObj = extractBalancedTagJson_(payload.source_citation || '', '[FIELD_SURVEY_JSON:') || {};
        var surveyId = String(payload.survey_id || envObj.survey_id || '').trim();
        if (!surveyId) {
          surveyId = 'SURV_' + new Date().getTime() + '_' + Math.floor(Math.random() * 100000);
        }
        var rawSurveyHcad = String(payload.hcad_num || envObj.hcad_num || '')
          .replace(/[\r\n\t]+/g, ' ')
          .trim()
          .replace(/^'+/, '')
          .replace(/^[=+\-@]+/, '');
        var surveyAt = String(payload.surveyed_at || envObj.surveyed_at || payload.submitted_at || new Date().toISOString().slice(0, 19).replace('T', ' ')).trim();
        var atlasYrRaw = payload.atlas_year_built !== undefined ? payload.atlas_year_built : (envObj.atlas_year_built !== undefined ? envObj.atlas_year_built : payload.hcad_year_built);
        var atlasYrVal = (atlasYrRaw !== undefined && atlasYrRaw !== null && String(atlasYrRaw).trim() !== '' && Number(atlasYrRaw) > 0) ? Math.round(Number(atlasYrRaw)) : '';
        var safeSurveyId = sanitizeSheetFormulaPrefix_(surveyId);
        var surveyRow = [
          safeSurveyId,
          sanitizeSheetFormulaPrefix_(surveyAt),
          'Unreviewed',
          rawSurveyHcad ? (SQUOTE_ + rawSurveyHcad) : '',
          sanitizeSheetFormulaPrefix_(payload.address || envObj.address || ''),
          sanitizeSheetFormulaPrefix_(payload.district_or_corridor || envObj.district_or_corridor || payload.historic_district || ''),
          sanitizeSheetFormulaPrefix_(payload.integrity_grade || envObj.integrity_grade || ''),
          sanitizeSheetFormulaPrefix_(payload.condition_rating || envObj.condition_rating || ''),
          sanitizeSheetFormulaPrefix_(payload.suggested_contributing || envObj.suggested_contributing || ''),
          sanitizeSheetFormulaPrefix_(payload.windows_status || envObj.windows_status || ''),
          sanitizeSheetFormulaPrefix_(payload.cladding_material || envObj.cladding_material || ''),
          sanitizeSheetFormulaPrefix_(payload.porch_integrity || envObj.porch_integrity || ''),
          atlasYrVal,
          sanitizeSheetFormulaPrefix_(payload.observed_year_or_demo_note || envObj.observed_year_or_demo_note || ''),
          sanitizeSheetFormulaPrefix_(payload.photo_url || envObj.photo_url || ''),
          sanitizeSheetFormulaPrefix_(payload.field_notes || envObj.field_notes || ''),
          sanitizeSheetFormulaPrefix_(payload.surveyor_name || envObj.surveyor_name || payload.submitter_name || ''),
          sanitizeSheetFormulaPrefix_(payload.gps_lat_lng_acc || envObj.gps_lat_lng_acc || '')
        ];
        var allowedReviewStatuses = {
          'Unreviewed': true, 'Verified': true, 'Promoted to Override': true,
          'Needs Follow-Up': true, 'Duplicate': true, 'Archived': true, 'Dismissed': true
        };
        var colA = surveySheet.getRange('A:A').getValues();
        var targetSurveyRow = 0;
        var lastSurveyRow = 1;
        for (var sr = 1; sr < colA.length; sr++) {
          var existingId = stripLeadingSheetTick_(colA[sr][0]).trim();
          if (existingId !== '') {
            lastSurveyRow = sr + 1;
            if (existingId === safeSurveyId || existingId === surveyId) {
              targetSurveyRow = sr + 1;
              break;
            }
          }
        }
        var writeSurveyRow = targetSurveyRow > 0 ? targetSurveyRow : (lastSurveyRow + 1);
        if (targetSurveyRow > 0) {
          var revCell = surveySheet.getRange(targetSurveyRow, 3);
          var existingReview = (typeof revCell.getFormula === 'function' && revCell.getFormula()) ? '' : String((typeof revCell.getDisplayValue === 'function' ? revCell.getDisplayValue() : revCell.getValue()) || '').trim();
          if (existingReview && existingReview !== 'Unreviewed' && allowedReviewStatuses[existingReview] === true) {
            surveyRow[2] = existingReview;
          }
        }
        surveySheet.getRange(writeSurveyRow, 1, 1, surveyRow.length).setValues([surveyRow]);
        return createJsonResponse_(env, {
          ok: true,
          status: surveyRow[2],
          routed_to: 'Field_Surveys',
          survey_id: safeSurveyId,
          upserted: targetSurveyRow > 0
        });
      }

      // Direct Staff `admin_approve` (1-click Approved_Edits writeback & Staff Polygon Editor)
      if (action === 'admin_approve') {
        var admAuth = resolveMonitoringAuth_(env, ss, payload);
        if (!admAuth.authorized || admAuth.actorType !== 'staff' || !admAuth.allowedActions.admin_approve) {
          return createJsonResponse_(env, { ok: false, authorized: false, error: admAuth.error || 'Staff authorization required to publish Approved_Edits overrides.' });
        }
        var writeRes = upsertApprovedPendingRow_(ss, env, payload, admAuth.displayLabel || 'Preservation Staff');
        if (!writeRes.ok) return createJsonResponse_(env, writeRes);
        return createJsonResponse_(env, {
          ok: true,
          authorized: true,
          status: writeRes.status,
          upserted: writeRes.upserted,
          rowNumber: writeRes.rowNumber
        });
      }

      // Public Community Suggestion Intake -> Pending_Submissions (`status = Pending`, split-range safe)
      var rawGeomValPub = payload.geometry_geojson;
      var geomPubStr = '';
      if (rawGeomValPub && typeof rawGeomValPub === 'object') geomPubStr = JSON.stringify(rawGeomValPub);
      else if (typeof rawGeomValPub === 'string' && rawGeomValPub.trim() !== '') geomPubStr = rawGeomValPub.trim();
      if (geomPubStr.length > 49000) {
        return createJsonResponse_(env, { ok: false, error: 'geometry_geojson exceeds Google Sheets 49,000-character limit.' });
      }
      var pubSheet = ss.getSheetByName('Pending_Submissions') || ss.insertSheet('Pending_Submissions');
      var rawBldPub = String(payload.building_name || '').trim();
      var pubParts = rawBldPub ? rawBldPub.split(/[;|]/).map(function(s) { return s.trim(); }).filter(Boolean) : [];
      var pubPrimary = pubParts.length > 0 ? pubParts[0] : '';
      var pubAlt = pubParts.length > 1 ? pubParts.slice(1).join('; ') : String(payload.alt_names || '').trim();
      var isNonYrPub = /(?:_FLAG|FIELD_SURVEY_OBSERVATION)$/i.test(rawSourceType);
      var rawSugPub = payload.suggested_year_built !== undefined && payload.suggested_year_built !== null && String(payload.suggested_year_built).trim() !== ''
        ? String(payload.suggested_year_built).trim()
        : (payload.year_built !== undefined && payload.year_built !== null ? String(payload.year_built).trim() : '');
      var parsedPubYr = (rawSugPub !== '' && isFinite(Number(rawSugPub))) ? Math.round(Number(rawSugPub)) : NaN;
      var validPubYr = isFinite(parsedPubYr) && parsedPubYr >= 1836 && parsedPubYr <= 2035;
      var isVacPub = !isNonYrPub && !geomPubStr && (payload.suppress_only === true || rawSugPub === '0' || /^vacant(\s+lot)?$|^demolished$/i.test(rawSugPub));
      var pubSheetYr = isNonYrPub ? '' : (isVacPub ? 0 : (validPubYr ? parsedPubYr : ''));
      var rawPubHcad = stripLeadingSheetTick_(payload.hcad_num || '').trim();
      var pubColsAtoR = [
        'Pending',
        /^\d{13}$/.test(rawPubHcad) ? (SQUOTE_ + rawPubHcad) : sanitizeSheetFormulaPrefix_(rawPubHcad),
        sanitizeSheetFormulaPrefix_(payload.address || ''),
        pubSheetYr,
        Number(payload.hcad_year_built || payload.original_hcad_year || 0) || '',
        sanitizeSheetFormulaPrefix_(payload.historic_district || ''),
        sanitizeSheetFormulaPrefix_(payload.contributing || ''),
        sanitizeSheetFormulaPrefix_(rawSourceType),
        sanitizeSheetFormulaPrefix_(payload.source_citation || ''),
        sanitizeSheetFormulaPrefix_(payload.source_url || ''),
        '',
        sanitizeSheetFormulaPrefix_(payload.submitter_name || payload.submitted_by || 'Community Member'),
        new Date().toISOString().slice(0, 10),
        'Community submission via Houston Building Atlas',
        sanitizeSheetFormulaPrefix_(pubPrimary),
        sanitizeSheetFormulaPrefix_(pubAlt),
        sanitizeSheetFormulaPrefix_(payload.bld_style || ''),
        sanitizeSheetFormulaPrefix_(payload.architect || '')
      ];
      var colBPub = pubSheet.getRange('B:B').getValues();
      var lastPubRow = 1;
      for (var pr = colBPub.length - 1; pr >= 0; pr--) {
        if (stripLeadingSheetTick_(colBPub[pr][0]).trim() !== '') {
          lastPubRow = pr + 1;
          break;
        }
      }
      writePendingRowPreservingFormulas_(pubSheet, lastPubRow + 1, pubColsAtoR, geomPubStr);
      return createJsonResponse_(env, { ok: true, status: 'Pending', authorized: false });
    } finally {
      if (lock && typeof lock.releaseLock === 'function') {
        lock.releaseLock();
      }
    }
  }

  return {
    doGet: handleDoGet,
    doPost: handleDoPost,
    mergeUserRoamingStateServer_: mergeUserRoamingStateServer_,
    resolveMonitoringAuth_: resolveMonitoringAuth_,
    readTableObjects_: readTableObjects_,
    upsertTableObjectsBatch_: upsertTableObjectsBatch_
  };
}

function doGet(e) {
  return createAppsScriptController_(defaultHostEnv_()).doGet(e);
}

function doPost(e) {
  return createAppsScriptController_(defaultHostEnv_()).doPost(e);
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    TABLE_REGISTRY: TABLE_REGISTRY_,
    createAppsScriptController: createAppsScriptController_,
    mergeUserRoamingStateServer_: mergeUserRoamingStateServer_,
    doGet: doGet,
    doPost: doPost
  };
}
