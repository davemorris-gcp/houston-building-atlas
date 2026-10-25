#!/usr/bin/env node
/**
 * Layer 3: Live External Archive & Municipal Link Health Monitor
 *
 * Crawls all external HTTP(S) links harvested from the 45-Archetype Playwright E2E suite
 * (test-results/harvested_external_urls.json) plus authoritative external links from
 * app/index.html, app/plat-monitor.html, and app/public/data/*.json.
 *
 * Produces:
 *   - test-results/external_link_health_report.json
 *   - test-results/external_link_health_report.md (formatted for the canonical GitHub Issue)
 *
 * Flags:
 *   --report-only         Always exit 0 after writing reports (used by CI step prior to gh issue edit)
 *   --max-per-domain=N    Cap non-harvested catalog URLs per domain (default: 25; harvested UI links are always checked)
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  BROWSER_CHALLENGE_PROTECTED_HOSTS,
  extractHttpUrlsFromText,
  validateExternalAtlasUrl,
} from "../app/js/urlValidators.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "..");
const APP_DIR = path.join(REPO_ROOT, "app");
const DATA_DIR = path.join(APP_DIR, "public", "data");
const RESULTS_DIR = path.join(REPO_ROOT, "test-results");
const HARVESTED_JSON = path.join(RESULTS_DIR, "harvested_external_urls.json");
const REPORT_JSON = path.join(RESULTS_DIR, "external_link_health_report.json");
const REPORT_MD = path.join(RESULTS_DIR, "external_link_health_report.md");

const USER_AGENT =
  "PreservationHouston-Atlas-LinkHealth/1.0 (+https://davemorris-gcp.github.io/houston-building-atlas/)";

const SOFT_404_URL_RE = /\/(?:Maintenance|Error|PageNotFound|404)\.aspx|(?:[/?&]|^)404(?:[/?&#]|$)/i;
const SOFT_404_TITLE_RE =
  /<title[^>]*>[^<]*(?:404\s+Not\s+Found|Page\s+Not\s+Found|Document\s+Not\s+Found|File\s+or\s+directory\s+not\s+found)[^<]*<\/title>/i;

function parseArgs(argv) {
  let reportOnly = false;
  let maxCatalogPerDomain = 25;
  for (const arg of argv.slice(2)) {
    if (arg === "--report-only") {
      reportOnly = true;
    } else if (arg.startsWith("--max-per-domain=")) {
      const n = parseInt(arg.split("=")[1], 10);
      if (Number.isFinite(n) && n > 0) maxCatalogPerDomain = n;
    }
  }
  return { reportOnly, maxCatalogPerDomain };
}

function collectCandidateUrls(maxCatalogPerDomain) {
  /** @type {Map<string, { url: string, priority: "harvested" | "catalog", sources: Set<string> }>} */
  const registry = new Map();

  function addUrl(rawUrl, source, priority = "catalog") {
    const clean = String(rawUrl || "").trim();
    if (!clean.startsWith("http://") && !clean.startsWith("https://")) return;
    if (
      clean.startsWith("http://127.0.0.1") ||
      clean.startsWith("http://localhost") ||
      clean.includes("fonts.googleapis.com") ||
      clean.includes("fonts.gstatic.com") ||
      clean.includes("unpkg.com")
    ) {
      return;
    }
    const existing = registry.get(clean);
    if (!existing) {
      registry.set(clean, {
        url: clean,
        priority,
        sources: new Set([source]),
      });
    } else {
      if (priority === "harvested") existing.priority = "harvested";
      if (existing.sources.size < 8) existing.sources.add(source);
    }
  }

  // 1. All Playwright-harvested DOM links (highest priority: always checked)
  if (fs.existsSync(HARVESTED_JSON)) {
    const harvested = JSON.parse(fs.readFileSync(HARVESTED_JSON, "utf8"));
    for (const item of harvested.urls || []) {
      const srcs = Array.isArray(item.sources) && item.sources.length ? item.sources : [item.firstSeenIn || "e2e"];
      for (const s of srcs) {
        addUrl(item.url, `e2e:${s}`, "harvested");
      }
    }
  }

  // 2. Static HTML <a href="http..."> hyperlinks (excluding <link rel="preconnect"> and <input placeholder>)
  const anchorHrefRe = /<a\b[^>]*?\bhref=["'](https?:\/\/[^"']+)["']/gi;
  for (const relFile of ["app/index.html", "app/plat-monitor.html"]) {
    const full = path.join(REPO_ROOT, relFile);
    const html = fs.readFileSync(full, "utf8");
    let m;
    while ((m = anchorHrefRe.exec(html)) !== null) {
      if (!m[1].includes("${")) {
        addUrl(m[1], relFile, "harvested");
      }
    }
  }

  // 3. Authoritative JSON catalogs (capped per domain so 1,000+ identical-template PDFs don't DoS public archives)
  const catalogDomainCounts = new Map();
  const catalogFiles = [
    "hahc_coa_tracker.json",
    "active_permit_alerts.json",
    "plat_agenda_monitor.json",
    "property_watchlist.json",
    "deed_restrictions_catalog.json",
    "sanborn_catalog.json",
    "citydir_index.json",
    "overlays.json",
  ];

  for (const filename of catalogFiles) {
    const full = path.join(DATA_DIR, filename);
    if (!fs.existsSync(full)) continue;
    const urls = extractHttpUrlsFromText(fs.readFileSync(full, "utf8"));
    for (const u of urls) {
      if (registry.has(u)) {
        addUrl(u, `data/${filename}`, "catalog");
        continue;
      }
      let host = "";
      try {
        host = new URL(u).hostname.toLowerCase();
      } catch (_e) {
        addUrl(u, `data/${filename}`, "harvested");
        continue;
      }
      const seenForHost = catalogDomainCounts.get(host) || 0;
      if (seenForHost < maxCatalogPerDomain) {
        catalogDomainCounts.set(host, seenForHost + 1);
        addUrl(u, `data/${filename}`, "catalog");
      }
    }
  }

  return Array.from(registry.values()).map((it) => ({
    url: it.url,
    priority: it.priority,
    sources: Array.from(it.sources),
  }));
}

