/**
 * Main application UI controller for The Houston Building Atlas v2 (Preservation Houston).
 */

import {
  CURATED_TOURS,
  getLegendItems,
  getYearColorHex,
} from "./palettes.js?v=20261010f";
import {
  buildShareableUrl,
  createFilterStore,
  HISTORIC_WARD_ERAS,
  parseHashToState,
  resolveActiveAnnexationDecade,
  resolveActiveWardEra,
  serializeStateToHash,
  SHARE_VIEW_PRESETS,
} from "./filterStore.js?v=20261010f";
import { AtlasMapController } from "./mapController.js?v=20261010g";
import { fetchHcadDeepLink, fetchHcadLiveRecord } from "./hcadLink.js?v=20261010f";
import {
  applyOverrideToProperties,
  authenticateAdminSession,
  clearAdminSession,
  formatSuggestionsAsCsv,
  getAdminSession,
  getLocalPendingSuggestions,
  saveGoogleSheetEndpoints,
  submitAdminApprovedOverride,
  submitCorrectionSuggestion,
} from "./curatedEdits.js?v=20261010f";
import {
  buildStreetViewUrl,
  hideBuildingPhoto,
  loadCuratedPhotosIndex,
  registerSessionPhoto,
  resolveBuildingPhotos,
} from "./photoService.js?v=20261010f";

class HoustonAtlasApp {
  constructor() {
    this._hadInitialUrlParams = Boolean(
      (window.location.hash && window.location.hash.length > 1) ||
        (window.location.search && window.location.search.length > 1)
    );
    const { patch, viewport, selection, collapseSidebar } = parseHashToState(
      window.location.hash,
      window.location.search
    );
    this.initialViewport = viewport;
    this.initialSelection = selection;
    this.initialCollapseSidebar = collapseSidebar;
    this.filterStore = createFilterStore(patch);
    this.searchIndex = [];
    this.globalStats = null;
    this.deedCatalog = null;
    this.lastViewportStats = null;
    this.timelapseTimer = null;
    this._suppressUrlUpdate = false;
    this._activePhotoState = null;
    this._activeTour = null;
    this._activeTourStopIndex = -1;
    this._preTourSnapshot = null;
    this._exportStudioState = {
      targetSpec: null, // null = current isolated/selected or viewport
      composition: "figure_ground", // 'figure_ground' | 'footprints_only' | 'border_only' | 'era_poster'
      theme: "stencil_white", // 'stencil_white' | 'stencil_black' | 'ph_emerald' | 'blueprint' | 'terracotta' | 'archival_era'
      format: "square", // 'square' | 'round_coaster' | 'poster'
      transparentBg: false,
      fillBuildings: true,
      showLandmarks: true,
      showCaption: true,
      customTitle: "",
      customSubtitle: "",
      borderWeight: 4,
      lastExtraction: null,
      lastSvgMarkup: "",
    };

    this.mapController = new AtlasMapController({
      containerId: "map-canvas",
      filterStore: this.filterStore,
      onSelectFeature: (props) => this.renderInspectorDrawer(props),
      onViewportStats: (stats) => this.handleViewportStats(stats),
      onPitchChange: (pitch) => this._syncTiltControls(pitch),
      onSelectTourStop: (stopIdx) => {
        if (this._activeTour) {
          this._activateTourStop(this._activeTour, stopIdx);
        }
      },
      onOverlapStackChange: (stack, activeIdx) =>
        this._renderMapOverlapBar(stack, activeIdx),
      onIsolationChange: (iso) => this._renderIsolationBanner(iso),
    });
  }

  async start() {
    this._bindControls();
    this._renderTourPills();
    this._renderLegend();
    this._syncControlsFromState(this.filterStore.getState());

    if (this.initialCollapseSidebar !== null) {
      this._setSidebarCollapsed(this.initialCollapseSidebar);
    } else if (typeof window !== "undefined" && window.innerWidth <= 900) {
      this._setSidebarCollapsed(true);
    }

    await Promise.all([
      this.mapController.init(this.initialViewport),
      this._loadMetadataFiles(),
      loadCuratedPhotosIndex(),
    ]);

    this._mergeCuratedOverridesIntoSearchIndex();
    this._enrichSearchIndexWithOverlayMetadata();

    if (this.initialSelection) {
      this.mapController.selectFeatureByIdOrHcad({
        hcadNum: this.initialSelection.hcadNum || "",
        featureId: this.initialSelection.featureId || "",
        flyTo: !this.initialViewport,
      });
    }

    this._applyExportStudioHashParams(window.location.hash, window.location.search);

    this.filterStore.subscribe((state) => {
      this._syncControlsFromState(state);
      this._renderLegend();
      this._manageTimelapseLoop(state);
      this._updateUrlHash(state);
      this._refreshShareModalContent();
    });

    const handleUrlChange = () => {
      const { patch, viewport, selection, collapseSidebar } = parseHashToState(
        window.location.hash,
        window.location.search
      );
      this._suppressUrlUpdate = true;
      if (Object.keys(patch).length > 0) {
        this.filterStore.setState(patch);
      }
      if (collapseSidebar !== null) {
        this._setSidebarCollapsed(collapseSidebar);
      }
      if (viewport) {
        this.mapController.flyToLocation({
          lng: viewport.lng,
          lat: viewport.lat,
          zoom: viewport.zoom,
          pitch: viewport.pitch,
          hcadNum: selection?.hcadNum || "",
          featureId: selection?.featureId || "",
        });
      } else if (selection) {
        this.mapController.selectFeatureByIdOrHcad({
          hcadNum: selection.hcadNum || "",
          featureId: selection.featureId || "",
          flyTo: true,
        });
      }
      this._applyExportStudioHashParams(window.location.hash, window.location.search);
      this._suppressUrlUpdate = false;
    };

    window.addEventListener("hashchange", handleUrlChange);
    window.addEventListener("popstate", handleUrlChange);
  }

  _setSidebarCollapsed(collapsed) {
    const sidebar = document.getElementById("atlas-sidebar");
    const shell = document.querySelector(".atlas-shell");
    const btnToggle = document.getElementById("btn-toggle-sidebar");
    if (!sidebar) return;

    sidebar.classList.toggle("collapsed", Boolean(collapsed));
    if (shell) {
      shell.classList.toggle("sidebar-collapsed", Boolean(collapsed));
    }
    if (btnToggle) {
      btnToggle.classList.toggle("active", !collapsed);
    }
    if (!collapsed && typeof window !== "undefined" && window.innerWidth <= 900) {
      const drawer = document.getElementById("inspector-drawer");
      if (drawer) drawer.classList.add("hidden");
    }
  }

  async _loadMetadataFiles() {
    try {
      const [searchRes, statsRes, haifRes, deedRes] = await Promise.all([
        fetch("public/data/search_index.json?v=20261010f"),
        fetch("public/data/stats_summary.json?v=20261010f"),
        fetch("public/data/haif_index.json?v=20261010f").catch(() => null),
        fetch("public/data/deed_restrictions_catalog.json?v=20261010f").catch(() => null),
      ]);
      const rawIdx = await searchRes.json();
      for (const item of rawIdx) {
        if (item.is_neighborhood_entry) {
          if (!item.label) item.label = item.name || item.building_name || "Houston Neighborhood";
          if (!Number.isFinite(item.lon) && Number.isFinite(item.lng)) item.lon = item.lng;
          if (item.overlay_layer === "historicWards") item.overlay_layer = "historic_wards";
          if (item.overlay_layer === "superNeighborhoods") item.overlay_layer = "super_neighborhoods";
          if (item.overlay_layer === "plattedSubdivisions") {
            item.overlay_layer = "platted_subdivisions";
          }
          if (!item.sublabel) {
            const alts = Array.isArray(item.alt_names) ? item.alt_names.slice(0, 3).join(", ") : "";
            item.sublabel = [alts ? `AKA: ${alts}` : "", item.address || ""]
              .filter(Boolean)
              .join(" • ");
          }
        }
      }
      this.searchIndex = rawIdx;
      this.globalStats = await statsRes.json();
      if (haifRes && haifRes.ok) {
        this.haifIndex = await haifRes.json();
      } else {
        this.haifIndex = null;
      }
      if (deedRes && deedRes.ok) {
        this.deedCatalog = await deedRes.json();
      } else {
        this.deedCatalog = null;
      }
      if (this.mapController?.overlaysData) {
        this._mergeCuratedOverridesIntoSearchIndex();
        this._enrichSearchIndexWithOverlayMetadata();
      }
      this._renderGlobalDatasetSummary();
    } catch (err) {
      console.error("Failed to load metadata files:", err);
    }
  }

  _normalizeAddressForHaifLookup(addr) {
    if (!addr) return "";
    let s = String(addr)
      .toUpperCase()
      .replace(/[^A-Z0-9\s]/g, " ")
      .replace(/\b(?:STE|SUITE|APT|UNIT|BLDG|FL|FLOOR)\b.*$/, "");
    const parts = s.trim().split(/\s+/);
    if (!parts.length || !/^\d+$/.test(parts[0])) return "";
    const stripTail = new Set([
      "HOUSTON", "TX", "TEXAS", "BAYTOWN", "PASADENA", "BELLAIRE",
      "ST", "STREET", "AVE", "AVENUE", "BLVD", "BOULEVARD", "RD", "ROAD",
      "DR", "DRIVE", "LN", "LANE", "WAY", "PKWY", "PARKWAY", "FWY", "FREEWAY",
      "HWY", "HIGHWAY", "CT", "COURT", "PL", "PLACE", "CIR", "CIRCLE",
      "SQ", "SQUARE", "TER", "TERRACE", "TRL", "TRAIL", "LOOP",
      "N", "S", "E", "W", "NORTH", "SOUTH", "EAST", "WEST"
    ]);
    while (parts.length > 2 && (/^\d+$/.test(parts[parts.length - 1]) || stripTail.has(parts[parts.length - 1]))) {
      parts.pop();
    }
    const dirMap = { NORTH: "N", SOUTH: "S", EAST: "E", WEST: "W" };
    if (parts.length >= 3 && dirMap[parts[1]]) {
      parts[1] = dirMap[parts[1]];
    }
    return parts.join(" ");
  }

  _resolveHaifThreads(props) {
    const out = [];
    const seenTids = new Set();
    const pushThread = (t) => {
      if (!t) return;
      const tid = Number(t.tid || 0);
      const key = tid || t.url || t.title;
      if (!key || seenTids.has(key)) return;
      seenTids.add(key);
      out.push({
        tid,
        title: t.title || t.t || "HAIF Architectural Discussion",
        subforum: t.subforum || t.f || "Architecture & Development",
        url: t.url || t.u || "",
        wb_url: t.wb_url || t.w || "",
        date: t.date || t.d || "",
        excerpt: t.excerpt || t.e || "",
        is_demo: Boolean(t.is_demo || t.m),
      });
    };

    let rawThreads = props.haif_threads;
    if (typeof rawThreads === "string" && rawThreads.trim().startsWith("[")) {
      try {
        rawThreads = JSON.parse(rawThreads);
      } catch (_e) {
        rawThreads = null;
      }
    }
    if (Array.isArray(rawThreads)) {
      for (const t of rawThreads) pushThread(t);
    }

    if (this.haifIndex && this.haifIndex.threads) {
      const threadsDict = this.haifIndex.threads;
      const byKey = this.haifIndex.by_key || {};
      const byAddr = this.haifIndex.by_addr || {};
      const rawId = String(props.id || "").trim();
      const baseId = rawId.replace(/#fp_\d+$/, "");
      const hcad = String(props.hcad_num || "").trim();
      const candidateTids = [
        ...(byKey[rawId] || []),
        ...(byKey[baseId] || []),
        ...(byKey[hcad] || []),
      ];
      const normA = this._normalizeAddressForHaifLookup(props.address);
      if (normA) {
        if (byAddr[normA]) candidateTids.push(...byAddr[normA]);
        const parts = normA.split(" ");
        if (parts.length >= 3 && ["N", "S", "E", "W", "NE", "NW", "SE", "SW"].includes(parts[1])) {
          const noDir = `${parts[0]} ${parts.slice(2).join(" ")}`;
          if (byAddr[noDir]) candidateTids.push(...byAddr[noDir]);
        }
      }
      for (const tid of candidateTids) {
        const rec = threadsDict[String(tid)];
        if (rec) {
          pushThread({ tid, ...rec });
        }
      }
    }
    return out.slice(0, 4);
  }

  /**
   * Resolves the authoritative Primary Building Name (`primaryName`), Historical / Colloquial
   * Alternate Names (`altNames`), and Name Provenance (`nameSource`) across:
   * 1. Feature properties (`building_name`, `landmark_name`, `alt_names`, `name_source`)
   * 2. City of Houston Landmark & Good Brick Award overlay features (`overlaysData`)
   * 3. Runtime HAIF & Landmark Name index (`haifIndex.names_by_key` & `haifIndex.names_by_addr`)
   *    so even countywide PMTiles vector shard buildings display their historic & modern names.
   */
  _resolveBuildingNameAndAliases(props = {}) {
    const cleanNameStr = (raw) => {
      if (!raw) return "";
      let s = String(raw);
      for (let i = 0; i < 2; i += 1) {
        if (/%[0-9a-fA-F]{2}/.test(s)) {
          try {
            s = decodeURIComponent(s);
          } catch (_e) {
            break;
          }
        }
      }
      s = s
        .replace(/[\u200b-\u200f\ufeff]/g, "")
        .replace(/[\t\r\n\u00a0]+/g, " ")
        .replace(/\s{2,}/g, " ")
        .trim();
      if (s && /^[a-z]/.test(s)) {
        s = s.charAt(0).toUpperCase() + s.slice(1);
      }
      s = s
        .replace(/\bMcintyre([’']?s)?\b/g, "McIntyre$1")
        .replace(/\bMcgovern\b/g, "McGovern")
        .replace(/\bJpmorgan\b/g, "JPMorgan")
        .replace(/\bUthealth\b/g, "UTHealth")
        .replace(/\bMd\s+Anderson\b/g, "MD Anderson")
        .replace(/\bSt\s+Luke([’']?s)?\b/g, (_m, p1) => (p1 ? "St. Luke's" : "St. Luke"));
      return s;
    };

    const alnumKey = (s) =>
      String(s || "")
        .toLowerCase()
        .replace(/^the\s+/, "")
        .replace(/&/g, " and ")
        .replace(/[’']/g, "")
        .replace(/[^a-z0-9\s]/g, " ")
        .replace(/\s+/g, " ")
        .trim();

    const rawAddr = String(props.address || "").trim();
    const normAddr = this._normalizeAddressForHaifLookup(rawAddr);
    const rawId = String(props.building_id || props.id || "").trim().replace(/#fp_\d+$/, "");
    const hcad = String(props.hcad_num || "").trim();
    const isSubBuilding = rawId.includes("#");

    const isStreetAddrOnly = (s) => {
      if (!s) return true;
      const clean = cleanNameStr(s);
      if (!clean || clean.startsWith("HCAD ")) return true;
      if (normAddr && this._normalizeAddressForHaifLookup(clean) === normAddr && /^\d+\s+/.test(clean)) {
        return true;
      }
      return /^\d{1,5}(?:\s*-\s*\d{1,5})?\s+(?:[NSEW]\.?\s+)?[A-Za-z0-9\.\s]+\b(?:Street|St|Avenue|Ave|Boulevard|Blvd|Drive|Dr|Road|Rd|Lane|Ln|Court|Ct|Place|Pl|Circle|Cir|Parkway|Pkwy|Freeway|Fwy|Highway|Hwy|Loop|Way)\.?$/i.test(
        clean
      );
    };

    const candidatePrimaries = [];
    const candidateAlts = [];
    let resolvedSource = String(props.name_source || "").trim();

    const addPrimary = (nm, src = "") => {
      const c = cleanNameStr(nm);
      if (!c || isStreetAddrOnly(c)) return;
      candidatePrimaries.push(c);
      if (src && !resolvedSource) resolvedSource = src;
    };

    const addAltList = (arr) => {
      if (!arr) return;
      let parsed = arr;
      if (typeof parsed === "string") {
        const t = parsed.trim();
        if (t.startsWith("[")) {
          try {
            parsed = JSON.parse(t);
          } catch (_e) {
            parsed = t.split(/[;|]/);
          }
        } else {
          parsed = t.split(/[;|]/);
        }
      }
      if (Array.isArray(parsed)) {
        for (const item of parsed) {
          const c = cleanNameStr(item);
          if (c && !isStreetAddrOnly(c)) candidateAlts.push(c);
        }
      }
    };

    // 1. Direct feature properties
    addPrimary(props.building_name, props.name_source || "");
    addPrimary(props.landmark_name, props.name_source || "");
    addPrimary(props.name, props.name_source || "");
    addAltList(props.alt_names);

    // 2. O(1) indexed lookup in overlaysData.landmarks & good_brick_awards
    if (!isSubBuilding && this.mapController?.overlaysData) {
      if (!this._overlayNameIndex) {
        const lmByHcad = new Map();
        const lmByAddr = new Map();
        const gbByHcad = new Map();
        const gbByAddr = new Map();
        for (const f of this.mapController.overlaysData.landmarks?.features || []) {
          const lp = f.properties || {};
          const lHcad = String(lp.hcad_num || "").trim();
          const lNormAddr = this._normalizeAddressForHaifLookup(lp.address);
          if (lHcad && !lmByHcad.has(lHcad)) lmByHcad.set(lHcad, lp);
          if (lNormAddr && !lmByAddr.has(lNormAddr)) lmByAddr.set(lNormAddr, lp);
        }
        for (const f of this.mapController.overlaysData.good_brick_awards?.features || []) {
          const gp = f.properties || {};
          const gHcad = String(gp.hcad_num || "").trim();
          const gNormAddr = this._normalizeAddressForHaifLookup(gp.address);
          if (gHcad && !gbByHcad.has(gHcad)) gbByHcad.set(gHcad, gp);
          if (gNormAddr && !gbByAddr.has(gNormAddr)) gbByAddr.set(gNormAddr, gp);
        }
        this._overlayNameIndex = { lmByHcad, lmByAddr, gbByHcad, gbByAddr };
      }
      const idx = this._overlayNameIndex;
      const lp = (hcad && idx.lmByHcad.get(hcad)) || (normAddr && idx.lmByAddr.get(normAddr));
      if (lp) {
        addPrimary(lp.building_name || lp.name, lp.name_source || "COH Landmark Designation Report");
        addAltList(lp.alt_names);
      }
      const gp = (hcad && idx.gbByHcad.get(hcad)) || (normAddr && idx.gbByAddr.get(normAddr));
      if (gp) {
        addPrimary(
          gp.building_name || gp.landmark_name || gp.name,
          gp.name_source || "Preservation Houston Good Brick Award"
        );
        addAltList(gp.alt_names);
      }
    }

    // 3. Runtime lookup in haifIndex.names_by_key & haifIndex.names_by_addr (covers 4,795 HCAD parcels & 5,073 addresses in PMTiles shards!)
    if (this.haifIndex) {
      const namesByKey = this.haifIndex.names_by_key || {};
      const namesByAddr = this.haifIndex.names_by_addr || {};
      const keyHit =
        (rawId && namesByKey[rawId]) ||
        (!isSubBuilding && hcad && namesByKey[hcad]) ||
        (!isSubBuilding && normAddr && namesByAddr[normAddr]);
      if (keyHit) {
        addPrimary(keyHit.n, keyHit.s || "Houston Architecture Forum (HAIF)");
        addAltList(keyHit.a);
      }
    }

    const primaryName = candidatePrimaries[0] || rawAddr || "Houston Historic Structure";
    const hasCustomBuildingName = Boolean(candidatePrimaries.length > 0);
    if (!resolvedSource && hasCustomBuildingName) {
      resolvedSource =
        props.landmark_report_url || props.landmark_code
          ? "COH Landmark Designation Report"
          : props.good_brick_awards || props.good_brick_summary
          ? "Preservation Houston Good Brick Award"
          : props.is_curated_override
          ? "Preservation Houston Curated Archive"
          : "Archival & Community Inventory";
    }

    // Deduplicate alternate names (including any secondary primary candidates that differ from primaryName)
    const allAltCandidates = [...candidatePrimaries.slice(1), ...candidateAlts];
    const primLow = primaryName.toLowerCase();
    const primNoThe = primLow.replace(/^the\s+/, "");
    const primStem = primNoThe.replace(/\s+(?:building|bldg\.?|house|home|residence|tower)$/i, "").trim();
    const primAlnum = alnumKey(primaryName);
    const seenNorms = new Set([primLow, primNoThe, primStem, primAlnum]);
    const altNames = [];
    for (const cand of allAltCandidates) {
      const clean = cleanNameStr(cand);
      if (!clean || isStreetAddrOnly(clean)) continue;
      const low = clean.toLowerCase();
      const lowNoThe = low.replace(/^the\s+/, "");
      const lowStem = lowNoThe.replace(/\s+(?:building|bldg\.?|house|home|residence|tower)$/i, "").trim();
      const lowAlnum = alnumKey(clean);
      if (
        seenNorms.has(low) ||
        seenNorms.has(lowNoThe) ||
        (lowStem && seenNorms.has(lowStem)) ||
        (lowAlnum && seenNorms.has(lowAlnum))
      ) {
        continue;
      }
      seenNorms.add(low);
      seenNorms.add(lowNoThe);
      if (lowStem) seenNorms.add(lowStem);
      if (lowAlnum) seenNorms.add(lowAlnum);
      altNames.push(clean);
    }

    return {
      primaryName,
      altNames: altNames.slice(0, 5),
      nameSource: resolvedSource,
      hasCustomBuildingName,
    };
  }

  _resolveStyleAndClassInfo(props = {}) {
    const GENERIC_STYLES = new Set([
      "",
      "Historical / Architectural Structure",
      "Houston Historic Structure",
      "Historic Structure",
      "Structure",
      "None",
      "null",
    ]);
    const GRADE_LABELS = {
      "A++": "Luxury Custom Residential (HCAD Grade A++)",
      "A+": "High-Grade Custom Residential (HCAD Grade A+)",
      A: "High-Grade Residential (HCAD Grade A)",
      "A-": "Upper-Grade Residential (HCAD Grade A-)",
      "B+": "Good-Grade Residential (HCAD Grade B+)",
      B: "Good-Grade Residential (HCAD Grade B)",
      "B-": "Above-Average Residential (HCAD Grade B-)",
      "C+": "Average Residential (HCAD Grade C+)",
      C: "Standard Residential (HCAD Grade C)",
      "C-": "Standard Economy Residential (HCAD Grade C-)",
      "D+": "Economy Residential (HCAD Grade D+)",
      D: "Economy Residential (HCAD Grade D)",
      "D-": "Basic Frame Residential (HCAD Grade D-)",
      "E+": "Vernacular Frame Cottage (HCAD Grade E+)",
      E: "Vernacular Frame / Cottage (HCAD Grade E)",
      "E-": "Minimal Frame Structure (HCAD Grade E-)",
      X: "Tax-Exempt / Institutional Class (HCAD X)",
    };
    const STRUCTURAL_CLASSES = new Set([
      "Wood or Light Steel",
      "Masonry Bearing",
      "Open Steel Skeleton",
      "Reinforced Concrete",
      "Fireproofed Steel",
      "Mobile Home/Manufactured Housing",
      "Storage Tank",
      "Petrochemical Complex",
      "Educational Campus Structure",
      "Frame Utility Shed",
      "Carport - Residential",
      "Canopy - Residential",
      "Frame Detached Garage",
      "Utility Building - Metal",
      "Portable/Modular Office - Average",
    ]);

    const splitParenNote = (str) => {
      const s = String(str || "").trim();
      const m = s.match(/^(.+?)\s*\(([^)]+)\)\s*$/);
      if (!m) return { core: s, note: "" };
      const inside = m[2].trim();
      if (/^hcad\b|\bstories\b|\bunits\b/i.test(inside)) {
        return { core: s, note: "" };
      }
      return { core: m[1].trim(), note: inside };
    };

    const rawStyle = String(props.style || "").trim();
    const rawBldStyle = String(props.bld_style || "").trim();
    const rawGrade = String(
      props.hcad_grade ||
        (GRADE_LABELS[rawBldStyle] ? rawBldStyle : GRADE_LABELS[rawStyle] ? rawStyle : "")
    ).trim();
    const useCat = String(props.use_category || "").trim();
    const landuseDesc = String(props.landuse_desc || "").trim();

    const cleanArchStyle =
      rawStyle && !GENERIC_STYLES.has(rawStyle) && !GRADE_LABELS[rawStyle] ? rawStyle : "";
    const cleanBldStyle =
      rawBldStyle && !GENERIC_STYLES.has(rawBldStyle) && !GRADE_LABELS[rawBldStyle]
        ? rawBldStyle
        : "";

    const chips = [];

    if (cleanArchStyle && !STRUCTURAL_CLASSES.has(cleanArchStyle)) {
      const { core, note } = splitParenNote(cleanArchStyle);
      chips.push({
        label: core,
        query: core,
        note,
        headerLabel: `Architectural Style: ${core}`,
        isSecondary: false,
      });
      if (
        cleanBldStyle &&
        cleanBldStyle.toLowerCase() !== cleanArchStyle.toLowerCase() &&
        cleanBldStyle.toLowerCase() !== core.toLowerCase() &&
        cleanBldStyle.toLowerCase() !== useCat.toLowerCase()
      ) {
        const bSplit = splitParenNote(cleanBldStyle);
        const isStruct =
          STRUCTURAL_CLASSES.has(cleanBldStyle) ||
          /steel|masonry|concrete|frame|tank|canopy|carport|mobile|utility|paving|plant/i.test(
            cleanBldStyle
          );
        chips.push({
          label: bSplit.core,
          query: bSplit.core,
          note: bSplit.note,
          headerLabel: `${isStruct ? "Building Class" : "Style"}: ${bSplit.core}`,
          isSecondary: true,
        });
      }
      return {
        displayStyle: core,
        searchQuery: core,
        headerLabel: `Architectural Style: ${core}`,
        chips,
      };
    }

    if (cleanBldStyle) {
      const isStructClass =
        STRUCTURAL_CLASSES.has(cleanBldStyle) ||
        /steel|masonry|concrete|frame|tank|canopy|carport|mobile|utility|paving|plant/i.test(
          cleanBldStyle
        );
      const { core, note } = splitParenNote(cleanBldStyle);
      const hdr = `${isStructClass ? "Building Class" : "Architectural Style"}: ${core}`;
      chips.push({
        label: core,
        query: core,
        note,
        headerLabel: hdr,
        isSecondary: false,
      });
      return {
        displayStyle: core,
        searchQuery: core,
        headerLabel: hdr,
        chips,
      };
    }

    if (rawGrade && GRADE_LABELS[rawGrade]) {
      const gradeLabel = GRADE_LABELS[rawGrade];
      const hdr = `Building Class: ${gradeLabel}`;
      chips.push({
        label: gradeLabel,
        query: gradeLabel,
        note: "",
        headerLabel: hdr,
        isSecondary: false,
      });
      return {
        displayStyle: gradeLabel,
        searchQuery: gradeLabel,
        headerLabel: hdr,
        chips,
      };
    }

    const fallbackCat = useCat || landuseDesc || "Residential";
    const isHistNote =
      rawStyle === "Historical / Architectural Structure" ||
      rawBldStyle === "Historical / Architectural Structure";
    const hdr = `Building Category: ${fallbackCat}`;
    chips.push({
      label: fallbackCat,
      query: fallbackCat,
      note: isHistNote ? "Historic / Architectural" : "",
      headerLabel: hdr,
      isSecondary: false,
    });
    return {
      displayStyle: fallbackCat,
      searchQuery: fallbackCat,
      headerLabel: hdr,
      chips,
    };
  }

  _resolveLandUseChips(props = {}) {
    const CRYPTIC_HCAD_LANDUSE = new Set([
      "",
      "Res Improved Table Value",
      "Res Improved Table Value (Res. Use)",
      "Res Improved Override",
      "Res Improved Override (Res. Use)",
      "Res. Struct. Or Conversion",
      "Res Vacant Table Value",
      "Res Vacant Table Value (Res. Use)",
      "Res Vacant Override",
      "Residential Imps Only Land",
      "General Commercial Vacant",
      "Vacant Exempt Land",
      "Auxiliary Improvement",
      "Mkt Value of NV Land",
      "UDI Vacant Land",
      "UDI Improved Land",
      "Commercial Imps Only Land",
      "Downtown ROW",
      "Non-Usable Land in Flood Control Easement",
      "Condo Land",
      "Res Open Space/Retention Land",
    ]);

    const rawLandUse = String(props.landuse_desc || "").trim();
    const broadCategory = String(props.use_category || "").trim() || "Residential";
    const specificUse = CRYPTIC_HCAD_LANDUSE.has(rawLandUse) ? "" : rawLandUse;

    if (specificUse && specificUse.toLowerCase() !== broadCategory.toLowerCase()) {
      return [
        {
          label: specificUse,
          query: specificUse,
          headerLabel: `Land Use: ${specificUse}`,
          title: `Click to find '${specificUse}' & related structures across Houston`,
          isCategory: false,
        },
        {
          label: broadCategory,
          query: broadCategory,
          headerLabel: `Land Use Category: ${broadCategory}`,
          title: `Click to find all '${broadCategory}' structures across Houston`,
          isCategory: true,
        },
      ];
    }

    const singleUse = specificUse || broadCategory;
    return [
      {
        label: singleUse,
        query: singleUse,
        headerLabel: `Land Use: ${singleUse}`,
        title: `Click to find '${singleUse}' structures across Houston`,
        isCategory: false,
      },
    ];
  }

  _resolveArchitectChips(props = {}) {
    const raw = String(props.architect || "").trim();
    if (!raw || /^(?:unknown|none|n\/a|null|unrecorded|tbd)$/i.test(raw)) {
      return [];
    }
    const parts = raw
      .split(/\s*[;/]\s*/)
      .map((s) => s.trim())
      .filter(Boolean);

    const chips = [];
    for (const part of parts) {
      const cleaned = part.replace(/,\s*(?:architects?|builders?|bldr|consulting)\b.*$/i, "").trim();
      const m = cleaned.match(/^(.+?)\s*\(([^)]+)\)\s*$/);
      const core = m ? m[1].trim() : cleaned;
      const note = m ? m[2].trim() : "";
      if (!core) continue;
      chips.push({
        label: core,
        query: core,
        note,
        headerLabel: `Architect / Builder: ${core}`,
      });
    }
    return chips;
  }

  _mergeCuratedOverridesIntoSearchIndex() {
    const ovMap = (this.mapController && this.mapController.curatedOverrides) || {};
    const suppressedHcads = new Set();
    for (const [k, ov] of Object.entries(ovMap)) {
      if (ov && ov.suppress_only) {
        suppressedHcads.add(String(ov.hcad_num || k).trim());
      }
    }
    if (suppressedHcads.size > 0) {
      this.searchIndex = this.searchIndex.filter(
        (item) => !item.hcad_num || !suppressedHcads.has(String(item.hcad_num).trim())
      );
    }
    const existingByHcad = new Map();
    for (let i = 0; i < this.searchIndex.length; i++) {
      const item = this.searchIndex[i];
      if (item.hcad_num) {
        existingByHcad.set(String(item.hcad_num).trim(), i);
      }
    }

    for (const [hcad, ov] of Object.entries(ovMap)) {
      if (!ov || ov.suppress_only) continue;
      const existingIdx = existingByHcad.get(hcad);
      const existingItem = existingIdx !== undefined ? this.searchIndex[existingIdx] : null;
      let lon = ov.lon ?? ov.lng ?? existingItem?.lon ?? -95.38718;
      let lat = ov.lat ?? existingItem?.lat ?? 29.79175;
      if (ov.geometry && ov.geometry.coordinates && ov.geometry.coordinates[0]?.[0]) {
        const ring =
          ov.geometry.type === "Polygon"
            ? ov.geometry.coordinates[0]
            : ov.geometry.coordinates[0][0];
        if (ring && ring.length) {
          lon = ring[0][0];
          lat = ring[0][1];
        }
      }
      const ovStyle = String(ov.style || ov.bld_style || "").trim();
      const preserveExistingStyle =
        !ovStyle || ovStyle === "Historical / Architectural Structure";
      const mergedBldStyle = preserveExistingStyle
        ? existingItem?.bld_style || existingItem?.style || ovStyle
        : ovStyle;
      const mergedUseCategory =
        ov.use_category ||
        existingItem?.use_category ||
        (existingItem?.category &&
        [
          "Residential",
          "Civic / Institutional",
          "Commercial",
          "Multi-Family",
          "Industrial",
          "Vacant / Exempt",
        ].includes(existingItem.category)
          ? existingItem.category
          : "Residential");

      const primaryBldName =
        ov.building_name || ov.landmark_name || existingItem?.building_name || "";
      const rawAlt = ov.alt_names || existingItem?.alt_names || [];
      const altNames = Array.isArray(rawAlt)
        ? rawAlt.map((s) => String(s).trim()).filter(Boolean)
        : String(rawAlt)
            .split(/\s*\|\s*|\s*;\s*/)
            .map((s) => s.trim())
            .filter(Boolean);
      const akaSnippet =
        altNames.length > 0 ? `AKA: ${altNames.slice(0, 2).join(", ")} • ` : "";

      const primaryAddr = String(existingItem?.address || ov.address || "").trim();
      const rawAltAddrs = [
        ...(Array.isArray(existingItem?.alt_addresses) ? existingItem.alt_addresses : []),
        ...(Array.isArray(ov.alt_addresses) ? ov.alt_addresses : []),
        ov.address || "",
        existingItem?.address || "",
      ];
      const seenAddrLow = new Set(primaryAddr ? [primaryAddr.toLowerCase()] : []);
      const altAddresses = [];
      for (const a of rawAltAddrs) {
        const cleanA = String(a || "").trim();
        if (!cleanA) continue;
        const lowA = cleanA.toLowerCase();
        if (!seenAddrLow.has(lowA)) {
          seenAddrLow.add(lowA);
          altAddresses.push(cleanA);
        }
      }
      const allDispAddrs = [primaryAddr, ...altAddresses].filter(Boolean);
      const combinedAddrDisplay = allDispAddrs.slice(0, 2).join(" / ");
      const addrSnippet =
        primaryBldName && combinedAddrDisplay && combinedAddrDisplay.toLowerCase() !== primaryBldName.toLowerCase()
          ? `${combinedAddrDisplay} • `
          : !primaryBldName && altAddresses.length > 0
          ? `${combinedAddrDisplay} • `
          : "";

      const entry = {
        type: "building",
        id: ov.id || existingItem?.id || `ov_${hcad}`,
        hcad_num: hcad,
        label: primaryBldName || primaryAddr || ov.address || existingItem?.label || `HCAD ${hcad}`,
        address: primaryAddr || ov.address || "",
        ...(altAddresses.length > 0 ? { alt_addresses: altAddresses } : {}),
        building_name: primaryBldName,
        alt_names: altNames,
        name_source: ov.name_source || existingItem?.name_source || "",
        sublabel: `${akaSnippet}${addrSnippet}${ov.historic_district || ov.neighborhood || existingItem?.historic_district || existingItem?.neighborhood || mergedUseCategory || "Harris County"} • Built ${ov.year_built} (✓ PH Verified)`,
        category: `Built ${ov.year_built} ✓`,
        year_built: ov.year_built,
        architect: ov.architect || existingItem?.architect || "",
        style: ov.style || existingItem?.style || "",
        bld_style: mergedBldStyle,
        hcad_grade: ov.hcad_grade || existingItem?.hcad_grade || "",
        use_category: mergedUseCategory,
        landuse_desc: ov.landuse_desc || existingItem?.landuse_desc || "",
        historic_district: ov.historic_district || existingItem?.historic_district || "",
        neighborhood: ov.neighborhood || existingItem?.neighborhood || "",
        neighborhood_aliases:
          Array.isArray(ov.neighborhood_aliases) && ov.neighborhood_aliases.length
            ? ov.neighborhood_aliases
            : Array.isArray(existingItem?.neighborhood_aliases)
            ? existingItem.neighborhood_aliases
            : [],
        super_neighborhood: ov.super_neighborhood || existingItem?.super_neighborhood || "",
        historic_ward: ov.historic_ward || existingItem?.historic_ward || "",
        subdivision: ov.subdivision || existingItem?.subdivision || "",
        lon,
        lat,
        zoom: 17.6,
      };
      if (existingIdx !== undefined) {
        this.searchIndex[existingIdx] = {
          ...existingItem,
          ...entry,
        };
      } else {
        this.searchIndex.unshift(entry);
      }
    }
  }

  _enrichSearchIndexWithOverlayMetadata() {
    if (!this.mapController) return;
    const byHcad = new Map();
    const byLabel = new Map();
    for (let i = 0; i < this.searchIndex.length; i++) {
      const item = this.searchIndex[i];
      if (item.is_neighborhood_entry) {
        if (!item.label) item.label = item.name || item.building_name || "Houston Neighborhood";
        if (!Number.isFinite(item.lon) && Number.isFinite(item.lng)) item.lon = item.lng;
        if (item.overlay_layer === "historicWards") item.overlay_layer = "historic_wards";
        if (item.overlay_layer === "superNeighborhoods") item.overlay_layer = "super_neighborhoods";
        if (!item.sublabel) {
          const alts = Array.isArray(item.alt_names) ? item.alt_names.slice(0, 3).join(", ") : "";
          item.sublabel = [alts ? `AKA: ${alts}` : "", item.address || ""]
            .filter(Boolean)
            .join(" • ");
        }
      }
      if (item.hcad_num) byHcad.set(String(item.hcad_num).trim(), item);
      if (item.label) byLabel.set(String(item.label).trim().toLowerCase(), item);
    }

    // 1. Enrich from buildingsData (architect, bld_style, style, use_category, landuse_desc, historic_district, neighborhood, super_neighborhood, historic_ward, subdivision, good_brick_years, building_name, alt_names, address, alt_addresses, and geometry coords)
    for (const feat of this.mapController.buildingsData || []) {
      const p = feat.properties || {};
      const hcad = String(p.hcad_num || "").trim();
      const ring =
        feat.geometry?.type === "Polygon"
          ? feat.geometry.coordinates?.[0]
          : feat.geometry?.type === "MultiPolygon"
          ? feat.geometry.coordinates?.[0]?.[0]
          : null;
      const target =
        (hcad && byHcad.get(hcad)) ||
        (p.landmark_name && byLabel.get(String(p.landmark_name).toLowerCase()));
      if (target) {
        if (p.id && String(target.id || "").startsWith("ov_")) target.id = p.id;
        if (ring && ring[0] && (!target.lon || Math.abs(target.lon - -95.38718) < 0.0001)) {
          let sumLon = 0,
            sumLat = 0;
          for (const pt of ring) {
            sumLon += pt[0];
            sumLat += pt[1];
          }
          target.lon = sumLon / ring.length;
          target.lat = sumLat / ring.length;
        }
        if (p.address && !target.address) {
          target.address = p.address;
        } else if (
          p.address &&
          target.address &&
          p.address.toLowerCase() !== target.address.toLowerCase()
        ) {
          const existingAlts = Array.isArray(target.alt_addresses) ? target.alt_addresses : [];
          if (!existingAlts.some((a) => String(a).toLowerCase() === p.address.toLowerCase())) {
            target.alt_addresses = [...existingAlts, p.address];
          }
        }
        if (Array.isArray(p.alt_addresses) && p.alt_addresses.length > 0) {
          const existingAlts = Array.isArray(target.alt_addresses) ? target.alt_addresses : [];
          const mergedAlts = [...existingAlts];
          for (const a of p.alt_addresses) {
            if (
              a &&
              a.toLowerCase() !== String(target.address || "").toLowerCase() &&
              !mergedAlts.some((x) => String(x).toLowerCase() === a.toLowerCase())
            ) {
              mergedAlts.push(a);
            }
          }
          if (mergedAlts.length > 0) target.alt_addresses = mergedAlts;
        }
        if ((p.building_name || p.landmark_name) && !target.building_name) {
          target.building_name = p.building_name || p.landmark_name;
        }
        if (Array.isArray(p.alt_names) && p.alt_names.length > 0 && (!target.alt_names || !target.alt_names.length)) {
          target.alt_names = p.alt_names;
        }
        if (p.name_source && !target.name_source) target.name_source = p.name_source;
        if (p.architect && !target.architect) target.architect = p.architect;
        if (p.style && (!target.style || target.style === "Historical / Architectural Structure")) {
          target.style = p.style;
        }
        if (
          (p.bld_style || p.style) &&
          (!target.bld_style || target.bld_style === "Historical / Architectural Structure")
        ) {
          target.bld_style = p.bld_style || p.style;
        }
        if (p.hcad_grade && !target.hcad_grade) target.hcad_grade = p.hcad_grade;
        if (p.use_category && !target.use_category) target.use_category = p.use_category;
        if (p.landuse_desc && !target.landuse_desc) target.landuse_desc = p.landuse_desc;
        if (p.good_brick_years && !target.good_brick_years)
          target.good_brick_years = String(p.good_brick_years);
        if (p.historic_district && !target.historic_district)
          target.historic_district = p.historic_district;
        if (p.neighborhood && !target.neighborhood) target.neighborhood = p.neighborhood;
        if (
          Array.isArray(p.neighborhood_aliases) &&
          p.neighborhood_aliases.length > 0 &&
          (!target.neighborhood_aliases || !target.neighborhood_aliases.length)
        ) {
          target.neighborhood_aliases = p.neighborhood_aliases;
        }
        if (p.super_neighborhood && !target.super_neighborhood)
          target.super_neighborhood = p.super_neighborhood;
        if (p.historic_ward && !target.historic_ward) target.historic_ward = p.historic_ward;
        if (p.subdivision && !target.subdivision) target.subdivision = p.subdivision;
        if (p.landmark_code && !target.landmark_code) target.landmark_code = p.landmark_code;
        if (p.landmark_report_url && !target.landmark_report_url)
          target.landmark_report_url = p.landmark_report_url;
        if (p.landmark_summary && !target.landmark_summary)
          target.landmark_summary = p.landmark_summary;
      } else if (
        p.address ||
        p.architect ||
        p.building_name ||
        p.landmark_name ||
        p.good_brick_years ||
        p.landmark_report_url
      ) {
        let lon = -95.3698;
        let lat = 29.7604;
        if (ring && ring[0]) {
          [lon, lat] = ring[0];
        }
        const newEntry = {
          type: "building",
          id: p.id || "",
          hcad_num: hcad,
          label: p.building_name || p.landmark_name || p.address || `HCAD ${hcad}`,
          address: p.address || "",
          ...(Array.isArray(p.alt_addresses) && p.alt_addresses.length
            ? { alt_addresses: p.alt_addresses }
            : {}),
          building_name: p.building_name || p.landmark_name || "",
          alt_names: Array.isArray(p.alt_names) ? p.alt_names : [],
          name_source: p.name_source || "",
          sublabel: [
            Array.isArray(p.alt_names) && p.alt_names.length
              ? `AKA: ${p.alt_names.slice(0, 2).join(", ")}`
              : "",
            p.address,
            p.historic_district || p.neighborhood,
            p.architect ? `Arch: ${p.architect}` : "",
          ]
            .filter(Boolean)
            .join(" • "),
          category: p.year_built ? `Built ${p.year_built}` : p.use_category || "Historic Structure",
          year_built: p.year_built || 0,
          architect: p.architect || "",
          style: p.style || "",
          bld_style: p.bld_style || p.style || "",
          hcad_grade: p.hcad_grade || "",
          use_category: p.use_category || "Residential",
          landuse_desc: p.landuse_desc || "",
          good_brick_years: p.good_brick_years ? String(p.good_brick_years) : "",
          historic_district: p.historic_district || "",
          neighborhood: p.neighborhood || "",
          neighborhood_aliases: Array.isArray(p.neighborhood_aliases) ? p.neighborhood_aliases : [],
          super_neighborhood: p.super_neighborhood || "",
          historic_ward: p.historic_ward || "",
          subdivision: p.subdivision || "",
          landmark_code: p.landmark_code || "",
          landmark_report_url: p.landmark_report_url || "",
          landmark_summary: p.landmark_summary || "",
          lon,
          lat,
          zoom: 17.4,
        };
        this.searchIndex.push(newEntry);
        if (hcad) byHcad.set(hcad, newEntry);
      }
    }

    // 2. Enrich from overlaysData.landmarks & overlaysData.good_brick_awards
    const landmarks = this.mapController.overlaysData?.landmarks?.features || [];
    for (const feat of landmarks) {
      const p = feat.properties || {};
      const hcad = String(p.hcad_num || "").trim();
      const lmCode = p.plm_num || p.lm_num || "";
      const target =
        (hcad && byHcad.get(hcad)) ||
        (p.name && byLabel.get(String(p.name).toLowerCase()));
      if (target) {
        if ((p.building_name || p.name) && !target.building_name) {
          target.building_name = p.building_name || p.name;
        }
        if (Array.isArray(p.alt_names) && p.alt_names.length > 0 && (!target.alt_names || !target.alt_names.length)) {
          target.alt_names = p.alt_names;
        }
        if (p.architect && !target.architect) target.architect = p.architect;
        if (p.style && !target.bld_style) target.bld_style = p.style;
        if (lmCode && !target.landmark_code) target.landmark_code = lmCode;
        if (p.report_pdf_url && !target.landmark_report_url) target.landmark_report_url = p.report_pdf_url;
        if (p.pdf_summary && !target.landmark_summary) target.landmark_summary = p.pdf_summary;
      } else if (feat.geometry?.coordinates) {
        const [lon, lat] = feat.geometry.coordinates;
        this.searchIndex.push({
          type: "landmark",
          id: p.id || "",
          hcad_num: hcad,
          label: p.building_name || p.name || p.address || "Houston Landmark",
          address: p.address || "",
          ...(Array.isArray(p.alt_addresses) && p.alt_addresses.length
            ? { alt_addresses: p.alt_addresses }
            : {}),
          building_name: p.building_name || p.name || "",
          alt_names: Array.isArray(p.alt_names) ? p.alt_names : [],
          sublabel: [
            Array.isArray(p.alt_names) && p.alt_names.length
              ? `AKA: ${p.alt_names.slice(0, 2).join(", ")}`
              : "",
            p.address,
            lmCode ? `HPO #${lmCode}` : "",
            p.style,
            p.architect ? `Arch: ${p.architect}` : "",
          ]
            .filter(Boolean)
            .join(" • "),
          category: p.designation || "Landmark",
          year_built: p.year_built || 0,
          architect: p.architect || "",
          bld_style: p.style || "",
          landmark_code: lmCode,
          landmark_report_url: p.report_pdf_url || "",
          landmark_summary: p.pdf_summary || "",
          lon,
          lat,
          zoom: 17.5,
        });
      }
    }

    const gbAwards = this.mapController.overlaysData?.good_brick_awards?.features || [];
    for (const feat of gbAwards) {
      const p = feat.properties || {};
      const hcad = String(p.hcad_num || "").trim();
      const target =
        (hcad && byHcad.get(hcad)) ||
        (p.landmark_name && byLabel.get(String(p.landmark_name).toLowerCase()));
      if (target) {
        if ((p.building_name || p.landmark_name) && !target.building_name) {
          target.building_name = p.building_name || p.landmark_name;
        }
        if (Array.isArray(p.alt_names) && p.alt_names.length > 0 && (!target.alt_names || !target.alt_names.length)) {
          target.alt_names = p.alt_names;
        }
        if (p.good_brick_years) target.good_brick_years = String(p.good_brick_years);
        if (p.good_brick_summary && !String(target.sublabel || "").includes("Good Brick")) {
          target.sublabel = `${target.sublabel || ""} • ★ ${p.good_brick_summary}`;
        }
      } else if (feat.geometry?.coordinates) {
        const [lon, lat] = feat.geometry.coordinates;
        this.searchIndex.push({
          type: "good_brick",
          id: p.building_id || p.id || "",
          hcad_num: hcad,
          label: p.building_name || p.landmark_name || p.address || "Good Brick Winner",
          building_name: p.building_name || p.landmark_name || "",
          alt_names: Array.isArray(p.alt_names) ? p.alt_names : [],
          sublabel: [
            Array.isArray(p.alt_names) && p.alt_names.length
              ? `AKA: ${p.alt_names.slice(0, 2).join(", ")}`
              : "",
            p.address || "",
            `★ ${p.good_brick_summary || "Good Brick Award"}`,
          ]
            .filter(Boolean)
            .join(" • "),
          category: `★ ${p.good_brick_years || "Good Brick"}`,
          year_built: p.year_built || 0,
          good_brick_years: String(p.good_brick_years || ""),
          lon,
          lat,
          zoom: 17.5,
        });
      }
    }

    for (let i = 0; i < this.searchIndex.length; i++) {
      this.searchIndex[i]._lc = null;
    }
    this._scheduleSearchIndexPrewarm();
  }

  _getSearchLowercaseCache(item) {
    if (item._lc) return item._lc;
    const rawKey = String(item.id || item.hcad_num || item.label || "").trim();
    const key = rawKey.replace(/#fp_\d+$/, "");
    const hcadKey = String(item.hcad_num || "").trim();
    const lbl = String(item.label || "").toLowerCase();
    const placeName = String(item.name || item.label || "").toLowerCase();
    const bldName = String(item.building_name || "").toLowerCase();
    const altNames = Array.isArray(item.alt_names)
      ? item.alt_names.map((s) => String(s).toLowerCase())
      : [];
    const addr = String(item.address || "").toLowerCase();
    const altAddrs = Array.isArray(item.alt_addresses)
      ? item.alt_addresses.map((s) => String(s).toLowerCase())
      : [];
    const sub = String(item.sublabel || "").toLowerCase();
    const hcad = hcadKey.toLowerCase();
    const arch = String(item.architect || "").toLowerCase();
    const style = String(item.style || "").toLowerCase();
    const bldStyle = String(item.bld_style || "").toLowerCase();
    const useCat = String(item.use_category || "").toLowerCase();
    const landuse = String(item.landuse_desc || "").toLowerCase();
    const cat = String(item.category || "").toLowerCase();
    const grade = String(item.hcad_grade || "").toLowerCase();
    const gbYrs = String(item.good_brick_years || "").toLowerCase();
    const dist = String(item.historic_district || "").toLowerCase();
    const nh = String(item.neighborhood || "").toLowerCase();
    const nhAliases = Array.isArray(item.neighborhood_aliases)
      ? item.neighborhood_aliases.map((s) => String(s).toLowerCase())
      : [];
    const superNh = String(item.super_neighborhood || "").toLowerCase();
    const ward = String(item.historic_ward || "").toLowerCase();
    const subdiv = String(item.subdivision || "").toLowerCase();
    const topSubdivs = Array.isArray(item.top_subdivisions)
      ? item.top_subdivisions.map((s) => String(s).toLowerCase())
      : [];
    const lmCode = String(item.landmark_code || "").toLowerCase();
    const isPlaceBoundary = Boolean(
      item.is_neighborhood_entry ||
        item.type === "neighborhood" ||
        item.type === "platted_subdivision" ||
        item.type === "super_neighborhood" ||
        item.type === "historic_ward" ||
        item.type === "historical_waterway" ||
        item.type === "historical_railroad"
    );
    const hasCustomName = Boolean(
      item.label &&
        !/^\d+\s+/.test(String(item.label).trim()) &&
        !String(item.label).startsWith("HCAD ")
    );
    const itemId = String(item.id || "").trim().toLowerCase();
    const allText = [
      lbl,
      placeName !== lbl ? placeName : "",
      bldName,
      altNames.join(" "),
      addr,
      altAddrs.join(" "),
      sub,
      hcad,
      arch,
      style,
      bldStyle !== style ? bldStyle : "",
      useCat,
      landuse,
      cat,
      grade,
      gbYrs,
      dist,
      nh,
      nhAliases.join(" "),
      superNh,
      ward,
      subdiv,
      topSubdivs.join(" "),
      lmCode ? `${lmCode} hpo #${lmCode}` : "",
    ]
      .filter(Boolean)
      .join(" | ");

    const lc = {
      key,
      hcadKey,
      itemId,
      hasCustomName,
      placeName,
      lbl,
      bldName,
      altNames,
      addr,
      altAddrs,
      sub,
      hcad,
      arch,
      style,
      bldStyle,
      useCat,
      landuse,
      cat,
      grade,
      gbYrs,
      dist,
      nh,
      nhAliases,
      superNh,
      ward,
      subdiv,
      topSubdivs,
      lmCode,
      isPlaceBoundary,
      allText,
    };
    item._lc = lc;
    return lc;
  }

  _scheduleSearchIndexPrewarm() {
    if (this._searchPrewarmTimer) {
      clearTimeout(this._searchPrewarmTimer);
      this._searchPrewarmTimer = null;
    }
    let idx = 0;
    const chunk = () => {
      const list = this.searchIndex || [];
      const end = Math.min(idx + 3000, list.length);
      for (; idx < end; idx++) {
        this._getSearchLowercaseCache(list[idx]);
      }
      if (idx < list.length) {
        this._searchPrewarmTimer = setTimeout(chunk, 12);
      } else {
        this._searchPrewarmTimer = null;
      }
    };
    this._searchPrewarmTimer = setTimeout(chunk, 60);
  }

  _renderGlobalDatasetSummary() {
    if (!this.globalStats) return;
    const totalEl = document.getElementById("stat-dataset-total");
    if (totalEl) {
      totalEl.textContent = `${Number(this.globalStats.total_buildings || 0).toLocaleString()} structures`;
    }
  }

  _bindControls() {
    // Color Lens tabs
    document.querySelectorAll("[data-color-mode]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const mode = btn.getAttribute("data-color-mode");
        this.filterStore.setState({ colorMode: mode });
      });
    });

    // Palette style toggle (Archival vs Classic Earth Engine YlOrRd)
    const paletteSelect = document.getElementById("select-palette-style");
    if (paletteSelect) {
      paletteSelect.addEventListener("change", (e) => {
        this.filterStore.setState({ paletteStyle: e.target.value });
      });
    }

    // Geometry Render Mode ('buildings' | 'both' | 'parcels')
    document.querySelectorAll("[data-render-mode]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const mode = btn.getAttribute("data-render-mode");
        this.filterStore.setState({ renderMode: mode });
      });
    });

    // Basemap selector
    document.querySelectorAll("[data-basemap]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const base = btn.getAttribute("data-basemap");
        this.filterStore.setState({ basemap: base });
      });
    });

    // 3D Extrusion Toggle
    const btn3d = document.getElementById("btn-toggle-3d");
    if (btn3d) {
      btn3d.addEventListener("click", () => {
        const curState = this.filterStore.getState();
        const next3D = !curState.extrude3D;
        const patch = { extrude3D: next3D };
        if (next3D && curState.renderMode === "none") {
          patch.renderMode = curState.lastActiveRenderMode || "buildings";
        }
        this.filterStore.setState(patch);
        this.mapController.toggle3DPitch(next3D);
      });
    }

    // Bottom-Right Camera Tilt Preset Buttons (2D / 30° / 50° / 65°)
    document.querySelectorAll("[data-tilt-pitch]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const targetPitch = parseInt(btn.getAttribute("data-tilt-pitch"), 10) || 0;
        const enable3D = targetPitch > 0;
        const curState = this.filterStore.getState();
        const patch = { extrude3D: enable3D };
        if (enable3D && curState.renderMode === "none") {
          patch.renderMode = curState.lastActiveRenderMode || "buildings";
        }
        this.filterStore.setState(patch);
        this.mapController.setCameraPitch(targetPitch, enable3D ? null : 0);
      });
    });

    // Min / Max Year Sliders
    const minSlider = document.getElementById("slider-min-year");
    const maxSlider = document.getElementById("slider-max-year");
    if (minSlider) {
      minSlider.addEventListener("input", (e) => {
        const val = parseInt(e.target.value, 10) || 1836;
        const currMax = this.filterStore.getState().maxYear;
        this.filterStore.setState({
          minYear: Math.min(val, currMax),
          selectedDecade: "all",
          isPlaying: false,
        });
      });
    }
    if (maxSlider) {
      maxSlider.addEventListener("input", (e) => {
        const val = parseInt(e.target.value, 10) || 2026;
        const currMin = this.filterStore.getState().minYear;
        this.filterStore.setState({
          maxYear: Math.max(val, currMin),
          selectedDecade: "all",
          isPlaying: false,
        });
      });
    }

    // Decade Selector Dropdown
    const decadeSelect = document.getElementById("select-decade");
    if (decadeSelect) {
      decadeSelect.addEventListener("change", (e) => {
        const val = e.target.value;
        if (val === "all" || val === "unknown") {
          this.filterStore.setState({
            selectedDecade: val,
            minYear: 1836,
            maxYear: 2026,
            isPlaying: false,
          });
        } else {
          const decInt = parseInt(val, 10);
          this.filterStore.setState({
            selectedDecade: val,
            minYear: decInt === 1840 ? 1836 : decInt,
            maxYear: Math.min(2026, decInt + 9),
            stepYears: 10,
            isPlaying: false,
          });
        }
      });
    }

    // Show Unknown Years Checkbox
    const chkUnknown = document.getElementById("chk-show-unknown");
    if (chkUnknown) {
      chkUnknown.addEventListener("change", (e) => {
        this.filterStore.setState({ showUnknownYears: e.target.checked });
      });
    }

    // Sync Annexation Boundary to Time-Lapse Checkbox
    const chkSyncAnnex = document.getElementById("chk-sync-annexation");
    if (chkSyncAnnex) {
      chkSyncAnnex.addEventListener("change", (e) => {
        this.filterStore.setLayerVisibility("annexations", Boolean(e.target.checked));
      });
    }

    // Time-Lapse Play / Pause Button
    const btnPlay = document.getElementById("btn-timelapse-play");
    if (btnPlay) {
      btnPlay.addEventListener("click", () => {
        const state = this.filterStore.getState();
        if (!state.isPlaying) {
          const startMax = state.maxYear >= 2025 ? 1850 : state.maxYear;
          this.filterStore.setState({
            isPlaying: true,
            selectedDecade: "all",
            minYear: 1836,
            maxYear: startMax,
            showUnknownYears: false,
          });
        } else {
          this.filterStore.setState({ isPlaying: false });
        }
      });
    }

    // Time-Lapse Speed Selector
    const speedSelect = document.getElementById("select-timelapse-speed");
    if (speedSelect) {
      speedSelect.addEventListener("change", (e) => {
        this.filterStore.setState({ playSpeed: Number(e.target.value) || 1 });
      });
    }

    // Time-Travel Stepper (1 yr, 5 yrs, 10 yrs + Prev/Next Buttons + Arrow Keys)
    const btnStepPrev = document.getElementById("btn-step-prev");
    const btnStepNext = document.getElementById("btn-step-next");
    const flashStepButton = (btn) => {
      if (!btn) return;
      btn.classList.add("flash-active");
      setTimeout(() => btn.classList.remove("flash-active"), 150);
    };

    if (btnStepPrev) {
      btnStepPrev.addEventListener("click", () => {
        this.filterStore.stepTime(-1);
      });
    }
    if (btnStepNext) {
      btnStepNext.addEventListener("click", () => {
        this.filterStore.stepTime(1);
      });
    }

    document.querySelectorAll("[data-step-years]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const yrs = parseInt(btn.getAttribute("data-step-years"), 10) || 5;
        this.filterStore.setState({ stepYears: yrs });
      });
    });

    window.addEventListener(
      "keydown",
      (e) => {
        if (e.metaKey || e.ctrlKey || e.altKey) return;
        const active = document.activeElement;
        if (active) {
          const tag = (active.tagName || "").toUpperCase();
          const isTextInput =
            (tag === "INPUT" && !["range", "checkbox", "button"].includes(active.type)) ||
            tag === "TEXTAREA" ||
            active.isContentEditable;
          if (isTextInput) return;
        }

        if (e.key === "ArrowLeft") {
          e.preventDefault();
          e.stopPropagation();
          flashStepButton(btnStepPrev);
          this.filterStore.stepTime(-1);
        } else if (e.key === "ArrowRight") {
          e.preventDefault();
          e.stopPropagation();
          flashStepButton(btnStepNext);
          this.filterStore.stepTime(1);
        } else if (
          e.key === " " &&
          (!active || !["BUTTON", "SELECT", "INPUT", "A"].includes((active.tagName || "").toUpperCase()))
        ) {
          e.preventDefault();
          e.stopPropagation();
          if (btnPlay) btnPlay.click();
        }
      },
      { capture: true }
    );

    // Reset Filters Button
    const btnReset = document.getElementById("btn-reset-filters");
    if (btnReset) {
      btnReset.addEventListener("click", () => {
        this.filterStore.resetFilters();
      });
    }

    // Master Building Footprints Layer Toggle & Solo Button
    const chkBuildings = document.getElementById("chk-layer-buildings");
    if (chkBuildings) {
      chkBuildings.addEventListener("change", (e) => {
        this.filterStore.toggleBuildingsLayer(e.target.checked);
      });
    }
    const btnSoloBuildings = document.getElementById("btn-solo-buildings");
    if (btnSoloBuildings) {
      btnSoloBuildings.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        this.filterStore.soloBuildingsOnly();
      });
    }

    // Single-Layer Mode (1-at-a-Time) & All / None Quick Actions
    const btnSingleLayerMode = document.getElementById("btn-single-layer-mode");
    if (btnSingleLayerMode) {
      btnSingleLayerMode.addEventListener("click", () => {
        this.filterStore.toggleSingleLayerMode();
      });
    }
    const btnOverlaysAll = document.getElementById("btn-overlays-all");
    if (btnOverlaysAll) {
      btnOverlaysAll.addEventListener("click", () => {
        this.filterStore.setAllOverlays(true);
      });
    }
    const btnOverlaysNone = document.getElementById("btn-overlays-none");
    if (btnOverlaysNone) {
      btnOverlaysNone.addEventListener("click", () => {
        this.filterStore.setAllOverlays(false);
      });
    }

    // Overlay Layer Toggles & Per-Row Solo Buttons
    const layerCheckboxes = [
      ["chk-layer-good-brick", "goodBrickAwards"],
      ["chk-layer-landmarks", "landmarks"],
      ["chk-layer-historic-districts", "historicDistricts"],
      ["chk-layer-heritage-districts", "heritageDistricts"],
      ["chk-layer-nrhp-districts", "nrhpDistricts"],
      ["chk-layer-neighborhoods", "neighborhoods"],
      ["chk-layer-platted-subdivisions", "plattedSubdivisions"],
      ["chk-layer-land-use-protections", "landUseProtections"],
      ["chk-layer-super-neighborhoods", "superNeighborhoods"],
      ["chk-layer-historic-wards", "historicWards"],
      ["chk-layer-thc-markers", "thcMarkers"],
      ["chk-layer-annexations", "annexations"],
      ["chk-layer-waterways", "historicalWaterways"],
      ["chk-layer-railroads", "historicalRailroads"],
      ["chk-layer-historic-map", "historicMap"],
    ];
    for (const [domId, layerKey] of layerCheckboxes) {
      const el = document.getElementById(domId);
      if (el) {
        el.addEventListener("change", (e) => {
          this.filterStore.setLayerVisibility(layerKey, e.target.checked);
        });
      }
    }

    document.querySelectorAll("[data-ward-era]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const eraYr = parseInt(btn.getAttribute("data-ward-era"), 10) || 1903;
        const curLayers = this.filterStore.getState().layers || {};
        this.filterStore.setState({
          wardEra: eraYr,
          layers: {
            ...curLayers,
            historicWards: true,
          },
        });
      });
    });

    const chkAnnexSpokes = document.getElementById("chk-annexation-spokes");
    if (chkAnnexSpokes) {
      chkAnnexSpokes.addEventListener("change", (e) => {
        this.filterStore.setState({ showAnnexationSpokes: Boolean(e.target.checked) });
      });
    }

    const histOpacitySlider = document.getElementById("slider-historic-map-opacity");
    if (histOpacitySlider) {
      histOpacitySlider.addEventListener("input", (e) => {
        const val = Math.max(15, Math.min(100, parseInt(e.target.value, 10) || 75));
        const readout = document.getElementById("historic-map-opacity-readout");
        if (readout) readout.textContent = `${val}%`;
        this.filterStore.setState({ historicMapOpacity: val });
      });
    }

    document.querySelectorAll("[data-solo-layer]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const layerKey = btn.getAttribute("data-solo-layer");
        if (layerKey) {
          this.filterStore.soloOverlayLayer(layerKey);
        }
      });
    });

    // Search Input & Autocomplete
    const searchInput = document.getElementById("search-input");
    const searchResults = document.getElementById("search-results");
    if (searchInput && searchResults) {
      let searchDebounceTimer = null;
      searchInput.addEventListener("input", (e) => {
        const val = String(e.target.value || "");
        if (searchDebounceTimer) {
          clearTimeout(searchDebounceTimer);
          searchDebounceTimer = null;
        }
        if (val.trim().length < 2) {
          searchResults.classList.add("hidden");
          searchResults.innerHTML = "";
          return;
        }
        searchDebounceTimer = setTimeout(() => {
          searchDebounceTimer = null;
          this._runSearchQuery(val, "");
        }, 55);
      });

      document.addEventListener("click", (e) => {
        const isFilterChip = e.target.closest && e.target.closest("[data-filter-chip]");
        if (!searchInput.contains(e.target) && !searchResults.contains(e.target) && !isFilterChip) {
          searchResults.classList.add("hidden");
        }
      });
    }

    // Printable Archival Property Dossier Modal
    const dossierModal = document.getElementById("dossier-modal");
    const btnCloseDossierModal = document.getElementById("btn-close-dossier-modal");
    const btnPrintDossierNow = document.getElementById("btn-print-dossier-now");
    if (btnCloseDossierModal && dossierModal) {
      btnCloseDossierModal.addEventListener("click", () => dossierModal.classList.add("hidden"));
    }
    if (dossierModal) {
      dossierModal.addEventListener("click", (e) => {
        if (e.target === dossierModal) dossierModal.classList.add("hidden");
      });
    }
    if (btnPrintDossierNow) {
      btnPrintDossierNow.addEventListener("click", () => {
        window.print();
      });
    }

    // Inspector Drawer Close Button
    const btnCloseInspector = document.getElementById("btn-close-inspector");
    if (btnCloseInspector) {
      btnCloseInspector.addEventListener("click", () => {
        const drawer = document.getElementById("inspector-drawer");
        if (drawer) drawer.classList.add("hidden");
        this.mapController.clearSelection();
        this._updateUrlHash(this.filterStore.getState());
        this._refreshShareModalContent();
      });
    }

    // Share Map Configuration Modal
    const shareModal = document.getElementById("share-modal");
    const btnOpenShareModal = document.getElementById("btn-open-share-modal");
    const btnShareLayersQuick = document.getElementById("btn-share-layers-quick");
    const btnCloseShareModal = document.getElementById("btn-close-share-modal");

    if (btnOpenShareModal) {
      btnOpenShareModal.addEventListener("click", () => this.openShareModal());
    }
    if (btnShareLayersQuick) {
      btnShareLayersQuick.addEventListener("click", () => this.openShareModal());
    }
    if (btnCloseShareModal && shareModal) {
      btnCloseShareModal.addEventListener("click", () => shareModal.classList.add("hidden"));
    }
    if (shareModal) {
      shareModal.addEventListener("click", (e) => {
        if (e.target === shareModal) shareModal.classList.add("hidden");
      });
    }

    for (const chkId of [
      "chk-share-include-viewport",
      "chk-share-include-selection",
      "chk-share-collapse-sidebar",
    ]) {
      const chkEl = document.getElementById(chkId);
      if (chkEl) {
        chkEl.addEventListener("change", () => this._refreshShareModalContent());
      }
    }

    const btnCopyShareUrl = document.getElementById("btn-copy-share-modal-url");
    if (btnCopyShareUrl) {
      btnCopyShareUrl.addEventListener("click", () => {
        const inputEl = document.getElementById("share-url-input");
        const feedbackEl = document.getElementById("share-copy-feedback");
        const urlToCopy = inputEl ? inputEl.value : window.location.href;
        if (navigator.clipboard) {
          navigator.clipboard.writeText(urlToCopy);
        }
        if (inputEl) inputEl.select();
        btnCopyShareUrl.textContent = "✓ Copied!";
        if (feedbackEl) feedbackEl.textContent = "✓ Hyperlink copied to clipboard";
        setTimeout(() => {
          btnCopyShareUrl.innerHTML = "&#128279; Copy Link";
          if (feedbackEl) feedbackEl.textContent = "";
        }, 2200);
      });
    }

    const btnCopyEmbed = document.getElementById("btn-copy-share-embed");
    if (btnCopyEmbed) {
      btnCopyEmbed.addEventListener("click", () => {
        const embedEl = document.getElementById("share-embed-input");
        const feedbackEl = document.getElementById("share-copy-feedback");
        const code = embedEl ? embedEl.value : "";
        if (code && navigator.clipboard) {
          navigator.clipboard.writeText(code);
        }
        if (embedEl) embedEl.select();
        btnCopyEmbed.textContent = "✓ Copied!";
        if (feedbackEl) feedbackEl.textContent = "✓ Embed <iframe> copied to clipboard";
        setTimeout(() => {
          btnCopyEmbed.textContent = "Copy Embed";
          if (feedbackEl) feedbackEl.textContent = "";
        }, 2200);
      });
    }

    this._renderSharePresetsGrid();
    this._bindExportStudioControls();

    // About / Methodology Modal
    const btnOpenModal = document.getElementById("btn-open-about-modal");
    const btnCloseModal = document.getElementById("btn-close-about-modal");
    const aboutModal = document.getElementById("about-modal");
    if (btnOpenModal && aboutModal) {
      btnOpenModal.addEventListener("click", () => aboutModal.classList.remove("hidden"));
    }
    if (btnCloseModal && aboutModal) {
      btnCloseModal.addEventListener("click", () => aboutModal.classList.add("hidden"));
    }
    if (aboutModal) {
      aboutModal.addEventListener("click", (e) => {
        if (e.target === aboutModal) aboutModal.classList.add("hidden");
      });
    }

    // Full-Screen Photograph Lightbox Modal
    const lightboxModal = document.getElementById("photo-lightbox-modal");
    const btnCloseLightbox = document.getElementById("btn-close-photo-lightbox");
    const btnLightboxCompare = document.getElementById("btn-lightbox-toggle-compare");
    if (btnCloseLightbox && lightboxModal) {
      btnCloseLightbox.addEventListener("click", () => lightboxModal.classList.add("hidden"));
    }
    if (lightboxModal) {
      lightboxModal.addEventListener("click", (e) => {
        if (e.target === lightboxModal) lightboxModal.classList.add("hidden");
      });
    }
    if (btnLightboxCompare) {
      btnLightboxCompare.addEventListener("click", () => {
        if (!this._activePhotoState || this._activePhotoState.photos.length < 2) return;
        this._activePhotoState.compareMode = !this._activePhotoState.compareMode;
        this._syncPhotoViews();
      });
    }

    // Suggest a Property Data Correction Modal & Staff Admin Gate
    const corrModal = document.getElementById("correction-modal");
    const btnCloseCorrModal = document.getElementById("btn-close-correction-modal");
    const corrForm = document.getElementById("form-property-correction");
    const btnExportPendingCsv = document.getElementById("btn-export-pending-csv");
    const btnSaveSheetConfig = document.getElementById("btn-save-sheet-config");
    const btnToggleAdminAuth = document.getElementById("btn-toggle-admin-auth");
    const btnTopbarAdmin = document.getElementById("btn-topbar-admin");
    const adminAuthPanel = document.getElementById("admin-auth-panel");
    const btnVerifyAdminUnlock = document.getElementById("btn-verify-admin-unlock");
    const btnLockAdminSession = document.getElementById("btn-lock-admin-session");
    const btnAdminApproveDirect = document.getElementById("btn-admin-approve-direct");

    const syncAdminUiState = () => {
      const session = getAdminSession();
      const isUnlocked = Boolean(session && session.authorized);
      const adminSheetDetails = document.getElementById("admin-sheet-details");
      const feedbackText = document.getElementById("admin-auth-feedback-text");

      if (btnToggleAdminAuth) {
        btnToggleAdminAuth.innerHTML = isUnlocked
          ? `&#128275; Admin: ${session.email}`
          : `&#128274; Staff Admin`;
        btnToggleAdminAuth.classList.toggle("active", isUnlocked);
      }
      if (btnTopbarAdmin) {
        btnTopbarAdmin.innerHTML = isUnlocked
          ? `&#128275; Admin: ${session.email}`
          : `&#128274; Staff Admin`;
        btnTopbarAdmin.classList.toggle("active", isUnlocked);
      }
      if (btnAdminApproveDirect) {
        btnAdminApproveDirect.classList.toggle("hidden", !isUnlocked);
      }
      if (btnExportPendingCsv) {
        btnExportPendingCsv.classList.toggle("hidden", !isUnlocked);
      }
      if (adminSheetDetails) {
        adminSheetDetails.classList.toggle("hidden", !isUnlocked);
      }
      if (btnLockAdminSession) {
        btnLockAdminSession.classList.toggle("hidden", !isUnlocked);
      }
      if (feedbackText && isUnlocked) {
        feedbackText.textContent = `✓ Unlocked (${session.email})`;
      }
    };

    syncAdminUiState();

    if (btnTopbarAdmin && corrModal && adminAuthPanel) {
      btnTopbarAdmin.addEventListener("click", () => {
        const csvInput = document.getElementById("admin-sheet-csv-url");
        const webhookInput = document.getElementById("admin-webhook-url");
        const syncStatus = this.mapController?.sheetSyncStatus;
        if (csvInput && syncStatus?.csvUrl) csvInput.value = syncStatus.csvUrl;
        if (webhookInput && syncStatus?.webhookUrl) webhookInput.value = syncStatus.webhookUrl;

        corrModal.classList.remove("hidden");
        adminAuthPanel.classList.remove("hidden");
        syncAdminUiState();
        const emailField = document.getElementById("admin-auth-email");
        if (emailField && !getAdminSession()) emailField.focus();
      });
    }

    if (btnToggleAdminAuth && adminAuthPanel) {
      btnToggleAdminAuth.addEventListener("click", () => {
        const csvInput = document.getElementById("admin-sheet-csv-url");
        const webhookInput = document.getElementById("admin-webhook-url");
        const syncStatus = this.mapController?.sheetSyncStatus;
        if (csvInput && syncStatus?.csvUrl) csvInput.value = syncStatus.csvUrl;
        if (webhookInput && syncStatus?.webhookUrl) webhookInput.value = syncStatus.webhookUrl;

        adminAuthPanel.classList.toggle("hidden");
        syncAdminUiState();

        if (!adminAuthPanel.classList.contains("hidden")) {
          const modalBody = corrModal?.querySelector(".modal-body");
          if (modalBody) modalBody.scrollTop = 0;
          const emailField = document.getElementById("admin-auth-email");
          if (emailField && !getAdminSession()) emailField.focus();
        }
      });
    }

    if (btnVerifyAdminUnlock) {
      const triggerAdminVerify = async () => {
        const emailVal = document.getElementById("admin-auth-email")?.value || "";
        const passkeyVal = document.getElementById("admin-auth-passkey")?.value || "";
        const feedbackText = document.getElementById("admin-auth-feedback-text");
        const webhookUrl =
          document.getElementById("admin-webhook-url")?.value ||
          this.mapController?.sheetSyncStatus?.webhookUrl ||
          "";

        if (feedbackText) feedbackText.textContent = "Verifying Sheet Editor access...";
        const isJwt = passkeyVal.split(".").length === 3 && passkeyVal.length > 80;
        const res = await authenticateAdminSession({
          googleIdToken: isJwt ? passkeyVal : "",
          adminPasskey: isJwt ? "" : passkeyVal,
          adminEmail: emailVal,
          webhookUrl,
        });

        if (!res.ok) {
          if (feedbackText) feedbackText.textContent = `✕ ${res.error}`;
          return;
        }
        syncAdminUiState();
      };

      btnVerifyAdminUnlock.addEventListener("click", triggerAdminVerify);

      for (const inputId of ["admin-auth-email", "admin-auth-passkey"]) {
        const inputEl = document.getElementById(inputId);
        if (inputEl) {
          inputEl.addEventListener("keydown", (e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              triggerAdminVerify();
            }
          });
        }
      }
    }

    if (btnLockAdminSession) {
      btnLockAdminSession.addEventListener("click", () => {
        clearAdminSession();
        const feedbackText = document.getElementById("admin-auth-feedback-text");
        if (feedbackText) feedbackText.textContent = "Admin Mode locked.";
        syncAdminUiState();
      });
    }

    if (btnAdminApproveDirect) {
      btnAdminApproveDirect.addEventListener("click", async () => {
        const feedbackEl = document.getElementById("corr-submit-feedback");
        const payload = {
          address: document.getElementById("corr-address")?.value || "",
          hcad_num: document.getElementById("corr-hcad-num")?.value || "",
          current_year_built: document.getElementById("corr-current-year")?.value || "",
          suggested_year_built: document.getElementById("corr-suggested-year")?.value || "",
          historic_district: document.getElementById("corr-district")?.value || "",
          source_type: document.getElementById("corr-source-type")?.value || "Houston City Directory",
          architect: document.getElementById("corr-style-arch")?.value || "",
          source_citation: document.getElementById("corr-citation")?.value || "",
          photo_url: document.getElementById("corr-photo-url")?.value || "",
          photo_year: document.getElementById("corr-photo-year")?.value || "",
          photo_caption: document.getElementById("corr-photo-caption")?.value || "",
        };

        if (!payload.suggested_year_built) {
          if (feedbackEl) {
            feedbackEl.innerHTML = `<strong>Please enter a Corrected Year Built (1836–2026) before approving.</strong>`;
            feedbackEl.classList.remove("hidden");
          }
          return;
        }

        const webhookUrl =
          document.getElementById("admin-webhook-url")?.value ||
          this.mapController?.sheetSyncStatus?.webhookUrl ||
          "";

        try {
          btnAdminApproveDirect.disabled = true;
          const res = await submitAdminApprovedOverride(payload, webhookUrl);
          btnAdminApproveDirect.disabled = false;

          if (payload.photo_url) {
            registerSessionPhoto({
              hcad_num: payload.hcad_num,
              building_id: payload.hcad_num,
              landmark_name: payload.address,
              photo_url: payload.photo_url,
              photo_year: payload.photo_year,
              photo_caption: payload.photo_caption || payload.source_citation,
              credit: "Preservation Houston Verified Archival Photo",
              source_url: payload.photo_url,
            });
          }

          if (res.ok && res.override && (res.overrideKey || res.override.hcad_num)) {
            const ovKey = res.overrideKey || res.override.building_id || res.override.id || res.override.hcad_num;
            const existingOv =
              this.mapController.curatedOverrides[ovKey] ||
              this.mapController.curatedOverrides[res.override.hcad_num] ||
              {};
            this.mapController.curatedOverrides[ovKey] = {
              ...existingOv,
              ...res.override,
              geometry:
                existingOv.geometry ||
                this.mapController.selectedFeatureGeometry ||
                null,
            };
            this.mapController._refreshCuratedOverridesSource();
            this._mergeCuratedOverridesIntoSearchIndex();
            this._enrichSearchIndexWithOverlayMetadata();
            if (this.mapController.selectedFeatureProps) {
              this.mapController.highlightAndInspectFeature(
                this.mapController.selectedFeatureProps,
                this.mapController.selectedFeatureGeometry
              );
            }
          }

          if (feedbackEl) {
            feedbackEl.innerHTML = `<strong>&#10003; Published Live (Admin Approved)!</strong> <strong>${payload.address || payload.hcad_num}</strong> is now set to <strong>Built ${payload.suggested_year_built} ✓</strong> on the live map and dispatched to the Google Sheet.`;
            feedbackEl.classList.remove("hidden");
          }
        } catch (err) {
          btnAdminApproveDirect.disabled = false;
          if (feedbackEl) {
            feedbackEl.innerHTML = `<strong>✕ Admin Approval Error:</strong> ${err.message || err}`;
            feedbackEl.classList.remove("hidden");
          }
        }
      });
    }

    if (btnCloseCorrModal && corrModal) {
      btnCloseCorrModal.addEventListener("click", () => corrModal.classList.add("hidden"));
    }

    const footprintSelectEl = document.getElementById("corr-footprint-issue");
    const footprintNotesWrapEl = document.getElementById("corr-footprint-notes-wrap");
    const footprintNotesEl = document.getElementById("corr-footprint-notes");
    const footprintBoxEl = document.getElementById("corr-footprint-box");
    if (footprintSelectEl) {
      footprintSelectEl.addEventListener("change", () => {
        const hasIssue = Boolean(footprintSelectEl.value);
        if (footprintNotesWrapEl) {
          footprintNotesWrapEl.classList.toggle("hidden", !hasIssue);
        }
        if (footprintBoxEl) {
          footprintBoxEl.classList.toggle("active", hasIssue);
        }
        if (footprintNotesEl) {
          footprintNotesEl.required = hasIssue;
          if (hasIssue) {
            setTimeout(() => footprintNotesEl.focus(), 40);
          }
        }
        const sourceTypeEl = document.getElementById("corr-source-type");
        const suggYrEl = document.getElementById("corr-suggested-year");
        if (hasIssue && sourceTypeEl && (!suggYrEl || !suggYrEl.value)) {
          sourceTypeEl.value = "Aerial / Satellite Imagery";
        }
      });
    }

    if (corrForm) {
      corrForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const submitBtn = document.getElementById("btn-submit-correction");
        const feedbackEl = document.getElementById("corr-submit-feedback");

        const rawBldNameField = String(document.getElementById("corr-building-name")?.value || "").trim();
        let parsedPrimaryBldName = "";
        let parsedAltNames = [];
        if (rawBldNameField) {
          const nameParts = rawBldNameField
            .split(/\s*\|\s*|\s*;\s*/)
            .map((s) => s.trim())
            .filter(Boolean);
          parsedPrimaryBldName = nameParts[0] || "";
          parsedAltNames = nameParts.slice(1);
        }

        const payload = {
          address: document.getElementById("corr-address")?.value || "",
          building_name: parsedPrimaryBldName,
          alt_names: parsedAltNames,
          hcad_num: document.getElementById("corr-hcad-num")?.value || "",
          current_year_built: document.getElementById("corr-current-year")?.value || "",
          suggested_year_built: document.getElementById("corr-suggested-year")?.value || "",
          historic_district: document.getElementById("corr-district")?.value || "",
          source_type: document.getElementById("corr-source-type")?.value || "Houston City Directory",
          architect: document.getElementById("corr-style-arch")?.value || "",
          source_citation: document.getElementById("corr-citation")?.value || "",
          footprint_issue: document.getElementById("corr-footprint-issue")?.value || "",
          footprint_notes: document.getElementById("corr-footprint-notes")?.value || "",
          satellite_url: this._activeCorrectionSatelliteUrl || "",
          photo_url: document.getElementById("corr-photo-url")?.value || "",
          photo_year: document.getElementById("corr-photo-year")?.value || "",
          photo_caption: document.getElementById("corr-photo-caption")?.value || "",
          deed_subdivision: document.getElementById("corr-deed-subdivision")?.value || "",
          deed_clerk_file: document.getElementById("corr-deed-clerk-file")?.value || "",
          deed_pdf_url: document.getElementById("corr-deed-url")?.value || "",
          submitter_name: document.getElementById("corr-submitter-name")?.value || "",
          submitter_email: document.getElementById("corr-submitter-email")?.value || "",
        };

        const hasYearChange = Boolean(String(payload.suggested_year_built).trim());
        const hasNameUpdate = Boolean(parsedPrimaryBldName);
        const hasFootprintReport = Boolean(
          String(payload.footprint_issue).trim() || String(payload.footprint_notes).trim()
        );
        const hasCitation = Boolean(String(payload.source_citation).trim());
        const hasPhoto = Boolean(String(payload.photo_url).trim());
        const hasDeedContribution = Boolean(
          String(payload.deed_pdf_url).trim() || String(payload.deed_clerk_file).trim()
        );

        if (
          !hasYearChange &&
          !hasNameUpdate &&
          !hasFootprintReport &&
          !hasCitation &&
          !hasPhoto &&
          !hasDeedContribution
        ) {
          if (feedbackEl) {
            feedbackEl.innerHTML = `<strong>Please enter a Corrected Year Built, Building Name / Alias, Subdivision Deed Restriction PDF / Clerk File #, select a Building Footprint Issue, or provide historical notes before submitting.</strong>`;
            feedbackEl.classList.remove("hidden");
          }
          return;
        }

        if (hasFootprintReport && !String(payload.footprint_notes).trim() && !hasCitation) {
          if (feedbackEl) {
            feedbackEl.innerHTML = `<strong>Please briefly describe the building shape or orientation error as you see it so our moderators know what to fix.</strong>`;
            feedbackEl.classList.remove("hidden");
          }
          if (footprintNotesEl) footprintNotesEl.focus();
          return;
        }

        if (submitBtn) submitBtn.disabled = true;

        const webhookUrl =
          document.getElementById("admin-webhook-url")?.value ||
          this.mapController?.sheetSyncStatus?.webhookUrl ||
          "";

        const res = await submitCorrectionSuggestion(payload, webhookUrl);
        if (submitBtn) submitBtn.disabled = false;

        if (payload.photo_url) {
          registerSessionPhoto({
            hcad_num: payload.hcad_num,
            building_id: payload.hcad_num,
            landmark_name: payload.building_name || payload.address,
            photo_url: payload.photo_url,
            photo_year: payload.photo_year,
            photo_caption: payload.photo_caption || payload.source_citation,
            credit: payload.submitter_name
              ? `Contributed by ${payload.submitter_name} (Pending PH Review)`
              : "Community Archival Submission (Pending PH Review)",
            source_url: payload.photo_url,
          });
          if (this.mapController?.selectedFeatureProps) {
            this.renderInspectorDrawer(this.mapController.selectedFeatureProps);
          }
        }

        if (feedbackEl) {
          const deliveryNote = res.webhookDelivered
            ? "Sent directly to Preservation Houston's Google Sheet Pending Review Queue."
            : "Queued in Pending Review Queue (use 'Export Pending Queue (.CSV)' or connect a Google Sheet webhook below).";
          const photoNote = payload.photo_url
            ? " Your contributed photograph has also been added to the Archival Photographs timeline for this session and queued for moderation."
            : "";
          const deedNote = hasDeedContribution
            ? ` Your subdivision deed restriction citation (${
                payload.deed_subdivision || "Subdivision"
              }${payload.deed_clerk_file ? ` · ${payload.deed_clerk_file}` : ""}) has been queued for archival PDF mirroring.`
            : "";
          const summaryDesc = hasFootprintReport && hasYearChange
            ? `Year Built <strong>${payload.suggested_year_built}</strong> + Footprint Report (<em>${payload.footprint_issue || "Shape/Orientation"}</em>)`
            : hasFootprintReport
            ? `Footprint Shape / Orientation Report (<em>${payload.footprint_issue || "Geometry Issue"}</em>)`
            : hasDeedContribution && !hasYearChange
            ? `Deed Restriction / Plat Record (<strong>${payload.deed_subdivision || payload.address}</strong>)`
            : `Built <strong>${payload.suggested_year_built || payload.current_year_built || "Updated"}</strong>, source: <em>${payload.source_type}</em>`;

          feedbackEl.innerHTML = `<strong>&#10003; Thank you!</strong> Suggestion for <strong>${payload.building_name || payload.address || payload.hcad_num}</strong> (${summaryDesc}) has been recorded with status <code>Pending</code>. ${deliveryNote}${photoNote}${deedNote}`;
          feedbackEl.classList.remove("hidden");
        }
      });
    }

    if (btnExportPendingCsv) {
      btnExportPendingCsv.addEventListener("click", () => {
        const pending = getLocalPendingSuggestions();
        const csvContent = formatSuggestionsAsCsv(
          pending.length
            ? pending
            : [
                {
                  status: "Approved",
                  hcad_num: "0621100000014",
                  address: "1127 KEY ST",
                  historic_district: "Norhill Historic District",
                  hcad_year_built: 1920,
                  suggested_year_built: 1928,
                  bld_style: "1920s Bungalow",
                  architect: "",
                  source_type: "Houston City Directory",
                  source_citation:
                    "1928 Houston City Directory (Morrison & Fourmy); lot unimproved through 1926 directory",
                  source_url: "https://cdm17006.contentdm.oclc.org/digital/collection/citydir/search",
                  submitter_name: "Dave Morris",
                  submitter_email: "",
                  submitted_at: "2026-10-05",
                },
              ]
        );
        const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = "preservation_houston_property_edits.csv";
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
      });
    }

    if (btnSaveSheetConfig) {
      btnSaveSheetConfig.addEventListener("click", async () => {
        const csvInput = document.getElementById("admin-sheet-csv-url");
        const webhookInput = document.getElementById("admin-webhook-url");
        const statusText = document.getElementById("admin-sheet-status-text");
        const csvUrl = csvInput ? csvInput.value.trim() : "";
        const webhookUrl = webhookInput ? webhookInput.value.trim() : "";

        saveGoogleSheetEndpoints({ csvUrl, webhookUrl });
        if (statusText) statusText.textContent = "Syncing Google Sheet...";

        const syncStatus = await this.mapController.reloadCuratedOverrides(csvUrl);
        this._mergeCuratedOverridesIntoSearchIndex();
        this._enrichSearchIndexWithOverlayMetadata();

        if (statusText) {
          if (syncStatus && syncStatus.error) {
            statusText.textContent = `Sync error: ${syncStatus.error}`;
          } else if (syncStatus && syncStatus.connected) {
            statusText.textContent = `✓ Synced (${syncStatus.sheetRowCount} sheet edits, ${syncStatus.totalOverrideCount} total active)`;
          } else {
            statusText.textContent = `✓ Using baseline overrides (${syncStatus?.totalOverrideCount || 1} active)`;
          }
        }
      });
    }

    // Sidebar Collapse Toggle (for smaller screens & mobile drawer)
    const btnToggleSidebar = document.getElementById("btn-toggle-sidebar");
    const btnCloseSidebarMobile = document.getElementById("btn-close-sidebar-mobile");
    const sidebar = document.getElementById("atlas-sidebar");
    if (btnToggleSidebar && sidebar) {
      btnToggleSidebar.addEventListener("click", () => {
        const nextCollapsed = !sidebar.classList.contains("collapsed");
        this._setSidebarCollapsed(nextCollapsed);
      });
    }
    if (btnCloseSidebarMobile) {
      btnCloseSidebarMobile.addEventListener("click", () => {
        this._setSidebarCollapsed(true);
      });
    }

    // Dynamically track topbar bottom edge on mobile/tablet so Tour Bar & Sidebar never overlap
    const topbarEl = document.getElementById("atlas-topbar");
    if (topbarEl) {
      const updateTopbarOffset = () => {
        const rect = topbarEl.getBoundingClientRect();
        if (rect.height > 0) {
          document.documentElement.style.setProperty(
            "--topbar-offset",
            `${Math.round(rect.bottom + 6)}px`
          );
        }
      };
      updateTopbarOffset();
      window.addEventListener("resize", updateTopbarOffset);
      if (typeof ResizeObserver !== "undefined") {
        const ro = new ResizeObserver(updateTopbarOffset);
        ro.observe(topbarEl);
      }
    }
  }

  _runSearchQuery(rawQuery, customHeaderLabel = "") {
    const searchInput = document.getElementById("search-input");
    const searchResults = document.getElementById("search-results");
    if (!searchResults) return;

    const q = String(rawQuery || "").trim().toLowerCase();
    if (q.length < 2) {
      searchResults.classList.add("hidden");
      searchResults.innerHTML = "";
      return;
    }

    // Parse HCAD Grade query whether formatted as "HCAD Grade E" or "Vernacular Frame / Cottage (HCAD Grade E)"
    const gradeMatch = q.match(/hcad\s+(?:grade|class)?\s*([a-ex][+-]{0,2})(?:\b|\)|$)/i);
    const targetGrade = gradeMatch ? gradeMatch[1].toLowerCase() : "";

    // Semantic Land-Use Sub-Families: clicking a specific sub-type chip (e.g. "Auditorium") ranks exact matches #1 (115)
    // and also surfaces peer structures in the same functional family (92) so hyper-specific HCAD codes never return just 1 item.
    const LANDUSE_SEMANTIC_FAMILIES = [
      {
        triggers: [
          "auditorium",
          "auditorium / performing arts",
          "theater / performing arts",
          "legitimate theater",
          "cultural facility",
          "night club/dinner theater",
        ],
        landuseMatches: [
          "auditorium",
          "auditorium / performing arts",
          "theater / performing arts",
          "legitimate theater",
          "cultural facility",
          "night club/dinner theater",
        ],
        nameKeywords: [
          "jones hall",
          "theater",
          "theatre",
          "auditorium",
          "performing arts",
          "center for dance",
          "music hall",
          "concert hall",
          "opera",
          "symphony",
          "playhouse",
        ],
      },
      {
        triggers: ["religious", "church / religious"],
        landuseMatches: ["religious", "church / religious"],
        nameKeywords: [
          "church",
          "cathedral",
          "chapel",
          "sanctuary",
          "synagogue",
          "temple",
          "parish",
          "baptist",
          "methodist",
          "lutheran",
          "episcopal",
          "catholic",
          "presbyterian",
        ],
      },
      {
        triggers: ["library"],
        landuseMatches: ["library"],
        nameKeywords: ["library", "ideson", "gregory school"],
      },
      {
        triggers: ["school", "college or university", "day care center"],
        landuseMatches: ["school", "college or university", "day care center"],
        nameKeywords: [
          "school",
          "elementary",
          "academy",
          "college",
          "university",
          "hisd",
          "campus",
        ],
      },
      {
        triggers: ["police or fire station"],
        landuseMatches: ["police or fire station"],
        nameKeywords: ["fire station", "police", "hook and ladder"],
      },
      {
        triggers: ["miscellaneous government", "post office", "correctional"],
        landuseMatches: ["miscellaneous government", "post office", "correctional"],
        nameKeywords: [
          "courthouse",
          "city hall",
          "municipal courts",
          "federal building",
          "post office",
          "custom house",
          "justice center",
          "permitting center",
          "county annex",
          "administration building",
        ],
      },
      {
        triggers: ["hospitals", "medical office", "veterinary clinic"],
        landuseMatches: ["hospitals", "medical office", "veterinary clinic"],
        nameKeywords: ["hospital", "medical", "clinic", "infirmary"],
      },
      {
        triggers: [
          "hotel/motel, hi-rise 4+ stories",
          "hotel/motel, low-rise 1 to 3 stories",
          "extended stay hotel/motel",
        ],
        landuseMatches: [
          "hotel/motel, hi-rise 4+ stories",
          "hotel/motel, low-rise 1 to 3 stories",
          "extended stay hotel/motel",
        ],
        nameKeywords: ["hotel", "motel", "inn"],
      },
      {
        triggers: [
          "office bldgs. hi-rise (5+ stories)",
          "office bldgs. low-rise (1 to 4 stories)",
          "office condominium",
        ],
        landuseMatches: [
          "office bldgs. hi-rise (5+ stories)",
          "office bldgs. low-rise (1 to 4 stories)",
          "office condominium",
        ],
        nameKeywords: ["office", "building", "tower", "bank"],
      },
      {
        triggers: ["warehouse", "warehouse-metallic", "office - warehouse", "light industrial - metallic"],
        landuseMatches: [
          "warehouse",
          "warehouse-metallic",
          "office - warehouse",
          "light industrial - metallic",
        ],
        nameKeywords: ["warehouse", "storage", "industrial", "freight", "cotton"],
      },
    ];

    const activeLanduseFamily = LANDUSE_SEMANTIC_FAMILIES.find((fam) =>
      fam.triggers.includes(q)
    );

    // Architectural Movement Tokens for compound style chips (e.g. "Mid-Century Travertine Formalism" or "Queen Anne with Eastlake influences")
    const STYLE_MOVEMENT_GROUPS = [
      ["formalism", "mid-century", "mid century", "modernist", "modern", "international style", "brutalist"],
      ["art deco", "moderne", "streamline", "zigzag", "modernistic"],
      ["queen anne", "eastlake", "victorian", "folk victorian", "shingle", "stick"],
      ["craftsman", "bungalow", "prairie", "praire", "arts and crafts"],
      ["neoclassical", "classical revival", "greek revival", "beaux-arts", "beaux arts", "roman"],
      ["colonial revival", "georgian", "federal", "dutch colonial"],
      ["tudor", "english", "jacobethan", "norman"],
      ["mediterranean", "spanish", "mission", "italian renaissance", "italianate"],
      ["gothic revival", "late gothic", "romanesque", "richardsonian"],
      ["vernacular", "l plan", "l-plan", "shotgun", "cottage"],
    ];
    const activeStyleMovements = new Set();
    for (const grp of STYLE_MOVEMENT_GROUPS) {
      if (grp.some((tok) => q.includes(tok))) {
        for (const tok of grp) activeStyleMovements.add(tok);
      }
    }

    const hdrLow = String(customHeaderLabel || "").toLowerCase();
    const isNameOrAliasFilter =
      hdrLow.startsWith("name / alias:") || hdrLow.startsWith("building name:");
    const isArchitectFilter =
      hdrLow.startsWith("architect:") || hdrLow.startsWith("architect / builder:");
    const isDistrictFilter = hdrLow.startsWith("district:");
    const isNeighborhoodFilter =
      hdrLow.startsWith("neighborhood:") || hdrLow.startsWith("neighborhood / alias:");
    const isSuperNeighborhoodFilter = hdrLow.startsWith("super neighborhood:");
    const isHistoricWardFilter = hdrLow.startsWith("historic ward:");
    const isSubdivisionFilter =
      hdrLow.startsWith("subdivision:") || hdrLow.startsWith("platted subdivision:");
    const isGoodBrickFilter = hdrLow.startsWith("good brick");
    const isLandUseCategoryFilter = hdrLow.startsWith("land use category:");
    const isLandUseSpecificFilter = hdrLow.startsWith("land use:");
    const isStyleOrClassFilter =
      hdrLow.startsWith("architectural style:") ||
      hdrLow.startsWith("building class:") ||
      hdrLow.startsWith("building category:");

    const qAddr = q
      .replace(/,\s*houston.*$/i, "")
      .replace(/\./g, "")
      .trim();
    const qTokens = qAddr.split(/\s+/).filter((t) => t.length >= 2);
    const GENERIC_NAME_STOPWORDS = new Set([
      "the",
      "and",
      "of",
      "at",
      "in",
      "on",
      "for",
      "building",
      "buildings",
      "structure",
      "house",
      "home",
      "residence",
      "cottage",
      "warehouse",
      "company",
      "co",
      "inc",
      "corp",
      "center",
      "complex",
      "block",
      "tower",
      "plaza",
      "hall",
      "hotel",
      "bank",
      "church",
      "school",
      "lofts",
      "apartments",
      "houston",
      "texas",
      "historic",
    ]);
    const distinctiveNameTokens = qTokens.filter(
      (t) => t.length >= 3 && !GENERIC_NAME_STOPWORDS.has(t)
    );

    const selHcad = String(this.selectedProperties?.hcad_num || "").trim().toLowerCase();
    const selId = String(
      this.selectedProperties?.id || this.selectedProperties?.building_id || ""
    )
      .trim()
      .toLowerCase();

    const canUseFastTextGuard =
      !targetGrade &&
      !activeLanduseFamily &&
      activeStyleMovements.size === 0 &&
      !isStyleOrClassFilter;
    const fastTok1 = canUseFastTextGuard
      ? qTokens.length <= 1
        ? qAddr || q
        : qTokens[0]
      : "";
    const fastTok2 =
      canUseFastTextGuard && qTokens.length >= 2 && !qAddr ? qTokens[1] : "";

    const scoreCandidate = (item, isViewportCandidate = false) => {
      const lc = this._getSearchLowercaseCache(item);
      if (fastTok1 && !lc.allText.includes(fastTok1)) return 0;
      if (fastTok2 && !lc.allText.includes(fastTok2)) return 0;

      const {
        itemId,
        hasCustomName,
        placeName,
        lbl,
        bldName,
        altNames,
        addr,
        altAddrs,
        sub,
        hcad,
        arch,
        style,
        bldStyle,
        useCat,
        landuse,
        cat,
        grade,
        gbYrs,
        dist,
        nh,
        nhAliases,
        superNh,
        ward,
        subdiv,
        topSubdivs,
        lmCode,
        isPlaceBoundary,
      } = lc;

      if (targetGrade) {
        if (isPlaceBoundary) return 0;
        const hasSpecificStyleOrClass =
          (style && style !== targetGrade && style !== "historical / architectural structure") ||
          (bldStyle &&
            bldStyle !== targetGrade &&
            bldStyle !== "historical / architectural structure" &&
            bldStyle !== "residential");
        if (
          !hasSpecificStyleOrClass &&
          (grade === targetGrade || bldStyle === targetGrade || style === targetGrade)
        ) {
          return (
            (isViewportCandidate ? 120 : 100) +
            (item.landmark_code || item.good_brick_years ? 15 : 0)
          );
        }
        return 0;
      }

      if (isPlaceBoundary) {
        if (
          isArchitectFilter ||
          isDistrictFilter ||
          isGoodBrickFilter ||
          isLandUseCategoryFilter ||
          isLandUseSpecificFilter ||
          isStyleOrClassFilter ||
          isNameOrAliasFilter
        ) {
          return 0;
        }
        if (isNeighborhoodFilter && item.type === "neighborhood") {
          if (placeName === q || altNames.includes(q)) return 142;
        } else if (isSubdivisionFilter && item.type === "platted_subdivision") {
          if (placeName === q || altNames.includes(q)) return 142;
        } else if (isSuperNeighborhoodFilter && item.type === "super_neighborhood") {
          if (placeName === q) return 142;
        } else if (isHistoricWardFilter && item.type === "historic_ward") {
          if (placeName === q || lbl === q) return 142;
        } else if (
          !isNeighborhoodFilter &&
          !isSuperNeighborhoodFilter &&
          !isHistoricWardFilter &&
          !isSubdivisionFilter
        ) {
          const isPlat =
            item.type === "platted_subdivision" || item.overlay_layer === "platted_subdivisions";
          if (placeName === q || lbl === q) {
            return isPlat ? 136 : 138;
          }
          if (altNames.some((an) => an === q)) return isPlat ? 132 : 135;
          if (placeName.startsWith(q)) return isPlat ? 114 : 118;
          if (altNames.some((an) => an.startsWith(q))) return isPlat ? 92 : 112;
          if (placeName.includes(q)) return isPlat ? 90 : 98;
          if (altNames.some((an) => an.includes(q))) return isPlat ? 80 : 94;
          if (topSubdivs.some((sd) => sd === q || sd.includes(q))) return 88;
        }
        return 0;
      }

      let score = 0;

      if (isNameOrAliasFilter) {
        if (bldName === q || lbl === q) {
          score = 130;
        } else if (altNames.some((an) => an === q)) {
          score = 128;
        } else if (
          (bldName && bldName.includes(q)) ||
          altNames.some((an) => an.includes(q)) ||
          (lbl && !/^\d+\s+/.test(lbl) && lbl.includes(q))
        ) {
          score = 110;
        } else if (distinctiveNameTokens.length > 0 && qTokens.length >= 2) {
          const nameHaystack = `${bldName} ${altNames.join(" ")} ${!/^\d+\s+/.test(lbl) ? lbl : ""}`;
          if (
            distinctiveNameTokens.every((tok) => nameHaystack.includes(tok)) &&
            qTokens.every((tok) => nameHaystack.includes(tok))
          ) {
            score = 94;
          }
        }
      } else if (isArchitectFilter) {
        if (arch && arch.includes(q)) {
          score = arch === q ? 120 : 105;
        }
      } else if (isDistrictFilter) {
        if (dist && dist.includes(q)) {
          score = dist === q ? 120 : 105;
        }
      } else if (isNeighborhoodFilter) {
        if (nh === q) {
          score = 126;
        } else if (nhAliases.some((a) => a === q)) {
          score = 122;
        } else if (nh && nh.includes(q)) {
          score = 108;
        } else if (nhAliases.some((a) => a.includes(q))) {
          score = 102;
        } else if (subdiv === q || (subdiv && subdiv.includes(q))) {
          score = 96;
        }
      } else if (isSuperNeighborhoodFilter) {
        if (superNh === q) {
          score = 126;
        } else if (superNh && superNh.includes(q)) {
          score = 108;
        }
      } else if (isHistoricWardFilter) {
        if (ward === q) {
          score = 126;
        } else if (ward && ward.includes(q)) {
          score = 108;
        }
      } else if (isSubdivisionFilter) {
        if (subdiv === q) {
          score = 126;
        } else if (subdiv && subdiv.includes(q)) {
          score = 110;
        } else if (nh === q || nhAliases.includes(q)) {
          score = 98;
        }
      } else if (isGoodBrickFilter) {
        if (gbYrs && gbYrs.split(/[,\s]+/).includes(q)) {
          score = 120;
        }
      } else if (isLandUseCategoryFilter) {
        if (useCat === q || cat === q) {
          score = 115;
        } else if (useCat && useCat.includes(q)) {
          score = 95;
        }
      } else if (isLandUseSpecificFilter) {
        if (landuse === q) {
          score = 122;
        } else if (landuse && landuse.includes(q)) {
          score = 104;
        } else if (useCat === q) {
          score = 100;
        } else if (activeLanduseFamily) {
          if (landuse && activeLanduseFamily.landuseMatches.includes(landuse)) {
            score = 94;
          } else {
            const nameText = `${lbl} ${bldName}`;
            if (
              activeLanduseFamily.nameKeywords.some((kw) => nameText.includes(kw)) &&
              !nameText.includes("grocery")
            ) {
              score = 88;
            }
          }
        }
      } else if (isStyleOrClassFilter) {
        if (style === q || bldStyle === q) {
          score = 122;
        } else if (
          (style &&
            style !== "historical / architectural structure" &&
            (style.includes(q) || (style.length > 3 && q.includes(style)))) ||
          (bldStyle &&
            bldStyle !== "historical / architectural structure" &&
            (bldStyle.includes(q) || (bldStyle.length > 3 && q.includes(bldStyle))))
        ) {
          score = 102;
        } else if (activeStyleMovements.size > 0 && (style || bldStyle)) {
          const combinedStyle = `${style} ${bldStyle}`;
          for (const tok of activeStyleMovements) {
            if (combinedStyle.includes(tok)) {
              score = 90;
              break;
            }
          }
        } else if (useCat === q || landuse === q) {
          score = 95;
        }
      } else {
        // General free-text search across all metadata & labels
        // Note: Do NOT check `q.includes(style)` here, or multi-word building queries like
        // "Spaghetti Warehouse" or "Sam Houston Hotel" will match every building with style="Warehouse" or "Hotel"!
        if (style === q || bldStyle === q) {
          score = Math.max(score, 118);
        } else if (
          (style &&
            style !== "historical / architectural structure" &&
            style.includes(q)) ||
          (bldStyle &&
            bldStyle !== "historical / architectural structure" &&
            bldStyle.includes(q))
        ) {
          score = Math.max(score, 96);
        } else if (activeStyleMovements.size > 0 && (style || bldStyle)) {
          const combinedStyle = `${style} ${bldStyle}`;
          for (const tok of activeStyleMovements) {
            if (combinedStyle.includes(tok)) {
              score = Math.max(score, 89);
              break;
            }
          }
        }

        if (useCat === q || landuse === q || cat === q) {
          score = Math.max(score, 115);
        } else if ((useCat && useCat.includes(q)) || (landuse && landuse.includes(q))) {
          score = Math.max(score, 94);
        } else if (activeLanduseFamily) {
          if (landuse && activeLanduseFamily.landuseMatches.includes(landuse)) {
            score = Math.max(score, 92);
          } else {
            const nameText = `${lbl} ${bldName}`;
            if (
              activeLanduseFamily.nameKeywords.some((kw) => nameText.includes(kw)) &&
              !nameText.includes("grocery")
            ) {
              score = Math.max(score, 88);
            }
          }
        }

        if (arch && arch.includes(q)) {
          score = Math.max(score, arch === q ? 116 : 98);
        }
        if (dist && dist.includes(q)) {
          score = Math.max(score, dist === q ? 105 : 90);
        }
        if (nh && (nh === q || nh.includes(q))) {
          score = Math.max(score, nh === q ? 106 : 88);
        }
        if (nhAliases.some((a) => a === q || a.includes(q))) {
          score = Math.max(score, nhAliases.includes(q) ? 104 : 86);
        }
        if (superNh && (superNh === q || superNh.includes(q))) {
          score = Math.max(score, superNh === q ? 102 : 84);
        }
        if (ward && (ward === q || ward.includes(q))) {
          score = Math.max(score, ward === q ? 104 : 86);
        }
        if (subdiv && (subdiv === q || subdiv.includes(q))) {
          score = Math.max(score, subdiv === q ? 106 : 86);
        }
        if (gbYrs && gbYrs.split(/[,\s]+/).includes(q)) {
          score = Math.max(score, 110);
        }
        if (lmCode && (lmCode === q || `hpo #${lmCode}` === q)) {
          score = Math.max(score, 120);
        }
        if (hcad && hcad.includes(q)) {
          score = Math.max(score, hcad === q ? 125 : 90);
        }

        // Primary Street Address & Secondary / Alternate Street Addresses (`alt_addresses`) match
        if (addr) {
          if (addr === q || (qAddr && addr === qAddr)) {
            score = Math.max(score, 126);
          } else if (addr.startsWith(q) || (qAddr && addr.startsWith(qAddr))) {
            score = Math.max(score, 116);
          } else if (addr.includes(q) || (qAddr && addr.includes(qAddr))) {
            score = Math.max(score, 96);
          }
        }
        for (const aa of altAddrs) {
          if (aa === q || (qAddr && aa === qAddr)) {
            score = Math.max(score, 124);
          } else if (aa.startsWith(q) || (qAddr && aa.startsWith(qAddr))) {
            score = Math.max(score, 114);
          } else if (aa.includes(q) || (qAddr && aa.includes(qAddr))) {
            score = Math.max(score, 94);
          }
        }

        // Primary Building Name & Historical / Colloquial Aliases (`alt_names`) match
        if (bldName) {
          if (bldName === q) score = Math.max(score, 125);
          else if (bldName.startsWith(q)) score = Math.max(score, 102);
          else if (bldName.includes(q) && !bldName.includes("grocery")) score = Math.max(score, 82);
        }
        for (const an of altNames) {
          if (an === q) score = Math.max(score, 122);
          else if (an.startsWith(q)) score = Math.max(score, 98);
          else if (an.includes(q)) score = Math.max(score, 78);
        }

        // Title / address / sublabel match
        if (lbl === q) {
          score = Math.max(score, 120);
        } else if (lbl.startsWith(q) && !lbl.includes("grocery")) {
          score = Math.max(score, 88);
        } else if (lbl.includes(q) && !lbl.includes("grocery")) {
          score = Math.max(score, 65);
        } else if (sub.includes(q)) {
          score = Math.max(score, 55);
        }

        // Multi-word token fallback across address + names (e.g. "1001 mckinney" or "city national mckinney")
        // Require at least one distinctive non-stopword token if matching against names so generic words
        // like "warehouse" or "building" alone don't pull in unrelated structures.
        if (score < 85 && qTokens.length >= 2) {
          const addrHaystack = `${addr} ${altAddrs.join(" ")}`;
          const nameHaystack = `${lbl} ${bldName} ${altNames.join(" ")}`;
          if (qTokens.every((tok) => addrHaystack.includes(tok))) {
            score = Math.max(score, 86);
          } else if (
            distinctiveNameTokens.length > 0 &&
            qTokens.every((tok) => `${addrHaystack} ${nameHaystack}`.includes(tok))
          ) {
            score = Math.max(score, 86);
          }
        }
      }

      if (score === 0) return 0;

      // Boost nearby viewport buildings and notable landmarks / named structures
      if (isViewportCandidate) score += 14;
      if ((selId && itemId === selId) || (selHcad && hcad === selHcad)) {
        score += 4;
      }
      if (item.type === "landmark" || item.type === "good_brick" || item.landmark_code || item.good_brick_years) {
        score += 8;
      }
      if (hasCustomName) score += 5;

      return score;
    };

    const scoredMatches = [];
    const seenKeys = new Set();

    // First: Check rendered viewport buildings from vector tiles
    const liveCandidates = this.mapController?.getRenderedBuildingCandidates
      ? this.mapController.getRenderedBuildingCandidates(400)
      : [];
    const needsPreGeo =
      isNeighborhoodFilter ||
      isSuperNeighborhoodFilter ||
      isHistoricWardFilter ||
      isSubdivisionFilter;

    for (const cand of liveCandidates) {
      const p = cand.props || {};
      const rawKey = String(p.id || p.building_id || p.hcad_num || "").trim();
      const key = rawKey.replace(/#fp_\d+$/, "");
      const hcadKey = String(p.hcad_num || "").trim();
      if ((key && seenKeys.has(key)) || (hcadKey && !key.includes("#") && seenKeys.has(hcadKey))) {
        continue;
      }

      // Only populate geographic context before scoring when a geographic filter chip is active
      if (
        needsPreGeo &&
        (!p.neighborhood || !p.super_neighborhood || !p.historic_ward) &&
        !p._geoResolved &&
        this.mapController?.resolveGeographicContextAtPoint
      ) {
        p._geoResolved = true;
        const gCtx = this.mapController.resolveGeographicContextAtPoint(cand.lon, cand.lat);
        if (gCtx) {
          if (!p.neighborhood && gCtx.neighborhood) p.neighborhood = gCtx.neighborhood;
          if (!p.neighborhood_aliases && gCtx.neighborhood_aliases?.length) {
            p.neighborhood_aliases = gCtx.neighborhood_aliases;
          }
          if (!p.super_neighborhood && gCtx.super_neighborhood) {
            p.super_neighborhood = gCtx.super_neighborhood;
          }
          if (!p.historic_ward && gCtx.historic_ward) {
            p.historic_ward = gCtx.historic_ward;
          }
        }
      }

      const nameInfo = this._resolveBuildingNameAndAliases(p);
      const yr = Number(p.year_built) || 0;
      const distClean =
        p.historic_district &&
        p.historic_district !== "Outside City District" &&
        p.historic_district !== "Outside Historic District"
          ? p.historic_district
          : "";
      const primaryAddr = String(p.address || "").trim();
      const altAddresses = Array.isArray(p.alt_addresses)
        ? p.alt_addresses.map((a) => String(a || "").trim()).filter(Boolean)
        : [];
      const nhAliasesArr = Array.isArray(p.neighborhood_aliases)
        ? p.neighborhood_aliases
        : typeof p.neighborhood_aliases === "string" && p.neighborhood_aliases.trim()
        ? p.neighborhood_aliases.split(/\s*\|\s*|\s*;\s*/)
        : [];
      const synthItem = {
        type: "building",
        id: key || p.id || p.building_id || "",
        hcad_num: String(p.hcad_num || "").trim(),
        label: nameInfo.primaryName || primaryAddr || `HCAD ${p.hcad_num}`,
        address: primaryAddr,
        alt_addresses: altAddresses,
        building_name: nameInfo.hasCustomBuildingName ? nameInfo.primaryName : "",
        alt_names: nameInfo.altNames,
        name_source: nameInfo.nameSource,
        sublabel: "",
        category: yr >= 1836 ? `Built ${yr}${p.is_curated_override ? " ✓" : ""}` : p.use_category || "Structure",
        year_built: yr,
        architect: p.architect || "",
        style: p.style || "",
        bld_style: p.bld_style || p.style || "",
        hcad_grade: p.hcad_grade || "",
        use_category: p.use_category || "",
        landuse_desc: p.landuse_desc || "",
        good_brick_years: p.good_brick_years ? String(p.good_brick_years) : "",
        historic_district: distClean,
        neighborhood: p.neighborhood || "",
        neighborhood_aliases: nhAliasesArr,
        super_neighborhood: p.super_neighborhood || "",
        historic_ward: p.historic_ward || "",
        subdivision: p.subdivision || "",
        landmark_code: p.landmark_code || "",
        landmark_report_url: p.landmark_report_url || "",
        landmark_summary: p.landmark_summary || "",
        lon: cand.lon,
        lat: cand.lat,
        zoom: 17.5,
      };
      const sc = scoreCandidate(synthItem, cand.inViewport);
      if (sc > 0) {
        const styleInfo = this._resolveStyleAndClassInfo(p);
        const luChips = this._resolveLandUseChips(p);
        const luSummary = luChips.map((c) => c.label).join(" › ");
        const allAddrs = [primaryAddr, ...altAddresses].filter(Boolean);
        const combinedAddrDisplay = allAddrs.slice(0, 2).join(" / ");
        synthItem.sublabel = [
          cand.inViewport ? "📍 In Current View" : "",
          nameInfo.altNames.length > 0 ? `AKA: ${nameInfo.altNames.slice(0, 2).join(", ")}` : "",
          (nameInfo.hasCustomBuildingName || altAddresses.length > 0) &&
          combinedAddrDisplay &&
          combinedAddrDisplay.toLowerCase() !== String(synthItem.label).toLowerCase()
            ? combinedAddrDisplay
            : "",
          distClean || p.neighborhood || luSummary || "Harris County",
          p.neighborhood && distClean && p.neighborhood !== distClean ? p.neighborhood : "",
          luChips.length > 1 && (distClean || p.neighborhood) ? luSummary : "",
          styleInfo.displayStyle && styleInfo.displayStyle !== luSummary ? styleInfo.displayStyle : "",
          yr >= 1836 ? `Built ${yr}` : "",
        ]
          .filter(Boolean)
          .join(" • ");
        if (key) seenKeys.add(key);
        if (synthItem.hcad_num && !key.includes("#")) seenKeys.add(synthItem.hcad_num);
        scoredMatches.push({ item: synthItem, score: sc });
      }
    }

    // Second: Check full citywide searchIndex
    const vp = this.mapController?.getCurrentViewport
      ? this.mapController.getCurrentViewport()
      : null;
    for (const item of this.searchIndex) {
      const lc = this._getSearchLowercaseCache(item);
      const { key, hcadKey } = lc;
      if ((key && seenKeys.has(key)) || (hcadKey && !key.includes("#") && seenKeys.has(hcadKey))) {
        continue;
      }
      const inVp = Boolean(
        vp &&
          Number.isFinite(item.lon) &&
          Number.isFinite(item.lat) &&
          item.lon >= vp.west &&
          item.lon <= vp.east &&
          item.lat >= vp.south &&
          item.lat <= vp.north
      );
      const sc = scoreCandidate(item, inVp);
      if (sc > 0) {
        if (key) seenKeys.add(key);
        if (hcadKey && !key.includes("#")) seenKeys.add(hcadKey);
        scoredMatches.push({ item, score: sc });
      }
    }

    scoredMatches.sort((a, b) => b.score - a.score);
    const totalMatchCount = scoredMatches.length;
    const matches = scoredMatches.slice(0, 25).map((m) => m.item);

    if (!matches.length) {
      searchResults.innerHTML = `<div class="search-empty">No matching addresses, neighborhoods, wards, subdivisions, architects, styles, or landmarks found for "${rawQuery}".</div>`;
      searchResults.classList.remove("hidden");
      return;
    }

    const countSummary =
      totalMatchCount > matches.length
        ? `<strong>${matches.length}</strong> of <strong>${totalMatchCount.toLocaleString()}</strong> shown`
        : `<strong>${matches.length}</strong> shown`;

    const headerBanner = customHeaderLabel
      ? `<div class="search-filter-header">
          <span>&#128269; ${customHeaderLabel} (${countSummary})</span>
          <button type="button" class="search-filter-clear" id="btn-clear-search-filter">Clear</button>
        </div>`
      : totalMatchCount > matches.length
      ? `<div class="search-filter-header">
          <span>&#128269; Matching Results (${countSummary})</span>
          <button type="button" class="search-filter-clear" id="btn-clear-search-filter">Clear</button>
        </div>`
      : "";

    searchResults.innerHTML =
      headerBanner +
      matches
        .map((m, i) => {
          const isPlace = Boolean(
            m.is_neighborhood_entry ||
              m.type === "neighborhood" ||
              m.type === "platted_subdivision" ||
              m.type === "super_neighborhood" ||
              m.type === "historic_ward" ||
              m.type === "historical_waterway" ||
              m.type === "historical_railroad"
          );
          const dispLabel = m.label || m.name || m.building_name || "Houston Neighborhood";
          const dispSub =
            m.sublabel ||
            [
              Array.isArray(m.alt_names) && m.alt_names.length
                ? `AKA: ${m.alt_names.slice(0, 3).join(", ")}`
                : "",
              m.address || "",
            ]
              .filter(Boolean)
              .join(" • ");
          return `
          <button type="button" class="search-result-item ${
            isPlace ? "is-place-boundary-item" : ""
          }" data-idx="${i}">
            <div class="search-result-main">
              <span class="search-result-title">${
                isPlace ? `&#128506; ${dispLabel}` : dispLabel
              }</span>
              <span class="search-result-cat">${m.category}</span>
            </div>
            <div class="search-result-sub">${dispSub}</div>
          </button>`;
        })
        .join("");
    searchResults.classList.remove("hidden");

    const btnClear = document.getElementById("btn-clear-search-filter");
    if (btnClear) {
      btnClear.addEventListener("click", (e) => {
        e.stopPropagation();
        if (searchInput) searchInput.value = "";
        searchResults.classList.add("hidden");
      });
    }

    searchResults.querySelectorAll(".search-result-item").forEach((btn) => {
      btn.addEventListener("click", () => {
        const idx = Number(btn.getAttribute("data-idx"));
        const chosen = matches[idx];
        if (chosen) {
          searchResults.classList.add("hidden");
          const chosenLabel = chosen.label || chosen.name || chosen.building_name || "";
          if (searchInput) searchInput.value = chosenLabel;
          if (typeof window !== "undefined" && window.innerWidth <= 900) {
            this._setSidebarCollapsed(true);
          }
          if (
            chosen.is_neighborhood_entry ||
            chosen.type === "neighborhood" ||
            chosen.type === "platted_subdivision" ||
            chosen.type === "super_neighborhood" ||
            chosen.type === "historic_ward" ||
            chosen.type === "historical_waterway" ||
            chosen.type === "historical_railroad"
          ) {
            const rawLayer = String(chosen.overlay_layer || "");
            const layerKey =
              rawLayer === "historicWards"
                ? "historic_wards"
                : rawLayer === "superNeighborhoods"
                ? "super_neighborhoods"
                : rawLayer === "plattedSubdivisions"
                ? "platted_subdivisions"
                : rawLayer === "historicalWaterways"
                ? "historical_waterways"
                : rawLayer === "historicalRailroads"
                ? "historical_railroads"
                : rawLayer ||
                  (chosen.type === "super_neighborhood"
                    ? "super_neighborhoods"
                    : chosen.type === "historic_ward"
                    ? "historic_wards"
                    : chosen.type === "platted_subdivision"
                    ? "platted_subdivisions"
                    : chosen.type === "historical_waterway"
                    ? "historical_waterways"
                    : chosen.type === "historical_railroad"
                    ? "historical_railroads"
                    : "neighborhoods");
            if (chosen.type === "historic_ward" && chosen.era_year) {
              this.filterStore.setState({ wardEra: Number(chosen.era_year) });
            }
            if (layerKey === "historical_waterways") {
              this.filterStore.setLayerVisibility("historicalWaterways", true);
            } else if (layerKey === "historical_railroads") {
              this.filterStore.setLayerVisibility("historicalRailroads", true);
            }
            this.mapController.highlightBoundaryByIdOrName({
              id: chosen.id || "",
              name: chosen.name || chosenLabel,
              layerKey,
              eraYear: chosen.era_year || null,
              flyTo: true,
              inspect: true,
            });
            return;
          }

          this.mapController.flyToLocation({
            lng: chosen.lon,
            lat: chosen.lat,
            zoom: chosen.zoom || 17.2,
            hcadNum: chosen.hcad_num || "",
            featureId: chosen.id || "",
          });
          if (
            (chosen.type === "building" ||
              chosen.type === "landmark" ||
              chosen.type === "good_brick") &&
            document.getElementById("inspector-drawer")?.classList.contains("hidden")
          ) {
            this.renderInspectorDrawer({
              address: chosen.address || "",
              alt_addresses: chosen.alt_addresses || [],
              building_name: chosen.building_name || chosen.label,
              landmark_name: chosen.label,
              alt_names: chosen.alt_names || [],
              name_source: chosen.name_source || "",
              year_built: chosen.year_built,
              hcad_num: chosen.hcad_num,
              landmark_type: chosen.category,
              historic_district: chosen.historic_district || "",
              neighborhood: chosen.neighborhood || "",
              neighborhood_aliases: chosen.neighborhood_aliases || [],
              super_neighborhood: chosen.super_neighborhood || "",
              historic_ward: chosen.historic_ward || "",
              subdivision: chosen.subdivision || "",
              architect: chosen.architect || "",
              style: chosen.style || "",
              bld_style: chosen.bld_style || "",
              hcad_grade: chosen.hcad_grade || "",
              use_category: chosen.use_category || "",
              landuse_desc: chosen.landuse_desc || "",
              landmark_code: chosen.landmark_code || "",
              landmark_report_url: chosen.landmark_report_url || "",
              landmark_summary: chosen.landmark_summary || "",
            });
          }
        }
      });
    });
  }

  triggerMetadataFilterSearch(query, headerLabel = "") {
    const searchInput = document.getElementById("search-input");
    if (searchInput) {
      searchInput.value = query;
    }
    this._runSearchQuery(query, headerLabel || `Filter: ${query}`);
  }

  _computeGeometryCentroid(geom) {
    if (!geom || !geom.coordinates) return [NaN, NaN];
    if (geom.type === "Point" && Array.isArray(geom.coordinates)) {
      return [Number(geom.coordinates[0]), Number(geom.coordinates[1])];
    }
    const ring =
      geom.type === "Polygon"
        ? geom.coordinates[0]
        : geom.type === "MultiPolygon" && Array.isArray(geom.coordinates[0])
        ? geom.coordinates[0][0]
        : null;
    if (!Array.isArray(ring) || !ring.length) return [NaN, NaN];
    let sumLng = 0;
    let sumLat = 0;
    let count = 0;
    for (const pt of ring) {
      if (Array.isArray(pt) && Number.isFinite(pt[0]) && Number.isFinite(pt[1])) {
        sumLng += pt[0];
        sumLat += pt[1];
        count += 1;
      }
    }
    return count > 0 ? [sumLng / count, sumLat / count] : [NaN, NaN];
  }

  openCorrectionModal(props, opts = {}) {
    const modal = document.getElementById("correction-modal");
    if (!modal || !props) return;

    const addrEl = document.getElementById("corr-address");
    const bldNameEl = document.getElementById("corr-building-name");
    const hcadEl = document.getElementById("corr-hcad-num");
    const currYrEl = document.getElementById("corr-current-year");
    const suggYrEl = document.getElementById("corr-suggested-year");
    const distEl = document.getElementById("corr-district");
    const styleEl = document.getElementById("corr-style-arch");
    const citeEl = document.getElementById("corr-citation");
    const photoUrlEl = document.getElementById("corr-photo-url");
    const photoYearEl = document.getElementById("corr-photo-year");
    const photoCapEl = document.getElementById("corr-photo-caption");
    const photoDetailsEl = document.getElementById("corr-photo-details");
    const deedSubEl = document.getElementById("corr-deed-subdivision");
    const deedClerkEl = document.getElementById("corr-deed-clerk-file");
    const deedUrlEl = document.getElementById("corr-deed-url");
    const deedDetailsEl = document.getElementById("corr-deed-details");
    const feedbackEl = document.getElementById("corr-submit-feedback");

    const footprintSelectEl = document.getElementById("corr-footprint-issue");
    const footprintNotesWrapEl = document.getElementById("corr-footprint-notes-wrap");
    const footprintNotesEl = document.getElementById("corr-footprint-notes");
    const footprintBoxEl = document.getElementById("corr-footprint-box");
    const satLinkEl = document.getElementById("corr-satellite-check-link");
    const sourceTypeEl = document.getElementById("corr-source-type");

    const nameInfo = this._resolveBuildingNameAndAliases(props);
    if (addrEl) addrEl.value = props.address || nameInfo.primaryName || "Unknown Address";
    if (bldNameEl) {
      const allNames = nameInfo.hasCustomBuildingName
        ? [nameInfo.primaryName, ...nameInfo.altNames]
        : [...nameInfo.altNames];
      bldNameEl.value = allNames.join(" | ");
    }
    if (hcadEl) {
      const bldKey =
        props.building_id && String(props.building_id).includes("#")
          ? props.building_id
          : props.id && String(props.id).includes("#")
          ? props.id
          : props.hcad_num || "";
      hcadEl.value = bldKey;
    }
    if (currYrEl) currYrEl.value = props.original_hcad_year || props.year_built || "";
    if (suggYrEl) suggYrEl.value = props.is_curated_override ? props.year_built : "";
    if (distEl) distEl.value = props.historic_district || "";
    if (styleEl) styleEl.value = props.architect || props.bld_style || "";
    if (citeEl) citeEl.value = props.source_citation || "";
    if (sourceTypeEl) sourceTypeEl.value = "Houston City Directory";
    if (footprintSelectEl) footprintSelectEl.value = "";
    if (footprintNotesEl) {
      footprintNotesEl.value = "";
      footprintNotesEl.required = false;
    }
    if (footprintNotesWrapEl) footprintNotesWrapEl.classList.add("hidden");
    if (footprintBoxEl) footprintBoxEl.classList.remove("active");
    if (photoUrlEl) photoUrlEl.value = "";
    if (photoYearEl) photoYearEl.value = "";
    if (photoCapEl) photoCapEl.value = "";
    if (photoDetailsEl) photoDetailsEl.open = false;

    const subProps = props._platted_subdivision_props || null;
    const defaultSubName =
      opts.subdivisionName ||
      props.subdivision ||
      subProps?.name ||
      (props.overlay_layer === "platted_subdivisions" ? props.name : "") ||
      "";
    const defaultClerkCitation =
      opts.clerkCitation ||
      props.plat_citation ||
      subProps?.plat_citation ||
      props.deed_citation ||
      subProps?.deed_citation ||
      "";
    if (deedSubEl) deedSubEl.value = defaultSubName;
    if (deedClerkEl) deedClerkEl.value = defaultClerkCitation;
    if (deedUrlEl) deedUrlEl.value = "";
    if (deedDetailsEl) {
      deedDetailsEl.open = Boolean(opts.openDeedSection);
      if (opts.openDeedSection && deedUrlEl) {
        setTimeout(() => deedUrlEl.focus(), 60);
      }
    }

    if (feedbackEl) {
      feedbackEl.classList.add("hidden");
      feedbackEl.innerHTML = "";
    }

    // Compute exact building centroid for Zoom-20 Satellite Roof Verification link
    try {
      let satLat = Number(props.lat);
      let satLng = Number(props.lng || props.lon);
      if ((!Number.isFinite(satLat) || !Number.isFinite(satLng)) && this.mapController?.selectedFeatureGeometry) {
        const [gLng, gLat] = this._computeGeometryCentroid(this.mapController.selectedFeatureGeometry);
        if (Number.isFinite(gLat) && Number.isFinite(gLng)) {
          satLat = gLat;
          satLng = gLng;
        }
      }
      if ((!Number.isFinite(satLat) || !Number.isFinite(satLng)) && this.mapController?.map) {
        const c = this.mapController.map.getCenter();
        satLat = c.lat;
        satLng = c.lng;
      }
      if (Number.isFinite(satLat) && Number.isFinite(satLng)) {
        const satUrl = `https://www.google.com/maps/@?api=1&map_action=map&center=${satLat.toFixed(
          6
        )},${satLng.toFixed(6)}&zoom=20&basemap=satellite`;
        this._activeCorrectionSatelliteUrl = satUrl;
        if (satLinkEl) satLinkEl.href = satUrl;
      } else {
        this._activeCorrectionSatelliteUrl = "";
      }
    } catch (_err) {
      this._activeCorrectionSatelliteUrl = "";
    }

    const syncStatus = this.mapController?.sheetSyncStatus;
    const csvInput = document.getElementById("admin-sheet-csv-url");
    const webhookInput = document.getElementById("admin-webhook-url");
    if (csvInput && syncStatus?.csvUrl) csvInput.value = syncStatus.csvUrl;
    if (webhookInput && syncStatus?.webhookUrl) webhookInput.value = syncStatus.webhookUrl;

    modal.classList.remove("hidden");
  }

  _capturePreTourSnapshot() {
    const curState = this.filterStore.getState();
    const inspectorDrawer = document.getElementById("inspector-drawer");
    this._preTourSnapshot = {
      viewport: this.mapController.getCurrentViewport(),
      extrude3D: Boolean(curState.extrude3D),
      layers: { ...(curState.layers || {}) },
      selectedProps: this.mapController.selectedFeatureProps
        ? { ...this.mapController.selectedFeatureProps }
        : null,
      selectedGeom: this.mapController.selectedFeatureGeometry || null,
      inspectorOpen: Boolean(inspectorDrawer && !inspectorDrawer.classList.contains("hidden")),
      hadInitialUrlHash: Boolean(this._hadInitialUrlParams),
    };
  }

  _syncTourFocusPills() {
    const activeMode = this.mapController?.getTourFocusMode
      ? this.mapController.getTourFocusMode()
      : "all";
    document.querySelectorAll("#tour-focus-mode-bar .tour-focus-pill").forEach((pill) => {
      const mode = pill.getAttribute("data-tour-focus") || "all";
      pill.classList.toggle("active", mode === activeMode);
    });
  }

  _exitActiveTour({ restoreViewport = true } = {}) {
    const banner = document.getElementById("tour-narrative-banner");
    if (banner) banner.classList.add("hidden");

    const container = document.getElementById("tour-pills");
    if (container) {
      container.querySelectorAll(".tour-pill").forEach((b) => b.classList.remove("active"));
    }

    const snap = this._preTourSnapshot;
    this._activeTour = null;
    this._activeTourStopIndex = -1;
    this._preTourSnapshot = null;

    if (this.mapController.setTourFocusMode) {
      this.mapController.setTourFocusMode("all");
    }
    this.mapController.setTourRoute(null, -1);
    this._syncTourFocusPills();

    const targetExtrude3D = snap ? Boolean(snap.extrude3D) : false;
    const statePatch = { extrude3D: targetExtrude3D };
    if (snap && snap.layers) {
      statePatch.layers = { ...snap.layers };
    }
    this._suppressUrlUpdate = true;
    this.filterStore.setState(statePatch);
    this._suppressUrlUpdate = false;

    const inspectorDrawer = document.getElementById("inspector-drawer");
    if (restoreViewport && snap && snap.inspectorOpen && snap.selectedProps) {
      this.mapController.highlightAndInspectFeature(snap.selectedProps, snap.selectedGeom);
    } else {
      if (inspectorDrawer) inspectorDrawer.classList.add("hidden");
      this.mapController.clearSelection();
    }

    if (restoreViewport && snap && snap.viewport) {
      const vp = snap.viewport;
      this.mapController.flyToLocation({
        lng: vp.lng,
        lat: vp.lat,
        zoom: vp.zoom,
        pitch: targetExtrude3D ? Math.max(30, vp.pitch || 48) : 0,
      });
      if (!snap.hadInitialUrlHash && typeof window !== "undefined" && window.history?.replaceState) {
        window.history.replaceState(null, "", window.location.pathname + window.location.search);
      } else {
        this._updateUrlHash(this.filterStore.getState());
      }
    } else {
      const curVp = this.mapController.getCurrentViewport();
      this.mapController.flyToLocation({
        lng: curVp.lng,
        lat: curVp.lat,
        zoom: curVp.zoom > 16.4 ? 16.2 : curVp.zoom,
        pitch: targetExtrude3D ? Math.max(30, curVp.pitch || 45) : 0,
      });
      this._updateUrlHash(this.filterStore.getState());
    }
  }

  _renderTourPills() {
    const container = document.getElementById("tour-pills");
    if (!container) return;

    container.innerHTML = CURATED_TOURS.map(
      (t) => `
      <button type="button" class="tour-pill" id="tour-pill-${t.id}" data-tour-id="${t.id}">
        <span class="tour-pill-name">${t.name}</span>
        <span class="tour-pill-era">${t.era}</span>
      </button>`
    ).join("");

    container.querySelectorAll(".tour-pill").forEach((btn) => {
      btn.addEventListener("click", () => {
        const tourId = btn.getAttribute("data-tour-id");
        const tour = CURATED_TOURS.find((t) => t.id === tourId);
        if (!tour) return;

        // If clicking the already-active tour pill:
        // - If on a guided stop, return to the tour overview
        // - If already on the overview, exit the tour and restore pre-tour view
        if (this._activeTour && this._activeTour.id === tour.id) {
          if (this._activeTourStopIndex >= 0) {
            this._activateTourOverview(tour);
          } else {
            this._exitActiveTour({ restoreViewport: true });
          }
          return;
        }

        if (!this._activeTour) {
          this._capturePreTourSnapshot();
        }

        if (typeof window !== "undefined" && window.innerWidth <= 900) {
          this._setSidebarCollapsed(true);
        }

        container.querySelectorAll(".tour-pill").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");

        if (tour.id === "good_brick_highlights" && !this.filterStore.getState().layers.goodBrickAwards) {
          this.filterStore.setLayerVisibility("goodBrickAwards", true);
        }

        this._activateTourOverview(tour);
      });
    });

    const btnPrevStop = document.getElementById("btn-tour-prev-stop");
    const btnNextStop = document.getElementById("btn-tour-next-stop");
    const btnTourOverview = document.getElementById("btn-tour-overview");
    const btnExploreHere = document.getElementById("btn-tour-explore-here");
    const btnCloseBanner = document.getElementById("btn-close-tour-banner");

    if (btnPrevStop) {
      btnPrevStop.addEventListener("click", () => {
        if (!this._activeTour || !Array.isArray(this._activeTour.stops)) return;
        const nextIdx =
          this._activeTourStopIndex <= 0
            ? this._activeTour.stops.length - 1
            : this._activeTourStopIndex - 1;
        this._activateTourStop(this._activeTour, nextIdx);
      });
    }

    if (btnNextStop) {
      btnNextStop.addEventListener("click", () => {
        if (!this._activeTour || !Array.isArray(this._activeTour.stops)) return;
        const nextIdx =
          this._activeTourStopIndex + 1 >= this._activeTour.stops.length
            ? 0
            : this._activeTourStopIndex + 1;
        this._activateTourStop(this._activeTour, nextIdx);
      });
    }

    if (btnTourOverview) {
      btnTourOverview.addEventListener("click", () => {
        if (this._activeTour) {
          this._activateTourOverview(this._activeTour);
        }
      });
    }

    if (btnExploreHere) {
      btnExploreHere.addEventListener("click", () => {
        this._exitActiveTour({ restoreViewport: false });
      });
    }

    if (btnCloseBanner) {
      btnCloseBanner.addEventListener("click", () => {
        this._exitActiveTour({ restoreViewport: true });
      });
    }

    document.querySelectorAll("#tour-focus-mode-bar .tour-focus-pill").forEach((pill) => {
      pill.addEventListener("click", () => {
        const mode = pill.getAttribute("data-tour-focus") || "all";
        if (this.mapController.setTourFocusMode) {
          this.mapController.setTourFocusMode(mode);
        }
        this._syncTourFocusPills();
      });
    });
  }

  _activateTourOverview(tour) {
    if (!this._activeTour && !this._preTourSnapshot) {
      this._capturePreTourSnapshot();
    }
    this._activeTour = tour;
    this._activeTourStopIndex = -1;

    // Restore pre-tour 2D/3D preference when returning to overview from a 3D stop
    if (this._preTourSnapshot) {
      const want3D = Boolean(this._preTourSnapshot.extrude3D);
      if (this.filterStore.getState().extrude3D !== want3D) {
        this.filterStore.setState({ extrude3D: want3D });
      }
    }

    // Hide the right-hand Property Inspector drawer & clear any previously selected irrelevant property
    const inspectorDrawer = document.getElementById("inspector-drawer");
    if (inspectorDrawer) {
      inspectorDrawer.classList.add("hidden");
    }
    this.mapController.clearSelection();
    this.mapController.setTourRoute(tour, -1);
    this._syncTourFocusPills();

    const banner = document.getElementById("tour-narrative-banner");
    const bannerTitle = document.getElementById("tour-banner-title");
    const stepBadge = document.getElementById("tour-banner-step-badge");
    const bannerDesc = document.getElementById("tour-banner-desc");
    const controlsEl = document.getElementById("tour-banner-controls");
    const dotsEl = document.getElementById("tour-banner-stop-dots");
    const btnPrev = document.getElementById("btn-tour-prev-stop");
    const btnNext = document.getElementById("btn-tour-next-stop");
    const btnOverview = document.getElementById("btn-tour-overview");

    const stops = Array.isArray(tour.stops) ? tour.stops : [];

    if (bannerTitle) bannerTitle.textContent = `${tour.name} (${tour.era})`;
    if (stepBadge) {
      if (stops.length > 0) {
        const walkLabel = tour.walkMiles
          ? ` · ${new Intl.NumberFormat(undefined, {
              style: "unit",
              unit: "mile",
              unitDisplay: "short",
              maximumFractionDigits: 1,
            }).format(tour.walkMiles)} walk`
          : "";
        stepBadge.textContent = `${stops.length} Stops${walkLabel}`;
        stepBadge.classList.remove("hidden");
      } else {
        stepBadge.classList.add("hidden");
      }
    }
    if (bannerDesc) bannerDesc.textContent = tour.description;
    if (banner) banner.classList.remove("hidden");

    if (controlsEl && dotsEl) {
      if (stops.length > 0) {
        controlsEl.classList.remove("hidden");
        dotsEl.innerHTML = stops
          .map(
            (s, i) =>
              `<button type="button" class="tour-stop-dot mono" data-stop-idx="${i}" title="Stop ${
                i + 1
              }: ${s.title} (${s.year})">${i + 1}</button>`
          )
          .join("");
        dotsEl.querySelectorAll(".tour-stop-dot").forEach((dotBtn) => {
          dotBtn.addEventListener("click", () => {
            const idx = parseInt(dotBtn.getAttribute("data-stop-idx"), 10) || 0;
            this._activateTourStop(tour, idx);
          });
        });
        if (btnPrev) btnPrev.disabled = false;
        if (btnNext) btnNext.innerHTML = `Start Walking Tour (1/${stops.length}) &#9654;`;
        if (btnOverview) btnOverview.classList.add("hidden");
      } else {
        controlsEl.classList.add("hidden");
      }
    }

    this.mapController.flyToLocation({
      lng: tour.center[0],
      lat: tour.center[1],
      zoom: tour.zoom,
      pitch: this.filterStore.getState().extrude3D ? Math.max(45, tour.pitch) : 0,
    });
    this._updateUrlHash(this.filterStore.getState());
  }

  _activateTourStop(tour, stopIdx) {
    const stops = Array.isArray(tour?.stops) ? tour.stops : [];
    if (!stops.length) return;
    if (!this._activeTour && !this._preTourSnapshot) {
      this._capturePreTourSnapshot();
    }
    const idx = Math.max(0, Math.min(stops.length - 1, stopIdx));
    const stop = stops[idx];
    this._activeTour = tour;
    this._activeTourStopIndex = idx;

    this.mapController.setTourRoute(tour, idx);
    this._syncTourFocusPills();

    const bannerTitle = document.getElementById("tour-banner-title");
    const stepBadge = document.getElementById("tour-banner-step-badge");
    const bannerDesc = document.getElementById("tour-banner-desc");
    const dotsEl = document.getElementById("tour-banner-stop-dots");
    const btnNext = document.getElementById("btn-tour-next-stop");
    const btnOverview = document.getElementById("btn-tour-overview");

    if (bannerTitle) bannerTitle.textContent = stop.title;
    if (stepBadge) {
      stepBadge.textContent = `Stop ${idx + 1} of ${stops.length} · Built ${stop.year}`;
      stepBadge.classList.remove("hidden");
    }
    if (bannerDesc) bannerDesc.textContent = stop.story;

    if (dotsEl) {
      dotsEl.querySelectorAll(".tour-stop-dot").forEach((dotBtn, i) => {
        dotBtn.classList.toggle("active", i === idx);
      });
    }

    if (btnNext) {
      btnNext.innerHTML =
        idx + 1 < stops.length
          ? `Next Stop (${idx + 2}/${stops.length}) &#9654;`
          : `Restart Tour &#8634;`;
    }
    if (btnOverview) {
      btnOverview.classList.remove("hidden");
    }

    const usePitch = stop.pitch != null ? stop.pitch : 52;
    if (usePitch >= 25 && !this.filterStore.getState().extrude3D) {
      this.filterStore.setState({ extrude3D: true });
    }

    this.mapController.flyToLocation({
      lng: stop.lng,
      lat: stop.lat,
      zoom: stop.zoom || 17.6,
      pitch: usePitch,
      hcadNum: stop.hcad_num || "",
      featureId: stop.building_id || "",
    });
  }

  _renderLegend() {
    const container = document.getElementById("legend-items");
    if (!container) return;
    const state = this.filterStore.getState();
    const items = getLegendItems(state.colorMode, state.paletteStyle);

    container.innerHTML = items
      .map(
        (item) => `
        <div class="legend-row">
          <span class="legend-swatch" style="background:${item.color}"></span>
          <span class="legend-label">${item.label}</span>
        </div>`
      )
      .join("");
  }

  _syncControlsFromState(state) {
    // Color Mode Buttons
    document.querySelectorAll("[data-color-mode]").forEach((btn) => {
      btn.classList.toggle("active", btn.getAttribute("data-color-mode") === state.colorMode);
    });

    // Palette style row visibility (only relevant in 'year_built' mode)
    const paletteRow = document.getElementById("palette-style-row");
    if (paletteRow) {
      paletteRow.style.display = state.colorMode === "year_built" ? "flex" : "none";
    }
    const paletteSelect = document.getElementById("select-palette-style");
    if (paletteSelect && paletteSelect.value !== state.paletteStyle) {
      paletteSelect.value = state.paletteStyle;
    }

    // Render Mode Buttons
    document.querySelectorAll("[data-render-mode]").forEach((btn) => {
      btn.classList.toggle("active", btn.getAttribute("data-render-mode") === state.renderMode);
    });

    // Basemap Buttons
    document.querySelectorAll("[data-basemap]").forEach((btn) => {
      btn.classList.toggle("active", btn.getAttribute("data-basemap") === state.basemap);
    });

    // 3D Toggle Button
    const btn3d = document.getElementById("btn-toggle-3d");
    if (btn3d) {
      btn3d.classList.toggle("active", state.extrude3D);
      btn3d.textContent = state.extrude3D ? "3D Extrusion: ON" : "3D Extrusion: OFF";
    }

    const currentPitch = this.mapController ? this.mapController.getCameraPitch() : state.extrude3D ? 50 : 0;
    this._syncTiltControls(state.extrude3D ? Math.max(currentPitch, 30) : 0);

    // Sliders & Readout
    const minSlider = document.getElementById("slider-min-year");
    const maxSlider = document.getElementById("slider-max-year");
    if (minSlider) minSlider.value = String(state.minYear);
    if (maxSlider) maxSlider.value = String(state.maxYear);

    const yearBadge = document.getElementById("year-range-readout");
    if (yearBadge) {
      if (state.selectedDecade === "unknown") {
        yearBadge.textContent = "Undated / Exempt Only";
      } else if (state.selectedDecade !== "all") {
        yearBadge.textContent = `${state.selectedDecade}s Decade (${state.minYear}–${state.maxYear})`;
      } else {
        yearBadge.textContent = `${state.minYear} – ${state.maxYear}`;
      }
    }

    const decadeSelect = document.getElementById("select-decade");
    if (decadeSelect && decadeSelect.value !== String(state.selectedDecade)) {
      decadeSelect.value = String(state.selectedDecade);
    }

    const chkUnknown = document.getElementById("chk-show-unknown");
    if (chkUnknown) chkUnknown.checked = Boolean(state.showUnknownYears);

    const chkSyncAnnex = document.getElementById("chk-sync-annexation");
    if (chkSyncAnnex) chkSyncAnnex.checked = Boolean(state.syncAnnexationToTime);

    const btnPlay = document.getElementById("btn-timelapse-play");
    if (btnPlay) {
      btnPlay.classList.toggle("playing", state.isPlaying);
      btnPlay.innerHTML = state.isPlaying
        ? `<span class="play-icon">&#9646;&#9646;</span> Pause Time-Lapse`
        : `<span class="play-icon">&#9654;</span> Play Growth Time-Lapse`;
    }

    // Time-Travel Stepper Buttons & Labels
    const stepYrs = Number(state.stepYears) || 5;
    const unitLabel = stepYrs === 1 ? "1 yr" : `${stepYrs} yrs`;
    const prevLabelEl = document.getElementById("step-prev-label");
    const nextLabelEl = document.getElementById("step-next-label");
    if (prevLabelEl) prevLabelEl.textContent = `-${unitLabel}`;
    if (nextLabelEl) nextLabelEl.textContent = `+${unitLabel}`;

    const btnStepPrev = document.getElementById("btn-step-prev");
    const btnStepNext = document.getElementById("btn-step-next");
    const isWindowMode =
      (state.selectedDecade !== "all" && state.selectedDecade !== "unknown") ||
      state.minYear > 1836;
    const atStart = isWindowMode ? state.minYear <= 1836 : state.maxYear <= 1836;
    const atEnd = state.maxYear >= 2026;
    if (btnStepPrev) btnStepPrev.disabled = atStart;
    if (btnStepNext) btnStepNext.disabled = atEnd;

    document.querySelectorAll("[data-step-years]").forEach((btn) => {
      const btnYrs = parseInt(btn.getAttribute("data-step-years"), 10);
      btn.classList.toggle("active", btnYrs === stepYrs);
    });

    if (this.lastViewportStats && this.lastViewportStats.decadeCounts) {
      this._renderDecadeHistogram(this.lastViewportStats.decadeCounts);
    }

    // Master Building Footprints Card & Toggle
    const buildingsVisible = state.renderMode !== "none";
    const chkBuildings = document.getElementById("chk-layer-buildings");
    if (chkBuildings) {
      chkBuildings.checked = buildingsVisible;
    }
    const buildingsSublabel = document.getElementById("buildings-layer-sublabel");
    if (buildingsSublabel) {
      if (!buildingsVisible) {
        buildingsSublabel.textContent = "Hidden — showing overlays only";
      } else if (state.renderMode === "both") {
        buildingsSublabel.textContent = "1.51M structures + lots · Visible";
      } else if (state.renderMode === "parcels") {
        buildingsSublabel.textContent = "Tax parcels only · Visible";
      } else {
        buildingsSublabel.textContent = "1.51M structures · Visible";
      }
    }

    const activeOverlayKeys = Object.entries(state.layers || {})
      .filter(([, v]) => Boolean(v))
      .map(([k]) => k);
    const allOverlaysOff = activeOverlayKeys.length === 0;
    const buildingsOnlyActive = buildingsVisible && allOverlaysOff;

    const masterBuildingsCard = document.getElementById("master-buildings-card");
    if (masterBuildingsCard) {
      masterBuildingsCard.classList.toggle("is-hidden-layer", !buildingsVisible);
      masterBuildingsCard.classList.toggle("is-soloed", buildingsOnlyActive);
    }

    const btnSoloBuildings = document.getElementById("btn-solo-buildings");
    if (btnSoloBuildings) {
      btnSoloBuildings.classList.toggle("active", buildingsOnlyActive);
      btnSoloBuildings.textContent = buildingsOnlyActive ? "Only ✓" : "Only";
    }

    // Single-Layer Mode Toggle
    const btnSingleLayerMode = document.getElementById("btn-single-layer-mode");
    if (btnSingleLayerMode) {
      btnSingleLayerMode.classList.toggle("active", Boolean(state.singleLayerMode));
      btnSingleLayerMode.textContent = state.singleLayerMode ? "1-at-a-Time: ON" : "1-at-a-Time";
    }

    // Overlay Layer Checkboxes & Per-Row Solo State
    const mapLayerIds = {
      "chk-layer-good-brick": state.layers.goodBrickAwards,
      "chk-layer-landmarks": state.layers.landmarks,
      "chk-layer-historic-districts": state.layers.historicDistricts,
      "chk-layer-heritage-districts": state.layers.heritageDistricts,
      "chk-layer-nrhp-districts": state.layers.nrhpDistricts,
      "chk-layer-thc-markers": state.layers.thcMarkers,
      "chk-layer-neighborhoods": state.layers.neighborhoods,
      "chk-layer-platted-subdivisions": state.layers.plattedSubdivisions,
      "chk-layer-land-use-protections": state.layers.landUseProtections,
      "chk-layer-super-neighborhoods": state.layers.superNeighborhoods,
      "chk-layer-historic-wards": state.layers.historicWards,
      "chk-layer-annexations": state.layers.annexations,
      "chk-layer-waterways": state.layers.historicalWaterways,
      "chk-layer-railroads": state.layers.historicalRailroads,
      "chk-layer-historic-map": state.layers.historicMap,
    };
    for (const [id, checked] of Object.entries(mapLayerIds)) {
      const el = document.getElementById(id);
      if (el) el.checked = Boolean(checked);
    }

    const waterwaysLegendRow = document.getElementById("waterways-legend-row");
    if (waterwaysLegendRow) {
      waterwaysLegendRow.classList.toggle("hidden", !state.layers.historicalWaterways);
    }
    const railroadsLegendRow = document.getElementById("railroads-legend-row");
    if (railroadsLegendRow) {
      railroadsLegendRow.classList.toggle("hidden", !state.layers.historicalRailroads);
    }

    const wardEraRow = document.getElementById("historic-ward-era-row");
    if (wardEraRow) {
      wardEraRow.classList.toggle("hidden", !state.layers.historicWards);
    }
    const activeWardEra = resolveActiveWardEra(state);
    document.querySelectorAll("[data-ward-era]").forEach((btn) => {
      const era = parseInt(btn.getAttribute("data-ward-era"), 10) || 1903;
      btn.classList.toggle("active", era === activeWardEra);
    });

    const annexOptionsRow = document.getElementById("annexation-options-row");
    if (annexOptionsRow) {
      annexOptionsRow.classList.toggle("hidden", !state.layers.annexations);
    }
    const annexDecadeBadge = document.getElementById("annexation-active-decade-badge");
    if (annexDecadeBadge) {
      const activeDec = resolveActiveAnnexationDecade(state);
      annexDecadeBadge.textContent = `${
        activeDec === 1836 ? "1836" : `${activeDec}s`
      } City Limits`;
    }
    const chkAnnexSpokes = document.getElementById("chk-annexation-spokes");
    if (chkAnnexSpokes) {
      chkAnnexSpokes.checked = state.showAnnexationSpokes !== false;
    }

    const histOpacityRow = document.getElementById("historic-map-opacity-row");
    if (histOpacityRow) {
      histOpacityRow.classList.toggle("hidden", !state.layers.historicMap);
    }
    const histOpacitySlider = document.getElementById("slider-historic-map-opacity");
    const histOpacityVal = Math.max(15, Math.min(100, Number(state.historicMapOpacity) || 75));
    if (histOpacitySlider && Number(histOpacitySlider.value) !== histOpacityVal) {
      histOpacitySlider.value = String(histOpacityVal);
    }
    const histOpacityReadout = document.getElementById("historic-map-opacity-readout");
    if (histOpacityReadout) {
      histOpacityReadout.textContent = `${histOpacityVal}%`;
    }

    document.querySelectorAll(".layer-item-row[data-layer-key]").forEach((row) => {
      const key = row.getAttribute("data-layer-key");
      const isChecked = Boolean(state.layers[key]);
      const isSoloed = isChecked && activeOverlayKeys.length === 1;
      row.classList.toggle("is-unchecked", !isChecked);
      row.classList.toggle("is-soloed", isSoloed);

      const soloBtn = row.querySelector("[data-solo-layer]");
      if (soloBtn) {
        soloBtn.classList.toggle("active", isSoloed);
        soloBtn.textContent = isSoloed ? "Soloed ✓" : "Solo";
      }
    });
  }

  _syncTiltControls(pitch) {
    const roundedPitch = Math.max(0, Math.min(65, Math.round(Number(pitch) || 0)));
    const readout = document.getElementById("tilt-angle-readout");
    if (readout) {
      readout.textContent = new Intl.NumberFormat(undefined, {
        style: "unit",
        unit: "degree",
        unitDisplay: "narrow",
      }).format(roundedPitch);
    }
    const presets = [0, 30, 50, 65];
    let closestPreset = 0;
    if (roundedPitch >= 5) {
      closestPreset = presets.slice(1).reduce((prev, curr) =>
        Math.abs(curr - roundedPitch) < Math.abs(prev - roundedPitch) ? curr : prev
      );
    }
    document.querySelectorAll("[data-tilt-pitch]").forEach((btn) => {
      const btnPitch = parseInt(btn.getAttribute("data-tilt-pitch"), 10) || 0;
      btn.classList.toggle("active", btnPitch === closestPreset);
    });
  }

  _manageTimelapseLoop(state) {
    if (this.timelapseTimer) {
      clearInterval(this.timelapseTimer);
      this.timelapseTimer = null;
    }
    if (!state.isPlaying) return;

    const intervalMs = state.playSpeed === 5 ? 90 : state.playSpeed === 2 ? 180 : 320;
    const stepYears = state.playSpeed === 5 ? 3 : 2;

    this.timelapseTimer = setInterval(() => {
      const curr = this.filterStore.getState();
      if (!curr.isPlaying) {
        clearInterval(this.timelapseTimer);
        this.timelapseTimer = null;
        return;
      }
      const nextMax = curr.maxYear + stepYears;
      if (nextMax >= 2026) {
        this.filterStore.setState({
          maxYear: 2026,
          isPlaying: false,
        });
      } else {
        this.filterStore.setState({
          maxYear: nextMax,
        });
      }
    }, intervalMs);
  }

  handleViewportStats(stats) {
    this.lastViewportStats = stats;

    const visCountEl = document.getElementById("stat-viewport-count");
    if (visCountEl) {
      visCountEl.textContent = Number(stats.matchingFilterCount || 0).toLocaleString();
    }

    const oldestEl = document.getElementById("stat-viewport-oldest");
    if (oldestEl) {
      oldestEl.textContent = stats.oldestYear ? `${stats.oldestYear}` : "—";
    }

    const contribEl = document.getElementById("stat-viewport-contributing");
    if (contribEl) {
      contribEl.textContent = Number(stats.contributingCount || 0).toLocaleString();
    }

    const lmEl = document.getElementById("stat-viewport-landmarks");
    if (lmEl) {
      lmEl.textContent = Number(stats.landmarkCount || 0).toLocaleString();
    }

    this._renderDecadeHistogram(stats.decadeCounts || {});
    this._updateUrlHash(this.filterStore.getState());
  }

  _renderDecadeHistogram(decadeCounts) {
    const container = document.getElementById("decade-histogram");
    if (!container) return;

    const state = this.filterStore.getState();
    const decades = [];
    let maxCount = 1;

    for (let d = 1840; d <= 2020; d += 10) {
      const c = Number(decadeCounts[String(d)] || 0);
      if (d === 1840) {
        const c1830 = Number(decadeCounts["1830"] || 0);
        decades.push({ dec: 1840, label: "≤1840s", count: c + c1830 });
        maxCount = Math.max(maxCount, c + c1830);
      } else {
        decades.push({ dec: d, label: `${d}s`, count: c });
        maxCount = Math.max(maxCount, c);
      }
    }

    container.innerHTML = decades
      .map((item) => {
        const heightPct = item.count > 0 ? Math.max(10, Math.round((item.count / maxCount) * 100)) : 4;
        const barColor = getYearColorHex(item.dec + 4, state.paletteStyle);
        const inRange =
          state.selectedDecade === "all"
            ? item.dec + 9 >= state.minYear && item.dec <= state.maxYear
            : Number(state.selectedDecade) === item.dec;

        return `
          <button
            type="button"
            class="hist-bar-col ${inRange ? "in-range" : "dimmed"}"
            data-decade="${item.dec}"
            title="${item.label}: ${item.count.toLocaleString()} structures in view (click to filter)"
          >
            <div class="hist-bar-track">
              <div class="hist-bar-fill" style="height:${heightPct}%; background:${barColor};"></div>
            </div>
            <span class="hist-bar-label">${String(item.dec).slice(2)}s</span>
          </button>`;
      })
      .join("");

    container.querySelectorAll(".hist-bar-col").forEach((btn) => {
      btn.addEventListener("click", () => {
        const clickedDec = btn.getAttribute("data-decade");
        const currDec = String(this.filterStore.getState().selectedDecade);
        if (currDec === clickedDec) {
          this.filterStore.setState({
            selectedDecade: "all",
            minYear: 1836,
            maxYear: 2026,
            isPlaying: false,
          });
        } else {
          const dInt = parseInt(clickedDec, 10);
          this.filterStore.setState({
            selectedDecade: clickedDec,
            minYear: dInt === 1840 ? 1836 : dInt,
            maxYear: Math.min(2026, dInt + 9),
            stepYears: 10,
            isPlaying: false,
          });
        }
      });
    });
  }

  _renderMapOverlapBar(stack, activeIdx) {
    const bar = document.getElementById("map-overlap-bar");
    if (!bar) return;

    if (!Array.isArray(stack) || stack.length <= 1) {
      bar.classList.add("hidden");
      bar.innerHTML = "";
      return;
    }

    bar.classList.remove("hidden");
    const safeIdx = Math.max(0, Math.min(stack.length - 1, Number(activeIdx) || 0));

    bar.innerHTML = `
      <span class="map-overlap-label">
        &#9783; ${stack.length} Layers (${safeIdx + 1}/${stack.length})
      </span>
      ${stack
        .map((item, idx) => {
          const badge = item.typeBadge || item.badge || "Layer";
          const color = item.swatchColor || item.color || "#38bdf8";
          return `
            <button
              type="button"
              class="map-overlap-chip ${idx === safeIdx ? "active" : ""}"
              data-overlap-idx="${idx}"
              title="${String(badge).replace(/"/g, "&quot;")}: ${String(item.title || "").replace(/"/g, "&quot;")}"
            >
              <span class="overlap-pill-dot" style="background:${color};"></span>
              <span>${badge}: ${item.title}</span>
            </button>`;
        })
        .join("")}
      <button
        type="button"
        class="map-overlap-next-btn"
        data-overlap-step="1"
        title="Cycle to next underlying polygon or building at this spot"
      >
        Next Layer &#8594;
      </button>
    `;

    bar.querySelectorAll("[data-overlap-idx]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const idx = parseInt(btn.getAttribute("data-overlap-idx"), 10);
        if (Number.isFinite(idx) && this.mapController) {
          this.mapController.selectOverlapStackItem(idx);
        }
      });
    });

    bar.querySelectorAll("[data-overlap-step]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const step = parseInt(btn.getAttribute("data-overlap-step"), 10) || 1;
        if (this.mapController) {
          this.mapController.cycleOverlapStack(step);
        }
      });
    });
  }

  _buildInspectorOverlapStackHtml() {
    const stack = this.mapController?.overlapStack || [];
    const activeIdx = this.mapController?.overlapStackIndex || 0;
    if (!Array.isArray(stack) || stack.length <= 1) return "";
    const safeIdx = Math.max(0, Math.min(stack.length - 1, Number(activeIdx) || 0));

    return `
      <div class="inspector-overlap-stack" id="inspector-overlap-stack">
        <div class="inspector-overlap-header">
          <span class="inspector-overlap-title">
            &#9783; Overlapping Layers Here (${safeIdx + 1} of ${stack.length})
          </span>
          <button
            type="button"
            class="inspector-overlap-cycle-btn"
            data-inspector-overlap-step="1"
            title="Cycle to next underlying polygon at this point"
          >
            Next Layer &#8594;
          </button>
        </div>
        <div class="inspector-overlap-pills">
          ${stack
            .map((item, idx) => {
              const badge = item.typeBadge || item.badge || "Layer";
              const color = item.swatchColor || item.color || "#38bdf8";
              return `
                <button
                  type="button"
                  class="inspector-overlap-pill ${idx === safeIdx ? "active" : ""}"
                  data-inspector-overlap-idx="${idx}"
                >
                  <span class="overlap-pill-left">
                    <span class="overlap-pill-dot" style="background:${color};"></span>
                    <span class="overlap-pill-name">${item.title}</span>
                  </span>
                  <span class="overlap-pill-badge">${badge}</span>
                </button>`;
            })
            .join("")}
        </div>
        <div class="inspector-overlap-hint">
          Tip: Click the same spot on the map again or click any row above to inspect polygons underneath.
        </div>
      </div>
    `;
  }

  _bindInspectorOverlapStackEvents(container) {
    if (!container) return;
    container.querySelectorAll("[data-inspector-overlap-idx]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const idx = parseInt(btn.getAttribute("data-inspector-overlap-idx"), 10);
        if (Number.isFinite(idx) && this.mapController) {
          this.mapController.selectOverlapStackItem(idx);
        }
      });
    });

    container.querySelectorAll("[data-inspector-overlap-step]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const step = parseInt(btn.getAttribute("data-inspector-overlap-step"), 10) || 1;
        if (this.mapController) {
          this.mapController.cycleOverlapStack(step);
        }
      });
    });
  }

  _resolvePlattedSubdivisionProps(subNameOrProps) {
    if (!subNameOrProps) return null;
    if (typeof subNameOrProps === "object" && subNameOrProps.overlay_layer === "platted_subdivisions") {
      return subNameOrProps;
    }
    const rawName =
      typeof subNameOrProps === "string"
        ? subNameOrProps.trim()
        : String(subNameOrProps.subdivision || subNameOrProps.name || "").trim();
    if (!rawName) return null;

    const features = this.mapController?.overlaysData?.platted_subdivisions?.features || [];
    if (!features.length) return null;

    const norm = (s) =>
      String(s || "")
        .toUpperCase()
        .replace(/[^A-Z0-9\s]/g, " ")
        .replace(/\s+/g, " ")
        .trim();
    const stripSec = (s) =>
      norm(s)
        .replace(
          /\s+(?:SEC(?:TION)?\s*\w*|ADDN|ADDITION|ANNEX|U\s*R|UR|AMENDED|REPLAT|PARTIAL|PT|EXT|BLK\s*\w*)\b.*$/i,
          ""
        )
        .trim();

    const targetNorm = norm(rawName);
    const targetBase = stripSec(rawName);

    let exactMatch = null;
    let baseMatch = null;
    for (const f of features) {
      const p = f.properties;
      if (!p) continue;
      const pName = norm(p.name);
      const pFull = norm(p.full_name);
      if (pName === targetNorm || pFull === targetNorm) {
        exactMatch = p;
        break;
      }
      if (!baseMatch && targetBase && (pName === targetBase || stripSec(p.name) === targetBase)) {
        baseMatch = p;
      }
    }
    return exactMatch || baseMatch;
  }

  _resolveDeedCatalogEntry(subProps = null, fallbackSubName = "", fallbackNbhdName = "") {
    if (!this.deedCatalog || !this.deedCatalog.subdivisions) return null;
    const subs = this.deedCatalog.subdivisions;
    const byName = this.deedCatalog.by_name || {};

    if (subProps?.catalog_id && subs[subProps.catalog_id]) {
      return subs[subProps.catalog_id];
    }
    if (subProps?.id && subs[subProps.id]) {
      return subs[subProps.id];
    }

    const norm = (s) =>
      String(s || "")
        .toUpperCase()
        .replace(/[^A-Z0-9\s]/g, " ")
        .replace(/\s+/g, " ")
        .trim();
    const stripSec = (s) =>
      norm(s)
        .replace(
          /\s+(?:SEC(?:TION)?\s*\w*|ADDN|ADDITION|ANNEX|U\s*R|UR|AMENDED|REPLAT|PARTIAL|PT|EXT|BLK\s*\w*)\b.*$/i,
          ""
        )
        .trim();

    const candidates = [
      subProps?.name,
      subProps?.full_name,
      fallbackSubName,
      subProps?.neighborhood,
      fallbackNbhdName,
    ].filter(Boolean);

    for (const cand of candidates) {
      const n = norm(cand);
      if (byName[n] && subs[byName[n]]) {
        return subs[byName[n]];
      }
      const b = stripSec(cand);
      if (b && byName[b] && subs[byName[b]]) {
        return subs[byName[b]];
      }
      if (n === "BOULEVARD OAKS" && byName["BROADACRES"] && subs[byName["BROADACRES"]]) {
        return subs[byName["BROADACRES"]];
      }
    }
    return null;
  }

  _buildDeedRestrictionsCardHtml({
    subProps = null,
    catalogEntry = null,
    landUseProtections = [],
    rawSubdivisionName = "",
    neighborhoodName = "",
    isBoundary = false,
  } = {}) {
    const prots = Array.isArray(landUseProtections) ? landUseProtections : [];
    const docs = Array.isArray(catalogEntry?.documents) ? [...catalogEntry.documents] : [];
    const cov = catalogEntry?.covenant_summary || null;

    const subName =
      rawSubdivisionName ||
      subProps?.name ||
      catalogEntry?.subdivision_name ||
      neighborhoodName ||
      "";
    const platCitation =
      subProps?.plat_citation ||
      catalogEntry?.plat_citation ||
      (subProps?.vol_page ? `HCAD Plat Map Book Vol-Page ${subProps.vol_page}` : "");
    const deedCitation = subProps?.deed_citation || catalogEntry?.deed_citation || "";
    const civicClub = subProps?.civic_club || catalogEntry?.civic_association || "";
    const civicClubUrl = subProps?.civic_club_url || catalogEntry?.civic_association_url || "";

    const smlsCount = Number(subProps?.smls_count || 0);
    const smlsMinSqft = Number(subProps?.smls_min_sqft || 0);
    const smblCount = Number(subProps?.smbl_count || 0);
    const smblMinFt = Number(subProps?.smbl_min_ft || 0);

    if (
      !catalogEntry &&
      !platCitation &&
      !deedCitation &&
      !prots.length &&
      smlsCount === 0 &&
      smblCount === 0 &&
      !subName
    ) {
      return "";
    }

    // If rawSubdivisionName specifies a section number (e.g. "OAK FOREST SEC 5"), prioritize matching section PDFs first
    const secMatch = String(rawSubdivisionName || "").match(/\bSEC(?:TION)?\s*0*(\d{1,2})\b/i);
    if (secMatch && docs.length > 1) {
      const secNum = parseInt(secMatch[1], 10);
      const secPadded = String(secNum).padStart(2, "0");
      docs.sort((a, b) => {
        const aMatch =
          a.title.includes(`Section ${secPadded}`) ||
          new RegExp(`\\bSection\\s*0*${secNum}\\b`, "i").test(a.title);
        const bMatch =
          b.title.includes(`Section ${secPadded}`) ||
          new RegExp(`\\bSection\\s*0*${secNum}\\b`, "i").test(b.title);
        if (aMatch && !bMatch) return -1;
        if (!aMatch && bMatch) return 1;
        return 0;
      });
    }

    const hasDocs = docs.length > 0;
    const hasProtections = prots.length > 0 || smlsCount > 0 || smblCount > 0;

    let badgeText = "HCAD Plat Record";
    let badgeClass = "cyan";
    if (hasDocs) {
      badgeText = `${docs.length} Full-Text Deed PDF${docs.length === 1 ? "" : "s"}`;
      badgeClass = "gold";
    } else if (hasProtections) {
      badgeText = "Ch. 42 Protected";
      badgeClass = "amber";
    }

    const renderDocItem = (doc) => {
      const href = doc.local_url || doc.external_url || "#";
      const isMirrored = Boolean(doc.local_url);
      return `
        <div class="deed-doc-item">
          <div class="deed-doc-main">
            <a
              href="${href}"
              target="_blank"
              rel="noopener noreferrer"
              class="deed-doc-btn"
              title="Open full-text searchable PDF of ${String(doc.title || "").replace(/"/g, "&quot;")}"
            >
              <span class="deed-doc-icon">&#128196;</span>
              <span class="deed-doc-title">${doc.title}</span>
              <span class="deed-doc-tag">${isMirrored ? "Mirrored PDF &#8599;" : "Open PDF &#8599;"}</span>
            </a>
          </div>
          <div class="deed-doc-meta">
            ${
              doc.clerk_file
                ? `<span class="deed-clerk-pill mono" title="Harris County Clerk Instrument / Volume Reference">${doc.clerk_file}</span>`
                : ""
            }
            ${
              doc.doc_type
                ? `<span class="deed-type-pill">${doc.doc_type}</span>`
                : ""
            }
            ${
              doc.external_url && doc.local_url
                ? `<a href="${doc.external_url}" target="_blank" rel="noopener noreferrer" class="deed-ext-link" title="View original civic association source link">Civic Club Source &#8599;</a>`
                : ""
            }
          </div>
        </div>
      `;
    };

    const primaryDocs = docs.slice(0, 4);
    const extraDocs = docs.slice(4);

    const docsHtml = hasDocs
      ? `<div class="deed-docs-section">
          <div class="deed-subhead">
            <span>Full-Text Recorded Deed Restrictions &amp; Covenants (Archival Mirror)</span>
          </div>
          <div class="deed-doc-list">
            ${primaryDocs.map(renderDocItem).join("")}
          </div>
          ${
            extraDocs.length > 0
              ? `<details class="deed-doc-more">
                  <summary class="deed-doc-more-summary">
                    Show all ${docs.length} Section Deed Restriction PDFs (+${extraDocs.length} more)
                  </summary>
                  <div class="deed-doc-list" style="margin-top:6px;">
                    ${extraDocs.map(renderDocItem).join("")}
                  </div>
                </details>`
              : ""
          }
        </div>`
      : "";

    const covenantGridHtml = cov
      ? `<div class="deed-covenant-section">
          <div class="deed-subhead">
            <span>Structured Covenant &amp; Architectural Digest</span>
            ${
              catalogEntry?.plat_year
                ? `<span class="deed-plat-year-pill mono">Platted ${catalogEntry.plat_year}</span>`
                : ""
            }
          </div>
          <div class="deed-covenant-grid">
            ${
              cov.allowed_use
                ? `<div class="deed-cov-cell">
                    <span class="deed-cov-k">Permitted Land Use</span>
                    <span class="deed-cov-v">${cov.allowed_use}</span>
                  </div>`
                : ""
            }
            ${
              cov.min_lot_size
                ? `<div class="deed-cov-cell">
                    <span class="deed-cov-k">Min. Lot Size / Area</span>
                    <span class="deed-cov-v">${cov.min_lot_size}</span>
                  </div>`
                : ""
            }
            ${
              cov.front_setback
                ? `<div class="deed-cov-cell">
                    <span class="deed-cov-k">Front Building Line</span>
                    <span class="deed-cov-v">${cov.front_setback}</span>
                  </div>`
                : ""
            }
            ${
              cov.side_rear_setback
                ? `<div class="deed-cov-cell">
                    <span class="deed-cov-k">Side / Rear Setbacks</span>
                    <span class="deed-cov-v">${cov.side_rear_setback}</span>
                  </div>`
                : ""
            }
            ${
              cov.max_height
                ? `<div class="deed-cov-cell">
                    <span class="deed-cov-k">Max Height / Scale</span>
                    <span class="deed-cov-v">${cov.max_height}</span>
                  </div>`
                : ""
            }
            ${
              cov.plan_review
                ? `<div class="deed-cov-cell">
                    <span class="deed-cov-k">Architectural Review</span>
                    <span class="deed-cov-v">${cov.plan_review}</span>
                  </div>`
                : ""
            }
          </div>
          ${
            cov.notes
              ? `<div class="deed-covenant-notes">${cov.notes}</div>`
              : ""
          }
        </div>`
      : "";

    let protectionsHtml = "";
    if (prots.length > 0) {
      protectionsHtml = `
        <div class="deed-protections-section">
          <div class="deed-subhead">
            <span>City of Houston Chapter 42 Land-Use Protections Here (${prots.length})</span>
          </div>
          <div class="deed-protection-list">
            ${prots
              .map((pr) => {
                const pt = pr.protection_type || "smls";
                const pillClass =
                  pt === "smbl" ? "emerald" : pt === "conservation" ? "rose" : "amber";
                const icon = pt === "smbl" ? "&#128207;" : pt === "conservation" ? "&#127963;" : "&#128737;";
                const details = [];
                if (pr.ordinance) details.push(`Ord. #${pr.ordinance}`);
                if (Number(pr.min_lot_sqft) > 0) {
                  details.push(`Min Lot: ${Number(pr.min_lot_sqft).toLocaleString()} sq ft`);
                }
                if (Number(pr.min_bldg_line_ft) > 0) {
                  details.push(`Min Setback: ${pr.min_bldg_line_ft} ft`);
                }
                if (pr.expiration_date) {
                  details.push(`Active thru ${String(pr.expiration_date).slice(0, 4)}`);
                }
                return `
                  <div class="deed-protection-item ${pillClass}">
                    <div class="deed-protection-top">
                      <span class="deed-protection-badge">${icon} ${pr.type_label || "Chapter 42 Protection"}</span>
                      <button
                        type="button"
                        class="inspector-boundary-btn"
                        data-highlight-boundary="${String(pr.id || pr.name || "").replace(/"/g, "&quot;")}"
                        data-boundary-layer="land_use_protections"
                        title="Highlight this Chapter 42 protection boundary on the map"
                      >Outline</button>
                    </div>
                    <div class="deed-protection-name">${pr.name || "Protected Blockface"}</div>
                    ${
                      details.length
                        ? `<div class="deed-protection-meta mono">${details.join(" · ")}</div>`
                        : ""
                    }
                  </div>
                `;
              })
              .join("")}
          </div>
        </div>
      `;
    } else if (smlsCount > 0 || smblCount > 0) {
      const summaryPills = [];
      if (smlsCount > 0) {
        const smlsOrds = Array.isArray(subProps?.smls_ordinances)
          ? subProps.smls_ordinances.slice(0, 3).join(", ")
          : "";
        summaryPills.push(`
          <div class="deed-protection-item amber">
            <div class="deed-protection-top">
              <span class="deed-protection-badge">&#128737; Chapter 42 Special Minimum Lot Size (SMLS)</span>
              <span class="deed-protection-count mono">${smlsCount} protected area${smlsCount === 1 ? "" : "s"}</span>
            </div>
            <div class="deed-protection-meta mono">
              ${smlsMinSqft > 0 ? `Min Lot Size: <strong>${smlsMinSqft.toLocaleString()} sq ft</strong>` : "Prevents townhouse lot splitting"}
              ${smlsOrds ? ` · Ord. #${smlsOrds}` : ""}
            </div>
          </div>
        `);
      }
      if (smblCount > 0) {
        const smblOrds = Array.isArray(subProps?.smbl_ordinances)
          ? subProps.smbl_ordinances.slice(0, 3).join(", ")
          : "";
        summaryPills.push(`
          <div class="deed-protection-item emerald">
            <div class="deed-protection-top">
              <span class="deed-protection-badge">&#128207; Chapter 42 Special Minimum Building Line (SMBL)</span>
              <span class="deed-protection-count mono">${smblCount} protected blockface${smblCount === 1 ? "" : "s"}</span>
            </div>
            <div class="deed-protection-meta mono">
              ${smblMinFt > 0 ? `Min Front Setback: <strong>${smblMinFt} ft</strong>` : "Preserves historic front yard setback"}
              ${smblOrds ? ` · Ord. #${smblOrds}` : ""}
            </div>
          </div>
        `);
      }
      protectionsHtml = `
        <div class="deed-protections-section">
          <div class="deed-subhead">
            <span>City of Houston Chapter 42 Lot &amp; Setback Protections</span>
          </div>
          <div class="deed-protection-list">
            ${summaryPills.join("")}
          </div>
        </div>
      `;
    }

    const clerkCopyQuery = subName || platCitation || "";

    return `
      <div class="deed-restrictions-card ${hasDocs ? "has-docs" : ""}">
        <div class="deed-card-header">
          <span class="deed-card-kicker">&#128220; Deed Restrictions, Plat &amp; Land-Use Protections</span>
          <span class="deed-status-badge ${badgeClass}">${badgeText}</span>
        </div>

        <div class="deed-citation-banner">
          ${
            subName
              ? `<div class="deed-citation-row">
                  <span class="deed-cit-label">Platted Subdivision:</span>
                  <span class="deed-cit-val"><strong>${subName}</strong></span>
                </div>`
              : ""
          }
          ${
            platCitation
              ? `<div class="deed-citation-row">
                  <span class="deed-cit-label">HCAD Plat Record:</span>
                  <span class="deed-cit-val mono">${platCitation}</span>
                </div>`
              : ""
          }
          ${
            deedCitation
              ? `<div class="deed-citation-row">
                  <span class="deed-cit-label">Deed Covenant Filing:</span>
                  <span class="deed-cit-val">${deedCitation}</span>
                </div>`
              : ""
          }
          ${
            civicClub
              ? `<div class="deed-citation-row">
                  <span class="deed-cit-label">Civic Association:</span>
                  <span class="deed-cit-val">
                    ${
                      civicClubUrl
                        ? `<a href="${civicClubUrl}" target="_blank" rel="noopener noreferrer" class="deed-civic-link">${civicClub} &#8599;</a>`
                        : civicClub
                    }
                  </span>
                </div>`
              : ""
          }
        </div>

        ${covenantGridHtml}
        ${docsHtml}
        ${protectionsHtml}

        <div class="deed-clerk-footer">
          <div class="deed-clerk-actions">
            <button
              type="button"
              class="deed-action-btn clerk-btn"
              data-copy-clerk-query="${String(clerkCopyQuery).replace(/"/g, "&quot;")}"
              title="Copies '${String(clerkCopyQuery).replace(/"/g, "&quot;")}' to your clipboard and opens the Harris County Clerk Real Property & Map Book Archive"
            >
              &#127963; Copy Plat Name &amp; Search County Clerk Archive &#8599;
            </button>
            <button
              type="button"
              class="deed-action-btn contrib-btn"
              data-contribute-deed-sub="${String(subName).replace(/"/g, "&quot;")}"
              data-contribute-deed-cit="${String(deedCitation || platCitation).replace(/"/g, "&quot;")}"
              title="Submit a link to a neighborhood deed restriction PDF or Harris County Clerk File # for this subdivision"
            >
              + Contribute Deed Restriction PDF / Clerk #
            </button>
          </div>
        </div>
      </div>
    `;
  }

  _bindDeedRestrictionsCardEvents(container, contextProps) {
    if (!container) return;

    container.querySelectorAll("[data-copy-clerk-query]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const q = btn.getAttribute("data-copy-clerk-query") || "";
        if (q && navigator.clipboard) {
          navigator.clipboard.writeText(q);
        }
        const origHtml = btn.innerHTML;
        btn.innerHTML = `&#10003; Copied "${q.slice(0, 22)}${q.length > 22 ? "…" : ""}" · Opening Clerk Search &#8599;`;
        window.open(
          "https://www.cclerk.hctx.net/Applications/WebSearch/RP.aspx",
          "_blank",
          "noopener,noreferrer"
        );
        setTimeout(() => {
          btn.innerHTML = origHtml;
        }, 2800);
      });
    });

    container.querySelectorAll("[data-contribute-deed-sub]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const subName = btn.getAttribute("data-contribute-deed-sub") || "";
        const cit = btn.getAttribute("data-contribute-deed-cit") || "";
        this.openCorrectionModal(contextProps || {}, {
          openDeedSection: true,
          subdivisionName: subName,
          clerkCitation: cit,
        });
      });
    });
  }

  _renderBoundaryInspectorDrawer(rawProps, drawer, content) {
    const parseJsonList = (val) => {
      if (Array.isArray(val)) return val.map((x) => String(x || "").trim()).filter(Boolean);
      if (typeof val === "string") {
        const s = val.trim();
        if (s.startsWith("[")) {
          try {
            const parsed = JSON.parse(s);
            return Array.isArray(parsed)
              ? parsed.map((x) => String(x || "").trim()).filter(Boolean)
              : [];
          } catch (_e) {
            return [];
          }
        }
        if (s) {
          return s
            .split(/\s*\|\s*|\s*;\s*/)
            .map((x) => x.trim())
            .filter(Boolean);
        }
      }
      return [];
    };

    const overlayLayer = String(rawProps.overlay_layer || "neighborhoods");
    const name = String(
      rawProps.name || rawProps.era_label || rawProps.historic_district || "Houston Boundary"
    ).trim();
    const fullName = String(rawProps.full_name || "").trim();
    const volPage = String(rawProps.vol_page || "").trim();
    const recNum = String(rawProps.recnum || rawProps.deed_num || "").trim();
    const parentNbhd = String(rawProps.neighborhood || "").trim();
    const aliases = parseJsonList(rawProps.aliases || rawProps.alt_names);
    const topSubs = parseJsonList(rawProps.top_subdivisions);
    const snName = String(rawProps.super_neighborhood || "").trim();
    const wardName = String(rawProps.historic_ward || "").trim();
    const wardEra = Number(rawProps.era || rawProps.ward_era || rawProps.era_year || 0);
    const eraLabel = String(rawProps.era_label || rawProps.display_title || "").trim();

    let badgeLabel = "Neighborhood / Subdivision";
    let badgeColor = "#38bdf8";
    if (overlayLayer === "platted_subdivisions") {
      if (rawProps.has_deed_docs) {
        badgeLabel = `📜 Deed-Documented Subdivision (${rawProps.deed_doc_count || 1} PDF${
          Number(rawProps.deed_doc_count) === 1 ? "" : "s"
        })`;
        badgeColor = "#fbbf24";
      } else {
        badgeLabel = "HCAD Platted Subdivision";
        badgeColor = "#2dd4bf";
      }
    } else if (overlayLayer === "land_use_protections") {
      badgeLabel = rawProps.type_label || "Chapter 42 Land-Use Protection";
      const pt = rawProps.protection_type;
      badgeColor = pt === "smbl" ? "#34d399" : pt === "conservation" ? "#fb7185" : "#fbbf24";
    } else if (overlayLayer === "super_neighborhoods") {
      badgeLabel = `COH Super Neighborhood${rawProps.sn_id || rawProps.poly_id ? ` #${rawProps.sn_id || rawProps.poly_id}` : ""}`;
      badgeColor = "#818cf8";
    } else if (overlayLayer === "historic_wards") {
      badgeLabel = `Historic Ward${wardEra === 1903 ? " (1903–05)" : wardEra ? ` (${wardEra})` : ""}`;
      badgeColor = String(rawProps.color || "#fb923c");
    } else if (overlayLayer === "historic_districts") {
      badgeLabel = "COH Historic District";
      badgeColor = "#a855f7";
    } else if (overlayLayer === "heritage_districts") {
      badgeLabel = "Community Heritage District";
      badgeColor = "#ec4899";
    } else if (overlayLayer === "nrhp_districts") {
      badgeLabel = "National Register District (NRHP)";
      badgeColor = "#10b981";
    } else if (overlayLayer === "annexations") {
      const annexYr = rawProps.annex_year || rawProps.decade || "";
      badgeLabel = `Municipal Annexation${annexYr ? ` (${annexYr})` : ""}`;
      badgeColor = "#f43f5e";
    } else if (overlayLayer === "historical_waterways") {
      badgeLabel = rawProps.type_label || "Historical Waterway";
      const wt = rawProps.waterway_type;
      badgeColor =
        wt === "buried_gully"
          ? "#fbbf24"
          : wt === "historic_oxbow"
          ? "#a78bfa"
          : wt === "bayou"
          ? "#0ea5e9"
          : "#22d3ee";
    } else if (overlayLayer === "historical_railroads") {
      badgeLabel = rawProps.type_label || "Historical Railroad";
      const rt = rawProps.rail_type;
      badgeColor =
        rt === "depot"
          ? "#fde047"
          : rt === "abandoned_trail"
          ? "#fb7185"
          : rt === "streetcar_interurban"
          ? "#c084fc"
          : rt === "industrial_spur"
          ? "#94a3b8"
          : "#f59e0b";
    }

    const isLinearOrPointOverlay =
      overlayLayer === "historical_waterways" || overlayLayer === "historical_railroads";

    const bldCount = Number(rawProps.building_count || 0);
    const earliestYear = Number(rawProps.earliest_year || 0);
    const medianYear = Number(rawProps.median_year || 0);
    const pre1940Count = Number(rawProps.pre_1940_count || 0);
    const landmarkCount = Number(rawProps.landmark_count || 0);
    const goodBrickCount = Number(rawProps.good_brick_count || 0);

    const subtitleParts = [];
    if (overlayLayer === "historical_waterways") {
      if (rawProps.status) subtitleParts.push(rawProps.status);
      if (rawProps.watershed) subtitleParts.push(`Watershed: ${rawProps.watershed}`);
      if (Number(rawProps.length_miles) > 0) subtitleParts.push(`${rawProps.length_miles} mi mapped`);
    } else if (overlayLayer === "historical_railroads") {
      if (rawProps.historic_company) subtitleParts.push(rawProps.historic_company);
      if (rawProps.charter_year) subtitleParts.push(`Opened/Chartered ${rawProps.charter_year}`);
      if (rawProps.status) subtitleParts.push(rawProps.status);
    } else if (overlayLayer === "platted_subdivisions") {
      if (rawProps.plat_citation) {
        subtitleParts.push(rawProps.plat_citation);
      } else {
        if (volPage) subtitleParts.push(`Plat Vol-Page: ${volPage}`);
        if (recNum) subtitleParts.push(`Clerk Filing #${recNum}`);
      }
      if (parentNbhd && parentNbhd.toLowerCase() !== name.toLowerCase()) {
        subtitleParts.push(`In ${parentNbhd}`);
      }
    } else if (overlayLayer === "land_use_protections") {
      if (rawProps.ordinance) subtitleParts.push(`Ordinance #${rawProps.ordinance}`);
      if (Number(rawProps.min_lot_sqft) > 0) {
        subtitleParts.push(`Min Lot Size: ${Number(rawProps.min_lot_sqft).toLocaleString()} sq ft`);
      }
      if (Number(rawProps.min_bldg_line_ft) > 0) {
        subtitleParts.push(`Min Front Setback: ${rawProps.min_bldg_line_ft} ft`);
      }
    }
    if (eraLabel && overlayLayer === "historic_wards") subtitleParts.push(eraLabel);
    if (snName && overlayLayer !== "super_neighborhoods") {
      subtitleParts.push(`Super Neighborhood: ${snName}`);
    }
    if (wardName && overlayLayer !== "historic_wards") {
      subtitleParts.push(`1903–05 ${wardName}`);
    }
    const subtitle = subtitleParts.join(" · ") || "Harris County, Texas";

    const akaHeroHtml =
      aliases.length > 0
        ? `<div class="inspector-aka-bar" id="inspector-aka-bar">
            <span class="inspector-aka-label">Historical &amp; Colloquial Names:</span>
            <div class="inspector-aka-chips">
              ${aliases
                .map(
                  (alias) => `<button
                    type="button"
                    class="inspector-alias-chip inspector-filter-chip"
                    data-filter-chip="${alias.replace(/"/g, "&quot;")}"
                    data-filter-label="Area / Alias: ${alias.replace(/"/g, "&quot;")}"
                    title="Click to filter structures matching '${alias.replace(/"/g, "&quot;")}'"
                  >${alias} &#128269;</button>`
                )
                .join("")}
            </div>
          </div>`
        : "";

    const topSubsHtml =
      topSubs.length > 0
        ? `<div class="inspector-cell full">
            <span class="cell-label">Major Platted HCAD Subdivisions in ${name}</span>
            <span class="cell-value inspector-chip-group">
              ${topSubs
                .map(
                  (sub) => `<button
                    type="button"
                    class="inspector-filter-chip"
                    data-filter-chip="${sub.replace(/"/g, "&quot;")}"
                    data-filter-label="Subdivision: ${sub.replace(/"/g, "&quot;")}"
                    title="Click to find structures in subdivision '${sub.replace(/"/g, "&quot;")}'"
                  >${sub} &#128269;</button>`
                )
                .join('<span class="inspector-chip-sep" aria-hidden="true">·</span>')}
            </span>
          </div>`
        : "";

    // Resolve Deed Restrictions & Land-Use Protections Card for Platted Subdivisions, Neighborhoods, and Land-Use Protections
    const resolvedSubProps =
      overlayLayer === "platted_subdivisions"
        ? rawProps
        : overlayLayer === "neighborhoods"
        ? this._resolvePlattedSubdivisionProps(name)
        : null;
    const resolvedCatalogEntry =
      overlayLayer === "platted_subdivisions" || overlayLayer === "neighborhoods"
        ? this._resolveDeedCatalogEntry(resolvedSubProps, name, parentNbhd || name)
        : null;
    const boundaryDeedCardHtml =
      overlayLayer === "platted_subdivisions" ||
      overlayLayer === "land_use_protections" ||
      (overlayLayer === "neighborhoods" && (resolvedCatalogEntry || resolvedSubProps))
        ? this._buildDeedRestrictionsCardHtml({
            subProps: resolvedSubProps,
            catalogEntry: resolvedCatalogEntry,
            landUseProtections: overlayLayer === "land_use_protections" ? [rawProps] : [],
            rawSubdivisionName: overlayLayer === "platted_subdivisions" ? name : resolvedSubProps?.name || "",
            neighborhoodName: overlayLayer === "neighborhoods" ? name : parentNbhd,
            isBoundary: true,
          })
        : "";

    const overlapStackHtml = this._buildInspectorOverlapStackHtml();
    const curIso = this.mapController ? this.mapController.getIsolatedBoundary() : null;
    const isCurrentlyIsolated = Boolean(
      curIso &&
        ((rawProps.id && curIso.id === rawProps.id) ||
          curIso.name.toLowerCase() === String(name || "").trim().toLowerCase())
    );
    const activeIsoMode = isCurrentlyIsolated ? curIso.mode : "contents";

    const isolationCardHtml = isLinearOrPointOverlay
      ? ""
      : `
      <div class="inspector-isolation-card">
        <div class="inspector-isolation-header">
          <span class="inspector-isolation-kicker">&#127919; Isolate Area &amp; Merch / Print Export</span>
          <span class="mono" style="font-size:10px;color:${isCurrentlyIsolated ? "#FDE68A" : "var(--text-secondary)"};">
            ${isCurrentlyIsolated ? "ACTIVE ISOLATION" : "Figure-Ground &amp; SVG"}
          </span>
        </div>
        <p class="inspector-isolation-desc">
          Solo <strong>${name}</strong> on the map to hide all outside buildings and overlays, or open the Vector Studio to save a T-shirt, coaster, or print design.
        </p>
        <div class="inspector-isolation-modes" role="group" aria-label="Isolation Display Mode">
          <button
            type="button"
            class="inspector-iso-pill ${isCurrentlyIsolated && activeIsoMode === "contents" ? "active" : ""}"
            data-inspector-iso-mode="contents"
            title="Show the boundary border plus all building footprints and markers inside"
          >
            Border + Buildings
          </button>
          <button
            type="button"
            class="inspector-iso-pill ${isCurrentlyIsolated && activeIsoMode === "footprints_only" ? "active" : ""}"
            data-inspector-iso-mode="footprints_only"
            title="Show only the collection of building footprints inside this polygon"
          >
            Buildings Only
          </button>
          <button
            type="button"
            class="inspector-iso-pill ${isCurrentlyIsolated && activeIsoMode === "border_only" ? "active" : ""}"
            data-inspector-iso-mode="border_only"
            title="Show only the distinctive polygon border silhouette"
          >
            Border Silhouette
          </button>
        </div>
        <div class="inspector-isolation-actions">
          <button
            type="button"
            class="inspector-btn ${isCurrentlyIsolated ? "secondary" : "primary"}"
            id="btn-inspector-toggle-isolate"
          >
            ${isCurrentlyIsolated ? "&#10005; Exit Isolation Mode" : "&#127919; Isolate This Area"}
          </button>
          <button
            type="button"
            class="inspector-btn primary"
            id="btn-inspector-open-export-studio"
            style="background:linear-gradient(135deg,#B45309,#D97706);border-color:#FBBF24;color:#FFFBEB;"
          >
            &#127912; Vector / Shirt Studio
          </button>
        </div>
      </div>
    `;

    const narrativeDesc = rawProps.historic_significance || rawProps.description || "";

    content.innerHTML = `
      ${overlapStackHtml}
      <div class="inspector-hero">
        <div class="inspector-badges">
          <span class="inspector-year-pill" style="background:${badgeColor};color:#090d16;">${badgeLabel}</span>
          ${
            earliestYear >= 1836
              ? `<span class="inspector-age-pill">Earliest Structure: ${earliestYear}</span>`
              : rawProps.charter_year
              ? `<span class="inspector-age-pill">Era: ${rawProps.charter_year}</span>`
              : ""
          }
        </div>
        <h2 class="inspector-title" id="inspector-property-title">${name}</h2>
        ${akaHeroHtml}
        <p class="inspector-subtitle">${subtitle}</p>
        <div class="inspector-status-banner">
          <span class="status-dot" style="background:${badgeColor};"></span>
          <span>${rawProps.source || "City of Houston &amp; HCAD Boundary Index"}</span>
        </div>
      </div>

      ${isolationCardHtml}

      ${boundaryDeedCardHtml}

      ${
        narrativeDesc
          ? `<div class="ph-verified-override-card" style="border-left-color:${badgeColor};">
              <div class="ph-verified-header">
                <span>${isLinearOrPointOverlay ? "Historical &amp; Architectural Significance" : "Geographic &amp; Historical Context"}</span>
              </div>
              <div class="ph-verified-citation">
                ${narrativeDesc}
              </div>
            </div>`
          : ""
      }

      ${
        bldCount > 0 || earliestYear >= 1836
          ? `<div class="boundary-dossier-stats-grid">
              <div class="boundary-stat-card">
                <span class="boundary-stat-label">Recorded Structures</span>
                <span class="boundary-stat-val mono">${bldCount > 0 ? bldCount.toLocaleString() : "-"}</span>
              </div>
              <div class="boundary-stat-card">
                <span class="boundary-stat-label">Earliest Structure</span>
                <span class="boundary-stat-val mono">${earliestYear >= 1836 ? earliestYear : "-"}</span>
              </div>
              <div class="boundary-stat-card">
                <span class="boundary-stat-label">Median Build Year</span>
                <span class="boundary-stat-val mono">${medianYear >= 1836 ? medianYear : "-"}</span>
              </div>
              <div class="boundary-stat-card">
                <span class="boundary-stat-label">Pre-1940 Structures</span>
                <span class="boundary-stat-val mono">${pre1940Count > 0 ? pre1940Count.toLocaleString() : "0"}</span>
              </div>
              <div class="boundary-stat-card">
                <span class="boundary-stat-label">Designated Landmarks</span>
                <span class="boundary-stat-val mono">${landmarkCount > 0 ? landmarkCount.toLocaleString() : "0"}</span>
              </div>
              <div class="boundary-stat-card">
                <span class="boundary-stat-label">Good Brick Awards</span>
                <span class="boundary-stat-val mono">${goodBrickCount > 0 ? goodBrickCount.toLocaleString() : "0"}</span>
              </div>
            </div>`
          : ""
      }

      <div class="inspector-grid">
        ${
          overlayLayer === "historical_waterways" && rawProps.status
            ? `<div class="inspector-cell full">
                <span class="cell-label">Channel &amp; Culvert Status</span>
                <span class="cell-value">${rawProps.status}</span>
              </div>`
            : ""
        }
        ${
          overlayLayer === "historical_waterways" && rawProps.watershed
            ? `<div class="inspector-cell">
                <span class="cell-label">Primary Watershed</span>
                <span class="cell-value">${rawProps.watershed}</span>
              </div>`
            : ""
        }
        ${
          overlayLayer === "historical_waterways" && rawProps.hcfcd_unit
            ? `<div class="inspector-cell">
                <span class="cell-label">HCFCD Unit / Tributary ID</span>
                <span class="cell-value mono">${rawProps.hcfcd_unit}</span>
              </div>`
            : ""
        }
        ${
          overlayLayer === "historical_waterways" && rawProps.era_notes
            ? `<div class="inspector-cell full">
                <span class="cell-label">Historical Engineering &amp; Channelization Milestones</span>
                <span class="cell-value">${rawProps.era_notes}</span>
              </div>`
            : ""
        }
        ${
          overlayLayer === "historical_railroads" && rawProps.historic_company
            ? `<div class="inspector-cell full">
                <span class="cell-label">Pioneer Railroad / Streetcar Company</span>
                <span class="cell-value">${rawProps.historic_company}</span>
              </div>`
            : ""
        }
        ${
          overlayLayer === "historical_railroads" && rawProps.charter_year
            ? `<div class="inspector-cell">
                <span class="cell-label">Charter / Opening Era</span>
                <span class="cell-value mono">${rawProps.charter_year}</span>
              </div>`
            : ""
        }
        ${
          overlayLayer === "historical_railroads" && rawProps.modern_operator
            ? `<div class="inspector-cell">
                <span class="cell-label">Modern Operator / Legacy</span>
                <span class="cell-value">${rawProps.modern_operator}</span>
              </div>`
            : ""
        }
        ${
          overlayLayer === "historical_railroads" && rawProps.status
            ? `<div class="inspector-cell full">
                <span class="cell-label">Current Corridor Status</span>
                <span class="cell-value">${rawProps.status}</span>
              </div>`
            : ""
        }
        ${
          overlayLayer === "historical_railroads" && rawProps.route_corridor
            ? `<div class="inspector-cell full">
                <span class="cell-label">Historic Route Alignment</span>
                <span class="cell-value">${rawProps.route_corridor}</span>
              </div>`
            : ""
        }
        ${
          overlayLayer === "historical_railroads" && rawProps.address
            ? `<div class="inspector-cell full">
                <span class="cell-label">Historic Site Location</span>
                <span class="cell-value">${rawProps.address}</span>
              </div>`
            : ""
        }
        ${
          Number(rawProps.length_miles) > 0
            ? `<div class="inspector-cell">
                <span class="cell-label">Mapped Corridor Length</span>
                <span class="cell-value mono">${rawProps.length_miles} miles</span>
              </div>`
            : ""
        }
        ${
          fullName && overlayLayer === "platted_subdivisions"
            ? `<div class="inspector-cell full">
                <span class="cell-label">Full Recorded HCAD Plat Name</span>
                <span class="cell-value mono">${fullName}</span>
              </div>`
            : ""
        }
        ${
          rawProps.plat_citation && overlayLayer === "platted_subdivisions"
            ? `<div class="inspector-cell full">
                <span class="cell-label">Harris County Map Book / Plat Citation</span>
                <span class="cell-value mono">${rawProps.plat_citation}</span>
              </div>`
            : ""
        }
        ${
          volPage
            ? `<div class="inspector-cell">
                <span class="cell-label">HCAD Plat Map Book (Vol-Page)</span>
                <span class="cell-value mono">${volPage}</span>
              </div>`
            : ""
        }
        ${
          recNum
            ? `<div class="inspector-cell">
                <span class="cell-label">County Clerk Filing / Deed #</span>
                <span class="cell-value mono">${recNum}</span>
              </div>`
            : ""
        }
        ${
          overlayLayer === "land_use_protections" && rawProps.ordinance
            ? `<div class="inspector-cell">
                <span class="cell-label">City Council Ordinance #</span>
                <span class="cell-value mono">${rawProps.ordinance}</span>
              </div>`
            : ""
        }
        ${
          overlayLayer === "land_use_protections" && Number(rawProps.min_lot_sqft) > 0
            ? `<div class="inspector-cell">
                <span class="cell-label">Minimum Lot Size (Ch. 42)</span>
                <span class="cell-value mono">${Number(rawProps.min_lot_sqft).toLocaleString()} sq ft</span>
              </div>`
            : ""
        }
        ${
          overlayLayer === "land_use_protections" && Number(rawProps.min_bldg_line_ft) > 0
            ? `<div class="inspector-cell">
                <span class="cell-label">Minimum Front Setback (Ch. 42)</span>
                <span class="cell-value mono">${rawProps.min_bldg_line_ft} ft</span>
              </div>`
            : ""
        }
        ${
          overlayLayer === "land_use_protections" && (rawProps.effective_date || rawProps.expiration_date)
            ? `<div class="inspector-cell full">
                <span class="cell-label">Ordinance Effective / Expiration Window</span>
                <span class="cell-value mono">${rawProps.effective_date || "Recorded"} &#8594; ${rawProps.expiration_date || "Permanent"}</span>
              </div>`
            : ""
        }
        ${
          parentNbhd && overlayLayer === "platted_subdivisions"
            ? `<div class="inspector-cell full">
                <span class="cell-label">Containing Neighborhood / Subdivision</span>
                <span class="cell-value inspector-chip-group">
                  <button
                    type="button"
                    class="inspector-filter-chip"
                    data-filter-chip="${parentNbhd.replace(/"/g, "&quot;")}"
                    data-filter-label="Neighborhood: ${parentNbhd.replace(/"/g, "&quot;")}"
                  >${parentNbhd} &#128269;</button>
                  <button
                    type="button"
                    class="inspector-boundary-btn"
                    data-highlight-boundary="${parentNbhd.replace(/"/g, "&quot;")}"
                    data-boundary-layer="neighborhoods"
                    title="Highlight the ${parentNbhd.replace(/"/g, "&quot;")} neighborhood boundary on the map"
                  >Outline Neighborhood</button>
                </span>
              </div>`
            : ""
        }
        ${
          snName && overlayLayer !== "super_neighborhoods"
            ? `<div class="inspector-cell">
                <span class="cell-label">COH Super Neighborhood</span>
                <span class="cell-value">
                  <button
                    type="button"
                    class="inspector-filter-chip"
                    data-filter-chip="${snName.replace(/"/g, "&quot;")}"
                    data-filter-label="Super Neighborhood: ${snName.replace(/"/g, "&quot;")}"
                  >${snName} &#128269;</button>
                </span>
              </div>`
            : ""
        }
        ${
          wardName && overlayLayer !== "historic_wards"
            ? `<div class="inspector-cell">
                <span class="cell-label">Historic Ward (1839–1905)</span>
                <span class="cell-value">
                  <button
                    type="button"
                    class="inspector-filter-chip"
                    data-filter-chip="${wardName.replace(/"/g, "&quot;")}"
                    data-filter-label="Historic Ward: ${wardName.replace(/"/g, "&quot;")}"
                  >${wardName} &#128269;</button>
                </span>
              </div>`
            : ""
        }
        ${topSubsHtml}
        <div class="inspector-cell full">
          <span class="cell-label">Cartographic &amp; Archival Data Source</span>
          <span class="cell-value">${rawProps.source || "City of Houston &amp; HCAD GIS"}</span>
        </div>
      </div>

      <div class="inspector-actions">
        ${
          isLinearOrPointOverlay
            ? ""
            : `<button
                type="button"
                class="inspector-btn primary"
                id="btn-explore-boundary-buildings"
                data-filter-chip="${name.replace(/"/g, "&quot;")}"
                data-filter-label="${badgeLabel}: ${name.replace(/"/g, "&quot;")}"
              >
                &#128269; Explore Structures in ${name}
              </button>`
        }
        <button
          type="button"
          class="inspector-btn secondary"
          id="btn-clear-boundary-outline"
        >
          Clear Highlight on Map
        </button>
      </div>
    `;

    drawer.classList.remove("hidden");

    this._bindInspectorOverlapStackEvents(content);
    this._bindDeedRestrictionsCardEvents(content, rawProps);

    const boundarySpec = {
      layerKey: overlayLayer || "neighborhoods",
      id: rawProps.id || name,
      name,
    };

    content.querySelectorAll("[data-inspector-iso-mode]").forEach((modeBtn) => {
      modeBtn.addEventListener("click", () => {
        const mode = modeBtn.getAttribute("data-inspector-iso-mode") || "contents";
        if (this.mapController) {
          this.mapController.setIsolatedBoundary(
            { ...boundarySpec, mode },
            { fitBounds: !isCurrentlyIsolated, openInspector: false }
          );
          this._renderBoundaryInspectorDrawer(rawProps, drawer, content);
        }
      });
    });

    const btnToggleIsolate = document.getElementById("btn-inspector-toggle-isolate");
    if (btnToggleIsolate) {
      btnToggleIsolate.addEventListener("click", () => {
        if (!this.mapController) return;
        if (isCurrentlyIsolated) {
          this.mapController.clearIsolatedBoundary();
        } else {
          this.mapController.setIsolatedBoundary(
            { ...boundarySpec, mode: "contents" },
            { fitBounds: true, openInspector: false }
          );
        }
        this._renderBoundaryInspectorDrawer(rawProps, drawer, content);
      });
    }

    const btnOpenExport = document.getElementById("btn-inspector-open-export-studio");
    if (btnOpenExport) {
      btnOpenExport.addEventListener("click", () => {
        this.openExportStudioModal({
          layerKey: boundarySpec.layerKey,
          id: boundarySpec.id,
          name: boundarySpec.name,
        });
      });
    }

    content.querySelectorAll("[data-filter-chip]").forEach((chipBtn) => {
      chipBtn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const chipQuery = chipBtn.getAttribute("data-filter-chip") || "";
        const chipLabel = chipBtn.getAttribute("data-filter-label") || "";
        if (chipQuery) {
          this.triggerMetadataFilterSearch(chipQuery, chipLabel);
        }
      });
    });

    content.querySelectorAll("[data-highlight-boundary]").forEach((bBtn) => {
      bBtn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const bName = bBtn.getAttribute("data-highlight-boundary") || "";
        const bLayer = bBtn.getAttribute("data-boundary-layer") || "";
        if (bName && this.mapController) {
          this.mapController.highlightBoundaryByIdOrName(bName, {
            layerKey: bLayer,
            fitBounds: true,
            openInspector: true,
          });
        }
      });
    });

    const btnClearOutline = document.getElementById("btn-clear-boundary-outline");
    if (btnClearOutline) {
      btnClearOutline.addEventListener("click", () => {
        if (this.mapController) {
          this.mapController.clearHighlightedBoundary();
        }
      });
    }
  }

  renderInspectorDrawer(rawProps) {
    const drawer = document.getElementById("inspector-drawer");
    const content = document.getElementById("inspector-body");
    if (!drawer || !content || !rawProps) return;

    if (typeof window !== "undefined" && window.innerWidth <= 900) {
      this._setSidebarCollapsed(true);
    }

    if (
      rawProps.is_boundary_feature ||
      [
        "platted_subdivisions",
        "land_use_protections",
        "neighborhoods",
        "super_neighborhoods",
        "historic_wards",
        "historic_districts",
        "heritage_districts",
        "nrhp_districts",
        "annexations",
        "historical_waterways",
        "historical_railroads",
      ].includes(String(rawProps.overlay_layer || ""))
    ) {
      this._renderBoundaryInspectorDrawer(rawProps, drawer, content);
      return;
    }

    const props = applyOverrideToProperties(
      rawProps,
      this.mapController ? this.mapController.curatedOverrides : {}
    );

    // Parse good_brick_awards if serialized as a JSON string by MapLibre GL queryRenderedFeatures
    let goodBrickAwards = [];
    if (Array.isArray(props.good_brick_awards)) {
      goodBrickAwards = props.good_brick_awards;
    } else if (typeof props.good_brick_awards === "string" && props.good_brick_awards.trim().startsWith("[")) {
      try {
        goodBrickAwards = JSON.parse(props.good_brick_awards);
      } catch (_e) {
        goodBrickAwards = [];
      }
    }

    const yr = Number(props.year_built) || 0;
    const currentYear = 2026;
    const isCircaEst =
      props.year_source === "subdivision_median" || props.year_source === "blockface_median";
    const ageText =
      yr >= 1836
        ? isCircaEst
          ? `~${currentYear - yr} yrs old (Est.)`
          : `${currentYear - yr} yrs old`
        : "Date unrecorded in HCAD";
    const yearDisplay =
      yr >= 1836 ? (isCircaEst ? `Circa ${yr}` : `Built ${yr}`) : "Undated / Vacant";
    const yearColor = getYearColorHex(yr, this.filterStore.getState().paletteStyle);

    const YEAR_SOURCE_LABELS = {
      curated_landmark: "Preservation Houston Curated Landmark & Campus Record",
      hcad_extra_feature: "HCAD Extra Features / Improvement Table (act_yr / eff_yr)",
      hcad_tieback: "HCAD Multi-Parcel Tieback (parcel_tieback.txt)",
      adjacent_owner: "Spatial Same-Owner / Same-Address Contiguous Parcel",
      campus_contiguity: "Institutional Campus / THC Historical Marker Record",
      hcad_permit: "HCAD Structural Building Permit Record (permits.txt)",
      hcad_deed: "HCAD Historical Deed Record (deeds.txt)",
      subdivision_median: "Circa Estimate — HCAD Subdivision / Street Median",
      blockface_median: "Circa Estimate — Nearest Block-Face Structure Median",
    };
    const yearProvenanceLabel = props.is_curated_override
      ? `Verified by ${props.verified_by || "Preservation Houston"} (${props.source_type || "Archival Record"})`
      : YEAR_SOURCE_LABELS[props.year_source] || "HCAD Real Property Building Record (date_erected / yr_impr)";

    const nameInfo = this._resolveBuildingNameAndAliases(props);
    const title = nameInfo.primaryName || "Houston Historic Structure";
    const allDrawerAddrs = [
      props.address || "",
      ...(Array.isArray(props.alt_addresses) ? props.alt_addresses : []),
    ]
      .map((a) => String(a || "").trim())
      .filter(Boolean);
    const combinedDrawerAddr = allDrawerAddrs.slice(0, 2).join(" / ");
    const subtitle =
      combinedDrawerAddr && combinedDrawerAddr.toLowerCase() !== title.toLowerCase()
        ? combinedDrawerAddr
        : props.historic_district || "Harris County, Texas";

    const akaHeroHtml =
      nameInfo.altNames.length > 0
        ? `<div class="inspector-aka-bar" id="inspector-aka-bar">
            <span class="inspector-aka-label">Also Known As / Historical Names:</span>
            <div class="inspector-aka-chips">
              ${nameInfo.altNames
                .map(
                  (alias) => `<button
                    type="button"
                    class="inspector-alias-chip inspector-filter-chip"
                    data-filter-chip="${alias.replace(/"/g, "&quot;")}"
                    data-filter-label="Name / Alias: ${alias.replace(/"/g, "&quot;")}"
                    title="Click to search '${alias.replace(/"/g, "&quot;")}' across the Atlas"
                  >${alias}</button>`
                )
                .join("")}
            </div>
          </div>`
        : "";

    const statusBadge =
      props.good_brick_summary && !props.landmark_type
        ? `★ ${props.good_brick_summary}`
        : props.landmark_type ||
          props.designation ||
          (props.contributing && props.contributing !== "Outside Historic District"
            ? `${props.contributing} Structure`
            : "Standard Tax Parcel");

    const bldSqft =
      Number(props.bld_area) > 0
        ? `${Number(props.bld_area).toLocaleString()} sq ft`
        : "Not reported";
    const landSqft =
      Number(props.land_area) > 0
        ? `${Number(props.land_area).toLocaleString()} sq ft`
        : "Not reported";
    const scaleText =
      Number(props.stories) > 0
        ? `${props.stories} stories (~${props.height_m}m)`
        : "1 story";

    const fpSourceLabel =
      props.footprint_source === "observed"
        ? "Observed Building Footprint (OSM/Planimetric)"
        : "Architectural Footprint (Derived from Parcel + CAMA Area)";

    const hcadNum = String(props.hcad_num || "").trim();

    const goodBrickHtml =
      goodBrickAwards.length > 0
        ? `<div class="ph-good-brick-card">
            <div class="ph-good-brick-header">
              <span class="ph-good-brick-kicker">&#9733; Preservation Houston Good Brick Award${goodBrickAwards.length > 1 ? `s (${goodBrickAwards.length})` : ""}</span>
              <span class="ph-good-brick-years mono">${goodBrickAwards.map((a) => a.award_year).join(", ")}</span>
            </div>
            <div class="ph-good-brick-list">
              ${goodBrickAwards
                .map(
                  (a) => `
                  <div class="ph-good-brick-item">
                    <div class="ph-good-brick-item-top">
                      <button
                        type="button"
                        class="ph-good-brick-year-pill mono inspector-filter-chip"
                        data-filter-chip="${a.award_year}"
                        data-filter-label="Good Brick Award Winners (${a.award_year})"
                        title="Click to find all ${a.award_year} Good Brick Award winners"
                      >${a.award_year} &#128269;</button>
                      <span class="ph-good-brick-type-badge">${a.award_type || "Good Brick Award"}</span>
                    </div>
                    <div class="ph-good-brick-recipient">
                      <strong>Recipient:</strong> ${a.recipient || "Property Owner"}
                    </div>
                    <div class="ph-good-brick-reason">
                      <strong>Citation:</strong> ${a.reason || a.raw_description || "Recognized for excellence in historic preservation."}
                    </div>
                  </div>`
                )
                .join("")}
            </div>
            <a href="https://www.preservationhouston.org/awards/past" target="_blank" rel="noopener noreferrer" class="ph-good-brick-link">
              View Preservation Houston Good Brick Awards Archive &#8599;
            </a>
          </div>`
        : "";

    // Resolve City of Houston Landmark Designation Report metadata (including runtime fallback from overlaysData.landmarks)
    let reportPdfUrl = props.landmark_report_url || props.report_pdf_url || "";
    let secondaryPdfUrl = props.secondary_pdf_url || "";
    let landmarkCode = props.landmark_code || props.plm_num || props.lm_num || "";
    let landmarkSummary = props.landmark_summary || props.pdf_summary || "";
    if (!reportPdfUrl && this.mapController?.overlaysData?.landmarks?.features) {
      const lmFeatures = this.mapController.overlaysData.landmarks.features;
      const normTitle = String(title || "").trim().toLowerCase();
      const normAddr = String(props.address || "").trim().toLowerCase();
      for (const f of lmFeatures) {
        const lp = f.properties || {};
        const lHcad = String(lp.hcad_num || "").trim();
        const lName = String(lp.name || "").trim().toLowerCase();
        const lAddr = String(lp.address || "").trim().toLowerCase();
        if (
          (hcadNum && lHcad === hcadNum) ||
          (normTitle && lName && normTitle === lName) ||
          (normAddr && lAddr && normAddr === lAddr)
        ) {
          reportPdfUrl = lp.report_pdf_url || "";
          secondaryPdfUrl = lp.secondary_pdf_url || "";
          if (!landmarkCode) landmarkCode = lp.plm_num || lp.lm_num || "";
          if (!landmarkSummary) landmarkSummary = lp.pdf_summary || "";
          break;
        }
      }
    }

    const landmarkReportHtml =
      reportPdfUrl || landmarkCode
        ? `<div class="coh-landmark-report-card">
            <div class="coh-landmark-report-header">
              <span class="coh-landmark-report-kicker">&#127963; COH Landmark Designation Dossier</span>
              ${
                landmarkCode
                  ? `<span class="coh-landmark-code-badge" title="City of Houston Historic Preservation Office File #">HPO #${landmarkCode}</span>`
                  : ""
              }
            </div>
            ${
              landmarkSummary &&
              (!props.is_curated_override || !String(props.source_citation || "").includes(landmarkSummary.slice(0, 40)))
                ? `<div class="coh-landmark-report-summary">${landmarkSummary}</div>`
                : ""
            }
            ${
              reportPdfUrl
                ? `<div class="coh-landmark-report-actions">
                    <a
                      href="${reportPdfUrl}"
                      target="_blank"
                      rel="noopener noreferrer"
                      class="coh-landmark-pdf-btn"
                      title="Open the official City of Houston Archaeological & Historical Commission (HAHC) Landmark Designation Report PDF"
                    >
                      &#128196; Open Official Landmark Report (PDF) &#8599;
                    </a>
                    ${
                      secondaryPdfUrl && secondaryPdfUrl !== reportPdfUrl
                        ? `<a
                            href="${secondaryPdfUrl}"
                            target="_blank"
                            rel="noopener noreferrer"
                            class="coh-landmark-pdf-btn secondary"
                          >
                            Supplemental HAHC Filing / Action Report (PDF) &#8599;
                          </a>`
                        : ""
                    }
                  </div>`
                : ""
            }
          </div>`
        : "";

    const verifiedBannerHtml = props.is_curated_override
      ? `<div class="ph-verified-override-card">
          <div class="ph-verified-header">
            <span>&#10003; Verified by ${props.verified_by || "Preservation Houston"}</span>
            ${
              props.original_hcad_year && props.original_hcad_year !== yr
                ? `<span class="ph-verified-orig-year">HCAD lists ${props.original_hcad_year}</span>`
                : ""
            }
          </div>
          <div class="ph-verified-citation">
            <strong>${props.source_type || "Archival Source"}:</strong>
            ${props.source_citation || "Verified historical completion date overrides HCAD appraisal estimate."}
          </div>
          ${
            props.source_url && props.source_url !== reportPdfUrl
              ? `<a href="${props.source_url}" target="_blank" rel="noopener noreferrer" class="ph-verified-link">
                  View Historical Directory / Source Archive &#8599;
                </a>`
              : ""
          }
        </div>`
      : "";

    const haifThreads = this._resolveHaifThreads(props);
    const haifSearchTerm =
      (props.address && String(props.address).trim()) ||
      (title && title !== "Houston Structure" ? title : "");
    const haifGoogleSearchUrl = haifSearchTerm
      ? `https://www.google.com/search?q=${encodeURIComponent(
          `site:houstonarchitecture.com "${haifSearchTerm}"`
        )}`
      : "https://www.houstonarchitecture.com/";

    const haifForumHtml =
      haifThreads.length > 0
        ? `<div class="haif-forum-card">
            <div class="haif-forum-header">
              <span class="haif-forum-kicker">&#128172; Houston Architecture Forum (HAIF)</span>
              <span class="haif-forum-count-badge">${haifThreads.length} ${
                haifThreads.length === 1 ? "Thread" : "Threads"
              }</span>
            </div>
            <div class="haif-thread-list">
              ${haifThreads
                .map(
                  (th) => `<div class="haif-thread-item">
                    <div class="haif-thread-top">
                      <span class="haif-subforum-pill">${th.subforum || "Architecture"}</span>
                      ${
                        th.is_demo
                          ? `<span class="haif-demo-pill" title="HAIF thread discusses demolition or redevelopment at this site">&#9888; Demo / Redev</span>`
                          : ""
                      }
                      ${th.date ? `<span class="haif-thread-date">${th.date}</span>` : ""}
                    </div>
                    <div class="haif-thread-title">${th.title}</div>
                    ${
                      th.excerpt
                        ? `<div class="haif-thread-excerpt">${th.excerpt}</div>`
                        : ""
                    }
                    <div class="haif-thread-actions">
                      <a
                        href="${th.url}"
                        target="_blank"
                        rel="noopener noreferrer"
                        class="haif-thread-btn primary"
                        title="Open discussion thread on Houston Architecture Info Forum (houstonarchitecture.com)"
                      >
                        Open HAIF Thread &#8599;
                      </a>
                      ${
                        th.wb_url
                          ? `<a
                              href="${th.wb_url}"
                              target="_blank"
                              rel="noopener noreferrer"
                              class="haif-thread-btn archive"
                              title="Open archived snapshot on Internet Archive Wayback Machine"
                            >
                              Archive Snapshot &#8599;
                            </a>`
                          : ""
                      }
                    </div>
                  </div>`
                )
                .join("")}
            </div>
          </div>`
        : haifSearchTerm
        ? `<div class="haif-forum-compact-bar">
            <a
              href="${haifGoogleSearchUrl}"
              target="_blank"
              rel="noopener noreferrer"
              class="haif-compact-search-link"
              title="Search Houston Architecture Info Forum (houstonarchitecture.com) for discussions & photos of ${haifSearchTerm}"
            >
              &#128172; Search &ldquo;${haifSearchTerm}&rdquo; on Houston Architecture Forum (HAIF) &#8599;
            </a>
          </div>`
        : "";

    const selGeom = this.mapController ? this.mapController.selectedFeatureGeometry : null;
    let geomLng = null;
    let geomLat = null;
    if (selGeom && selGeom.coordinates) {
      if (selGeom.type === "Point" && Array.isArray(selGeom.coordinates)) {
        [geomLng, geomLat] = selGeom.coordinates;
      } else {
        const ring =
          selGeom.type === "Polygon"
            ? selGeom.coordinates[0]
            : selGeom.type === "MultiPolygon" && selGeom.coordinates[0]
            ? selGeom.coordinates[0][0]
            : null;
        if (Array.isArray(ring) && ring.length > 0) {
          let sumLng = 0;
          let sumLat = 0;
          let count = 0;
          for (const pt of ring) {
            if (Array.isArray(pt) && Number.isFinite(pt[0]) && Number.isFinite(pt[1])) {
              sumLng += pt[0];
              sumLat += pt[1];
              count += 1;
            }
          }
          if (count > 0) {
            geomLng = sumLng / count;
            geomLat = sumLat / count;
          }
        }
      }
    }
    const vpFallback = this.mapController ? this.mapController.getCurrentViewport() : null;
    const effLat = Number.isFinite(geomLat) ? geomLat : vpFallback?.lat ?? 29.7604;
    const effLng = Number.isFinite(geomLng) ? geomLng : vpFallback?.lng ?? -95.3698;
    const isCampusSubBuilding = String(props.id || "").includes("#");
    const hasStreetNum = /^\d+\s+[A-Za-z0-9]/.test(String(props.address || "").trim());
    const initialMapsQuery =
      Number.isFinite(geomLat) && Number.isFinite(geomLng)
        ? `${geomLat.toFixed(6)},${geomLng.toFixed(6)}`
        : hasStreetNum && !isCampusSubBuilding
        ? `${String(props.address).trim()}, Houston, TX`
        : `${effLat.toFixed(6)},${effLng.toFixed(6)}`;
    const googleMapsUrl = `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(
      initialMapsQuery
    )}`;
    const streetViewUrl = buildStreetViewUrl(effLat, effLng);

    // Runtime fallback enrichment from spatial index for countywide shard buildings & land-use context
    if (
      (!props.neighborhood ||
        !props._platted_subdivision_props ||
        !Array.isArray(props._land_use_protections)) &&
      Number.isFinite(effLng) &&
      Number.isFinite(effLat) &&
      this.mapController &&
      typeof this.mapController.resolveGeographicContextAtPoint === "function"
    ) {
      const geoCtx = this.mapController.resolveGeographicContextAtPoint(effLng, effLat);
      if (geoCtx.neighborhood && !props.neighborhood) props.neighborhood = geoCtx.neighborhood;
      if (
        Array.isArray(geoCtx.neighborhood_aliases) &&
        geoCtx.neighborhood_aliases.length > 0 &&
        !props.neighborhood_aliases
      ) {
        props.neighborhood_aliases = geoCtx.neighborhood_aliases;
      }
      if (geoCtx.super_neighborhood && !props.super_neighborhood) {
        props.super_neighborhood = geoCtx.super_neighborhood;
      }
      if (geoCtx.historic_ward && !props.historic_ward) props.historic_ward = geoCtx.historic_ward;
      if (geoCtx.platted_subdivision && !props.subdivision) {
        props.subdivision = geoCtx.platted_subdivision;
      }
      if (geoCtx.platted_subdivision_props && !props._platted_subdivision_props) {
        props._platted_subdivision_props = geoCtx.platted_subdivision_props;
      }
      if (Array.isArray(geoCtx.land_use_protections) && !props._land_use_protections) {
        props._land_use_protections = geoCtx.land_use_protections;
      }
    }

    let neighborhoodAliases = [];
    if (Array.isArray(props.neighborhood_aliases)) {
      neighborhoodAliases = props.neighborhood_aliases
        .map((a) => String(a || "").trim())
        .filter(Boolean);
    } else if (
      typeof props.neighborhood_aliases === "string" &&
      props.neighborhood_aliases.trim().startsWith("[")
    ) {
      try {
        const parsed = JSON.parse(props.neighborhood_aliases);
        if (Array.isArray(parsed)) {
          neighborhoodAliases = parsed.map((a) => String(a || "").trim()).filter(Boolean);
        }
      } catch (_e) {
        neighborhoodAliases = [];
      }
    }

    const nbhdVal = String(props.neighborhood || "").trim();
    const superNbhdVal = String(props.super_neighborhood || "").trim();
    const wardVal = String(props.historic_ward || "").trim();
    const subVal = String(props.subdivision || "").trim();

    const subPropsForDeed =
      props._platted_subdivision_props || this._resolvePlattedSubdivisionProps(subVal);
    const catalogEntryForDeed = this._resolveDeedCatalogEntry(
      subPropsForDeed || {
        name: subVal,
        raw_name: subVal,
        neighborhood: nbhdVal,
      }
    );
    const buildingDeedCardHtml = this._buildDeedRestrictionsCardHtml({
      subProps: subPropsForDeed,
      catalogEntry: catalogEntryForDeed,
      landUseProtections: Array.isArray(props._land_use_protections)
        ? props._land_use_protections
        : [],
      rawSubdivisionName: subVal,
      neighborhoodName: nbhdVal,
      isBoundary: false,
    });

    const distVal = String(props.historic_district || "").trim();
    const isRealDistrict =
      distVal &&
      distVal !== "Outside City District" &&
      distVal !== "Outside Historic District";
    const styleInfo = this._resolveStyleAndClassInfo(props);
    const styleChipsHtml = (styleInfo.chips || [])
      .map(
        (c) =>
          `<button
            type="button"
            class="inspector-filter-chip ${c.isSecondary ? "category-chip" : ""}"
            data-filter-chip="${c.query.replace(/"/g, "&quot;")}"
            data-filter-label="${c.headerLabel.replace(/"/g, "&quot;")}"
            title="Click to find '${c.label.replace(/"/g, "&quot;")}' buildings in current view & across Houston"
          >${c.label} &#128269;</button>${
            c.note ? `<span class="inspector-chip-note">${c.note}</span>` : ""
          }`
      )
      .join('<span class="inspector-chip-sep" aria-hidden="true">·</span>');

    const landUseChips = this._resolveLandUseChips(props);
    const landUseChipsHtml = landUseChips
      .map(
        (c) =>
          `<button
            type="button"
            class="inspector-filter-chip ${c.isCategory ? "category-chip" : ""}"
            data-filter-chip="${c.query.replace(/"/g, "&quot;")}"
            data-filter-label="${c.headerLabel.replace(/"/g, "&quot;")}"
            title="${c.title.replace(/"/g, "&quot;")}"
          >${c.label} &#128269;</button>`
      )
      .join('<span class="inspector-chip-sep" title="Specific Use › Broad Category" aria-hidden="true">&#8250;</span>');

    const archChips = this._resolveArchitectChips(props);
    const archChipsHtml = archChips
      .map(
        (c) =>
          `<button
            type="button"
            class="inspector-filter-chip"
            data-filter-chip="${c.query.replace(/"/g, "&quot;")}"
            data-filter-label="${c.headerLabel.replace(/"/g, "&quot;")}"
            title="Click to find all Houston buildings by '${c.label.replace(/"/g, "&quot;")}'"
          >${c.label} &#128269;</button>${
            c.note ? `<span class="inspector-chip-note">${c.note}</span>` : ""
          }`
      )
      .join('<span class="inspector-chip-sep" aria-hidden="true">·</span>');

    const buildingNamesGridRowHtml =
      nameInfo.hasCustomBuildingName || nameInfo.altNames.length > 0
        ? `<div class="inspector-cell full">
            <span class="cell-label">Building Name &amp; Historical Aliases</span>
            <span class="cell-value">
              <div class="inspector-name-dossier">
                <div class="inspector-primary-name-line">
                  <strong>${title}</strong>
                  ${
                    nameInfo.nameSource
                      ? `<span class="inspector-name-source-pill">${nameInfo.nameSource}</span>`
                      : ""
                  }
                </div>
                ${
                  nameInfo.altNames.length > 0
                    ? `<div class="inspector-alt-names-line">
                        <span class="inspector-alt-prefix">Also known as:</span>
                        ${nameInfo.altNames
                          .map(
                            (alias) => `<button
                              type="button"
                              class="inspector-alias-chip small inspector-filter-chip"
                              data-filter-chip="${alias.replace(/"/g, "&quot;")}"
                              data-filter-label="Name / Alias: ${alias.replace(/"/g, "&quot;")}"
                              title="Click to search '${alias.replace(/"/g, "&quot;")}'"
                            >${alias} &#128269;</button>`
                          )
                          .join(" ")}
                      </div>`
                    : ""
                }
              </div>
            </span>
          </div>`
        : "";

    const overlapStackHtml = this._buildInspectorOverlapStackHtml();

    content.innerHTML = `
      ${overlapStackHtml}
      <div class="inspector-hero">
        <div class="inspector-badges">
          <span class="inspector-year-pill" style="background:${yearColor};">${yearDisplay}${props.is_curated_override ? " &#10003;" : ""}</span>
          <span class="inspector-age-pill">${ageText}</span>
        </div>
        <h2 class="inspector-title" id="inspector-property-title">${title}</h2>
        ${akaHeroHtml}
        <p class="inspector-subtitle">${subtitle}</p>
        <div class="inspector-status-banner">
          <span class="status-dot"></span>
          <span>${statusBadge}</span>
        </div>
      </div>

      <div class="inspector-photo-card" id="inspector-photo-card" data-photo-hcad="${hcadNum}">
        <div class="inspector-photo-header">
          <div class="inspector-photo-title-group">
            <span class="inspector-photo-kicker">&#128247; Archival Photographs</span>
            <span class="inspector-photo-count-badge" id="inspector-photo-badge">Searching Archives…</span>
          </div>
          <div class="inspector-photo-header-btns" id="inspector-photo-header-btns"></div>
        </div>
        <div id="inspector-photo-stage"></div>
        <div class="photo-footer-actions">
          <a
            href="${streetViewUrl}"
            target="_blank"
            rel="noopener noreferrer"
            class="photo-streetview-btn"
            id="btn-inspector-streetview"
            title="Open Google Maps Street View Time Machine (2007–Present) for this building"
          >
            &#128065; Street View Time Machine (2007–Present) &#8599;
          </a>
        </div>
      </div>

      ${goodBrickHtml}
      ${landmarkReportHtml}
      ${verifiedBannerHtml}
      ${haifForumHtml}

      <div class="inspector-grid">
        ${buildingNamesGridRowHtml}
        <div class="inspector-cell">
          <span class="cell-label">HCAD Account #</span>
          <span class="cell-value mono">
            ${hcadNum || "Exempt / Unlisted"}
            ${
              hcadNum
                ? `<button type="button" id="btn-copy-hcad-acct" style="margin-left:6px;padding:1px 6px;font-size:10px;border-radius:4px;border:1px solid rgba(255,255,255,0.2);background:rgba(255,255,255,0.06);color:inherit;cursor:pointer;" title="Copy 13-digit HCAD Account Number">Copy</button>`
                : ""
            }
          </span>
        </div>
        <div class="inspector-cell">
          <span class="cell-label">Historic District</span>
          <span class="cell-value inspector-chip-group">
            ${
              isRealDistrict
                ? `<button type="button" class="inspector-filter-chip" data-filter-chip="${distVal.replace(/"/g, "&quot;")}" data-filter-label="District: ${distVal.replace(/"/g, "&quot;")}" title="Click to search all structures in ${distVal.replace(/"/g, "&quot;")}">${distVal} &#128269;</button>
                   <button type="button" class="inline-isolate-btn" data-isolate-boundary="${distVal.replace(/"/g, "&quot;")}" data-boundary-layer="historic_districts" title="Isolate ${distVal.replace(/"/g, "&quot;")} and its buildings on the map">&#127919; Isolate</button>`
                : distVal || "Outside City District"
            }
          </span>
        </div>
        <div class="inspector-cell">
          <span class="cell-label">Neighborhood / Area</span>
          <span class="cell-value inspector-chip-group">
            ${
              nbhdVal
                ? `<button type="button" class="inspector-filter-chip" data-filter-chip="${nbhdVal.replace(/"/g, "&quot;")}" data-filter-label="Neighborhood: ${nbhdVal.replace(/"/g, "&quot;")}" title="Click to find all structures in ${nbhdVal.replace(/"/g, "&quot;")}">${nbhdVal} &#128269;</button>
                   <button type="button" class="inspector-boundary-btn" data-highlight-boundary="${nbhdVal.replace(/"/g, "&quot;")}" data-boundary-layer="neighborhoods" title="Outline ${nbhdVal.replace(/"/g, "&quot;")} boundary on the map">Outline</button>
                   <button type="button" class="inline-isolate-btn" data-isolate-boundary="${nbhdVal.replace(/"/g, "&quot;")}" data-boundary-layer="neighborhoods" title="Isolate ${nbhdVal.replace(/"/g, "&quot;")} and its buildings on the map">&#127919; Isolate</button>`
                : "Unincorporated / Outside Boundary"
            }
          </span>
        </div>
        <div class="inspector-cell">
          <span class="cell-label">COH Super Neighborhood</span>
          <span class="cell-value inspector-chip-group">
            ${
              superNbhdVal
                ? `<button type="button" class="inspector-filter-chip category-chip" data-filter-chip="${superNbhdVal.replace(/"/g, "&quot;")}" data-filter-label="Super Neighborhood: ${superNbhdVal.replace(/"/g, "&quot;")}" title="Click to find structures in Super Neighborhood: ${superNbhdVal.replace(/"/g, "&quot;")}">${superNbhdVal} &#128269;</button>
                   <button type="button" class="inspector-boundary-btn" data-highlight-boundary="${superNbhdVal.replace(/"/g, "&quot;")}" data-boundary-layer="super_neighborhoods" title="Outline ${superNbhdVal.replace(/"/g, "&quot;")} Super Neighborhood boundary on the map">Outline</button>
                   <button type="button" class="inline-isolate-btn" data-isolate-boundary="${superNbhdVal.replace(/"/g, "&quot;")}" data-boundary-layer="super_neighborhoods" title="Isolate ${superNbhdVal.replace(/"/g, "&quot;")} Super Neighborhood on the map">&#127919; Isolate</button>`
                : "Outside COH Super Neighborhood"
            }
          </span>
        </div>
        ${
          neighborhoodAliases.length > 0
            ? `<div class="inspector-cell full">
                <span class="cell-label">Historical &amp; Colloquial Area Names</span>
                <span class="cell-value inspector-chip-group">
                  ${neighborhoodAliases
                    .map(
                      (alias) =>
                        `<button type="button" class="inspector-alias-chip small inspector-filter-chip" data-filter-chip="${alias.replace(/"/g, "&quot;")}" data-filter-label="Area / Alias: ${alias.replace(/"/g, "&quot;")}" title="Click to search structures in '${alias.replace(/"/g, "&quot;")}'">${alias} &#128269;</button>`
                    )
                    .join(" ")}
                </span>
              </div>`
            : ""
        }
        ${
          wardVal
            ? `<div class="inspector-cell full">
                <span class="cell-label">Historic Ward (1839–1905 Aldermanic Charter)</span>
                <span class="cell-value inspector-chip-group">
                  <button type="button" class="inspector-filter-chip" data-filter-chip="${wardVal.replace(/"/g, "&quot;")}" data-filter-label="Historic Ward: ${wardVal.replace(/"/g, "&quot;")}" title="Click to search structures in ${wardVal.replace(/"/g, "&quot;")}">1903–05 ${wardVal} &#128269;</button>
                  <button type="button" class="inspector-boundary-btn" data-highlight-boundary="${wardVal.replace(/"/g, "&quot;")}" data-boundary-layer="historic_wards" title="Outline 1903–1905 ${wardVal.replace(/"/g, "&quot;")} boundary on the map">Outline Ward</button>
                  <button type="button" class="inline-isolate-btn" data-isolate-boundary="${wardVal.replace(/"/g, "&quot;")}" data-boundary-layer="historic_wards" title="Isolate 1903–1905 ${wardVal.replace(/"/g, "&quot;")} on the map">&#127919; Isolate</button>
                </span>
              </div>`
            : ""
        }
        <div class="inspector-cell">
          <span class="cell-label">Building Floor Area</span>
          <span class="cell-value mono" id="inspector-cell-bld-sqft">${bldSqft}</span>
        </div>
        <div class="inspector-cell">
          <span class="cell-label">Parcel Lot Size</span>
          <span class="cell-value mono" id="inspector-cell-lot-sqft">${landSqft}</span>
        </div>
        <div class="inspector-cell">
          <span class="cell-label">Estimated Scale</span>
          <span class="cell-value mono">${scaleText}</span>
        </div>
        <div class="inspector-cell">
          <span class="cell-label">Remodel Year</span>
          <span class="cell-value mono">${Number(props.remodel_year) > 1836 ? props.remodel_year : "None recorded"}</span>
        </div>
        <div class="inspector-cell full">
          <span class="cell-label">Construction Date Provenance</span>
          <span class="cell-value">${yearProvenanceLabel}</span>
        </div>
        <div class="inspector-cell full">
          <span class="cell-label">Architectural Style / Building Class</span>
          <span class="cell-value inspector-chip-group">
            ${styleChipsHtml}
          </span>
        </div>
        ${
          archChips.length > 0
            ? `<div class="inspector-cell full">
                <span class="cell-label">Architect / Builder (COH Landmark Record)</span>
                <span class="cell-value inspector-chip-group">
                  ${archChipsHtml}
                </span>
              </div>`
            : ""
        }
        <div class="inspector-cell full">
          <span class="cell-label">Land Use Classification</span>
          <span class="cell-value inspector-chip-group">
            ${landUseChipsHtml}
          </span>
        </div>
        <div class="inspector-cell full">
          <span class="cell-label">Platted Subdivision / Legal Description (HCAD)</span>
          <span class="cell-value inspector-chip-group" id="inspector-cell-subdivision">
            ${
              subVal
                ? `<button type="button" class="inspector-filter-chip" data-filter-chip="${subVal.replace(/"/g, "&quot;")}" data-filter-label="Subdivision: ${subVal.replace(/"/g, "&quot;")}" title="Click to search structures in platted subdivision '${subVal.replace(/"/g, "&quot;")}'">${subVal} &#128269;</button>
                   <button type="button" class="inspector-boundary-btn" data-highlight-boundary="${subVal.replace(/"/g, "&quot;")}" data-boundary-layer="platted_subdivisions" title="Outline HCAD platted subdivision boundary for '${subVal.replace(/"/g, "&quot;")}' on the map">Outline Plat</button>
                   <button type="button" class="inline-isolate-btn" data-isolate-boundary="${subVal.replace(/"/g, "&quot;")}" data-boundary-layer="platted_subdivisions" title="Isolate HCAD platted subdivision '${subVal.replace(/"/g, "&quot;")}' and its buildings on the map">&#127919; Isolate Plat</button>`
                : "Not listed"
            }
          </span>
        </div>
        <div class="inspector-cell full">
          <span class="cell-label">Recorded Property Owner (HCAD)</span>
          <span class="cell-value" id="inspector-cell-owner">${props.owner || "Public / Unlisted"}</span>
        </div>
        <div class="inspector-cell full">
          <span class="cell-label">Geometry Provenance</span>
          <span class="cell-value">${fpSourceLabel}</span>
        </div>
      </div>

      ${buildingDeedCardHtml}

      ${
        hcadNum
          ? `<div class="hcad-live-record-card" id="inspector-hcad-live-card" data-hcad-num="${hcadNum}">
              <div class="hcad-live-header">
                <span class="hcad-live-title">Live HCAD Appraisal Record</span>
                <span class="hcad-live-badge" id="hcad-live-status-badge">Loading HCAD GIS…</span>
              </div>
              <div class="hcad-live-grid" id="hcad-live-grid">
                <div class="hcad-live-cell">
                  <span class="hcad-live-label">Appraised Value</span>
                  <span class="hcad-live-val mono" id="hcad-live-appr">…</span>
                </div>
                <div class="hcad-live-cell">
                  <span class="hcad-live-label">Market Value</span>
                  <span class="hcad-live-val mono" id="hcad-live-mkt">…</span>
                </div>
                <div class="hcad-live-cell">
                  <span class="hcad-live-label">Improvement Value</span>
                  <span class="hcad-live-val mono" id="hcad-live-impr">…</span>
                </div>
                <div class="hcad-live-cell">
                  <span class="hcad-live-label">Land Value</span>
                  <span class="hcad-live-val mono" id="hcad-live-land">…</span>
                </div>
                <div class="hcad-live-cell full" id="hcad-live-meta-row" style="display:none;">
                  <span class="hcad-live-label">State Class &amp; Legal Lines</span>
                  <span class="hcad-live-val" id="hcad-live-legal"></span>
                </div>
              </div>
            </div>`
          : ""
      }

      <div class="inspector-actions">
        <button type="button" class="inspector-btn suggest-edit" id="btn-suggest-correction">
          &#9998; Suggest a Date / Shape / Data Correction
        </button>
        ${
          hcadNum
            ? `<div class="hcad-link-status-note hidden" id="hcad-link-status-note"></div>
              <a
                href="https://search.hcad.org/"
                target="_blank"
                rel="noopener noreferrer"
                class="inspector-btn primary"
                id="btn-open-hcad"
                data-hcad-num="${hcadNum}"
                title="Copies ${hcadNum} to your clipboard and opens HCAD Property Search"
              >
                Copy # &amp; Open HCAD Search (${hcadNum}) &#8599;
              </a>
              <a
                href="https://arcweb.hcad.org/parcel-viewer-v2.0/?hcad_num=${encodeURIComponent(hcadNum)}"
                target="_blank"
                rel="noopener noreferrer"
                class="inspector-btn secondary"
                id="btn-open-hcad-gis"
                title="Direct link to parcel ${hcadNum} in HCAD Official GIS Parcel Viewer"
              >
                Open HCAD GIS Parcel Map (${hcadNum}) &#8599;
              </a>`
            : ""
        }
        <a
          href="${googleMapsUrl}"
          target="_blank"
          rel="noopener noreferrer"
          class="inspector-btn secondary"
          id="btn-open-google-maps"
          title="Open this building location on Google Maps &amp; Street View"
        >
          View on Google Maps &#8599;
        </a>
        <div class="inspector-action-row-2col">
          <button type="button" class="inspector-btn secondary" id="btn-copy-share-link">
            Copy Shareable Link
          </button>
          <button
            type="button"
            class="inspector-btn secondary"
            id="btn-print-property-sheet"
            title="Open a printable Preservation Houston archival property sheet / PDF dossier"
          >
            &#128424; Print Property Sheet
          </button>
        </div>
      </div>
    `;

    drawer.classList.remove("hidden");

    this._bindInspectorOverlapStackEvents(content);
    this._bindDeedRestrictionsCardEvents(content, props);

    content.querySelectorAll("[data-filter-chip]").forEach((chipBtn) => {
      chipBtn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const chipQuery = chipBtn.getAttribute("data-filter-chip") || "";
        const chipLabel = chipBtn.getAttribute("data-filter-label") || "";
        if (chipQuery) {
          this.triggerMetadataFilterSearch(chipQuery, chipLabel);
        }
      });
    });

    content.querySelectorAll("[data-highlight-boundary]").forEach((bBtn) => {
      bBtn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const bName = bBtn.getAttribute("data-highlight-boundary") || "";
        const bLayer = bBtn.getAttribute("data-boundary-layer") || "";
        if (bName && this.mapController) {
          this.mapController.highlightBoundaryByIdOrName(bName, {
            layerKey: bLayer,
            fitBounds: false,
            openInspector: false,
          });
          bBtn.textContent = "Outlined ✓";
          setTimeout(() => {
            bBtn.textContent =
              bLayer === "historic_wards"
                ? "Outline Ward"
                : bLayer === "platted_subdivisions"
                ? "Outline Plat"
                : "Outline";
          }, 2200);
        }
      });
    });

    content.querySelectorAll("[data-isolate-boundary]").forEach((isoBtn) => {
      isoBtn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const bName = isoBtn.getAttribute("data-isolate-boundary") || "";
        const bLayer = isoBtn.getAttribute("data-boundary-layer") || "";
        if (bName && this.mapController) {
          this.mapController.setIsolatedBoundary(
            { layerKey: bLayer, id: bName, name: bName, mode: "contents" },
            { fitBounds: true, openInspector: false }
          );
        }
      });
    });

    const btnPrintDossier = document.getElementById("btn-print-property-sheet");
    if (btnPrintDossier) {
      btnPrintDossier.addEventListener("click", () => {
        this.openPropertyDossierModal(props);
      });
    }

    this._populateInspectorPhotos({
      props,
      title,
      subtitle,
      hcadNum,
      lat: effLat,
      lng: effLng,
      streetViewUrl,
    });

    const btnSuggestCorr = document.getElementById("btn-suggest-correction");
    if (btnSuggestCorr) {
      btnSuggestCorr.addEventListener("click", () => {
        this.openCorrectionModal(props);
      });
    }

    const btnCopyAcct = document.getElementById("btn-copy-hcad-acct");
    if (btnCopyAcct && hcadNum) {
      btnCopyAcct.addEventListener("click", () => {
        if (navigator.clipboard) {
          navigator.clipboard.writeText(hcadNum);
        }
        btnCopyAcct.textContent = "Copied!";
        setTimeout(() => {
          btnCopyAcct.textContent = "Copy";
        }, 1800);
      });
    }

    if (hcadNum) {
      const fmtCurrency = (val) =>
        val != null && Number.isFinite(Number(val))
          ? "$" + Math.round(Number(val)).toLocaleString()
          : "N/A";

      fetchHcadLiveRecord(hcadNum)
        .then((rec) => {
          const card = document.getElementById("inspector-hcad-live-card");
          if (!card || card.getAttribute("data-hcad-num") !== hcadNum) return;
          const badge = document.getElementById("hcad-live-status-badge");
          if (!rec) {
            if (badge) badge.textContent = "Exempt / Unindexed";
            return;
          }
          if (badge) badge.textContent = "2026 Roll · Live";
          const apprEl = document.getElementById("hcad-live-appr");
          const mktEl = document.getElementById("hcad-live-mkt");
          const imprEl = document.getElementById("hcad-live-impr");
          const landEl = document.getElementById("hcad-live-land");
          if (apprEl) apprEl.textContent = fmtCurrency(rec.appraisedVal);
          if (mktEl) mktEl.textContent = fmtCurrency(rec.marketVal);
          if (imprEl) imprEl.textContent = fmtCurrency(rec.imprVal);
          if (landEl) landEl.textContent = fmtCurrency(rec.landVal);

          const metaParts = [];
          if (rec.stateClass) metaParts.push(`State Class ${rec.stateClass}`);
          if (rec.grade) metaParts.push(`Grade ${rec.grade}`);
          if (rec.legalDescription) metaParts.push(rec.legalDescription);
          const metaRow = document.getElementById("hcad-live-meta-row");
          const legalEl = document.getElementById("hcad-live-legal");
          if (metaRow && legalEl && metaParts.length > 0) {
            legalEl.textContent = metaParts.join(" · ");
            metaRow.style.display = "flex";
          }

          // Refine Google Maps & Street View links if geometry wasn't initially present
          if (!Number.isFinite(geomLat) && Array.isArray(rec.centroid)) {
            const [cLng, cLat] = rec.centroid;
            if (Number.isFinite(cLat) && Number.isFinite(cLng)) {
              const btnGmaps = document.getElementById("btn-open-google-maps");
              if (btnGmaps) {
                btnGmaps.href = `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(
                  `${cLat.toFixed(6)},${cLng.toFixed(6)}`
                )}`;
              }
              const refinedSvUrl = buildStreetViewUrl(cLat, cLng);
              const btnSv = document.getElementById("btn-inspector-streetview");
              if (btnSv) btnSv.href = refinedSvUrl;
              if (this._activePhotoState) {
                this._activePhotoState.streetViewUrl = refinedSvUrl;
              }
            }
          }

          // Enrich any missing Inspector fields with live HCAD record attributes
          const ownerCell = document.getElementById("inspector-cell-owner");
          if (ownerCell && rec.owner && (!props.owner || ownerCell.textContent === "Public / Unlisted")) {
            ownerCell.textContent = rec.owner;
          }
          const subCell = document.getElementById("inspector-cell-subdivision");
          if (
            subCell &&
            (rec.subdivision || rec.legalDescription) &&
            (!props.subdivision || subCell.textContent.trim() === "Not listed")
          ) {
            const liveSub = String(rec.subdivision || "").trim();
            if (liveSub) {
              subCell.innerHTML = `<button type="button" class="inspector-filter-chip" data-filter-chip="${liveSub.replace(/"/g, "&quot;")}" data-filter-label="Subdivision: ${liveSub.replace(/"/g, "&quot;")}" title="Click to search structures in subdivision '${liveSub.replace(/"/g, "&quot;")}'">${liveSub} &#128269;</button>${rec.legalDescription ? `<span class="inspector-chip-note">${rec.legalDescription}</span>` : ""}`;
              const newChip = subCell.querySelector("[data-filter-chip]");
              if (newChip) {
                newChip.addEventListener("click", (e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  this.triggerMetadataFilterSearch(liveSub, `Subdivision: ${liveSub}`);
                });
              }
            } else {
              subCell.textContent = rec.legalDescription;
            }
          }
          const bldCell = document.getElementById("inspector-cell-bld-sqft");
          if (bldCell && rec.bldgSqft && bldCell.textContent === "Unlisted") {
            bldCell.textContent = `${rec.bldgSqft.toLocaleString()} sq ft`;
          }
          const lotCell = document.getElementById("inspector-cell-lot-sqft");
          if (lotCell && rec.lotAreaSqft && lotCell.textContent === "Unlisted") {
            lotCell.textContent = `${rec.lotAreaSqft.toLocaleString()} sq ft`;
          }
        })
        .catch(() => {
          const badge = document.getElementById("hcad-live-status-badge");
          if (badge) badge.textContent = "Offline";
        });
    }

    const btnOpenHcad = document.getElementById("btn-open-hcad");
    const statusNote = document.getElementById("hcad-link-status-note");
    if (btnOpenHcad && hcadNum) {
      // Attempt to mint a fresh encrypted SearchResults deep-link token from HCAD's API
      fetchHcadDeepLink(hcadNum)
        .then((deepUrl) => {
          if (deepUrl && btnOpenHcad.getAttribute("data-hcad-num") === hcadNum) {
            btnOpenHcad.href = deepUrl;
            btnOpenHcad.setAttribute("data-deep-ready", "true");
            btnOpenHcad.setAttribute("data-minted-at", String(Date.now()));
            btnOpenHcad.title = `Direct HCAD Property Record deep link ready (${hcadNum})`;
            if (statusNote) statusNote.classList.add("hidden");
          }
        })
        .catch(() => {
          if (btnOpenHcad.getAttribute("data-hcad-num") !== hcadNum) return;
          btnOpenHcad.setAttribute("data-deep-fallback", "true");
          btnOpenHcad.innerHTML = `Copy # &amp; Open HCAD Search (${hcadNum}) &#8599;`;
          btnOpenHcad.title = `Copies ${hcadNum} to your clipboard and opens search.hcad.org (HCAD's direct-link token API is temporarily offline)`;
          if (statusNote) {
            statusNote.innerHTML = `HCAD's direct-link token service is temporarily offline. Clicking below automatically copies <strong>${hcadNum}</strong> to your clipboard to paste into HCAD Search, or use <strong>HCAD GIS Parcel Map &#8599;</strong> for a direct link.`;
            statusNote.classList.remove("hidden");
          }
        });

      btnOpenHcad.addEventListener("click", (evt) => {
        if (navigator.clipboard) {
          navigator.clipboard.writeText(hcadNum);
        }
        const isDeepReady = btnOpenHcad.getAttribute("data-deep-ready") === "true";
        const mintedAt = Number(btnOpenHcad.getAttribute("data-minted-at") || 0);
        if (isDeepReady && Date.now() - mintedAt > 45000) {
          // Refresh time-sensitive token in background if drawer was left open >45s
          evt.preventDefault();
          const newWin = window.open("about:blank", "_blank");
          fetchHcadDeepLink(hcadNum)
            .then((freshUrl) => {
              const targetUrl = freshUrl || btnOpenHcad.href || "https://search.hcad.org/";
              if (freshUrl) {
                btnOpenHcad.href = freshUrl;
                btnOpenHcad.setAttribute("data-minted-at", String(Date.now()));
              }
              if (newWin) newWin.location.href = targetUrl;
            })
            .catch(() => {
              if (newWin) newWin.location.href = "https://search.hcad.org/";
            });
          return;
        }
        if (!isDeepReady) {
          btnOpenHcad.innerHTML = `&#10003; Copied ${hcadNum} — Paste in HCAD Search &#8599;`;
          if (statusNote) {
            statusNote.innerHTML = `&#10003; Copied <strong>${hcadNum}</strong> to clipboard! Paste (<code>Ctrl+V</code> / <code>&#8984;V</code>) into the HCAD Account Number search box.`;
            statusNote.classList.remove("hidden");
            statusNote.classList.add("copied-highlight");
          }
          setTimeout(() => {
            if (btnOpenHcad.getAttribute("data-hcad-num") === hcadNum) {
              btnOpenHcad.innerHTML = `Copy # &amp; Open HCAD Search (${hcadNum}) &#8599;`;
            }
          }, 4000);
        }
      });
    }

    const btnShare = document.getElementById("btn-copy-share-link");
    if (btnShare) {
      btnShare.addEventListener("click", () => {
        const vp = this.mapController ? this.mapController.getCurrentViewport() : null;
        const url = buildShareableUrl(this.filterStore.getState(), vp, {
          includeViewport: true,
          selectedHcad: hcadNum,
          selectedFeatureId: !hcadNum ? props.id || "" : "",
          useQueryString: true,
        });
        if (navigator.clipboard) {
          navigator.clipboard.writeText(url);
        }
        btnShare.textContent = "✓ Link Copied!";
        setTimeout(() => {
          btnShare.textContent = "Copy Shareable Link";
        }, 2000);
      });
    }

    this._updateUrlHash(this.filterStore.getState());
    this._refreshShareModalContent();
  }

  async _populateInspectorPhotos({
    props,
    title,
    subtitle,
    hcadNum,
    lat,
    lng,
    streetViewUrl,
  }) {
    const requestToken = `${hcadNum}|${props.building_id || props.id || ""}|${Date.now()}`;
    this._latestPhotoRequestToken = requestToken;

    const photos = await resolveBuildingPhotos({
      buildingId: props.building_id || props.id || "",
      hcadNum,
      landmarkName: props.landmark_name || props.name || "",
      address: props.address || "",
      useCategory: props.use_category || "",
      lat,
      lng,
    });

    if (this._latestPhotoRequestToken !== requestToken) return;

    this._activePhotoState = {
      title,
      subtitle,
      streetViewUrl,
      photos,
      activeIndex: 0,
      compareMode: false,
      thenIndex: 0,
      nowIndex: Math.max(0, photos.length - 1),
      sliderPct: 50,
    };

    this._syncPhotoViews();
  }

  _syncPhotoViews() {
    const state = this._activePhotoState;
    if (!state) return;

    const badgeEl = document.getElementById("inspector-photo-badge");
    const headerBtnsEl = document.getElementById("inspector-photo-header-btns");
    const inspectorStageEl = document.getElementById("inspector-photo-stage");

    const photos = state.photos || [];
    if (badgeEl) {
      if (photos.length === 0) {
        badgeEl.textContent = "Street View Ready";
      } else if (photos.length === 1) {
        badgeEl.textContent = photos[0].photo_year
          ? `${photos[0].photo_year} Archival Photo`
          : "1 Archival Photo";
      } else {
        const firstYr = photos[0].photo_year || "Historic";
        const lastYr = photos[photos.length - 1].photo_year || "Present";
        badgeEl.textContent = `${photos.length} Eras · ${firstYr}–${lastYr}`;
      }
    }

    if (headerBtnsEl) {
      if (photos.length === 0) {
        headerBtnsEl.innerHTML = "";
      } else {
        headerBtnsEl.innerHTML = `
          ${
            photos.length >= 2
              ? `<button type="button" class="photo-mode-btn ${
                  state.compareMode ? "active" : ""
                }" id="btn-inspector-toggle-compare" title="Compare earliest and latest photographs with a draggable curtain slider">
                  ${state.compareMode ? "&#10003; Then &amp; Now" : "&#8644; Then &amp; Now"}
                </button>`
              : ""
          }
          <button type="button" class="photo-mode-btn" id="btn-inspector-expand-lightbox" title="Open full-screen archival photograph viewer">
            &#10530; Expand
          </button>
        `;
        const btnCompare = document.getElementById("btn-inspector-toggle-compare");
        if (btnCompare) {
          btnCompare.addEventListener("click", () => {
            state.compareMode = !state.compareMode;
            this._syncPhotoViews();
          });
        }
        const btnExpand = document.getElementById("btn-inspector-expand-lightbox");
        if (btnExpand) {
          btnExpand.addEventListener("click", () => {
            this.openPhotoLightboxModal();
          });
        }
      }
    }

    if (inspectorStageEl) {
      this._renderPhotoStage(inspectorStageEl, false);
    }

    const lightboxModal = document.getElementById("photo-lightbox-modal");
    if (lightboxModal && !lightboxModal.classList.contains("hidden")) {
      const lightboxBody = document.getElementById("photo-lightbox-body");
      const btnLbCompare = document.getElementById("btn-lightbox-toggle-compare");
      if (btnLbCompare) {
        btnLbCompare.classList.toggle("hidden", photos.length < 2);
        btnLbCompare.classList.toggle("active", Boolean(state.compareMode));
        btnLbCompare.innerHTML = state.compareMode
          ? "&#10003; Then &amp; Now Slider"
          : "&#8644; Then &amp; Now Slider";
      }
      if (lightboxBody) {
        this._renderPhotoStage(lightboxBody, true);
      }
    }
  }

  _renderPhotoStage(containerEl, isLightbox = false) {
    const state = this._activePhotoState;
    if (!state || !containerEl) return;

    const photos = state.photos || [];
    if (photos.length === 0) {
      containerEl.innerHTML = `
        <div class="photo-empty-note">
          No public-domain archival photograph is linked to this parcel yet. Launch <strong>Street View Time Machine</strong> below to scrub Google's 2007–2026 street-level photography, or submit a historic photo via <em>Suggest a Date / Data Correction</em>.
        </div>
      `;
      return;
    }

    if (state.compareMode && photos.length >= 2) {
      const thenIdx = Math.max(0, Math.min(photos.length - 1, state.thenIndex));
      const nowIdx = Math.max(0, Math.min(photos.length - 1, state.nowIndex));
      const thenPhoto = photos[thenIdx];
      const nowPhoto = photos[nowIdx];
      const pct = Number.isFinite(state.sliderPct) ? state.sliderPct : 50;

      const optionsHtml = (selectedIdx) =>
        photos
          .map(
            (p, idx) =>
              `<option value="${idx}" ${idx === selectedIdx ? "selected" : ""}>${
                p.era_label || p.photo_year || `Photo ${idx + 1}`
              }</option>`
          )
          .join("");

      containerEl.innerHTML = `
        <div class="photo-compare-wrapper">
          <div class="photo-compare-selectors">
            <div class="photo-compare-select-group">
              <span class="photo-compare-select-label">Then (Left)</span>
              <select class="photo-compare-select" data-compare-role="then" aria-label="Select earlier era photograph">
                ${optionsHtml(thenIdx)}
              </select>
            </div>
            <div class="photo-compare-select-group">
              <span class="photo-compare-select-label">Now (Right)</span>
              <select class="photo-compare-select" data-compare-role="now" aria-label="Select later era photograph">
                ${optionsHtml(nowIdx)}
              </select>
            </div>
          </div>

          <div class="photo-compare-stage">
            <div
              class="photo-ambient-bg"
              style="background-image: url('${isLightbox ? nowPhoto.full_url || nowPhoto.image_url : nowPhoto.image_url}');"
              aria-hidden="true"
            ></div>
            <img
              src="${isLightbox ? nowPhoto.full_url || nowPhoto.image_url : nowPhoto.image_url}"
              alt="${nowPhoto.caption || state.title}"
              class="photo-compare-img"
              loading="lazy"
            />
            <div class="photo-compare-before-clip" style="clip-path: inset(0 ${100 - pct}% 0 0);">
              <div
                class="photo-ambient-bg"
                style="background-image: url('${isLightbox ? thenPhoto.full_url || thenPhoto.image_url : thenPhoto.image_url}');"
                aria-hidden="true"
              ></div>
              <img
                src="${isLightbox ? thenPhoto.full_url || thenPhoto.image_url : thenPhoto.image_url}"
                alt="${thenPhoto.caption || state.title}"
                class="photo-compare-img"
                loading="lazy"
              />
            </div>
            <div class="photo-compare-divider" style="left: ${pct}%;">
              <div class="photo-compare-handle">&#8644;</div>
            </div>
            <span class="photo-compare-tag then">Then: ${thenPhoto.era_label || thenPhoto.photo_year || "Earlier"}</span>
            <span class="photo-compare-tag now">Now: ${nowPhoto.era_label || nowPhoto.photo_year || "Later"}</span>
            <input
              type="range"
              min="0"
              max="100"
              value="${pct}"
              class="photo-compare-range"
              aria-label="Drag left or right to compare historical and modern photographs"
            />
          </div>

          <div class="photo-caption-box">
            <div class="photo-caption-text">
              <strong>Then (${thenPhoto.photo_year || "Historic"}):</strong> ${thenPhoto.caption || ""}<br/>
              <strong>Now (${nowPhoto.photo_year || "Modern"}):</strong> ${nowPhoto.caption || ""}
            </div>
            <div class="photo-meta-row">
              <span>Drag curtain slider &#8644; to compare eras</span>
              ${
                !isLightbox
                  ? `<button type="button" class="photo-mode-btn" data-action="open-lightbox" style="flex:0 0 auto;">&#10530; Full Screen</button>`
                  : ""
              }
            </div>
          </div>
        </div>
      `;

      const rangeInput = containerEl.querySelector(".photo-compare-range");
      const beforeClip = containerEl.querySelector(".photo-compare-before-clip");
      const divider = containerEl.querySelector(".photo-compare-divider");
      if (rangeInput && beforeClip && divider) {
        rangeInput.addEventListener("input", (e) => {
          const val = Number(e.target.value);
          state.sliderPct = val;
          beforeClip.style.clipPath = `inset(0 ${100 - val}% 0 0)`;
          divider.style.left = `${val}%`;
        });
      }

      containerEl.querySelectorAll(".photo-compare-select").forEach((sel) => {
        sel.addEventListener("change", (e) => {
          const role = sel.getAttribute("data-compare-role");
          const idx = parseInt(e.target.value, 10) || 0;
          if (role === "then") state.thenIndex = idx;
          if (role === "now") state.nowIndex = idx;
          this._syncPhotoViews();
        });
      });

      const btnOpenLb = containerEl.querySelector('[data-action="open-lightbox"]');
      if (btnOpenLb) {
        btnOpenLb.addEventListener("click", () => this.openPhotoLightboxModal());
      }
      return;
    }

    // Single-Photo Timeline View
    const idx = Math.max(0, Math.min(photos.length - 1, state.activeIndex));
    const current = photos[idx];
    const activePhotoUrl = isLightbox ? current.full_url || current.image_url : current.image_url;
    const eraPillsHtml =
      photos.length >= 2
        ? `<div class="photo-era-pills" role="tablist" aria-label="Historical photograph eras">
            ${photos
              .map(
                (p, i) => `
                <button
                  type="button"
                  class="photo-era-pill ${i === idx ? "active" : ""}"
                  data-photo-idx="${i}"
                  role="tab"
                  aria-selected="${i === idx ? "true" : "false"}"
                >
                  ${p.era_label || p.photo_year || `Photo ${i + 1}`}
                </button>`
              )
              .join("")}
          </div>`
        : "";

    containerEl.innerHTML = `
      ${eraPillsHtml}
      <div class="photo-viewport" data-action="${isLightbox ? "" : "open-lightbox"}">
        <div
          class="photo-ambient-bg"
          style="background-image: url('${activePhotoUrl}');"
          aria-hidden="true"
        ></div>
        <img
          src="${activePhotoUrl}"
          alt="${current.caption || state.title}"
          class="photo-viewport-img"
          loading="lazy"
        />
        <span class="photo-era-overlay-badge">${
          current.era_label || (current.photo_year ? `${current.photo_year}` : "Archival Photo")
        }</span>
        ${
          !isLightbox
            ? `<button type="button" class="photo-expand-overlay-btn" data-action="open-lightbox" title="View full-size photograph">
                &#10530; Full Screen
              </button>`
            : ""
        }
        ${
          photos.length >= 2
            ? `<button type="button" class="photo-nav-btn prev" data-photo-Step="-1" aria-label="Previous Era Photograph" title="Previous Era Photograph">&#8249;</button>
               <button type="button" class="photo-nav-btn next" data-photo-Step="1" aria-label="Next Era Photograph" title="Next Era Photograph">&#8250;</button>`
            : ""
        }
      </div>
      <div class="photo-caption-box">
        <div class="photo-caption-text">${current.caption || state.title}</div>
        <div class="photo-meta-row">
          <span>${current.credit || "Public Domain / Wikimedia Commons"}</span>
          <span class="photo-meta-actions" style="display:inline-flex;align-items:center;gap:10px;">
            ${
              current.source_url
                ? `<a href="${current.source_url}" target="_blank" rel="noopener noreferrer" class="photo-source-link">
                    Source Archive &#8599;
                  </a>`
                : ""
            }
            <button
              type="button"
              class="photo-hide-btn"
              data-action="hide-photo"
              title="Hide this photograph if it does not depict the building or landscape"
              style="background:transparent;border:none;color:var(--text-muted,#9aa4ad);font-size:11px;cursor:pointer;padding:2px 4px;opacity:0.75;"
            >
              &#10005; Hide Photo
            </button>
          </span>
        </div>
      </div>
    `;

    // Auto-scroll active era pill horizontally into center of pill strip
    const pillsStrip = containerEl.querySelector(".photo-era-pills");
    const activePill = pillsStrip?.querySelector(".photo-era-pill.active");
    if (pillsStrip && activePill) {
      const targetLeft =
        activePill.offsetLeft - pillsStrip.clientWidth / 2 + activePill.clientWidth / 2;
      pillsStrip.scrollTo({ left: Math.max(0, targetLeft), behavior: "smooth" });
    }

    containerEl.querySelectorAll("[data-photo-idx]").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.activeIndex = parseInt(btn.getAttribute("data-photo-idx"), 10) || 0;
        this._syncPhotoViews();
      });
    });

    containerEl.querySelectorAll("[data-photo-Step]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const delta = parseInt(btn.getAttribute("data-photo-Step"), 10) || 1;
        state.activeIndex = (idx + delta + photos.length) % photos.length;
        this._syncPhotoViews();
      });
    });

    // Touch swipe left/right on .photo-viewport to step through eras on mobile
    const viewportEl = containerEl.querySelector(".photo-viewport");
    if (viewportEl && photos.length >= 2) {
      let touchStartX = null;
      let touchStartY = null;
      viewportEl.addEventListener(
        "touchstart",
        (e) => {
          if (e.touches && e.touches.length === 1) {
            touchStartX = e.touches[0].clientX;
            touchStartY = e.touches[0].clientY;
          }
        },
        { passive: true }
      );
      viewportEl.addEventListener(
        "touchend",
        (e) => {
          if (touchStartX === null || !e.changedTouches || e.changedTouches.length === 0) return;
          const dx = e.changedTouches[0].clientX - touchStartX;
          const dy = e.changedTouches[0].clientY - touchStartY;
          touchStartX = null;
          touchStartY = null;
          if (Math.abs(dx) > 42 && Math.abs(dx) > Math.abs(dy) * 1.4) {
            const delta = dx < 0 ? 1 : -1;
            state.activeIndex = (idx + delta + photos.length) % photos.length;
            this._syncPhotoViews();
          }
        },
        { passive: true }
      );
    }

    containerEl.querySelectorAll('[data-action="open-lightbox"]').forEach((el) => {
      el.addEventListener("click", () => this.openPhotoLightboxModal());
    });

    containerEl.querySelectorAll('[data-action="hide-photo"]').forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        if (!current?.image_url) return;
        hideBuildingPhoto(current.image_url);
        state.photos = (state.photos || []).filter((p) => p.image_url !== current.image_url);
        state.activeIndex = Math.max(0, Math.min(state.activeIndex, state.photos.length - 1));
        state.thenIndex = 0;
        state.nowIndex = Math.max(0, state.photos.length - 1);
        if (state.photos.length < 2) state.compareMode = false;
        this._syncPhotoViews();
      });
    });
  }

  openPhotoLightboxModal() {
    const state = this._activePhotoState;
    const modal = document.getElementById("photo-lightbox-modal");
    if (!state || !modal) return;

    const titleEl = document.getElementById("lightbox-modal-title");
    const kickerEl = document.getElementById("lightbox-modal-kicker");
    const svBtn = document.getElementById("btn-lightbox-streetview");
    const compareBtn = document.getElementById("btn-lightbox-toggle-compare");
    const bodyEl = document.getElementById("photo-lightbox-body");

    if (titleEl) titleEl.textContent = state.title || "Photographs Through History";
    if (kickerEl) {
      kickerEl.textContent = state.subtitle
        ? `Preservation Houston • ${state.subtitle}`
        : "Preservation Houston • Archival Photograph Timeline";
    }
    if (svBtn && state.streetViewUrl) {
      svBtn.href = state.streetViewUrl;
    }
    if (compareBtn) {
      compareBtn.classList.toggle("hidden", (state.photos || []).length < 2);
      compareBtn.classList.toggle("active", Boolean(state.compareMode));
      compareBtn.innerHTML = state.compareMode
        ? "&#10003; Then &amp; Now Slider"
        : "&#8644; Then &amp; Now Slider";
    }
    if (bodyEl) {
      this._renderPhotoStage(bodyEl, true);
    }

    modal.classList.remove("hidden");
  }

  _buildQrMatrixSvg(text = "") {
    // Deterministic 15x15 finder-pattern archival matrix emblem encoding the shareable URL hash
    const size = 15;
    let hash = 2166136261;
    for (let i = 0; i < text.length; i++) {
      hash ^= text.charCodeAt(i);
      hash = Math.imul(hash, 16777619);
    }
    const isFinder = (r, c) => {
      const inTL = r < 5 && c < 5;
      const inTR = r < 5 && c >= size - 5;
      const inBL = r >= size - 5 && c < 5;
      return inTL || inTR || inBL;
    };
    const finderVal = (r, c) => {
      const lr = r >= size - 5 ? r - (size - 5) : r;
      const lc = c >= size - 5 ? c - (size - 5) : c;
      if (lr === 0 || lr === 4 || lc === 0 || lc === 4) return 1;
      if (lr === 2 && lc === 2) return 1;
      return 0;
    };
    let rects = "";
    let seed = Math.abs(hash) || 1234567;
    for (let r = 0; r < size; r++) {
      for (let c = 0; c < size; c++) {
        let on = 0;
        if (isFinder(r, c)) {
          on = finderVal(r, c);
        } else if (r === 5 || c === 5) {
          on = (r + c) % 2 === 0 ? 1 : 0;
        } else {
          seed = (seed * 1664525 + 1013904223) >>> 0;
          on = (seed & 1) === 1 ? 1 : 0;
        }
        if (on) {
          rects += `<rect x="${c * 4}" y="${r * 4}" width="4" height="4" fill="#181614"/>`;
        }
      }
    }
    return `<svg viewBox="0 0 ${size * 4} ${size * 4}" width="64" height="64" class="dossier-qr-svg" aria-hidden="true">${rects}</svg>`;
  }

  async openPropertyDossierModal(props) {
    const modal = document.getElementById("dossier-modal");
    const bodyEl = document.getElementById("dossier-modal-body");
    if (!modal || !bodyEl || !props) return;

    const year = Number(props.year_built) >= 1836 ? Number(props.year_built) : null;
    const eraColor = year ? getYearColorHex(year, this.filterStore.getState().paletteStyle) : "#4d781d";
    const eraLabel = !year
      ? "Historic Structure"
      : year < 1890
      ? "Pioneer & Victorian"
      : year < 1900
      ? "Gilded Age"
      : year < 1920
      ? "Early Streetcar Era"
      : year < 1930
      ? "Roaring Twenties"
      : year < 1940
      ? "Art Deco & Depression Era"
      : year < 1960
      ? "Post-War Boom"
      : year < 1980
      ? "Space City Era"
      : year < 2000
      ? "Late 20th Century"
      : "21st Century";
    const isCurated = Boolean(
      props.is_curated_override ||
        props.curated_override ||
        (props.source && String(props.source).includes("Curated"))
    );
    const nameInfo = this._resolveBuildingNameAndAliases(props);
    const title = nameInfo.primaryName || "Historic Property";
    const address = props.address || "Houston, TX";
    const hcadAcct = props.hcad_num ? String(props.hcad_num).trim() : "";
    const hasRealHcad = hcadAcct && !hcadAcct.startsWith("PH-") && !hcadAcct.startsWith("DEMO-");

    const vp = this.mapController ? this.mapController.getCurrentViewport() : null;
    const shareUrl = buildShareableUrl(this.filterStore.getState(), vp, {
      includeViewport: true,
      selectedHcad: hcadAcct,
      selectedFeatureId: !hcadAcct && props.id ? String(props.id) : "",
      useQueryString: true,
    });

    let photos =
      this._activePhotoState && Array.isArray(this._activePhotoState.photos)
        ? this._activePhotoState.photos
        : [];
    if (photos.length === 0) {
      photos =
        (await resolveBuildingPhotos({
          buildingId: props.building_id || props.id || "",
          hcadNum: hcadAcct,
          landmarkName: title,
          address: props.address || "",
        })) || [];
    }
    const primaryPhoto = photos[0] || null;
    const secondaryPhoto = photos.length > 1 ? photos[photos.length - 1] : null;

    const goodBrickAwards = Array.isArray(props.good_brick_awards)
      ? props.good_brick_awards
      : hcadAcct && this.goodBrickByHcad?.get(hcadAcct)
      ? this.goodBrickByHcad.get(hcadAcct)
      : [];

    const badges = [];
    if (goodBrickAwards.length > 0) {
      const yrs = goodBrickAwards.map((a) => a.award_year).join(", ");
      badges.push(`★ Preservation Houston Good Brick Award (${yrs})`);
    } else if (props.good_brick_summary) {
      badges.push(`★ ${props.good_brick_summary}`);
    }
    if (props.landmark_type || props.designation) {
      badges.push(`${props.landmark_type || props.designation}`);
    }
    if (
      props.historic_district &&
      props.historic_district !== "Outside City District" &&
      props.historic_district !== "Outside Historic District"
    ) {
      badges.push(`Historic District: ${props.historic_district}`);
    }
    const todayStr = new Date().toLocaleDateString("en-US", {
      year: "numeric",
      month: "long",
      day: "numeric",
    });

    const displayStyle = this._resolveStyleAndClassInfo(props).displayStyle;

    const liveAppr = document.getElementById("hcad-live-appr")?.textContent || "";
    const liveOwner = document.getElementById("inspector-cell-owner")?.textContent || props.owner || "Public / Unlisted";
    const bldSqftText =
      Number(props.bld_area) > 0
        ? `${Number(props.bld_area).toLocaleString()} sq ft`
        : document.getElementById("inspector-cell-bld-sqft")?.textContent || "—";

    bodyEl.innerHTML = `
      <article class="dossier-sheet" id="printable-dossier-sheet">
        <header class="dossier-sheet-header">
          <div class="dossier-brand-col">
            <div class="dossier-org-kicker">PRESERVATION HOUSTON • THE HOUSTON BUILDING ATLAS</div>
            <h1 class="dossier-prop-title">${title}</h1>
            ${
              nameInfo.altNames.length > 0
                ? `<div class="dossier-prop-aka"><strong>Also Known As:</strong> ${nameInfo.altNames.join(" • ")}</div>`
                : ""
            }
            <div class="dossier-prop-address">${address}${props.city ? `, ${props.city}` : ", Houston, TX"}</div>
          </div>
          <div class="dossier-year-seal" style="border-color: ${eraColor}">
            <span class="dossier-year-label">${isCurated ? "VERIFIED BUILT" : "YEAR BUILT"}</span>
            <strong class="dossier-year-num">${year || "Undated"}</strong>
            <span class="dossier-era-label">${eraLabel}</span>
          </div>
        </header>

        ${
          badges.length > 0
            ? `<div class="dossier-badges-strip">
                ${badges.map((b) => `<span class="dossier-badge-pill">${b}</span>`).join("")}
              </div>`
            : ""
        }

        ${
          primaryPhoto
            ? `<section class="dossier-photos-row ${secondaryPhoto ? "two-up" : "one-up"}">
                <figure class="dossier-photo-fig">
                  <div class="dossier-photo-stage">
                    <div
                      class="photo-ambient-bg"
                      style="background-image: url('${primaryPhoto.image_url || primaryPhoto.url}');"
                      aria-hidden="true"
                    ></div>
                    <img src="${primaryPhoto.image_url || primaryPhoto.url}" alt="${title}" loading="eager" />
                  </div>
                  <figcaption>
                    <strong>${primaryPhoto.era_label || primaryPhoto.photo_year || "Archival View"}</strong> — ${
                      primaryPhoto.caption || title
                    } <em>(${primaryPhoto.source_credit || primaryPhoto.credit || "Preservation Houston Archive"})</em>
                  </figcaption>
                </figure>
                ${
                  secondaryPhoto
                    ? `<figure class="dossier-photo-fig">
                        <div class="dossier-photo-stage">
                          <div
                            class="photo-ambient-bg"
                            style="background-image: url('${secondaryPhoto.image_url || secondaryPhoto.url}');"
                            aria-hidden="true"
                          ></div>
                          <img src="${secondaryPhoto.image_url || secondaryPhoto.url}" alt="${title} comparison" loading="eager" />
                        </div>
                        <figcaption>
                          <strong>${secondaryPhoto.era_label || secondaryPhoto.photo_year || "Comparison View"}</strong> — ${
                            secondaryPhoto.caption || title
                          } <em>(${secondaryPhoto.source_credit || secondaryPhoto.credit || "Archival / Street View"})</em>
                        </figcaption>
                      </figure>`
                    : ""
                }
              </section>`
            : ""
        }

        <section class="dossier-grid-section">
          <div class="dossier-section-title">ARCHITECTURAL &amp; PARCEL RECORD</div>
          <div class="dossier-kv-grid">
            <div class="dossier-kv-cell">
              <span class="dossier-k">HCAD Account #</span>
              <span class="dossier-v mono">${hcadAcct || "Exempt / N/A"}</span>
            </div>
            <div class="dossier-kv-cell">
              <span class="dossier-k">Architectural Style</span>
              <span class="dossier-v">${displayStyle}</span>
            </div>
            <div class="dossier-kv-cell">
              <span class="dossier-k">Architect / Builder</span>
              <span class="dossier-v">${props.architect || "Not Listed"}</span>
            </div>
            <div class="dossier-kv-cell">
              <span class="dossier-k">Historic District</span>
              <span class="dossier-v">${
                props.historic_district &&
                props.historic_district !== "Outside City District" &&
                props.historic_district !== "Outside Historic District"
                  ? props.historic_district
                  : "Individual Landmark / Site"
              }</span>
            </div>
            <div class="dossier-kv-cell">
              <span class="dossier-k">Recorded Owner</span>
              <span class="dossier-v">${liveOwner}</span>
            </div>
            <div class="dossier-kv-cell">
              <span class="dossier-k">Gross Building Area</span>
              <span class="dossier-v mono">${bldSqftText}</span>
            </div>
            <div class="dossier-kv-cell">
              <span class="dossier-k">Stories / Scale</span>
              <span class="dossier-v mono">${
                Number(props.stories) > 0
                  ? `${props.stories} stories${props.height_m ? ` (~${props.height_m}m)` : ""}`
                  : "1 story"
              }</span>
            </div>
            <div class="dossier-kv-cell">
              <span class="dossier-k">${liveAppr && liveAppr !== "…" ? "2026 Appraised Value" : "Data Provenance"}</span>
              <span class="dossier-v mono">${
                liveAppr && liveAppr !== "…"
                  ? liveAppr
                  : isCurated
                  ? "PH Curated + HCAD"
                  : "HCAD / COH GIS"
              }</span>
            </div>
          </div>
        </section>

        ${
          goodBrickAwards.length > 0 || props.source_citation || props.notes
            ? `<section class="dossier-notes-section">
                <div class="dossier-section-title">HISTORICAL &amp; PRESERVATION CITATIONS</div>
                ${goodBrickAwards
                  .map(
                    (a) =>
                      `<p class="dossier-note-para"><strong>★ Good Brick Award (${a.award_year}):</strong> ${
                        a.recipient ? `<em>${a.recipient}</em> — ` : ""
                      }${a.reason || a.raw_description || "Recognized for excellence in historic preservation."}</p>`
                  )
                  .join("")}
                ${
                  props.source_citation
                    ? `<p class="dossier-note-para"><strong>Archival Date Verification (${
                        props.verified_by || "Preservation Houston"
                      }):</strong> ${props.source_citation}</p>`
                    : ""
                }
              </section>`
            : ""
        }

        <footer class="dossier-sheet-footer">
          <div class="dossier-footer-meta">
            <div><strong>Generated by The Houston Building Atlas</strong> • Preservation Houston</div>
            <div>Date Prepared: ${todayStr}${hasRealHcad ? ` • HCAD Parcel ${hcadAcct}` : ""}</div>
            <div class="dossier-share-url mono">${shareUrl}</div>
          </div>
          <div class="dossier-qr-box">
            ${this._buildQrMatrixSvg(shareUrl)}
            <span class="dossier-qr-caption">Interactive Atlas Link</span>
          </div>
        </footer>
      </article>
    `;

    modal.classList.remove("hidden");
  }

  openShareModal() {
    const shareModal = document.getElementById("share-modal");
    if (!shareModal) return;
    this._refreshShareModalContent();
    shareModal.classList.remove("hidden");
  }

  _renderSharePresetsGrid() {
    const grid = document.getElementById("share-presets-grid");
    if (!grid) return;

    grid.innerHTML = SHARE_VIEW_PRESETS.map(
      (preset) => `
      <button
        type="button"
        class="share-preset-card"
        data-preset-id="${preset.id}"
        title="Click to apply '${preset.label}' to the map and copy its shareable URL"
      >
        <div class="share-preset-top">
          <span class="share-preset-title">${preset.label}</span>
          <span class="share-preset-badge mono" data-preset-status="${preset.id}">Apply &amp; Copy</span>
        </div>
        <p class="share-preset-desc">${preset.description}</p>
      </button>`
    ).join("");

    grid.querySelectorAll("[data-preset-id]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const presetId = btn.getAttribute("data-preset-id");
        const preset = SHARE_VIEW_PRESETS.find((p) => p.id === presetId);
        if (!preset) return;

        // Apply the preset configuration to the live map
        this.filterStore.setState(preset.statePatch || preset.patch || {});
        this._refreshShareModalContent();

        // Build clean shareable URL (respecting user's viewport/sidebar checkboxes)
        const inputEl = document.getElementById("share-url-input");
        const feedbackEl = document.getElementById("share-copy-feedback");
        const statusBadge = btn.querySelector("[data-preset-status]");
        const urlToCopy = inputEl ? inputEl.value : window.location.href;

        if (navigator.clipboard) {
          navigator.clipboard.writeText(urlToCopy);
        }

        grid.querySelectorAll(".share-preset-card").forEach((c) => c.classList.remove("active"));
        btn.classList.add("active");

        if (statusBadge) statusBadge.textContent = "✓ Copied URL!";
        if (feedbackEl) {
          feedbackEl.textContent = `✓ Applied "${preset.label}" & copied link!`;
        }
        setTimeout(() => {
          if (statusBadge) statusBadge.textContent = "Apply & Copy";
          if (feedbackEl) feedbackEl.textContent = "";
        }, 2500);
      });
    });
  }

  _refreshShareModalContent() {
    const shareModal = document.getElementById("share-modal");
    if (!shareModal) return;

    const state = this.filterStore.getState();
    const vp = this.mapController ? this.mapController.getCurrentViewport() : null;
    const selProps = this.mapController ? this.mapController.selectedFeatureProps : null;
    const selectedHcad = selProps?.hcad_num ? String(selProps.hcad_num).trim() : "";
    const selectedFeatureId = !selectedHcad && selProps?.id ? String(selProps.id).trim() : "";

    const chkViewport = document.getElementById("chk-share-include-viewport");
    const chkSelection = document.getElementById("chk-share-include-selection");
    const rowSelection = document.getElementById("row-share-include-selection");
    const lblSelection = document.getElementById("lbl-share-include-selection");
    const chkSidebar = document.getElementById("chk-share-collapse-sidebar");

    const hasSelection = Boolean(selectedHcad || selectedFeatureId);
    if (rowSelection) {
      rowSelection.style.display = hasSelection ? "flex" : "none";
    }
    if (lblSelection && selProps) {
      const propLabel = selProps.landmark_name || selProps.address || selectedHcad || selectedFeatureId;
      lblSelection.innerHTML = `Include selected property (<strong>${propLabel}</strong>)`;
    }

    const includeViewport = chkViewport ? chkViewport.checked : true;
    const includeSelection = hasSelection && (chkSelection ? chkSelection.checked : true);
    const collapseSidebar = chkSidebar ? chkSidebar.checked : false;

    const shareUrl = buildShareableUrl(state, vp, {
      includeViewport,
      selectedHcad: includeSelection ? selectedHcad : "",
      selectedFeatureId: includeSelection ? selectedFeatureId : "",
      collapseSidebar,
      useQueryString: true,
    });

    const embedUrl = buildShareableUrl(state, vp, {
      includeViewport,
      selectedHcad: includeSelection ? selectedHcad : "",
      selectedFeatureId: includeSelection ? selectedFeatureId : "",
      collapseSidebar: true,
      useQueryString: true,
    });

    const urlInput = document.getElementById("share-url-input");
    if (urlInput) urlInput.value = shareUrl;

    const openTabBtn = document.getElementById("btn-open-share-url-tab");
    if (openTabBtn) openTabBtn.href = shareUrl;

    const embedInput = document.getElementById("share-embed-input");
    if (embedInput) {
      embedInput.value = `<iframe src="${embedUrl}" width="100%" height="680" style="border:0;border-radius:12px;" loading="lazy" title="The Houston Building Atlas — Preservation Houston"></iframe>`;
    }

    // Render summary pills describing the active configuration
    const pillsContainer = document.getElementById("share-config-summary-pills");
    if (pillsContainer) {
      const basemapLabels = {
        dark_archival: "Basemap: Archival Dark",
        warm_parchment: "Basemap: Light Parchment",
        satellite: "Basemap: Aerial Satellite",
        solid_dark: "Basemap: Solid Dark (No Map)",
        solid_light: "Basemap: Solid Light (No Map)",
      };
      const geomLabels = {
        buildings: "Footprints: Buildings ON",
        both: "Footprints: Buildings + Parcels",
        parcels: "Footprints: Tax Parcels Only",
        none: "Footprints: Buildings OFF",
      };
      const overlayLabels = {
        goodBrickAwards: "★ Good Brick Awards",
        landmarks: "COH Landmarks",
        historicDistricts: "Historic Districts",
        heritageDistricts: "Freedmen's Town",
        nrhpDistricts: "NRHP Districts",
        neighborhoods: "Neighborhoods",
        plattedSubdivisions: "Platted Subdivisions & Deeds",
        landUseProtections: "Ch. 42 Lot & Setback Protections",
        superNeighborhoods: "Super Neighborhoods",
        historicWards: "Historic Wards",
        thcMarkers: "THC Markers",
        annexations: "Annexation History",
        historicMap: "Historic Topo Map",
      };

      const pills = [
        `<span class="share-pill-tag">${basemapLabels[state.basemap] || "Archival Dark"}</span>`,
        `<span class="share-pill-tag ${state.renderMode === "none" ? "muted" : "accent"}">${
          geomLabels[state.renderMode] || "Footprints: Buildings ON"
        }</span>`,
      ];

      const activeOverlays = Object.entries(state.layers || {})
        .filter(([, v]) => Boolean(v))
        .map(([k]) => overlayLabels[k] || k);

      if (activeOverlays.length === 0) {
        pills.push(`<span class="share-pill-tag muted">Overlays: None</span>`);
      } else {
        for (const lbl of activeOverlays) {
          pills.push(`<span class="share-pill-tag green">${lbl}</span>`);
        }
      }

      if (state.minYear > 1836 || state.maxYear < 2026 || state.selectedDecade !== "all") {
        pills.push(
          `<span class="share-pill-tag">Years: ${state.minYear}–${state.maxYear}</span>`
        );
      }
      if (state.extrude3D) {
        pills.push(`<span class="share-pill-tag">3D Extrusion: ON</span>`);
      }
      if (state.isolatedBoundary && state.isolatedBoundary.name) {
        pills.push(
          `<span class="share-pill-tag accent">&#127919; Isolated: ${state.isolatedBoundary.name} (${
            state.isolatedBoundary.mode || "contents"
          })</span>`
        );
      }
      if (includeSelection && selProps) {
        pills.push(
          `<span class="share-pill-tag accent">Inspecting: ${
            selProps.landmark_name || selProps.address || selectedHcad
          }</span>`
        );
      }

      pillsContainer.innerHTML = pills.join("");
    }
  }

  _renderIsolationBanner(iso) {
    const banner = document.getElementById("isolation-active-banner");
    if (!banner) return;
    if (!iso || !iso.name) {
      banner.classList.add("hidden");
      banner.innerHTML = "";
      return;
    }

    this._ensureCachedExportFootprints(iso);

    const mode = iso.mode || "contents";
    const curBase = this.filterStore.getState().basemap;
    const isSolid = curBase === "solid_dark" || curBase === "solid_light";

    banner.innerHTML = `
      <div class="isolation-banner-info">
        <span class="isolation-banner-badge">&#127919; Isolated Area</span>
        <span class="isolation-banner-title">${iso.name}</span>
      </div>
      <div class="isolation-banner-controls">
        <div class="isolation-mode-pills" role="group" aria-label="Isolation Display Mode">
          <button
            type="button"
            class="iso-mode-btn ${mode === "contents" ? "active" : ""}"
            data-banner-iso-mode="contents"
            title="Show the boundary border plus all building footprints and markers inside"
          >
            Border + Buildings
          </button>
          <button
            type="button"
            class="iso-mode-btn ${mode === "footprints_only" ? "active" : ""}"
            data-banner-iso-mode="footprints_only"
            title="Show only the building footprints inside this polygon"
          >
            Buildings Only
          </button>
          <button
            type="button"
            class="iso-mode-btn ${mode === "border_only" ? "active" : ""}"
            data-banner-iso-mode="border_only"
            title="Show only the polygon border silhouette"
          >
            Border Only
          </button>
        </div>
        <button
          type="button"
          class="iso-action-btn"
          id="btn-banner-toggle-solid"
          title="Toggle between clean solid background (no map underlay) and map tiles"
        >
          ${isSolid ? "&#9635; Map Underlay" : "&#9632; Solid Backdrop"}
        </button>
        <button
          type="button"
          class="iso-action-btn"
          id="btn-banner-open-export"
          title="Open Vector SVG, Print, T-Shirt &amp; Coaster Studio for this isolated area"
        >
          &#127912; Vector / Shirt Studio
        </button>
        <button
          type="button"
          class="iso-action-btn clear"
          id="btn-banner-exit-isolation"
          title="Exit Polygon Isolation Mode and show the full Houston map"
        >
          &#10005; Exit
        </button>
      </div>
    `;
    banner.classList.remove("hidden");

    banner.querySelectorAll("[data-banner-iso-mode]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const nextMode = btn.getAttribute("data-banner-iso-mode") || "contents";
        if (this.mapController) {
          this.mapController.setIsolationMode(nextMode);
        }
      });
    });

    const btnSolid = document.getElementById("btn-banner-toggle-solid");
    if (btnSolid) {
      btnSolid.addEventListener("click", () => {
        const st = this.filterStore.getState();
        if (st.basemap === "solid_dark" || st.basemap === "solid_light") {
          this.filterStore.setState({ basemap: "dark_archival" });
        } else {
          this.filterStore.setState({
            basemap: st.basemap === "warm_parchment" ? "solid_light" : "solid_dark",
          });
        }
        this._renderIsolationBanner(this.mapController.getIsolatedBoundary());
      });
    }

    const btnExport = document.getElementById("btn-banner-open-export");
    if (btnExport) {
      btnExport.addEventListener("click", () => {
        this.openExportStudioModal({
          layerKey: iso.layerKey,
          id: iso.id,
          name: iso.name,
        });
      });
    }

    const btnExit = document.getElementById("btn-banner-exit-isolation");
    if (btnExit) {
      btnExit.addEventListener("click", () => {
        if (this.mapController) {
          this.mapController.clearIsolatedBoundary();
        }
      });
    }
  }

  _bindExportStudioControls() {
    const modal = document.getElementById("export-studio-modal");
    const btnOpenTop = document.getElementById("btn-open-export-studio");
    const btnClose = document.getElementById("btn-close-export-studio");

    if (btnOpenTop) {
      btnOpenTop.addEventListener("click", () => this.openExportStudioModal());
    }
    if (btnClose && modal) {
      btnClose.addEventListener("click", () => modal.classList.add("hidden"));
    }
    if (modal) {
      modal.addEventListener("click", (e) => {
        if (e.target === modal) modal.classList.add("hidden");
      });
    }

    const areaSelect = document.getElementById("select-export-target-area");
    if (areaSelect) {
      areaSelect.addEventListener("change", () => {
        const val = areaSelect.value || "";
        if (val === "__viewport__") {
          this._exportStudioState.targetSpec = "__viewport__";
        } else if (val.includes("::")) {
          const [layerKey, idOrName] = val.split("::");
          this._exportStudioState.targetSpec = {
            layerKey,
            id: idOrName,
            name: idOrName,
          };
        } else {
          this._exportStudioState.targetSpec = null;
        }
        this._exportStudioState.customTitle = "";
        this._exportStudioState.customSubtitle = "";
        const titleInput = document.getElementById("input-export-title");
        const subInput = document.getElementById("input-export-subtitle");
        if (titleInput) titleInput.value = "";
        if (subInput) subInput.value = "";
        this._renderExportStudioPreview();
      });
    }

    document.querySelectorAll("[data-export-comp]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const comp = btn.getAttribute("data-export-comp") || "figure_ground";
        this._exportStudioState.composition = comp;
        if (comp === "era_poster" && this._exportStudioState.theme !== "archival_era") {
          this._exportStudioState.theme = "archival_era";
        }
        this._syncExportStudioPills();
        this._renderExportStudioPreview();
      });
    });

    document.querySelectorAll("[data-export-theme]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const theme = btn.getAttribute("data-export-theme") || "stencil_white";
        this._exportStudioState.theme = theme;
        this._syncExportStudioPills();
        this._renderExportStudioPreview();
      });
    });

    document.querySelectorAll("[data-export-format]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const fmt = btn.getAttribute("data-export-format") || "square";
        this._exportStudioState.format = fmt;
        this._syncExportStudioPills();
        this._renderExportStudioPreview();
      });
    });

    const chkTransparent = document.getElementById("chk-export-transparent");
    if (chkTransparent) {
      chkTransparent.addEventListener("change", (e) => {
        this._exportStudioState.transparentBg = Boolean(e.target.checked);
        this._renderExportStudioPreview();
      });
    }

    const chkFillBld = document.getElementById("chk-export-fill-buildings");
    if (chkFillBld) {
      chkFillBld.addEventListener("change", (e) => {
        this._exportStudioState.fillBuildings = Boolean(e.target.checked);
        this._renderExportStudioPreview();
      });
    }

    const chkLandmarks = document.getElementById("chk-export-show-landmarks");
    if (chkLandmarks) {
      chkLandmarks.addEventListener("change", (e) => {
        this._exportStudioState.showLandmarks = Boolean(e.target.checked);
        this._renderExportStudioPreview();
      });
    }

    const chkCaption = document.getElementById("chk-export-show-caption");
    if (chkCaption) {
      chkCaption.addEventListener("change", (e) => {
        this._exportStudioState.showCaption = Boolean(e.target.checked);
        this._renderExportStudioPreview();
      });
    }

    const inputTitle = document.getElementById("input-export-title");
    if (inputTitle) {
      inputTitle.addEventListener("input", (e) => {
        this._exportStudioState.customTitle = e.target.value;
        this._renderExportStudioPreview();
      });
    }

    const inputSubtitle = document.getElementById("input-export-subtitle");
    if (inputSubtitle) {
      inputSubtitle.addEventListener("input", (e) => {
        this._exportStudioState.customSubtitle = e.target.value;
        this._renderExportStudioPreview();
      });
    }

    const sliderWeight = document.getElementById("slider-export-border-weight");
    if (sliderWeight) {
      sliderWeight.addEventListener("input", (e) => {
        const val = parseFloat(e.target.value) || 4;
        this._exportStudioState.borderWeight = val;
        const readout = document.getElementById("export-border-weight-readout");
        if (readout) readout.textContent = `${val.toFixed(1)}px`;
        this._renderExportStudioPreview();
      });
    }

    const btnApplyIso = document.getElementById("btn-export-apply-isolation");
    if (btnApplyIso) {
      btnApplyIso.addEventListener("click", () => {
        const ext = this._exportStudioState.lastExtraction;
        if (ext && ext.isIsolatedPolygon && this.mapController) {
          const modeMap = {
            figure_ground: "contents",
            footprints_only: "footprints_only",
            border_only: "border_only",
            era_poster: "contents",
          };
          this.mapController.setIsolatedBoundary(
            {
              layerKey: ext.boundaryLayerKey,
              id: ext.boundaryName,
              name: ext.boundaryName,
              mode: modeMap[this._exportStudioState.composition] || "contents",
            },
            { fitBounds: true, openInspector: false }
          );
          if (modal) modal.classList.add("hidden");
        }
      });
    }

    const btnDlSvg = document.getElementById("btn-download-export-svg");
    if (btnDlSvg) {
      btnDlSvg.addEventListener("click", () => this.downloadExportSvg());
    }

    const btnDlPng = document.getElementById("btn-download-export-png");
    if (btnDlPng) {
      btnDlPng.addEventListener("click", () => this.downloadExportPng());
    }

    const btnDlScreenshot = document.getElementById("btn-download-live-screenshot");
    if (btnDlScreenshot) {
      btnDlScreenshot.addEventListener("click", () => this.downloadLiveMapScreenshot());
    }

    const btnDlGeoJson = document.getElementById("btn-download-export-geojson");
    if (btnDlGeoJson) {
      btnDlGeoJson.addEventListener("click", () => this.downloadExportGeoJson());
    }
  }

  _syncExportStudioPills() {
    const st = this._exportStudioState;
    document.querySelectorAll("[data-export-comp]").forEach((b) => {
      b.classList.toggle("active", b.getAttribute("data-export-comp") === st.composition);
    });
    document.querySelectorAll("[data-export-theme]").forEach((b) => {
      b.classList.toggle("active", b.getAttribute("data-export-theme") === st.theme);
    });
    document.querySelectorAll("[data-export-format]").forEach((b) => {
      b.classList.toggle("active", b.getAttribute("data-export-format") === st.format);
    });
  }

  openExportStudioModal(initialBoundarySpec = null, opts = {}) {
    const modal = document.getElementById("export-studio-modal");
    if (!modal) return;

    if (initialBoundarySpec && initialBoundarySpec.name) {
      this._exportStudioState.targetSpec = {
        layerKey: initialBoundarySpec.layerKey || "neighborhoods",
        id: initialBoundarySpec.id || initialBoundarySpec.name,
        name: initialBoundarySpec.name,
      };
      if (!opts.preserveCustomText) {
        this._exportStudioState.customTitle = "";
        this._exportStudioState.customSubtitle = "";
      }
    } else if (this.mapController?.getIsolatedBoundary()) {
      const iso = this.mapController.getIsolatedBoundary();
      this._exportStudioState.targetSpec = {
        layerKey: iso.layerKey,
        id: iso.id,
        name: iso.name,
      };
    } else if (this.mapController?.highlightedBoundary?.feature) {
      const hb = this.mapController.highlightedBoundary;
      this._exportStudioState.targetSpec = {
        layerKey: hb.layerKey || "neighborhoods",
        id: hb.id || hb.name,
        name: hb.name,
      };
    } else if (!this._exportStudioState.targetSpec) {
      // Default to an iconic Houston historic district so the preview immediately showcases a great shape
      this._exportStudioState.targetSpec = {
        layerKey: "historic_districts",
        id: "Norhill Historic District",
        name: "Norhill Historic District",
      };
    }

    this._populateExportTargetAreaSelect();
    this._syncExportStudioPills();
    this._renderExportStudioPreview();
    modal.classList.remove("hidden");
  }

  _populateExportTargetAreaSelect() {
    const sel = document.getElementById("select-export-target-area");
    if (!sel) return;

    const curTarget = this._exportStudioState.targetSpec;
    const curVal =
      curTarget === "__viewport__"
        ? "__viewport__"
        : curTarget && typeof curTarget === "object"
        ? `${curTarget.layerKey}::${curTarget.name || curTarget.id}`
        : "";

    const featuredGroups = [
      {
        label: "Current Map Selection / Viewport",
        items: [
          ...(curTarget && typeof curTarget === "object"
            ? [
                {
                  val: `${curTarget.layerKey}::${curTarget.name || curTarget.id}`,
                  text: `🎯 Selected: ${curTarget.name || curTarget.id}`,
                },
              ]
            : []),
          { val: "__viewport__", text: "🗺️ Current Map Viewport (All Visible Structures)" },
        ],
      },
      {
        label: "COH Historic Districts & Heritage Districts (All 24)",
        items: [
          { val: "historic_districts::Norhill Historic District", text: "Norhill Historic District" },
          { val: "historic_districts::Woodland Heights Historic District", text: "Woodland Heights Historic District" },
          { val: "historic_districts::Old Sixth Ward Historic District", text: "Old Sixth Ward Protected Historic District" },
          { val: "heritage_districts::Freedmen's Town Heritage District", text: "Freedmen's Town Heritage District" },
          { val: "historic_districts::Houston Heights East Historic District", text: "Houston Heights East Historic District" },
          { val: "historic_districts::Houston Heights West Historic District", text: "Houston Heights West Historic District" },
          { val: "historic_districts::Houston Heights South Historic District", text: "Houston Heights South Historic District" },
          { val: "historic_districts::Freeland Historic District", text: "Freeland Historic District" },
          { val: "historic_districts::Germantown Historic District", text: "Germantown Historic District" },
          { val: "historic_districts::High First Ward Historic District", text: "High First Ward Historic District" },
          { val: "historic_districts::Starkweather Historic District", text: "Starkweather Historic District" },
          { val: "historic_districts::Brunner-Harmonium Historic District", text: "Brunner-Harmonium Historic District" },
          { val: "historic_districts::Main Street/Market Square Historic District", text: "Main Street/Market Square Historic District" },
          { val: "historic_districts::Avondale East Historic District", text: "Avondale East Historic District" },
          { val: "historic_districts::Avondale West Historic District", text: "Avondale West Historic District" },
          { val: "historic_districts::Broadacres Historic District", text: "Broadacres Historic District" },
          { val: "historic_districts::Boulevard Oaks Historic District", text: "Boulevard Oaks Historic District" },
          { val: "historic_districts::Courtland Place Historic District", text: "Courtland Place Historic District" },
          { val: "historic_districts::First Montrose Commons Historic District", text: "First Montrose Commons Historic District" },
          { val: "historic_districts::Westmoreland Historic District", text: "Westmoreland Historic District" },
          { val: "historic_districts::Audubon Place Historic District", text: "Audubon Place Historic District" },
          { val: "historic_districts::Shadow Lawn Historic District", text: "Shadow Lawn Historic District" },
          { val: "historic_districts::West Eleventh Place Historic District", text: "West Eleventh Place Historic District" },
          { val: "historic_districts::Glenbrook Valley Historic District", text: "Glenbrook Valley Historic District" },
        ],
      },
      {
        label: "National Register (NRHP) Historic Districts",
        items: [
          { val: "nrhp_districts::HOUSTON HEIGHTS MRA", text: "Houston Heights MRA (NRHP)" },
          { val: "nrhp_districts::INDEPENDENCE HEIGHTS N.R.", text: "Independence Heights N.R. (NRHP)" },
          { val: "nrhp_districts::NEAR NORTHSIDE N.R.", text: "Near Northside N.R. (NRHP)" },
          { val: "nrhp_districts::IDYLWOOD N.R.", text: "Idylwood N.R. (NRHP)" },
          { val: "nrhp_districts::FREEDMEN'S TOWN N.R.", text: "Freedmen's Town N.R. (NRHP)" },
          { val: "nrhp_districts::OLD SIXTH WARD N.R.", text: "Old Sixth Ward N.R. (NRHP)" },
          { val: "nrhp_districts::BOULEVARD OAKS N.R.", text: "Boulevard Oaks N.R. (NRHP)" },
          { val: "nrhp_districts::BROADACRES N.R.", text: "Broadacres N.R. (NRHP)" },
          { val: "nrhp_districts::COURTLANDT PLACE N.R.", text: "Courtlandt Place N.R. (NRHP)" },
          { val: "nrhp_districts::WESTMORELAND N.R.", text: "Westmoreland N.R. (NRHP)" },
          { val: "nrhp_districts::WEST ELEVENTH PLACE N.R.", text: "West Eleventh Place N.R. (NRHP)" },
          { val: "nrhp_districts::MAIN STREET/MARKET SQUARE N.R.", text: "Main Street/Market Square N.R. (NRHP)" },
        ],
      },
      {
        label: "Historic Aldermanic Wards (1839–1905)",
        items: [
          { val: "historic_wards::First Ward", text: "First Ward (1903–1905 Charter)" },
          { val: "historic_wards::Second Ward", text: "Second Ward (1903–1905 Charter)" },
          { val: "historic_wards::Third Ward", text: "Third Ward (1903–1905 Charter)" },
          { val: "historic_wards::Fourth Ward", text: "Fourth Ward (1903–1905 Charter)" },
          { val: "historic_wards::Fifth Ward", text: "Fifth Ward (1903–1905 Charter)" },
          { val: "historic_wards::Sixth Ward", text: "Sixth Ward (1903–1905 Charter)" },
        ],
      },
      {
        label: "Famous Houston Neighborhoods & Subdivisions",
        items: [
          { val: "neighborhoods::Montrose", text: "Montrose (Neighborhood)" },
          { val: "neighborhoods::Houston Heights", text: "Houston Heights (Neighborhood)" },
          { val: "neighborhoods::River Oaks", text: "River Oaks (Neighborhood)" },
          { val: "neighborhoods::Southampton", text: "Southampton Place (Neighborhood)" },
          { val: "neighborhoods::Boulevard Oaks", text: "Boulevard Oaks (Neighborhood)" },
          { val: "neighborhoods::Woodland Heights", text: "Woodland Heights (Neighborhood)" },
          { val: "neighborhoods::Riverside Terrace", text: "Riverside Terrace (Neighborhood)" },
          { val: "neighborhoods::Garden Oaks", text: "Garden Oaks (Neighborhood)" },
          { val: "neighborhoods::Oak Forest", text: "Oak Forest (Neighborhood)" },
          { val: "neighborhoods::Idylwood", text: "Idylwood (Neighborhood)" },
          { val: "neighborhoods::Eastwood", text: "Eastwood (Neighborhood)" },
          { val: "neighborhoods::Pecan Park Place", text: "Pecan Park Place (Neighborhood)" },
          { val: "neighborhoods::Lindale Park", text: "Lindale Park (Neighborhood)" },
          { val: "super_neighborhoods::Museum Park", text: "Museum Park / Museum District (Super Neighborhood)" },
          { val: "neighborhoods::Midtown", text: "Midtown (Neighborhood)" },
          { val: "neighborhoods::Downtown", text: "Downtown Houston (Neighborhood)" },
          { val: "neighborhoods::East Downtown", text: "East Downtown / EaDo (Neighborhood)" },
          { val: "neighborhoods::Rice Military", text: "Rice Military (Neighborhood)" },
          { val: "neighborhoods::Camp Logan", text: "Camp Logan (Neighborhood)" },
          { val: "neighborhoods::Cottage Grove", text: "Cottage Grove (Neighborhood)" },
          { val: "neighborhoods::Timbergrove Manor", text: "Timbergrove Manor (Neighborhood)" },
          { val: "neighborhoods::Lazybrook", text: "Lazybrook (Neighborhood)" },
          { val: "neighborhoods::Meyerland", text: "Meyerland (Neighborhood)" },
          { val: "neighborhoods::Old Braeswood", text: "Old Braeswood (Neighborhood)" },
          { val: "neighborhoods::Bellaire", text: "Bellaire (City / Neighborhood)" },
          { val: "platted_subdivisions::West University Place", text: "West University Place (Subdivision)" },
          { val: "neighborhoods::Southgate", text: "Southgate (Neighborhood)" },
          { val: "neighborhoods::Tanglewood", text: "Tanglewood (Neighborhood)" },
          { val: "neighborhoods::Shady Acres", text: "Shady Acres (Neighborhood)" },
          { val: "neighborhoods::Sunset Heights", text: "Sunset Heights (Neighborhood)" },
          { val: "neighborhoods::Brooke Smith", text: "Brooke Smith (Neighborhood)" },
          { val: "neighborhoods::Magnolia Park", text: "Magnolia Park (Neighborhood)" },
          { val: "neighborhoods::Pleasantville", text: "Pleasantville (Neighborhood)" },
          { val: "neighborhoods::Denver Harbor", text: "Denver Harbor (Neighborhood)" },
          { val: "neighborhoods::Kashmere Gardens", text: "Kashmere Gardens (Neighborhood)" },
          { val: "neighborhoods::Acres Homes", text: "Acres Homes (Neighborhood)" },
          { val: "super_neighborhoods::Sunnyside", text: "Sunnyside (Super Neighborhood)" },
          { val: "super_neighborhoods::Independence Heights", text: "Independence Heights (Super Neighborhood)" },
          { val: "neighborhoods::Near Northside", text: "Near Northside (Neighborhood)" },
          { val: "neighborhoods::Third Ward", text: "Third Ward (Neighborhood)" },
          { val: "neighborhoods::Second Ward", text: "Second Ward / Segundo Barrio (Neighborhood)" },
          { val: "neighborhoods::Fourth Ward", text: "Fourth Ward / Freedmen's Town (Neighborhood)" },
          { val: "neighborhoods::Fifth Ward", text: "Fifth Ward / The Nickel (Neighborhood)" },
          { val: "neighborhoods::First Ward", text: "First Ward (Neighborhood)" },
          { val: "neighborhoods::Clear Lake City", text: "Clear Lake City (Neighborhood)" },
          { val: "neighborhoods::Kingwood", text: "Kingwood (Neighborhood)" },
          { val: "platted_subdivisions::Woodland Heights", text: "Woodland Heights (1907 Platted Subdivision)" },
          { val: "platted_subdivisions::Norhill", text: "Norhill (1920 Platted Subdivision)" },
          { val: "platted_subdivisions::East Norhill", text: "East Norhill (1923 Platted Subdivision)" },
          { val: "platted_subdivisions::North Norhill", text: "North Norhill (1924 Platted Subdivision)" },
          { val: "platted_subdivisions::Broadacres", text: "Broadacres (1923 Platted Subdivision)" },
          { val: "platted_subdivisions::Cherokee", text: "Cherokee (1925 Platted Subdivision)" },
          { val: "platted_subdivisions::Edgemont", text: "Edgemont (1924 Platted Subdivision)" },
          { val: "platted_subdivisions::Ormond Place", text: "Ormond Place (1922 Platted Subdivision)" },
          { val: "platted_subdivisions::Woodson Place", text: "Woodson Place (1914 Platted Subdivision)" },
          { val: "platted_subdivisions::Woodland Terrace", text: "Woodland Terrace (1909 Platted Subdivision)" },
        ],
      },
    ];

    const seenVals = new Set();
    sel.innerHTML = featuredGroups
      .map((grp) => {
        const opts = grp.items
          .filter((it) => {
            if (seenVals.has(it.val)) return false;
            seenVals.add(it.val);
            return true;
          })
          .map(
            (it) =>
              `<option value="${it.val.replace(/"/g, "&quot;")}" ${
                it.val === curVal ? "selected" : ""
              }>${it.text}</option>`
          )
          .join("");
        return `<optgroup label="${grp.label}">${opts}</optgroup>`;
      })
      .join("");
  }

  _getExportCacheSlug(layerKey, name) {
    const cleanLayer = String(layerKey || "neighborhoods")
      .toLowerCase()
      .replace(/[^a-z0-9_]+/g, "_");
    const cleanName = String(name || "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_+|_+$/g, "");
    return `${cleanLayer}__${cleanName}`;
  }

  async _ensureCachedExportFootprints(boundarySpec) {
    if (!boundarySpec || typeof boundarySpec !== "object" || !boundarySpec.name) return false;
    if (!this._loadedExportFootprintSlugs) {
      this._loadedExportFootprintSlugs = new Set();
    }
    const slug = this._getExportCacheSlug(
      boundarySpec.layerKey || "neighborhoods",
      boundarySpec.name || boundarySpec.id
    );
    if (this._loadedExportFootprintSlugs.has(slug)) return false;
    this._loadedExportFootprintSlugs.add(slug);

    try {
      const res = await fetch(`public/data/export_cache/${slug}.json`);
      if (!res.ok) return false;
      const data = await res.json();
      if (!Array.isArray(data?.buildings) || data.buildings.length === 0) return false;

      if (this.mapController && Array.isArray(this.mapController.buildingsData)) {
        const getKey = (props) =>
          this.mapController._getCanonicalBuildingKey
            ? this.mapController._getCanonicalBuildingKey(props)
            : String(props?.hcad_num || props?.building_id || props?.id || "").trim();
        const existingKeys = new Set(
          this.mapController.buildingsData.map((f) => getKey(f?.properties)).filter(Boolean)
        );
        let added = 0;
        for (const f of data.buildings) {
          const k = getKey(f?.properties);
          if (k && existingKeys.has(k)) continue;
          if (k) existingKeys.add(k);
          this.mapController.buildingsData.push(f);
          added += 1;
        }
        if (added > 0 && this.mapController.useCanvasFallback) {
          this.mapController._renderCanvas2D();
        }
        return added > 0;
      }
    } catch (_e) {
      // Cache file optional
    }
    return false;
  }

  _renderExportStudioPreview() {
    const stage = document.getElementById("export-svg-preview-stage");
    const statsEl = document.getElementById("export-preview-stats-readout");
    const btnApplyIso = document.getElementById("btn-export-apply-isolation");
    if (!stage || !this.mapController) return;

    const st = this._exportStudioState;
    stage.classList.toggle("transparent-checker", Boolean(st.transparentBg));

    const boundarySpec =
      st.targetSpec === "__viewport__" ? null : st.targetSpec;
    if (boundarySpec) {
      this._ensureCachedExportFootprints(boundarySpec).then((added) => {
        if (added) {
          this._renderExportStudioPreview();
        }
      });
    }
    const extraction = this.mapController.collectVectorFeaturesForExport({
      boundarySpec,
    });
    st.lastExtraction = extraction;

    const titleInput = document.getElementById("input-export-title");
    const subInput = document.getElementById("input-export-subtitle");
    if (titleInput && !st.customTitle) {
      titleInput.placeholder = (extraction.boundaryName || "HOUSTON BUILDING ATLAS").toUpperCase();
    }
    if (subInput && !st.customSubtitle) {
      const yrRange =
        extraction.stats.earliestYear
          ? `EST. ${extraction.stats.earliestYear} • ${extraction.stats.buildingCount.toLocaleString()} STRUCTURES`
          : "HOUSTON, TEXAS";
      subInput.placeholder = yrRange;
    }

    if (statsEl) {
      const s = extraction.stats;
      statsEl.textContent = `${extraction.boundaryName} • ${s.buildingCount.toLocaleString()} footprints${
        s.earliestYear ? ` • Earliest ${s.earliestYear}` : ""
      }${s.landmarkCount ? ` • ${s.landmarkCount} Landmarks` : ""}`;
    }
    if (btnApplyIso) {
      btnApplyIso.style.display = extraction.isIsolatedPolygon ? "inline-flex" : "none";
    }

    const svgMarkup = this._generateExportStudioSvgMarkup(extraction, st);
    st.lastSvgMarkup = svgMarkup;
    stage.innerHTML = svgMarkup;
  }

  _generateExportStudioSvgMarkup(extraction, st) {
    const themes = {
      stencil_white: {
        bg: "#0B0E11",
        borderStroke: "#FFFFFF",
        borderFill: "rgba(255,255,255,0.04)",
        bldFill: "#FFFFFF",
        bldStroke: "#FFFFFF",
        accent: "#FBBF24",
        textPrimary: "#FFFFFF",
        textSecondary: "#CBD5E1",
      },
      stencil_black: {
        bg: "#F8F6F0",
        borderStroke: "#111827",
        borderFill: "rgba(17,24,39,0.03)",
        bldFill: "#111827",
        bldStroke: "#111827",
        accent: "#B45309",
        textPrimary: "#111827",
        textSecondary: "#4B5563",
      },
      ph_emerald: {
        bg: "#0C1610",
        borderStroke: "#95C959",
        borderFill: "rgba(149,201,89,0.06)",
        bldFill: "#E9F6D8",
        bldStroke: "#95C959",
        accent: "#FBBF24",
        textPrimary: "#F4F9EE",
        textSecondary: "#95C959",
      },
      blueprint: {
        bg: "#0A2540",
        borderStroke: "#38BDF8",
        borderFill: "rgba(56,189,248,0.07)",
        bldFill: "#E0F2FE",
        bldStroke: "#7DD3FC",
        accent: "#FDE047",
        textPrimary: "#F0F9FF",
        textSecondary: "#7DD3FC",
      },
      terracotta: {
        bg: "#F5EFE6",
        borderStroke: "#9A3412",
        borderFill: "rgba(154,52,18,0.05)",
        bldFill: "#B45309",
        bldStroke: "#7C2D12",
        accent: "#15803D",
        textPrimary: "#431407",
        textSecondary: "#78350F",
      },
      archival_era: {
        bg: "#0B0E11",
        borderStroke: "#FBBF24",
        borderFill: "rgba(251,191,36,0.04)",
        bldFill: "#E2E8F0",
        bldStroke: "#94A3B8",
        accent: "#95C959",
        textPrimary: "#F8FAFC",
        textSecondary: "#94A3B8",
      },
    };

    const pal = themes[st.theme] || themes.stencil_white;
    const isPoster = st.format === "poster";
    const isCoaster = st.format === "round_coaster";
    const vbW = 1200;
    const vbH = isPoster ? 1500 : 1200;

    const [minLng, minLat, maxLng, maxLat] = extraction.bbox;
    const midLat = (minLat + maxLat) / 2;
    const cosLat = Math.cos((midLat * Math.PI) / 180);
    const geoW = Math.max(0.0002, (maxLng - minLng) * cosLat);
    const geoH = Math.max(0.0002, maxLat - minLat);

    const padTop = isCoaster ? 150 : st.showCaption ? 115 : 85;
    const padBottom = isCoaster
      ? st.showCaption
        ? 265
        : 165
      : st.showCaption
      ? isPoster
        ? 255
        : 175
      : 85;
    const padSide = isCoaster ? 195 : 95;
    const availW = vbW - padSide * 2;
    const availH = vbH - padTop - padBottom;
    const scale = Math.min(availW / geoW, availH / geoH);
    const drawW = geoW * scale;
    const drawH = geoH * scale;
    const offsetX = padSide + (availW - drawW) / 2;
    const offsetY = padTop + (availH - drawH) / 2;

    const proj = (lng, lat) => {
      const x = offsetX + (lng - minLng) * cosLat * scale;
      const y = offsetY + (maxLat - lat) * scale;
      return [x, y];
    };

    const geomToSvgPath = (geom) => {
      if (!geom || !geom.coordinates) return "";
      const polys =
        geom.type === "Polygon"
          ? [geom.coordinates]
          : geom.type === "MultiPolygon"
          ? geom.coordinates
          : [];
      const parts = [];
      for (const poly of polys) {
        for (const ring of poly) {
          if (!Array.isArray(ring) || ring.length < 3) continue;
          for (let i = 0; i < ring.length; i++) {
            const [x, y] = proj(ring[i][0], ring[i][1]);
            parts.push(`${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`);
          }
          parts.push("Z");
        }
      }
      return parts.join(" ");
    };

    const escXml = (str) =>
      String(str || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");

    const useEraColors =
      st.composition === "era_poster" || st.theme === "archival_era";
    const showBuildings = st.composition !== "border_only";
    const showBorder = st.composition !== "footprints_only";

    let buildingsSvg = "";
    if (showBuildings && Array.isArray(extraction.buildings)) {
      const bldPaths = [];
      for (const f of extraction.buildings) {
        const d = geomToSvgPath(f.geometry);
        if (!d) continue;
        const p = f.properties || {};
        const yr = Number(p.year_built) || 0;
        const fillCol = useEraColors ? getYearColorHex(yr, "archival") : pal.bldFill;
        const strokeCol = useEraColors ? fillCol : pal.bldStroke;
        if (st.fillBuildings) {
          bldPaths.push(
            `<path d="${d}" fill="${fillCol}" fill-opacity="${
              useEraColors ? "0.9" : "0.92"
            }" stroke="${strokeCol}" stroke-width="0.6" stroke-linejoin="round" />`
          );
        } else {
          bldPaths.push(
            `<path d="${d}" fill="none" stroke="${strokeCol}" stroke-width="1.35" stroke-linejoin="round" />`
          );
        }
      }
      buildingsSvg = `<g id="building-footprints">${bldPaths.join("\n")}</g>`;
    }

    let borderSvg = "";
    if (showBorder && extraction.boundaryFeature?.geometry) {
      const bdPath = geomToSvgPath(extraction.boundaryFeature.geometry);
      if (bdPath) {
        const bw = Number(st.borderWeight) || 4;
        borderSvg = `
          <g id="boundary-outline">
            <path d="${bdPath}" fill="${
              st.composition === "border_only" && st.fillBuildings
                ? pal.borderFill
                : "none"
            }" stroke="${pal.borderStroke}" stroke-width="${(bw * 2.2).toFixed(
          1
        )}" stroke-opacity="0.18" stroke-linejoin="round" />
            <path d="${bdPath}" fill="none" stroke="${
          pal.borderStroke
        }" stroke-width="${bw.toFixed(1)}" stroke-linejoin="round" />
          </g>
        `;
      }
    }

    let landmarksSvg = "";
    if (st.showLandmarks && showBuildings) {
      const pins = [];
      for (const f of extraction.landmarks || []) {
        const [cx, cy] = proj(f.geometry.coordinates[0], f.geometry.coordinates[1]);
        pins.push(
          `<circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(
            1
          )}" r="5.5" fill="${pal.accent}" stroke="${pal.bg}" stroke-width="1.8" />`
        );
      }
      for (const f of extraction.goodBricks || []) {
        const [cx, cy] = proj(f.geometry.coordinates[0], f.geometry.coordinates[1]);
        pins.push(
          `<circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(
            1
          )}" r="6.2" fill="#95C959" stroke="${pal.bg}" stroke-width="1.8" />`
        );
      }
      if (pins.length > 0) {
        landmarksSvg = `<g id="landmark-accents">${pins.join("\n")}</g>`;
      }
    }

    const titleText = (
      st.customTitle ||
      extraction.boundaryName ||
      "THE HOUSTON BUILDING ATLAS"
    ).toUpperCase();
    const centerLat = ((minLat + maxLat) / 2).toFixed(4);
    const centerLng = Math.abs((minLng + maxLng) / 2).toFixed(4);
    const defaultSub = extraction.stats.earliestYear
      ? `HOUSTON, TX • EST. ${extraction.stats.earliestYear} • ${extraction.stats.buildingCount.toLocaleString()} STRUCTURES • ${centerLat}°N ${centerLng}°W`
      : `HOUSTON, TEXAS • ${centerLat}°N ${centerLng}°W`;
    const subtitleText = (st.customSubtitle || defaultSub).toUpperCase();

    let frameSvg = "";
    if (isCoaster) {
      frameSvg = `
        <g id="coaster-medallion-rings">
          <circle cx="600" cy="600" r="560" fill="none" stroke="${pal.borderStroke}" stroke-width="8" />
          <circle cx="600" cy="600" r="542" fill="none" stroke="${pal.borderStroke}" stroke-width="2" stroke-dasharray="8 6" stroke-opacity="0.65" />
        </g>
      `;
    } else if (isPoster) {
      frameSvg = `
        <rect x="36" y="36" width="${vbW - 72}" height="${
        vbH - 72
      }" fill="none" stroke="${pal.borderStroke}" stroke-width="2.5" stroke-opacity="0.55" rx="8" />
      `;
    }

    let captionSvg = "";
    if (st.showCaption) {
      const titleY = isCoaster ? 985 : vbH - (isPoster ? 130 : 88);
      const subY = titleY + 34;
      const fontSize = titleText.length > 26 ? 28 : 34;
      captionSvg = `
        <g id="cartographic-typography" text-anchor="middle">
          <text x="${vbW / 2}" y="${titleY}" fill="${
        pal.textPrimary
      }" font-family="'Fraunces', 'Georgia', serif" font-size="${fontSize}" font-weight="700" letter-spacing="3">${escXml(
        titleText
      )}</text>
          <text x="${vbW / 2}" y="${subY}" fill="${
        pal.textSecondary
      }" font-family="'JetBrains Mono', 'Courier New', monospace" font-size="14" font-weight="600" letter-spacing="2.2">${escXml(
        subtitleText
      )}</text>
        </g>
      `;
    }

    const bgSvg = st.transparentBg
      ? ""
      : isCoaster
      ? `<circle cx="600" cy="600" r="572" fill="${pal.bg}" />`
      : `<rect x="0" y="0" width="${vbW}" height="${vbH}" fill="${pal.bg}" rx="16" />`;

    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${vbW} ${vbH}" width="${vbW}" height="${vbH}">
      ${bgSvg}
      ${frameSvg}
      ${buildingsSvg}
      ${borderSvg}
      ${landmarksSvg}
      ${captionSvg}
    </svg>`;
  }

  _slugifyExportName(name) {
    return (
      String(name || "houston_atlas")
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "_")
        .replace(/^_+|_+$/g, "") || "houston_atlas"
    );
  }

  downloadExportSvg() {
    const st = this._exportStudioState;
    if (!st.lastSvgMarkup) {
      this._renderExportStudioPreview();
    }
    const slug = this._slugifyExportName(
      st.customTitle || st.lastExtraction?.boundaryName || "houston_atlas"
    );
    const blob = new Blob([st.lastSvgMarkup], {
      type: "image/svg+xml;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${slug}_${st.composition}.svg`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  downloadExportPng() {
    const st = this._exportStudioState;
    if (!st.lastSvgMarkup) {
      this._renderExportStudioPreview();
    }
    const isPoster = st.format === "poster";
    const outW = 3000;
    const outH = isPoster ? 3750 : 3000;
    const slug = this._slugifyExportName(
      st.customTitle || st.lastExtraction?.boundaryName || "houston_atlas"
    );

    const svgBlob = new Blob([st.lastSvgMarkup], {
      type: "image/svg+xml;charset=utf-8",
    });
    const url = URL.createObjectURL(svgBlob);
    const img = new Image();
    img.onload = () => {
      const canvas = document.createElement("canvas");
      canvas.width = outW;
      canvas.height = outH;
      const ctx = canvas.getContext("2d");
      ctx.drawImage(img, 0, 0, outW, outH);
      URL.revokeObjectURL(url);
      const pngUrl = canvas.toDataURL("image/png");
      const a = document.createElement("a");
      a.href = pngUrl;
      a.download = `${slug}_${st.composition}_3000px.png`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
    };
    img.src = url;
  }

  downloadLiveMapScreenshot() {
    if (!this.mapController) return;
    const dataUrl = this.mapController.captureMapScreenshotDataUrl();
    if (!dataUrl) return;
    const iso = this.mapController.getIsolatedBoundary();
    const slug = this._slugifyExportName(iso?.name || "houston_building_atlas_map");
    const a = document.createElement("a");
    a.href = dataUrl;
    a.download = `${slug}_screenshot.png`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  downloadExportGeoJson() {
    const ext = this._exportStudioState.lastExtraction;
    if (!ext) return;
    const features = [];
    if (ext.boundaryFeature) {
      features.push({
        type: "Feature",
        properties: {
          ...(ext.boundaryFeature.properties || {}),
          export_role: "isolated_boundary",
          boundary_layer: ext.boundaryLayerKey,
        },
        geometry: ext.boundaryFeature.geometry,
      });
    }
    for (const f of ext.buildings || []) {
      features.push({
        type: "Feature",
        properties: {
          ...(f.properties || {}),
          export_role: "building_footprint",
        },
        geometry: f.geometry,
      });
    }
    const fc = {
      type: "FeatureCollection",
      name: ext.boundaryName || "Houston Building Atlas Export",
      features,
    };
    const slug = this._slugifyExportName(ext.boundaryName || "houston_atlas");
    const blob = new Blob([JSON.stringify(fc, null, 2)], {
      type: "application/geo+json;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${slug}_isolated.geojson`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  _applyExportStudioHashParams(hashStr = "", searchStr = "") {
    const raw =
      hashStr && hashStr.length > 1
        ? hashStr.replace(/^#/, "")
        : searchStr && searchStr.length > 1
        ? searchStr.replace(/^\?/, "")
        : "";
    if (!raw) return;
    const params = new URLSearchParams(raw);
    const expOpen = params.get("exp");
    if (!expOpen || expOpen === "0" || expOpen === "false") return;

    const st = this._exportStudioState;
    const comp = params.get("expComp");
    if (
      comp &&
      ["figure_ground", "footprints_only", "border_only", "era_poster"].includes(comp)
    ) {
      st.composition = comp;
    }
    const theme = params.get("expTheme");
    if (
      theme &&
      [
        "stencil_white",
        "stencil_black",
        "ph_emerald",
        "blueprint",
        "terracotta",
        "archival_era",
      ].includes(theme)
    ) {
      st.theme = theme;
    }
    const fmt = params.get("expFmt");
    if (fmt && ["square", "round_coaster", "poster"].includes(fmt)) {
      st.format = fmt;
    }
    if (params.has("expFill")) {
      st.fillBuildings = params.get("expFill") !== "0" && params.get("expFill") !== "false";
    }
    if (params.has("expTrans")) {
      st.transparentBg = params.get("expTrans") === "1" || params.get("expTrans") === "true";
    }
    if (params.has("expLm")) {
      st.showLandmarks = params.get("expLm") !== "0" && params.get("expLm") !== "false";
    }
    if (params.has("expCap")) {
      st.showCaption = params.get("expCap") !== "0" && params.get("expCap") !== "false";
    }
    if (params.has("expBw")) {
      const bw = parseFloat(params.get("expBw"));
      if (Number.isFinite(bw) && bw >= 0.5 && bw <= 16) {
        st.borderWeight = bw;
      }
    }
    const hasCustomText = params.has("expTitle") || params.has("expSub");
    if (params.has("expTitle")) {
      st.customTitle = params.get("expTitle") || "";
    }
    if (params.has("expSub")) {
      st.customSubtitle = params.get("expSub") || "";
    }

    const chkTransparent = document.getElementById("chk-export-transparent");
    if (chkTransparent) chkTransparent.checked = Boolean(st.transparentBg);
    const chkFillBld = document.getElementById("chk-export-fill-buildings");
    if (chkFillBld) chkFillBld.checked = Boolean(st.fillBuildings);
    const chkLandmarks = document.getElementById("chk-export-show-landmarks");
    if (chkLandmarks) chkLandmarks.checked = Boolean(st.showLandmarks);
    const chkCaption = document.getElementById("chk-export-show-caption");
    if (chkCaption) chkCaption.checked = Boolean(st.showCaption);
    const sliderWeight = document.getElementById("slider-export-border-weight");
    if (sliderWeight) sliderWeight.value = String(st.borderWeight);
    const readout = document.getElementById("export-border-weight-readout");
    if (readout) readout.textContent = `${Number(st.borderWeight).toFixed(1)}px`;
    const inputTitle = document.getElementById("input-export-title");
    if (inputTitle) inputTitle.value = st.customTitle || "";
    const inputSubtitle = document.getElementById("input-export-subtitle");
    if (inputSubtitle) inputSubtitle.value = st.customSubtitle || "";

    const iso =
      this.mapController?.getIsolatedBoundary() ||
      this.filterStore.getState()?.isolatedBoundary;
    this.openExportStudioModal(
      iso && iso.name
        ? { layerKey: iso.layerKey, id: iso.id || iso.name, name: iso.name }
        : null,
      { preserveCustomText: hasCustomText }
    );
  }

  _updateUrlHash(state) {
    if (this._suppressUrlUpdate) return;
    const vp = this.mapController
      ? this.mapController.getCurrentViewport()
      : this.lastViewportStats
      ? this.lastViewportStats.viewport
      : null;
    const selProps = this.mapController ? this.mapController.selectedFeatureProps : null;
    const selectedHcad = selProps?.hcad_num ? String(selProps.hcad_num).trim() : "";
    const selectedFeatureId = !selectedHcad && selProps?.id ? String(selProps.id).trim() : "";

    let hash = serializeStateToHash(state, vp, {
      includeViewport: true,
      selectedHcad,
      selectedFeatureId,
    });
    const expModal = document.getElementById("export-studio-modal");
    if (expModal && !expModal.classList.contains("hidden")) {
      const st = this._exportStudioState;
      const expParams = new URLSearchParams();
      expParams.set("exp", "1");
      expParams.set("expComp", st.composition || "figure_ground");
      expParams.set("expTheme", st.theme || "stencil_white");
      expParams.set("expFmt", st.format || "square");
      expParams.set("expFill", st.fillBuildings ? "1" : "0");
      if (st.transparentBg) expParams.set("expTrans", "1");
      expParams.set("expLm", st.showLandmarks ? "1" : "0");
      expParams.set("expCap", st.showCaption ? "1" : "0");
      expParams.set("expBw", String(st.borderWeight || 4));
      hash = hash ? `${hash}&${expParams.toString()}` : expParams.toString();
    }
    const basePath = window.location.pathname;
    if (hash) {
      window.history.replaceState(null, "", `${basePath}#${hash}`);
    } else {
      window.history.replaceState(null, "", basePath);
    }
  }
}

window.addEventListener("DOMContentLoaded", () => {
  const app = new HoustonAtlasApp();
  window.atlasApp = app;
  app.start();
});
