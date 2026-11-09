import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { HoustonAtlasApp } from "../../app/js/app.js";
import { ORGANIZER_CACHE_BUST } from "../../app/js/preservationOrganizer.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "../..");
const APP_DIR = path.join(REPO_ROOT, "app");
const DATA_DIR = path.join(APP_DIR, "public", "data");

function makeClassListStub(initial = []) {
  const set = new Set(initial);
  return {
    add: (...tokens) => tokens.forEach((t) => set.add(t)),
    remove: (...tokens) => tokens.forEach((t) => set.delete(t)),
    contains: (t) => set.has(t),
    toggle: (t, force) => {
      const next = force === undefined ? !set.has(t) : Boolean(force);
      if (next) set.add(t);
      else set.delete(t);
      return next;
    },
    toArray: () => Array.from(set),
  };
}

test("UT-M11-1: app/index.html and app/plat-monitor.html declare viewport-fit=cover in meta viewport", () => {
  const indexHtml = fs.readFileSync(path.join(APP_DIR, "index.html"), "utf8");
  const platHtml = fs.readFileSync(path.join(APP_DIR, "plat-monitor.html"), "utf8");

  const indexViewportMatch = indexHtml.match(/<meta\s+name=["']viewport["']\s+content=["']([^"']+)["']/i);
  const platViewportMatch = platHtml.match(/<meta\s+name=["']viewport["']\s+content=["']([^"']+)["']/i);

  assert.ok(indexViewportMatch, "app/index.html must include a <meta name=\"viewport\"> tag");
  assert.ok(platViewportMatch, "app/plat-monitor.html must include a <meta name=\"viewport\"> tag");

  assert.match(indexViewportMatch[1], /\bwidth=device-width\b/);
  assert.match(indexViewportMatch[1], /\bviewport-fit=cover\b/);
  assert.match(platViewportMatch[1], /\bwidth=device-width\b/);
  assert.match(platViewportMatch[1], /\bviewport-fit=cover\b/);
});

test("UT-M11-2: app/styles/index.css enforces strictly monotonic :root z-index tokens, active var(--z-*) wiring, and 100dvh .atlas-shell", () => {
  const css = fs.readFileSync(path.join(APP_DIR, "styles", "index.css"), "utf8");

  const rootMatch = css.match(/:root\s*\{([\s\S]*?)\n\}/);
  assert.ok(rootMatch, "app/styles/index.css must declare a :root custom property block");
  const rootBody = rootMatch[1];

  const expectedOrderedTokens = [
    "--z-map",
    "--z-hud",
    "--z-card",
    "--z-inspector-mobile",
    "--z-polyedit",
    "--z-survey-sheet",
    "--z-sidebar-mobile",
    "--z-topbar",
    "--z-modal-base",
  ];

  const values = [];
  for (const token of expectedOrderedTokens) {
    const m = rootBody.match(new RegExp(`${token}\\s*:\\s*(\\d+)\\s*;`));
    assert.ok(m, `Expected :root to declare integer ${token}`);
    const val = Number(m[1]);
    assert.equal(Number.isInteger(val), true);
    values.push({ token, val });

    // Every :root --z-* token must be referenced via var(--z-*) in index.css (zero dead tokens)
    assert.match(
      css,
      new RegExp(`var\\(\\s*${token}\\b`),
      `Expected ${token} to be referenced via var(${token}) in app/styles/index.css`
    );
  }

  for (let i = 1; i < values.length; i += 1) {
    assert.ok(
      values[i].val > values[i - 1].val,
      `Expected strictly monotonic z-index ladder: ${values[i - 1].token} (${values[i - 1].val}) < ${values[i].token} (${values[i].val})`
    );
  }

  const byToken = Object.fromEntries(values.map((v) => [v.token, v.val]));
  assert.ok(
    byToken["--z-sidebar-mobile"] > byToken["--z-inspector-mobile"],
    "Mobile Filters sidebar must stack above Property Inspector sheet"
  );
  assert.ok(
    byToken["--z-modal-base"] > byToken["--z-topbar"],
    "Modal backdrop base z-index must stack above #atlas-topbar"
  );

  // Verify modal sub-layer ladder stays strictly >= --z-modal-base and monotonic up to .field-survey-toast
  const modalSelectors = [
    "#historian-audit-modal",
    "#scorecard-modal",
    "#ch42-modal",
    "#dossier-modal,\\s*#sanborn-browser-modal",
    "#share-modal",
    "#correction-modal",
    "#photo-lightbox-modal",
    "\\.field-survey-toast",
  ];
  let prevModalZ = byToken["--z-modal-base"];
  for (const sel of modalSelectors) {
    const m = css.match(new RegExp(`${sel}\\s*\\{[^}]*?z-index:\\s*(\\d+)\\s*;`));
    assert.ok(m, `Expected explicit z-index rule for ${sel}`);
    const zVal = Number(m[1]);
    assert.ok(zVal > prevModalZ, `Expected ${sel} (${zVal}) > previous modal layer (${prevModalZ})`);
    prevModalZ = zVal;
  }

  // Verify .atlas-shell includes 100vh fallback followed by 100dvh dynamic viewport height
  const shellMatch = css.match(/\.atlas-shell\s*\{([^}]+)\}/);
  assert.ok(shellMatch, "Expected .atlas-shell rule in app/styles/index.css");
  assert.match(shellMatch[1], /height:\s*100vh\s*;\s*height:\s*100dvh\s*;/);
});

test("UT-M11-3: Milestone 11 mobile rules use @media screen strictly isolated from @media print in app/styles/index.css", () => {
  const css = fs.readFileSync(path.join(APP_DIR, "styles", "index.css"), "utf8");

  const printIdx = css.indexOf("@media print {");
  const m11MarkerIdx = css.indexOf("Milestone 11: Systematic Mobile UX");
  assert.ok(printIdx > 0, "Expected @media print block in app/styles/index.css");
  assert.ok(m11MarkerIdx > printIdx, "Expected Milestone 11 responsive section after @media print");

  const m11CommentStart = css.lastIndexOf("/*", m11MarkerIdx);
  const m11Slice = css.slice(m11CommentStart >= 0 ? m11CommentStart : m11MarkerIdx);
  const m11Uncommented = m11Slice.replace(/\/\*[\s\S]*?\*\//g, "");
  const mediaHeaders = Array.from(m11Uncommented.matchAll(/@media\s+([^{]+)\{/g), (m) => m[1].trim());
  assert.ok(mediaHeaders.length >= 5, `Expected >= 5 @media blocks in Milestone 11 section, got ${mediaHeaders.length}`);
  for (const header of mediaHeaders) {
    assert.equal(
      header.startsWith("screen and "),
      true,
      `Every Milestone 11 media query below @media print must be scoped to '@media screen and ...', found '@media ${header}'`
    );
  }

  // Verify key responsive layout contracts exist in the Milestone 11 @media screen slice
  assert.match(m11Slice, /\.modal-backdrop,\s*#scorecard-modal,\s*#ch42-modal/);
  assert.match(m11Slice, /100dvh\s*-\s*var\(--modal-safe-top,\s*8px\)\s*-\s*var\(--modal-safe-bottom,\s*8px\)/);
  assert.match(m11Slice, /\.modal-card\s*>\s*\.modal-header\s+\.icon-btn\[id\^="btn-close-"\]/);
  assert.match(m11Slice, /\.modal-card\s*>\s*\.modal-header\s+\.photo-lightbox-close-btn/);
  assert.match(m11Slice, /#btn-open-scorecards-hub\s*\{\s*display:\s*none\s*!important;/);
  assert.match(m11Slice, /top:\s*var\(--hud-top-offset,\s*var\(--topbar-offset,\s*112px\)\)/);
  assert.match(m11Slice, /nav\.tour-bar\s*\{\s*display:\s*none\s*!important;/);
  assert.match(m11Slice, /\.camera-tilt-dock\s*\{\s*display:\s*none\s*!important;/);
  assert.match(m11Slice, /font-size:\s*16px\s*!important;/);

  // Verify @media print still uncaps printable modal cards
  const printSlice = css.slice(printIdx, printIdx + 3500);
  assert.match(printSlice, /\.dossier-modal-card,\s*\.scorecard-modal-card,\s*\.ch42-modal-card/);
  assert.match(printSlice, /max-height:\s*none\s*!important;/);
  assert.match(printSlice, /overflow:\s*visible\s*!important;/);
});

test("UT-M11-4: _closeTransientTopbarPopovers, _syncResponsiveHudOffsets, and _setSidebarCollapsed handle full DOM and minimal Node stubs safely", () => {
  const prevDoc = globalThis.document;
  const prevWin = globalThis.window;

  try {
    // 1. Bare Node environment (document & window undefined): zero ReferenceError/TypeError
    delete globalThis.document;
    delete globalThis.window;

    const bareApp = Object.create(HoustonAtlasApp.prototype);
    assert.doesNotThrow(() => bareApp._closeTransientTopbarPopovers());
    assert.doesNotThrow(() => bareApp._syncResponsiveHudOffsets());
    assert.doesNotThrow(() => bareApp._setSidebarCollapsed(true));

    // 2. Minimal Node DOM stub (elements missing classList, setAttribute, querySelector, and window.getComputedStyle)
    globalThis.document = {
      getElementById: (id) => ({ id }),
    };
    assert.doesNotThrow(() => bareApp._closeTransientTopbarPopovers());
    assert.doesNotThrow(() => bareApp._syncResponsiveHudOffsets());
    assert.doesNotThrow(() => bareApp._setSidebarCollapsed(false));

    // 3. Interactive DOM stub: verify popover auto-close + --topbar-offset and --hud-top-offset computation
    const cssVars = new Map();
    let tourDisplay = "flex";
    let tourRect = { top: 108, bottom: 144, height: 36 };
    const elements = {
      "basemap-dropdown-menu": { classList: makeClassListStub() },
      "btn-basemap-trigger": {
        attrs: { "aria-expanded": "true" },
        setAttribute(k, v) {
          this.attrs[k] = v;
        },
      },
      "topbar-more-dropdown": {
        classList: makeClassListStub(["open"]),
        attrs: { open: "open" },
        removeAttribute(k) {
          delete this.attrs[k];
        },
      },
      "btn-topbar-more": {
        attrs: { "aria-expanded": "true" },
        setAttribute(k, v) {
          this.attrs[k] = v;
        },
      },
      "search-results": { classList: makeClassListStub() },
      "atlas-topbar": {
        getBoundingClientRect: () => ({ top: 8, bottom: 102, height: 94 }),
      },
      "atlas-sidebar": { classList: makeClassListStub(["collapsed"]) },
      "btn-toggle-sidebar": { classList: makeClassListStub() },
      "inspector-drawer": { classList: makeClassListStub() },
      "permit-watch-modal": {
        classList: makeClassListStub(["hidden"]),
        querySelectorAll: () => [],
      },
    };
    const shellStub = { classList: makeClassListStub(["sidebar-collapsed"]) };
    const tourBarStub = {
      classList: makeClassListStub(),
      getBoundingClientRect: () => tourRect,
    };

    globalThis.document = {
      documentElement: {
        style: {
          setProperty: (k, v) => cssVars.set(k, v),
        },
      },
      getElementById: (id) => elements[id] || null,
      querySelector: (sel) => {
        if (sel === ".tour-bar") return tourBarStub;
        if (sel === ".atlas-shell") return shellStub;
        return null;
      },
    };
    globalThis.window = {
      innerWidth: 390,
      getComputedStyle: (el) =>
        el === tourBarStub ? { display: tourDisplay, visibility: "visible" } : { display: "block", visibility: "visible" },
    };

    const app = Object.create(HoustonAtlasApp.prototype);
    app._syncPermitWatchCounts = () => {};

    // Opening Permit Watch modal must auto-close basemap menu, More dropdown, and search autocomplete
    app.openPermitWatchModal("all", false);
    assert.equal(elements["basemap-dropdown-menu"].classList.contains("hidden"), true);
    assert.equal(elements["btn-basemap-trigger"].attrs["aria-expanded"], "false");
    assert.equal(elements["topbar-more-dropdown"].classList.contains("open"), false);
    assert.equal("open" in elements["topbar-more-dropdown"].attrs, false);
    assert.equal(elements["btn-topbar-more"].attrs["aria-expanded"], "false");
    assert.equal(elements["search-results"].classList.contains("hidden"), true);
    assert.equal(elements["permit-watch-modal"].classList.contains("hidden"), false);

    // Visible tour bar below topbar: --topbar-offset = 102 + 6 = 108px, --hud-top-offset = 144 + 6 = 150px
    app._syncResponsiveHudOffsets();
    assert.equal(cssVars.get("--topbar-offset"), "108px");
    assert.equal(cssVars.get("--hud-top-offset"), "150px");

    // Suppressed tour bar (display: none when card/sheet open): --hud-top-offset collapses to --topbar-offset (108px)
    tourDisplay = "none";
    app._syncResponsiveHudOffsets();
    assert.equal(cssVars.get("--topbar-offset"), "108px");
    assert.equal(cssVars.get("--hud-top-offset"), "108px");

    // Opening mobile sidebar at 390px hides inspector drawer and updates aria/classes
    app._setSidebarCollapsed(false);
    assert.equal(elements["atlas-sidebar"].classList.contains("collapsed"), false);
    assert.equal(shellStub.classList.contains("sidebar-collapsed"), false);
    assert.equal(elements["btn-toggle-sidebar"].classList.contains("active"), true);
    assert.equal(elements["inspector-drawer"].classList.contains("hidden"), true);
  } finally {
    if (prevDoc === undefined) delete globalThis.document;
    else globalThis.document = prevDoc;
    if (prevWin === undefined) delete globalThis.window;
    else globalThis.window = prevWin;
  }
});

test("UT-M11-5: app/plat-monitor.html enforces mobile @media screen rules, toast pointer-events:none, table overflow wrappers, and menu viewport clamping", () => {
  const platHtml = fs.readFileSync(path.join(APP_DIR, "plat-monitor.html"), "utf8");

  // 1. Passive toast banner never intercepts mobile taps
  const toastRule = platHtml.match(/\.toast\s*\{([^}]+)\}/);
  assert.ok(toastRule, "Expected .toast CSS rule in app/plat-monitor.html");
  assert.match(toastRule[1], /pointer-events:\s*none\b/);
  assert.match(toastRule[1], /env\(safe-area-inset-bottom/);

  // 2. Mobile @media screen rule enforces 16px inputs, 48dvh list cap, and 100dvh modal cap
  assert.match(platHtml, /@media screen and \(max-width:900px\)\{/);
  assert.match(platHtml, /max-height:48vh;max-height:48dvh/);
  assert.match(platHtml, /max-height:calc\(100dvh - var\(--modal-safe-top,10px\) - var\(--modal-safe-bottom,10px\)\)/);
  assert.match(platHtml, /select,textarea\{font-size:16px !important\}/);

  // 3. Both 15-point checklist and Chronological Findings Log .wl-table instances sit inside overflow-x:auto wrappers
  const checklistTableMatches = Array.from(
    platHtml.matchAll(/<div style="overflow-x:auto[^"]*">\s*<table class="wl-table">/g)
  );
  assert.equal(
    checklistTableMatches.length,
    2,
    `Expected both Watchlist detail .wl-table blocks to be wrapped in overflow-x:auto containers, found ${checklistTableMatches.length}`
  );

  // 4. Dropdown clamping helper bounds #loadMenu/#exportMenu within [12, vw - 12] and re-runs on resize
  assert.match(platHtml, /function clampMenuListToViewport\(list\)/);
  assert.match(platHtml, /if \(targetLeft \+ r\.width > vw - 12\) targetLeft = vw - 12 - r\.width;/);
  assert.match(platHtml, /if \(targetLeft < 12\) targetLeft = 12;/);
  assert.match(platHtml, /window\.addEventListener\("resize",\s*\(\)\s*=>\s*\{\s*document\.querySelectorAll\("\.menu-list:not\(\.hidden\)"\)\.forEach\(clampMenuListToViewport\);/);
});

test("UT-M11-6: dynamic ?v= cache-bust parity and zero U+2013/U+2014 dashes across Milestone 11 responsive files", () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(DATA_DIR, "pmtiles_manifest.json"), "utf8"));
  const expectedVer = String(manifest.cache_bust || "").trim();
  assert.match(expectedVer, /^\d{8}[a-z]?$/);
  assert.equal(ORGANIZER_CACHE_BUST, expectedVer);

  const schemaPy = fs.readFileSync(path.join(REPO_ROOT, "pipeline", "atlas_pipeline", "schema.py"), "utf8");
  assert.match(schemaPy, new RegExp(`ACTIVE_CACHE_BUST\\s*=\\s*"${expectedVer}"`));

  const appFiles = [
    path.join(APP_DIR, "index.html"),
    path.join(APP_DIR, "plat-monitor.html"),
    ...fs
      .readdirSync(path.join(APP_DIR, "js"))
      .filter((f) => f.endsWith(".js"))
      .map((f) => path.join(APP_DIR, "js", f)),
  ];

  for (const filePath of appFiles) {
    const rel = path.relative(REPO_ROOT, filePath);
    const content = fs.readFileSync(filePath, "utf8");
    for (const m of content.matchAll(/\?v=([A-Za-z0-9._-]+)/g)) {
      assert.equal(m[1], expectedVer, `Expected ?v=${expectedVer} in ${rel}, got ?v=${m[1]}`);
    }
  }

  for (const relDoc of [
    "README.md",
    "docs/01-architecture-and-rendering.md",
    "docs/10-sources-integrations-and-curation-workflows.md",
    "app/index.html",
    "app/plat-monitor.html",
    "app/styles/index.css",
    "tests/unit/mobile_ux_responsive.test.mjs",
    "tests/e2e/test_mobile_ux_geometry.py",
  ]) {
    const raw = fs.readFileSync(path.join(REPO_ROOT, relDoc), "utf8");
    assert.equal(
      raw.includes("\u2014"),
      false,
      `Found forbidden U+2014 em-dash in ${relDoc}`
    );
    assert.equal(
      raw.includes("\u2013"),
      false,
      `Found forbidden U+2013 en-dash in ${relDoc}`
    );
  }
});