async function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function fetchOnce(url, method, timeoutMs = 16000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const headers = {
      "User-Agent": USER_AGENT,
      Accept: "text/html,application/pdf,application/xhtml+xml,image/*,*/*;q=0.8",
    };
    if (method === "GET") {
      headers.Range = "bytes=0-8191";
    }
    const resp = await fetch(url, {
      method,
      headers,
      redirect: "follow",
      signal: controller.signal,
    });
    let snippet = "";
    const ctype = (resp.headers.get("content-type") || "").toLowerCase();
    if (method === "GET" && ctype.includes("text/html")) {
      const buf = await resp.arrayBuffer();
      snippet = Buffer.from(buf).toString("utf8", 0, Math.min(buf.byteLength, 8192));
    } else if (resp.body) {
      try {
        await resp.body.cancel();
      } catch (_e) {
        // ignore stream cancel errors
      }
    }
    return {
      ok: resp.ok || resp.status === 206,
      status: resp.status,
      finalUrl: resp.url || url,
      contentType: ctype,
      snippet,
      error: null,
    };
  } catch (err) {
    return {
      ok: false,
      status: 0,
      finalUrl: url,
      contentType: "",
      snippet: "",
      error: err?.name === "AbortError" ? "Timeout (16s)" : String(err?.message || err),
    };
  } finally {
    clearTimeout(timer);
  }
}

const hostTcpDropCount = new Map();

