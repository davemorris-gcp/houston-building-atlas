/**
 * Main application UI controller for The Houston Building Atlas v2 (Preservation Houston).
 */

import {
  CURATED_TOURS,
  getLegendItems,
  getYearColorHex,
} from "./palettes.js?v=20261007f";
import {
  buildShareableUrl,
  createFilterStore,
  parseHashToState,
  serializeStateToHash,
  SHARE_VIEW_PRESETS,
} from "./filterStore.js?v=20261007f";
import { AtlasMapController } from "./mapController.js?v=20261008t";
import { fetchHcadDeepLink, fetchHcadLiveRecord } from "./hcadLink.js?v=20261008t";
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
} from "./curatedEdits.js?v=20261008t";
import {
  buildStreetViewUrl,
  hideBuildingPhoto,
  loadCuratedPhotosIndex,
  registerSessionPhoto,
  resolveBuildingPhotos,
} from "./photoService.js?v=20261007f";

class HoustonAtlasApp {
  constructor() {
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
    this.lastViewportStats = null;
    this.timelapseTimer = null;
    this._suppressUrlUpdate = false;
    this._activePhotoState = null;
    this._activeTour = null;
    this._activeTourStopIndex = -1;

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
      const [searchRes, statsRes, haifRes] = await Promise.all([
        fetch("public/data/search_index.json?v=20261008r"),
        fetch("public/data/stats_summary.json?v=20261008r"),
        fetch("public/data/haif_index.json?v=20261008r").catch(() => null),
      ]);
      this.searchIndex = await searchRes.json();
      this.globalStats = await statsRes.json();
      if (haifRes && haifRes.ok) {
        this.haifIndex = await haifRes.json();
      } else {
        this.haifIndex = null;
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

    // 2. Runtime lookup in overlaysData.landmarks & good_brick_awards
    if (!isSubBuilding && this.mapController?.overlaysData) {
      const lmFeatures = this.mapController.overlaysData.landmarks?.features || [];
      for (const f of lmFeatures) {
        const lp = f.properties || {};
        const lHcad = String(lp.hcad_num || "").trim();
        const lNormAddr = this._normalizeAddressForHaifLookup(lp.address);
        if ((hcad && lHcad === hcad) || (normAddr && lNormAddr && normAddr === lNormAddr)) {
          addPrimary(lp.building_name || lp.name, lp.name_source || "COH Landmark Designation Report");
          addAltList(lp.alt_names);
          break;
        }
      }
      const gbFeatures = this.mapController.overlaysData.good_brick_awards?.features || [];
      for (const f of gbFeatures) {
        const gp = f.properties || {};
        const gHcad = String(gp.hcad_num || "").trim();
        const gNormAddr = this._normalizeAddressForHaifLookup(gp.address);
        if ((hcad && gHcad === hcad) || (normAddr && gNormAddr && normAddr === gNormAddr)) {
          addPrimary(gp.building_name || gp.landmark_name || gp.name, gp.name_source || "Preservation Houston Good Brick Award");
          addAltList(gp.alt_names);
          break;
        }
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
        sublabel: `${akaSnippet}${addrSnippet}${ov.historic_district || existingItem?.historic_district || mergedUseCategory || "Harris County"} • Built ${ov.year_built} (✓ PH Verified)`,
        category: `Built ${ov.year_built} ✓`,
        year_built: ov.year_built,
        architect: ov.architect || existingItem?.architect || "",
        style: ov.style || existingItem?.style || "",
        bld_style: mergedBldStyle,
        hcad_grade: ov.hcad_grade || existingItem?.hcad_grade || "",
        use_category: mergedUseCategory,
        landuse_desc: ov.landuse_desc || existingItem?.landuse_desc || "",
        historic_district: ov.historic_district || existingItem?.historic_district || "",
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
      if (item.hcad_num) byHcad.set(String(item.hcad_num).trim(), item);
      if (item.label) byLabel.set(String(item.label).trim().toLowerCase(), item);
    }

    // 1. Enrich from buildingsData (architect, bld_style, style, use_category, landuse_desc, historic_district, good_brick_years, building_name, alt_names, address, alt_addresses, and geometry coords)
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
            p.historic_district,
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
        const checked = e.target.checked;
        this.filterStore.setState({
          syncAnnexationToTime: checked,
          layers: {
            ...this.filterStore.getState().layers,
            annexations: checked ? true : this.filterStore.getState().layers.annexations,
          },
        });
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
      ["chk-layer-thc-markers", "thcMarkers"],
      ["chk-layer-annexations", "annexations"],
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
      searchInput.addEventListener("input", (e) => {
        this._runSearchQuery(e.target.value, "");
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

        if (!hasYearChange && !hasNameUpdate && !hasFootprintReport && !hasCitation && !hasPhoto) {
          if (feedbackEl) {
            feedbackEl.innerHTML = `<strong>Please enter a Corrected Year Built, Building Name / Alias, select a Building Footprint Shape / Orientation Issue, or provide historical notes before submitting.</strong>`;
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
          const summaryDesc = hasFootprintReport && hasYearChange
            ? `Year Built <strong>${payload.suggested_year_built}</strong> + Footprint Report (<em>${payload.footprint_issue || "Shape/Orientation"}</em>)`
            : hasFootprintReport
            ? `Footprint Shape / Orientation Report (<em>${payload.footprint_issue || "Geometry Issue"}</em>)`
            : `Built <strong>${payload.suggested_year_built || payload.current_year_built || "Updated"}</strong>, source: <em>${payload.source_type}</em>`;

          feedbackEl.innerHTML = `<strong>&#10003; Thank you!</strong> Suggestion for <strong>${payload.building_name || payload.address || payload.hcad_num}</strong> (${summaryDesc}) has been recorded with status <code>Pending</code>. ${deliveryNote}${photoNote}`;
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
    const isArchitectFilter =
      hdrLow.startsWith("architect:") || hdrLow.startsWith("architect / builder:");
    const isDistrictFilter = hdrLow.startsWith("district:");
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

    const scoreCandidate = (item, isViewportCandidate = false) => {
      const lbl = String(item.label || "").toLowerCase();
      const bldName = String(item.building_name || "").toLowerCase();
      const altNames = Array.isArray(item.alt_names)
        ? item.alt_names.map((s) => String(s).toLowerCase())
        : [];
      const addr = String(item.address || "").toLowerCase();
      const altAddrs = Array.isArray(item.alt_addresses)
        ? item.alt_addresses.map((s) => String(s).toLowerCase())
        : [];
      const sub = String(item.sublabel || "").toLowerCase();
      const hcad = String(item.hcad_num || "").toLowerCase();
      const arch = String(item.architect || "").toLowerCase();
      const style = String(item.style || "").toLowerCase();
      const bldStyle = String(item.bld_style || "").toLowerCase();
      const useCat = String(item.use_category || "").toLowerCase();
      const landuse = String(item.landuse_desc || "").toLowerCase();
      const cat = String(item.category || "").toLowerCase();
      const grade = String(item.hcad_grade || "").toLowerCase();
      const gbYrs = String(item.good_brick_years || "").toLowerCase();
      const dist = String(item.historic_district || "").toLowerCase();
      const lmCode = String(item.landmark_code || "").toLowerCase();

      if (targetGrade) {
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

      let score = 0;

      if (isArchitectFilter) {
        if (arch && arch.includes(q)) {
          score = arch === q ? 120 : 105;
        }
      } else if (isDistrictFilter) {
        if (dist && dist.includes(q)) {
          score = dist === q ? 120 : 105;
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
        if (style === q || bldStyle === q) {
          score = Math.max(score, 118);
        } else if (
          (style &&
            style !== "historical / architectural structure" &&
            (style.includes(q) || (style.length > 3 && q.includes(style)))) ||
          (bldStyle &&
            bldStyle !== "historical / architectural structure" &&
            (bldStyle.includes(q) || (bldStyle.length > 3 && q.includes(bldStyle))))
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
        if (score < 85 && qTokens.length >= 2) {
          const combinedHaystack = `${addr} ${altAddrs.join(" ")} ${lbl} ${bldName} ${altNames.join(" ")} ${sub}`;
          if (qTokens.every((tok) => combinedHaystack.includes(tok))) {
            score = Math.max(score, 86);
          }
        }
      }

      if (score === 0) return 0;

      // Boost nearby viewport buildings and notable landmarks / named structures
      if (isViewportCandidate) score += 14;
      const selHcad = String(this.selectedProperties?.hcad_num || "").trim().toLowerCase();
      const selId = String(
        this.selectedProperties?.id || this.selectedProperties?.building_id || ""
      )
        .trim()
        .toLowerCase();
      const itemId = String(item.id || "").trim().toLowerCase();
      if ((selId && itemId === selId) || (selHcad && hcad === selHcad)) {
        score += 4;
      }
      if (item.type === "landmark" || item.type === "good_brick" || item.landmark_code || item.good_brick_years) {
        score += 8;
      }
      const hasCustomName =
        item.label &&
        !/^\d+\s+/.test(String(item.label).trim()) &&
        !String(item.label).startsWith("HCAD ");
      if (hasCustomName) score += 5;

      return score;
    };

    const scoredMatches = [];
    const seenKeys = new Set();

    // First: Check all rendered & in-memory buildings in overridesFC + buildings.geojson
    const liveCandidates = this.mapController?.getRenderedBuildingCandidates
      ? this.mapController.getRenderedBuildingCandidates(20000)
      : [];
    for (const cand of liveCandidates) {
      const p = cand.props || {};
      const key = String(p.id || p.building_id || p.hcad_num || "").trim();
      if (key && seenKeys.has(key)) continue;

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
      const synthItem = {
        type: "building",
        id: p.id || p.building_id || "",
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
          distClean || luSummary || "Harris County",
          luChips.length > 1 && distClean ? luSummary : "",
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
      const key = String(item.id || item.hcad_num || item.label || "").trim();
      const hcadKey = String(item.hcad_num || "").trim();
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
      searchResults.innerHTML = `<div class="search-empty">No matching addresses, architects, styles, landmarks, or districts found for "${rawQuery}".</div>`;
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
          <span>&#128269; Matching Structures (${countSummary})</span>
          <button type="button" class="search-filter-clear" id="btn-clear-search-filter">Clear</button>
        </div>`
      : "";

    searchResults.innerHTML =
      headerBanner +
      matches
        .map(
          (m, i) => `
          <button type="button" class="search-result-item" data-idx="${i}">
            <div class="search-result-main">
              <span class="search-result-title">${m.label}</span>
              <span class="search-result-cat">${m.category}</span>
            </div>
            <div class="search-result-sub">${m.sublabel || ""}</div>
          </button>`
        )
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
          if (searchInput) searchInput.value = chosen.label;
          if (typeof window !== "undefined" && window.innerWidth <= 900) {
            this._setSidebarCollapsed(true);
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
              historic_district: chosen.historic_district || chosen.sublabel,
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

  openCorrectionModal(props) {
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

    if (btnCloseBanner) {
      btnCloseBanner.addEventListener("click", () => {
        const banner = document.getElementById("tour-narrative-banner");
        if (banner) banner.classList.add("hidden");
        this._activeTour = null;
        this._activeTourStopIndex = -1;
        this.mapController.setTourRoute(null, -1);
        container.querySelectorAll(".tour-pill").forEach((b) => b.classList.remove("active"));
      });
    }
  }

  _activateTourOverview(tour) {
    this._activeTour = tour;
    this._activeTourStopIndex = -1;

    // Hide the right-hand Property Inspector drawer & clear any previously selected irrelevant property
    const inspectorDrawer = document.getElementById("inspector-drawer");
    if (inspectorDrawer) {
      inspectorDrawer.classList.add("hidden");
    }
    this.mapController.clearSelection();
    this.mapController.setTourRoute(tour, -1);

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
    const idx = Math.max(0, Math.min(stops.length - 1, stopIdx));
    const stop = stops[idx];
    this._activeTour = tour;
    this._activeTourStopIndex = idx;

    this.mapController.setTourRoute(tour, idx);

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
      "chk-layer-annexations": state.layers.annexations,
      "chk-layer-historic-map": state.layers.historicMap,
    };
    for (const [id, checked] of Object.entries(mapLayerIds)) {
      const el = document.getElementById(id);
      if (el) el.checked = Boolean(checked);
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

  renderInspectorDrawer(rawProps) {
    const drawer = document.getElementById("inspector-drawer");
    const content = document.getElementById("inspector-body");
    if (!drawer || !content || !rawProps) return;

    if (typeof window !== "undefined" && window.innerWidth <= 900) {
      this._setSidebarCollapsed(true);
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

    content.innerHTML = `
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
          <span class="cell-value">
            ${
              isRealDistrict
                ? `<button type="button" class="inspector-filter-chip" data-filter-chip="${distVal}" data-filter-label="District: ${distVal}" title="Click to search all structures in ${distVal}">${distVal} &#128269;</button>`
                : distVal || "Outside City District"
            }
          </span>
        </div>
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
          <span class="cell-label">Subdivision / Legal Description</span>
          <span class="cell-value" id="inspector-cell-subdivision">${props.subdivision || "Not listed"}</span>
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
          if (subCell && (rec.subdivision || rec.legalDescription) && (!props.subdivision || subCell.textContent === "Not listed")) {
            subCell.textContent = [rec.subdivision, rec.legalDescription].filter(Boolean).join(" — ");
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

    const hash = serializeStateToHash(state, vp, {
      includeViewport: true,
      selectedHcad,
      selectedFeatureId,
    });
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