async function probeUrl(entry) {
  const { url } = entry;
  const patternCheck = validateExternalAtlasUrl(url);
  if (!patternCheck.valid) {
    return {
      ...entry,
      host: patternCheck.host || "invalid-url",
      category: "BROKEN",
      status: 0,
      finalUrl: url,
      detail: patternCheck.reason || "Failed deterministic URL validation",
    };
  }

  const host = patternCheck.host;
  const isWafHost = BROWSER_CHALLENGE_PROTECTED_HOSTS.has(host) || host.endsWith(".texas.gov");

  if (isWafHost && (hostTcpDropCount.get(host) || 0) >= 2) {
    return {
      ...entry,
      host,
      category: "WAF_PROTECTED",
      status: 0,
      finalUrl: url,
      detail: "Browser JS/WAF or datacenter firewall challenge (TCP drop fast-path); URL syntax verified via urlValidators.js",
    };
  }

  let res = await fetchOnce(url, "HEAD", isWafHost ? 8000 : 14000);
  if (isWafHost && res.status === 0) {
    hostTcpDropCount.set(host, (hostTcpDropCount.get(host) || 0) + 1);
    return {
      ...entry,
      host,
      category: "WAF_PROTECTED",
      status: 0,
      finalUrl: url,
      detail: "Browser JS/WAF or datacenter firewall challenge (timeout); URL syntax verified via urlValidators.js",
    };
  }
  // Fall back to GET when HEAD is unsupported/blocked or when checking HTML soft-404s
  if (
    !res.ok ||
    res.status === 405 ||
    res.status === 403 ||
    res.status === 400 ||
    res.status === 501 ||
    res.contentType.includes("text/html")
  ) {
    res = await fetchOnce(url, "GET", isWafHost ? 10000 : 18000);
  }

  // One retry on transient network timeout or 5xx (skip redundant retry on known WAF TCP drops)
  if ((res.status === 0 && !isWafHost) || res.status >= 500) {
    await sleep(1400);
    res = await fetchOnce(url, "GET", 16000);
  }
  if (isWafHost && res.status === 0) {
    hostTcpDropCount.set(host, (hostTcpDropCount.get(host) || 0) + 1);
  }

  // Soft-404 checks on final URL and HTML <title>
  if (res.ok) {
    if (SOFT_404_URL_RE.test(res.finalUrl)) {
      return {
        ...entry,
        host,
        category: "BROKEN",
        status: res.status,
        finalUrl: res.finalUrl,
        detail: `Soft-404 redirect to maintenance/error page: ${res.finalUrl}`,
      };
    }
    if (res.snippet && SOFT_404_TITLE_RE.test(res.snippet)) {
      return {
        ...entry,
        host,
        category: "BROKEN",
        status: res.status,
        finalUrl: res.finalUrl,
        detail: "Soft-404 HTML page title ('Page Not Found' / '404 Not Found')",
      };
    }
    return {
      ...entry,
      host,
      category: "OK",
      status: res.status,
      finalUrl: res.finalUrl,
      detail: `HTTP ${res.status}`,
    };
  }

  // Restricted Staff Admin Google Docs / Sheets (intentionally restricted to Editors; HTTP 401/403 expected without auth)
  if (
    host === "docs.google.com" &&
    (res.status === 401 || res.status === 403) &&
    !url.includes("/pub?")
  ) {
    return {
      ...entry,
      host,
      category: "WAF_PROTECTED",
      status: res.status,
      finalUrl: res.finalUrl,
      detail: `Restricted Staff Google Workspace document (HTTP ${res.status} expected without editor session)`,
    };
  }

  // Browser-challenge WAF protected portals (Cloudflare / Anubis JS PoW / Struts JSESSIONID / Texas DIR datacenter firewall)
  // Note: HTTP 404 is NEVER exempted here, so moved/deleted .texas.gov or archive paths are still caught when reachable.
  if (
    (isWafHost || host.endsWith(".texas.gov")) &&
    (res.status === 403 || res.status === 500 || res.status === 503 || res.status === 429 || res.status === 0)
  ) {
    return {
      ...entry,
      host,
      category: "WAF_PROTECTED",
      status: res.status,
      finalUrl: res.finalUrl,
      detail: `Browser JS/WAF or datacenter firewall challenge (HTTP ${res.status || "timeout"}); URL syntax verified via urlValidators.js`,
    };
  }

  return {
    ...entry,
    host,
    category: "BROKEN",
    status: res.status,
    finalUrl: res.finalUrl,
    detail: res.error ? `Network error: ${res.error}` : `HTTP ${res.status}`,
  };
}

async function runPoolByHost(entries, globalConcurrency = 10) {
  // Interleave across hosts so no single archive receives burst traffic
  const byHost = new Map();
  for (const e of entries) {
    let h = "unknown";
    try {
      h = new URL(e.url).hostname.toLowerCase();
    } catch (_err) {
      // keep unknown
    }
    if (!byHost.has(h)) byHost.set(h, []);
    byHost.get(h).push(e);
  }

  const interleaved = [];
  let added = true;
  while (added) {
    added = false;
    for (const queue of byHost.values()) {
      if (queue.length > 0) {
        interleaved.push(queue.shift());
        added = true;
      }
    }
  }

  const results = new Array(interleaved.length);
  const hostQueues = new Map();
  let cursor = 0;

  async function runSerializedForHost(host, fn) {
    const minSpacingMs = host === "web.archive.org" ? 650 : 60;
    const prevChain = hostQueues.get(host) || Promise.resolve(0);
    let release;
    const currentGate = new Promise((resolve) => {
      release = resolve;
    });
    hostQueues.set(host, currentGate);
    const lastFinishedAt = await prevChain;
    const elapsed = Date.now() - lastFinishedAt;
    if (elapsed < minSpacingMs) {
      await sleep(minSpacingMs - elapsed);
    }
    try {
      return await fn();
    } finally {
      release(Date.now());
    }
  }

  async function worker() {
    while (true) {
      const idx = cursor++;
      if (idx >= interleaved.length) break;
      const item = interleaved[idx];
      let host = "unknown";
      try {
        host = new URL(item.url).hostname.toLowerCase();
      } catch (_e) {
        // ignore
      }
      results[idx] = await runSerializedForHost(host, () => probeUrl(item));
    }
  }

  const workers = Array.from({ length: Math.min(globalConcurrency, interleaved.length || 1) }, () => worker());
  await Promise.all(workers);
  return results;
}

function buildMarkdownReport(summary, results) {
  const lines = [];
  const statusBanner =
    summary.brokenCount === 0
      ? "ALL VERIFIED ARCHIVE & MUNICIPAL LINKS HEALTHY (0 BROKEN LINKS)"
      : `ACTION REQUIRED: ${summary.brokenCount} BROKEN OR MOVED EXTERNAL LINK(S) DETECTED`;

  lines.push("# External Archive & Municipal Link Health Report");
  lines.push("");
  lines.push(`- **Overall Status:** \`${statusBanner}\``);
  lines.push(`- **Last Automated Crawl:** \`${summary.checkedAtUtc}\``);
  lines.push(
    `- **Live Production Atlas:** [The Houston Building Atlas](https://davemorris-gcp.github.io/houston-building-atlas/)`
  );
  lines.push(
    `- **Workflow Schedule:** Automated every Monday at 13:00 UTC (8:00 AM Central) and via manual \`workflow_dispatch\` in \`.github/workflows/external-link-health.yml\`.`
  );
  lines.push("");
  lines.push("## Summary Metrics");
  lines.push("");
  lines.push("| Metric | Count | Notes |");
  lines.push("| :--- | ---: | :--- |");
  lines.push(
    `| **Total Unique External URLs Checked** | **${summary.totalChecked}** | Harvested from 45 Stratified Playwright UI Archetypes + static catalogs |`
  );
  lines.push(
    `| **Verified Live (\`HTTP 200 / 206\`)** | **${summary.okCount}** | Direct 200/206 response with zero soft-404 redirects |`
  );
  lines.push(
    `| **Browser-WAF Protected Portals** | **${summary.wafCount}** | Cloudflare / Anubis JS challenge hosts (syntax verified via \`urlValidators.js\`) |`
  );
  lines.push(
    `| **Broken / 404 / Soft-404 Links** | **${summary.brokenCount}** | Municipal 404s, \`Maintenance.aspx\` soft-404s, DNS failures, or retired paths |`
  );
  lines.push("");

  if (summary.brokenCount > 0) {
    lines.push("## Broken or Moved Links Requiring Remediation");
    lines.push("");
    lines.push("| Status | URL | Referenced In | Failure Detail |");
    lines.push("| :--- | :--- | :--- | :--- |");
    for (const r of results.filter((x) => x.category === "BROKEN")) {
      lines.push(
        `| \`${r.status || "ERR"}\` | ${r.url} | \`${r.sources.slice(0, 3).join(", ")}\` | ${r.detail.replace(/\|/g, "/")} |`
      );
    }
    lines.push("");
  } else {
    lines.push("## Broken Links");
    lines.push("");
    lines.push(
      "Zero broken, HTTP 404, or soft-404 links were detected across any municipal, county, state, university, or archival portal."
    );
    lines.push("");
  }

  lines.push("## Per-Domain Archive Health Breakdown");
  lines.push("");
  lines.push("| External Domain | Total Checked | Live 200/206 | Browser WAF | Broken |");
  lines.push("| :--- | ---: | ---: | ---: | ---: |");
  for (const d of summary.domains) {
    lines.push(`| \`${d.host}\` | ${d.total} | ${d.ok} | ${d.waf} | ${d.broken} |`);
  }
  lines.push("");

  lines.push("## How Preservation Houston Staff & Maintainers Use This Report");
  lines.push("");
  lines.push(
    "1. **When a City of Houston, HCAD, Harris County Clerk, THC, HPL, or UT Austin link moves:** the weekly Monday crawl adds it to the **Broken or Moved Links** table above with the exact source dataset or UI component (`e2e:c01_plm_1`, `data/hahc_coa_tracker.json`, etc.)."
  );
  lines.push(
    "2. **Fixing a moved URL:** update the URL template in `app/js/` or catalog entry in `app/public/data/`, add the retired path pattern to `KNOWN_DEAD_URL_PATTERNS` in `app/js/urlValidators.js` so it can never regress, and trigger the **External Archive Link Health Monitor** workflow in GitHub Actions to confirm `0` broken links."
  );
  lines.push("");

  return lines.join("\n").replace(/\u2014/g, " - ");
}

async function main() {
  const { reportOnly, maxCatalogPerDomain } = parseArgs(process.argv);
  fs.mkdirSync(RESULTS_DIR, { recursive: true });

  const candidates = collectCandidateUrls(maxCatalogPerDomain);
  console.log(
    `Checking ${candidates.length} unique external URLs across harvested UI archetypes & catalogs (maxCatalogPerDomain=${maxCatalogPerDomain})...`
  );

  const results = await runPoolByHost(candidates, 10);

  const domainMap = new Map();
  let okCount = 0;
  let wafCount = 0;
  let brokenCount = 0;

  for (const r of results) {
    if (r.category === "OK") okCount += 1;
    else if (r.category === "WAF_PROTECTED") wafCount += 1;
    else brokenCount += 1;

    const d = domainMap.get(r.host) || { host: r.host, total: 0, ok: 0, waf: 0, broken: 0 };
    d.total += 1;
    if (r.category === "OK") d.ok += 1;
    else if (r.category === "WAF_PROTECTED") d.waf += 1;
    else d.broken += 1;
    domainMap.set(r.host, d);
  }

  const nowIso = new Date().toISOString().replace(/\.\d{3}Z$/, " UTC").replace("T", " ");
  const domains = Array.from(domainMap.values()).sort((a, b) => b.broken - a.broken || b.total - a.total);
  const summary = {
    checkedAtUtc: nowIso,
    totalChecked: results.length,
    okCount,
    wafCount,
    brokenCount,
    domains,
  };

  fs.writeFileSync(
    REPORT_JSON,
    JSON.stringify({ summary, results }, null, 2).replace(/\u2014/g, " - ") + "\n",
    "utf8"
  );
  fs.writeFileSync(REPORT_MD, buildMarkdownReport(summary, results) + "\n", "utf8");

  console.log(
    `External link health summary: total=${summary.totalChecked}, ok=${okCount}, wafProtected=${wafCount}, broken=${brokenCount}`
  );
  if (brokenCount > 0) {
    console.log("Broken links detected:");
    for (const b of results.filter((x) => x.category === "BROKEN")) {
      console.log(`  - [${b.status || "ERR"}] ${b.url} (${b.detail}) <- ${b.sources.join(", ")}`);
    }
  }

  if (brokenCount > 0 && !reportOnly) {
    process.exit(1);
  }
}

main();
