/**
 * Main application UI controller for The Houston Building Atlas v2 (Preservation Houston).
 */

import {
  CURATED_TOURS,
  getLegendItems,
  getYearColorHex,
  HISTORICAL_MAP_CATALOG,
  SANBORN_MATERIAL_ITEMS,
} from "./palettes.js?v=20261029a";
import {
  buildShareableUrl,
  createFilterStore,
  HISTORIC_WARD_ERAS,
  normalizeComparableAddress,
  parseHashToState,
  resolveActiveAnnexationDecade,
  resolveActiveWardEra,
  serializeStateToHash,
  SHARE_VIEW_PRESETS,
} from "./filterStore.js?v=20261029a";
import { AtlasMapController } from "./mapController.js?v=20261029a";
import {
  buildPortfolioDataset,
  CITY_HISTORY_CHAPTERS,
  FEATURED_PORTFOLIO_PRESETS,
} from "./guidedChaptersAndPortfolios.js?v=20261029a";
import { fetchHcadDeepLink, fetchHcadLiveRecord } from "./hcadLink.js?v=20261029a";
import {
  buildResearchKitDeedChainView,
  formatClerkPartyTokens,
  parseClerkLegalTokens,
} from "./clerkSearchHelpers.js?v=20261029a";
import {
  applyOverrideToProperties,
  authenticateAdminSession,
  classifyFieldEvidentiaryTier,
  clearAdminSession,
  DEMOLISHED_REPLACED_ISSUE,
  DEMOLISHED_VACANT_ISSUE,
  formatSuggestionsAsCsv,
  getAdminSession,
  getLocalPendingSuggestions,
  isClaimSuppressed,
  isDemolishedVacantOverrideInput,
  isEntitySuppressedByOverrides,
  MAX_VALID_YEAR,
  mergeOverrideRecord,
  MIN_VALID_YEAR,
  saveGoogleSheetEndpoints,
  submitAdminApprovedOverride,
  submitCorrectionSuggestion,
  submitQuickFlagReport,
} from "./curatedEdits.js?v=20261029a";
import {
  buildStreetViewUrl,
  clearRegisteredPhotoExclusions,
  hideBuildingPhoto,
  loadCuratedPhotosIndex,
  registerGloballyRejectedPhotos,
  registerKnownLandmarksForPhotoExclusion,
  registerSessionPhoto,
  resolveBuildingPhotos,
} from "./photoService.js?v=20261029a";
import {
  buildDemolitionBenchmarksTablesHtml,
  DEFAULT_ANNUAL_DEMOLITION_BENCHMARKS,
  formatDemolitionBenchmarksGrantText,
  resolveAnnualDemolitionBenchmarks,
} from "./platMonitorHelpers.js?v=20261029a";

const DEFAULT_FOOTPRINT_NOTES_PLACEHOLDER =
  "Describe what looks wrong compared to the actual building or satellite view (e.g. 'Polygon is rotated ~30 degrees off the street grid' or 'Only the rear garage is outlined, missing the L-shaped main house fronting the street').";
const VACANT_FOOTPRINT_NOTES_PLACEHOLDER =
  "Optional: note approximate demolition timeframe or current lot condition (e.g. 'House demolished; lot currently grass / vacant').";

export class HoustonAtlasApp {
  constructor() {
    this._hadInitialUrlParams = Boolean(
      (window.location.hash && window.location.hash.length > 1) ||
        (window.location.search && window.location.search.length > 1)
    );
    const {
      patch,
      viewport,
      selection,
      collapseSidebar,
      activeChapterId,
      activePortfolio,
    } = parseHashToState(window.location.hash, window.location.search);
    this.initialViewport = viewport;
    this.initialSelection = selection;
    this.initialCollapseSidebar = collapseSidebar;
    this.initialChapterId = activeChapterId || "";
    this.initialPortfolio = activePortfolio || null;
    this.filterStore = createFilterStore(patch);
    this.searchIndex = [];
    this.globalStats = null;
    this.deedCatalog = null;
    this.sanbornCatalog = null;
    this._permitShardCache = new Map();
    this.lastViewportStats = null;
    this.timelapseTimer = null;
    this._suppressUrlUpdate = false;
    this._isRestoringHistory = false;
    this._started = false;
    this._lastHistorySignature = null;
    this._lastHandledUrlKey = "";
    this._activePhotoState = null;
    this._activeTour = null;
    this._activeTourStopIndex = -1;
    this._preTourSnapshot = null;
    this._activeChapterId = "";
    this._preChapterSnapshot = null;
    this._activePortfolio = null;
    this._prePortfolioSnapshot = null;
    this._hahcModalState = {
      district: "all",
      category: "all",
      search: "",
    };
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
      onSelectPortfolioWork: (workIdx) => {
        this._selectPortfolioWorkByIndex(workIdx);
      },
      onOverlapStackChange: (stack, activeIdx) =>
        this._renderMapOverlapBar(stack, activeIdx),
      onIsolationChange: (iso) => this._renderIsolationBanner(iso),
    });
  }

  async start() {
    this._bindControls();
    this._renderTourPills();
    this._populatePortfolioPresetSelect();
    this._renderLegend();
    this._syncControlsFromState(this.filterStore.getState());
    this._syncPermitWatchCounts();

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

    this._syncPhotoExclusionRegistries();
    this._syncPermitWatchCounts();
    this._syncHahcCounts();
    this._mergeCuratedOverridesIntoSearchIndex();
    this._enrichSearchIndexWithOverlayMetadata();

    if (this.initialChapterId) {
      this.activateCityChapter(this.initialChapterId, {
        fitViewport: !this.initialViewport,
        skipHistoryPush: true,
      });
    } else if (this.initialPortfolio && this.initialPortfolio.query) {
      this.activatePortfolioMode(
        this.initialPortfolio.kind || "architect",
        this.initialPortfolio.query,
        {
          fitBounds: !this.initialViewport,
          skipHistoryPush: true,
        }
      );
    }

    if (this.initialSelection) {
      if (this.initialSelection.boundaryId) {
        const initBLayer = String(this.initialSelection.boundaryLayer || "");
        const initBId = String(this.initialSelection.boundaryId || "");
        if (
          initBLayer === "red_book_1915" ||
          initBLayer === "redBook1915" ||
          initBId.startsWith("rb1915_")
        ) {
          this.filterStore.setLayerVisibility("redBook1915", true);
          this.mapController.inspectRedBookById(initBId, {
            flyTo: !this.initialViewport,
          });
        } else if (
          initBLayer === "hahc_coa_filings" ||
          initBLayer === "hahcTracker" ||
          initBLayer === "hahc_coa" ||
          initBId.startsWith("hahc_")
        ) {
          this.filterStore.setLayerVisibility("hahcTracker", true);
          this.mapController.inspectHahcById(initBId, {
            flyTo: !this.initialViewport,
          });
        } else if (
          initBLayer === "nrhp_properties" ||
          initBLayer === "nrhpProperties" ||
          initBId.startsWith("nrhp_")
        ) {
          this.filterStore.setLayerVisibility("nrhpProperties", true);
          this.mapController.inspectNrhpById(initBId, {
            flyTo: !this.initialViewport,
          });
        } else {
          const normInitLayer =
            initBLayer === "holc1937" || initBId.startsWith("holc_1937_")
              ? "holc_1937_zones"
              : initBLayer === "plattedSubdivisions" || initBId.startsWith("plat_")
              ? "platted_subdivisions"
              : initBLayer || "neighborhoods";
          if (normInitLayer === "holc_1937_zones") {
            this.filterStore.setLayerVisibility("holc1937", true);
          } else if (normInitLayer === "platted_subdivisions") {
            this.filterStore.setLayerVisibility("plattedSubdivisions", true);
          }
          this.mapController.highlightBoundaryByIdOrName({
            layerKey: normInitLayer,
            id: initBId,
            name: initBId,
            flyTo: !this.initialViewport,
            inspect: true,
          });
        }
      } else {
        await this._resolveAndSelectDeepLinkedProperty(
          this.initialSelection,
          this.initialViewport,
          false
        );
      }
    }

    this._applyExportStudioHashParams(window.location.hash, window.location.search);
    this._lastHistorySignature = this._computeHistorySignature(this.filterStore.getState());
    this._lastHandledUrlKey = `${window.location.pathname}${window.location.search}${window.location.hash}`;

    this.filterStore.subscribe((state) => {
      this._syncControlsFromState(state);
      this._renderLegend();
      this._manageTimelapseLoop(state);
      this._updateUrlHash(state);
      this._refreshShareModalContent();
    });

    const handleUrlChange = async () => {
      const urlKey = `${window.location.pathname}${window.location.search}${window.location.hash}`;
      if (urlKey === this._lastHandledUrlKey) return;
      this._lastHandledUrlKey = urlKey;

      const {
        patch,
        viewport,
        selection,
        collapseSidebar,
        activeChapterId,
        activePortfolio,
      } = parseHashToState(window.location.hash, window.location.search);
      this._isRestoringHistory = true;
      this._suppressUrlUpdate = true;
      try {
        if (!patch.isolatedBoundary && this.filterStore.getState().isolatedBoundary) {
          patch.isolatedBoundary = null;
          this.mapController.clearIsolatedBoundary();
        }
        if (Object.keys(patch).length > 0) {
          this.filterStore.setState(patch);
        }
        if (collapseSidebar !== null) {
          this._setSidebarCollapsed(collapseSidebar);
        }

        if (activeChapterId && activeChapterId !== this._activeChapterId) {
          this.activateCityChapter(activeChapterId, {
            fitViewport: !viewport,
            skipHistoryPush: true,
          });
        } else if (!activeChapterId && this._activeChapterId) {
          this.exitCityChapters({ restoreState: false, skipUrlUpdate: true });
        }

        if (
          activePortfolio &&
          activePortfolio.query &&
          (!this._activePortfolio ||
            this._activePortfolio.kind !== activePortfolio.kind ||
            this._activePortfolio.query !== activePortfolio.query)
        ) {
          this.activatePortfolioMode(
            activePortfolio.kind || "architect",
            activePortfolio.query,
            {
              fitBounds: !viewport,
              skipHistoryPush: true,
            }
          );
        } else if (!activePortfolio && this._activePortfolio) {
          this.exitPortfolioMode({ restoreState: false, skipUrlUpdate: true });
        }

        const curVp = this.mapController ? this.mapController.getCurrentViewport() : null;
        const vpChangedSignificantly =
          viewport &&
          (!curVp ||
            Math.abs(curVp.lat - viewport.lat) > 0.00018 ||
            Math.abs(curVp.lng - viewport.lng) > 0.00018 ||
            Math.abs(curVp.zoom - viewport.zoom) > 0.04 ||
            Math.abs((curVp.pitch || 0) - (viewport.pitch || 0)) > 1);

        if (selection?.hcadNum || selection?.featureId || selection?.query) {
          this.mapController.clearHighlightedBoundary();
          await this._resolveAndSelectDeepLinkedProperty(
            selection,
            viewport,
            Boolean(vpChangedSignificantly)
          );
        } else if (selection?.boundaryId) {
          if (vpChangedSignificantly) {
            this.mapController.flyToLocation({
              lng: viewport.lng,
              lat: viewport.lat,
              zoom: viewport.zoom,
              pitch: viewport.pitch,
            });
          }
          const selBLayer = String(selection.boundaryLayer || "");
          const selBId = String(selection.boundaryId || "");
          if (
            selBLayer === "red_book_1915" ||
            selBLayer === "redBook1915" ||
            selBId.startsWith("rb1915_")
          ) {
            this.filterStore.setLayerVisibility("redBook1915", true);
            this.mapController.inspectRedBookById(selBId, {
              flyTo: !viewport,
            });
          } else if (
            selBLayer === "hahc_coa_filings" ||
            selBLayer === "hahcTracker" ||
            selBLayer === "hahc_coa" ||
            selBId.startsWith("hahc_")
          ) {
            this.filterStore.setLayerVisibility("hahcTracker", true);
            this.mapController.inspectHahcById(selBId, {
              flyTo: !viewport,
            });
          } else if (
            selBLayer === "nrhp_properties" ||
            selBLayer === "nrhpProperties" ||
            selBId.startsWith("nrhp_")
          ) {
            this.filterStore.setLayerVisibility("nrhpProperties", true);
            this.mapController.inspectNrhpById(selBId, {
              flyTo: !viewport,
            });
          } else {
            const normSelLayer =
              selBLayer === "holc1937" || selBId.startsWith("holc_1937_")
                ? "holc_1937_zones"
                : selBLayer === "plattedSubdivisions" || selBId.startsWith("plat_")
                ? "platted_subdivisions"
                : selBLayer || "neighborhoods";
            if (normSelLayer === "holc_1937_zones") {
              this.filterStore.setLayerVisibility("holc1937", true);
            } else if (normSelLayer === "platted_subdivisions") {
              this.filterStore.setLayerVisibility("plattedSubdivisions", true);
            }
            this.mapController.highlightBoundaryByIdOrName({
              layerKey: normSelLayer,
              id: selBId,
              name: selBId,
              flyTo: !viewport,
              inspect: true,
            });
          }
        } else if (!activeChapterId && !activePortfolio) {
          this.mapController.clearSelection();
          document.getElementById("inspector-drawer")?.classList.add("hidden");
          if (vpChangedSignificantly) {
            this.mapController.flyToLocation({
              lng: viewport.lng,
              lat: viewport.lat,
              zoom: viewport.zoom,
              pitch: viewport.pitch,
            });
          }
        }
        this._applyExportStudioHashParams(window.location.hash, window.location.search);
        this._lastHistorySignature = this._computeHistorySignature(this.filterStore.getState());
      } finally {
        this._suppressUrlUpdate = false;
        this._isRestoringHistory = false;
      }
    };

    window.addEventListener("hashchange", handleUrlChange);
    window.addEventListener("popstate", handleUrlChange);
    this._handleUrlChange = handleUrlChange;
    this._started = true;
  }

  async _resolveAndSelectDeepLinkedProperty(selection, viewport = null, vpChangedSignificantly = false) {
    if (!selection) return;
    let hcadNum = String(selection.hcadNum || "").trim();
    let featureId = String(selection.featureId || "").trim();
    if (!hcadNum && featureId.startsWith("rb1915_")) {
      this.filterStore.setLayerVisibility("redBook1915", true);
      this.mapController?.inspectRedBookById?.(featureId, {
        flyTo: !viewport || vpChangedSignificantly,
      });
      return;
    }
    if (!hcadNum && featureId.startsWith("hahc_")) {
      this.filterStore.setLayerVisibility("hahcTracker", true);
      this.mapController?.inspectHahcById?.(featureId, {
        flyTo: !viewport || vpChangedSignificantly,
      });
      return;
    }
    const rawQuery = String(selection.query || "").trim();
    let addressQuery = rawQuery;
    let fallbackYearBuilt = 0;
    let fallbackBuildingName = "";
    const extraCandidateHcads = [];
    let targetLng = Number.isFinite(Number(viewport?.lng)) ? Number(viewport.lng) : null;
    let targetLat = Number.isFinite(Number(viewport?.lat)) ? Number(viewport.lat) : null;

    if (rawQuery) {
      const searchInput = document.getElementById("search-input");
      if (searchInput && !searchInput.value) {
        searchInput.value = rawQuery;
      }
    }

    const normAddr = normalizeComparableAddress;

    // 1. Check searchIndex for matching featureId/building_id first, then HCAD or street address
    if (Array.isArray(this.searchIndex)) {
      const normQ = normAddr(rawQuery);
      const isSelectableSearchItem = (it) =>
        !(
          it.is_neighborhood_entry ||
          it.is_place_boundary ||
          it.type === "red_book_1915" ||
          it.type === "hahc_coa"
        );
      const exactIdHit = featureId
        ? this.searchIndex.find(
            (it) =>
              isSelectableSearchItem(it) &&
              (String(it.id || "").trim() === featureId ||
                String(it.building_id || "").trim() === featureId)
          )
        : null;
      const idxHit =
        exactIdHit ||
        this.searchIndex.find((it) => {
          if (!isSelectableSearchItem(it)) return false;
          if (featureId && featureId.includes("#")) {
            const itEntity = String(it.building_id || it.id || "").trim();
            if (itEntity.includes("#") && itEntity !== featureId) return false;
          }
          if (!featureId && hcadNum && String(it.hcad_num || "").trim() === hcadNum) return true;
          if (normQ && (normAddr(it.address) === normQ || normAddr(it.label) === normQ)) return true;
          if (hcadNum && !featureId.includes("#") && String(it.hcad_num || "").trim() === hcadNum) {
            return true;
          }
          return false;
        });
      if (idxHit) {
        if (!hcadNum && idxHit.hcad_num) hcadNum = String(idxHit.hcad_num).trim();
        if (idxHit.hcad_num && String(idxHit.hcad_num).trim() !== hcadNum) {
          extraCandidateHcads.push(String(idxHit.hcad_num).trim());
        }
        const hitBldId = String(idxHit.building_id || "").trim();
        const hitId = String(idxHit.id || "").trim();
        if (hitBldId && (!featureId || featureId.startsWith("school_"))) {
          featureId = hitBldId;
        } else if (!featureId && hitId && !hitId.startsWith("school_")) {
          featureId = hitId;
        }
        if (!addressQuery && idxHit.address) addressQuery = String(idxHit.address).trim();
        if (Number(idxHit.year_built) >= 1836) fallbackYearBuilt = Number(idxHit.year_built);
        if (!fallbackBuildingName) {
          fallbackBuildingName = String(
            idxHit.building_name || (hitId.startsWith("school_") ? idxHit.label : "") || ""
          ).trim();
        }
        if (!Number.isFinite(targetLng) && Number.isFinite(Number(idxHit.lon ?? idxHit.lng))) {
          targetLng = Number(idxHit.lon ?? idxHit.lng);
        }
        if (!Number.isFinite(targetLat) && Number.isFinite(Number(idxHit.lat))) {
          targetLat = Number(idxHit.lat);
        }
      }
    }

    // 1b. Also check curatedOverrides directly for #<slug> deep links (including auxiliary skip_search_index buildings)
    const rawDirectOv =
      (featureId && this.mapController?.curatedOverrides?.[featureId]) ||
      (!featureId && hcadNum && this.mapController?.curatedOverrides?.[hcadNum]) ||
      null;
    const directOv =
      rawDirectOv &&
      !isEntitySuppressedByOverrides(this.mapController?.curatedOverrides, {
        buildingId: featureId,
        hcadNum: hcadNum || rawDirectOv.hcad_num || "",
      })
        ? rawDirectOv
        : null;
    if (directOv) {
      const ovHcad = String(
        directOv.hcad_num || (featureId.includes("#") ? featureId.split("#")[0] : "")
      ).trim();
      if (!hcadNum && ovHcad) hcadNum = ovHcad;
      if (!addressQuery && directOv.address) addressQuery = String(directOv.address).trim();
      if (!fallbackYearBuilt && Number(directOv.year_built) >= 1836) {
        fallbackYearBuilt = Number(directOv.year_built);
      }
      if (!fallbackBuildingName && (directOv.building_name || directOv.landmark_name)) {
        fallbackBuildingName = String(directOv.building_name || directOv.landmark_name).trim();
      }
      if (!Number.isFinite(targetLng) && Number.isFinite(Number(directOv.lon ?? directOv.lng))) {
        targetLng = Number(directOv.lon ?? directOv.lng);
      }
      if (!Number.isFinite(targetLat) && Number.isFinite(Number(directOv.lat))) {
        targetLat = Number(directOv.lat);
      }
    }

    // 2. Also check countywide address_shards whenever rawQuery (or addressQuery) begins with a house number
    const addrNumMatch = String(addressQuery || "").trim().match(/^(\d+[a-z]?)\s+(.*)$/i);
    if (addrNumMatch) {
      const typedNum = addrNumMatch[1].toUpperCase();
      const typedDigits = typedNum.replace(/\D/g, "");
      const typedStreetPart = normAddr(addrNumMatch[2]);
      const streetTokens = typedStreetPart.split(/\s+/).filter(Boolean);
      if (typedDigits.length >= 1 && !typedDigits.startsWith("0")) {
        const shardKey = typedDigits.slice(0, 2).padEnd(2, "0");
        this._addressShardCache = this._addressShardCache || {};
        if (!this._addressShardCache[shardKey]) {
          try {
            const resp = await fetch(`public/data/address_shards/addr_${shardKey}.json?v=20261029a`);
            this._addressShardCache[shardKey] = resp.ok ? await resp.json() : {};
          } catch (_e) {
            this._addressShardCache[shardKey] = {};
          }
        }
        const shardData = this._addressShardCache[shardKey] || {};
        let bestRow = null;
        let bestStreet = "";
        let bestRowScore = -Infinity;
        for (const [stName, rows] of Object.entries(shardData)) {
          const normSt = normAddr(stName);
          if (streetTokens.length > 0 && !streetTokens.every((tok) => normSt.includes(tok))) {
            continue;
          }
          const exactSt = normSt === typedStreetPart;
          for (const row of rows) {
            if (String(row[0]).toUpperCase() !== typedNum) continue;
            const rYr = Number(row[3]) || 0;
            const rHcad = String(row[4] || "").trim();
            const rName = String(row[5] || "").trim();
            const rBldId = String(row[6] || "").trim();
            if (this._isSuppressedByCuratedOverride?.(rBldId, rHcad)) continue;
            let rowScore = exactSt ? 100 : 40;
            if (rHcad.length === 13) rowScore += 20;
            if (rYr >= 1836) rowScore += 10;
            if (rBldId) {
              if (rBldId.endsWith("_main") || rBldId.endsWith("#b1")) {
                rowScore += 15;
              } else if (/(?:_aux_|_shop_|_shed_|_portable)/i.test(rBldId)) {
                rowScore -= 5;
              } else {
                rowScore += 10;
              }
            }
            if (rName) rowScore += 5;
            if (rowScore > bestRowScore) {
              bestRowScore = rowScore;
              bestRow = row;
              bestStreet = normSt;
            }
          }
        }
        if (bestRow) {
          const shardLon = Number(bestRow[1]);
          const shardLat = Number(bestRow[2]);
          const shardYr = Number(bestRow[3]) || 0;
          const shardHcad = String(bestRow[4] || "").trim();
          const shardBldName = String(bestRow[5] || "").trim();
          const shardBldId = String(bestRow[6] || "").trim();
          const shardAddr = `${bestRow[0]} ${bestStreet}`.replace(/\s+/g, " ").trim();
          if (shardHcad) {
            if (!hcadNum) {
              hcadNum = shardHcad;
            } else if (shardHcad !== hcadNum) {
              extraCandidateHcads.push(shardHcad);
            }
          }
          if (!featureId && shardBldId) featureId = shardBldId;
          if (!fallbackBuildingName && shardBldName) fallbackBuildingName = shardBldName;
          if (!addressQuery) addressQuery = shardAddr;
          if (!fallbackYearBuilt && shardYr >= 1836) fallbackYearBuilt = shardYr;
          if (!Number.isFinite(targetLng) && Number.isFinite(shardLon)) targetLng = shardLon;
          if (!Number.isFinite(targetLat) && Number.isFinite(shardLat)) targetLat = shardLat;
        }
      }
    }

    if (vpChangedSignificantly && viewport) {
      this.mapController.flyToLocation({
        lng: viewport.lng,
        lat: viewport.lat,
        zoom: viewport.zoom,
        pitch: viewport.pitch,
        hcadNum,
        featureId,
        addressQuery,
        fallbackYearBuilt,
        fallbackBuildingName,
        extraCandidateHcads,
      });
    } else {
      this.mapController.selectFeatureByIdOrHcad({
        hcadNum,
        featureId,
        addressQuery,
        fallbackYearBuilt,
        fallbackBuildingName,
        extraCandidateHcads,
        targetLng,
        targetLat,
        flyTo: !viewport,
      });
    }
  }

  _isSuppressedByCuratedOverride(buildingId = "", hcadNum = "") {
    return isEntitySuppressedByOverrides(this.mapController?.curatedOverrides, {
      buildingId,
      hcadNum,
    });
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
      const [searchRes, statsRes, haifRes, deedRes, sanbornRes, citydirRes] = await Promise.all([
        fetch("public/data/search_index.json?v=20261029a"),
        fetch("public/data/stats_summary.json?v=20261029a"),
        fetch("public/data/haif_index.json?v=20261029a").catch(() => null),
        fetch("public/data/deed_restrictions_catalog.json?v=20261029a").catch(() => null),
        fetch("public/data/sanborn_catalog.json?v=20261029a").catch(() => null),
        fetch("public/data/citydir_index.json?v=20261029a").catch(() => null),
      ]);
      const rawIdx = await searchRes.json();
      for (const item of rawIdx) {
        if (
          item.is_neighborhood_entry ||
          item.type === "lost_houston" ||
          item.type === "nrhp_property" ||
          item.type === "red_book_1915" ||
          item.type === "hahc_coa"
        ) {
          if (!item.label) item.label = item.name || item.building_name || "Houston Neighborhood";
          if (!Number.isFinite(item.lon) && Number.isFinite(item.lng)) item.lon = item.lng;
          if (item.overlay_layer === "historicWards") item.overlay_layer = "historic_wards";
          if (item.overlay_layer === "superNeighborhoods") item.overlay_layer = "super_neighborhoods";
          if (item.overlay_layer === "plattedSubdivisions") {
            item.overlay_layer = "platted_subdivisions";
          }
          if (item.overlay_layer === "lostHouston") item.overlay_layer = "lost_houston";
          if (item.overlay_layer === "potentialHistoricDistricts") {
            item.overlay_layer = "potential_historic_districts";
          }
          if (item.overlay_layer === "culturalDistricts") {
            item.overlay_layer = "cultural_districts";
          }
          if (item.overlay_layer === "holc1937") {
            item.overlay_layer = "holc_1937_zones";
          }
          if (item.overlay_layer === "redBook1915") {
            item.overlay_layer = "red_book_1915";
          }
          if (
            item.type === "hahc_coa" ||
            item.overlay_layer === "hahcTracker" ||
            item.overlay_layer === "hahc_coa"
          ) {
            item.overlay_layer = "hahc_coa_filings";
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
      if (sanbornRes && sanbornRes.ok) {
        this.sanbornCatalog = await sanbornRes.json();
      } else {
        this.sanbornCatalog = null;
      }
      if (citydirRes && citydirRes.ok) {
        this.citydirIndex = await citydirRes.json();
        this.citydirAbsentSet = new Set(this.citydirIndex?.absent_through_1926 || []);
      } else {
        this.citydirIndex = null;
        this.citydirAbsentSet = new Set();
      }
      if (this.mapController?.overlaysData) {
        this._mergeCuratedOverridesIntoSearchIndex();
        this._enrichSearchIndexWithOverlayMetadata();
      }
      this._renderGlobalDatasetSummary();
      const activeDrawer = document.getElementById("inspector-drawer");
      if (
        this._currentInspectedRawProps &&
        activeDrawer &&
        !activeDrawer.classList.contains("hidden") &&
        !activeDrawer.classList.contains("boundary-mode")
      ) {
        this.renderInspectorDrawer(this._currentInspectedRawProps);
      }
    } catch (err) {
      console.error("Failed to load metadata files:", err);
    }
  }

  _syncPhotoExclusionRegistries() {
    clearRegisteredPhotoExclusions();
    const rejectedPhotos = this.mapController?.globalRejections?.rejectedPhotos || [];
    if (rejectedPhotos.length > 0) {
      registerGloballyRejectedPhotos(rejectedPhotos);
    }
    const landmarkEntries = [];
    const overlays = this.mapController?.overlaysData;
    if (overlays) {
      for (const f of overlays.landmarks?.features || []) {
        if (f?.properties) landmarkEntries.push(f.properties);
      }
      for (const f of overlays.good_brick_awards?.features || []) {
        if (f?.properties) landmarkEntries.push(f.properties);
      }
    }
    const ovMap = this.mapController?.curatedOverrides || {};
    for (const ov of Object.values(ovMap)) {
      if (ov && !ov.suppress_only) landmarkEntries.push(ov);
    }
    if (landmarkEntries.length > 0) {
      registerKnownLandmarksForPhotoExclusion(landmarkEntries);
    }
  }

  _getActiveWebhookUrl() {
    return (
      document.getElementById("admin-webhook-url")?.value?.trim() ||
      this.mapController?.sheetSyncStatus?.webhookUrl ||
      ""
    );
  }

  _showQuickFlagToast(message) {
    if (typeof document === "undefined") return;
    let toast = document.getElementById("quick-flag-toast");
    if (!toast) {
      toast = document.createElement("div");
      toast.id = "quick-flag-toast";
      toast.className = "quick-flag-toast";
      toast.setAttribute("role", "status");
      toast.setAttribute("aria-live", "polite");
      document.body.appendChild(toast);
    }
    toast.textContent = message;
    toast.style.opacity = "1";
    toast.classList.remove("hidden");
    if (this._quickFlagToastTimer) {
      clearTimeout(this._quickFlagToastTimer);
    }
    this._quickFlagToastTimer = setTimeout(() => {
      if (toast) {
        toast.style.opacity = "0";
        setTimeout(() => toast.classList.add("hidden"), 240);
      }
    }, 3800);
  }

  _escapeHtmlAttr(val) {
    return String(val ?? "")
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  _parseCitationsArray(raw) {
    let list = raw;
    if (typeof list === "string") {
      const s = list.trim();
      if (s.startsWith("[")) {
        try {
          list = JSON.parse(s);
        } catch (_e) {
          list = [];
        }
      } else {
        list = [];
      }
    }
    if (!Array.isArray(list)) return [];
    const out = [];
    for (const item of list) {
      if (!item) continue;
      if (typeof item === "string") {
        const clean = item.trim();
        if (clean) out.push({ label: clean, url: "" });
      } else if (typeof item === "object") {
        const label = String(item.label || item.title || item.source || "").trim();
        const url = String(item.url || item.href || "").trim();
        if (label) out.push({ label, url });
      }
    }
    return out;
  }

  _buildNarrativeCitationsHtml({
    citations = [],
    contextId = "",
    contextTitle = "",
    excerpt = "",
    compact = false,
  } = {}) {
    const parsed = this._parseCitationsArray(citations);
    if (!parsed.length && !contextId && !contextTitle) return "";
    const pillsHtml = parsed
      .map((c) => {
        const escLabel = this._escapeHtmlAttr(c.label);
        if (c.url && /^https?:\/\//i.test(c.url)) {
          const escUrl = this._escapeHtmlAttr(c.url);
          return `<a
            href="${escUrl}"
            target="_blank"
            rel="noopener noreferrer"
            class="narrative-citation-pill"
            title="Open primary archival source: ${escLabel}"
          >${escLabel} &#8599;</a>`;
        }
        return `<span class="narrative-citation-pill" title="Primary archival citation">${escLabel}</span>`;
      })
      .join("");

    const flagBtnHtml =
      contextId || contextTitle
        ? `<button
            type="button"
            class="quick-flag-btn"
            data-quick-flag="narrative"
            data-flag-context-id="${this._escapeHtmlAttr(contextId)}"
            data-flag-context-title="${this._escapeHtmlAttr(contextTitle)}"
            data-flag-excerpt="${this._escapeHtmlAttr(String(excerpt || "").slice(0, 180))}"
            title="Report a historical error, conflation, or misattribution in this narrative for Preservation Houston review"
          >&#9873; Flag Error</button>`
        : "";

    return `<div class="narrative-citations-bar"${compact ? ' style="margin-top:5px;"' : ""}>
      ${parsed.length ? '<span class="narrative-citations-label">Sources:</span>' : ""}
      ${pillsHtml}
      ${flagBtnHtml}
    </div>`;
  }

  _renderNarrativeCitationsBar(containerEl, opts = {}) {
    if (!containerEl) return;
    const html = this._buildNarrativeCitationsHtml(opts);
    containerEl.innerHTML = html;
    containerEl.classList.toggle("hidden", !html);
    this._bindQuickFlagEvents(containerEl);
  }

  _bindQuickFlagEvents(containerEl, contextProps = null) {
    if (!containerEl) return;
    containerEl.querySelectorAll("[data-quick-flag]").forEach((btn) => {
      if (btn.dataset.flagBound === "1") return;
      btn.dataset.flagBound = "1";
      btn.addEventListener("click", async (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (btn.classList.contains("flagged")) return;

        const flagType = btn.getAttribute("data-quick-flag") || "claim";
        const hcadNum =
          btn.getAttribute("data-flag-hcad") ||
          String(contextProps?.hcad_num || contextProps?.id || "").trim();
        const address =
          btn.getAttribute("data-flag-address") ||
          String(contextProps?.address || "").trim();
        const district =
          btn.getAttribute("data-flag-district") ||
          String(contextProps?.historic_district || "").trim();
        const field = btn.getAttribute("data-flag-field") || "";
        const disputedValue =
          btn.getAttribute("data-flag-value") ||
          btn.getAttribute("data-flag-excerpt") ||
          "";
        const provenance = btn.getAttribute("data-flag-provenance") || "";
        const contextId = btn.getAttribute("data-flag-context-id") || "";
        const contextTitle = btn.getAttribute("data-flag-context-title") || "";
        const currentYearBuilt =
          Number(btn.getAttribute("data-flag-year") || contextProps?.year_built) || 0;
        const shouldSuppress = btn.getAttribute("data-flag-suppress") !== "false";

        btn.classList.add("flagged");
        btn.innerHTML = "&#10003; Flagged";

        await submitQuickFlagReport(
          {
            flagType,
            hcad_num: hcadNum,
            address,
            historic_district: district,
            field,
            disputed_value: disputedValue,
            provenance,
            context_id: contextId,
            context_title: contextTitle,
            current_year_built: currentYearBuilt,
            suppressLocally: shouldSuppress,
          },
          this._getActiveWebhookUrl()
        );

        if (flagType === "narrative") {
          this._showQuickFlagToast(
            `Flagged "${contextTitle || "narrative blurb"}" for Preservation Houston historical review.`
          );
        } else if (shouldSuppress && (field === "building_name" || field === "architect")) {
          this._showQuickFlagToast(
            `Hidden unverified ${field.replace("_", " ")} "${disputedValue}" locally and queued for Preservation Houston moderation.`
          );
          if (this._currentInspectedRawProps) {
            this.renderInspectorDrawer(this._currentInspectedRawProps);
          }
        } else {
          this._showQuickFlagToast(
            `Flagged ${field || "record"} "${disputedValue}" for Preservation Houston archival review.`
          );
        }
      });
    });
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
    const candidateSources = [];
    const candidateAlts = [];
    const globalRej = this.mapController?.globalRejections || null;
    const suppressKey = hcad || rawId;

    const addPrimary = (nm, src = "", isVerifiedOverride = false) => {
      const c = cleanNameStr(nm);
      if (!c || isStreetAddrOnly(c)) return;
      if (
        !isVerifiedOverride &&
        isClaimSuppressed({ hcadNum: suppressKey, field: "building_name", value: c }, globalRej)
      ) {
        return;
      }
      candidatePrimaries.push(c);
      candidateSources.push(String(src || "").trim());
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
          if (
            c &&
            !isStreetAddrOnly(c) &&
            !isClaimSuppressed({ hcadNum: suppressKey, field: "building_name", value: c }, globalRej)
          ) {
            candidateAlts.push(c);
          }
        }
      }
    };

    // 1. Direct feature properties
    const isVerifiedOv = Boolean(props.is_curated_override);
    addPrimary(props.building_name, props.name_source || "", isVerifiedOv);
    addPrimary(props.landmark_name, props.name_source || "", isVerifiedOv);
    addPrimary(props.name, props.name_source || "", isVerifiedOv);
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
    let resolvedSource = hasCustomBuildingName
      ? candidateSources[0] || String(props.name_source || "").trim()
      : "";
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
    const nameEvidentiaryTier = hasCustomBuildingName
      ? classifyFieldEvidentiaryTier({
          field: "building_name",
          value: primaryName,
          sourceLabel: resolvedSource,
          isCuratedOverride: Boolean(props.is_curated_override),
        })
      : null;

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
      nameEvidentiaryTier,
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

    const globalRej = this.mapController?.globalRejections || null;
    const suppressKey = String(props.hcad_num || props.building_id || props.id || "").trim();

    const chips = [];
    for (const part of parts) {
      const cleaned = part.replace(/,\s*(?:architects?|builders?|bldr|consulting)\b.*$/i, "").trim();
      const m = cleaned.match(/^(.+?)\s*\(([^)]+)\)\s*$/);
      const core = m ? m[1].trim() : cleaned;
      const note = m ? m[2].trim() : "";
      if (!core) continue;
      if (
        !props.is_curated_override &&
        isClaimSuppressed({ hcadNum: suppressKey, field: "architect", value: core }, globalRej)
      ) {
        continue;
      }
      chips.push({
        label: core,
        query: core,
        note,
        headerLabel: `Architect / Builder: ${core}`,
      });
    }
    return chips;
  }

  _resolveBuilderChips(props = {}) {
    const raw = String(props.builder || "").trim();
    if (!raw || /^(?:unknown|none|n\/a|null|unrecorded|tbd)$/i.test(raw)) {
      return [];
    }
    const parts = raw
      .split(/\s*[;/]\s*/)
      .map((s) => s.trim())
      .filter(Boolean);

    const chips = [];
    for (const part of parts) {
      const m = part.match(/^(.+?)\s*\(([^)]+)\)\s*$/);
      const core = m ? m[1].trim() : part;
      const note = m ? m[2].trim() : "";
      if (!core) continue;
      chips.push({
        label: core,
        query: core,
        note,
        headerLabel: `Builder / Contractor: ${core}`,
      });
    }
    return chips;
  }

  _mergeCuratedOverridesIntoSearchIndex() {
    const ovMap = (this.mapController && this.mapController.curatedOverrides) || {};
    const suppressedHcads = new Set();
    const suppressedEntityIds = new Set();
    for (const [k, ov] of Object.entries(ovMap)) {
      if (ov && ov.suppress_only) {
        const cleanK = String(k || "").trim();
        if (cleanK) suppressedEntityIds.add(cleanK);
        if (cleanK.includes("#") || ov.is_building_override) {
          if (ov.building_id) suppressedEntityIds.add(String(ov.building_id).trim());
        } else {
          const cleanH = String(ov.hcad_num || cleanK).trim();
          if (cleanH) suppressedHcads.add(cleanH);
        }
      }
    }
    if (suppressedHcads.size > 0 || suppressedEntityIds.size > 0) {
      this.searchIndex = this.searchIndex.filter((item) => {
        const itemId = String(item.id || "").trim();
        const itemBldId = String(item.building_id || "").trim();
        if (
          (itemId && suppressedEntityIds.has(itemId)) ||
          (itemBldId && suppressedEntityIds.has(itemBldId))
        ) {
          return false;
        }
        if (
          item.is_neighborhood_entry ||
          item.is_place_boundary ||
          item.type === "lost_houston" ||
          item.type === "red_book_1915" ||
          item.type === "hahc_coa"
        ) {
          return true;
        }
        if (item.hcad_num && suppressedHcads.has(String(item.hcad_num).trim())) {
          return false;
        }
        return true;
      });
    }
    const existingByEntityId = new Map();
    const existingByHcad = new Map();
    const coordsByHcad = new Map();
    const schoolIdxsByBuildingId = new Map();
    const schoolIdxsByHcad = new Map();
    for (let i = 0; i < this.searchIndex.length; i++) {
      const item = this.searchIndex[i];
      if (
        item.is_neighborhood_entry ||
        item.is_place_boundary ||
        item.type === "red_book_1915" ||
        item.type === "hahc_coa"
      ) {
        continue;
      }
      const itemId = String(item.id || "").trim();
      const itemBldId = String(item.building_id || "").trim();
      const itemHcad = String(item.hcad_num || "").trim();
      if (
        itemHcad &&
        !coordsByHcad.has(itemHcad) &&
        Number.isFinite(Number(item.lon)) &&
        Number.isFinite(Number(item.lat))
      ) {
        coordsByHcad.set(itemHcad, { lon: Number(item.lon), lat: Number(item.lat) });
      }
      if (itemId.startsWith("school_")) {
        if (itemBldId) {
          const list = schoolIdxsByBuildingId.get(itemBldId) || [];
          list.push(i);
          schoolIdxsByBuildingId.set(itemBldId, list);
        }
        if (itemHcad) {
          const list = schoolIdxsByHcad.get(itemHcad) || [];
          list.push(i);
          schoolIdxsByHcad.set(itemHcad, list);
        }
        continue;
      }
      const entityKey = itemBldId || itemId;
      if (entityKey) {
        existingByEntityId.set(entityKey, i);
      }
      if (itemId && !existingByEntityId.has(itemId)) {
        existingByEntityId.set(itemId, i);
      }
      const isSubBuildingItem =
        (entityKey.includes("#") && !entityKey.endsWith("#b1")) ||
        Boolean(item.is_building_override);
      if (!isSubBuildingItem && itemHcad) {
        existingByHcad.set(itemHcad, i);
      }
    }

    for (const [ovKeyRaw, ov] of Object.entries(ovMap)) {
      if (!ov || ov.suppress_only) continue;
      const ovKey = String(ovKeyRaw || "").trim();
      if (!ovKey) continue;
      const isSubBuildingOv = ovKey.includes("#") || Boolean(ov.is_building_override);
      const cleanHcad = String(
        ov.hcad_num || (ovKey.includes("#") ? ovKey.split("#")[0] : ovKey)
      ).trim();
      const existingIdx = isSubBuildingOv
        ? existingByEntityId.get(ovKey)
        : existingByEntityId.get(ovKey) ??
          (cleanHcad ? existingByHcad.get(cleanHcad) : undefined);
      const existingItem = existingIdx !== undefined ? this.searchIndex[existingIdx] : null;
      const hcadFallbackCoords = cleanHcad ? coordsByHcad.get(cleanHcad) : null;
      let lon = ov.lon ?? ov.lng ?? existingItem?.lon ?? hcadFallbackCoords?.lon ?? -95.38718;
      let lat = ov.lat ?? existingItem?.lat ?? hcadFallbackCoords?.lat ?? 29.79175;
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
      if (
        cleanHcad &&
        !coordsByHcad.has(cleanHcad) &&
        (ov.lon !== undefined || ov.lng !== undefined || ov.geometry)
      ) {
        coordsByHcad.set(cleanHcad, { lon: Number(lon), lat: Number(lat) });
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

      const cleanExistingAddr = /^0\s+/.test(String(existingItem?.address || "").trim())
        ? ""
        : String(existingItem?.address || "").trim();
      const cleanOvAddr = /^0\s+/.test(String(ov.address || "").trim())
        ? ""
        : String(ov.address || "").trim();
      const primaryAddr = String(
        (isSubBuildingOv ? cleanOvAddr || cleanExistingAddr : cleanOvAddr || cleanExistingAddr) ||
          ov.address ||
          existingItem?.address ||
          ""
      ).trim();
      const rawAltAddrs = [
        ...(Array.isArray(existingItem?.alt_addresses) ? existingItem.alt_addresses : []),
        ...(Array.isArray(ov.alt_addresses) ? ov.alt_addresses : []),
        cleanOvAddr,
        cleanExistingAddr,
      ];
      const seenAddrLow = new Set(primaryAddr ? [primaryAddr.toLowerCase()] : []);
      const altAddresses = [];
      for (const a of rawAltAddrs) {
        const cleanA = String(a || "").trim();
        if (!cleanA || /^0\s+/.test(cleanA)) continue;
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

      const mergedBuilder = ov.builder || existingItem?.builder || "";
      const bldrSnippet = mergedBuilder ? ` • Built by ${mergedBuilder}` : "";
      const canonicalBldId = String(
        ov.building_id || (isSubBuildingOv ? ovKey : existingItem?.building_id || "")
      ).trim();

      const entry = {
        type: "building",
        id: ov.id || (isSubBuildingOv ? ovKey : existingItem?.id || `ov_${cleanHcad}`),
        ...(canonicalBldId ? { building_id: canonicalBldId } : {}),
        ...(isSubBuildingOv ? { is_building_override: true } : {}),
        hcad_num: cleanHcad,
        label: primaryBldName || primaryAddr || ov.address || existingItem?.label || `HCAD ${cleanHcad}`,
        address: primaryAddr || ov.address || "",
        ...(altAddresses.length > 0 ? { alt_addresses: altAddresses } : {}),
        building_name: primaryBldName,
        alt_names: altNames,
        name_source: ov.name_source || existingItem?.name_source || "",
        sublabel: `${akaSnippet}${addrSnippet}${ov.historic_district || ov.neighborhood || existingItem?.historic_district || existingItem?.neighborhood || mergedUseCategory || "Harris County"} • Built ${ov.year_built}${bldrSnippet} (✓ PH Verified)`,
        category: `Built ${ov.year_built} ✓`,
        year_built: ov.year_built,
        architect: ov.architect || existingItem?.architect || "",
        builder: mergedBuilder,
        builder_source: ov.builder_source || existingItem?.builder_source || "",
        engineers: ov.engineers || existingItem?.engineers || "",
        original_cost: ov.original_cost || existingItem?.original_cost || "",
        original_permit: ov.original_permit || existingItem?.original_permit || "",
        yr_remodel: ov.yr_remodel || existingItem?.yr_remodel || 0,
        eff_year_built: ov.eff_year_built || existingItem?.eff_year_built || 0,
        was_relocated: Boolean(ov.was_relocated || existingItem?.was_relocated),
        relocation_note: ov.relocation_note || existingItem?.relocation_note || "",
        hist_contract: ov.hist_contract || existingItem?.hist_contract || null,
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
      } else if (!ov.skip_search_index) {
        const newIdx = this.searchIndex.push(entry) - 1;
        existingByEntityId.set(ovKey, newIdx);
        if (canonicalBldId) existingByEntityId.set(canonicalBldId, newIdx);
        if (!isSubBuildingOv && cleanHcad) {
          existingByHcad.set(cleanHcad, newIdx);
        }
      }

      // Propagate verified year/architect/numbered situs non-destructively to linked school_* entries
      const linkedSchoolIdxs = isSubBuildingOv
        ? schoolIdxsByBuildingId.get(ovKey) || []
        : [
            ...(schoolIdxsByBuildingId.get(ovKey) || []),
            ...(cleanHcad
              ? (schoolIdxsByHcad.get(cleanHcad) || []).filter(
                  (sIdx) => !String(this.searchIndex[sIdx]?.building_id || "").includes("#")
                )
              : []),
          ];
      for (const sIdx of linkedSchoolIdxs) {
        const sItem = this.searchIndex[sIdx];
        if (!sItem) continue;
        if (Number(ov.year_built) >= 1836) {
          sItem.year_built = Number(ov.year_built);
        }
        if (ov.architect && !sItem.architect) {
          sItem.architect = ov.architect;
        }
        if ((ov.bld_style || ov.style) && !sItem.bld_style) {
          sItem.bld_style = ov.bld_style || ov.style;
        }
        if (cleanOvAddr && (!sItem.address || /^0\s+/.test(String(sItem.address).trim()))) {
          sItem.address = cleanOvAddr;
        }
      }
    }

    const multiEntityHcads = new Set();
    const hcadItemCount = new Map();
    for (const [ovKeyRaw, ov] of Object.entries(ovMap)) {
      if (!ov || ov.suppress_only) continue;
      const ovKey = String(ovKeyRaw || "").trim();
      if (ovKey.includes("#") && !ovKey.endsWith("#b1")) {
        const h = String(ov.hcad_num || ovKey.split("#")[0]).trim();
        if (h) multiEntityHcads.add(h);
      }
    }
    for (const item of this.searchIndex) {
      if (
        !item ||
        item.is_neighborhood_entry ||
        item.is_place_boundary ||
        item.type === "red_book_1915" ||
        item.type === "hahc_coa"
      ) {
        continue;
      }
      const h = String(item.hcad_num || "").trim();
      if (!h) continue;
      const eKey = String(item.building_id || item.id || "").trim();
      if (eKey.includes("#") && !eKey.endsWith("#b1")) {
        multiEntityHcads.add(h);
      }
      const nextCnt = (hcadItemCount.get(h) || 0) + 1;
      hcadItemCount.set(h, nextCnt);
      if (nextCnt >= 2) {
        multiEntityHcads.add(h);
      }
    }
    this._multiEntityHcads = multiEntityHcads;
  }

  _enrichSearchIndexWithOverlayMetadata() {
    if (!this.mapController) return;
    const byEntityId = new Map();
    const byHcad = new Map();
    const byLabel = new Map();
    for (let i = 0; i < this.searchIndex.length; i++) {
      const item = this.searchIndex[i];
      if (item.type === "red_book_1915" || item.type === "hahc_coa") continue;
      if (item.is_neighborhood_entry || item.is_place_boundary) {
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
        continue;
      }
      const itemId = String(item.id || "").trim();
      const entityKey = String(item.building_id || itemId).trim();
      if (!itemId.startsWith("school_") && entityKey) {
        byEntityId.set(entityKey, item);
      }
      if (!itemId.startsWith("school_") && !entityKey.includes("#") && item.hcad_num) {
        byHcad.set(String(item.hcad_num).trim(), item);
      }
      if (item.label) byLabel.set(String(item.label).trim().toLowerCase(), item);
    }

    // 1. Enrich from buildingsData
    for (const feat of this.mapController.buildingsData || []) {
      const p = feat.properties || {};
      const hcad = String(p.hcad_num || "").trim();
      const featEntityKey = String(p.building_id || p.id || "").trim();
      const ring =
        feat.geometry?.type === "Polygon"
          ? feat.geometry.coordinates?.[0]
          : feat.geometry?.type === "MultiPolygon"
          ? feat.geometry.coordinates?.[0]?.[0]
          : null;
      const target =
        (featEntityKey && byEntityId.get(featEntityKey)) ||
        (!featEntityKey.includes("#") && hcad && byHcad.get(hcad)) ||
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
        if (p.builder && !target.builder) target.builder = p.builder;
        if (p.builder_source && !target.builder_source) target.builder_source = p.builder_source;
        if (p.engineers && !target.engineers) target.engineers = p.engineers;
        if (p.original_cost && !target.original_cost) target.original_cost = p.original_cost;
        if (p.original_permit && !target.original_permit) target.original_permit = p.original_permit;
        if (p.yr_remodel && !target.yr_remodel) target.yr_remodel = p.yr_remodel;
        if (p.eff_year_built && !target.eff_year_built) target.eff_year_built = p.eff_year_built;
        if (p.was_relocated) target.was_relocated = true;
        if (p.relocation_note && !target.relocation_note) target.relocation_note = p.relocation_note;
        if (p.hist_contract && !target.hist_contract) target.hist_contract = p.hist_contract;
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
        p.builder ||
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
            p.builder ? `Built by ${p.builder}` : "",
          ]
            .filter(Boolean)
            .join(" • "),
          category: p.year_built ? `Built ${p.year_built}` : p.use_category || "Historic Structure",
          year_built: p.year_built || 0,
          architect: p.architect || "",
          builder: p.builder || "",
          builder_source: p.builder_source || "",
          engineers: p.engineers || "",
          original_cost: p.original_cost || "",
          original_permit: p.original_permit || "",
          yr_remodel: p.yr_remodel || 0,
          eff_year_built: p.eff_year_built || 0,
          was_relocated: Boolean(p.was_relocated),
          relocation_note: p.relocation_note || "",
          hist_contract: p.hist_contract || null,
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

    // 2. Enrich from overlaysData.landmarks & overlaysData.good_brick_awards & overlaysData.relocated_structures
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
        if (p.builder && !target.builder) target.builder = p.builder;
        if (p.builder_source && !target.builder_source) target.builder_source = p.builder_source;
        if (p.original_cost && !target.original_cost) target.original_cost = p.original_cost;
        if (p.original_permit && !target.original_permit) target.original_permit = p.original_permit;
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
            p.builder ? `Built by ${p.builder}` : "",
          ]
            .filter(Boolean)
            .join(" • "),
          category: p.designation || "Landmark",
          year_built: p.year_built || 0,
          architect: p.architect || "",
          builder: p.builder || "",
          builder_source: p.builder_source || "",
          original_cost: p.original_cost || "",
          original_permit: p.original_permit || "",
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

    const relocFeats = this.mapController.overlaysData?.relocated_structures?.features || [];
    for (const feat of relocFeats) {
      const p = feat.properties || {};
      const hcad = String(p.hcad_num || "").trim();
      const target =
        (hcad && byHcad.get(hcad)) ||
        (p.name && byLabel.get(String(p.name).toLowerCase()));
      if (target) {
        if (p.builder && !target.builder) target.builder = p.builder;
        if (p.builder_source && !target.builder_source) target.builder_source = p.builder_source;
        if (p.architect && !target.architect) target.architect = p.architect;
        if (p.original_cost && !target.original_cost) target.original_cost = p.original_cost;
        if (p.original_permit && !target.original_permit) target.original_permit = p.original_permit;
        if (p.was_relocated) target.was_relocated = true;
        if (p.relocation_note && !target.relocation_note) target.relocation_note = p.relocation_note;
        if (p.hist_contract && !target.hist_contract) target.hist_contract = p.hist_contract;
      } else if (feat.geometry?.coordinates) {
        const [lon, lat] = feat.geometry.coordinates;
        const newEntry = {
          type: "building",
          id: p.id || hcad,
          hcad_num: hcad,
          label: p.name || p.address || `HCAD ${hcad}`,
          address: p.address || "",
          building_name: p.name || "",
          sublabel: [
            p.address,
            p.builder ? `Built by ${p.builder}` : "",
            p.was_relocated ? "🚚 Relocated Structure" : "",
          ]
            .filter(Boolean)
            .join(" • "),
          category: p.year_built ? `Built ${p.year_built}` : "Historic Structure",
          year_built: p.year_built || 0,
          architect: p.architect || "",
          builder: p.builder || "",
          builder_source: p.builder_source || "",
          original_cost: p.original_cost || "",
          original_permit: p.original_permit || "",
          was_relocated: Boolean(p.was_relocated),
          relocation_note: p.relocation_note || "",
          hist_contract: p.hist_contract || null,
          lon,
          lat,
          zoom: 17.5,
        };
        this.searchIndex.push(newEntry);
        if (hcad) byHcad.set(hcad, newEntry);
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

    // 3. Enrich from active_permit_alerts.json
    const permitAlertsPayload = this.mapController?.getPermitAlertsPayload?.();
    const permitAlerts = Array.isArray(permitAlertsPayload?.alerts) ? permitAlertsPayload.alerts : [];
    for (const al of permitAlerts) {
      const hcad = String(al.hcad_num || "").trim();
      const target = hcad && byHcad.get(hcad);
      const badgeLabel =
        al.severity === "critical"
          ? `🚨 ${al.badge || "Active Demo / Sewer Disc"}`
          : `⚠ ${al.badge || "Permit / Asbestos Watch"}`;
      if (target) {
        if (al.building_name && !target.building_name) {
          target.building_name = al.building_name;
          if (!target.label || target.label.toLowerCase() === String(al.address || "").toLowerCase()) {
            target.label = al.building_name;
          }
        }
        if (al.architect && !target.architect) target.architect = al.architect;
        if (al.style && !target.style) target.style = al.style;
        if (al.yr_remodel && !target.yr_remodel) target.yr_remodel = al.yr_remodel;
        if (!String(target.sublabel || "").includes(badgeLabel)) {
          target.sublabel = `${badgeLabel} • ${target.sublabel || al.address || ""}`;
        }
        target.original_permit = [
          target.original_permit || "",
          al.headline || "",
          al.badge || "",
          "demolition watch permit watch sewer disconnect asbestos abatement",
        ]
          .filter(Boolean)
          .join(" ");
      } else if (Number.isFinite(al.lon) && Number.isFinite(al.lat)) {
        const newEntry = {
          type: "building",
          id: al.building_id || hcad,
          hcad_num: hcad,
          label: al.building_name || al.address || `HCAD ${hcad}`,
          address: al.address || "",
          building_name: al.building_name || "",
          sublabel: [
            badgeLabel,
            al.address,
            al.neighborhood || al.historic_district || "",
            al.headline || "",
          ]
            .filter(Boolean)
            .join(" • "),
          category: al.year_built ? `Built ${al.year_built}` : badgeLabel,
          year_built: al.year_built || 0,
          architect: al.architect || "",
          style: al.style || "",
          bld_style: al.style || "",
          historic_district: al.historic_district || "",
          neighborhood: al.neighborhood || "",
          original_permit: `${al.headline || ""} ${al.badge || ""} demolition watch permit watch sewer disconnect asbestos abatement`,
          lon: al.lon,
          lat: al.lat,
          zoom: 17.6,
        };
        this.searchIndex.push(newEntry);
        if (hcad) byHcad.set(hcad, newEntry);
      }
    }

    for (let i = 0; i < this.searchIndex.length; i++) {
      this.searchIndex[i]._lc = null;
    }
    this._scheduleSearchIndexPrewarm();
  }

  _getSearchLowercaseCache(item) {
    if (item._lc) return item._lc;
    const rawKey = String(item.id || item.building_id || item.hcad_num || item.label || "").trim();
    const key = rawKey.replace(/#fp_\d+$/, "");
    const rawHcadStr = String(item.hcad_num || "").trim();
    const hcadKey =
      item.type === "red_book_1915" || item.type === "hahc_coa" ? "" : rawHcadStr;
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
    const hcad = rawHcadStr.toLowerCase();
    const arch = String(item.architect || "").toLowerCase();
    const builder = String(item.builder || "").toLowerCase();
    const permit = String(item.original_permit || "").toLowerCase();
    const reloc = item.was_relocated ? "relocated moved structure" : "";
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
    const nrhpRef = String(item.refnum || item.nrhp_refnum || "").toLowerCase();
    const isPlaceBoundary = Boolean(
      item.is_neighborhood_entry ||
        item.type === "neighborhood" ||
        item.type === "platted_subdivision" ||
        item.type === "super_neighborhood" ||
        item.type === "historic_ward" ||
        item.type === "historic_district" ||
        item.type === "nrhp_district" ||
        item.type === "potential_historic_district" ||
        item.type === "cultural_district" ||
        item.type === "holc_1937_zone" ||
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
      builder,
      permit,
      reloc,
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
      nrhpRef ? `${nrhpRef} nrhp #${nrhpRef} national register` : "",
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
      builder,
      permit,
      reloc,
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

    // Basemap dropdown & selector
    const basemapDropdown = document.getElementById("basemap-dropdown");
    const basemapTrigger = document.getElementById("btn-basemap-trigger");
    const basemapMenu = document.getElementById("basemap-dropdown-menu");
    const moreDropdown = document.getElementById("topbar-more-dropdown");
    const moreTrigger = document.getElementById("btn-topbar-more");

    const closeTopbarDropdowns = () => {
      if (basemapMenu) basemapMenu.classList.add("hidden");
      if (basemapTrigger) basemapTrigger.setAttribute("aria-expanded", "false");
      if (moreDropdown) moreDropdown.classList.remove("open");
      if (moreTrigger) moreTrigger.setAttribute("aria-expanded", "false");
    };

    if (basemapTrigger && basemapMenu) {
      basemapTrigger.addEventListener("click", (e) => {
        e.stopPropagation();
        const willOpen = basemapMenu.classList.contains("hidden");
        closeTopbarDropdowns();
        if (willOpen) {
          basemapMenu.classList.remove("hidden");
          basemapTrigger.setAttribute("aria-expanded", "true");
        }
      });
    }

    if (moreTrigger && moreDropdown) {
      moreTrigger.addEventListener("click", (e) => {
        e.stopPropagation();
        const willOpen = !moreDropdown.classList.contains("open");
        closeTopbarDropdowns();
        if (willOpen) {
          moreDropdown.classList.add("open");
          moreTrigger.setAttribute("aria-expanded", "true");
        }
      });
    }

    document.querySelectorAll("#topbar-more-items button, #topbar-more-items a").forEach((btn) => {
      btn.addEventListener("click", () => {
        closeTopbarDropdowns();
      });
    });

    document.querySelectorAll("[data-basemap]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const base = btn.getAttribute("data-basemap");
        this.filterStore.setState({ basemap: base });
        closeTopbarDropdowns();
      });
    });

    document.addEventListener("click", (e) => {
      if (
        (basemapDropdown && !basemapDropdown.contains(e.target)) &&
        (!moreDropdown || !moreDropdown.contains(e.target))
      ) {
        closeTopbarDropdowns();
      }
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

    // Historical Snapshot Mode Checkbox
    const chkHistoricalSnapshot = document.getElementById("chk-historical-snapshot");
    if (chkHistoricalSnapshot) {
      chkHistoricalSnapshot.addEventListener("change", (e) => {
        const checked = Boolean(e.target.checked);
        const curLayers = this.filterStore.getState().layers || {};
        this.filterStore.setState({
          historicalSnapshotMode: checked,
          layers: {
            ...curLayers,
            lostHouston: checked ? true : curLayers.lostHouston,
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
      ["chk-layer-permit-watch", "permitWatch"],
      ["chk-layer-plat-watch", "platWatch"],
      ["chk-layer-hahc-tracker", "hahcTracker"],
      ["chk-layer-good-brick", "goodBrickAwards"],
      ["chk-layer-landmarks", "landmarks"],
      ["chk-layer-potential-districts", "potentialHistoricDistricts"],
      ["chk-layer-lost-houston", "lostHouston"],
      ["chk-layer-relocated-structures", "relocatedStructures"],
      ["chk-layer-historic-districts", "historicDistricts"],
      ["chk-layer-heritage-districts", "heritageDistricts"],
      ["chk-layer-nrhp-districts", "nrhpDistricts"],
      ["chk-layer-nrhp-properties", "nrhpProperties"],
      ["chk-layer-cultural-districts", "culturalDistricts"],
      ["chk-layer-red-book", "redBook1915"],
      ["chk-layer-holc-1937", "holc1937"],
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

    document.querySelectorAll("[data-lm-filter]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const rawVal = String(btn.getAttribute("data-lm-filter") || "all");
        const filterVal =
          rawVal === "protected" || rawVal === "waiver_lm" ? rawVal : "all";
        const curLayers = this.filterStore.getState().layers || {};
        this.filterStore.setState({
          landmarkFilter: filterVal,
          layers: {
            ...curLayers,
            landmarks: true,
          },
        });
      });
    });

    document.querySelectorAll("[data-rb-cat]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const rawVal = String(btn.getAttribute("data-rb-cat") || "all");
        const validCats = ["all", "church", "school", "business", "residence"];
        const catVal = validCats.includes(rawVal) ? rawVal : "all";
        const curLayers = this.filterStore.getState().layers || {};
        this.filterStore.setState({
          redBookCategory: catVal,
          layers: {
            ...curLayers,
            redBook1915: true,
          },
        });
      });
    });

    document.querySelectorAll("[data-holc-grade]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const rawVal = String(btn.getAttribute("data-holc-grade") || "all").toUpperCase();
        const gradeVal = ["A", "B", "C", "D"].includes(rawVal) ? rawVal : "all";
        const curLayers = this.filterStore.getState().layers || {};
        this.filterStore.setState({
          holcGradeFilter: gradeVal,
          layers: {
            ...curLayers,
            holc1937: true,
          },
        });
      });
    });

    const btnDrapeHolcScan = document.getElementById("btn-drape-holc-scan");
    if (btnDrapeHolcScan) {
      btnDrapeHolcScan.addEventListener("click", () => {
        const curState = this.filterStore.getState();
        const curLayers = curState.layers || {};
        const alreadyDraped = Boolean(
          curLayers.historicMap && curState.historicMapId === "holc_1937"
        );
        this.filterStore.setState({
          historicMapId: "holc_1937",
          layers: {
            ...curLayers,
            holc1937: true,
            historicMap: !alreadyDraped,
          },
        });
      });
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

    const selectHistoricMapId = document.getElementById("select-historic-map-id");
    if (selectHistoricMapId) {
      selectHistoricMapId.addEventListener("change", (e) => {
        const mapId = String(e.target.value || "sanborn_1907");
        const curLayers = this.filterStore.getState().layers || {};
        this.filterStore.setState({
          historicMapId: mapId,
          layers: {
            ...curLayers,
            historicMap: true,
          },
        });
        const mapEntry = HISTORICAL_MAP_CATALOG.find((m) => m.id === mapId);
        if (mapEntry && Array.isArray(mapEntry.focusCenter) && Array.isArray(mapEntry.bounds)) {
          const [[minLng, minLat], [maxLng, maxLat]] = mapEntry.bounds;
          const [fLng, fLat, fZoom] = mapEntry.focusCenter;
          const curCenter = this.mapController?.getMapCenterAndZoom?.();
          const cLng = Number(curCenter?.lng);
          const cLat = Number(curCenter?.lat);
          const outsideBounds =
            !Number.isFinite(cLng) ||
            !Number.isFinite(cLat) ||
            cLng < minLng - 0.008 ||
            cLng > maxLng + 0.008 ||
            cLat < minLat - 0.008 ||
            cLat > maxLat + 0.008;
          if (outsideBounds) {
            this.mapController?.flyToLocation?.(fLng, fLat, fZoom || 15.4);
          }
        }
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

    const btnToggleSpyglass = document.getElementById("btn-toggle-spyglass");
    if (btnToggleSpyglass) {
      btnToggleSpyglass.addEventListener("click", () => {
        const curState = this.filterStore.getState();
        const nextSpy = !curState.historicMapSpyglass;
        this.filterStore.setState({
          historicMapSpyglass: nextSpy,
          layers: {
            ...(curState.layers || {}),
            historicMap: true,
          },
        });
      });
    }

    const spyglassRadiusSlider = document.getElementById("slider-spyglass-radius");
    if (spyglassRadiusSlider) {
      spyglassRadiusSlider.addEventListener("input", (e) => {
        const val = Math.max(72, Math.min(360, parseInt(e.target.value, 10) || 152));
        const readout = document.getElementById("spyglass-radius-readout");
        if (readout) readout.textContent = `${val}px`;
        this.filterStore.setState({ historicMapSpyglassRadius: val });
        this.mapController?._renderSpyglassOverlay?.();
      });
    }

    const btnOpenSanbornBrowser = document.getElementById("btn-open-sanborn-browser");
    if (btnOpenSanbornBrowser) {
      btnOpenSanbornBrowser.addEventListener("click", () => {
        this.openSanbornBrowserModal();
      });
    }
    const btnOpenSanbornQuick = document.getElementById("btn-open-sanborn-browser-quick");
    if (btnOpenSanbornQuick) {
      btnOpenSanbornQuick.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        this.openSanbornBrowserModal();
      });
    }

    // Active Demolition, Sewer Disconnect & Texas DSHS Asbestos Watchlist Modal
    const btnOpenPermitWatchTop = document.getElementById("btn-open-permit-watch-modal");
    if (btnOpenPermitWatchTop) {
      btnOpenPermitWatchTop.addEventListener("click", () => {
        this.openPermitWatchModal("all");
      });
    }
    const btnOpenPermitWatchInline = document.getElementById("btn-open-permit-watch-inline");
    if (btnOpenPermitWatchInline) {
      btnOpenPermitWatchInline.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        this.openPermitWatchModal("all", false);
      });
    }
    const btnOpenDemoBenchmarksSidebar = document.getElementById(
      "btn-open-demo-benchmarks-sidebar"
    );
    if (btnOpenDemoBenchmarksSidebar) {
      btnOpenDemoBenchmarksSidebar.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        this.openPermitWatchModal("all", true);
      });
    }
    const btnCopyGrantDemoStats = document.getElementById("btn-copy-grant-demo-stats");
    if (btnCopyGrantDemoStats) {
      btnCopyGrantDemoStats.addEventListener("click", async (e) => {
        e.preventDefault();
        e.stopPropagation();
        const payload = this.mapController?.getPermitAlertsPayload?.();
        const txt = formatDemolitionBenchmarksGrantText(payload);
        try {
          await navigator.clipboard.writeText(txt);
        } catch (_err) {
          const ta = document.createElement("textarea");
          ta.value = txt;
          document.body.appendChild(ta);
          ta.select();
          document.execCommand("copy");
          ta.remove();
        }
        if (!btnCopyGrantDemoStats.dataset.origHtml) {
          btnCopyGrantDemoStats.dataset.origHtml = btnCopyGrantDemoStats.innerHTML;
        }
        const origHtml = btnCopyGrantDemoStats.dataset.origHtml;
        btnCopyGrantDemoStats.innerHTML = "&#10003; Copied Grant Stats";
        setTimeout(() => {
          btnCopyGrantDemoStats.innerHTML = origHtml;
        }, 2500);
      });
    }
    const permitWatchModal = document.getElementById("permit-watch-modal");
    const btnClosePermitWatchModal = document.getElementById("btn-close-permit-watch-modal");
    if (btnClosePermitWatchModal && permitWatchModal) {
      btnClosePermitWatchModal.addEventListener("click", () => {
        permitWatchModal.classList.add("hidden");
      });
    }
    if (permitWatchModal) {
      permitWatchModal.addEventListener("click", (e) => {
        if (e.target === permitWatchModal) {
          permitWatchModal.classList.add("hidden");
        }
      });
      permitWatchModal.querySelectorAll("[data-pw-filter]").forEach((pillBtn) => {
        pillBtn.addEventListener("click", () => {
          const sev = pillBtn.getAttribute("data-pw-filter") || "all";
          permitWatchModal
            .querySelectorAll("[data-pw-filter]")
            .forEach((b) => b.classList.remove("active"));
          pillBtn.classList.add("active");
          this._renderPermitWatchModalList(sev);
        });
      });
    }

    // Priority #5: HAHC COA & Designations Sub-Filters & Docket Modal
    document.querySelectorAll("[data-hahc-filter]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const rawVal = String(btn.getAttribute("data-hahc-filter") || "all");
        const validFilters = [
          "all",
          "active_agenda",
          "demolition_relocation",
          "addition_new",
          "designation",
          "denied_or_cor",
        ];
        const filterVal = validFilters.includes(rawVal) ? rawVal : "all";
        const curLayers = this.filterStore.getState().layers || {};
        this.filterStore.setState({
          hahcFilter: filterVal,
          layers: {
            ...curLayers,
            hahcTracker: true,
          },
        });
      });
    });

    const closeTopbarMoreDropdown = () => {
      const moreDd = document.getElementById("topbar-more-dropdown");
      const moreBtn = document.getElementById("btn-topbar-more");
      if (moreDd) moreDd.classList.remove("open");
      if (moreBtn) moreBtn.setAttribute("aria-expanded", "false");
    };

    const btnMoreHahc = document.getElementById("btn-more-hahc-tracker");
    if (btnMoreHahc) {
      btnMoreHahc.addEventListener("click", () => {
        closeTopbarMoreDropdown();
        this.openHahcTrackerModal();
      });
    }
    const btnOpenHahcInline = document.getElementById("btn-open-hahc-inline");
    if (btnOpenHahcInline) {
      btnOpenHahcInline.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        this.openHahcTrackerModal();
      });
    }
    const btnPwOpenHahc = document.getElementById("btn-pw-open-hahc-modal");
    if (btnPwOpenHahc) {
      btnPwOpenHahc.addEventListener("click", () => {
        if (permitWatchModal) permitWatchModal.classList.add("hidden");
        this.openHahcTrackerModal();
      });
    }
    const hahcModal = document.getElementById("hahc-tracker-modal");
    const btnCloseHahcModal = document.getElementById("btn-close-hahc-modal");
    if (btnCloseHahcModal && hahcModal) {
      btnCloseHahcModal.addEventListener("click", () => {
        hahcModal.classList.add("hidden");
      });
    }
    if (hahcModal) {
      hahcModal.addEventListener("click", (e) => {
        if (e.target === hahcModal) {
          hahcModal.classList.add("hidden");
        }
      });
      const btnHahcEnableLayer = document.getElementById("btn-hahc-enable-map-layer");
      if (btnHahcEnableLayer) {
        btnHahcEnableLayer.addEventListener("click", () => {
          const curLayers = this.filterStore.getState().layers || {};
          this.filterStore.setState({
            hahcFilter: "all",
            layers: {
              ...curLayers,
              hahcTracker: true,
            },
          });
          hahcModal.classList.add("hidden");
        });
      }
      const hahcDistSelect = document.getElementById("hahc-modal-district-select");
      if (hahcDistSelect) {
        hahcDistSelect.addEventListener("change", (e) => {
          this._hahcModalState.district = String(e.target.value || "all");
          this._renderHahcModalList();
        });
      }
      const hahcSearchInput = document.getElementById("hahc-modal-search");
      if (hahcSearchInput) {
        hahcSearchInput.addEventListener("input", (e) => {
          this._hahcModalState.search = String(e.target.value || "");
          this._renderHahcModalList();
        });
      }
      hahcModal.querySelectorAll("[data-hahc-modal-cat]").forEach((catBtn) => {
        catBtn.addEventListener("click", () => {
          const cat = String(catBtn.getAttribute("data-hahc-modal-cat") || "all");
          this._hahcModalState.category = cat;
          hahcModal
            .querySelectorAll("[data-hahc-modal-cat]")
            .forEach((b) => b.classList.toggle("active", b === catBtn));
          this._renderHahcModalList();
        });
      });
    }

    // Priority #4A: Guided Citywide Visual Chapters ("How Houston Was Built")
    const btnOpenCityChapters = document.getElementById("btn-open-city-chapters");
    if (btnOpenCityChapters) {
      btnOpenCityChapters.addEventListener("click", () => {
        if (this._activeChapterId) {
          this.exitCityChapters({ restoreState: true });
        } else {
          this.activateCityChapter(CITY_HISTORY_CHAPTERS[0].id);
        }
      });
    }
    const btnMoreCityChapters = document.getElementById("btn-more-city-chapters");
    if (btnMoreCityChapters) {
      btnMoreCityChapters.addEventListener("click", () => {
        closeTopbarMoreDropdown();
        this.activateCityChapter(this._activeChapterId || CITY_HISTORY_CHAPTERS[0].id);
      });
    }
    const btnChapterPrev = document.getElementById("btn-chapter-prev");
    if (btnChapterPrev) {
      btnChapterPrev.addEventListener("click", () => {
        const curIdx = CITY_HISTORY_CHAPTERS.findIndex((c) => c.id === this._activeChapterId);
        const prevIdx = curIdx <= 0 ? CITY_HISTORY_CHAPTERS.length - 1 : curIdx - 1;
        this.activateCityChapter(CITY_HISTORY_CHAPTERS[prevIdx].id);
      });
    }
    const btnChapterNext = document.getElementById("btn-chapter-next");
    if (btnChapterNext) {
      btnChapterNext.addEventListener("click", () => {
        const curIdx = CITY_HISTORY_CHAPTERS.findIndex((c) => c.id === this._activeChapterId);
        const nextIdx = curIdx < 0 || curIdx + 1 >= CITY_HISTORY_CHAPTERS.length ? 0 : curIdx + 1;
        this.activateCityChapter(CITY_HISTORY_CHAPTERS[nextIdx].id);
      });
    }
    const btnExitCityChapters = document.getElementById("btn-exit-city-chapters");
    if (btnExitCityChapters) {
      btnExitCityChapters.addEventListener("click", () => {
        this.exitCityChapters({ restoreState: true });
      });
    }

    // Priority #4B: Architect, Master Builder & Architectural Style Map Portfolios
    const btnOpenPortfoliosQuick = document.getElementById("btn-open-portfolios-quick");
    if (btnOpenPortfoliosQuick) {
      btnOpenPortfoliosQuick.addEventListener("click", () => {
        if (this._activePortfolio) {
          this.exitPortfolioMode({ restoreState: true });
        } else {
          const defPreset = FEATURED_PORTFOLIO_PRESETS[0];
          this.activatePortfolioMode(defPreset.kind, defPreset.query);
        }
      });
    }
    const btnMorePortfolios = document.getElementById("btn-more-portfolios");
    if (btnMorePortfolios) {
      btnMorePortfolios.addEventListener("click", () => {
        closeTopbarMoreDropdown();
        if (this._activePortfolio) {
          this.activatePortfolioMode(this._activePortfolio.kind, this._activePortfolio.query);
        } else {
          const defPreset = FEATURED_PORTFOLIO_PRESETS[0];
          this.activatePortfolioMode(defPreset.kind, defPreset.query);
        }
      });
    }
    const portfolioPresetSelect = document.getElementById("portfolio-preset-select");
    if (portfolioPresetSelect) {
      portfolioPresetSelect.addEventListener("change", (e) => {
        const raw = String(e.target.value || "");
        const colonIdx = raw.indexOf(":");
        if (colonIdx > 0) {
          const pKind = raw.slice(0, colonIdx) === "style" ? "style" : "architect";
          const pQuery = raw.slice(colonIdx + 1).trim();
          if (pQuery) {
            this.activatePortfolioMode(pKind, pQuery);
          }
        }
      });
    }
    const btnPortfolioFitAll = document.getElementById("btn-portfolio-fit-all");
    if (btnPortfolioFitAll) {
      btnPortfolioFitAll.addEventListener("click", () => {
        if (this._activePortfolio && this.mapController) {
          this.mapController.setPortfolioWorks(
            this._activePortfolio.filteredWorks || this._activePortfolio.works || [],
            this._activePortfolio.activeIndex ?? -1,
            { fit: true }
          );
        }
      });
    }
    const btnExitPortfolio = document.getElementById("btn-exit-portfolio");
    if (btnExitPortfolio) {
      btnExitPortfolio.addEventListener("click", () => {
        this.exitPortfolioMode({ restoreState: true });
      });
    }
    document.querySelectorAll("[data-portfolio-filter]").forEach((pBtn) => {
      pBtn.addEventListener("click", () => {
        const subFilter = String(pBtn.getAttribute("data-portfolio-filter") || "all");
        if (!this._activePortfolio) return;
        this._activePortfolio.subFilter = subFilter;
        this._activePortfolio.activeIndex = -1;
        document.querySelectorAll("[data-portfolio-filter]").forEach((b) => {
          b.classList.toggle("active", b === pBtn);
        });
        this._renderPortfolioWorksList({ fitBounds: false });
      });
    });

    const sanbornModal = document.getElementById("sanborn-browser-modal");
    const btnCloseSanbornModal = document.getElementById("btn-close-sanborn-modal");
    if (btnCloseSanbornModal && sanbornModal) {
      btnCloseSanbornModal.addEventListener("click", () => {
        sanbornModal.classList.add("hidden");
      });
    }
    if (sanbornModal) {
      const backdrop = sanbornModal.querySelector(".modal-backdrop");
      if (backdrop) {
        backdrop.addEventListener("click", () => {
          sanbornModal.classList.add("hidden");
        });
      }
    }

    const btnActivate1907Mosaic = document.getElementById("btn-sanborn-activate-1907-mosaic");
    if (btnActivate1907Mosaic) {
      btnActivate1907Mosaic.addEventListener("click", () => {
        const curLayers = this.filterStore.getState().layers || {};
        this.filterStore.setState({
          historicMapId: "sanborn_1907",
          layers: {
            ...curLayers,
            historicMap: true,
          },
        });
        const vp = this.mapController ? this.mapController.getCurrentViewport() : null;
        if (!vp || vp.zoom < 14) {
          this.mapController?.flyToLocation({
            lng: -95.3632,
            lat: 29.7595,
            zoom: 15.6,
          });
        }
        if (sanbornModal) sanbornModal.classList.add("hidden");
      });
    }

    const selectSanbornVol = document.getElementById("select-sanborn-volume");
    const inputSanbornFilter = document.getElementById("input-sanborn-filter");
    if (selectSanbornVol) {
      selectSanbornVol.addEventListener("change", () => {
        this._renderSanbornBrowserSheets();
      });
    }
    if (inputSanbornFilter) {
      inputSanbornFilter.addEventListener("input", () => {
        this._renderSanbornBrowserSheets();
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

    // Inspector Drawer Close, Desktop Wide-Mode Toggle & Mobile 3-State Sheet Controls
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

    const savedWidePref = localStorage.getItem("atlas_inspector_wide") === "1";
    this._inspectorWideMode = savedWidePref;
    this._mobileSheetMode = "half";
    this._activeInspectorTab = "overview";
    this._syncInspectorLayoutControls();

    const btnToggleWide = document.getElementById("btn-toggle-inspector-wide");
    if (btnToggleWide) {
      btnToggleWide.addEventListener("click", () => {
        this._inspectorWideMode = !this._inspectorWideMode;
        try {
          localStorage.setItem("atlas_inspector_wide", this._inspectorWideMode ? "1" : "0");
        } catch (_e) {}
        this._syncInspectorLayoutControls();
      });
    }

    const cycleMobileSheet = () => {
      const order = ["half", "full", "peek"];
      const nextIdx = (order.indexOf(this._mobileSheetMode || "half") + 1) % order.length;
      this._setMobileSheetMode(order[nextIdx]);
    };

    const btnCycleMobileSheet = document.getElementById("btn-cycle-mobile-sheet");
    if (btnCycleMobileSheet) {
      btnCycleMobileSheet.addEventListener("click", cycleMobileSheet);
    }

    const mobileDragHandle = document.getElementById("inspector-mobile-drag-handle");
    if (mobileDragHandle) {
      mobileDragHandle.addEventListener("click", cycleMobileSheet);
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
          building_name: document.getElementById("corr-building-name")?.value || "",
          hcad_num: document.getElementById("corr-hcad-num")?.value || "",
          current_year_built: document.getElementById("corr-current-year")?.value || "",
          suggested_year_built: document.getElementById("corr-suggested-year")?.value || "",
          historic_district: document.getElementById("corr-district")?.value || "",
          source_type: document.getElementById("corr-source-type")?.value || "Houston City Directory",
          architect: document.getElementById("corr-style-arch")?.value || "",
          builder: document.getElementById("corr-builder")?.value || "",
          permit_info: document.getElementById("corr-permit-info")?.value || "",
          source_citation: document.getElementById("corr-citation")?.value || "",
          footprint_issue: document.getElementById("corr-footprint-issue")?.value || "",
          footprint_notes: document.getElementById("corr-footprint-notes")?.value || "",
          satellite_url: this._activeCorrectionSatelliteUrl || "",
          photo_url: document.getElementById("corr-photo-url")?.value || "",
          photo_year: document.getElementById("corr-photo-year")?.value || "",
          photo_caption: document.getElementById("corr-photo-caption")?.value || "",
        };

        const rawSuggYr = String(payload.suggested_year_built || "").trim();
        const isDemolishedVacant = isDemolishedVacantOverrideInput({
          yearRaw: rawSuggYr,
          footprintIssue: payload.footprint_issue,
          footprintNotes: payload.footprint_notes,
          sourceType: payload.source_type,
          sourceCitation: payload.source_citation,
        });
        const yrNum = rawSuggYr !== "" ? Number(rawSuggYr) : NaN;
        const isValidStandingYear =
          Number.isFinite(yrNum) &&
          Number.isInteger(yrNum) &&
          yrNum >= MIN_VALID_YEAR &&
          yrNum <= MAX_VALID_YEAR;

        if (!isDemolishedVacant && !isValidStandingYear) {
          if (feedbackEl) {
            feedbackEl.innerHTML = `<strong>Please enter a Corrected Year Built between ${MIN_VALID_YEAR} and ${MAX_VALID_YEAR}, or enter <code>0</code> / select "Building demolished - lot currently vacant" to hide a demolished building footprint.</strong>`;
            feedbackEl.classList.remove("hidden");
          }
          return;
        }

        if (isDemolishedVacant && !String(payload.footprint_notes).trim()) {
          payload.footprint_notes = "Building demolished; lot currently vacant.";
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
              (!res.override.is_building_override
                ? this.mapController.curatedOverrides[res.override.hcad_num]
                : null) ||
              {};
            const canUseSelectedBuildingGeom =
              !this.mapController.selectedFeatureProps?.is_tax_parcel &&
              !this.mapController.selectedFeatureProps?.is_boundary_feature &&
              this.mapController.selectedFeatureGeometry?.type !== "Point";
            const selectedBuildingGeom = canUseSelectedBuildingGeom
              ? this.mapController.selectedFeatureGeometry
              : null;
            const rawBuildingsPool =
              this.mapController._rawBuildingsData || this.mapController.buildingsData || [];
            const baselineBldFeat = rawBuildingsPool.find((f) => {
              const p = f?.properties || {};
              if (res.override.is_building_override || ovKey.includes("#")) {
                return String(p.building_id || p.id || "") === ovKey;
              }
              return String(p.hcad_num || "") === String(res.override.hcad_num || "");
            });
            const fallbackGeometry =
              selectedBuildingGeom || baselineBldFeat?.geometry || null;

            this.mapController.curatedOverrides[ovKey] = mergeOverrideRecord(
              existingOv,
              res.override,
              {
                overrideKey: ovKey,
                fallbackGeometry,
              }
            );
            this.mapController._refreshCuratedOverridesSource();
            this._mergeCuratedOverridesIntoSearchIndex();
            this._enrichSearchIndexWithOverlayMetadata();
            if (res.override.suppress_only) {
              this.mapController.clearSelection?.();
            } else if (this.mapController.selectedFeatureProps) {
              const nextGeom =
                this.mapController.curatedOverrides[ovKey]?.geometry ||
                fallbackGeometry ||
                this.mapController.selectedFeatureGeometry;
              this.mapController.highlightAndInspectFeature(
                this.mapController.selectedFeatureProps,
                nextGeom
              );
            }
          }

          if (feedbackEl) {
            if (res.override?.suppress_only) {
              feedbackEl.innerHTML = `<strong>&#10003; Published Live (Admin Approved)!</strong> <strong>${payload.address || payload.hcad_num}</strong> is now marked <strong>Demolished / Lot Currently Vacant &#10003;</strong> (ghost building footprint hidden on the live map) and dispatched to the Google Sheet.`;
            } else {
              feedbackEl.innerHTML = `<strong>&#10003; Published Live (Admin Approved)!</strong> <strong>${payload.address || payload.hcad_num}</strong> is now set to <strong>Built ${payload.suggested_year_built} &#10003;</strong> on the live map and dispatched to the Google Sheet.`;
            }
            feedbackEl.classList.remove("hidden");
          }
        } catch (err) {
          btnAdminApproveDirect.disabled = false;
          if (feedbackEl) {
            feedbackEl.innerHTML = `<strong>&#10005; Admin Approval Error:</strong> ${err.message || err}`;
            feedbackEl.classList.remove("hidden");
          }
        }
      });
    }

    if (btnCloseCorrModal && corrModal) {
      btnCloseCorrModal.addEventListener("click", () => corrModal.classList.add("hidden"));
    }

    const footprintSelectEl = document.getElementById("corr-footprint-issue");
    const footprintNotesEl = document.getElementById("corr-footprint-notes");
    const corrSuggYrEl = document.getElementById("corr-suggested-year");

    if (footprintSelectEl) {
      footprintSelectEl.addEventListener("change", () =>
        (this._syncFootprintIssueUi || HoustonAtlasApp.prototype._syncFootprintIssueUi).call(
          this,
          true
        )
      );
    }
    if (corrSuggYrEl) {
      corrSuggYrEl.addEventListener("input", () => {
        const val = String(corrSuggYrEl.value || "").trim();
        if (
          val === "0" &&
          footprintSelectEl &&
          (!footprintSelectEl.value || footprintSelectEl.value === DEMOLISHED_REPLACED_ISSUE)
        ) {
          footprintSelectEl.value = DEMOLISHED_VACANT_ISSUE;
          (this._syncFootprintIssueUi || HoustonAtlasApp.prototype._syncFootprintIssueUi).call(
            this,
            false
          );
        } else if (
          footprintSelectEl &&
          footprintSelectEl.value === DEMOLISHED_VACANT_ISSUE &&
          val !== "" &&
          val !== "0"
        ) {
          const yrNum = Number(val);
          if (
            Number.isFinite(yrNum) &&
            Number.isInteger(yrNum) &&
            yrNum >= MIN_VALID_YEAR &&
            yrNum <= MAX_VALID_YEAR
          ) {
            footprintSelectEl.value = DEMOLISHED_REPLACED_ISSUE;
            (this._syncFootprintIssueUi || HoustonAtlasApp.prototype._syncFootprintIssueUi).call(
              this,
              false
            );
          }
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
          builder: document.getElementById("corr-builder")?.value || "",
          permit_info: document.getElementById("corr-permit-info")?.value || "",
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

        const rawSuggYr = String(payload.suggested_year_built).trim();
        const isDemolishedVacant = isDemolishedVacantOverrideInput({
          yearRaw: rawSuggYr,
          footprintIssue: payload.footprint_issue,
          footprintNotes: payload.footprint_notes,
          sourceType: payload.source_type,
          sourceCitation: payload.source_citation,
        });
        if (rawSuggYr !== "" && !isDemolishedVacant) {
          const yrNum = Number(rawSuggYr);
          if (
            !Number.isFinite(yrNum) ||
            !Number.isInteger(yrNum) ||
            yrNum < MIN_VALID_YEAR ||
            yrNum > MAX_VALID_YEAR
          ) {
            if (feedbackEl) {
              feedbackEl.innerHTML = `<strong>Please enter a construction year between ${MIN_VALID_YEAR} and ${MAX_VALID_YEAR}, or enter <code>0</code> to mark the building demolished and the lot currently vacant.</strong>`;
              feedbackEl.classList.remove("hidden");
            }
            if (corrSuggYrEl) corrSuggYrEl.focus();
            return;
          }
        }
        if (isDemolishedVacant) {
          if (!String(payload.footprint_issue).trim()) {
            payload.footprint_issue = DEMOLISHED_VACANT_ISSUE;
          }
          if (!String(payload.footprint_notes).trim()) {
            payload.footprint_notes = "Building demolished; lot currently vacant.";
          }
        }

        const hasYearChange = rawSuggYr !== "" || isDemolishedVacant;
        const hasNameUpdate = Boolean(parsedPrimaryBldName);
        const hasBuilderOrPermit = Boolean(
          String(payload.builder).trim() || String(payload.permit_info).trim()
        );
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
          !hasBuilderOrPermit &&
          !hasFootprintReport &&
          !hasCitation &&
          !hasPhoto &&
          !hasDeedContribution
        ) {
          if (feedbackEl) {
            feedbackEl.innerHTML = `<strong>Please enter a Corrected Year Built (or <code>0</code> if demolished &amp; vacant), Building Name / Alias, Builder / Contractor, Subdivision Deed Restriction PDF / Clerk File #, select a Building Footprint Issue, or provide historical notes before submitting.</strong>`;
            feedbackEl.classList.remove("hidden");
          }
          return;
        }

        if (
          hasFootprintReport &&
          !isDemolishedVacant &&
          !String(payload.footprint_notes).trim() &&
          !hasCitation
        ) {
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
          const summaryDesc = isDemolishedVacant
            ? `Demolished / Lot Currently Vacant (<em>hide ghost footprint · Year 0</em>)`
            : hasFootprintReport && hasYearChange
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
        this._syncPhotoExclusionRegistries();
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
    const isBuilderFilter =
      hdrLow.startsWith("builder:") ||
      hdrLow.startsWith("builder / contractor:") ||
      hdrLow.startsWith("contractor:");
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
        builder,
        permit,
        reloc,
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
          isBuilderFilter ||
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
          if (
            qTokens.length >= 2 &&
            !isPlat &&
            qTokens.some((tok) => placeName.includes(tok) || altNames.some((an) => an.includes(tok))) &&
            qTokens.every((tok) => `${placeName} ${altNames.join(" ")} ${cat} ${sub}`.includes(tok))
          ) {
            return 86;
          }
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
      } else if (isBuilderFilter) {
        if (builder && builder.includes(q)) {
          score = builder === q ? 124 : 108;
        } else if (arch && arch.includes(q)) {
          score = 96;
        }
      } else if (isArchitectFilter) {
        if (arch && arch.includes(q)) {
          score = arch === q ? 120 : 105;
        } else if (builder && builder.includes(q)) {
          score = builder === q ? 115 : 100;
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
        if (builder && builder.includes(q)) {
          score = Math.max(score, builder === q ? 118 : 100);
        }
        if (permit && permit.includes(q)) {
          score = Math.max(score, 102);
        }
        if (reloc && reloc.includes(q)) {
          score = Math.max(score, 98);
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

        // Multi-word token fallback across address + names
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
      if (
        item.type === "landmark" ||
        item.type === "good_brick" ||
        item.type === "nrhp_property" ||
        item.landmark_code ||
        item.good_brick_years
      ) {
        score += 8;
      }
      if (hasCustomName) score += 5;
      const subBldKey = String(item.building_id || item.id || "");
      if (subBldKey.includes("#")) {
        const subOv = this.mapController?.curatedOverrides?.[subBldKey];
        if (/(?:_main(?:_\d+)?|#b1)$/i.test(subBldKey)) {
          score += 4;
        } else if (subOv?.skip_search_index || /(?:_aux(?:_\d+)?|_shop_\d+|_shed_\d+|_portable)/i.test(subBldKey)) {
          score -= 10;
        }
      }

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
      const rawKey = String(p.building_id || p.id || p.hcad_num || "").trim();
      const key = rawKey.replace(/#fp_\d+$/, "");
      const hcadKey = String(p.hcad_num || "").trim();
      if ((key && seenKeys.has(key)) || (hcadKey && !key.includes("#") && seenKeys.has(hcadKey))) {
        continue;
      }

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
        building_id: String(p.building_id || (key.includes("#") ? key : "")).trim(),
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
        builder: p.builder || "",
        builder_source: p.builder_source || "",
        engineers: p.engineers || "",
        original_cost: p.original_cost || "",
        original_permit: p.original_permit || "",
        yr_remodel: p.yr_remodel || 0,
        eff_year_built: p.eff_year_built || 0,
        was_relocated: Boolean(p.was_relocated),
        relocation_note: p.relocation_note || "",
        hist_contract: p.hist_contract || null,
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
          p.builder ? `Built by ${p.builder}` : "",
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
    const rawMapBounds = this.mapController?.map?.getBounds?.() || null;
    const vp = rawMapBounds
      ? {
          west: Number(rawMapBounds.getWest()),
          east: Number(rawMapBounds.getEast()),
          south: Number(rawMapBounds.getSouth()),
          north: Number(rawMapBounds.getNorth()),
        }
      : this.mapController?.getCurrentViewport
      ? this.mapController.getCurrentViewport()
      : null;
    const seenLinkedAddrs = new Set();
    for (const m of scoredMatches) {
      const mAddr = String(m.item?.address || "").replace(/\s+/g, " ").trim().toUpperCase();
      if (mAddr && (m.item?.hcad_num || m.item?.building_id)) seenLinkedAddrs.add(mAddr);
      if (m.item?.building_id) seenKeys.add(String(m.item.building_id).trim());
    }
    for (const item of this.searchIndex) {
      const lc = this._getSearchLowercaseCache(item);
      const { key, hcadKey } = lc;
      const isSubEntity = Boolean(key && (key.includes("#") || key.startsWith("school_")));
      if ((key && seenKeys.has(key)) || (hcadKey && !isSubEntity && seenKeys.has(hcadKey))) {
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
        if (item.building_id) seenKeys.add(String(item.building_id).trim());
        if (hcadKey && !isSubEntity) seenKeys.add(hcadKey);
        const itemAddr = String(item.address || "").replace(/\s+/g, " ").trim().toUpperCase();
        if (itemAddr && (item.hcad_num || item.building_id)) seenLinkedAddrs.add(itemAddr);
        scoredMatches.push({ item, score: sc });
      }
    }

    // Third: Lazy-load and match countywide street address shards (1.22M Harris County addresses)
    // whenever the query begins with a street number (e.g. "1407 Enid", "3000 Caroline", "413 E 13th").
    const addrNumMatch = qAddr.match(/^(\d+[a-z]?)(?:\s+(.*))?$/i);
    if (addrNumMatch) {
      const typedNum = addrNumMatch[1].toUpperCase();
      const typedDigits = typedNum.replace(/\D/g, "");
      const typedStreetPart = String(addrNumMatch[2] || "")
        .trim()
        .toUpperCase();
      const streetTokens = typedStreetPart.split(/\s+/).filter(Boolean);
      if (typedDigits.length >= 1 && !typedDigits.startsWith("0")) {
        const shardKey = typedDigits.slice(0, 2).padEnd(2, "0");
        this._addressShardCache = this._addressShardCache || {};
        this._addressShardPending = this._addressShardPending || {};
        const shardData = this._addressShardCache[shardKey];
        if (!shardData && !this._addressShardPending[shardKey]) {
          this._addressShardPending[shardKey] = fetch(`public/data/address_shards/addr_${shardKey}.json?v=20261029a`)
            .then((r) => (r.ok ? r.json() : {}))
            .then((data) => {
              this._addressShardCache[shardKey] = data || {};
              delete this._addressShardPending[shardKey];
              const curInputVal = String(searchInput?.value || "").trim();
              if (curInputVal.toLowerCase() === rawQuery.trim().toLowerCase()) {
                this._runSearchQuery(rawQuery, customHeaderLabel);
              }
            })
            .catch(() => {
              this._addressShardCache[shardKey] = {};
              delete this._addressShardPending[shardKey];
            });
        } else if (shardData) {
          let shardAdded = 0;
          for (const [stName, rows] of Object.entries(shardData)) {
            const normStName = String(stName).replace(/\s+/g, " ").trim();
            if (
              streetTokens.length > 0 &&
              !streetTokens.every((tok) => normStName.includes(tok))
            ) {
              continue;
            }
            const hasLinkedRowForNum = rows.some((r) => {
              if (String(r[0]) !== typedNum) return false;
              const rH = String(r[4] || "").trim();
              const rB = String(r[6] || "").trim();
              if (!rH && !rB) return false;
              return !this._isSuppressedByCuratedOverride?.(rB, rH);
            });
            for (const row of rows) {
              const hnum = String(row[0]);
              const exactNum = hnum === typedNum;
              const prefixNum = !exactNum && hnum.startsWith(typedNum);
              if (!exactNum && !prefixNum) continue;
              const lon = Number(row[1]);
              const lat = Number(row[2]);
              const yr = Number(row[3]) || 0;
              const hcad = String(row[4] || "");
              const bldName = String(row[5] || "");
              const rowBldId = String(row[6] || "").trim();
              if (this._isSuppressedByCuratedOverride?.(rowBldId, hcad)) continue;
              const fullAddr = `${hnum} ${normStName}`;
              if (!hcad && !rowBldId && (seenLinkedAddrs.has(fullAddr.toUpperCase()) || (exactNum && hasLinkedRowForNum))) {
                continue;
              }
              const dedupKey = rowBldId || hcad;
              if (dedupKey && seenKeys.has(dedupKey)) continue;
              const addrId = rowBldId || hcad || `addr_${fullAddr}`;
              if (seenKeys.has(addrId)) continue;
              seenKeys.add(addrId);
              if (hcad && !rowBldId) seenKeys.add(hcad);
              if (hcad || rowBldId) seenLinkedAddrs.add(fullAddr.toUpperCase());

              let sc = exactNum ? 118 : 92;
              if (streetTokens.length > 0) {
                if (normStName === typedStreetPart || normStName.startsWith(typedStreetPart + " ")) {
                  sc += 12;
                } else if (normStName.startsWith(streetTokens[0])) {
                  sc += 6;
                }
              }
              if (bldName) sc += 4;
              if (hcad.length === 13 && yr >= 1836) sc += 3;
              if (rowBldId.endsWith("_main")) {
                sc += 2;
              } else if (/(?:_aux_|_shop_|_shed_|_portable)/i.test(rowBldId)) {
                sc -= 8;
              }

              scoredMatches.push({
                score: sc,
                item: {
                  type: "building",
                  id: rowBldId || (hcad ? `${hcad}#b1` : addrId),
                  building_id: rowBldId,
                  hcad_num: hcad,
                  label: bldName ? `${bldName} (${fullAddr})` : fullAddr,
                  building_name: bldName,
                  address: fullAddr,
                  sublabel: [
                    bldName ? fullAddr : "Harris County Street Address",
                    hcad ? `HCAD ${hcad}` : "",
                    yr >= 1836 ? `Built ${yr}` : "",
                  ]
                    .filter(Boolean)
                    .join(" • "),
                  category: yr >= 1836 ? `Built ${yr}` : "Property Address",
                  year_built: yr,
                  lon,
                  lat,
                  zoom: 17.6,
                },
              });
              shardAdded++;
              if (shardAdded >= 60) break;
            }
            if (shardAdded >= 60) break;
          }
        }
      }
    }

    scoredMatches.sort((a, b) => b.score - a.score);
    const totalMatchCount = scoredMatches.length;
    const matches = scoredMatches.slice(0, 25).map((m) => m.item);

    if (!matches.length) {
      searchResults.innerHTML = `<div class="search-empty">No matching addresses, neighborhoods, wards, subdivisions, architects, builders, styles, or landmarks found for "${rawQuery}".</div>`;
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

    let portfolioCtaSpec = null;
    const cleanQ = String(rawQuery || "").trim();
    const lowQ = cleanQ.toLowerCase();
    if (cleanQ.length >= 4 && !/^\d/.test(cleanQ)) {
      const hdrLow = String(customHeaderLabel || "").toLowerCase();
      const presetMatch = FEATURED_PORTFOLIO_PRESETS.find(
        (p) =>
          p.query.toLowerCase() === lowQ ||
          p.label.toLowerCase() === lowQ ||
          (lowQ.length >= 5 && p.query.toLowerCase().includes(lowQ))
      );
      if (hdrLow.startsWith("architect:") || hdrLow.startsWith("builder:")) {
        portfolioCtaSpec = { kind: "architect", query: cleanQ };
      } else if (hdrLow.startsWith("style:")) {
        portfolioCtaSpec = { kind: "style", query: cleanQ };
      } else if (presetMatch) {
        portfolioCtaSpec = { kind: presetMatch.kind, query: presetMatch.query };
      } else {
        const archHits = matches.filter(
          (m) =>
            (m.architect && String(m.architect).toLowerCase().includes(lowQ)) ||
            (m.builder && String(m.builder).toLowerCase().includes(lowQ))
        );
        const styleHits = matches.filter(
          (m) => m.style && String(m.style).toLowerCase().includes(lowQ)
        );
        if (archHits.length >= 2) {
          portfolioCtaSpec = { kind: "architect", query: cleanQ };
        } else if (styleHits.length >= 3) {
          portfolioCtaSpec = { kind: "style", query: cleanQ };
        }
      }
    }
    const portfolioCtaHtml = portfolioCtaSpec
      ? `<button
          type="button"
          class="portfolio-dropdown-cta"
          id="btn-search-open-portfolio"
          data-portfolio-kind="${portfolioCtaSpec.kind}"
          data-portfolio-query="${portfolioCtaSpec.query.replace(/"/g, "&quot;")}"
        >
          <span>&#127959; Map All Works: <strong>${portfolioCtaSpec.query}</strong> (Extant + Lost Houston)</span>
          <span class="mono">Portfolio &rarr;</span>
        </button>`
      : "";

    searchResults.innerHTML =
      headerBanner +
      portfolioCtaHtml +
      matches
        .map((m, i) => {
          const isPlace = Boolean(
            m.is_neighborhood_entry ||
              m.type === "neighborhood" ||
              m.type === "platted_subdivision" ||
              m.type === "super_neighborhood" ||
              m.type === "historic_ward" ||
              m.type === "historic_district" ||
              m.type === "nrhp_district" ||
              m.type === "potential_historic_district" ||
              m.type === "cultural_district" ||
              m.type === "holc_1937_zones" ||
              m.type === "holc_1937_zone" ||
              m.type === "historical_waterway" ||
              m.type === "historical_railroad"
          );
          const isLostHouston = Boolean(
            m.type === "lost_houston" || m.overlay_layer === "lost_houston"
          );
          const isRedBook = Boolean(
            m.type === "red_book_1915" || m.overlay_layer === "red_book_1915"
          );
          const isHahc = Boolean(
            m.type === "hahc_coa" || m.overlay_layer === "hahc_coa_filings"
          );
          const isNrhpProperty = Boolean(m.type === "nrhp_property");
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
            isPlace || isLostHouston || isRedBook || isHahc ? "is-place-boundary-item" : ""
          }" data-idx="${i}">
            <div class="search-result-main">
              <span class="search-result-title">${
                isHahc
                  ? `&#127963; ${dispLabel}`
                  : isRedBook
                  ? `&#128214; ${dispLabel}`
                  : isLostHouston
                  ? `&#127963; ${dispLabel}`
                  : isPlace
                  ? `&#128506; ${dispLabel}`
                  : isNrhpProperty
                  ? `&#127963; ${dispLabel}`
                  : dispLabel
              }</span>
              <span class="search-result-cat">${m.category}</span>
            </div>
            <div class="search-result-sub">${dispSub}</div>
          </button>`;
        })
        .join("");
    searchResults.classList.remove("hidden");

    const btnSearchOpenPortfolio = document.getElementById("btn-search-open-portfolio");
    if (btnSearchOpenPortfolio) {
      btnSearchOpenPortfolio.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const pKind = btnSearchOpenPortfolio.getAttribute("data-portfolio-kind") || "architect";
        const pQuery = btnSearchOpenPortfolio.getAttribute("data-portfolio-query") || "";
        searchResults.classList.add("hidden");
        if (pQuery) {
          this.activatePortfolioMode(pKind, pQuery);
        }
      });
    }

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
          if (chosen.type === "hahc_coa" || chosen.overlay_layer === "hahc_coa_filings") {
            const curState = this.filterStore.getState();
            const patch = {
              layers: {
                ...(curState.layers || {}),
                hahcTracker: true,
              },
            };
            if (curState.hahcFilter && curState.hahcFilter !== "all") {
              patch.hahcFilter = "all";
            }
            this.filterStore.setState(patch);
            this.mapController.inspectHahcById(chosen.id, {
              flyTo: true,
              zoom: chosen.zoom || 17.5,
            });
            return;
          }
          if (chosen.type === "red_book_1915" || chosen.overlay_layer === "red_book_1915") {
            const curState = this.filterStore.getState();
            const patch = {
              layers: {
                ...(curState.layers || {}),
                redBook1915: true,
              },
            };
            if (curState.redBookCategory && curState.redBookCategory !== "all") {
              patch.redBookCategory = "all";
            }
            this.filterStore.setState(patch);
            this.mapController.inspectRedBookById(chosen.id, {
              flyTo: true,
              zoom: chosen.zoom || 17.5,
            });
            return;
          }
          if (chosen.type === "lost_houston" || chosen.overlay_layer === "lost_houston") {
            this.filterStore.setLayerVisibility("lostHouston", true);
            this.mapController.highlightBoundaryByIdOrName(
              {
                id: chosen.id || "",
                name: chosen.name || chosenLabel,
                layerKey: "lost_houston",
              },
              {
                layerKey: "lost_houston",
                fitBounds: true,
                openInspector: true,
              }
            );
            return;
          }
          if (
            chosen.is_neighborhood_entry ||
            chosen.type === "neighborhood" ||
            chosen.type === "platted_subdivision" ||
            chosen.type === "super_neighborhood" ||
            chosen.type === "historic_ward" ||
            chosen.type === "historic_district" ||
            chosen.type === "nrhp_district" ||
            chosen.type === "potential_historic_district" ||
            chosen.type === "cultural_district" ||
            chosen.type === "holc_1937_zone" ||
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
                : rawLayer === "potentialHistoricDistricts"
                ? "potential_historic_districts"
                : rawLayer === "culturalDistricts"
                ? "cultural_districts"
                : rawLayer === "holc1937"
                ? "holc_1937_zones"
                : rawLayer ||
                  (chosen.type === "super_neighborhood"
                    ? "super_neighborhoods"
                    : chosen.type === "historic_ward"
                    ? "historic_wards"
                    : chosen.type === "platted_subdivision"
                    ? "platted_subdivisions"
                    : chosen.type === "historic_district"
                    ? "historic_districts"
                    : chosen.type === "nrhp_district"
                    ? "nrhp_districts"
                    : chosen.type === "potential_historic_district"
                    ? "potential_historic_districts"
                    : chosen.type === "cultural_district"
                    ? "cultural_districts"
                    : chosen.type === "holc_1937_zone"
                    ? "holc_1937_zones"
                    : chosen.type === "historical_waterway"
                    ? "historical_waterways"
                    : chosen.type === "historical_railroad"
                    ? "historical_railroads"
                    : "neighborhoods");
            if (chosen.type === "historic_ward" && chosen.era_year) {
              this.filterStore.setState({ wardEra: Number(chosen.era_year) });
            }
            if (layerKey === "holc_1937_zones" && this.filterStore.getState().holcGradeFilter !== "all") {
              this.filterStore.setState({ holcGradeFilter: "all" });
            }
            const layerKeyToStoreKey = {
              historical_waterways: "historicalWaterways",
              historical_railroads: "historicalRailroads",
              historic_districts: "historicDistricts",
              heritage_districts: "heritageDistricts",
              nrhp_districts: "nrhpDistricts",
              potential_historic_districts: "potentialHistoricDistricts",
              cultural_districts: "culturalDistricts",
              holc_1937_zones: "holc1937",
              neighborhoods: "neighborhoods",
              platted_subdivisions: "plattedSubdivisions",
              super_neighborhoods: "superNeighborhoods",
              historic_wards: "historicWards",
            };
            if (layerKeyToStoreKey[layerKey]) {
              this.filterStore.setLayerVisibility(layerKeyToStoreKey[layerKey], true);
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

          if (chosen.type === "nrhp_property") {
            this.filterStore.setLayerVisibility("nrhpProperties", true);
            this.mapController?.clearSelection?.();
            const fullNrhpFeat = (
              this.mapController?.overlaysData?.nrhp_properties?.features || []
            ).find(
              (f) =>
                f.properties &&
                ((chosen.id && f.properties.id === chosen.id) ||
                  (chosen.refnum && f.properties.refnum === chosen.refnum))
            );
            const fullNrhpProps = fullNrhpFeat?.properties || chosen;
            if (chosen.is_demolished_nrhp || (!chosen.hcad_num && !chosen.building_id)) {
              this.mapController.flyToLocation({
                lng: chosen.lon,
                lat: chosen.lat,
                zoom: chosen.zoom || 17.5,
              });
              this.renderInspectorDrawer({
                ...fullNrhpProps,
                overlay_layer: "nrhp_properties",
                is_nrhp_marker: true,
                lng: chosen.lon,
                lat: chosen.lat,
              });
              return;
            }
            this.mapController.flyToLocation({
              lng: chosen.lon,
              lat: chosen.lat,
              zoom: chosen.zoom || 17.5,
              hcadNum: chosen.hcad_num || "",
              featureId: chosen.building_id || "",
            });
            if (document.getElementById("inspector-drawer")?.classList.contains("hidden")) {
              this.renderInspectorDrawer({
                id: chosen.building_id || chosen.id || "",
                building_id: chosen.building_id || "",
                address: chosen.address || "",
                building_name: chosen.building_name || chosen.label,
                landmark_name: chosen.label,
                alt_names: chosen.alt_names || [],
                year_built: chosen.year_built || 0,
                hcad_num: chosen.hcad_num || "",
                historic_district: chosen.historic_district || "",
                super_neighborhood: chosen.super_neighborhood || "",
                nrhp_refnum: chosen.refnum || "",
                nrhp_resname: chosen.label || "",
                nrhp_thc_url: chosen.thc_atlas_url || "",
                nrhp_pdf_url: chosen.nomination_pdf_url || "",
                _nrhp_property_record: fullNrhpProps,
              });
            }
            return;
          }

          const chosenIdStr = String(chosen.id || "").trim();
          const targetFeatureId = String(
            chosen.building_id || (!chosenIdStr.startsWith("school_") ? chosenIdStr : "") || ""
          ).trim();
          this.mapController.flyToLocation({
            lng: chosen.lon,
            lat: chosen.lat,
            zoom: chosen.zoom || 17.2,
            hcadNum: chosen.hcad_num || "",
            featureId: targetFeatureId || chosenIdStr,
            addressQuery: chosen.address || "",
            fallbackYearBuilt: Number(chosen.year_built) || 0,
            fallbackBuildingName:
              chosen.building_name || (chosenIdStr.startsWith("school_") ? chosen.label : "") || "",
          });
          if (
            (chosen.type === "building" ||
              chosen.type === "landmark" ||
              chosen.type === "good_brick") &&
            document.getElementById("inspector-drawer")?.classList.contains("hidden")
          ) {
            const baseDrawerProps = {
              id: targetFeatureId || chosenIdStr || chosen.hcad_num || "",
              building_id: String(
                chosen.building_id || (targetFeatureId.includes("#") ? targetFeatureId : "")
              ).trim(),
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
              builder: chosen.builder || "",
              builder_source: chosen.builder_source || "",
              engineers: chosen.engineers || "",
              original_cost: chosen.original_cost || "",
              original_permit: chosen.original_permit || "",
              yr_remodel: chosen.yr_remodel || 0,
              eff_year_built: chosen.eff_year_built || 0,
              was_relocated: Boolean(chosen.was_relocated),
              relocation_note: chosen.relocation_note || "",
              hist_contract: chosen.hist_contract || null,
              style: chosen.style || "",
              bld_style: chosen.bld_style || "",
              hcad_grade: chosen.hcad_grade || "",
              use_category: chosen.use_category || "",
              landuse_desc: chosen.landuse_desc || "",
              landmark_code: chosen.landmark_code || "",
              landmark_report_url: chosen.landmark_report_url || "",
              landmark_summary: chosen.landmark_summary || "",
            };
            const hydratedDrawerProps = applyOverrideToProperties(
              baseDrawerProps,
              this.mapController?.curatedOverrides || {}
            );
            this.renderInspectorDrawer(hydratedDrawerProps);
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

  _syncFootprintIssueUi(autoFocusNotes = false) {
    const footprintSelectEl = document.getElementById("corr-footprint-issue");
    if (!footprintSelectEl) return;
    const footprintNotesWrapEl = document.getElementById("corr-footprint-notes-wrap");
    const footprintNotesEl = document.getElementById("corr-footprint-notes");
    const footprintNotesLblEl = document.getElementById("lbl-corr-footprint-notes");
    const footprintBoxEl = document.getElementById("corr-footprint-box");
    const corrSuggYrEl = document.getElementById("corr-suggested-year");
    const sourceTypeEl = document.getElementById("corr-source-type");
    const issueVal = footprintSelectEl.value;
    const hasIssue = Boolean(issueVal);
    const isVacantChoice = issueVal === DEMOLISHED_VACANT_ISSUE;
    if (footprintNotesWrapEl) {
      footprintNotesWrapEl.classList.toggle("hidden", !hasIssue);
    }
    if (footprintBoxEl) {
      footprintBoxEl.classList.toggle("active", hasIssue);
    }
    if (footprintNotesLblEl) {
      footprintNotesLblEl.textContent = isVacantChoice
        ? "Notes on Demolished Building / Vacant Lot (Optional)"
        : "Describe the Shape or Orientation Error *";
    }
    if (footprintNotesEl) {
      footprintNotesEl.required = hasIssue && !isVacantChoice;
      footprintNotesEl.placeholder = isVacantChoice
        ? VACANT_FOOTPRINT_NOTES_PLACEHOLDER
        : DEFAULT_FOOTPRINT_NOTES_PLACEHOLDER;
      if (hasIssue && autoFocusNotes && !isVacantChoice) {
        setTimeout(() => footprintNotesEl.focus(), 40);
      }
    }
    if (isVacantChoice) {
      if (corrSuggYrEl) {
        corrSuggYrEl.value = "0";
      }
      if (sourceTypeEl && sourceTypeEl.value === "Houston City Directory") {
        sourceTypeEl.value = "Aerial / Satellite Imagery";
      }
    } else if (issueVal === DEMOLISHED_REPLACED_ISSUE) {
      if (corrSuggYrEl && String(corrSuggYrEl.value || "").trim() === "0") {
        corrSuggYrEl.value = "";
      }
      if (sourceTypeEl && (!corrSuggYrEl || !corrSuggYrEl.value)) {
        sourceTypeEl.value = "Aerial / Satellite Imagery";
      }
    } else {
      if (!hasIssue && corrSuggYrEl && String(corrSuggYrEl.value || "").trim() === "0") {
        corrSuggYrEl.value = "";
      }
      if (hasIssue && sourceTypeEl && (!corrSuggYrEl || !corrSuggYrEl.value)) {
        sourceTypeEl.value = "Aerial / Satellite Imagery";
      }
    }
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
    const builderEl = document.getElementById("corr-builder");
    const permitInfoEl = document.getElementById("corr-permit-info");
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
    const isAlreadyVacant = Boolean(props.is_demolished_vacant || props.suppress_only);
    if (currYrEl) currYrEl.value = props.original_hcad_year || props.year_built || "";
    if (suggYrEl) {
      suggYrEl.value = isAlreadyVacant
        ? "0"
        : props.is_curated_override && Number(props.year_built) >= MIN_VALID_YEAR
        ? props.year_built
        : "";
    }
    if (distEl) distEl.value = props.historic_district || "";
    if (styleEl) styleEl.value = props.architect || props.bld_style || "";
    if (builderEl) builderEl.value = props.builder || "";
    if (permitInfoEl) {
      permitInfoEl.value =
        props.original_permit ||
        props.relocation_note ||
        (props.original_cost ? `Cost: ${props.original_cost}` : "") ||
        "";
    }
    if (citeEl) citeEl.value = props.source_citation || "";
    if (sourceTypeEl) {
      sourceTypeEl.value = isAlreadyVacant ? "Aerial / Satellite Imagery" : "Houston City Directory";
    }
    if (footprintSelectEl) {
      footprintSelectEl.value = isAlreadyVacant ? DEMOLISHED_VACANT_ISSUE : "";
    }
    if (footprintNotesEl) {
      footprintNotesEl.value = "";
    }
    (this._syncFootprintIssueUi || HoustonAtlasApp.prototype._syncFootprintIssueUi).call(
      this,
      false
    );
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
    if (this._activeChapterId) {
      this.exitCityChapters({ restoreState: false, skipUrlUpdate: true });
    }
    if (this._activePortfolio) {
      this.exitPortfolioMode({ restoreState: false, skipUrlUpdate: true });
    }
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
    this._renderNarrativeCitationsBar(document.getElementById("tour-banner-citations"), {
      citations: tour.citations || [],
      contextId: `tour_${tour.id}`,
      contextTitle: `${tour.name} (${tour.era})`,
      excerpt: tour.description || "",
      compact: true,
    });
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
    this._renderNarrativeCitationsBar(document.getElementById("tour-banner-citations"), {
      citations:
        Array.isArray(stop.citations) && stop.citations.length > 0
          ? stop.citations
          : tour.citations || [],
      contextId: `tour_${tour.id}_stop_${idx + 1}`,
      contextTitle: `${tour.name} - Stop ${idx + 1}: ${stop.title}`,
      excerpt: stop.story || "",
      compact: true,
    });

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

    // Basemap Buttons & Dropdown Trigger
    const BASEMAP_LABELS = {
      dark_archival: "Archival Dark",
      warm_parchment: "Parchment",
      satellite: "Aerial",
      solid_dark: "Solid Dark",
      solid_light: "Solid Light",
    };
    document.querySelectorAll("[data-basemap]").forEach((btn) => {
      btn.classList.toggle("active", btn.getAttribute("data-basemap") === state.basemap);
    });
    const activeBaseLabelEl = document.getElementById("basemap-active-label");
    if (activeBaseLabelEl) {
      activeBaseLabelEl.textContent = BASEMAP_LABELS[state.basemap] || "Archival Dark";
    }
    const activeBaseSwatchEl = document.getElementById("basemap-active-swatch");
    if (activeBaseSwatchEl) {
      activeBaseSwatchEl.setAttribute("data-swatch", state.basemap || "dark_archival");
    }

    // 3D Toggle Button
    const btn3d = document.getElementById("btn-toggle-3d");
    if (btn3d) {
      btn3d.classList.toggle("active", state.extrude3D);
      btn3d.textContent = state.extrude3D ? "3D: ON" : "3D: OFF";
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
      if (state.historicalSnapshotMode) {
        yearBadge.textContent = `Snapshot: ${state.maxYear} (Standing)`;
      } else if (state.selectedDecade === "unknown") {
        yearBadge.textContent = "Undated / Exempt Only";
      } else if (state.selectedDecade !== "all") {
        yearBadge.textContent = `${state.selectedDecade}s Decade (${state.minYear}-${state.maxYear})`;
      } else {
        yearBadge.textContent = `${state.minYear} - ${state.maxYear}`;
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

    const chkHistoricalSnapshot = document.getElementById("chk-historical-snapshot");
    if (chkHistoricalSnapshot) {
      chkHistoricalSnapshot.checked = Boolean(state.historicalSnapshotMode);
    }

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
        buildingsSublabel.textContent = "Hidden - showing overlays only";
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
      "chk-layer-permit-watch": state.layers.permitWatch,
      "chk-layer-plat-watch": state.layers.platWatch,
      "chk-layer-hahc-tracker": state.layers.hahcTracker,
      "chk-layer-good-brick": state.layers.goodBrickAwards,
      "chk-layer-landmarks": state.layers.landmarks,
      "chk-layer-potential-districts": state.layers.potentialHistoricDistricts,
      "chk-layer-relocated-structures": state.layers.relocatedStructures,
      "chk-layer-lost-houston": state.layers.lostHouston || state.historicalSnapshotMode,
      "chk-layer-historic-districts": state.layers.historicDistricts,
      "chk-layer-heritage-districts": state.layers.heritageDistricts,
      "chk-layer-nrhp-districts": state.layers.nrhpDistricts,
      "chk-layer-nrhp-properties": state.layers.nrhpProperties,
      "chk-layer-cultural-districts": state.layers.culturalDistricts,
      "chk-layer-red-book": state.layers.redBook1915,
      "chk-layer-holc-1937": state.layers.holc1937,
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

    const landmarksFilterRow = document.getElementById("landmarks-filter-row");
    if (landmarksFilterRow) {
      landmarksFilterRow.classList.toggle("hidden", !state.layers.landmarks);
    }
    const activeLmFilter =
      state.landmarkFilter === "protected" || state.landmarkFilter === "waiver_lm"
        ? state.landmarkFilter
        : "all";
    document.querySelectorAll("[data-lm-filter]").forEach((btn) => {
      const val = String(btn.getAttribute("data-lm-filter") || "all");
      btn.classList.toggle("active", val === activeLmFilter);
    });

    const hahcFilterRow = document.getElementById("hahc-filter-row");
    if (hahcFilterRow) {
      hahcFilterRow.classList.toggle("hidden", !state.layers.hahcTracker);
    }
    const validHahcFilters = [
      "all",
      "active_agenda",
      "demolition_relocation",
      "addition_new",
      "designation",
      "denied_or_cor",
    ];
    const activeHahcFilter = validHahcFilters.includes(state.hahcFilter)
      ? state.hahcFilter
      : "all";
    document.querySelectorAll("[data-hahc-filter]").forEach((btn) => {
      const val = String(btn.getAttribute("data-hahc-filter") || "all");
      btn.classList.toggle("active", val === activeHahcFilter);
    });

    const redBookFilterRow = document.getElementById("red-book-filter-row");
    if (redBookFilterRow) {
      redBookFilterRow.classList.toggle("hidden", !state.layers.redBook1915);
    }
    const activeRbCat = ["church", "school", "business", "residence"].includes(
      state.redBookCategory
    )
      ? state.redBookCategory
      : "all";
    document.querySelectorAll("[data-rb-cat]").forEach((btn) => {
      const val = String(btn.getAttribute("data-rb-cat") || "all");
      btn.classList.toggle("active", val === activeRbCat);
    });

    const holcFilterRow = document.getElementById("holc-grade-filter-row");
    if (holcFilterRow) {
      holcFilterRow.classList.toggle("hidden", !state.layers.holc1937);
    }
    const activeHolcGrade = ["A", "B", "C", "D"].includes(state.holcGradeFilter)
      ? state.holcGradeFilter
      : "all";
    document.querySelectorAll("[data-holc-grade]").forEach((btn) => {
      const val = String(btn.getAttribute("data-holc-grade") || "all");
      btn.classList.toggle("active", val === activeHolcGrade);
    });
    const btnDrapeHolcScan = document.getElementById("btn-drape-holc-scan");
    if (btnDrapeHolcScan) {
      const holcScanOn = Boolean(
        state.layers.historicMap && state.historicMapId === "holc_1937"
      );
      btnDrapeHolcScan.classList.toggle("active", holcScanOn);
      btnDrapeHolcScan.innerHTML = holcScanOn
        ? "&#10003; 1937 Original HOLC Map Scan Draped (Click to Hide)"
        : "&#128506; Drape 1937 Original HOLC Map Scan";
    }

    const lostHoustonLegendRow = document.getElementById("lost-houston-legend-row");
    if (lostHoustonLegendRow) {
      lostHoustonLegendRow.classList.toggle(
        "hidden",
        !state.layers.lostHouston && !state.historicalSnapshotMode
      );
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
    const selectHistoricMapId = document.getElementById("select-historic-map-id");
    const activeMapId = state.historicMapId || "sanborn_1907";
    if (selectHistoricMapId && selectHistoricMapId.value !== activeMapId) {
      selectHistoricMapId.value = activeMapId;
    }
    const histCitationEl = document.getElementById("historic-map-citation-readout");
    if (histCitationEl) {
      const mapEntry =
        HISTORICAL_MAP_CATALOG.find((m) => m.id === activeMapId) || HISTORICAL_MAP_CATALOG[0];
      if (mapEntry) {
        histCitationEl.innerHTML = "";
        const textSpan = document.createElement("span");
        textSpan.textContent = `${mapEntry.attribution} · ${mapEntry.description || ""}`;
        histCitationEl.appendChild(textSpan);
        if (Array.isArray(mapEntry.focusCenter)) {
          const [fLng, fLat, fZoom] = mapEntry.focusCenter;
          const jumpBtn = document.createElement("button");
          jumpBtn.type = "button";
          jumpBtn.className = "action-pill subtle";
          jumpBtn.style.cssText =
            "margin-top:6px;padding:3px 8px;font-size:11px;display:inline-flex;align-items:center;gap:4px;";
          jumpBtn.textContent = "📍 Center on Map Coverage Area";
          jumpBtn.addEventListener("click", () => {
            this.mapController?.flyToLocation?.(fLng, fLat, fZoom || 15.4);
          });
          const br = document.createElement("div");
          br.appendChild(jumpBtn);
          histCitationEl.appendChild(br);
        }
      }
    }
    const btnToggleSpyglass = document.getElementById("btn-toggle-spyglass");
    const spyOn = Boolean(state.historicMapSpyglass);
    if (btnToggleSpyglass) {
      btnToggleSpyglass.classList.toggle("active", spyOn);
      btnToggleSpyglass.innerHTML = spyOn
        ? `&#128269; Spyglass Lens: ON`
        : `&#128269; Spyglass Lens: OFF`;
    }
    const spyglassRadiusRow = document.getElementById("spyglass-radius-row");
    if (spyglassRadiusRow) {
      spyglassRadiusRow.classList.toggle("hidden", !spyOn);
    }
    const spyglassRadiusVal = Math.max(
      72,
      Math.min(360, Number(state.historicMapSpyglassRadius) || 152)
    );
    const spyglassRadiusSlider = document.getElementById("slider-spyglass-radius");
    if (spyglassRadiusSlider && Number(spyglassRadiusSlider.value) !== spyglassRadiusVal) {
      spyglassRadiusSlider.value = String(spyglassRadiusVal);
    }
    const spyglassRadiusReadout = document.getElementById("spyglass-radius-readout");
    if (spyglassRadiusReadout) {
      spyglassRadiusReadout.textContent = `${spyglassRadiusVal}px`;
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

    // Update 4-category accordion active-layer count badges & auto-expand when a category activates
    this._prevCategoryActiveCounts = this._prevCategoryActiveCounts || null;
    const isInitialCategorySync = this._prevCategoryActiveCounts === null;
    const nextCategoryCounts = {};
    document.querySelectorAll("details.layer-category-group[data-layer-group]").forEach((groupEl) => {
      const grpKey = String(groupEl.getAttribute("data-layer-group") || "");
      let activeCount = 0;
      groupEl.querySelectorAll(".layer-item-row[data-layer-key]").forEach((row) => {
        const lKey = row.getAttribute("data-layer-key");
        if (
          Boolean(state.layers[lKey]) ||
          (lKey === "lostHouston" && Boolean(state.historicalSnapshotMode))
        ) {
          activeCount += 1;
        }
      });
      nextCategoryCounts[grpKey] = activeCount;
      const badgeEl = groupEl.querySelector(`[data-group-badge="${grpKey}"]`);
      if (badgeEl) {
        badgeEl.textContent = `${activeCount} active`;
        badgeEl.classList.toggle("is-zero", activeCount === 0);
      }
      const prevCount = isInitialCategorySync
        ? 0
        : Number(this._prevCategoryActiveCounts[grpKey] || 0);
      if (
        (isInitialCategorySync && activeCount > 0) ||
        (!isInitialCategorySync && activeCount > prevCount)
      ) {
        groupEl.open = true;
      }
    });
    this._prevCategoryActiveCounts = nextCategoryCounts;
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
      oldestEl.textContent = stats.oldestYear ? `${stats.oldestYear}` : "-";
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
    const platYearLabel = String(
      subProps?.plat_year_label ||
        (catalogEntry?.plat_year ? String(catalogEntry.plat_year) : "") ||
        (Number(subProps?.estimated_plat_year) >= 1836 ? `c. ${subProps.estimated_plat_year}` : "")
    ).trim();
    const platYearBasis = String(subProps?.plat_year_basis || "").trim();
    const recordSeries = String(subProps?.record_series || "").trim();
    const healedReplatCount = Number(subProps?.healed_replat_count || 0);
    const survivingAcres = Number(subProps?.surviving_acres || 0);
    const reconstructedAcres = Number(subProps?.reconstructed_acres || 0);
    const sampleHealedReplats = Array.isArray(subProps?.sample_healed_replats)
      ? subProps.sample_healed_replats.filter(Boolean)
      : [];
    const archivalLookupNote = String(subProps?.archival_lookup_note || "").trim();
    const bracey1922Url = String(subProps?.bracey_1922_url || "").trim();

    let badgeText = "HCAD Plat Record";
    let badgeClass = "cyan";
    if (hasDocs) {
      badgeText = `${docs.length} Full-Text Deed PDF${docs.length === 1 ? "" : "s"}`;
      badgeClass = "gold";
    } else if (healedReplatCount > 0 && platYearLabel) {
      badgeText = `Platted ${platYearLabel} · ${healedReplatCount} Replats Healed`;
      badgeClass = "amber";
    } else if (platYearLabel && subProps?.is_historic_plat) {
      badgeText = `Historic Plat (${platYearLabel})`;
      badgeClass = "cyan";
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
    const deedCitationsHtml =
      catalogEntry && Array.isArray(catalogEntry.citations) && catalogEntry.citations.length > 0
        ? this._buildNarrativeCitationsHtml({
            citations: catalogEntry.citations,
            contextId: `deed_${catalogEntry.subdivision_name || subName}`,
            contextTitle: `Deed Restrictions: ${subName || catalogEntry.subdivision_name}`,
            excerpt: cov?.notes || deedCitation || platCitation,
            compact: true,
          })
        : "";

    const historicPlatReconstructionHtml =
      healedReplatCount > 0 || archivalLookupNote
        ? `<div class="deed-covenant-section" style="margin-top:8px;">
            <div class="deed-subhead">
              <span>Historic Plat Provenance &amp; Pre-Replat Reconstruction</span>
              ${
                platYearLabel
                  ? `<span class="deed-plat-year-pill mono">Platted ${platYearLabel}</span>`
                  : ""
              }
            </div>
            ${
              healedReplatCount > 0
                ? `<div class="deed-covenant-notes" style="margin-top:4px;color:#fde68a;">
                    <strong>De-Replatted Historic Footprint:</strong> ${
                      reconstructedAcres > 0 && survivingAcres > 0
                        ? `Reconstructed <strong>${reconstructedAcres.toFixed(
                            1
                          )} acres</strong> from <strong>${survivingAcres.toFixed(
                            1
                          )} surviving un-replatted HCAD acres</strong> by healing <strong>${healedReplatCount} modern infill replat cutout${
                            healedReplatCount === 1 ? "" : "s"
                          }</strong> back into the original addition perimeter.`
                        : `Healed <strong>${healedReplatCount} modern infill replat cutout${
                            healedReplatCount === 1 ? "" : "s"
                          }</strong> back into the original addition perimeter.`
                    }
                    ${
                      sampleHealedReplats.length > 0
                        ? `<div style="margin-top:4px;font-size:11px;color:#cbd5e1;"><strong>Sample Absorbed Modern Replats:</strong> ${sampleHealedReplats
                            .slice(0, 10)
                            .join(", ")}${
                            healedReplatCount > sampleHealedReplats.slice(0, 10).length
                              ? ` (+${healedReplatCount - sampleHealedReplats.slice(0, 10).length} more)`
                              : ""
                          }</div>`
                        : ""
                    }
                  </div>`
                : ""
            }
            ${
              archivalLookupNote
                ? `<div class="deed-covenant-notes" style="margin-top:6px;">
                    <strong>County Title &amp; Plat Lookup Tip:</strong> ${archivalLookupNote}
                  </div>`
                : ""
            }
          </div>`
        : "";

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
                  <span class="deed-cit-val"><strong>${subName}</strong>${
                    subProps?.full_name &&
                    String(subProps.full_name).toUpperCase() !== String(subName).toUpperCase()
                      ? ` <span class="mono" style="font-size:11px;opacity:0.82;">(${subProps.full_name})</span>`
                      : ""
                  }</span>
                </div>`
              : ""
          }
          ${
            platYearLabel
              ? `<div class="deed-citation-row">
                  <span class="deed-cit-label">Original Plat Date:</span>
                  <span class="deed-cit-val"><strong>${platYearLabel}</strong>${
                    platYearBasis ? ` <span style="font-size:11px;opacity:0.85;">· ${platYearBasis}</span>` : ""
                  }</span>
                </div>`
              : ""
          }
          ${
            recordSeries
              ? `<div class="deed-citation-row">
                  <span class="deed-cit-label">Clerk Record Series:</span>
                  <span class="deed-cit-val">${recordSeries}</span>
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
                  <span class="deed-cit-label">Root Deed / Filing:</span>
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

        ${historicPlatReconstructionHtml}
        ${covenantGridHtml}
        ${docsHtml}
        ${protectionsHtml}
        ${deedCitationsHtml}

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
            ${
              bracey1922Url
                ? `<a
                    href="${bracey1922Url}"
                    target="_blank"
                    rel="noopener noreferrer"
                    class="deed-action-btn clerk-btn"
                    style="text-decoration:none;text-align:center;"
                    title="Open digitized 1922 Bracey's Block Maps of the City of Houston in the Houston Public Library Digital Archive showing original pre-replat addition names, lots, and blocks"
                  >
                    &#128506; Open 1922 Bracey's Block Maps of Houston (HPL Archive) &#8599;
                  </a>`
                : ""
            }
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
    this._bindQuickFlagEvents(container, contextProps);

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
      overlayLayer === "hahc_coa_filings"
        ? rawProps.landmark_name || rawProps.address || rawProps.name || "HAHC COA Parcel"
        : rawProps.name || rawProps.era_label || rawProps.historic_district || "Houston Boundary"
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
        }${rawProps.plat_year_label ? ` · Platted ${rawProps.plat_year_label}` : ""})`;
        badgeColor = "#fbbf24";
      } else if (rawProps.is_historic_plat && rawProps.plat_year_label) {
        badgeLabel = `Historic Subdivision Plat (${rawProps.plat_year_label})`;
        badgeColor = "#2dd4bf";
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
    } else if (overlayLayer === "potential_historic_districts") {
      badgeLabel = rawProps.already_designated_coh_hd
        ? "2019 Surveyed (Now Designated COH HD)"
        : rawProps.survey_subtype === "eligible_site"
        ? "2019 Surveyed NRHP-Eligible Site"
        : "2019 Surveyed Potential Historic District";
      badgeColor = rawProps.already_designated_coh_hd
        ? "#a855f7"
        : rawProps.survey_subtype === "eligible_site"
        ? "#fbbf24"
        : "#fb923c";
    } else if (overlayLayer === "cultural_districts") {
      badgeLabel = "State-Recognized Cultural Arts District";
      badgeColor = "#f472b6";
    } else if (overlayLayer === "nrhp_properties") {
      badgeLabel = rawProps.is_demolished_nrhp
        ? "Demolished NRHP Property (Gone)"
        : "National Register Property (NRHP)";
      badgeColor = rawProps.is_demolished_nrhp ? "#94a3b8" : "#c084fc";
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
    } else if (overlayLayer === "lost_houston") {
      badgeLabel = rawProps.type_label || "Lost Houston · Demolished Landmark";
      const mat = String(rawProps.sanborn_material || "brick").toLowerCase();
      badgeColor =
        mat === "frame"
          ? "#facc15"
          : mat === "stone"
          ? "#60a5fa"
          : mat === "iron"
          ? "#cbd5e1"
          : mat === "fireproof"
          ? "#a3b18a"
          : "#f472b6";
    } else if (overlayLayer === "red_book_1915") {
      badgeLabel = rawProps.category_label || "1915 Red Book of Houston";
      const rbCat = String(rawProps.category || "").toLowerCase();
      badgeColor =
        rbCat === "church"
          ? "#c084fc"
          : rbCat === "school"
          ? "#38bdf8"
          : rbCat === "business"
          ? "#f59e0b"
          : "#34d399";
    } else if (overlayLayer === "holc_1937_zones") {
      badgeLabel = `1937 HOLC ${rawProps.zone_code || ""} · ${
        rawProps.grade_label || "Security Area"
      }`.trim();
      badgeColor = String(rawProps.color || "#ef4444");
    } else if (overlayLayer === "hahc_coa_filings") {
      badgeLabel = rawProps.has_active_agenda
        ? "HAHC Oct 8, 2026 Active Agenda"
        : rawProps.has_designation
        ? "HAHC Landmark Designation Filing"
        : rawProps.has_demolition_relocation
        ? "HAHC Demolition / Relocation COA"
        : "HAHC Certificate of Appropriateness";
      badgeColor = rawProps.has_active_agenda
        ? "#f43f5e"
        : rawProps.has_demolition_relocation
        ? "#ef4444"
        : rawProps.has_designation
        ? "#a855f7"
        : rawProps.has_denied_or_cor
        ? "#f59e0b"
        : "#14b8a6";
    }

    const isLinearOrPointOverlay =
      overlayLayer === "historical_waterways" ||
      overlayLayer === "historical_railroads" ||
      overlayLayer === "lost_houston" ||
      overlayLayer === "nrhp_properties" ||
      overlayLayer === "red_book_1915" ||
      overlayLayer === "hahc_coa_filings";

    const bldCount = Number(rawProps.building_count || 0);
    const earliestYear = Number(rawProps.earliest_year || 0);
    const medianYear = Number(rawProps.median_year || 0);
    const pre1940Count = Number(rawProps.pre_1940_count || 0);
    const pre1945Count = Number(rawProps.pre_1945_count || 0);
    const landmarkCount = Number(rawProps.landmark_count || 0);
    const goodBrickCount = Number(rawProps.good_brick_count || 0);

    const subtitleParts = [];
    if (overlayLayer === "lost_houston") {
      if (rawProps.address) subtitleParts.push(rawProps.address);
      if (rawProps.lifespan_label) subtitleParts.push(`Stood ${rawProps.lifespan_label}`);
      if (rawProps.sanborn_material_label) subtitleParts.push(rawProps.sanborn_material_label);
    } else if (overlayLayer === "nrhp_properties") {
      if (rawProps.address) subtitleParts.push(rawProps.address);
      if (rawProps.refnum) subtitleParts.push(`NRHP #${rawProps.refnum}`);
      if (rawProps.listed_date) subtitleParts.push(`Listed ${rawProps.listed_date}`);
    } else if (overlayLayer === "potential_historic_districts") {
      subtitleParts.push("COH Nov. 2019 Historic Survey");
      if (pre1945Count > 0) subtitleParts.push(`${pre1945Count.toLocaleString()} pre-1946 structures`);
      if (!rawProps.already_designated_coh_hd) subtitleParts.push("No Ch. 33 Demolition Ban");
    } else if (overlayLayer === "cultural_districts") {
      subtitleParts.push("COH & Texas Commission on the Arts");
      if (landmarkCount > 0) subtitleParts.push(`${landmarkCount} Designated Landmarks`);
    } else if (overlayLayer === "historical_waterways") {
      if (rawProps.status) subtitleParts.push(rawProps.status);
      if (rawProps.watershed) subtitleParts.push(`Watershed: ${rawProps.watershed}`);
      if (Number(rawProps.length_miles) > 0) subtitleParts.push(`${rawProps.length_miles} mi mapped`);
    } else if (overlayLayer === "historical_railroads") {
      if (rawProps.historic_company) subtitleParts.push(rawProps.historic_company);
      if (
        rawProps.charter_year &&
        rawProps.opened_year &&
        String(rawProps.charter_year) !== String(rawProps.opened_year)
      ) {
        subtitleParts.push(`Chartered ${rawProps.charter_year} · Opened ${rawProps.opened_year}`);
      } else if (rawProps.opened_year) {
        subtitleParts.push(`Opened ${rawProps.opened_year}`);
      } else if (rawProps.charter_year) {
        subtitleParts.push(`Chartered ${rawProps.charter_year}`);
      }
      if (rawProps.status) subtitleParts.push(rawProps.status);
    } else if (overlayLayer === "platted_subdivisions") {
      if (rawProps.plat_year_label) {
        subtitleParts.push(`Platted ${rawProps.plat_year_label}`);
      }
      if (rawProps.deed_citation && rawProps.is_historic_plat && !rawProps.has_deed_docs) {
        subtitleParts.push(rawProps.deed_citation);
      } else if (rawProps.plat_citation) {
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
    } else if (overlayLayer === "red_book_1915") {
      if (rawProps.subtitle) subtitleParts.push(rawProps.subtitle);
      if (rawProps.historic_address_1915) {
        subtitleParts.push(`1915: ${rawProps.historic_address_1915}`);
      }
      if (rawProps.survival_label) subtitleParts.push(rawProps.survival_label);
    } else if (overlayLayer === "holc_1937_zones") {
      if (Number(rawProps.area_acres) > 0) {
        subtitleParts.push(
          `${Number(rawProps.area_acres).toLocaleString()} acres${
            rawProps.area_sq_mi ? ` (${rawProps.area_sq_mi} sq mi)` : ""
          }`
        );
      }
      const holcTopNbhds = parseJsonList(rawProps.top_neighborhoods);
      if (holcTopNbhds.length > 0) {
        subtitleParts.push(holcTopNbhds.slice(0, 3).join(", "));
      }
      if (Number(rawProps.af_temp_f) > 0) {
        subtitleParts.push(`HARC Heat Watch: ${Number(rawProps.af_temp_f).toFixed(1)}°F`);
      }
    } else if (overlayLayer === "hahc_coa_filings") {
      if (rawProps.address && rawProps.address !== name) subtitleParts.push(rawProps.address);
      if (rawProps.historic_district) subtitleParts.push(rawProps.historic_district);
      if (Number(rawProps.year_built) > 1800) subtitleParts.push(`Built ${rawProps.year_built}`);
      if (Number(rawProps.filings_count) > 0) {
        subtitleParts.push(
          `${rawProps.filings_count} HAHC Filing${Number(rawProps.filings_count) === 1 ? "" : "s"}`
        );
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

    const narrativeDesc =
      rawProps.historic_significance ||
      rawProps.description ||
      (overlayLayer === "holc_1937_zones" ? rawProps.appraisal_summary || "" : "");
    const lostSiteHcads = parseJsonList(rawProps.site_hcad_nums);
    const primarySiteHcad = String(rawProps.hcad_num || lostSiteHcads[0] || "").trim();
    const lostLat = Number(rawProps.lat ?? rawProps.label_lat) || 29.7604;
    const lostLng = Number(rawProps.lng ?? rawProps.lon ?? rawProps.label_lng) || -95.3698;
    const lostStreetViewUrl = buildStreetViewUrl(lostLat, lostLng);

    const redBookPhotoCardHtml =
      overlayLayer === "red_book_1915" && (rawProps.rice_photo_img_url || rawProps.rice_photo_url)
        ? `<div class="red-book-photo-wrap">
            ${
              rawProps.rice_photo_img_url
                ? `<a
                    href="${rawProps.rice_photo_url || rawProps.rice_photo_full_url || rawProps.rice_photo_img_url}"
                    target="_blank"
                    rel="noopener noreferrer"
                    title="Open high-resolution 1915 Red Book plate in Rice University Woodson Research Center Digital Collections"
                  >
                    <img
                      class="red-book-photo-thumb"
                      src="${rawProps.rice_photo_img_url}"
                      alt="1915 Red Book of Houston archival plate for ${name.replace(/"/g, "&quot;")}"
                      loading="lazy"
                    />
                  </a>`
                : ""
            }
            <div style="padding:7px 10px;display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;font-size:10.8px;color:#fde68a;">
              <span>&#128247; 1915 <em>Red Book of Houston</em> Plate${
                rawProps.red_book_page ? ` (${rawProps.red_book_page})` : ""
              } · Public Domain</span>
              ${
                rawProps.rice_photo_url
                  ? `<a
                      href="${rawProps.rice_photo_url}"
                      target="_blank"
                      rel="noopener noreferrer"
                      style="color:#fef08a;font-weight:700;text-decoration:underline;"
                    >Rice Woodson Archive &#8599;</a>`
                  : ""
              }
            </div>
          </div>`
        : "";

    const holcGradeSummaryList =
      overlayLayer === "holc_1937_zones" &&
      Array.isArray(this.mapController?.overlaysData?.holc_1937_grade_summary)
        ? this.mapController.overlaysData.holc_1937_grade_summary
        : [];
    const holcThermalBannerHtml =
      overlayLayer === "holc_1937_zones" && Number(rawProps.af_temp_f) > 0
        ? `<div class="holc-thermal-banner">
            <div style="font-weight:700;color:#fecaca;margin-bottom:3px;">
              &#127777; HARC / NOAA Houston Urban Heat Watch Telemetry
            </div>
            <div>
              Afternoon Mean: <strong class="mono">${Number(rawProps.af_temp_f).toFixed(1)}°F</strong>
              ${
                Number(rawProps.pm_temp_f) > 0
                  ? ` · Evening Mean: <strong class="mono">${Number(rawProps.pm_temp_f).toFixed(1)}°F</strong>`
                  : ""
              }
              ${
                Number.isFinite(Number(rawProps.af_temp_delta_vs_a))
                  ? ` · <strong class="mono" style="color:${
                      Number(rawProps.af_temp_delta_vs_a) > 0.3 ? "#fca5a5" : "#bbf7d0"
                    };">${
                      Number(rawProps.af_temp_delta_vs_a) >= 0 ? "+" : ""
                    }${Number(rawProps.af_temp_delta_vs_a).toFixed(1)}°F vs. Grade A avg</strong>`
                  : ""
              }
            </div>
          </div>`
        : "";

    const holcComparisonTableHtml =
      overlayLayer === "holc_1937_zones" && holcGradeSummaryList.length > 0
        ? `<div class="ph-verified-override-card" style="border-left-color:${badgeColor};margin-top:8px;">
            <div class="ph-verified-header">
              <span>Citywide 1937 HOLC Grade Equity Comparison (All 47 Houston Zones)</span>
            </div>
            <div class="holc-comparison-table-wrap">
              <table class="holc-comparison-table">
                <thead>
                  <tr>
                    <th>Grade</th>
                    <th>Pre-38</th>
                    <th>PLM/LM</th>
                    <th>Ch.42</th>
                    <th>1915 RB</th>
                    <th>Heat</th>
                  </tr>
                </thead>
                <tbody>
                  ${holcGradeSummaryList
                    .map((row) => {
                      const rowGrade = String(row.holc_grade || row.grade || "").toUpperCase();
                      const rowShort =
                        row.grade_short ||
                        (rowGrade === "A"
                          ? "Best"
                          : rowGrade === "B"
                          ? "Desirable"
                          : rowGrade === "C"
                          ? "Declining"
                          : "Hazardous");
                      const isCur = rowGrade === String(rawProps.holc_grade || "").toUpperCase();
                      const ch42 =
                        Number(row.smls_block_count || 0) + Number(row.smbl_block_count || 0);
                      return `<tr class="${isCur ? "is-active-grade" : ""}">
                        <td>
                          <span style="display:inline-block;width:7px;height:7px;border-radius:2px;background:${
                            row.color || "#94a3b8"
                          };margin-right:4px;"></span>${rowGrade}·${rowShort} (${
                        row.zone_count || 0
                      })
                        </td>
                        <td>${Number(row.pre_1938_count || 0).toLocaleString()}</td>
                        <td>${Number(row.plm_count || 0)}/${Number(
                        row.vulnerable_lm_count || 0
                      )}</td>
                        <td>${ch42.toLocaleString()}</td>
                        <td>${Number(row.red_book_1915_count || 0).toLocaleString()}</td>
                        <td>${
                          Number(row.mean_af_temp_f) > 0
                            ? `${Number(row.mean_af_temp_f).toFixed(1)}°`
                            : "-"
                        }</td>
                      </tr>`;
                    })
                    .join("")}
                </tbody>
              </table>
            </div>
          </div>`
        : "";

    const lostPhotoCardHtml =
      overlayLayer === "lost_houston"
        ? `<div class="inspector-photo-card" id="inspector-photo-card" data-photo-hcad="${primarySiteHcad}">
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
                href="${lostStreetViewUrl}"
                target="_blank"
                rel="noopener noreferrer"
                class="photo-streetview-btn"
                id="btn-inspector-streetview"
                title="Open Google Maps Street View Time Machine (2007-Present) for this site"
              >
                &#128065; Present-Day Site on Street View &#8599;
              </a>
            </div>
          </div>`
        : "";

    content.innerHTML = `
      ${overlapStackHtml}
      <div class="inspector-hero">
        <div class="inspector-badges">
          <span class="inspector-year-pill" style="background:${badgeColor};color:#090d16;">${badgeLabel}</span>
          ${
            overlayLayer === "red_book_1915"
              ? `<span class="inspector-age-pill" style="color:${
                  rawProps.survival_status === "surviving_era_structure"
                    ? "#fef08a"
                    : rawProps.survival_status === "cleared_or_row"
                    ? "#fecaca"
                    : "#cbd5e1"
                };border-color:${
                  rawProps.survival_status === "surviving_era_structure"
                    ? "rgba(250,204,21,0.55)"
                    : rawProps.survival_status === "cleared_or_row"
                    ? "rgba(248,113,113,0.45)"
                    : "rgba(148,163,184,0.35)"
                };">${rawProps.survival_label || "1915 Heritage Site"}</span>`
              : overlayLayer === "holc_1937_zones" && Number(rawProps.area_acres) > 0
              ? `<span class="inspector-age-pill">${Number(rawProps.area_acres).toLocaleString()} Acres</span>`
              : overlayLayer === "lost_houston" && rawProps.lifespan_label
              ? `<span class="inspector-age-pill">Stood ${rawProps.lifespan_label} (${rawProps.lifespan_years || ""} yrs)</span>`
              : overlayLayer === "platted_subdivisions" && Number(rawProps.healed_replat_count) > 0
              ? `<span class="inspector-age-pill" style="color:#fde68a;border-color:rgba(250,204,21,0.45);">${
                  Number(rawProps.reconstructed_acres) > 0
                    ? `${Number(rawProps.reconstructed_acres).toFixed(1)} ac Reconstructed · `
                    : ""
                }${Number(rawProps.healed_replat_count)} Replats Healed</span>`
              : earliestYear >= 1836
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
          <span>${
            rawProps.source ||
            (overlayLayer === "red_book_1915"
              ? "1915 Red Book of Houston (Sotex Pub. Co.) · Rice Woodson Research Center &amp; Spatial Studies Lab"
              : overlayLayer === "holc_1937_zones"
              ? "1937 HOLC Residential Security Map (NARA RG 195) · Mapping Inequality &amp; HARC Heat Watch"
              : overlayLayer === "hahc_coa_filings"
              ? "City of Houston Archaeological &amp; Historical Commission (HAHC) · Chapter 33 COA &amp; Designation Docket"
              : "City of Houston &amp; HCAD Boundary Index")
          }</span>
        </div>
      </div>

      ${(() => {
        if (overlayLayer === "hahc_coa_filings") {
          let parsedFilings = [];
          if (Array.isArray(rawProps.filings)) {
            parsedFilings = rawProps.filings;
          } else if (typeof rawProps.filings_json === "string" && rawProps.filings_json.trim()) {
            try {
              parsedFilings = JSON.parse(rawProps.filings_json);
            } catch (_e) {}
          }
          if (!parsedFilings.length && rawProps.hcad_num && this.mapController) {
            parsedFilings = this.mapController.getHahcFilingsForBuilding({
              hcadNum: rawProps.hcad_num,
            });
          }
          const parcelCard = this._buildHahcParcelCardHtml(
            parsedFilings,
            rawProps.historic_district || ""
          );
          const distCard = rawProps.historic_district
            ? this._buildHahcDistrictPrecedentsCardHtml(
                rawProps.historic_district,
                rawProps.hcad_num || ""
              )
            : "";
          const inspectBldBtn = rawProps.hcad_num
            ? `<div style="margin-bottom:10px;">
                <button
                  type="button"
                  class="inspector-btn primary"
                  id="btn-hahc-inspect-building"
                  data-hahc-hcad="${String(rawProps.hcad_num).replace(/"/g, "&quot;")}"
                  data-hahc-lng="${Number(rawProps.lng) || ""}"
                  data-hahc-lat="${Number(rawProps.lat) || ""}"
                  style="width:100%;"
                >
                  &#127968; Inspect Underlying Building &amp; Protection Scorecard (HCAD ${rawProps.hcad_num})
                </button>
              </div>`
            : "";
          return `${inspectBldBtn}${parcelCard}${distCard}`;
        }
        if (overlayLayer === "historic_districts") {
          return this._buildHahcDistrictPrecedentsCardHtml(name, "");
        }
        return "";
      })()}

      ${redBookPhotoCardHtml}

      ${lostPhotoCardHtml}

      ${holcThermalBannerHtml}

      ${isolationCardHtml}

      ${boundaryDeedCardHtml}

      ${
        narrativeDesc
          ? `<div class="ph-verified-override-card" style="border-left-color:${badgeColor};">
              <div class="ph-verified-header">
                <span>${
                  overlayLayer === "holc_1937_zones"
                    ? "1937 HOLC Appraisal &amp; Archival Provenance (NARA RG 195)"
                    : isLinearOrPointOverlay
                    ? "Historical &amp; Architectural Significance"
                    : "Geographic &amp; Historical Context"
                }</span>
              </div>
              <div class="ph-verified-citation">
                ${narrativeDesc}
                ${
                  overlayLayer === "holc_1937_zones" && rawProps.nara_sheet_status
                    ? `<div style="margin-top:6px;font-size:11px;color:#cbd5e1;"><strong>NARA Archival Note:</strong> ${rawProps.nara_sheet_status}</div>`
                    : ""
                }
                ${this._buildNarrativeCitationsHtml({
                  citations: rawProps.citations,
                  contextId: `${overlayLayer}_${rawProps.id || name}`,
                  contextTitle: `${badgeLabel}: ${name}`,
                  excerpt: narrativeDesc,
                })}
              </div>
            </div>`
          : ""
      }

      ${holcComparisonTableHtml}

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
          overlayLayer === "lost_houston"
            ? `<div class="inspector-cell">
                <span class="cell-label">Completed / Opened</span>
                <span class="cell-value mono">${rawProps.year_built || "-"}</span>
              </div>
              <div class="inspector-cell">
                <span class="cell-label">Demolished / Destroyed</span>
                <span class="cell-value mono" style="color:#fda4af;">${rawProps.year_demolished || "-"} (${rawProps.demolition_cause || "Demolished"})</span>
              </div>
              <div class="inspector-cell">
                <span class="cell-label">Sanborn Construction Class</span>
                <span class="cell-value">${rawProps.sanborn_material_label || "Brick / Masonry"}</span>
              </div>
              <div class="inspector-cell">
                <span class="cell-label">Historic Scale</span>
                <span class="cell-value mono">${rawProps.stories || 2} stories (~${rawProps.height_m || 9}m)</span>
              </div>
              ${
                rawProps.sanborn_ref
                  ? `<div class="inspector-cell full">
                      <span class="cell-label">Verified Sanborn Fire Insurance Atlas Plate</span>
                      <span class="cell-value" style="color:#fde68a;">${rawProps.sanborn_ref}</span>
                    </div>`
                  : ""
              }
              ${
                rawProps.architect
                  ? `<div class="inspector-cell full">
                      <span class="cell-label">Architect / Builder</span>
                      <span class="cell-value">${rawProps.architect}</span>
                    </div>`
                  : ""
              }
              ${
                rawProps.style
                  ? `<div class="inspector-cell full">
                      <span class="cell-label">Architectural Style</span>
                      <span class="cell-value">${rawProps.style}</span>
                    </div>`
                  : ""
              }
              ${
                rawProps.replaced_by
                  ? `<div class="inspector-cell full">
                      <span class="cell-label">Present-Day Site / Replaced By</span>
                      <span class="cell-value">${rawProps.replaced_by}</span>
                    </div>`
                  : ""
              }
              ${
                primarySiteHcad
                  ? `<div class="inspector-cell full">
                      <span class="cell-label">Present-Day HCAD Tax Parcel(s) on Site</span>
                      <span class="cell-value mono">${lostSiteHcads.join(", ") || primarySiteHcad}</span>
                    </div>`
                  : ""
              }`
            : ""
        }
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
          overlayLayer === "historical_railroads" && (rawProps.charter_year || rawProps.opened_year)
            ? `<div class="inspector-cell">
                <span class="cell-label">Charter &amp; Opening Era</span>
                <span class="cell-value mono">${
                  rawProps.charter_year &&
                  rawProps.opened_year &&
                  String(rawProps.charter_year) !== String(rawProps.opened_year)
                    ? `Chartered ${rawProps.charter_year} · Opened ${rawProps.opened_year}`
                    : rawProps.opened_year
                    ? `Opened ${rawProps.opened_year}`
                    : `Chartered ${rawProps.charter_year}`
                }</span>
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
        ${
          overlayLayer === "red_book_1915"
            ? `<div class="inspector-cell">
                <span class="cell-label">1915 Red Book Address</span>
                <span class="cell-value mono" style="color:#fde68a;">${
                  rawProps.historic_address_1915 || "Recorded in 1915 Red Book"
                }${
                rawProps.addr_precision === "StreetIntersection"
                  ? " (Intersection)"
                  : ""
              }</span>
              </div>
              <div class="inspector-cell">
                <span class="cell-label">Present-Day Address / Parcel</span>
                <span class="cell-value mono">${
                  rawProps.modern_address || rawProps.historic_address_1915 || "Houston, TX"
                }</span>
              </div>
              <div class="inspector-cell full">
                <span class="cell-label">2026 Site Survival &amp; Preservation Status</span>
                <span class="cell-value" style="color:${
                  rawProps.survival_status === "surviving_era_structure"
                    ? "#fef08a"
                    : rawProps.survival_status === "cleared_or_row"
                    ? "#fda4af"
                    : "#cbd5e1"
                };font-weight:600;">${rawProps.survival_label || "Surveyed Site"}${
                rawProps.landmark_designation
                  ? ` · ${rawProps.landmark_designation}`
                  : ""
              }${
                rawProps.extant_building_name &&
                rawProps.extant_building_name.toLowerCase() !== name.toLowerCase()
                  ? ` (${rawProps.extant_building_name})`
                  : ""
              }</span>
              </div>
              ${
                rawProps.person_name &&
                rawProps.person_name.toLowerCase() !== name.toLowerCase()
                  ? `<div class="inspector-cell">
                      <span class="cell-label">${
                        rawProps.category === "church"
                          ? "Pastor (1915)"
                          : rawProps.category === "business"
                          ? "Proprietor / Principal"
                          : "Honoree / Resident"
                      }</span>
                      <span class="cell-value">${rawProps.person_name}</span>
                    </div>`
                  : ""
              }
              ${
                rawProps.occupation
                  ? `<div class="inspector-cell">
                      <span class="cell-label">1915 Profession / Occupation</span>
                      <span class="cell-value">${rawProps.occupation}</span>
                    </div>`
                  : ""
              }
              ${
                rawProps.business_type
                  ? `<div class="inspector-cell">
                      <span class="cell-label">1915 Enterprise / Institution</span>
                      <span class="cell-value">${rawProps.business_type}</span>
                    </div>`
                  : ""
              }
              ${
                rawProps.teaches_at
                  ? `<div class="inspector-cell">
                      <span class="cell-label">1915 Teaching Appointment</span>
                      <span class="cell-value">${rawProps.teaches_at}</span>
                    </div>`
                  : ""
              }
              ${
                rawProps.church_attended
                  ? `<div class="inspector-cell">
                      <span class="cell-label">Church Congregation (1915)</span>
                      <span class="cell-value">${rawProps.church_attended}</span>
                    </div>`
                  : ""
              }
              ${
                rawProps.school_attended
                  ? `<div class="inspector-cell">
                      <span class="cell-label">Education / Alma Mater</span>
                      <span class="cell-value">${rawProps.school_attended}</span>
                    </div>`
                  : ""
              }
              ${
                rawProps.lodges_clubs
                  ? `<div class="inspector-cell full">
                      <span class="cell-label">Fraternal Orders, Lodges &amp; Civic Clubs (1915)</span>
                      <span class="cell-value">${rawProps.lodges_clubs}</span>
                    </div>`
                  : ""
              }
              ${
                rawProps.birthplace || Number(rawProps.birth_year) > 1800
                  ? `<div class="inspector-cell">
                      <span class="cell-label">Birthplace &amp; Birth Year</span>
                      <span class="cell-value">${[
                        rawProps.birthplace || "",
                        Number(rawProps.birth_year) > 1800 ? `b. ${rawProps.birth_year}` : "",
                      ]
                        .filter(Boolean)
                        .join(" · ")}</span>
                    </div>`
                  : ""
              }
              ${
                rawProps.married_to || rawProps.children
                  ? `<div class="inspector-cell">
                      <span class="cell-label">Family Recorded in 1915</span>
                      <span class="cell-value">${[
                        rawProps.married_to ? `Spouse: ${rawProps.married_to}` : "",
                        rawProps.children ? `Children: ${rawProps.children}` : "",
                      ]
                        .filter(Boolean)
                        .join(" · ")}</span>
                    </div>`
                  : ""
              }
              ${
                rawProps.holc_1937_grade
                  ? `<div class="inspector-cell full">
                      <span class="cell-label">Subsequent 1937 HOLC Redlining Grade Assigned to Area</span>
                      <span class="cell-value inspector-chip-group">
                        <button
                          type="button"
                          class="holc-grade-chip grade-${rawProps.holc_1937_grade}"
                          data-highlight-boundary="${String(
                            rawProps.holc_1937_zone_id || rawProps.holc_1937_zone_code || ""
                          ).replace(/"/g, "&quot;")}"
                          data-boundary-layer="holc_1937_zones"
                          title="Inspect 1937 HOLC Zone ${rawProps.holc_1937_zone_code || rawProps.holc_1937_grade} on the map"
                        >
                          1937 HOLC ${rawProps.holc_1937_zone_code || `Grade ${rawProps.holc_1937_grade}`} (${
                      rawProps.holc_1937_grade === "D"
                        ? "Hazardous / Redlined"
                        : rawProps.holc_1937_grade === "C"
                        ? "Definitely Declining"
                        : rawProps.holc_1937_grade === "B"
                        ? "Still Desirable"
                        : "Best"
                    }) &rarr;
                        </button>
                      </span>
                    </div>`
                  : ""
              }
              ${
                rawProps.sanborn_1907_covered || Number(rawProps.sanborn_volumes_count) > 0
                  ? `<div class="inspector-cell full">
                      <span class="cell-label">Sanborn Fire Insurance Atlas Coverage</span>
                      <span class="cell-value">${
                        rawProps.sanborn_1907_covered
                          ? "Covered by Georeferenced 1907 Sanborn Mosaic"
                          : "Covered by Archival Sanborn Volumes"
                      }${
                      Number(rawProps.sanborn_volumes_count) > 0
                        ? ` (${rawProps.sanborn_volumes_count} Sanborn volume bounds)`
                        : ""
                    }</span>
                    </div>`
                  : ""
              }`
            : ""
        }
        ${
          overlayLayer === "holc_1937_zones"
            ? `<div class="inspector-cell">
                <span class="cell-label">Surviving Pre-1938 Structures</span>
                <span class="cell-value mono">${Number(
                  rawProps.pre_1938_count || 0
                ).toLocaleString()}</span>
              </div>
              <div class="inspector-cell">
                <span class="cell-label">Post-1990 Redevelopment</span>
                <span class="cell-value mono">${Number(
                  rawProps.post_1990_count || 0
                ).toLocaleString()}</span>
              </div>
              <div class="inspector-cell">
                <span class="cell-label">COH Landmarks (PLM vs Waiver)</span>
                <span class="cell-value mono">${Number(
                  rawProps.plm_count || 0
                )} Protected · ${Number(rawProps.vulnerable_lm_count || 0)} Waiver</span>
              </div>
              <div class="inspector-cell">
                <span class="cell-label">Ch. 42 Block Armor &amp; Deeds</span>
                <span class="cell-value mono">${Number(
                  rawProps.smls_block_count || 0
                )} SMLS · ${Number(rawProps.smbl_block_count || 0)} SMBL · ${Number(
                rawProps.deed_subdiv_count || 0
              )} Deed</span>
              </div>
              <div class="inspector-cell">
                <span class="cell-label">1915 Red Book Heritage Sites</span>
                <span class="cell-value mono" style="color:#fde68a;">${Number(
                  rawProps.red_book_1915_count || 0
                ).toLocaleString()} (${Number(
                rawProps.red_book_1915_surviving_count || 0
              )} surviving)</span>
              </div>
              <div class="inspector-cell">
                <span class="cell-label">Lost Houston &amp; Active Alerts</span>
                <span class="cell-value mono">${Number(
                  rawProps.lost_houston_count || 0
                )} Lost · ${Number(rawProps.active_permit_alert_count || 0)} Permits · ${Number(
                rawProps.active_plat_alert_count || 0
              )} Plats</span>
              </div>
              ${
                parseJsonList(rawProps.top_neighborhoods).length > 0
                  ? `<div class="inspector-cell full">
                      <span class="cell-label">Neighborhoods Intersecting ${
                        rawProps.zone_code || "This HOLC Zone"
                      }</span>
                      <span class="cell-value inspector-chip-group">
                        ${parseJsonList(rawProps.top_neighborhoods)
                          .map(
                            (nh) => `<button
                              type="button"
                              class="inspector-filter-chip"
                              data-filter-chip="${nh.replace(/"/g, "&quot;")}"
                              data-filter-label="Neighborhood: ${nh.replace(/"/g, "&quot;")}"
                            >${nh} &#128269;</button>`
                          )
                          .join('<span class="inspector-chip-sep" aria-hidden="true">·</span>')}
                      </span>
                    </div>`
                  : ""
              }
              ${
                parseJsonList(rawProps.coh_historic_districts).length > 0 ||
                parseJsonList(rawProps.nrhp_districts).length > 0
                  ? `<div class="inspector-cell full">
                      <span class="cell-label">Designated COH &amp; NRHP Historic Districts in Zone</span>
                      <span class="cell-value">${[
                        ...parseJsonList(rawProps.coh_historic_districts),
                        ...parseJsonList(rawProps.nrhp_districts).map((d) => `${d} (NRHP)`),
                      ].join(" · ")}</span>
                    </div>`
                  : ""
              }`
            : ""
        }
        ${
          overlayLayer === "nrhp_properties"
            ? `<div class="inspector-cell">
                <span class="cell-label">National Register Ref #</span>
                <span class="cell-value mono">${rawProps.refnum ? `#${rawProps.refnum}` : "Listed"}</span>
              </div>
              <div class="inspector-cell">
                <span class="cell-label">Date Listed on NRHP</span>
                <span class="cell-value mono">${rawProps.listed_date || "-"}</span>
              </div>
              <div class="inspector-cell full">
                <span class="cell-label">Recorded Historic Address</span>
                <span class="cell-value">${rawProps.address || "Houston, Harris County, TX"}</span>
              </div>
              <div class="inspector-cell full">
                <span class="cell-label">Preservation &amp; Tax-Credit Legal Status</span>
                <span class="cell-value" style="color:${rawProps.is_demolished_nrhp ? "#fda4af" : "#e9d5ff"};">${
                  rawProps.local_protection_status ||
                  (rawProps.is_demolished_nrhp
                    ? "Demolished after National Register listing. Illustrates that federal NRHP status does not block private-funds demolition without local COH Chapter 33 protection."
                    : "Listed in the National Register of Historic Places (up to 45% combined Federal + Texas historic tax credits for qualifying income-producing / nonprofit rehabilitations; no municipal demolition ban unless also protected under COH Chapter 33).")
                }</span>
              </div>
              ${
                rawProps.multiname
                  ? `<div class="inspector-cell full">
                      <span class="cell-label">Multiple Property Submission (MPS)</span>
                      <span class="cell-value">${rawProps.multiname}</span>
                    </div>`
                  : ""
              }`
            : ""
        }
        ${
          (overlayLayer === "potential_historic_districts" ||
            overlayLayer === "historic_districts" ||
            overlayLayer === "nrhp_districts" ||
            overlayLayer === "cultural_districts") &&
          (pre1945Count > 0 ||
            Number(rawProps.smls_block_count) > 0 ||
            Number(rawProps.smbl_block_count) > 0 ||
            Number(rawProps.active_permit_alert_count) > 0 ||
            Number(rawProps.active_plat_alert_count) > 0)
            ? `<div class="inspector-cell">
                <span class="cell-label">Pre-1946 Historic Structures</span>
                <span class="cell-value mono">${pre1945Count.toLocaleString()}</span>
              </div>
              <div class="inspector-cell">
                <span class="cell-label">Ch. 42 Lot / Setback Blocks</span>
                <span class="cell-value mono">${Number(rawProps.smls_block_count || 0)} SMLS · ${Number(rawProps.smbl_block_count || 0)} SMBL</span>
              </div>
              <div class="inspector-cell full">
                <span class="cell-label">Active Demolition / Permit &amp; Plat Filings Inside Boundary</span>
                <span class="cell-value mono" style="color:${
                  Number(rawProps.active_permit_alert_count || 0) > 0 ? "#fda4af" : "#cbd5e1"
                };">${Number(rawProps.active_permit_alert_count || 0)} Permit Alert${
                  Number(rawProps.active_permit_alert_count || 0) === 1 ? "" : "s"
                } · ${Number(rawProps.active_plat_alert_count || 0)} Replat Filing${
                  Number(rawProps.active_plat_alert_count || 0) === 1 ? "" : "s"
                }</span>
              </div>`
            : ""
        }
        ${topSubsHtml}
        <div class="inspector-cell full">
          <span class="cell-label">Cartographic &amp; Archival Data Source</span>
          <span class="cell-value">${
            rawProps.source ||
            (overlayLayer === "nrhp_properties"
              ? "National Park Service & Texas Historical Commission NRHP Atlas"
              : overlayLayer === "potential_historic_districts"
              ? "City of Houston Planning & Development Dept. (Nov. 2019 Historic Survey)"
              : overlayLayer === "cultural_districts"
              ? "City of Houston Mayor's Office of Cultural Affairs & Texas Commission on the Arts"
              : "City of Houston & HCAD GIS")
          }</span>
        </div>
      </div>

      <div class="inspector-actions">
        ${
          overlayLayer === "nrhp_properties"
            ? `
              ${
                rawProps.nomination_pdf_url
                  ? `<a
                      href="${rawProps.nomination_pdf_url}"
                      target="_blank"
                      rel="noopener noreferrer"
                      class="inspector-btn primary"
                      style="background:linear-gradient(135deg,#6b21a8,#9333ea);border-color:#d8b4fe;color:#faf5ff;"
                    >
                      &#128196; Open Scanned NRHP Nomination (PDF) &#8599;
                    </a>`
                  : ""
              }
              ${
                rawProps.thc_atlas_url
                  ? `<a
                      href="${rawProps.thc_atlas_url}"
                      target="_blank"
                      rel="noopener noreferrer"
                      class="inspector-btn secondary"
                    >
                      &#127963; Open THC Historic Sites Atlas Record &#8599;
                    </a>`
                  : ""
              }
              <a
                href="${lostStreetViewUrl}"
                target="_blank"
                rel="noopener noreferrer"
                class="inspector-btn secondary"
              >
                &#128065; Present-Day Site on Street View &#8599;
              </a>
            `
            : overlayLayer === "lost_houston"
            ? `
              ${
                rawProps.loc_volume_id
                  ? `<button
                      type="button"
                      class="inspector-btn primary"
                      id="btn-lost-houston-open-sanborn-sheet"
                      data-loc-vol="${rawProps.loc_volume_id}"
                      data-loc-sheet="${rawProps.loc_sheet_code || rawProps.loc_sheet_num || ""}"
                      data-loc-sp="${rawProps.loc_sp || ""}"
                      style="background:linear-gradient(135deg,#92400e,#d97706);border-color:#fde68a;color:#fffbeb;"
                    >
                      &#128220; Inspect Sheet ${rawProps.loc_sheet_num || ""} in Sanborn Atlas Browser
                    </button>`
                  : ""
              }
              <button
                type="button"
                class="inspector-btn primary"
                id="btn-lost-houston-overlay-sanborn"
                style="background:linear-gradient(135deg,#047857,#10B981);border-color:#6EE7B7;color:#ECFDF5;"
              >
                &#128506; Overlay 1907 Sanborn Atlas on Map
              </button>
              ${
                primarySiteHcad
                  ? `<button
                      type="button"
                      class="inspector-btn secondary"
                      id="btn-lost-houston-inspect-modern"
                      data-modern-hcad="${primarySiteHcad}"
                    >
                      &#127970; Inspect Modern Parcel on This Site (${primarySiteHcad})
                    </button>`
                  : ""
              }
            `
            : overlayLayer === "red_book_1915"
            ? `
              ${
                primarySiteHcad
                  ? `<button
                      type="button"
                      class="inspector-btn primary"
                      id="btn-red-book-inspect-site"
                      data-modern-hcad="${primarySiteHcad}"
                      data-modern-bld="${String(rawProps.building_id || "").replace(/"/g, "&quot;")}"
                      style="background:linear-gradient(135deg,#92400e,#d97706);border-color:#fde68a;color:#fffbeb;"
                    >
                      &#127963; ${
                        rawProps.survival_status === "surviving_era_structure"
                          ? `Inspect Surviving Building on Site (${rawProps.extant_year_built || "Landmark"})`
                          : `Inspect Modern Parcel on This Site (${primarySiteHcad})`
                      }
                    </button>`
                  : ""
              }
              ${
                rawProps.rice_photo_url
                  ? `<a
                      href="${rawProps.rice_photo_url}"
                      target="_blank"
                      rel="noopener noreferrer"
                      class="inspector-btn ${primarySiteHcad ? "secondary" : "primary"}"
                    >
                      &#128247; Open 1915 Plate at Rice Woodson Archive &#8599;
                    </a>`
                  : ""
              }
              <a
                href="${
                  rawProps.hpl_book_url ||
                  "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/182"
                }"
                target="_blank"
                rel="noopener noreferrer"
                class="inspector-btn secondary"
              >
                &#128214; Open Digitized 1915 Red Book (HPL Archive) &#8599;
              </a>
              ${
                rawProps.sanborn_1907_covered
                  ? `<button
                      type="button"
                      class="inspector-btn secondary"
                      id="btn-red-book-drape-sanborn"
                    >
                      &#128506; Drape 1907 Sanborn Mosaic Under Pin
                    </button>`
                  : ""
              }
              <button
                type="button"
                class="inspector-btn secondary"
                id="btn-red-book-copy-citydir"
                data-citydir-query="${String(
                  rawProps.historic_address_1915 || rawProps.person_name || name
                ).replace(/"/g, "&quot;")}"
              >
                &#128218; Copy 1915 Address &amp; Open 1866-1926 City Directories &#8599;
              </button>
              <a
                href="${lostStreetViewUrl}"
                target="_blank"
                rel="noopener noreferrer"
                class="inspector-btn secondary"
              >
                &#128065; Present-Day Site on Street View &#8599;
              </a>
            `
            : overlayLayer === "holc_1937_zones"
            ? `
              <button
                type="button"
                class="inspector-btn primary"
                id="btn-holc-drape-scan"
                style="background:linear-gradient(135deg,#78350f,#b45309);border-color:#fde68a;color:#fffbeb;"
              >
                &#128506; Drape 1937 Original HOLC Map Scan Under Zone
              </button>
              <button
                type="button"
                class="inspector-btn secondary"
                id="btn-holc-show-redbook"
              >
                &#128214; Overlay 1915 Red Book Heritage Sites (${Number(
                  rawProps.red_book_1915_count || 0
                )} in ${rawProps.zone_code || "Zone"})
              </button>
              ${
                rawProps.mapping_inequality_url
                  ? `<a
                      href="${rawProps.mapping_inequality_url}"
                      target="_blank"
                      rel="noopener noreferrer"
                      class="inspector-btn secondary"
                    >
                      &#127963; Open Mapping Inequality Archive (Univ. of Richmond) &#8599;
                    </a>`
                  : ""
              }
              <button
                type="button"
                class="inspector-btn secondary"
                id="btn-explore-boundary-buildings"
                data-filter-chip="${String(
                  parseJsonList(rawProps.top_neighborhoods)[0] || name
                ).replace(/"/g, "&quot;")}"
                data-filter-label="${badgeLabel}: ${name.replace(/"/g, "&quot;")}"
              >
                &#128269; Explore Structures in ${rawProps.zone_code || name}
              </button>
            `
            : isLinearOrPointOverlay
            ? ""
            : `
              ${
                overlayLayer === "cultural_districts" && rawProps.website
                  ? `<a
                      href="${rawProps.website}"
                      target="_blank"
                      rel="noopener noreferrer"
                      class="inspector-btn primary"
                      style="background:linear-gradient(135deg,#9d174d,#db2777);border-color:#f9a8d4;color:#fdf2f8;"
                    >
                      &#127912; Open Official Cultural District Website &#8599;
                    </a>`
                  : ""
              }
              <button
                type="button"
                class="inspector-btn ${overlayLayer === "cultural_districts" && rawProps.website ? "secondary" : "primary"}"
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
    this._bindQuickFlagEvents(content, rawProps);

    if (overlayLayer === "lost_houston") {
      this._populateInspectorPhotos({
        props: {
          ...rawProps,
          id: rawProps.id,
          building_name: name,
          landmark_name: name,
        },
        title: name,
        subtitle,
        hcadNum: primarySiteHcad,
        lat: lostLat,
        lng: lostLng,
        streetViewUrl: lostStreetViewUrl,
      });

      const btnOpenSanbornSheet = document.getElementById("btn-lost-houston-open-sanborn-sheet");
      if (btnOpenSanbornSheet) {
        btnOpenSanbornSheet.addEventListener("click", () => {
          const volId = btnOpenSanbornSheet.getAttribute("data-loc-vol") || "";
          const sheetCode = btnOpenSanbornSheet.getAttribute("data-loc-sheet") || "";
          const locSp = btnOpenSanbornSheet.getAttribute("data-loc-sp") || "";
          this.openSanbornBrowserModal(volId, sheetCode, locSp);
        });
      }

      const btnOverlaySanborn = document.getElementById("btn-lost-houston-overlay-sanborn");
      if (btnOverlaySanborn) {
        btnOverlaySanborn.addEventListener("click", () => {
          const curLayers = this.filterStore.getState().layers || {};
          this.filterStore.setState({
            historicMapId: "sanborn_1907",
            layers: {
              ...curLayers,
              historicMap: true,
              lostHouston: true,
            },
          });
        });
      }

      const btnInspectModern = document.getElementById("btn-lost-houston-inspect-modern");
      if (btnInspectModern) {
        btnInspectModern.addEventListener("click", () => {
          const modernHcad = btnInspectModern.getAttribute("data-modern-hcad") || "";
          if (modernHcad && this.mapController) {
            this.mapController.selectFeatureByIdOrHcad({
              hcadNum: modernHcad,
              flyTo: true,
            });
          }
        });
      }
    }

    if (overlayLayer === "red_book_1915") {
      const btnRbInspectSite = document.getElementById("btn-red-book-inspect-site");
      if (btnRbInspectSite) {
        btnRbInspectSite.addEventListener("click", () => {
          const siteHcad = btnRbInspectSite.getAttribute("data-modern-hcad") || "";
          const siteBld = btnRbInspectSite.getAttribute("data-modern-bld") || "";
          if ((siteHcad || siteBld) && this.mapController) {
            this.mapController.selectFeatureByIdOrHcad({
              hcadNum: siteHcad,
              featureId: siteBld,
              flyTo: true,
            });
          }
        });
      }

      const btnRbDrapeSanborn = document.getElementById("btn-red-book-drape-sanborn");
      if (btnRbDrapeSanborn) {
        btnRbDrapeSanborn.addEventListener("click", () => {
          const curLayers = this.filterStore.getState().layers || {};
          this.filterStore.setState({
            historicMapId: "sanborn_1907",
            layers: {
              ...curLayers,
              redBook1915: true,
              historicMap: true,
            },
          });
          btnRbDrapeSanborn.innerHTML = "&#10003; 1907 Sanborn Mosaic Draped";
        });
      }

      const btnRbCopyCitydir = document.getElementById("btn-red-book-copy-citydir");
      if (btnRbCopyCitydir) {
        btnRbCopyCitydir.addEventListener("click", () => {
          const q = btnRbCopyCitydir.getAttribute("data-citydir-query") || "";
          if (q && navigator.clipboard) {
            navigator.clipboard.writeText(q);
          }
          btnRbCopyCitydir.innerHTML = `&#10003; Copied "${q.slice(0, 24)}" · Opening HPL City Directories &#8599;`;
          window.open(
            "https://cdm17006.contentdm.oclc.org/digital/collection/citydir",
            "_blank",
            "noopener,noreferrer"
          );
        });
      }
    }

    if (overlayLayer === "holc_1937_zones") {
      const btnHolcDrape = document.getElementById("btn-holc-drape-scan");
      if (btnHolcDrape) {
        btnHolcDrape.addEventListener("click", () => {
          const curState = this.filterStore.getState();
          const curLayers = curState.layers || {};
          const alreadyOn = Boolean(
            curLayers.historicMap && curState.historicMapId === "holc_1937"
          );
          this.filterStore.setState({
            historicMapId: "holc_1937",
            layers: {
              ...curLayers,
              holc1937: true,
              historicMap: !alreadyOn,
            },
          });
          btnHolcDrape.innerHTML = !alreadyOn
            ? "&#10003; 1937 Original HOLC Map Scan Draped (Click to Hide)"
            : "&#128506; Drape 1937 Original HOLC Map Scan Under Zone";
        });
      }

      const btnHolcShowRb = document.getElementById("btn-holc-show-redbook");
      if (btnHolcShowRb) {
        btnHolcShowRb.addEventListener("click", () => {
          const curLayers = this.filterStore.getState().layers || {};
          this.filterStore.setState({
            redBookCategory: "all",
            layers: {
              ...curLayers,
              holc1937: true,
              redBook1915: true,
            },
          });
          btnHolcShowRb.innerHTML = "&#10003; 1915 Red Book Heritage Sites Visible on Map";
        });
      }
    }

    const btnHahcInspectBuilding = content.querySelector("#btn-hahc-inspect-building");
    if (btnHahcInspectBuilding) {
      btnHahcInspectBuilding.addEventListener("click", () => {
        const hcad = btnHahcInspectBuilding.getAttribute("data-hahc-hcad") || "";
        const lng = parseFloat(btnHahcInspectBuilding.getAttribute("data-hahc-lng"));
        const lat = parseFloat(btnHahcInspectBuilding.getAttribute("data-hahc-lat"));
        if (hcad && this.mapController) {
          this.mapController.clearHighlightedBoundary();
          this.mapController.selectFeatureByIdOrHcad({
            hcadNum: hcad,
            targetLng: Number.isFinite(lng) ? lng : null,
            targetLat: Number.isFinite(lat) ? lat : null,
            flyTo: false,
          });
        }
      });
    }

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
        const portfolioKind = chipBtn.getAttribute("data-portfolio-kind") || "";
        if (chipQuery) {
          if (portfolioKind) {
            this.activatePortfolioMode(portfolioKind, chipQuery);
          } else {
            this.triggerMetadataFilterSearch(chipQuery, chipLabel);
          }
        }
      });
    });

    this._bindHahcCardEvents(content);

    content.querySelectorAll("[data-highlight-boundary]").forEach((bBtn) => {
      bBtn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const bName = bBtn.getAttribute("data-highlight-boundary") || "";
        const bLayer = bBtn.getAttribute("data-boundary-layer") || "";
        if (bName && this.mapController) {
          if (bLayer === "holc_1937_zones") {
            this.filterStore.setLayerVisibility("holc1937", true);
          } else if (bLayer === "platted_subdivisions") {
            this.filterStore.setLayerVisibility("plattedSubdivisions", true);
          }
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
        this._updateUrlHash(this.filterStore.getState());
        this._refreshShareModalContent();
      });
    }

    this._updateUrlHash(this.filterStore.getState());
    this._refreshShareModalContent();
  }

  _syncInspectorLayoutControls() {
    const drawer = document.getElementById("inspector-drawer");
    const btnWide = document.getElementById("btn-toggle-inspector-wide");
    const btnMobile = document.getElementById("btn-cycle-mobile-sheet");
    const isWide = Boolean(this._inspectorWideMode);
    const sheetMode = this._mobileSheetMode || "half";

    if (drawer) {
      drawer.classList.toggle("wide-mode", isWide && !drawer.classList.contains("boundary-mode"));
      drawer.classList.remove("sheet-peek", "sheet-half", "sheet-full");
      drawer.classList.add(`sheet-${sheetMode}`);
    }

    if (btnWide) {
      btnWide.classList.toggle("active", isWide);
      btnWide.setAttribute("aria-pressed", isWide ? "true" : "false");
      btnWide.innerHTML = isWide ? "&#10529; Compact" : "&#10530; Wide View";
      btnWide.title = isWide
        ? "Switch back to Compact Single-Column Sidebar (364px)"
        : "Expand to 2-Column Wide Researcher View (760px)";
    }

    if (btnMobile) {
      const labelMap = {
        peek: "&#8593; Peek",
        half: "&#8597; Half",
        full: "&#8595; Full",
      };
      btnMobile.innerHTML = labelMap[sheetMode] || "&#8597; Half";
      btnMobile.classList.toggle("active", sheetMode === "full");
    }
  }

  _setMobileSheetMode(mode) {
    this._mobileSheetMode = ["peek", "half", "full"].includes(mode) ? mode : "half";
    this._syncInspectorLayoutControls();
  }

  _switchInspectorTab(tabKey, { scrollInWide = false } = {}) {
    const validTabs = ["overview", "history", "permits", "hcad"];
    const nextTab = validTabs.includes(tabKey) ? tabKey : "overview";
    this._activeInspectorTab = nextTab;

    const content = document.getElementById("inspector-body");
    const drawer = document.getElementById("inspector-drawer");
    if (!content) return;

    content.querySelectorAll("[data-inspector-tab]").forEach((btn) => {
      const isMatch = btn.getAttribute("data-inspector-tab") === nextTab;
      btn.classList.toggle("active", isMatch);
      btn.setAttribute("aria-selected", isMatch ? "true" : "false");
    });

    content.querySelectorAll("[data-tab-pane]").forEach((pane) => {
      const isMatch = pane.getAttribute("data-tab-pane") === nextTab;
      pane.classList.toggle("active", isMatch);
    });

    const compactAlertBanner = document.getElementById("inspector-compact-alert-banner");
    if (compactAlertBanner) {
      compactAlertBanner.style.display = nextTab === "permits" ? "none" : "";
    }

    if (typeof window !== "undefined" && window.innerWidth <= 900 && this._mobileSheetMode === "peek") {
      this._setMobileSheetMode("half");
    }

    if (
      scrollInWide &&
      drawer &&
      drawer.classList.contains("wide-mode") &&
      typeof window !== "undefined" &&
      window.innerWidth > 900
    ) {
      const targetPane = content.querySelector(`[data-tab-pane="${nextTab}"]`);
      if (targetPane) {
        targetPane.scrollIntoView({ behavior: "smooth", block: "nearest" });
        targetPane.classList.add("wide-focus-pulse");
        setTimeout(() => targetPane.classList.remove("wide-focus-pulse"), 1100);
      }
    }
  }

  _evaluatePropertyProtectionAndIncentives(
    props,
    {
      hcadNum = "",
      yr = 0,
      geoCtx = {},
      subPropsForDeed = null,
      catalogEntryForDeed = null,
      landmarkCode = "",
      reportPdfUrl = "",
    } = {}
  ) {
    const cleanDistStr = (val) => {
      const s = String(val || "").trim();
      if (
        !s ||
        s.toLowerCase() === "none" ||
        s === "Outside City District" ||
        s === "Outside Historic District"
      ) {
        return "";
      }
      return s;
    };

    const buildingId = String(props.building_id || props.id || "").trim();
    const lmRec =
      props._landmark_record ||
      (this.mapController && typeof this.mapController.getLandmarkForBuilding === "function"
        ? this.mapController.getLandmarkForBuilding({ hcadNum, buildingId })
        : null) ||
      null;
    const nrhpRec =
      props._nrhp_property_record ||
      (this.mapController && typeof this.mapController.getNrhpPropertyForBuilding === "function"
        ? this.mapController.getNrhpPropertyForBuilding({ hcadNum, buildingId })
        : null) ||
      null;

    const cohHdName =
      cleanDistStr(props.historic_district) ||
      cleanDistStr(lmRec?.historic_district) ||
      cleanDistStr(geoCtx.historic_district);
    const heritageDistName = String(geoCtx.heritage_district || "").trim();
    const nrhpDistricts = Array.isArray(geoCtx.nrhp_districts) ? geoCtx.nrhp_districts : [];
    const primaryNrhpDistName = String(
      props.nrhp_district || nrhpRec?.nrhp_district || nrhpDistricts[0]?.name || ""
    ).trim();
    const potentialDistricts = Array.isArray(geoCtx.potential_historic_districts)
      ? geoCtx.potential_historic_districts
      : [];
    const culturalDistricts = Array.isArray(geoCtx.cultural_districts)
      ? geoCtx.cultural_districts
      : [];
    const landUseProtections = Array.isArray(props._land_use_protections)
      ? props._land_use_protections
      : Array.isArray(geoCtx.land_use_protections)
      ? geoCtx.land_use_protections
      : [];

    const hasDeedDocs = Boolean(
      subPropsForDeed?.has_deed_docs ||
        (catalogEntryForDeed &&
          Array.isArray(catalogEntryForDeed.documents) &&
          catalogEntryForDeed.documents.length > 0)
    );
    const deedDocCount = Number(
      subPropsForDeed?.deed_doc_count || catalogEntryForDeed?.documents?.length || 0
    );

    const isPlm = Boolean(
      props.protection_tier === "plm" ||
        props.landmark_type === "Protected Landmark" ||
        lmRec?.is_protected_landmark ||
        lmRec?.protection_tier === "plm"
    );
    const isRegularLm = Boolean(
      !isPlm &&
        (props.landmark_type === "Landmark" ||
          props.protection_tier === "lm_waiver" ||
          (lmRec && !lmRec.is_protected_landmark))
    );
    const contribRaw = String(props.contributing || "").trim();
    const isNonContributingInHd = Boolean(
      cohHdName &&
        (contribRaw === "Non-Contributing" || props.protection_tier === "coh_hd_noncontributing")
    );
    const isContributingInHd = Boolean(cohHdName && !isNonContributingInHd);

    const hasIndividualNrhp = Boolean(
      (nrhpRec && !nrhpRec.is_demolished_nrhp) || String(props.nrhp_refnum || "").trim()
    );
    const nrhpRefnum = String(nrhpRec?.refnum || props.nrhp_refnum || "").trim();
    const nrhpResname = String(nrhpRec?.name || props.nrhp_resname || "").trim();
    const nrhpListedDate = String(nrhpRec?.listed_date || "").trim();
    const nrhpMultiname = String(nrhpRec?.multiname || "").trim();
    const nrhpThcUrl = String(nrhpRec?.thc_atlas_url || props.nrhp_thc_url || "").trim();
    const nrhpPdfUrl = String(nrhpRec?.nomination_pdf_url || props.nrhp_pdf_url || "").trim();
    const isEligibleInNrhpDist = Boolean(
      primaryNrhpDistName && !isNonContributingInHd && (yr === 0 || (yr >= 1836 && yr <= 1976))
    );

    const COH_TAX_BROCHURE_URL =
      "https://www.houstontx.gov/planning/HistoricPres/docs_pdfs/hist-incentives-broch.pdf";
    const COH_PLM_BROCHURE_URL =
      "https://www.houstontx.gov/planning/HistoricPres/docs_pdfs/histpres_benefits_brochure.pdf";
    const THC_TAX_CREDIT_URL =
      "https://thc.texas.gov/preserve/projects-and-programs/preservation-tax-incentives";
    const NPS_TAX_CREDIT_URL = "https://www.nps.gov/subjects/taxincentives/index.htm";

    // Pillar 1: Demolition & Exterior Alteration Rule (COH Chapter 33)
    let cardTier = "";
    let demoPillClass = "tier-neutral";
    let demoPillLabel = "No Municipal Demo Ban";
    let demoStatus = "No City of Houston Demolition Restriction";
    let demoCopy = "";
    const demoLinks = [];

    if (isPlm) {
      cardTier = "tier-protected";
      demoPillClass = "tier-plm";
      demoPillLabel = "Protected Landmark (PLM)";
      demoStatus = `Permanent Protected Landmark${landmarkCode ? ` (HPO #${landmarkCode})` : ""}`;
      demoCopy =
        `Designated as a City of Houston <strong>Protected Landmark (PLM)</strong> under Chapter 33${
          cohHdName ? ` within <strong>${cohHdName}</strong>` : ""
        }. Demolition, relocation, and exterior alterations require a Certificate of Appropriateness (COA) from the Houston Archaeological and Historical Commission (HAHC). The 90-day demolition waiver does <strong>not</strong> apply.`;
      if (reportPdfUrl) {
        demoLinks.push({
          label: "Landmark Designation Report (PDF)",
          url: reportPdfUrl,
          cls: "",
        });
      }
      demoLinks.push({
        label: "COH Chapter 33 Benefits Guide (PDF)",
        url: COH_PLM_BROCHURE_URL,
        cls: "",
      });
    } else if (isContributingInHd) {
      cardTier = "tier-protected";
      demoPillClass = "tier-safe";
      demoPillLabel = "COH Historic District COA";
      demoStatus = `Protected in ${cohHdName} (COA Required)`;
      demoCopy =
        `${
          isRegularLm
            ? "Designated City of Houston Landmark and contributing structure"
            : "Contributing historic structure"
        } inside <strong>${cohHdName}</strong> (Chapter 33). Exterior alterations visible from a public right-of-way, additions, relocation, and demolition require HAHC Certificate of Appropriateness (COA) approval. The 90-day waiver rule does <strong>not</strong> apply inside a COH Historic District.`;
      demoLinks.push({
        label: "COH Historic District & COA Guide (PDF)",
        url: COH_PLM_BROCHURE_URL,
        cls: "",
      });
    } else if (isRegularLm) {
      cardTier = "tier-waiver";
      demoPillClass = "tier-warn";
      demoPillLabel = "90-Day Waiver LM (Vulnerable)";
      demoStatus = `Non-Protected COH Landmark${landmarkCode ? ` (HPO #${landmarkCode})` : ""} - 90-Day Waiver Applies`;
      demoCopy =
        "Designated as a regular City of Houston <strong>Landmark (LM)</strong> located <strong>outside</strong> a COH Historic District. Under Chapter 33, if HAHC denies a demolition or relocation COA, the owner can obtain a <strong>90-day waiver certificate</strong> allowing demolition unless the owner voluntarily upgrades the property to a permanent <strong>Protected Landmark (PLM)</strong>.";
      demoLinks.push({
        label: "Upgrade to Protected Landmark Guide (PDF)",
        url: COH_PLM_BROCHURE_URL,
        cls: "amber",
      });
      if (reportPdfUrl) {
        demoLinks.push({
          label: "Landmark Report (PDF)",
          url: reportPdfUrl,
          cls: "",
        });
      }
    } else if (isNonContributingInHd) {
      cardTier = "tier-protected";
      demoPillClass = "tier-armor";
      demoPillLabel = "In COH HD (Non-Contributing)";
      demoStatus = `Non-Contributing Parcel in ${cohHdName}`;
      demoCopy =
        `Located inside <strong>${cohHdName}</strong> as a non-contributing structure. While demolition of a non-contributing building is not barred, any <strong>new construction, additions, setbacks, or massing changes</strong> on this lot require HAHC Certificate of Appropriateness (COA) review under district design guidelines.`;
    } else {
      const isHistoricPre1946 = yr >= 1836 && yr <= 1945;
      if (isHistoricPre1946) {
        cardTier = "tier-vulnerable";
        demoPillClass = "tier-danger";
        demoPillLabel = "No City Demo Ban";
      }
      if (hasIndividualNrhp || primaryNrhpDistName) {
        const nrhpLabel = hasIndividualNrhp
          ? `National Register property (${nrhpResname || `NRHP #${nrhpRefnum}`})`
          : `<strong>${primaryNrhpDistName}</strong> (National Register Historic District)`;
        demoCopy =
          `Listed in the federal ${nrhpLabel}, which unlocks major historic rehabilitation tax credits, <strong>not</strong> a municipal demolition ban. Under Houston law, NRHP listing alone does not restrict remodeling or demolition funded with private capital unless the owner designates the site as a COH Protected Landmark (PLM).`;
      } else if (potentialDistricts.length > 0) {
        const pot = potentialDistricts[0];
        demoCopy =
          `Located within <strong>${pot.name}</strong> (identified in the Nov. 2019 COH Historic Preservation Survey). Surveyed status documents historic character for future National Register tax-credit nomination or owner-initiated Chapter 33 petitioning, and provides <strong>no active demolition restriction</strong>.`;
      } else if (heritageDistName || culturalDistricts.length > 0) {
        const areaName = heritageDistName || culturalDistricts[0].name;
        demoCopy =
          `Located within <strong>${areaName}</strong>. Community Heritage and State Cultural Arts Districts support public-realm investments and cultural grants without restricting private-parcel demolition. Property owners may apply for permanent COH Protected Landmark (PLM) protection.`;
      } else if (isHistoricPre1946) {
        demoCopy =
          `Pre-1946 historic structure (Built ${yr}) outside City of Houston Chapter 33 protection. Standard demolition permits require no HAHC review. The property owner may apply for permanent <strong>Protected Landmark (PLM)</strong> status, or neighborhood owners may petition for a COH Historic District.`;
      } else {
        demoCopy =
          "Outside City of Houston Chapter 33 Historic Districts and not designated as a municipal Landmark. Demolition and exterior alterations follow standard Houston Building Code permitting without HAHC design review.";
      }
      if (yr >= 1836 && yr <= 1976) {
        demoLinks.push({
          label: "COH Protected Landmark Guide (PDF)",
          url: COH_PLM_BROCHURE_URL,
          cls: "",
        });
      }
    }

    // Pillar 2: Lot-Split & Setback Armor (COH Chapter 42 & Deed Restrictions)
    let minLotSqft = 0;
    let minSetbackFt = 0;
    let hasConservationDist = false;
    const ordList = [];
    for (const lp of landUseProtections) {
      const pt = String(lp.protection_type || "").toLowerCase();
      if (pt === "conservation") hasConservationDist = true;
      if (Number(lp.min_lot_sqft) > minLotSqft) minLotSqft = Number(lp.min_lot_sqft);
      if (Number(lp.min_bldg_line_ft) > minSetbackFt) minSetbackFt = Number(lp.min_bldg_line_ft);
      if (lp.ordinance) ordList.push(`#${lp.ordinance}`);
    }

    let armorPillClass = "tier-neutral";
    let armorPillLabel = "No Ch. 42 SMLS / SMBL";
    let armorStatus = "Standard Chapter 42 Subdivision Rules";
    let armorCopy = "";

    if (landUseProtections.length > 0 || hasDeedDocs) {
      armorPillClass = "tier-armor";
      const pillBits = [];
      if (hasConservationDist) pillBits.push("Conservation Dist");
      if (minLotSqft > 0) pillBits.push(`Min Lot ${minLotSqft.toLocaleString()} sf`);
      else if (landUseProtections.some((x) => x.protection_type === "smls")) pillBits.push("Ch. 42 SMLS");
      if (minSetbackFt > 0) pillBits.push(`${minSetbackFt}ft Setback`);
      else if (landUseProtections.some((x) => x.protection_type === "smbl")) pillBits.push("Ch. 42 SMBL");
      if (hasDeedDocs) pillBits.push(`Deeds (${deedDocCount} PDF)`);
      armorPillLabel = pillBits.slice(0, 2).join(" + ") || "Ch. 42 / Deed Armor";

      const statusBits = [];
      if (minLotSqft > 0) statusBits.push(`SMLS Min Lot ${minLotSqft.toLocaleString()} sq ft`);
      if (minSetbackFt > 0) statusBits.push(`SMBL Min Setback ${minSetbackFt} ft`);
      if (hasConservationDist) statusBits.push("COH Conservation District");
      if (hasDeedDocs) statusBits.push(`${deedDocCount} Recorded Deed Restriction PDF${deedDocCount === 1 ? "" : "s"}`);
      armorStatus = statusBits.join(" · ") || "Active Chapter 42 Block Ordinance";

      armorCopy =
        `${
          landUseProtections.length > 0
            ? `Protected against high-density townhouse lot-splitting or setback encroachment by City of Houston Chapter 42 ordinance${
                ordList.length ? ` (${ordList.slice(0, 3).join(", ")})` : ""
              }. `
            : ""
        }${
          hasDeedDocs
            ? "County Clerk subdivision deed restriction PDFs are indexed on the HCAD & Parcel tab."
            : "Chapter 42 SMLS/SMBL ordinances preserve neighborhood lot scale and front-yard setbacks for 40 years even where no Chapter 33 demolition ban applies."
        }`;
    } else {
      armorCopy =
        "No active Chapter 42 Special Minimum Lot Size (SMLS), Special Minimum Building Line (SMBL), or Conservation District ordinance detected on this block. Private subdivision deed covenants (if unindexed) may still govern use and setbacks; block homeowners can petition COH Planning for SMLS/SMBL protection.";
    }

    // Pillar 3: Historic Tax Exemptions & Rehabilitation Tax Credits
    const eligibleCohTaxExemption = Boolean(isPlm || isRegularLm || isContributingInHd);
    const eligibleNrhpTaxCredits = Boolean(hasIndividualNrhp || isEligibleInNrhpDist);
    const inPotentialNrhpSurvey = Boolean(
      !eligibleNrhpTaxCredits &&
        potentialDistricts.length > 0 &&
        (yr === 0 || (yr >= 1836 && yr <= 1976))
    );

    let creditPillClass = "tier-neutral";
    let creditPillLabel = "No Active Historic Credits";
    let creditStatus = "Not Currently Designated for Historic Tax Incentives";
    let creditCopy = "";
    const creditLinks = [];

    if (eligibleCohTaxExemption && eligibleNrhpTaxCredits) {
      creditPillClass = "tier-credit";
      creditPillLabel = "COH 15-Yr Exemp + 45% Credits";
      creditStatus = "Stackable: COH 15-Yr Property Tax Exemption + Up to 45% Fed/TX Credits";
      creditCopy =
        "Qualifies for both the <strong>City of Houston 15-Year Historic Site Tax Exemption</strong> (on approved rehabilitation equal to at least 25% of base building value) <strong>and</strong> up to <strong>45% in combined historic rehabilitation tax credits</strong> (20% Federal IRS + 25% Texas Historic Preservation Tax Credit for qualifying commercial, rental, or nonprofit rehabilitations).";
    } else if (eligibleCohTaxExemption) {
      creditPillClass = "tier-credit";
      creditPillLabel = "COH 15-Yr Tax Exemption";
      creditStatus = "Eligible for COH 15-Year Historic Site Tax Exemption";
      creditCopy =
        `Qualifying historic rehabilitation (investing at least 25% of HCAD base improvement value with HAHC approval) earns up to a <strong>15-year City of Houston ad valorem property tax exemption</strong> under Chapter 44${
          isPlm ? ", plus historic building/plat permit fee waivers" : ""
        }.`;
    } else if (eligibleNrhpTaxCredits) {
      creditPillClass = "tier-credit";
      creditPillLabel = "Up to 45% Fed + TX Credits";
      creditStatus = `NRHP Tax-Credit Eligible (${
        hasIndividualNrhp ? `NRHP #${nrhpRefnum || "Listed"}` : primaryNrhpDistName
      })`;
      creditCopy =
        "Qualifying rehabilitation of income-producing (commercial, residential rental, office, hospitality) or nonprofit buildings meeting Secretary of the Interior Standards is eligible for the <strong>25% Texas Historic Preservation Tax Credit</strong> (min. $5,000 QRE) and <strong>20% Federal Historic Tax Credit</strong> (45% combined).";
    } else if (inPotentialNrhpSurvey) {
      creditPillClass = "tier-warn";
      creditPillLabel = "Surveyed NRHP Candidate";
      creditStatus = `2019 Surveyed Candidate (${potentialDistricts[0].name})`;
      creditCopy =
        "Identified in the 2019 COH Historic Survey as potentially eligible for the National Register of Historic Places. Completing an individual or district NRHP Part 1 Determination of Eligibility with the Texas Historical Commission can unlock the <strong>25% Texas + 20% Federal</strong> historic rehabilitation tax credits.";
    } else {
      creditCopy =
        yr >= 1836 && yr <= 1976
          ? `Historic-age structure (Built ${yr}) not yet listed on the National Register or designated under COH Chapter 33. Designating as a COH Protected Landmark unlocks the 15-year City Historic Site Tax Exemption; NRHP listing unlocks 20% Federal + 25% Texas commercial/nonprofit tax credits.`
          : "Post-1976 or undesignated parcel outside current municipal and National Register historic tax-incentive programs.";
    }

    if (eligibleCohTaxExemption || (yr >= 1836 && yr <= 1976)) {
      creditLinks.push({
        label: "COH 15-Yr Tax Exemption Brochure (PDF)",
        url: COH_TAX_BROCHURE_URL,
        cls: "",
      });
    }
    if (eligibleNrhpTaxCredits || inPotentialNrhpSurvey) {
      creditLinks.push({
        label: "Texas 25% Tax Credit (THC)",
        url: THC_TAX_CREDIT_URL,
        cls: "purple",
      });
      creditLinks.push({
        label: "Federal 20% Credit (NPS)",
        url: NPS_TAX_CREDIT_URL,
        cls: "purple",
      });
    }
    if (nrhpPdfUrl) {
      creditLinks.push({
        label: "Scanned NRHP Nomination (PDF)",
        url: nrhpPdfUrl,
        cls: "purple",
      });
    }
    if (nrhpThcUrl) {
      creditLinks.push({
        label: "THC Historic Sites Atlas",
        url: nrhpThcUrl,
        cls: "purple",
      });
    }

    return {
      cardTier,
      demoPillClass,
      demoPillLabel,
      demoStatus,
      demoCopy,
      demoLinks,
      armorPillClass,
      armorPillLabel,
      armorStatus,
      armorCopy,
      hasArmorDetailsOnHcadTab: Boolean(landUseProtections.length > 0 || hasDeedDocs),
      creditPillClass,
      creditPillLabel,
      creditStatus,
      creditCopy,
      creditLinks,
      cohHdName,
      heritageDistName,
      primaryNrhpDistName,
      potentialDistricts,
      culturalDistricts,
      hasIndividualNrhp,
      nrhpRefnum,
      nrhpResname,
      nrhpListedDate,
      nrhpMultiname,
      nrhpThcUrl,
      nrhpPdfUrl,
      isEligibleInNrhpDist,
    };
  }

  _buildProtectionScorecardHtml(ev) {
    if (!ev) return "";
    const isExpanded = Boolean(this._protectionScorecardExpanded);
    const renderLinks = (links, extraHtml = "") => {
      if ((!links || !links.length) && !extraHtml) return "";
      const pills = (links || [])
        .map(
          (l) =>
            `<a href="${l.url}" target="_blank" rel="noopener noreferrer" class="scorecard-link-pill ${
              l.cls || ""
            }">${l.label} &#8599;</a>`
        )
        .join("");
      return `<div class="scorecard-links-row">${pills}${extraHtml}</div>`;
    };

    const hcadJumpBtnHtml = ev.hasArmorDetailsOnHcadTab
      ? `<button type="button" class="scorecard-link-pill" data-switch-inspector-tab="hcad" style="cursor:pointer;">Inspect Deeds &amp; Ch. 42 Ordinances &rarr;</button>`
      : "";

    return `
      <div class="protection-scorecard-card ${ev.cardTier || ""} ${
      isExpanded ? "is-expanded" : ""
    }" id="inspector-protection-scorecard">
        <button
          type="button"
          class="protection-scorecard-strip"
          id="btn-toggle-protection-scorecard"
          aria-expanded="${isExpanded ? "true" : "false"}"
          title="Click to expand plain-English Houston Demolition, Lot-Split & Historic Tax-Credit rules for this parcel"
        >
          <div class="protection-scorecard-toprow">
            <span class="protection-scorecard-kicker">&#9878; Legal Protection &amp; Tax-Credit Scorecard</span>
            <span class="protection-scorecard-toggle-hint" id="protection-scorecard-toggle-hint">${
              isExpanded ? "Hide Rules &#9652;" : "Plain-English Rules &#9662;"
            }</span>
          </div>
          <div class="protection-scorecard-pills">
            <span class="scorecard-pill ${ev.demoPillClass}">${ev.demoPillLabel}</span>
            <span class="scorecard-pill ${ev.armorPillClass}">${ev.armorPillLabel}</span>
            <span class="scorecard-pill ${ev.creditPillClass}">${ev.creditPillLabel}</span>
          </div>
        </button>
        <div class="protection-scorecard-details">
          <div class="scorecard-pillar">
            <div class="scorecard-pillar-header">
              <span class="scorecard-pillar-kicker">1. Demolition &amp; Exterior Rule (COH Ch. 33)</span>
              <span class="scorecard-pillar-status">${ev.demoStatus}</span>
            </div>
            <p class="scorecard-pillar-copy">${ev.demoCopy}</p>
            ${renderLinks(ev.demoLinks)}
          </div>
          <div class="scorecard-pillar">
            <div class="scorecard-pillar-header">
              <span class="scorecard-pillar-kicker">2. Lot-Split &amp; Setback Armor (Ch. 42 &amp; Deeds)</span>
              <span class="scorecard-pillar-status">${ev.armorStatus}</span>
            </div>
            <p class="scorecard-pillar-copy">${ev.armorCopy}</p>
            ${renderLinks([], hcadJumpBtnHtml)}
          </div>
          <div class="scorecard-pillar">
            <div class="scorecard-pillar-header">
              <span class="scorecard-pillar-kicker">3. Historic Tax Exemptions &amp; Rehab Credits</span>
              <span class="scorecard-pillar-status">${ev.creditStatus}</span>
            </div>
            <p class="scorecard-pillar-copy">${ev.creditCopy}</p>
            ${renderLinks(ev.creditLinks)}
          </div>
        </div>
      </div>
    `;
  }

  _buildNrhpHistoryCardHtml(ev) {
    if (!ev || (!ev.hasIndividualNrhp && !ev.primaryNrhpDistName)) return "";
    const heading = ev.hasIndividualNrhp
      ? "&#127963; National Register of Historic Places (NRHP)"
      : "&#127963; National Register Historic District (NRHP)";
    const codeBadge = ev.nrhpRefnum
      ? `NRHP #${ev.nrhpRefnum}`
      : ev.isEligibleInNrhpDist
      ? "45% Credit District"
      : "NRHP District";
    const summaryText = ev.hasIndividualNrhp
      ? `Individually listed on the National Register of Historic Places as <strong>${
          ev.nrhpResname || "Historic Resource"
        }</strong>${ev.nrhpListedDate ? ` (Listed ${ev.nrhpListedDate})` : ""}${
          ev.nrhpMultiname ? ` under <em>${ev.nrhpMultiname}</em>` : ""
        }${
          ev.primaryNrhpDistName ? `, and located within <strong>${ev.primaryNrhpDistName}</strong>` : ""
        }. Qualifying income-producing or nonprofit rehabilitations are eligible for up to 45% in combined Federal (20%) and Texas (25%) historic preservation tax credits.`
      : `Located within the federal <strong>${ev.primaryNrhpDistName}</strong> National Register Historic District. Contributing historic structures rehabilitated for commercial, residential rental, or nonprofit use qualify for the 20% Federal and 25% Texas Historic Preservation Tax Credits.`;

    return `
      <div class="coh-landmark-report-card" style="background:linear-gradient(135deg,rgba(168,85,247,0.14),rgba(15,23,42,0.68));border-color:rgba(192,132,252,0.42);">
        <div class="coh-landmark-report-header">
          <span class="coh-landmark-report-kicker" style="color:#e9d5ff;">${heading}</span>
          <span class="coh-landmark-code-badge" style="background:rgba(168,85,247,0.24);border-color:rgba(192,132,252,0.52);color:#faf5ff;">${codeBadge}</span>
        </div>
        <div class="coh-landmark-report-summary">${summaryText}</div>
        <div class="coh-landmark-report-actions">
          ${
            ev.nrhpPdfUrl
              ? `<a
                  href="${ev.nrhpPdfUrl}"
                  target="_blank"
                  rel="noopener noreferrer"
                  class="coh-landmark-pdf-btn"
                  style="background:rgba(168,85,247,0.25);border-color:rgba(192,132,252,0.6);color:#faf5ff;"
                >
                  &#128196; Open Scanned NRHP Nomination Form (PDF) &#8599;
                </a>`
              : ""
          }
          ${
            ev.nrhpThcUrl
              ? `<a
                  href="${ev.nrhpThcUrl}"
                  target="_blank"
                  rel="noopener noreferrer"
                  class="coh-landmark-pdf-btn secondary"
                >
                  Open Texas Historical Commission (THC) Atlas Record &#8599;
                </a>`
              : ""
          }
          <a
            href="https://thc.texas.gov/preserve/projects-and-programs/preservation-tax-incentives"
            target="_blank"
            rel="noopener noreferrer"
            class="coh-landmark-pdf-btn secondary"
          >
            25% Texas + 20% Federal Historic Tax Credit Guide (THC) &#8599;
          </a>
        </div>
      </div>
    `;
  }

  _escapeResearchHtml(val) {
    return String(val ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  _pointInSingleRing(lng, lat, ring) {
    if (!Array.isArray(ring) || ring.length < 3) return false;
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const xi = ring[i][0];
      const yi = ring[i][1];
      const xj = ring[j][0];
      const yj = ring[j][1];
      const intersects =
        yi > lat !== yj > lat && lng < ((xj - xi) * (lat - yi)) / (yj - yi || 1e-12) + xi;
      if (intersects) inside = !inside;
    }
    return inside;
  }

  _pointInPolygonRings(lng, lat, geom) {
    if (!geom || !Array.isArray(geom.coordinates)) return false;
    const testPoly = (rings) => {
      if (!Array.isArray(rings) || rings.length === 0) return false;
      if (!this._pointInSingleRing(lng, lat, rings[0])) return false;
      for (let h = 1; h < rings.length; h++) {
        if (this._pointInSingleRing(lng, lat, rings[h])) return false;
      }
      return true;
    };
    if (geom.type === "Polygon") {
      return testPoly(geom.coordinates);
    }
    if (geom.type === "MultiPolygon") {
      return geom.coordinates.some((poly) => testPoly(poly));
    }
    return false;
  }

  _resolveSanbornCoverageAtPoint(lng, lat, yearBuilt = 0) {
    const boundaries = Array.isArray(this.sanbornCatalog?.volume_boundaries)
      ? this.sanbornCatalog.volume_boundaries
      : [];
    const eraOrder = {
      "1877": 0,
      "1885": 1,
      "1890": 2,
      "1896": 3,
      "1907": 4,
      "1924-1929": 5,
      "1946-1951": 6,
    };
    const catalogMap = {
      "1885|Volume 1": { volId: "sanborn08580_001", archive: "LOC" },
      "1890|Volume 1": { volId: "sanborn08580_002", archive: "LOC" },
      "1896|Volume 1": { volId: "sanborn08580_003", archive: "LOC" },
      "1907|Volume 1": { volId: "sanborn08580_004", archive: "LOC" },
      "1907|Volume 2": { volId: "sanborn08580_005", archive: "LOC" },
      "1924-1929|Volume 2": { volId: "utpcl_houston_1924_vol2", archive: "UT PCL" },
      "1924-1929|Volume 3": { volId: "utpcl_houston_1924_vol3", archive: "UT PCL" },
      "1946-1951|Volume 6": { volId: "utpcl_houston_1948_vol6", archive: "UT PCL" },
      "1946-1951|Volume 11": { volId: "sanborn08580_027", archive: "LOC" },
      "1946-1951|Volume 12": { volId: "sanborn08580_028", archive: "LOC" },
      "1946-1951|Volume 15": { volId: "sanborn08580_016", archive: "LOC" },
    };

    const hitsByKey = new Map();
    if (Number.isFinite(lng) && Number.isFinite(lat)) {
      for (const feat of boundaries) {
        if (!feat || !feat.geometry) continue;
        if (!this._pointInPolygonRings(lng, lat, feat.geometry)) continue;
        const p = feat.properties || {};
        const era = String(p.sanborn_era || p.VolYear || "").trim();
        const title = String(p.Title || "Volume 1").trim();
        const volYear = String(p.VolYear || era).trim();
        const key = `${era}|${title}`;
        if (hitsByKey.has(key)) continue;
        const shortVol = title.replace(/^Volume\s+/i, "Vol. ");
        const mapped = catalogMap[key] || null;
        const catalogVolId = mapped ? mapped.volId : null;
        const archive = mapped ? mapped.archive : null;
        hitsByKey.set(key, {
          era,
          volYear,
          volTitle: title,
          shortVol,
          label:
            era === "1885" || era === "1890" || era === "1896"
              ? `${era} Edition`
              : `${volYear} ${shortVol}`,
          eraGroupLabel: `${era} (${shortVol})`,
          locVolId: catalogVolId,
          catalogVolId,
          archive,
          is1907: era === "1907",
          order: eraOrder[era] ?? 99,
        });
        if (key === "1885|Volume 1" && !hitsByKey.has("1877|Volume 1")) {
          hitsByKey.set("1877|Volume 1", {
            era: "1877",
            volYear: "1877",
            volTitle: "Volume 1",
            shortVol: "Vol. 1",
            label: "1877 Edition",
            eraGroupLabel: "1877 (Vol. 1)",
            locVolId: "utpcl_houston_1877",
            catalogVolId: "utpcl_houston_1877",
            archive: "UT PCL",
            is1907: false,
            order: eraOrder["1877"],
          });
        }
      }
    }

    const hits = Array.from(hitsByKey.values()).sort(
      (a, b) => a.order - b.order || a.volTitle.localeCompare(b.volTitle)
    );
    const has1907 = hits.some((h) => h.is1907);
    const digitizedHits = hits.filter((h) => Boolean(h.catalogVolId));
    const utPclHits = hits.filter((h) => h.archive === "UT PCL");
    const printOnlyHits = hits.filter((h) => !h.catalogVolId);

    const yr = Number(yearBuilt) || 0;
    let recommendedOverlay = {
      id: has1907 ? "sanborn_1907" : "hpl_1930",
      label: has1907 ? "1907 Sanborn Fire Insurance Mosaic" : "1930 Houston Street Map",
    };
    if (has1907 && (yr === 0 || yr <= 1914)) {
      recommendedOverlay = {
        id: "sanborn_1907",
        label: "1907 Sanborn Fire Insurance Mosaic",
      };
    } else if (yr > 0 && yr <= 1878) {
      recommendedOverlay = {
        id: "wood_1869",
        label: "1869 W. E. Wood Map of Houston",
      };
    } else if (yr > 0 && yr <= 1915) {
      recommendedOverlay = has1907
        ? { id: "sanborn_1907", label: "1907 Sanborn Fire Insurance Mosaic" }
        : { id: "whitty_1913", label: "1913 Whitty Streetcar & Ward Map" };
    } else if (yr > 1915 && yr <= 1924) {
      recommendedOverlay = {
        id: "hpl_1921",
        label: "1921 Houston Street & Subdivision Map",
      };
    } else if (yr > 1924 && yr <= 1935) {
      recommendedOverlay = {
        id: "hpl_1930",
        label: "1930 Houston Archival Street Map",
      };
    } else if (yr > 1935 && yr <= 1945) {
      recommendedOverlay = {
        id: "hpl_1941",
        label: "1941 Pre-WWII Houston Street Map",
      };
    } else if (yr > 1945 && yr <= 1956) {
      recommendedOverlay = {
        id: "hpl_1950",
        label: "1950 Post-War Houston Expansion Map",
      };
    } else if (yr > 1956 && yr <= 1972) {
      recommendedOverlay = {
        id: "hpl_1963",
        label: "1963 Space City Street & Loop Map",
      };
    } else if (yr > 1972) {
      recommendedOverlay = has1907
        ? { id: "sanborn_1907", label: "1907 Sanborn Mosaic (Earlier Site History)" }
        : { id: "usgs_topo", label: "1915-1955 USGS Topographic Quad Mosaic" };
    } else if (!has1907 && hits.length === 0) {
      recommendedOverlay = {
        id: "usgs_topo",
        label: "1915-1955 USGS Topographic Quad Mosaic",
      };
    }

    return {
      hits,
      has1907,
      locHits: digitizedHits,
      digitizedHits,
      utPclHits,
      printOnlyHits,
      nonLoc1924To1951: printOnlyHits,
      recommendedOverlay,
      summaryText:
        hits.length > 0
          ? hits
              .map((h) => (h.archive ? `${h.label} (${h.archive})` : `${h.label} (Print Archive)`))
              .join(" · ")
          : "Outside 1877-1951 Sanborn Limits (Use HPL Street Maps & USGS Quads)",
    };
  }

  _parseAddressForArchivalSearch(rawAddress, neighborhood = "", historicDistrict = "", subdivision = "") {
    const raw = String(rawAddress || "").trim();
    if (!raw) {
      return {
        hasStreetAddress: false,
        houseNum: "",
        streetStem: "",
        cleanQuery: "",
        exactCityDirUrl: "https://cdm17006.contentdm.oclc.org/digital/collection/citydir",
        hplAllArchivesUrl: "https://cdm17006.contentdm.oclc.org/digital",
        historicAliases: [],
        numberingTip: "",
      };
    }

    const upper = raw
      .toUpperCase()
      .replace(/[,#].*$/, "")
      .replace(/\b(?:STE|SUITE|APT|UNIT|BLDG|FL|FLOOR)\b.*$/, "")
      .replace(/[^A-Z0-9\s\-]/g, " ")
      .trim();
    const tokens = upper.split(/\s+/).filter(Boolean);
    if (!tokens.length || !/^\d+[A-Z]?$/.test(tokens[0])) {
      const q = raw.replace(/\s+/g, " ").trim();
      return {
        hasStreetAddress: false,
        houseNum: "",
        streetStem: q,
        cleanQuery: q,
        exactCityDirUrl: `https://cdm17006.contentdm.oclc.org/digital/collection/citydir/search/searchterm/${encodeURIComponent(
          q
        )}/field/all/mode/exact/conn/and/order/date`,
        hplAllArchivesUrl: `https://cdm17006.contentdm.oclc.org/digital/search/searchterm/${encodeURIComponent(
          q
        )}`,
        historicAliases: [],
        numberingTip: "",
      };
    }

    const houseNum = tokens[0].replace(/[A-Z]+$/, "");
    let rest = tokens.slice(1);
    const cityTail = new Set(["HOUSTON", "TX", "TEXAS"]);
    while (rest.length > 1 && (cityTail.has(rest[rest.length - 1]) || /^\d{5}$/.test(rest[rest.length - 1]))) {
      rest.pop();
    }
    const suffixes = new Set([
      "ST",
      "STREET",
      "AVE",
      "AVENUE",
      "BLVD",
      "BOULEVARD",
      "DR",
      "DRIVE",
      "RD",
      "ROAD",
      "LN",
      "LANE",
      "CT",
      "COURT",
      "PL",
      "PLACE",
      "PKWY",
      "PARKWAY",
      "WAY",
      "CIR",
      "CIRCLE",
      "TRL",
      "TRAIL",
      "FWY",
      "FREEWAY",
    ]);
    if (rest.length > 1 && suffixes.has(rest[rest.length - 1])) {
      rest.pop();
    }

    const dirTokens = new Set(["N", "S", "E", "W", "NORTH", "SOUTH", "EAST", "WEST"]);
    const fullWithDir = rest.join(" ");
    let stemTokens = [...rest];
    if (stemTokens.length > 1 && dirTokens.has(stemTokens[0])) {
      stemTokens = stemTokens.slice(1);
    }
    if (stemTokens.length > 1 && dirTokens.has(stemTokens[stemTokens.length - 1])) {
      stemTokens = stemTokens.slice(0, -1);
    }

    const toTitleCase = (s) =>
      String(s || "")
        .toLowerCase()
        .replace(/\b([a-z])/g, (_, ch) => ch.toUpperCase())
        .replace(/\b(\d+)(St|Nd|Rd|Th)\b/g, (_, d, ord) => `${d}${ord.toLowerCase()}`);

    const streetStemUpper = stemTokens.join(" ");
    const streetStem = toTitleCase(streetStemUpper);
    const cleanQuery = `${houseNum} ${streetStem}`.trim();

    const aliasMap = this.citydirIndex?.historic_streets || {};
    const rawAliases =
      aliasMap[fullWithDir] ||
      aliasMap[streetStemUpper] ||
      [];
    const seenFor = new Set();
    const historicAliases = [];
    for (const item of rawAliases) {
      if (!item || !item.former || seenFor.has(item.former)) continue;
      if (item.former.toUpperCase() === streetStemUpper) continue;
      seenFor.add(item.former);
      const aliasQuery = `${houseNum} ${item.former}`.trim();
      historicAliases.push({
        former: item.former,
        era: item.era || "Historic Name",
        note: item.note || "",
        aliasQuery,
        cityDirUrl: `https://cdm17006.contentdm.oclc.org/digital/collection/citydir/search/searchterm/${encodeURIComponent(
          aliasQuery
        )}/field/all/mode/exact/conn/and/order/date`,
      });
    }

    const areaCtx = `${neighborhood} ${historicDistrict} ${subdivision}`.toUpperCase();
    const isHoustonHeightsProper =
      /\bHOUSTON\s+HEIGHTS\b/.test(String(historicDistrict || "").toUpperCase()) ||
      /\bHOUSTON\s+HEIGHTS\b/.test(String(subdivision || "").toUpperCase());
    let numberingTip = "";
    if (isHoustonHeightsProper) {
      if (/^\d+(?:ST|ND|RD|TH)$/.test(streetStemUpper)) {
        numberingTip = `Houston Heights tip: Before Houston annexed the Heights in 1918, numbered E/W streets (${streetStem} St) were platted as "${streetStem} Ave" and early homes carried 3-digit Heights house numbers.`;
      } else {
        numberingTip =
          "Houston Heights tip: Pre-1918 City Directories list the Municipality of Houston Heights in a separate section at the back of the volume (often with pre-annexation 3-digit house numbers).";
      }
    } else if (
      areaCtx.includes("SIXTH WARD") ||
      areaCtx.includes("FREEDMEN") ||
      areaCtx.includes("FOURTH WARD") ||
      areaCtx.includes("FIRST WARD") ||
      areaCtx.includes("HIGH FIRST")
    ) {
      numberingTip =
        "Pre-1912 numbering tip: Houston unified street house numbers around 1911-1912; for 1866-1910 directories, also search by owner surname or corner cross-streets.";
    }

    return {
      hasStreetAddress: true,
      houseNum,
      streetStem,
      cleanQuery,
      exactCityDirUrl: `https://cdm17006.contentdm.oclc.org/digital/collection/citydir/search/searchterm/${encodeURIComponent(
        cleanQuery
      )}/field/all/mode/exact/conn/and/order/date`,
      hplAllArchivesUrl: `https://cdm17006.contentdm.oclc.org/digital/search/searchterm/${encodeURIComponent(
        cleanQuery
      )}`,
      historicAliases,
      numberingTip,
    };
  }

  _formatHistoricPlatDisplayName(platProps) {
    if (!platProps) return "";
    const baseName = String(platProps.name || "").trim();
    const rawFull = String(platProps.full_name || "").trim();
    if (
      baseName &&
      rawFull &&
      /\bADDITION\b/i.test(rawFull) &&
      !/\bADDITION\b/i.test(baseName)
    ) {
      return `${baseName} Addition`;
    }
    return baseName || rawFull;
  }

  _buildLegalCopyPillsHtml(tokens, { hcadNum = "", owner = "" } = {}) {
    const pills = [];
    const subDesc = String(tokens.subdivisionDesc || tokens.cleanAdditionSearch || "").trim();
    const histDesc = String(tokens.historicParentDesc || "").trim();

    if (tokens.hasDistinctHistoricParent && histDesc) {
      pills.push({
        k: "Original Plat (Subdivision / Desc)",
        v: histDesc,
        copy: histDesc,
        tip: `Click to copy '${histDesc}' for the 'Subdivision or Description' box on County Clerk RP (pre-replat historic deeds)`,
      });
    }
    if (subDesc) {
      pills.push({
        k:
          tokens.hasDistinctHistoricParent && histDesc
            ? "Modern Replat (Subdivision / Desc)"
            : "Subdivision or Description",
        v: subDesc,
        copy: subDesc,
        tip: `Click to copy '${subDesc}' for the 'Subdivision or Description' box on Harris County Clerk RP`,
      });
    }
    if (tokens.section) {
      pills.push({
        k: "Section",
        v: tokens.section,
        copy: tokens.section,
        tip: `Click to copy '${tokens.section}' for the 'Section' box on Harris County Clerk RP`,
      });
    }
    if (tokens.lot) {
      pills.push({
        k: "Lot",
        v: tokens.lotDisplay || tokens.lot,
        copy: tokens.lot,
        tip: `Click to copy '${tokens.lot}' for the 'Lot' box on Harris County Clerk RP`,
      });
    }
    if (tokens.block) {
      pills.push({
        k: "Block",
        v: tokens.block,
        copy: tokens.block,
        tip: `Click to copy '${tokens.block}' for the 'Block' box on Harris County Clerk RP`,
      });
    }
    if (tokens.unit) {
      pills.push({
        k: "Unit",
        v: tokens.unit,
        copy: tokens.unit,
        tip: `Click to copy '${tokens.unit}' for the 'Unit' box on Harris County Clerk RP`,
      });
    }
    if (tokens.tract) {
      pills.push({
        k: "Tract",
        v: tokens.tractDisplay || tokens.tract,
        copy: tokens.tract,
        tip: `Click to copy '${tokens.tract}' for the separate 'Tract' box on Harris County Clerk RP`,
      });
    }
    if (tokens.reserve) {
      pills.push({
        k: "Reserve",
        v: tokens.reserve,
        copy: tokens.reserve,
        tip: `Click to copy '${tokens.reserve}' for the 'Reserve' box on Harris County Clerk RP`,
      });
    }
    if (tokens.abstract) {
      const abstDisplay = tokens.abstractSurvey
        ? `${tokens.abstract} (${tokens.abstractSurvey})`
        : tokens.abstract;
      pills.push({
        k: "Abstract",
        v: abstDisplay,
        copy: tokens.abstract,
        tip: `Click to copy numeric Texas GLO Abstract '${tokens.abstract}' for the 'Abstract' box on Harris County Clerk RP`,
      });
    }
    if (tokens.outlot) {
      pills.push({
        k: "Outlot",
        v: tokens.outlot,
        copy: tokens.outlot,
        tip: `Click to copy '${tokens.outlot}' for the 'Outlot' box on Harris County Clerk RP`,
      });
    }

    const rootCite = tokens.rootPlatCitation || {};
    if (rootCite.volume && rootCite.page) {
      const seriesTag = rootCite.series ? ` (${rootCite.series.replace(/\s*\(.*\)/, "")})` : "";
      pills.push({
        k: `Plat Vol${seriesTag}`,
        v: rootCite.volume,
        copy: rootCite.volume,
        tip: `Original subdivision plat Volume ${rootCite.volume}, Page ${rootCite.page}${
          rootCite.series ? ` in Harris County ${rootCite.series}` : ""
        }`,
      });
      pills.push({
        k: "Plat Page",
        v: rootCite.page,
        copy: rootCite.page,
        tip: `Original subdivision plat Page ${rootCite.page} (Volume ${rootCite.volume})`,
      });
    } else if (rootCite.instrument) {
      pills.push({
        k: "Root Plat File #",
        v: rootCite.instrument,
        copy: rootCite.instrument,
        tip: `Original subdivision dedication Clerk File / Instrument #${rootCite.instrument}`,
      });
    }

    const partyTokens = formatClerkPartyTokens(owner);
    for (const pt of partyTokens) {
      pills.push({
        k: pt.label,
        v: pt.display,
        copy: pt.copy,
        tip: `Pre-formatted 'Last Name First - No Punctuation' ('${pt.copy}') for Harris County Clerk Grantee / Grantor search`,
      });
    }

    if (hcadNum) {
      pills.push({
        k: "HCAD # (Tax/GIS)",
        v: hcadNum,
        copy: hcadNum,
        cls: "hcad-only",
        tip: `13-digit HCAD Account # (${hcadNum}) for HCAD Search & GIS (note: County Clerk RP does not index by HCAD #)`,
      });
    }
    if (pills.length === 0) {
      return `<span class="rk-token-empty">Click parcel in HCAD GIS or County Clerk below to look up Lot &amp; Block legal description.</span>`;
    }
    return pills
      .map((p) => {
        const escCopy = this._escapeResearchHtml(p.copy);
        const escK = this._escapeResearchHtml(p.k);
        const escV = this._escapeResearchHtml(p.v);
        const escTip = this._escapeResearchHtml(
          p.tip || `Click to copy '${p.copy}' for pasting into Harris County Clerk search`
        );
        const extraCls = p.cls ? ` ${p.cls}` : "";
        return `<button
          type="button"
          class="rk-copy-pill${extraCls}"
          data-rk-copy="${escCopy}"
          title="${escTip}"
        >
          <span class="rk-copy-k">${escK}:</span>
          <span class="rk-copy-v mono">${escV}</span>
          <span class="rk-copy-icon" aria-hidden="true">&#10697;</span>
        </button>`;
      })
      .join("");
  }

  _populateResearchKitDeedChain(hcadNum, permitShardRec) {
    const box = document.getElementById("research-kit-deed-chain");
    if (!box) return;
    const view = buildResearchKitDeedChainView(
      hcadNum,
      box.getAttribute("data-hcad-num"),
      permitShardRec
    );
    if (view.ignore) return;
    if (view.hidden) {
      box.classList.add("hidden");
      box.classList.remove("is-empty-cama");
      box.innerHTML = "";
      return;
    }

    box.innerHTML = view.html;
    if (view.isEmptyCama) {
      box.classList.add("is-empty-cama");
      box.classList.remove("hidden");
      return;
    }

    box.classList.remove("is-empty-cama", "hidden");
    this._bindResearchKitCopyPills(box);
  }

  _buildCityDirectoryResearchData(props, hcadNum, yr, addrInfo) {
    const cleanHcad = String(hcadNum || "").trim();
    const auditHit =
      cleanHcad && this.citydirIndex?.by_hcad ? this.citydirIndex.by_hcad[cleanHcad] || null : null;
    const isAuditedAbsent =
      !auditHit && cleanHcad && this.citydirAbsentSet ? this.citydirAbsentSet.has(cleanHcad) : false;

    const allVolumes = Array.isArray(this.citydirIndex?.volumes)
      ? this.citydirIndex.volumes.filter((v) => v.primary !== false)
      : [];

    const milestoneVolumes = [];
    if (allVolumes.length > 0) {
      if (yr >= 1866 && yr <= 1928) {
        let beforeVol = null;
        let atOrAfterVol = null;
        for (const v of allVolumes) {
          if (v.year < yr) beforeVol = v;
          if (v.year >= yr && !atOrAfterVol) atOrAfterVol = v;
        }
        if (beforeVol) {
          milestoneVolumes.push({
            ...beforeVol,
            role: `Pre-Build (${beforeVol.label})`,
          });
        }
        if (atOrAfterVol && (!beforeVol || atOrAfterVol.pointer !== beforeVol.pointer)) {
          milestoneVolumes.push({
            ...atOrAfterVol,
            role: `First Listing Era (${atOrAfterVol.label})`,
          });
        }
        const latest1926 = allVolumes[allVolumes.length - 1];
        if (
          latest1926 &&
          !milestoneVolumes.some((m) => m.pointer === latest1926.pointer)
        ) {
          milestoneVolumes.push({
            ...latest1926,
            role: `1926 Criss-Cross (${latest1926.label})`,
          });
        }
      } else if (yr === 0) {
        for (const targetYr of [1907, 1915, 1926]) {
          const found = allVolumes.find((v) => v.year === targetYr);
          if (found) {
            milestoneVolumes.push({
              ...found,
              role: `${found.label} Directory`,
            });
          }
        }
      } else if (yr > 1928) {
        const v1926 = allVolumes[allVolumes.length - 1];
        if (v1926) {
          milestoneVolumes.push({
            ...v1926,
            role: `1926 Pre-Build Check`,
          });
        }
      }
    }

    return {
      auditHit,
      isAuditedAbsent,
      milestoneVolumes,
      isPost1926Build: yr > 1926,
      addrInfo,
    };
  }

  _buildHouseHistoryResearchKitHtml(ctx) {
    const {
      title,
      yr,
      hcadNum,
      sanbornInfo,
      cityDirInfo,
      legalTokens,
      owner,
      protectionEval,
      catalogEntryForDeed,
      historicParentPlat,
      isReplattedFromHistoricParent,
      subVal,
      archChips,
      builderChips,
      haifSearchTerm,
      haifGoogleSearchUrl,
      streetViewUrl,
    } = ctx;

    const curState = this.filterStore.getState();
    const activeMapId = curState.layers?.historicMap ? curState.historicMapId : "";
    const recOv = sanbornInfo.recommendedOverlay;
    const isRecActive = activeMapId === recOv.id;
    const is1907Active = activeMapId === "sanborn_1907";

    // Step 1: Sanborn & Historical Maps
    const sanbornBadgesHtml =
      sanbornInfo.hits.length > 0
        ? `<div class="rk-pill-strip">
            ${sanbornInfo.hits
              .map(
                (h) =>
                  `<span class="rk-coverage-badge ${h.catalogVolId ? "digitized" : "ut-pcl"}" title="${
                    h.catalogVolId
                      ? `Digitized online via ${
                          h.archive === "UT PCL"
                            ? "UT Austin Perry-Castaneda Library"
                            : "Library of Congress"
                        } (${this._escapeResearchHtml(h.catalogVolId)}) - click Browse Sheets below`
                      : `In-archive 1924-1951 Sanborn volume (consult print or microfilm edition at HPL Julia Ideson Building or UT Austin Dolph Briscoe Center)`
                  }">${this._escapeResearchHtml(h.label)}${
                    h.archive ? ` · ${this._escapeResearchHtml(h.archive)}` : " · Print Archive"
                  }</span>`
              )
              .join("")}
          </div>`
        : `<div class="rk-subtle-note">Parcel lies outside the 1877-1951 Sanborn Fire Insurance survey perimeter; use georeferenced 1921-1963 HPL street maps and USGS topographic quadrangles below to trace subdivision growth.</div>`;

    const digitizedList = sanbornInfo.digitizedHits || sanbornInfo.locHits || [];
    const sanbornLocBtnsHtml = digitizedList
      .map(
        (h) =>
          `<button
            type="button"
            class="rk-action-btn secondary"
            data-rk-open-sanborn-vol="${this._escapeResearchHtml(h.catalogVolId || h.locVolId)}"
            title="Open digitized ${this._escapeResearchHtml(
              h.archive || "LOC"
            )} sheets (including Street Index &amp; Graphic Key Map) for ${this._escapeResearchHtml(
              h.label
            )}"
          >
            &#128214; Browse ${this._escapeResearchHtml(h.label)} Sheets (${this._escapeResearchHtml(
              h.archive || "LOC"
            )})
          </button>`
      )
      .join("");

    const utHitLabels = (sanbornInfo.utPclHits || []).map((h) => h.label);
    const hasUtOrPrint1924 =
      utHitLabels.length > 0 || (sanbornInfo.printOnlyHits || []).length > 0;
    const utPclLinkHtml = hasUtOrPrint1924
      ? `<a
          href="https://maps.lib.utexas.edu/maps/sanborn/h.html"
          target="_blank"
          rel="noopener noreferrer"
          class="rk-action-btn secondary"
          title="Open UT Austin Perry-Castaneda Library Houston Sanborn Map Index (719 digitized JPEG sheets from 1877 to 1950)"
        >
          ${
            utHitLabels.length > 0
              ? `UT PCL Source Scans (${this._escapeResearchHtml(utHitLabels.slice(0, 2).join(", "))}) &#8599;`
              : `UT PCL Houston Sanborn Index &#8599;`
          }
        </a>`
      : "";

    // Step 2: City Directories (1866-1926)
    const hit = cityDirInfo.auditHit;
    const addrInfo = cityDirInfo.addrInfo;
    let cityDirAuditBannerHtml = "";
    if (hit) {
      const yrsList = Array.isArray(hit.y) ? hit.y : [];
      const spanLabel =
        yrsList.length > 1
          ? `${yrsList[0]}-${yrsList[yrsList.length - 1]} (${yrsList.length} volumes)`
          : `${hit.e}`;
      const pageUrl = hit.p
        ? `https://cdm17006.contentdm.oclc.org/digital/collection/citydir/id/${hit.p}/rec/1`
        : addrInfo.exactCityDirUrl;
      const flagNote =
        hit.f === "earlier" && yr > hit.e
          ? `Appears in directory earlier than HCAD roll`
          : hit.f === "undated_hcad"
          ? `Dates structure unrecorded on HCAD roll`
          : `Confirmed in digitized HPL directories`;
      cityDirAuditBannerHtml = `
        <div class="rk-verified-hit-box">
          <div class="rk-verified-hit-top">
            <span class="rk-verified-hit-badge">&#10003; Verified 1866-1926 Directory Sighting</span>
            <span class="rk-verified-hit-span mono">${this._escapeResearchHtml(spanLabel)}</span>
          </div>
          ${
            hit.c
              ? `<div class="rk-verified-hit-cite">${this._escapeResearchHtml(hit.c)}</div>`
              : `<div class="rk-verified-hit-cite">${this._escapeResearchHtml(flagNote)} (Earliest volume: ${hit.e}).</div>`
          }
          <div class="rk-verified-hit-actions">
            <a
              href="${pageUrl}"
              target="_blank"
              rel="noopener noreferrer"
              class="rk-action-btn primary small"
            >
              Open Earliest Scanned Page (${hit.e}) &#8599;
            </a>
          </div>
        </div>
      `;
    } else if (cityDirInfo.isAuditedAbsent) {
      cityDirAuditBannerHtml = `
        <div class="rk-subtle-note">
          <strong>Pre-Screened (1866-1926):</strong> No exact match under <code>${this._escapeResearchHtml(
            addrInfo.cleanQuery || title
          )}</code> in pre-1927 digitized directories${
        yr > 1926
          ? ` (consistent with ${yr} construction).`
          : ` (check pre-1912 house numbering or historic street aliases below).`
      }
        </div>
      `;
    }

    const aliasBtnsHtml = (addrInfo.historicAliases || [])
      .map(
        (al) => `
        <div class="rk-alias-alert">
          <div>
            <strong>Historic Street Name (${this._escapeResearchHtml(al.era)}):</strong>
            ${this._escapeResearchHtml(al.note)}
          </div>
          <a
            href="${al.cityDirUrl}"
            target="_blank"
            rel="noopener noreferrer"
            class="rk-action-btn secondary small"
          >
            Search &ldquo;${this._escapeResearchHtml(al.aliasQuery)}&rdquo; in 1866-1926 Directories &#8599;
          </a>
        </div>`
      )
      .join("");

    const milestoneVolBtnsHtml = (cityDirInfo.milestoneVolumes || [])
      .map(
        (v) =>
          `<a
            href="https://cdm17006.contentdm.oclc.org/digital/collection/citydir/id/${v.pointer}"
            target="_blank"
            rel="noopener noreferrer"
            class="rk-vol-chip"
            title="Open ${this._escapeResearchHtml(v.title)} in HPL ContentDM"
          >${this._escapeResearchHtml(v.role)} &#8599;</a>`
      )
      .join("");

    const post1926NoteHtml = cityDirInfo.isPost1926Build
      ? `<div class="rk-subtle-note">
          <strong>Post-1926 Occupant Lookup:</strong> Copyright restricts online HPL directories after 1926. To trace ${yr}-present residents by street address, consult the print <em>Morrison &amp; Fourmy</em> and <em>Polk Criss-Cross</em> directories at HPL's <strong>Houston History Research Center (Julia Ideson Building, 550 McKinney St)</strong>.
        </div>`
      : "";

    // Step 3: Chain of Title, Plat & Deed Lookup
    const legalPillsHtml = this._buildLegalCopyPillsHtml(legalTokens, {
      hcadNum,
      owner,
    });
    const hasMirroredDeeds =
      catalogEntryForDeed &&
      Array.isArray(catalogEntryForDeed.documents) &&
      catalogEntryForDeed.documents.length > 0;

    const parentPlatDisplayName = this._formatHistoricPlatDisplayName(historicParentPlat);
    const parentPlatYearLabel = String(
      historicParentPlat?.plat_year_label ||
        (Number(historicParentPlat?.estimated_plat_year) >= 1836
          ? `c. ${historicParentPlat.estimated_plat_year}`
          : "")
    ).trim();
    const parentPlatRootCite = String(
      historicParentPlat?.deed_citation || historicParentPlat?.plat_citation || ""
    ).trim();
    const parentPlatEstYear = Number(historicParentPlat?.estimated_plat_year) || 0;
    const parentBraceyUrl = String(historicParentPlat?.bracey_1922_url || "").trim();
    const historicParentStep3NoteHtml =
      historicParentPlat && parentPlatDisplayName
        ? `<div class="rk-subtle-note" style="margin-bottom:8px;">
            <strong>${
              isReplattedFromHistoricParent
                ? "Underlying Pre-Replat Addition:"
                : "Original Historic Addition:"
            }</strong>
            <strong>${this._escapeResearchHtml(parentPlatDisplayName)}</strong>${
            parentPlatYearLabel ? ` (Platted ${this._escapeResearchHtml(parentPlatYearLabel)})` : ""
          }${
            parentPlatRootCite ? ` · ${this._escapeResearchHtml(parentPlatRootCite)}` : ""
          }.${
            isReplattedFromHistoricParent && subVal
              ? ` Modern HCAD lists this parcel under the later replat <code>${this._escapeResearchHtml(
                  subVal
                )}</code>; check the modern replat sheet's subtitle recital ("being a replat of Lots X-Y, Block Z of ${this._escapeResearchHtml(
                  parentPlatDisplayName
                )}") to bridge back to the original block and lot numbers.`
              : ""
          }${
            parentPlatEstYear > 0 && parentPlatEstYear <= 1904
              ? ` Because Harris County did not open dedicated Map Record (MR) books until 1905, look up the original plat in pre-1905 <strong>Harris County Deed Records (DR)</strong> rather than the Clerk's modern Map index.`
              : ""
          }
          </div>`
        : "";

    // Step 4: Photos, Newspapers & Architectural Archives
    const primaryArch =
      Array.isArray(archChips) && archChips.length > 0
        ? archChips[0].label
        : Array.isArray(builderChips) && builderChips.length > 0
        ? builderChips[0].label
        : "";
    const archArchiveBtnHtml = primaryArch
      ? `<a
          href="https://cdm17006.contentdm.oclc.org/digital/search/searchterm/${encodeURIComponent(
            primaryArch
          )}"
          target="_blank"
          rel="noopener noreferrer"
          class="rk-action-btn secondary"
          title="Search HPL Digital Archives & Architectural Drawings for ${this._escapeResearchHtml(
            primaryArch
          )}"
        >
          HPL Drawings: ${this._escapeResearchHtml(primaryArch)} &#8599;
        </a>`
      : "";

    // Step 5: Tailored Preservation & Block Armor Playbook
    const pEval = protectionEval || {};
    const needsBlockArmor = !pEval.hasSmls || !pEval.hasSmbl;
    const missingArmorLabel =
      !pEval.hasSmls && !pEval.hasSmbl
        ? "Special Minimum Lot Size (SMLS) or Building Line (SMBL)"
        : !pEval.hasSmls
        ? "Special Minimum Lot Size (SMLS)"
        : "Special Minimum Building Line (SMBL)";

    let step5Summary = "";
    let step5BtnsHtml = "";
    if (pEval.demoTier === "waiver_lm") {
      step5Summary = `This property is a City of Houston Landmark subject to the 90-day demolition waiver rule. Upgrading to a <strong>Protected Landmark (PLM)</strong> permanently removes the 90-day demolition loophole and unlocks City historic tax-exemption eligibility.`;
      step5BtnsHtml += `
        <a
          href="https://www.houstontx.gov/planning/HistoricPres/"
          target="_blank"
          rel="noopener noreferrer"
          class="rk-action-btn primary"
        >
          Upgrade to Protected Landmark (COH) &#8599;
        </a>`;
    } else if (pEval.demoTier === "protected_plm" || pEval.demoTier === "protected_hd") {
      step5Summary = `Protected under City of Houston Chapter 33 (${this._escapeResearchHtml(
        pEval.demoStatus || "HAHC Review"
      )}). Exterior alterations visible from the street require an HAHC Certificate of Appropriateness (COA); substantial rehabilitations may qualify for City tax exemptions or 25% Texas historic tax credits.`;
      step5BtnsHtml += `
        <a
          href="https://www.houstontx.gov/planning/Commissions/HAHC.html"
          target="_blank"
          rel="noopener noreferrer"
          class="rk-action-btn secondary"
        >
          HAHC &amp; COA Guidelines &#8599;
        </a>
        <a
          href="https://www.houstontx.gov/planning/HistoricPres/docs_pdfs/hist-incentives-broch.pdf"
          target="_blank"
          rel="noopener noreferrer"
          class="rk-action-btn secondary"
        >
          COH Historic Tax Exemption Guide (PDF) &#8599;
        </a>`;
    } else {
      step5Summary =
        yr > 0 && yr <= 1976
          ? `Built in ${yr} without binding City demolition protection. Owners can apply for permanent <strong>Protected Landmark</strong> status, and neighbors can petition for Chapter 42 block-level lot-size and setback protections.`
          : `No binding City historic landmark ordinance covers this parcel. Neighbors can protect block character against townhome lot-splits and front-yard encroachments using Chapter 42 petitions.`;
      step5BtnsHtml += `
        <a
          href="https://www.houstontx.gov/planning/HistoricPres/"
          target="_blank"
          rel="noopener noreferrer"
          class="rk-action-btn secondary"
        >
          COH Landmark &amp; District Guide &#8599;
        </a>`;
    }

    if (needsBlockArmor) {
      step5BtnsHtml += `
        <a
          href="https://www.houstontx.gov/planning/Min-Lot_Size-Min_Bldg_Line.html"
          target="_blank"
          rel="noopener noreferrer"
          class="rk-action-btn primary"
          title="No active ${missingArmorLabel} detected on this block; learn how 51% of blockface owners can petition under Chapter 42"
        >
          Apply for Block SMLS / SMBL Armor (Ch. 42) &#8599;
        </a>`;
    } else {
      step5BtnsHtml += `
        <a
          href="https://www.houstontx.gov/planning/Neighborhood/docs_pdfs/MLS_Brochure.pdf"
          target="_blank"
          rel="noopener noreferrer"
          class="rk-action-btn secondary"
        >
          Ch. 42 SMLS / SMBL Rules (PDF) &#8599;
        </a>`;
    }
    step5BtnsHtml += `
      <a
        href="https://www.preservationhouston.org/services/research"
        target="_blank"
        rel="noopener noreferrer"
        class="rk-action-btn secondary"
      >
        Preservation Houston Research Guide &#8599;
      </a>`;

    return `
      <section class="research-kit-card" id="house-history-research-kit">
        <div class="research-kit-header">
          <div>
            <div class="research-kit-kicker">&#128218; One-Click Archival Workflow</div>
            <h3 class="research-kit-title">5-Step House History Research Kit</h3>
          </div>
          <span class="research-kit-badge">${
            sanbornInfo.hits.length > 0
              ? `${sanbornInfo.hits.length} Sanborn Vol${sanbornInfo.hits.length === 1 ? "" : "s"}`
              : "Parcel Tailored"
          }</span>
        </div>
        <p class="research-kit-intro">
          Primary-source links, spatial map coverage, and copy-paste search tokens tailored to <strong>${this._escapeResearchHtml(
            addrInfo.cleanQuery || title
          )}</strong>${yr >= 1836 ? ` (recorded ${yr})` : ""}.
        </p>

        <!-- STEP 1: SANBORN & HISTORICAL MAPS -->
        <div class="rk-step">
          <div class="rk-step-head">
            <span class="rk-step-num">1</span>
            <span class="rk-step-title">Sanborn Fire Insurance &amp; Historical Maps</span>
          </div>
          ${sanbornBadgesHtml}
          <div class="rk-btn-row">
            <button
              type="button"
              class="rk-action-btn primary ${isRecActive ? "is-active" : ""}"
              data-rk-drape-map="${this._escapeResearchHtml(recOv.id)}"
              data-rk-drape-label="${this._escapeResearchHtml(recOv.label)}"
              title="Toggle georeferenced ${this._escapeResearchHtml(recOv.label)} directly underneath this building on the map"
            >
              ${
                isRecActive
                  ? `&#10003; ${this._escapeResearchHtml(recOv.label)} Active (Hide)`
                  : `&#128506; Drape ${this._escapeResearchHtml(recOv.label)}`
              }
            </button>
            ${
              sanbornInfo.has1907 && recOv.id !== "sanborn_1907"
                ? `<button
                    type="button"
                    class="rk-action-btn secondary ${is1907Active ? "is-active" : ""}"
                    data-rk-drape-map="sanborn_1907"
                    data-rk-drape-label="1907 Sanborn Fire Insurance Mosaic"
                    title="Drape the 1907 Sanborn Fire Insurance Mosaic to check whether an earlier structure occupied this lot in 1907"
                  >
                    ${is1907Active ? "&#10003; 1907 Sanborn Active (Hide)" : "Drape 1907 Sanborn"}
                  </button>`
                : ""
            }
            ${sanbornLocBtnsHtml}
            ${utPclLinkHtml}
          </div>
        </div>

        <!-- STEP 2: HOUSTON CITY DIRECTORIES -->
        <div class="rk-step">
          <div class="rk-step-head">
            <span class="rk-step-num">2</span>
            <span class="rk-step-title">Houston City Directories (1866-1926 Occupants &amp; Dating)</span>
          </div>
          ${cityDirAuditBannerHtml}
          ${aliasBtnsHtml}
          <div class="rk-btn-row">
            <a
              href="${addrInfo.exactCityDirUrl}"
              target="_blank"
              rel="noopener noreferrer"
              class="rk-action-btn primary"
              title="Run an exact-phrase search across all 39 digitized Houston City Directories (1866-1926) in HPL ContentDM"
            >
              &#128269; Search &ldquo;${this._escapeResearchHtml(
                addrInfo.cleanQuery || title
              )}&rdquo; in 39 Directories (1866-1926) &#8599;
            </a>
          </div>
          ${
            milestoneVolBtnsHtml
              ? `<div class="rk-vol-strip">
                  <span class="rk-vol-strip-label">Jump to Era Volume:</span>
                  ${milestoneVolBtnsHtml}
                </div>`
              : ""
          }
          ${
            addrInfo.numberingTip
              ? `<div class="rk-subtle-note">${this._escapeResearchHtml(addrInfo.numberingTip)}</div>`
              : ""
          }
          ${post1926NoteHtml}
        </div>

        <!-- STEP 3: CHAIN OF TITLE, PLAT & DEEDS -->
        <div class="rk-step">
          <div class="rk-step-head">
            <span class="rk-step-num">3</span>
            <span class="rk-step-title">Chain of Title, Subdivision Plat &amp; Deed Lookup</span>
          </div>
          ${historicParentStep3NoteHtml}
          <div
            class="rk-deed-chain-box hidden"
            id="research-kit-deed-chain"
            data-hcad-num="${this._escapeResearchHtml(hcadNum)}"
          ></div>
          <div class="rk-copy-bar-label">1-Click Copy Tokens (Exact County Clerk RP Box Labels):</div>
          <div class="rk-clerk-hint">
            For full Lot &amp; Block history (1961-present), paste <strong>Subdivision or Description</strong> + <strong>Lot</strong> + <strong>Block</strong> together (leave unused boxes blank). Grantee names are pre-formatted <em>Last Name First, No Punctuation</em>.
          </div>
          <div class="rk-copy-pills-wrap" id="research-kit-legal-pills">
            ${legalPillsHtml}
          </div>
          <div class="rk-clerk-codes-note">
            <strong>Clerk Type Filter Codes:</strong> <code>W/D</code> Warranty Deed (sale) &middot; <code>M/L</code> Mechanic&rsquo;s Lien (historic builder contract) &middot; <code>D/T</code> Deed of Trust (mortgage) &middot; <code>REL</code> Release &middot; <code>REST</code> Restrictions. Pre-1961 bound Vol/Page deed books are held at Harris County Archives.
          </div>
          <div class="rk-btn-row">
            <a
              href="https://www.cclerk.hctx.net/applications/websearch/RP.aspx"
              target="_blank"
              rel="noopener noreferrer"
              class="rk-action-btn primary"
              title="Search Grantor/Grantee deeds, deeds of trust, mechanic's liens, and restrictions at the Harris County Clerk"
            >
              Harris County Clerk Deeds (RP) &#8599;
            </a>
            <a
              href="https://www.cclerk.hctx.net/Applications/WebSearch/MC.aspx"
              target="_blank"
              rel="noopener noreferrer"
              class="rk-action-btn secondary"
              title="Look up original subdivision plat maps by Addition name at the Harris County Clerk Maps & Condos portal"
            >
              Subdivision Plat Maps (Clerk MC) &#8599;
            </a>
            ${
              parentBraceyUrl
                ? `<a
                    href="${this._escapeResearchHtml(parentBraceyUrl)}"
                    target="_blank"
                    rel="noopener noreferrer"
                    class="rk-action-btn secondary"
                    title="Open digitized 1922 Bracey's Block Maps of Houston in the Houston Public Library Digital Archive showing original pre-replat addition boundaries, blocks, and lot numbers"
                  >
                    1922 Bracey's Block Maps (HPL) &#8599;
                  </a>`
                : ""
            }
            <a
              href="https://www.harriscountyarchives.com/"
              target="_blank"
              rel="noopener noreferrer"
              class="rk-action-btn secondary"
              title="Harris County Archives: pre-1961 deed books, historic tax assessment rolls, and commissioners court records"
            >
              Pre-1961 Deed Books &amp; Tax Rolls &#8599;
            </a>
            <a
              href="https://www.cclerk.hctx.net/Applications/WebSearch/CourtSearch.aspx?CaseType=Probate"
              target="_blank"
              rel="noopener noreferrer"
              class="rk-action-btn secondary"
              title="Search Harris County Probate Court estate filings to trace inherited properties and family wills"
            >
              Probate &amp; Wills &#8599;
            </a>
            ${
              hasMirroredDeeds
                ? `<button
                    type="button"
                    class="rk-action-btn secondary"
                    data-switch-inspector-tab="hcad"
                    title="View mirrored subdivision deed restriction PDFs in the HCAD & Parcel tab"
                  >
                    Mirrored Subdivision Deed PDF (${catalogEntryForDeed.documents.length}) &rarr;
                  </button>`
                : ""
            }
          </div>
        </div>

        <!-- STEP 4: PHOTOS, NEWSPAPERS & ARCHITECTURAL ARCHIVES -->
        <div class="rk-step">
          <div class="rk-step-head">
            <span class="rk-step-num">4</span>
            <span class="rk-step-title">Photographs, Newspapers &amp; Architectural Archives</span>
          </div>
          <div class="rk-btn-row">
            <a
              href="${addrInfo.hplAllArchivesUrl}"
              target="_blank"
              rel="noopener noreferrer"
              class="rk-action-btn primary"
              title="Search Houston Public Library Digital Archives (HHRC historic photos, architectural drawings, oral histories & Gregory School collections)"
            >
              HPL Digital Archives (&ldquo;${this._escapeResearchHtml(
                addrInfo.cleanQuery || title
              )}&rdquo;) &#8599;
            </a>
            ${archArchiveBtnHtml}
            <a
              href="https://houstonlibrary.org/research"
              target="_blank"
              rel="noopener noreferrer"
              class="rk-action-btn secondary"
              title="Free access with an HPL library card to Houston Chronicle (1901+), Houston Post (1880-1995), and Houston Forward Times historical archives"
            >
              Chronicle &amp; Post Archives (HPL Card) &#8599;
            </a>
            <a
              href="https://atlas.thc.texas.gov/"
              target="_blank"
              rel="noopener noreferrer"
              class="rk-action-btn secondary"
              title="Texas Historical Commission Historic Sites Atlas: NRHP nominations, RTHL markers, and county architectural surveys"
            >
              THC Historic Sites Atlas &#8599;
            </a>
            ${
              haifSearchTerm && haifGoogleSearchUrl
                ? `<a
                    href="${haifGoogleSearchUrl}"
                    target="_blank"
                    rel="noopener noreferrer"
                    class="rk-action-btn secondary"
                  >
                    HAIF Forum Search &#8599;
                  </a>`
                : ""
            }
            <a
              href="${streetViewUrl}"
              target="_blank"
              rel="noopener noreferrer"
              class="rk-action-btn secondary"
              id="btn-rk-streetview"
            >
              Street View Time Machine (2007+) &#8599;
            </a>
          </div>
        </div>

        <!-- STEP 5: PROTECT THIS PROPERTY & BLOCK -->
        <div class="rk-step">
          <div class="rk-step-head">
            <span class="rk-step-num">5</span>
            <span class="rk-step-title">Protect This Property &amp; Block${
              needsBlockArmor ? " · Block Armor Available" : ""
            }</span>
          </div>
          <p class="rk-step-copy">${step5Summary}</p>
          <div class="rk-btn-row">
            ${step5BtnsHtml}
          </div>
        </div>
      </section>
    `;
  }

  _bindResearchKitCopyPills(container) {
    if (!container) return;
    container.querySelectorAll("[data-rk-copy]").forEach((pill) => {
      pill.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const val = pill.getAttribute("data-rk-copy") || "";
        if (!val) return;
        if (navigator.clipboard) {
          navigator.clipboard.writeText(val);
        }
        const origHtml = pill.innerHTML;
        pill.classList.add("copied");
        pill.innerHTML = `<span class="rk-copy-v mono">&#10003; Copied &ldquo;${this._escapeResearchHtml(
          val
        )}&rdquo;</span>`;
        setTimeout(() => {
          pill.classList.remove("copied");
          pill.innerHTML = origHtml;
        }, 1700);
      });
    });
  }

  _bindHouseHistoryResearchKitEvents(content) {
    if (!content) return;
    const kitEl = content.querySelector("#house-history-research-kit");
    if (!kitEl) return;

    this._bindResearchKitCopyPills(kitEl);

    kitEl.querySelectorAll("[data-rk-open-sanborn-vol]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const volId = btn.getAttribute("data-rk-open-sanborn-vol") || "";
        if (volId) {
          this.openSanbornBrowserModal(volId);
        }
      });
    });

    kitEl.querySelectorAll("[data-rk-drape-map]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const mapId = btn.getAttribute("data-rk-drape-map") || "sanborn_1907";
        const curState = this.filterStore.getState();
        const curLayers = curState.layers || {};
        const isAlreadyThisMap = Boolean(curLayers.historicMap) && curState.historicMapId === mapId;
        const nextVisible = !isAlreadyThisMap;

        this.filterStore.setState({
          historicMapId: mapId,
          layers: {
            ...curLayers,
            historicMap: nextVisible,
          },
        });
        const selEl = document.getElementById("select-historic-map-id");
        if (selEl && nextVisible) {
          selEl.value = mapId;
        }
        const chkEl = document.getElementById("layer-historic-map");
        if (chkEl) {
          chkEl.checked = nextVisible;
        }
        const opRow = document.getElementById("historic-map-opacity-row");
        if (opRow) {
          opRow.classList.toggle("hidden", !nextVisible);
        }

        // Refresh button states inside the Research Kit
        kitEl.querySelectorAll("[data-rk-drape-map]").forEach((otherBtn) => {
          const otherId = otherBtn.getAttribute("data-rk-drape-map") || "";
          const otherLbl = otherBtn.getAttribute("data-rk-drape-label") || "Historical Map";
          const activeNow = nextVisible && otherId === mapId;
          otherBtn.classList.toggle("is-active", activeNow);
          if (otherBtn.classList.contains("primary")) {
            otherBtn.innerHTML = activeNow
              ? `&#10003; ${this._escapeResearchHtml(otherLbl)} Active (Hide)`
              : `&#128506; Drape ${this._escapeResearchHtml(otherLbl)}`;
          } else {
            otherBtn.innerHTML = activeNow
              ? `&#10003; 1907 Sanborn Active (Hide)`
              : `Drape 1907 Sanborn`;
          }
        });
      });
    });

    const jumpBtn = content.querySelector("#btn-jump-research-kit");
    if (jumpBtn) {
      jumpBtn.addEventListener("click", (e) => {
        e.preventDefault();
        this._switchInspectorTab("history", { scrollInWide: false });
        setTimeout(() => {
          const targetKit = document.getElementById("house-history-research-kit");
          if (targetKit) {
            targetKit.scrollIntoView({ behavior: "smooth", block: "start" });
            targetKit.classList.add("wide-focus-pulse");
            setTimeout(() => targetKit.classList.remove("wide-focus-pulse"), 1200);
          }
        }, 40);
      });
    }
  }

  _refreshResearchKitLegalTokens(props, rec) {
    const pillsWrap = document.getElementById("research-kit-legal-pills");
    if (!pillsWrap || !rec) return;
    const hcadNum = String(props?.hcad_num || "").trim();
    if (
      this._activeInspectorLegalCtx &&
      this._activeInspectorLegalCtx.hcadNum === hcadNum
    ) {
      this._activeInspectorLegalCtx.liveHcadRec = rec;
    }
    const ctx =
      this._activeInspectorLegalCtx &&
      this._activeInspectorLegalCtx.hcadNum === hcadNum
        ? this._activeInspectorLegalCtx
        : {};
    const ownerVal = String(rec.owner || props?.owner || "").trim();
    const tokens = parseClerkLegalTokens({
      legalLines: rec.legalLines,
      legalDescription: rec.legalDescription || props?.legal_description || "",
      legalSubdivision: rec.legalSubdivision || "",
      subdivision: props?.subdivision || rec.legalSubdivision || rec.subdivision || "",
      marketNeighborhood: rec.marketNeighborhood || "",
      historicParentPlat: ctx.historicParentPlat || null,
      subProps: ctx.subProps || null,
    });
    pillsWrap.innerHTML = this._buildLegalCopyPillsHtml(tokens, {
      hcadNum,
      owner: ownerVal,
    });
    this._bindResearchKitCopyPills(pillsWrap);
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
        "potential_historic_districts",
        "cultural_districts",
        "nrhp_properties",
        "annexations",
        "historical_waterways",
        "historical_railroads",
        "lost_houston",
        "red_book_1915",
        "holc_1937_zones",
        "hahc_coa_filings",
      ].includes(String(rawProps.overlay_layer || ""))
    ) {
      this._currentInspectedRawProps = null;
      drawer.classList.add("boundary-mode");
      this._syncInspectorLayoutControls();
      this._renderBoundaryInspectorDrawer(rawProps, drawer, content);
      return;
    }

    this._currentInspectedRawProps = rawProps;
    drawer.classList.remove("boundary-mode");
    this._syncInspectorLayoutControls();

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
    const hcadNumPre = String(props.hcad_num || "").trim();
    const hasCityDirHitPre = Boolean(
      hcadNumPre && this.citydirIndex?.by_hcad && this.citydirIndex.by_hcad[hcadNumPre]
    );
    const isAuditedAbsent1926 = Boolean(
      !hasCityDirHitPre &&
        hcadNumPre &&
        this.citydirAbsentSet &&
        this.citydirAbsentSet.has(hcadNumPre)
    );
    const isCircaEst =
      props.year_source === "subdivision_median" || props.year_source === "blockface_median";
    const isRoundedHcadDecade = Boolean(
      !props.is_curated_override &&
        !props.year_source &&
        yr >= 1890 &&
        yr <= 1950 &&
        yr % 10 === 0
    );
    const isCityDirContradicted = Boolean(
      !props.is_curated_override && isAuditedAbsent1926 && yr >= 1836 && yr <= 1925
    );
    const isDemolishedVacantParcel = Boolean(props.is_demolished_vacant || props.suppress_only);
    const origDemolishedYr = Number(props.original_hcad_year) || 0;
    const ageText =
      yr >= 1836
        ? isRoundedHcadDecade
          ? `~${currentYear - yr} yrs (HCAD Est.)`
          : isCircaEst
          ? `~${currentYear - yr} yrs old (Est.)`
          : `${currentYear - yr} yrs old`
        : isDemolishedVacantParcel
        ? origDemolishedYr >= 1836
          ? `Building demolished · Formerly ${origDemolishedYr}`
          : "Building demolished · Lot currently vacant"
        : "Date unrecorded in HCAD";
    const yearDisplay =
      yr >= 1836
        ? isRoundedHcadDecade
          ? `c. ${yr} (HCAD Est.)`
          : isCircaEst
          ? `Circa ${yr}`
          : `Built ${yr}`
        : isDemolishedVacantParcel
        ? "Vacant Lot (Demolished)"
        : "Undated / Vacant";
    const yearColor = getYearColorHex(yr, this.filterStore.getState().paletteStyle);

    const YEAR_SOURCE_LABELS = {
      curated_landmark: "Preservation Houston Curated Landmark & Campus Record",
      hcad_extra_feature: "HCAD Extra Features / Improvement Table (act_yr / eff_yr)",
      hcad_tieback: "HCAD Multi-Parcel Tieback (parcel_tieback.txt)",
      adjacent_owner: "Spatial Same-Owner / Same-Address Contiguous Parcel",
      campus_contiguity: "Institutional Campus / THC Historical Marker Record",
      hcad_permit: "HCAD Structural Building Permit Record (permits.txt)",
      hcad_deed: "HCAD Historical Deed Record (deeds.txt)",
      subdivision_median: "Circa Estimate - HCAD Subdivision / Street Median",
      blockface_median: "Circa Estimate - Nearest Block-Face Structure Median",
    };
    const yearProvenanceLabel = props.is_curated_override
      ? `Verified by ${props.verified_by || "Preservation Houston"} (${props.source_type || "Archival Record"})`
      : isRoundedHcadDecade
      ? "HCAD Appraisal Roll - Rounded Decade Estimate (unverified exact year)"
      : YEAR_SOURCE_LABELS[props.year_source] ||
        "HCAD Real Property Building Record (date_erected / yr_impr)";
    const yearEvidentiaryTier =
      yr >= 1836
        ? classifyFieldEvidentiaryTier({
            field: "year_built",
            value: String(yr),
            sourceLabel: yearProvenanceLabel,
            isCuratedOverride: Boolean(props.is_curated_override),
            isRoundedHcadDecade,
            isCityDirContradicted,
          })
        : null;

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
          if (!props.builder && lp.builder) props.builder = lp.builder;
          if (!props.builder_source && lp.builder_source) props.builder_source = lp.builder_source;
          if (!props.original_permit && lp.original_permit) props.original_permit = lp.original_permit;
          if (!props.original_cost && lp.original_cost) props.original_cost = lp.original_cost;
          if (!props.engineers && lp.engineers) props.engineers = lp.engineers;
          break;
        }
      }
    }

    // Runtime fallback from overlaysData.relocated_structures by hcadNum
    if (hcadNum && this.mapController?.overlaysData?.relocated_structures?.features) {
      for (const rf of this.mapController.overlaysData.relocated_structures.features) {
        const rp = rf.properties || {};
        if (String(rp.hcad_num || "").trim() === hcadNum) {
          if (!props.builder && rp.builder) props.builder = rp.builder;
          if (!props.builder_source && rp.builder_source) props.builder_source = rp.builder_source;
          if (!props.was_relocated && rp.was_relocated) props.was_relocated = true;
          if (!props.relocation_note && rp.relocation_note) props.relocation_note = rp.relocation_note;
          if (!props.hist_contract && rp.hist_contract) props.hist_contract = rp.hist_contract;
          if (!props.original_cost && rp.original_cost) props.original_cost = rp.original_cost;
          if (!props.original_permit && rp.original_permit) props.original_permit = rp.original_permit;
          break;
        }
      }
    }

    let histContractObj = props.hist_contract || null;
    if (typeof histContractObj === "string" && histContractObj.trim().startsWith("{")) {
      try {
        histContractObj = JSON.parse(histContractObj);
      } catch (_e) {
        histContractObj = null;
      }
    }

    const histBuilderVal = String(props.builder || histContractObj?.builder || histContractObj?.contractor || "").trim();
    const histCostVal = String(props.original_cost || histContractObj?.cost || "").trim();
    const histPermitVal = String(props.original_permit || "").trim();
    const histSourceLabel = String(
      histContractObj?.source_title || histContractObj?.source || props.builder_source || (histPermitVal ? "COH Historic Building Permit / Landmark Dossier" : "")
    ).trim();
    const histSourceUrl = String(histContractObj?.source_url || props.source_url || "")
      .trim()
      .replace(
        /^https?:\/\/digital\.houstonlibrary\.org\/digital\//i,
        "https://cdm17006.contentdm.oclc.org/digital/"
      );
    const histImageUrl = String(histContractObj?.image_url || "").trim();
    const histSubcontractorsVal = String(histContractObj?.subcontractors || "").trim();
    const histNotesVal = String(histContractObj?.scope || histContractObj?.notes || "").trim();
    const wasRelocatedFlag = Boolean(props.was_relocated);
    const relocationNoteVal = String(props.relocation_note || "").trim();

    const histContractHtml =
      histContractObj || histBuilderVal || histCostVal || histPermitVal || wasRelocatedFlag
        ? `<div class="hist-contract-card" id="inspector-hist-contract-card">
            <div class="hist-contract-header">
              <span class="hist-contract-kicker">&#127959; Historical Construction &amp; Contract Record</span>
              ${
                wasRelocatedFlag
                  ? `<span class="hist-relocated-badge" title="Structure has a documented move or relocation permit">&#128666; Relocated Structure</span>`
                  : histContractObj?.date
                  ? `<span class="hist-contract-date-badge">${histContractObj.date}</span>`
                  : ""
              }
            </div>
            <div class="hist-contract-grid">
              ${
                histBuilderVal
                  ? `<div class="hist-contract-row">
                      <span class="hist-contract-label">General Contractor / Builder:</span>
                      <span class="hist-contract-val"><strong>${histBuilderVal}</strong></span>
                    </div>`
                  : ""
              }
              ${
                props.engineers
                  ? `<div class="hist-contract-row">
                      <span class="hist-contract-label">Structural / MEP Engineers:</span>
                      <span class="hist-contract-val">${props.engineers}</span>
                    </div>`
                  : ""
              }
              ${
                histCostVal
                  ? `<div class="hist-contract-row">
                      <span class="hist-contract-label">Original Contract Cost:</span>
                      <span class="hist-contract-val mono">${histCostVal}</span>
                    </div>`
                  : ""
              }
              ${
                histPermitVal
                  ? `<div class="hist-contract-row">
                      <span class="hist-contract-label">Historic Building Permit:</span>
                      <span class="hist-contract-val mono">${histPermitVal}</span>
                    </div>`
                  : ""
              }
              ${
                histSubcontractorsVal
                  ? `<div class="hist-contract-row">
                      <span class="hist-contract-label">Subcontractors / Trades:</span>
                      <span class="hist-contract-val">${histSubcontractorsVal}</span>
                    </div>`
                  : ""
              }
              ${
                histNotesVal
                  ? `<div class="hist-contract-row">
                      <span class="hist-contract-label">Contract Scope:</span>
                      <span class="hist-contract-val">${histNotesVal}</span>
                    </div>`
                  : ""
              }
              ${
                relocationNoteVal
                  ? `<div class="hist-contract-row">
                      <span class="hist-contract-label">Relocation / Move Record:</span>
                      <span class="hist-contract-val">${relocationNoteVal}</span>
                    </div>`
                  : ""
              }
              ${
                histSourceLabel
                  ? `<div class="hist-contract-source-line">
                      <span><strong>Archival Source:</strong> ${histSourceLabel}</span>
                      <div class="hist-contract-source-actions" style="display:flex;flex-wrap:wrap;gap:6px;margin-top:4px;">
                        ${
                          histSourceUrl
                            ? `<a href="${histSourceUrl}" target="_blank" rel="noopener noreferrer" class="hist-contract-source-link">View HMRC Archive Page &#8599;</a>`
                            : ""
                        }
                        ${
                          histImageUrl
                            ? `<a href="${histImageUrl}" target="_blank" rel="noopener noreferrer" class="hist-contract-source-link">View Scanned Page (PDF) &#8599;</a>`
                            : ""
                        }
                      </div>
                    </div>`
                  : ""
              }
            </div>
          </div>`
        : "";

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

    // Runtime fallback enrichment from spatial index for countywide shard buildings & preservation overlays
    let fullGeoCtx = props._geo_context || {};
    if (
      Number.isFinite(effLng) &&
      Number.isFinite(effLat) &&
      this.mapController &&
      typeof this.mapController.resolveGeographicContextAtPoint === "function"
    ) {
      const geoCtx = this.mapController.resolveGeographicContextAtPoint(effLng, effLat);
      fullGeoCtx = geoCtx || {};
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
      if (geoCtx.historic_district && (!props.historic_district || props.historic_district === "none")) {
        props.historic_district = geoCtx.historic_district;
      }
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

    const historicParentPlat = fullGeoCtx?.historic_parent_plat_props || null;
    const _normPlatToken = (s) =>
      String(s || "")
        .toUpperCase()
        .replace(/\b(?:ADDITION|SUBDIVISION|SEC|SECTION)\b\s*\d*/g, "")
        .replace(/[^A-Z0-9]+/g, "")
        .trim();
    const normSubVal = _normPlatToken(subVal);
    const normHistParentName = _normPlatToken(historicParentPlat?.name || "");
    const normHistParentFull = _normPlatToken(historicParentPlat?.full_name || "");
    const isReplattedFromHistoricParent = Boolean(
      historicParentPlat &&
        (normHistParentName || normHistParentFull) &&
        (!normSubVal ||
          (normSubVal !== normHistParentName && normSubVal !== normHistParentFull))
    );

    const subPropsForDeed =
      props._platted_subdivision_props ||
      this._resolvePlattedSubdivisionProps(subVal) ||
      historicParentPlat;
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
      rawSubdivisionName: subVal || this._formatHistoricPlatDisplayName(historicParentPlat),
      neighborhoodName: nbhdVal,
      isBoundary: false,
    });

    const historicParentPlatRowHtml = historicParentPlat
      ? (() => {
          const pName = this._formatHistoricPlatDisplayName(historicParentPlat);
          const pId = String(historicParentPlat.id || pName).replace(/"/g, "&quot;");
          const pEscName = pName.replace(/"/g, "&quot;");
          const pYearLbl = String(
            historicParentPlat.plat_year_label ||
              (Number(historicParentPlat.estimated_plat_year) >= 1836
                ? `c. ${historicParentPlat.estimated_plat_year}`
                : "")
          ).trim();
          const pCite = String(
            historicParentPlat.deed_citation || historicParentPlat.plat_citation || ""
          )
            .replace(/^Harris County\s+/i, "")
            .split("·")[0]
            .trim();
          const healedCnt = Number(historicParentPlat.healed_replat_count) || 0;
          return `<div class="inspector-cell full">
            <span class="cell-label">${
              isReplattedFromHistoricParent
                ? "Original Historic Parent Plat (Pre-Replat)"
                : "Historic Subdivision Plat"
            }</span>
            <span class="cell-value inspector-chip-group">
              <button
                type="button"
                class="inspector-filter-chip"
                data-highlight-boundary="${pId}"
                data-boundary-layer="platted_subdivisions"
                data-open-boundary-dossier="1"
                title="Open reconstructed ${pEscName} Boundary Dossier (${
                  pYearLbl ? `Platted ${this._escapeHtmlAttr(pYearLbl)}` : "Historic Plat"
                })"
              >${pName}${pYearLbl ? ` · ${pYearLbl}` : ""} &#128220;</button>
              <button
                type="button"
                class="inspector-boundary-btn"
                data-highlight-boundary="${pId}"
                data-boundary-layer="platted_subdivisions"
                title="Outline reconstructed pre-replat boundary of ${pEscName} on the map"
              >Outline Historic Plat</button>
              <button
                type="button"
                class="inline-isolate-btn"
                data-isolate-boundary="${pId}"
                data-boundary-layer="platted_subdivisions"
                title="Isolate reconstructed ${pEscName} and all structures inside its historic footprint"
              >&#127919; Isolate</button>
              ${
                pCite
                  ? `<span class="inspector-chip-note">${this._escapeHtmlAttr(pCite)}${
                      healedCnt > 0 ? ` · ${healedCnt} replats healed` : ""
                    }</span>`
                  : ""
              }
              ${
                isReplattedFromHistoricParent && subVal
                  ? `<span class="inspector-chip-note" style="width:100%;margin-top:2px;">Modern HCAD parcel replatted as &ldquo;${this._escapeHtmlAttr(
                      subVal
                    )}&rdquo;; originally platted within ${this._escapeHtmlAttr(pName)}.</span>`
                  : ""
              }
            </span>
          </div>`;
        })()
      : "";

    const distVal = String(props.historic_district || "")
      .trim()
      .replace(/^none$/i, "");
    const isRealDistrict =
      distVal &&
      distVal !== "Outside City District" &&
      distVal !== "Outside Historic District";

    const protectionEval = this._evaluatePropertyProtectionAndIncentives(props, {
      hcadNum,
      yr,
      geoCtx: fullGeoCtx,
      subPropsForDeed,
      catalogEntryForDeed,
      landmarkCode,
      reportPdfUrl,
    });
    const protectionScorecardHtml = this._buildProtectionScorecardHtml(protectionEval);
    const nrhpHistoryCardHtml = this._buildNrhpHistoryCardHtml(protectionEval);

    const extraPreservationOverlays = [];
    if (protectionEval.primaryNrhpDistName) {
      extraPreservationOverlays.push({
        badge: "NRHP Dist",
        name: protectionEval.primaryNrhpDistName,
        layer: "nrhp_districts",
      });
    }
    for (const pot of (protectionEval.potentialDistricts || []).slice(0, 1)) {
      if (pot.name && !pot.already_designated_coh_hd) {
        extraPreservationOverlays.push({
          badge: "2019 Survey",
          name: pot.name,
          layer: "potential_historic_districts",
        });
      }
    }
    if (protectionEval.heritageDistName) {
      extraPreservationOverlays.push({
        badge: "Heritage Dist",
        name: protectionEval.heritageDistName,
        layer: "heritage_districts",
      });
    }
    for (const cd of (protectionEval.culturalDistricts || []).slice(0, 1)) {
      if (cd.name) {
        extraPreservationOverlays.push({
          badge: "Cultural Dist",
          name: cd.name,
          layer: "cultural_districts",
        });
      }
    }
    const extraPreservationOverlaysRowHtml =
      extraPreservationOverlays.length > 0
        ? `<div class="inspector-cell full">
            <span class="cell-label">Federal, Surveyed &amp; Cultural Districts</span>
            <span class="cell-value inspector-chip-group">
              ${extraPreservationOverlays
                .map((ov) => {
                  const escName = String(ov.name).replace(/"/g, "&quot;");
                  return `<span style="display:inline-flex;align-items:center;gap:4px;flex-wrap:wrap;">
                    <span class="inspector-chip-note" style="font-weight:700;">${ov.badge}:</span>
                    <button type="button" class="inspector-boundary-btn" data-highlight-boundary="${escName}" data-boundary-layer="${ov.layer}" title="Outline ${escName} on the map">${ov.name}</button>
                    <button type="button" class="inline-isolate-btn" data-isolate-boundary="${escName}" data-boundary-layer="${ov.layer}" title="Isolate ${escName} and its structures on the map">&#127919; Isolate</button>
                  </span>`;
                })
                .join('<span class="inspector-chip-sep" aria-hidden="true">·</span>')}
            </span>
          </div>`
        : "";

    const holcZone = fullGeoCtx?.holc_1937_zone || null;
    const holcContextRowHtml = holcZone
      ? (() => {
          const hGrade = String(holcZone.holc_grade || "C").toUpperCase();
          const hCode = String(holcZone.zone_code || hGrade).trim();
          const hShort = String(
            holcZone.grade_short ||
              (hGrade === "A"
                ? "Best"
                : hGrade === "B"
                ? "Still Desirable"
                : hGrade === "C"
                ? "Definitely Declining"
                : "Hazardous")
          ).trim();
          const hTitle = String(holcZone.area_title || holcZone.name || "").trim();
          const hId = String(holcZone.id || hCode).replace(/"/g, "&quot;");
          const afTemp = Number(holcZone.af_temp_f) || 0;
          return `<div class="inspector-cell full">
            <span class="cell-label">1937 HOLC Security Area ("Redlining" Map)</span>
            <span class="cell-value inspector-chip-group">
              <button
                type="button"
                class="holc-grade-chip grade-${hGrade}"
                data-highlight-boundary="${hId}"
                data-boundary-layer="holc_1937_zones"
                data-open-boundary-dossier="1"
                title="Open 1937 HOLC ${hCode} (${hShort}) Security Zone Dossier & Equity Scorecard"
              >HOLC ${hCode} · Grade ${hGrade} (${hShort})</button>
              ${hTitle ? `<span class="inspector-chip-note">${hTitle}</span>` : ""}
              <button
                type="button"
                class="inspector-boundary-btn"
                data-highlight-boundary="${hId}"
                data-boundary-layer="holc_1937_zones"
                title="Outline 1937 HOLC ${hCode} zone polygon on the map"
              >Outline Zone</button>
              <button
                type="button"
                class="inline-isolate-btn"
                data-isolate-boundary="${hId}"
                data-boundary-layer="holc_1937_zones"
                title="Isolate 1937 HOLC ${hCode} and its structures on the map"
              >&#127919; Isolate</button>
              ${
                afTemp > 0
                  ? `<span class="inspector-chip-note" title="HARC / NOAA Houston Heat Watch measured afternoon air temperature">· ${afTemp.toFixed(1)}&deg;F Heat Watch</span>`
                  : ""
              }
            </span>
          </div>`;
        })()
      : "";
    const styleInfo = this._resolveStyleAndClassInfo(props);
    const styleChipsHtml = (styleInfo.chips || [])
      .map((c) => {
        const isArchStylePortfolio =
          !c.isSecondary && /^Architectural Style:/i.test(c.headerLabel || "");
        return `<button
            type="button"
            class="inspector-filter-chip ${c.isSecondary ? "category-chip" : ""}"
            data-filter-chip="${c.query.replace(/"/g, "&quot;")}"
            data-filter-label="${c.headerLabel.replace(/"/g, "&quot;")}"
            ${isArchStylePortfolio ? 'data-portfolio-kind="style"' : ""}
            title="${
              isArchStylePortfolio
                ? `Click to map all '${c.label.replace(/"/g, "&quot;")}' works (extant + Lost Houston) across Harris County`
                : `Click to find '${c.label.replace(/"/g, "&quot;")}' buildings in current view & across Houston`
            }"
          >${c.label} ${isArchStylePortfolio ? "&#127959;" : "&#128269;"}</button>${
          c.note ? `<span class="inspector-chip-note">${c.note}</span>` : ""
        }`;
      })
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
    const archSourceLabel = String(
      props.architect_source ||
        (props.is_curated_override
          ? props.source_type || "Preservation Houston Curated Archive"
          : reportPdfUrl || landmarkCode
          ? "COH Landmark Designation Report"
          : goodBrickAwards.length > 0
          ? "Preservation Houston Good Brick Award"
          : props.architect && /haif|forum/i.test(String(props.name_source || ""))
          ? "Houston Architecture Forum (HAIF)"
          : "Published Architectural Survey")
    ).trim();
    const archEvidentiaryTier =
      archChips.length > 0
        ? classifyFieldEvidentiaryTier({
            field: "architect",
            value: String(props.architect || ""),
            sourceLabel: archSourceLabel,
            isCuratedOverride: Boolean(props.is_curated_override),
          })
        : null;
    const archChipsHtml = archChips
      .map(
        (c) =>
          `<button
            type="button"
            class="inspector-filter-chip"
            data-filter-chip="${c.query.replace(/"/g, "&quot;")}"
            data-filter-label="${c.headerLabel.replace(/"/g, "&quot;")}"
            data-portfolio-kind="architect"
            title="Click to light up all extant &amp; demolished Lost Houston works by '${c.label.replace(/"/g, "&quot;")}' across Harris County"
          >&#127959; ${c.label}</button>${
            c.note ? `<span class="inspector-chip-note">${c.note}</span>` : ""
          }`
      )
      .join('<span class="inspector-chip-sep" aria-hidden="true">·</span>');

    const builderChips = this._resolveBuilderChips(props);
    const builderChipsHtml = builderChips
      .map(
        (c) =>
          `<button
            type="button"
            class="inspector-filter-chip"
            data-filter-chip="${c.query.replace(/"/g, "&quot;")}"
            data-filter-label="${c.headerLabel.replace(/"/g, "&quot;")}"
            data-portfolio-kind="architect"
            title="Click to light up all extant &amp; demolished Lost Houston works built by '${c.label.replace(/"/g, "&quot;")}' across Harris County"
          >&#127959; ${c.label}</button>${
            c.note ? `<span class="inspector-chip-note">${c.note}</span>` : ""
          }`
      )
      .join('<span class="inspector-chip-sep" aria-hidden="true">·</span>');

    const nameTier = nameInfo.nameEvidentiaryTier;
    const heroNameProvenanceHtml =
      nameTier && nameTier.requiresFlagBadge
        ? `<div class="evidentiary-tier-row">
            <span class="evidentiary-tier-badge tier-4" title="${this._escapeHtmlAttr(
              nameInfo.nameSource || nameTier.tierLabel
            )}">&#9888; ${this._escapeHtmlAttr(nameTier.shortBadge)}</span>
            <button
              type="button"
              class="quick-flag-btn"
              data-quick-flag="claim"
              data-flag-field="building_name"
              data-flag-value="${this._escapeHtmlAttr(title)}"
              data-flag-provenance="${this._escapeHtmlAttr(nameInfo.nameSource || nameTier.shortBadge)}"
              title="Flag this automated building name as incorrect; hides immediately and queues for Preservation Houston review"
            >&#9873; Flag Name</button>
          </div>`
        : "";

    const buildingNamesGridRowHtml =
      nameInfo.hasCustomBuildingName || nameInfo.altNames.length > 0
        ? `<div class="inspector-cell full">
            <span class="cell-label">Building Name &amp; Historical Aliases</span>
            <span class="cell-value">
              <div class="inspector-name-dossier">
                <div class="inspector-primary-name-line" style="display:flex;flex-wrap:wrap;align-items:center;gap:6px;">
                  <strong>${title}</strong>
                  ${
                    nameTier
                      ? `<span class="evidentiary-tier-badge tier-${nameTier.tier}">${
                          nameTier.tier === 1
                            ? "&#10003; "
                            : nameTier.tier === 4
                            ? "&#9888; "
                            : ""
                        }${this._escapeHtmlAttr(nameTier.shortBadge)}</span>`
                      : ""
                  }
                  ${
                    nameInfo.nameSource
                      ? `<span class="inspector-name-source-pill">${nameInfo.nameSource}</span>`
                      : ""
                  }
                  ${
                    nameTier && nameTier.requiresFlagBadge
                      ? `<button
                          type="button"
                          class="quick-flag-btn"
                          data-quick-flag="claim"
                          data-flag-field="building_name"
                          data-flag-value="${this._escapeHtmlAttr(title)}"
                          data-flag-provenance="${this._escapeHtmlAttr(nameInfo.nameSource || "")}"
                          title="Flag this automated building name as incorrect"
                        >&#9873; Flag</button>`
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

    const lostHoustonAtSite =
      this.mapController && typeof this.mapController.findLostHoustonAtSite === "function"
        ? this.mapController.findLostHoustonAtSite({
            hcadNum,
            lng: effLng,
            lat: effLat,
          })
        : [];

    const lostHoustonSiteCardHtml =
      lostHoustonAtSite.length > 0
        ? `<div class="lost-houston-site-card" id="lost-houston-site-card">
            <div class="lost-houston-site-header">
              <span class="lost-houston-site-kicker">&#127963; Previous Historic Structure${
                lostHoustonAtSite.length > 1 ? `s (${lostHoustonAtSite.length})` : ""
              } on This Site</span>
              <span class="lost-houston-site-badge">Lost Houston</span>
            </div>
            <div class="lost-houston-site-list">
              ${lostHoustonAtSite
                .map((lh) => {
                  const mat = String(lh.sanborn_material || "brick").toLowerCase();
                  const matColor =
                    mat === "frame"
                      ? "#facc15"
                      : mat === "stone"
                      ? "#60a5fa"
                      : mat === "iron"
                      ? "#cbd5e1"
                      : mat === "fireproof"
                      ? "#a3b18a"
                      : "#f472b6";
                  return `
                  <div class="lost-houston-site-item">
                    <div class="lost-houston-site-item-top">
                      <span class="lost-houston-site-name">${lh.name}</span>
                      <span class="lost-houston-site-lifespan">${
                        lh.lifespan_label || `${lh.year_built || "?"}-${lh.year_demolished || "?"}`
                      }</span>
                    </div>
                    <div class="lost-houston-site-meta">
                      <span style="display:inline-flex;align-items:center;gap:4px;">
                        <span style="width:8px;height:8px;border-radius:2px;background:${matColor};display:inline-block;"></span>
                        ${lh.sanborn_material_label || "Brick / Masonry"}
                      </span>
                      ${lh.architect ? `<span>· Architect: ${lh.architect}</span>` : ""}
                      ${lh.demolition_cause ? `<span>· ${lh.demolition_cause}</span>` : ""}
                    </div>
                    ${
                      lh.historic_significance
                        ? `<div class="lost-houston-site-desc">${lh.historic_significance}</div>`
                        : ""
                    }
                    ${this._buildNarrativeCitationsHtml({
                      citations: lh.citations,
                      contextId: `lost_houston_${lh.id || lh.name}`,
                      contextTitle: `Lost Houston: ${lh.name}`,
                      excerpt: lh.historic_significance || "",
                      compact: true,
                    })}
                    <div class="lost-houston-site-actions">
                      <button
                        type="button"
                        class="lost-houston-site-btn"
                        data-inspect-lost-houston="${String(lh.id || lh.name).replace(/"/g, "&quot;")}"
                      >
                        &#128506; Outline Historic Footprint &amp; Open Dossier
                      </button>
                      <button
                        type="button"
                        class="lost-houston-site-btn secondary"
                        data-overlay-lost-sanborn="sanborn_1907"
                      >
                        1907 Sanborn Underlay
                      </button>
                    </div>
                  </div>`;
                })
                .join("")}
            </div>
          </div>`
        : "";

    let redBookEntries =
      this.mapController && typeof this.mapController.getRedBookEntriesForBuilding === "function"
        ? this.mapController.getRedBookEntriesForBuilding({
            hcadNum,
            buildingId: props.building_id || props.id || "",
          })
        : [];
    if (!redBookEntries.length && props._red_book_entries) {
      if (Array.isArray(props._red_book_entries)) {
        redBookEntries = props._red_book_entries;
      } else if (
        typeof props._red_book_entries === "string" &&
        props._red_book_entries.trim().startsWith("[")
      ) {
        try {
          redBookEntries = JSON.parse(props._red_book_entries);
        } catch (_e) {
          redBookEntries = [];
        }
      }
    }

    const redBookHistoryCardHtml =
      redBookEntries.length > 0
        ? `<div class="red-book-heritage-card" id="red-book-heritage-card">
            <div class="red-book-card-header">
              <span class="red-book-card-kicker">&#128214; 1915 Red Book of Houston Heritage${
                redBookEntries.length > 1 ? ` (${redBookEntries.length} Entries)` : ""
              }</span>
              <span class="lost-houston-site-badge" style="background:rgba(245,158,11,0.22);color:#fef3c7;border-color:rgba(251,191,36,0.55);">African American Compendium</span>
            </div>
            ${redBookEntries
              .map((rb) => {
                const rbId = String(rb.id || "").replace(/"/g, "&quot;");
                const rbName = String(rb.name || "1915 Red Book Site");
                const rbCat = String(rb.category_label || "1915 Heritage Entry");
                const rbSub = String(rb.subtitle || "");
                const rbAddr1915 = String(rb.historic_address_1915 || "");
                const rbSurv = String(rb.survival_status || "");
                const isSurvivingEra = rbSurv === "surviving_era_structure";
                const survBadgeText = isSurvivingEra
                  ? "Surviving 1915-Era Structure"
                  : "Redeveloped Parcel (1915 Site)";
                const survBadgeStyle = isSurvivingEra
                  ? "background:rgba(34,197,94,0.2);color:#bbf7d0;border:1px solid rgba(74,222,128,0.5);"
                  : "background:rgba(56,189,248,0.16);color:#bae6fd;border:1px solid rgba(125,211,252,0.42);";
                const bioBits = [];
                if (rbAddr1915) bioBits.push(`1915 Address: <strong>${rbAddr1915}</strong>`);
                if (rb.pastor_name && !rbSub.includes(rb.pastor_name)) {
                  bioBits.push(`Pastor: ${rb.pastor_name}`);
                }
                if (rb.birthplace) {
                  bioBits.push(
                    `Origin: ${rb.birthplace}${rb.birth_year ? ` (b. ${rb.birth_year})` : ""}`
                  );
                }
                if (rb.church_attended) bioBits.push(`Church: ${rb.church_attended}`);
                if (rb.school_attended) bioBits.push(`School: ${rb.school_attended}`);
                if (rb.lodges_clubs) bioBits.push(`Fraternal / Civic: ${rb.lodges_clubs}`);
                if (rb.red_book_page) bioBits.push(`1915 Compendium: ${rb.red_book_page}`);
                const imgUrl = String(rb.rice_photo_img_url || "").trim();
                const detailUrl = String(rb.rice_photo_url || rb.rice_collection_url || "").trim();
                const hplUrl = String(
                  rb.hpl_book_url ||
                    "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/182"
                ).trim();
                return `<div class="red-book-entry-item">
                  <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:8px;flex-wrap:wrap;">
                    <div>
                      <div class="red-book-entry-title">${rbName}</div>
                      <div class="red-book-entry-meta" style="color:#fde68a;font-weight:600;">${rbCat}${
                        rbSub ? ` · ${rbSub}` : ""
                      }</div>
                    </div>
                    <span style="font-size:10px;font-weight:700;padding:2px 7px;border-radius:999px;${survBadgeStyle}">${survBadgeText}</span>
                  </div>
                  ${
                    imgUrl
                      ? `<div class="red-book-photo-wrap">
                          <img
                            src="${imgUrl}"
                            alt="${rbName.replace(/"/g, "&quot;")} (1915 Red Book of Houston archival plate)"
                            class="red-book-photo-thumb"
                            loading="lazy"
                          />
                        </div>`
                      : ""
                  }
                  ${
                    bioBits.length
                      ? `<div class="red-book-entry-meta">${bioBits.join(" · ")}</div>`
                      : ""
                  }
                  <div class="lost-houston-site-actions" style="margin-top:3px;">
                    <button
                      type="button"
                      class="lost-houston-site-btn"
                      data-inspect-red-book="${rbId}"
                    >
                      &#128214; Open 1915 Red Book Dossier
                    </button>
                    ${
                      detailUrl
                        ? `<a
                            href="${detailUrl}"
                            target="_blank"
                            rel="noopener noreferrer"
                            class="lost-houston-site-btn secondary"
                            style="text-decoration:none;"
                          >Rice Woodson Plate &#8599;</a>`
                        : ""
                    }
                    <a
                      href="${hplUrl}"
                      target="_blank"
                      rel="noopener noreferrer"
                      class="lost-houston-site-btn secondary"
                      style="text-decoration:none;"
                    >1915 Red Book (HPL) &#8599;</a>
                  </div>
                </div>`;
              })
              .join("")}
          </div>`
        : "";

    const initialRemodelYear = Number(props.yr_remodel || props.remodel_year) || 0;
    const initialEffYear = Number(props.eff_year_built) || 0;

    const activePermitAlert =
      this.mapController && typeof this.mapController.getPermitAlertForBuilding === "function"
        ? this.mapController.getPermitAlertForBuilding({
            hcadNum,
            buildingId: props.building_id || props.id || "",
          })
        : null;
    const activePlatItem =
      this.mapController && typeof this.mapController.getPlatWatchForBuilding === "function"
        ? this.mapController.getPlatWatchForBuilding({
            hcadNum,
            buildingId: props.building_id || props.id || "",
          })
        : null;
    const permitAlertCardHtml = activePermitAlert
      ? this._buildInspectorPermitAlertCardHtml(activePermitAlert, activePlatItem)
      : activePlatItem
      ? this._buildInspectorPlatWatchCardHtml(activePlatItem)
      : "";

    const hahcFilings =
      this.mapController && typeof this.mapController.getHahcFilingsForBuilding === "function"
        ? this.mapController.getHahcFilingsForBuilding({
            hcadNum,
            buildingId: props.building_id || props.id || "",
          })
        : [];
    const hasActiveHahcAgenda = hahcFilings.some((f) => Boolean(f.is_active_agenda));
    const hahcParcelCardHtml =
      hahcFilings.length > 0
        ? this._buildHahcParcelCardHtml(hahcFilings, isRealDistrict ? distVal : "")
        : "";
    const hahcDistrictPrecedentsCardHtml = isRealDistrict
      ? this._buildHahcDistrictPrecedentsCardHtml(distVal, hcadNum)
      : "";

    const sanbornResearchInfo = this._resolveSanbornCoverageAtPoint(effLng, effLat, yr);
    const addrResearchInfo = this._parseAddressForArchivalSearch(
      props.address || title,
      nbhdVal,
      distVal,
      subVal
    );
    const cityDirResearchInfo = this._buildCityDirectoryResearchData(
      props,
      hcadNum,
      yr,
      addrResearchInfo
    );
    this._activeInspectorLegalCtx = {
      hcadNum,
      historicParentPlat,
      subProps: subPropsForDeed,
    };
    const initialLegalTokens = parseClerkLegalTokens({
      legalDescription: props.legal_description || "",
      subdivision: subVal,
      historicParentPlat,
      subProps: subPropsForDeed,
    });
    const researchKitHtml = this._buildHouseHistoryResearchKitHtml({
      title,
      yr,
      hcadNum,
      sanbornInfo: sanbornResearchInfo,
      cityDirInfo: cityDirResearchInfo,
      legalTokens: initialLegalTokens,
      owner: props.owner || "",
      protectionEval,
      catalogEntryForDeed,
      historicParentPlat,
      isReplattedFromHistoricParent,
      subVal,
      archChips,
      builderChips,
      haifSearchTerm,
      haifGoogleSearchUrl,
      streetViewUrl,
    });

    const historyItemCount = [
      Boolean(props.is_curated_override),
      Boolean(reportPdfUrl || landmarkCode),
      Boolean(nrhpHistoryCardHtml),
      redBookEntries.length > 0,
      goodBrickAwards.length > 0,
      Boolean(histContractObj || histBuilderVal || histCostVal || histPermitVal || wasRelocatedFlag),
      lostHoustonAtSite.length > 0,
      haifThreads.length > 0,
      Boolean(cityDirResearchInfo.auditHit),
    ].filter(Boolean).length;

    const initialTab =
      this._activeInspectorTab &&
      ["overview", "history", "permits", "hcad"].includes(this._activeInspectorTab)
        ? this._activeInspectorTab
        : "overview";
    this._activeInspectorTab = initialTab;

    const compactAlertBannerHtml =
      activePermitAlert || activePlatItem
        ? `<div
            class="inspector-compact-alert-banner ${
              activePermitAlert && activePermitAlert.severity === "critical" ? "critical" : "watch"
            }"
            id="inspector-compact-alert-banner"
            data-switch-inspector-tab="permits"
            style="${initialTab === "permits" ? "display:none;" : ""}"
            role="button"
            tabindex="0"
            title="Switch to the Permits & Watch tab to inspect active filings and open the 15-Point Property Tracker"
          >
            <span>
              <strong>${
                activePermitAlert && activePermitAlert.severity === "critical"
                  ? "&#128680; Active Demolition / Abatement Alert:"
                  : "&#9888; Active Permit / Plat Watch:"
              }</strong>
              ${
                activePermitAlert
                  ? activePermitAlert.badge || "Active Filing"
                  : `Plat App #${activePlatItem.appNo || "N/A"}`
              }
            </span>
            <span class="inspector-compact-alert-cta">Permits &amp; 15-Pt Tracker &rarr;</span>
          </div>`
        : "";

    const emptyHistoryFallbackHtml =
      !verifiedBannerHtml &&
      !landmarkReportHtml &&
      !nrhpHistoryCardHtml &&
      !redBookHistoryCardHtml &&
      !goodBrickHtml &&
      !histContractHtml &&
      !lostHoustonSiteCardHtml
        ? `<div class="inspector-empty-history-card">
            <div>
              <strong style="color:var(--text-cream);">Construction Date Basis:</strong>
              ${yearProvenanceLabel}
            </div>
            <div>
              Have a Houston City Directory listing, historic building permit, deed citation, or vintage photograph for <strong>${title}</strong>?
            </div>
            <button
              type="button"
              class="inspector-btn suggest-edit"
              data-open-correction-history="1"
              style="margin-top:2px;"
            >
              &#9998; Submit Archival Evidence or Historic Photo
            </button>
          </div>`
        : "";

    const cityDirContradictionBannerHtml = isCityDirContradicted
      ? `<div class="archival-contradiction-banner" id="inspector-citydir-contradiction-banner">
          <div>
            <strong>&#9888; Archival Date Contradiction Detected:</strong>
            HCAD lists <strong>${
              isRoundedHcadDecade ? `c. ${yr} (rounded decade estimate)` : yr
            }</strong>, whereas digitized 1866-1926 Houston City Directories record this address as absent or unimproved through 1926 (likely built c. 1927-1935, or numbered under an earlier street address).
          </div>
          <div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:6px;">
            <button
              type="button"
              class="quick-flag-btn"
              data-open-correction-history="1"
              title="Open the archival correction form to submit a verified completion year"
            >&#9998; Suggest Verified Year</button>
            <button
              type="button"
              class="quick-flag-btn"
              data-quick-flag="claim"
              data-flag-field="year_built"
              data-flag-value="${yr}"
              data-flag-provenance="${this._escapeHtmlAttr(yearProvenanceLabel)}"
              data-flag-suppress="false"
              title="Queue this HCAD vs. 1926 City Directory date discrepancy for Preservation Houston archival review"
            >&#9873; Flag HCAD Year</button>
          </div>
        </div>`
      : "";

    content.innerHTML = `
      ${overlapStackHtml}
      <div class="inspector-hero">
        <div class="inspector-badges">
          <span class="inspector-year-pill" style="background:${yearColor};">${yearDisplay}${props.is_curated_override ? " &#10003;" : ""}</span>
          <span class="inspector-age-pill">${ageText}</span>
          ${
            wasRelocatedFlag
              ? `<span class="inspector-age-pill" style="background:rgba(249,115,22,0.18);color:#fdba74;border-color:rgba(249,115,22,0.4);" title="Structure relocated to or within Houston">&#128666; Relocated</span>`
              : ""
          }
          ${
            activePermitAlert
              ? `<span class="inspector-age-pill" style="background:${
                  activePermitAlert.severity === "critical"
                    ? "rgba(239,68,68,0.28)"
                    : "rgba(245,158,11,0.24)"
                };color:${
                  activePermitAlert.severity === "critical" ? "#FECACA" : "#FDE68A"
                };border-color:${
                  activePermitAlert.severity === "critical"
                    ? "rgba(248,113,113,0.65)"
                    : "rgba(251,191,36,0.55)"
                };cursor:pointer;" data-switch-inspector-tab="permits" title="Click to open Permits & Watch tab">&#9888; ${activePermitAlert.badge || "Active Permit Watch"}</span>`
              : ""
          }
        </div>
        <h2 class="inspector-title" id="inspector-property-title">${title}</h2>
        ${heroNameProvenanceHtml}
        ${akaHeroHtml}
        <p class="inspector-subtitle">${subtitle}</p>
        <div class="inspector-status-banner">
          <span class="status-dot"></span>
          <span>${statusBadge}</span>
        </div>
      </div>

      ${compactAlertBannerHtml}
      ${cityDirContradictionBannerHtml}

      <div class="inspector-tabs-bar" role="tablist" aria-label="Property Record Sections">
        <button
          type="button"
          role="tab"
          class="inspector-tab-btn ${initialTab === "overview" ? "active" : ""}"
          data-inspector-tab="overview"
          aria-selected="${initialTab === "overview" ? "true" : "false"}"
        >
          Overview
        </button>
        <button
          type="button"
          role="tab"
          class="inspector-tab-btn ${initialTab === "history" ? "active" : ""}"
          data-inspector-tab="history"
          aria-selected="${initialTab === "history" ? "true" : "false"}"
        >
          History${
            historyItemCount > 0
              ? ` <span class="inspector-tab-badge">${historyItemCount}</span>`
              : ` <span class="inspector-tab-badge kit-badge">Kit</span>`
          }
        </button>
        <button
          type="button"
          role="tab"
          class="inspector-tab-btn ${initialTab === "permits" ? "active" : ""}"
          data-inspector-tab="permits"
          aria-selected="${initialTab === "permits" ? "true" : "false"}"
        >
          Permits${
            activePermitAlert
              ? ` <span class="inspector-tab-badge ${
                  activePermitAlert.severity === "critical" ? "alert-critical" : "alert-watch"
                }">Alert</span>`
              : hasActiveHahcAgenda
              ? ` <span class="inspector-tab-badge alert-critical">Oct '26 HAHC</span>`
              : activePlatItem
              ? ` <span class="inspector-tab-badge alert-watch">Plat</span>`
              : hahcFilings.length > 0
              ? ` <span class="inspector-tab-badge alert-watch">HAHC (${hahcFilings.length})</span>`
              : ` <span class="inspector-tab-badge hidden" id="inspector-tab-permits-count"></span>`
          }
        </button>
        <button
          type="button"
          role="tab"
          class="inspector-tab-btn ${initialTab === "hcad" ? "active" : ""}"
          data-inspector-tab="hcad"
          aria-selected="${initialTab === "hcad" ? "true" : "false"}"
        >
          HCAD &amp; Parcel
        </button>
      </div>

      <div class="inspector-sections-grid">
        <div class="inspector-col inspector-col-primary">
          <!-- TAB 1: OVERVIEW & ARCHITECTURE -->
          <div
            class="inspector-tab-pane ${initialTab === "overview" ? "active" : ""}"
            data-tab-pane="overview"
          >
            <div class="inspector-wide-pane-header">
              <span>&#127968; Overview &amp; Architecture</span>
              <span>Protection, Photos &amp; Facts</span>
            </div>

            ${protectionScorecardHtml}

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

            <div class="inspector-grid">
              <div class="inspector-cell full">
                <span class="cell-label">Architectural Style / Building Class</span>
                <span class="cell-value inspector-chip-group">
                  ${styleChipsHtml}
                </span>
              </div>
              ${
                archChips.length > 0
                  ? `<div class="inspector-cell full">
                      <span class="cell-label">Architect (COH / Archival Record)</span>
                      <span class="cell-value inspector-chip-group">
                        ${archChipsHtml}
                        ${
                          archEvidentiaryTier
                            ? `<span class="evidentiary-tier-badge tier-${archEvidentiaryTier.tier}" title="${this._escapeHtmlAttr(
                                archSourceLabel
                              )}">${
                                archEvidentiaryTier.tier === 1
                                  ? "&#10003; "
                                  : archEvidentiaryTier.tier === 4
                                  ? "&#9888; "
                                  : ""
                              }${this._escapeHtmlAttr(archEvidentiaryTier.shortBadge)}</span>`
                            : ""
                        }
                        ${
                          !props.is_curated_override
                            ? `<button
                                type="button"
                                class="quick-flag-btn"
                                data-quick-flag="claim"
                                data-flag-field="architect"
                                data-flag-value="${this._escapeHtmlAttr(archChips[0].label)}"
                                data-flag-provenance="${this._escapeHtmlAttr(archSourceLabel)}"
                                data-flag-suppress="${archEvidentiaryTier?.requiresFlagBadge ? "true" : "false"}"
                                title="Report an incorrect architect attribution for Preservation Houston review"
                              >&#9873; Flag</button>`
                            : ""
                        }
                      </span>
                    </div>`
                  : ""
              }
              ${
                builderChips.length > 0
                  ? `<div class="inspector-cell full">
                      <span class="cell-label">Builder / General Contractor</span>
                      <span class="cell-value inspector-chip-group">
                        ${builderChipsHtml}
                      </span>
                    </div>`
                  : ""
              }
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
              ${extraPreservationOverlaysRowHtml}
              ${historicParentPlatRowHtml}
              ${holcContextRowHtml}
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
                <span class="cell-label">Remodel / Effective Yr</span>
                <span class="cell-value mono" id="inspector-cell-remodel-year">${
                  initialRemodelYear > 1836 && initialEffYear > 1836 && initialEffYear !== initialRemodelYear
                    ? `Remod ${initialRemodelYear} · Eff ${initialEffYear}`
                    : initialRemodelYear > 1836
                    ? `Remodeled ${initialRemodelYear}`
                    : initialEffYear > 1836 && initialEffYear !== yr
                    ? `Eff. Year ${initialEffYear}`
                    : "None recorded"
                }</span>
              </div>
              <div class="inspector-cell full">
                <span class="cell-label">Construction Date Provenance</span>
                <span class="cell-value" style="display:flex;flex-wrap:wrap;align-items:center;gap:6px;">
                  <span>${yearProvenanceLabel}</span>
                  ${
                    yearEvidentiaryTier
                      ? `<span class="evidentiary-tier-badge tier-${
                          yearEvidentiaryTier.isContradicted ? 4 : yearEvidentiaryTier.tier
                        }">${
                          yearEvidentiaryTier.tier === 1
                            ? "&#10003; "
                            : yearEvidentiaryTier.isContradicted || yearEvidentiaryTier.tier === 4
                            ? "&#9888; "
                            : ""
                        }${this._escapeHtmlAttr(yearEvidentiaryTier.shortBadge)}</span>`
                      : ""
                  }
                </span>
              </div>
              <div class="inspector-cell full">
                <span class="cell-label">Land Use Classification</span>
                <span class="cell-value inspector-chip-group">
                  ${landUseChipsHtml}
                </span>
              </div>
            </div>

            <div class="inspector-actions">
              <button
                type="button"
                class="inspector-btn research-kit-jump"
                id="btn-jump-research-kit"
                title="Open the 5-Step House History Research Kit (Sanborn coverage, 1866-1926 City Directories, Deeds & Block Armor)"
              >
                &#128218; Open 5-Step House History Research Kit &rarr;
              </button>
              <button type="button" class="inspector-btn suggest-edit" id="btn-suggest-correction">
                &#9998; Suggest a Date / Shape / Data Correction
              </button>
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
            </div>
          </div>

          <!-- TAB 2: HISTORY, DESIGNATIONS & AWARDS -->
          <div
            class="inspector-tab-pane ${initialTab === "history" ? "active" : ""}"
            data-tab-pane="history"
          >
            <div class="inspector-wide-pane-header">
              <span>&#127963; History, Landmarks &amp; Awards</span>
              <span>Archival Provenance</span>
            </div>
            ${verifiedBannerHtml}
            ${landmarkReportHtml}
            ${nrhpHistoryCardHtml}
            ${redBookHistoryCardHtml}
            ${goodBrickHtml}
            ${histContractHtml}
            ${lostHoustonSiteCardHtml}
            ${
              buildingNamesGridRowHtml
                ? `<div class="inspector-grid">${buildingNamesGridRowHtml}</div>`
                : ""
            }
            ${haifForumHtml}
            ${researchKitHtml}
            ${emptyHistoryFallbackHtml}
          </div>
        </div>

        <div class="inspector-col inspector-col-secondary">
          <!-- TAB 3: PERMITS & ACTIVE WATCH -->
          <div
            class="inspector-tab-pane ${initialTab === "permits" ? "active" : ""}"
            data-tab-pane="permits"
          >
            <div class="inspector-wide-pane-header">
              <span>&#128203; Permits, HAHC COAs &amp; Active Watch</span>
              <span>COH · HAHC · TX DSHS · HCAD</span>
            </div>
            ${permitAlertCardHtml}
            ${hahcParcelCardHtml}
            ${hahcDistrictPrecedentsCardHtml}
            ${
              hcadNum || histPermitVal || histCostVal || wasRelocatedFlag || activePermitAlert
                ? `<details class="inspector-permits-card" id="inspector-permits-card" data-hcad-num="${hcadNum}" open>
                    <summary class="inspector-permits-summary">
                      <div class="inspector-permits-title-wrap">
                        <span class="inspector-permits-kicker">&#128203; Building Permits &amp; Alteration Timeline</span>
                        <span class="inspector-permits-badge" id="inspector-permits-badge">Loading history…</span>
                      </div>
                      <span class="inspector-permits-chevron" aria-hidden="true">&#9662;</span>
                    </summary>
                    <div class="inspector-permits-body" id="inspector-permits-body">
                      <div class="inspector-permits-loading">Loading HCAD &amp; City of Houston permit history…</div>
                    </div>
                  </details>`
                : `<div class="inspector-empty-history-card">No HCAD account number or permit history linked to this structure.</div>`
            }
          </div>

          <!-- TAB 4: HCAD & PARCEL RECORDS -->
          <div
            class="inspector-tab-pane ${initialTab === "hcad" ? "active" : ""}"
            data-tab-pane="hcad"
          >
            <div class="inspector-wide-pane-header">
              <span>&#128209; Parcel, Deed &amp; HCAD Appraisal</span>
              <span>Tax Roll &amp; Boundaries</span>
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

            <div class="inspector-grid">
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
              <div class="inspector-cell full">
                <span class="cell-label">Recorded Property Owner (HCAD)</span>
                <span class="cell-value" id="inspector-cell-owner">${props.owner || "Public / Unlisted"}</span>
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
              ${isReplattedFromHistoricParent ? historicParentPlatRowHtml : ""}
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
              <div class="inspector-cell full">
                <span class="cell-label">Geometry Provenance</span>
                <span class="cell-value">${fpSourceLabel}</span>
              </div>
            </div>

            ${buildingDeedCardHtml}

            ${
              hcadNum
                ? `<div class="inspector-actions">
                    <div class="hcad-link-status-note hidden" id="hcad-link-status-note"></div>
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
                    </a>
                  </div>`
                : ""
            }
          </div>
        </div>
      </div>
    `;

    drawer.classList.remove("hidden");

    content.querySelectorAll("[data-inspector-tab]").forEach((tabBtn) => {
      tabBtn.addEventListener("click", (e) => {
        e.preventDefault();
        const tKey = tabBtn.getAttribute("data-inspector-tab") || "overview";
        this._switchInspectorTab(tKey, { scrollInWide: true });
      });
    });

    content.querySelectorAll("[data-switch-inspector-tab]").forEach((jumpEl) => {
      jumpEl.addEventListener("click", (e) => {
        e.preventDefault();
        const tKey = jumpEl.getAttribute("data-switch-inspector-tab") || "permits";
        this._switchInspectorTab(tKey, { scrollInWide: true });
      });
    });

    content.querySelectorAll("[data-open-correction-history]").forEach((histBtn) => {
      histBtn.addEventListener("click", () => {
        this.openCorrectionModal(props);
      });
    });

    this._bindInspectorOverlapStackEvents(content);
    this._bindDeedRestrictionsCardEvents(content, props);
    this._bindQuickFlagEvents(content, props);
    this._bindHouseHistoryResearchKitEvents(content);

    const btnToggleScorecard = content.querySelector("#btn-toggle-protection-scorecard");
    const scorecardEl = content.querySelector("#inspector-protection-scorecard");
    const scorecardHintEl = content.querySelector("#protection-scorecard-toggle-hint");
    if (btnToggleScorecard && scorecardEl) {
      btnToggleScorecard.addEventListener("click", (e) => {
        if (
          drawer.classList.contains("wide-mode") &&
          typeof window !== "undefined" &&
          window.innerWidth > 900
        ) {
          return;
        }
        e.preventDefault();
        const nextExpanded = !scorecardEl.classList.contains("is-expanded");
        this._protectionScorecardExpanded = nextExpanded;
        scorecardEl.classList.toggle("is-expanded", nextExpanded);
        btnToggleScorecard.setAttribute("aria-expanded", nextExpanded ? "true" : "false");
        if (scorecardHintEl) {
          scorecardHintEl.innerHTML = nextExpanded
            ? "Hide Rules &#9652;"
            : "Plain-English Rules &#9662;";
        }
      });
    }

    content.querySelectorAll("[data-open-permit-watchlist]").forEach((pwBtn) => {
      pwBtn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const sev = pwBtn.getAttribute("data-open-permit-watchlist") || "all";
        this.openPermitWatchModal(sev);
      });
    });

    content.querySelectorAll("[data-report-site-activity]").forEach((repBtn) => {
      repBtn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const repAddr = repBtn.getAttribute("data-report-address") || props.address || "";
        const repBadge = repBtn.getAttribute("data-report-badge") || "Active Permit Watch";
        this.openCorrectionModal(props);
        const citeEl = document.getElementById("corr-citation");
        const sourceTypeEl = document.getElementById("corr-source-type");
        if (sourceTypeEl) {
          sourceTypeEl.value = "Field Survey / Architectural Guide";
        }
        if (citeEl) {
          const prefix = `[SITE ACTIVITY OBSERVATION - ${repBadge}${repAddr ? ` at ${repAddr}` : ""}] `;
          if (!String(citeEl.value || "").startsWith("[SITE ACTIVITY OBSERVATION")) {
            citeEl.value = prefix + (citeEl.value || "");
          }
          setTimeout(() => {
            citeEl.focus();
            citeEl.setSelectionRange(citeEl.value.length, citeEl.value.length);
          }, 80);
        }
      });
    });

    content.querySelectorAll("[data-inspect-lost-houston]").forEach((lhBtn) => {
      lhBtn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const lhId = lhBtn.getAttribute("data-inspect-lost-houston") || "";
        if (lhId && this.mapController) {
          this.filterStore.setLayerVisibility("lostHouston", true);
          this.mapController.highlightBoundaryByIdOrName(
            {
              id: lhId,
              name: lhId,
              layerKey: "lost_houston",
            },
            {
              layerKey: "lost_houston",
              fitBounds: true,
              openInspector: true,
            }
          );
        }
      });
    });

    content.querySelectorAll("[data-overlay-lost-sanborn]").forEach((sbBtn) => {
      sbBtn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const mapId = sbBtn.getAttribute("data-overlay-lost-sanborn") || "sanborn_1907";
        const curLayers = this.filterStore.getState().layers || {};
        this.filterStore.setState({
          historicMapId: mapId,
          layers: {
            ...curLayers,
            historicMap: true,
            lostHouston: true,
          },
        });
        sbBtn.textContent = "1907 Sanborn Active ✓";
      });
    });

    content.querySelectorAll("[data-filter-chip]").forEach((chipBtn) => {
      chipBtn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const chipQuery = chipBtn.getAttribute("data-filter-chip") || "";
        const chipLabel = chipBtn.getAttribute("data-filter-label") || "";
        const portfolioKind = chipBtn.getAttribute("data-portfolio-kind") || "";
        if (chipQuery) {
          if (portfolioKind) {
            this.activatePortfolioMode(portfolioKind, chipQuery);
          } else {
            this.triggerMetadataFilterSearch(chipQuery, chipLabel);
          }
        }
      });
    });

    this._bindHahcCardEvents(content);

    content.querySelectorAll("[data-inspect-red-book]").forEach((rbBtn) => {
      rbBtn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const rbId = rbBtn.getAttribute("data-inspect-red-book") || "";
        if (rbId && this.mapController) {
          this.filterStore.setLayerVisibility("redBook1915", true);
          this.mapController.inspectRedBookById(rbId, { flyTo: false });
        }
      });
    });

    content.querySelectorAll("[data-highlight-boundary]").forEach((bBtn) => {
      bBtn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const bName = bBtn.getAttribute("data-highlight-boundary") || "";
        const bLayer = bBtn.getAttribute("data-boundary-layer") || "";
        const openDossier = bBtn.hasAttribute("data-open-boundary-dossier");
        if (bName && this.mapController) {
          if (bLayer === "holc_1937_zones") {
            this.filterStore.setLayerVisibility("holc1937", true);
          } else if (bLayer === "platted_subdivisions") {
            this.filterStore.setLayerVisibility("plattedSubdivisions", true);
          }
          if (openDossier) {
            this.mapController.highlightBoundaryByIdOrName(bName, {
              layerKey: bLayer,
              fitBounds: true,
              openInspector: true,
            });
            return;
          }
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
                : bLayer === "holc_1937_zones"
                ? "Outline Zone"
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

    this._populateInspectorPermits(props, hcadNum);

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
              const btnRkSv = document.getElementById("btn-rk-streetview");
              if (btnRkSv) btnRkSv.href = refinedSvUrl;
              if (this._activePhotoState) {
                this._activePhotoState.streetViewUrl = refinedSvUrl;
              }
            }
          }

          this._refreshResearchKitLegalTokens(props, rec);

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
          btnOpenHcad.innerHTML = `&#10003; Copied ${hcadNum} - Paste in HCAD Search &#8599;`;
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
        const { selectedHcad, selectedFeatureId } = this._getSelectionUrlIds(props);
        const url = buildShareableUrl(this.filterStore.getState(), vp, {
          includeViewport: true,
          selectedHcad,
          selectedFeatureId,
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

  async _fetchPermitRecord(hcadNum) {
    const clean = String(hcadNum || "").replace(/\D/g, "");
    if (clean.length < 10) return null;
    const prefix3 = clean.slice(0, 3);
    if (!this._permitShardCache.has(prefix3)) {
      const p = fetch(`public/data/permits/permits_${prefix3}.json?v=20261029a`)
        .then((res) => (res.ok ? res.json() : {}))
        .catch(() => ({}));
      this._permitShardCache.set(prefix3, p);
    }
    const shard = await this._permitShardCache.get(prefix3);
    return (shard && shard[clean]) || null;
  }

  async _populateInspectorPermits(props, hcadNum) {
    const rec = hcadNum ? await this._fetchPermitRecord(hcadNum) : null;
    this._populateResearchKitDeedChain(hcadNum, rec);

    const card = document.getElementById("inspector-permits-card");
    const badge = document.getElementById("inspector-permits-badge");
    const body = document.getElementById("inspector-permits-body");
    if (!card || !body) return;
    if (card.getAttribute("data-hcad-num") !== String(hcadNum || "")) return;

    // Update Remodel / Effective Year cell in the main grid if shard has richer data
    const remodelCell = document.getElementById("inspector-cell-remodel-year");
    const remodYr = Number(rec?.remod || props.yr_remodel || props.remodel_year) || 0;
    const effYr = Number(rec?.eff || props.eff_year_built) || 0;
    const origYr = Number(rec?.yr || props.year_built) || 0;
    if (remodelCell) {
      if (remodYr > 1836 && effYr > 1836 && effYr !== remodYr && effYr !== origYr) {
        remodelCell.textContent = `Remod ${remodYr} · Eff ${effYr}`;
      } else if (remodYr > 1836) {
        remodelCell.textContent = `Remodeled ${remodYr}`;
      } else if (effYr > 1836 && effYr !== origYr) {
        remodelCell.textContent = `Eff. Year ${effYr}`;
      }
    }

    const permitsList = Array.isArray(rec?.p) ? rec.p : [];
    const extraFeatures = Array.isArray(rec?.xf) ? rec.xf : [];
    const appraiserNote = String(rec?.note || "").trim();
    const relocNote = String(rec?.reloc || props.relocation_note || "").trim();
    const qualGrade = String(rec?.qual || "").trim();
    const origPermit = String(props.original_permit || "").trim();
    const origCost = String(props.original_cost || "").trim();

    if (badge) {
      if (permitsList.length > 0) {
        badge.textContent = `${permitsList.length} Permit${permitsList.length === 1 ? "" : "s"}${
          remodYr > 1836 ? ` · Remod ${remodYr}` : ""
        }`;
      } else if (remodYr > 1836) {
        badge.textContent = `Remodeled ${remodYr}`;
      } else if (extraFeatures.length > 0) {
        badge.textContent = `${extraFeatures.length} Dated Addition${extraFeatures.length === 1 ? "" : "s"}`;
      } else if (origPermit || origCost) {
        badge.textContent = "Archival Permit";
      } else {
        badge.textContent = "HCAD Structural History";
      }
    }

    const tabPermitsBadge = document.getElementById("inspector-tab-permits-count");
    if (tabPermitsBadge) {
      const totalItems =
        permitsList.length + extraFeatures.length + (origPermit || origCost ? 1 : 0);
      if (totalItems > 0) {
        tabPermitsBadge.textContent = String(totalItems);
        tabPermitsBadge.classList.remove("hidden");
      }
    }

    if (permitsList.length > 0 || relocNote || appraiserNote) {
      card.open = true;
    }

    const summaryPills = [];
    if (origYr > 1800) {
      summaryPills.push(`<span class="permit-stat-pill">Original Erection: <strong>${origYr}</strong></span>`);
    }
    if (remodYr > 1836) {
      summaryPills.push(`<span class="permit-stat-pill highlight">HCAD Remodel Year: <strong>${remodYr}</strong></span>`);
    }
    if (effYr > 1836 && effYr !== origYr) {
      summaryPills.push(`<span class="permit-stat-pill">Effective Age Basis: <strong>${effYr}</strong></span>`);
    }
    if (qualGrade) {
      summaryPills.push(`<span class="permit-stat-pill">Quality: <strong>${qualGrade}</strong></span>`);
    }

    let html = "";
    if (summaryPills.length > 0) {
      html += `<div class="permit-summary-badges">${summaryPills.join("")}</div>`;
    }

    if (relocNote) {
      html += `<div class="permit-appraiser-note reloc">
        <strong>&#128666; Documented Structure Relocation:</strong> ${relocNote}
      </div>`;
    }
    if (appraiserNote && appraiserNote !== relocNote) {
      html += `<div class="permit-appraiser-note">
        <strong>HCAD Appraiser Permit / Renovation Note:</strong> ${appraiserNote}
      </div>`;
    }

    const timelineItems = [];
    if (origPermit || origCost) {
      timelineItems.push(`
        <div class="permit-timeline-item">
          <div class="permit-timeline-top">
            <span class="permit-cat-badge cat-new">Original Construction Permit</span>
            <span class="permit-date-mono">${origYr || "Historic"}</span>
          </div>
          <div class="permit-desc-text">
            ${origPermit ? `Permit ${origPermit}` : "Original Building Permit"}${
        origCost ? ` · Contract / Valuation: ${origCost}` : ""
      }${props.builder ? ` · Contractor: ${props.builder}` : ""}
          </div>
        </div>
      `);
    }

    for (const p of permitsList) {
      const cat = String(p.cat || "other");
      const tp = String(p.tp || "Building Permit");
      const dt = String(p.dt || "");
      const desc = String(p.d || "");
      const pid = String(p.id || "");
      const srcLabel =
        p.src === "TX_DSHS"
          ? "TX DSHS Notification"
          : p.src === "COH_LIVE"
          ? "COH Live Permit"
          : "";
      timelineItems.push(`
        <div class="permit-timeline-item ${
          cat === "demo" ? "permit-item-demo" : cat === "asbestos" ? "permit-item-asbestos" : ""
        }">
          <div class="permit-timeline-top">
            <span class="permit-cat-badge cat-${cat}">${tp}</span>
            <span class="permit-date-mono">${dt}${pid ? ` · #${pid}` : ""}${
        srcLabel ? ` · ${srcLabel}` : ""
      }</span>
          </div>
          ${desc ? `<div class="permit-desc-text">${desc}</div>` : ""}
        </div>
      `);
    }

    if (extraFeatures.length > 0) {
      for (const xf of extraFeatures) {
        timelineItems.push(`
          <div class="permit-timeline-item xf-item">
            <div class="permit-timeline-top">
              <span class="permit-cat-badge cat-aux">Dated Site Feature / Sub-Structure</span>
              <span class="permit-date-mono">${xf.y || "Dated"}</span>
            </div>
            <div class="permit-desc-text">${xf.d}</div>
          </div>
        `);
      }
    }

    if (timelineItems.length > 0) {
      html += `<div class="permit-timeline-list">${timelineItems.join("")}</div>`;
    } else if (!appraiserNote && !relocNote) {
      html += `<div class="permit-empty-note">No itemized 2004-2026 municipal building permits recorded in HCAD's CAMA permit table for this parcel. Know of a historic building permit or contractor record? Click <strong>Suggest a Date / Shape / Data Correction</strong> below.</div>`;
    }

    body.innerHTML = html;
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

    const allAltAddrs = [
      props.address || "",
      ...(Array.isArray(props.alt_addresses) ? props.alt_addresses : []),
    ]
      .map((a) => String(a || "").trim())
      .filter(Boolean);

    const photos = await resolveBuildingPhotos({
      buildingId: props.building_id || props.id || "",
      hcadNum,
      landmarkName: props.landmark_name || props.name || "",
      address: props.address || "",
      altAddresses: allAltAddrs,
      useCategory: props.use_category || "",
      lat,
      lng,
    });

    if (this._latestPhotoRequestToken !== requestToken) return;

    this._activePhotoState = {
      title,
      subtitle,
      hcadNum: hcadNum || "",
      buildingId: props.building_id || props.id || "",
      address: props.address || subtitle || "",
      historicDistrict: props.historic_district || "",
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
        <div class="photo-provenance-row">
          <span class="photo-provenance-pill ${
            current.is_unverified_auto ? "unverified" : "verified"
          }" title="${this._escapeHtmlAttr(
            current.is_unverified_auto
              ? "Automated geographic or name match - click 'Wrong Building' if this photo depicts a neighboring building or modern replacement"
              : "Curated archival photograph matched to this landmark or parcel"
          )}">
            ${current.is_unverified_auto ? "&#9888;" : "&#10003;"} ${
              current.provenance_badge ||
              (current.is_unverified_auto
                ? "Unverified Automated Match"
                : "Verified Archival Photo")
            }
          </span>
          <button
            type="button"
            class="quick-flag-btn"
            data-action="flag-wrong-photo"
            title="Report that this photograph depicts the wrong building or a modern replacement; hides immediately and queues for Preservation Houston moderation"
          >
            &#9873; Wrong Building
          </button>
        </div>
        <div class="photo-meta-row">
          <span>${current.credit || current.attribution || "Public Domain / Wikimedia Commons"}</span>
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
              title="Hide this photograph locally in your browser"
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

    containerEl.querySelectorAll('[data-action="flag-wrong-photo"]').forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        if (!current?.image_url) return;
        const flaggedUrl = current.image_url;
        hideBuildingPhoto(flaggedUrl);
        state.photos = (state.photos || []).filter((p) => p.image_url !== flaggedUrl);
        state.activeIndex = Math.max(0, Math.min(state.activeIndex, state.photos.length - 1));
        state.thenIndex = 0;
        state.nowIndex = Math.max(0, state.photos.length - 1);
        if (state.photos.length < 2) state.compareMode = false;
        this._syncPhotoViews();

        await submitQuickFlagReport(
          {
            flagType: "photo",
            hcad_num: state.hcadNum || state.buildingId || "",
            address: state.address || state.title || "",
            historic_district: state.historicDistrict || "",
            photo_url: flaggedUrl,
            source_url: current.source_url || flaggedUrl,
            photo_caption: current.caption || current.era_label || "",
            provenance: current.provenance_badge || current.source_tier || "",
          },
          this._getActiveWebhookUrl()
        );
        this._showQuickFlagToast(
          "Photo hidden immediately and flagged as 'Wrong Building' for Preservation Houston moderation."
        );
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
    const { selectedHcad, selectedFeatureId } = this._getSelectionUrlIds(props);
    const shareUrl = buildShareableUrl(this.filterStore.getState(), vp, {
      includeViewport: true,
      selectedHcad,
      selectedFeatureId,
      useQueryString: true,
    });

    let photos =
      this._activePhotoState && Array.isArray(this._activePhotoState.photos)
        ? this._activePhotoState.photos
        : [];
    if (photos.length === 0) {
      const dossierAltAddrs = [
        props.address || "",
        ...(Array.isArray(props.alt_addresses) ? props.alt_addresses : []),
      ]
        .map((a) => String(a || "").trim())
        .filter(Boolean);
      photos =
        (await resolveBuildingPhotos({
          buildingId: props.building_id || props.id || "",
          hcadNum: hcadAcct,
          landmarkName: title,
          address: props.address || "",
          altAddresses: dossierAltAddrs,
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
    const liveLegalLine = document.getElementById("hcad-live-legal")?.textContent || "";
    const bldSqftText =
      Number(props.bld_area) > 0
        ? `${Number(props.bld_area).toLocaleString()} sq ft`
        : document.getElementById("inspector-cell-bld-sqft")?.textContent || "-";

    // Compute Archival Research & Legal Reference for printable sheet
    let dLng = Number.isFinite(Number(props.lon ?? props.lng)) ? Number(props.lon ?? props.lng) : null;
    let dLat = Number.isFinite(Number(props.lat)) ? Number(props.lat) : null;
    const selG = this.mapController ? this.mapController.selectedFeatureGeometry : null;
    if ((!Number.isFinite(dLng) || !Number.isFinite(dLat)) && selG && selG.coordinates) {
      if (selG.type === "Point") {
        [dLng, dLat] = selG.coordinates;
      } else {
        const r0 =
          selG.type === "Polygon"
            ? selG.coordinates[0]
            : selG.type === "MultiPolygon" && selG.coordinates[0]
            ? selG.coordinates[0][0]
            : null;
        if (Array.isArray(r0) && r0.length > 0) {
          dLng = r0[0][0];
          dLat = r0[0][1];
        }
      }
    }
    if (!Number.isFinite(dLng) || !Number.isFinite(dLat)) {
      dLng = vp?.lng ?? -95.3698;
      dLat = vp?.lat ?? 29.7604;
    }
    const dossierSanborn = this._resolveSanbornCoverageAtPoint(dLng, dLat, year || 0);
    const dossierAddrInfo = this._parseAddressForArchivalSearch(
      address,
      props.neighborhood || "",
      props.historic_district || ""
    );
    const dossierCityDir = this._buildCityDirectoryResearchData(
      props,
      hcadAcct,
      year || 0,
      dossierAddrInfo
    );
    const activeCtx =
      this._activeInspectorLegalCtx &&
      this._activeInspectorLegalCtx.hcadNum === String(hcadAcct || "").trim()
        ? this._activeInspectorLegalCtx
        : {};
    const liveRec = activeCtx.liveHcadRec || null;
    const cleanedLiveLegalLine = String(liveLegalLine || "")
      .split("·")
      .map((s) => s.trim())
      .filter((s) => s && !/^(?:State\s+Class|Grade)\b/i.test(s))
      .join(" · ");
    const dossierLegal = parseClerkLegalTokens({
      legalLines: liveRec?.legalLines,
      legalDescription:
        liveRec?.legalDescription || cleanedLiveLegalLine || props.legal_description || "",
      legalSubdivision: liveRec?.legalSubdivision || "",
      subdivision:
        props.subdivision || liveRec?.legalSubdivision || liveRec?.subdivision || "",
      marketNeighborhood: liveRec?.marketNeighborhood || "",
      historicParentPlat: activeCtx.historicParentPlat || null,
      subProps: activeCtx.subProps || null,
    });
    const dossierLegalSummary = [
      dossierLegal.cleanAdditionSearch ? `Subdivision: ${dossierLegal.cleanAdditionSearch}` : "",
      dossierLegal.section ? `Sec ${dossierLegal.section}` : "",
      dossierLegal.block ? `Blk ${dossierLegal.block}` : "",
      dossierLegal.lot ? `Lot ${dossierLegal.lot}` : "",
      dossierLegal.tract ? `Tract ${dossierLegal.tract}` : "",
    ]
      .filter(Boolean)
      .join(" · ") || (props.subdivision ? String(props.subdivision) : "See HCAD & County Clerk RP");
    const dossierCityDirSummary = dossierCityDir.auditHit
      ? `Verified in HPL Directories (Earliest: ${dossierCityDir.auditHit.e}; ${
          Array.isArray(dossierCityDir.auditHit.y) ? dossierCityDir.auditHit.y.length : 1
        } digitized vol.)`
      : dossierCityDir.isAuditedAbsent
      ? `Absent in 1866-1926 directories (Search "${dossierAddrInfo.cleanQuery || title}")`
      : `Search "${dossierAddrInfo.cleanQuery || title}" in HPL 1866-1926 Directories${
          year && year > 1926 ? " + HHRC Print Criss-Cross (1927+)" : ""
        }`;

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
                    <strong>${primaryPhoto.era_label || primaryPhoto.photo_year || "Archival View"}</strong> - ${
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
                          <strong>${secondaryPhoto.era_label || secondaryPhoto.photo_year || "Comparison View"}</strong> - ${
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

        <section class="dossier-grid-section">
          <div class="dossier-section-title">ARCHIVAL RESEARCH &amp; HOUSE HISTORY REFERENCE</div>
          <div class="dossier-kv-grid">
            <div class="dossier-kv-cell">
              <span class="dossier-k">Platted Legal Reference</span>
              <span class="dossier-v">${this._escapeResearchHtml(dossierLegalSummary)}</span>
            </div>
            <div class="dossier-kv-cell">
              <span class="dossier-k">Sanborn Atlas Coverage</span>
              <span class="dossier-v">${this._escapeResearchHtml(dossierSanborn.summaryText)}</span>
            </div>
            <div class="dossier-kv-cell">
              <span class="dossier-k">1866-1926 City Directories</span>
              <span class="dossier-v">${this._escapeResearchHtml(dossierCityDirSummary)}</span>
            </div>
            <div class="dossier-kv-cell">
              <span class="dossier-k">Primary Deeds &amp; Plats</span>
              <span class="dossier-v">Harris County Clerk RP / MC &amp; County Archives</span>
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
                        a.recipient ? `<em>${a.recipient}</em> - ` : ""
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
    const { selectedHcad, selectedFeatureId } = this._getSelectionUrlIds(selProps);

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

    const selBnd = this.mapController?.selectedBoundaryFeature?.properties || null;
    const selectedBoundary =
      !selectedHcad && !selectedFeatureId && selBnd && (selBnd.id || selBnd.name || selBnd.era_label)
        ? {
            layerKey: selBnd.overlay_layer || "neighborhoods",
            id: selBnd.id || selBnd.name || selBnd.era_label || "",
          }
        : null;
    const activePortfolioSpec = this._activePortfolio
      ? { kind: this._activePortfolio.kind, query: this._activePortfolio.query }
      : null;

    const shareUrl = buildShareableUrl(state, vp, {
      includeViewport,
      selectedHcad: includeSelection ? selectedHcad : "",
      selectedFeatureId: includeSelection ? selectedFeatureId : "",
      selectedBoundary,
      activeChapterId: this._activeChapterId || "",
      activePortfolio: activePortfolioSpec,
      collapseSidebar,
      useQueryString: true,
    });

    const embedUrl = buildShareableUrl(state, vp, {
      includeViewport,
      selectedHcad: includeSelection ? selectedHcad : "",
      selectedFeatureId: includeSelection ? selectedFeatureId : "",
      selectedBoundary,
      activeChapterId: this._activeChapterId || "",
      activePortfolio: activePortfolioSpec,
      collapseSidebar: true,
      useQueryString: true,
    });

    const urlInput = document.getElementById("share-url-input");
    if (urlInput) urlInput.value = shareUrl;

    const openTabBtn = document.getElementById("btn-open-share-url-tab");
    if (openTabBtn) openTabBtn.href = shareUrl;

    const embedInput = document.getElementById("share-embed-input");
    if (embedInput) {
      embedInput.value = `<iframe src="${embedUrl}" width="100%" height="680" style="border:0;border-radius:12px;" loading="lazy" title="The Houston Building Atlas · Preservation Houston"></iframe>`;
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
        permitWatch: "Active Permit Watch",
        platWatch: "Plat & Replat Monitor",
        hahcTracker: "HAHC COA & Designations (283)",
        goodBrickAwards: "★ Good Brick Awards",
        landmarks: "COH Landmarks",
        potentialHistoricDistricts: "Potential Historic Districts (60)",
        redBook1915: "1915 Red Book of Houston (970)",
        holc1937: "1937 HOLC Security Grades (47)",
        lostHouston: "Lost Houston (110)",
        relocatedStructures: "Relocated Structures",
        historicDistricts: "COH Historic Districts (23)",
        heritageDistricts: "Freedmen's Town Heritage Dist",
        nrhpDistricts: "NRHP Districts (12)",
        nrhpProperties: "NRHP Individual Properties (238)",
        culturalDistricts: "Cultural Arts Districts (7)",
        neighborhoods: "Neighborhoods",
        plattedSubdivisions: "Platted Subdivisions & Deeds",
        landUseProtections: "Ch. 42 Lot & Setback Protections",
        superNeighborhoods: "Super Neighborhoods",
        historicWards: "Historic Wards",
        thcMarkers: "THC Markers",
        annexations: "Annexation History",
        historicalWaterways: "Historical Bayous & Gullies",
        historicalRailroads: "Historical Railroads & Streetcars",
        historicMap: "Historic Sanborn / USGS Map",
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
      if (
        state.layers?.landmarks &&
        (state.landmarkFilter === "protected" || state.landmarkFilter === "waiver_lm")
      ) {
        pills.push(
          `<span class="share-pill-tag accent">${
            state.landmarkFilter === "protected"
              ? "Landmarks: Protected (286)"
              : "Landmarks: 90-Day Waiver (223)"
          }</span>`
        );
      }
      if (state.layers?.redBook1915 && state.redBookCategory && state.redBookCategory !== "all") {
        const rbCatNames = {
          church: "Churches (64)",
          school: "Schools (12)",
          business: "Businesses (394)",
          residence: "Civic Leaders & Homes (500)",
        };
        pills.push(
          `<span class="share-pill-tag accent">1915 Red Book: ${
            rbCatNames[state.redBookCategory] || state.redBookCategory
          }</span>`
        );
      }
      if (state.layers?.holc1937 && state.holcGradeFilter && state.holcGradeFilter !== "all") {
        const holcNames = {
          A: 'Grade A "Best" (7)',
          B: 'Grade B "Still Desirable" (14)',
          C: 'Grade C "Definitely Declining" (15)',
          D: 'Grade D "Hazardous" (11)',
        };
        pills.push(
          `<span class="share-pill-tag accent">1937 HOLC: ${
            holcNames[String(state.holcGradeFilter).toUpperCase()] || state.holcGradeFilter
          }</span>`
        );
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
        label: "1937 HOLC Redlining Security Zones",
        items: [
          { val: "holc_1937_zones::holc_1937_d8", text: "HOLC D-8 (Hazardous): Fourth Ward / Freedmen's Town" },
          { val: "holc_1937_zones::holc_1937_d5", text: "HOLC D-5 (Hazardous): Fifth Ward" },
          { val: "holc_1937_zones::holc_1937_d10", text: "HOLC D-10 (Hazardous): Third Ward" },
          { val: "holc_1937_zones::holc_1937_d4", text: "HOLC D-4 (Hazardous): Near Northside / Hardy Yards" },
          { val: "holc_1937_zones::holc_1937_c9", text: "HOLC C-9 (Declining): Houston Heights / Woodland Heights" },
          { val: "holc_1937_zones::holc_1937_b7", text: "HOLC B-7 (Still Desirable): Montrose / Museum District" },
          { val: "holc_1937_zones::holc_1937_a1", text: "HOLC A-1 (Best): River Oaks" },
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

  openSanbornBrowserModal(initialVolId = null, initialSheetCode = null, initialSp = null) {
    const modal = document.getElementById("sanborn-browser-modal");
    if (!modal) return;
    modal.classList.remove("hidden");

    const btnActivateMosaic = document.getElementById("btn-sanborn-activate-1907-mosaic");
    if (btnActivateMosaic && !btnActivateMosaic._boundSanbornActivate) {
      btnActivateMosaic._boundSanbornActivate = true;
      btnActivateMosaic.addEventListener("click", () => {
        this.filterStore.setLayerEnabled("historic_map", true);
        this.filterStore.setHistoricMapId("sanborn_1907");
        const chkHistoricMap = document.getElementById("layer-historic_map");
        if (chkHistoricMap) chkHistoricMap.checked = true;
        const opacityRow = document.getElementById("historic-map-opacity-row");
        if (opacityRow) opacityRow.classList.remove("hidden");
        const selectSource = document.getElementById("select-historic-map-source");
        if (selectSource) selectSource.value = "sanborn_1907";
        modal.classList.add("hidden");
      });
    }

    const selectVol = document.getElementById("select-sanborn-volume");
    const inputFilter = document.getElementById("input-sanborn-filter");
    const volumes = Array.isArray(this.sanbornCatalog?.volumes)
      ? this.sanbornCatalog.volumes
      : [];
    const totalSheetsCount =
      Number(this.sanbornCatalog?.total_digitized_sheets) ||
      volumes.reduce((acc, v) => acc + (Number(v.sheet_count) || 0), 0) ||
      878;
    if (selectVol && selectVol.options.length <= 1 && volumes.length > 0) {
      selectVol.innerHTML =
        `<option value="featured">★ Featured Historic Sheets Across All Editions (1877-1955)</option>` +
        `<option value="all">All ${volumes.length} Digitized Volumes (${totalSheetsCount} Sheets Total)</option>` +
        volumes
          .map(
            (v) =>
              `<option value="${v.id}">${v.title || `${v.date} (${v.sheet_count} Sheets)`}</option>`
          )
          .join("");
      selectVol.value = "featured";
    }

    if (initialVolId && volumes.some((v) => v.id === initialVolId)) {
      if (selectVol) selectVol.value = initialVolId;
      if (inputFilter) inputFilter.value = "";
      const targetVol = volumes.find((v) => v.id === initialVolId);
      if (targetVol && Array.isArray(targetVol.sheets) && targetVol.sheets.length > 0) {
        const rawCode = initialSheetCode ? String(initialSheetCode).trim() : "";
        const paddedCode =
          rawCode && /^\d+$/.test(rawCode)
            ? String(Number(rawCode)).padStart(4, "0")
            : rawCode;
        const matchedSheet =
          (rawCode &&
            targetVol.sheets.find(
              (s) => String(s.code) === rawCode || String(s.code) === paddedCode
            )) ||
          (initialSp && targetVol.sheets.find((s) => Number(s.sp) === Number(initialSp))) ||
          targetVol.sheets[0];
        if (matchedSheet) {
          this._activeSanbornSheetRec = { key: `${initialVolId}__${matchedSheet.sp}` };
        }
      }
    }

    this._renderSanbornBrowserSheets();
    if (initialVolId) {
      const grid =
        document.getElementById("sanborn-sheet-grid") ||
        document.getElementById("sanborn-sheets-grid");
      const activeBtn = grid?.querySelector(".sanborn-sheet-card.active");
      if (activeBtn && typeof activeBtn.scrollIntoView === "function") {
        setTimeout(() => activeBtn.scrollIntoView({ block: "nearest", behavior: "smooth" }), 50);
      }
    }
  }

  _formatSanbornSheetTag(code, sp, source = "loc") {
    const codeStr = String(code || "").trim();
    const isUt = source === "ut_pcl";
    if (isUt) {
      if (/^\d+k\d*$/i.test(codeStr)) return "Key Map (UT PCL)";
      if (/^\d+i\d*$/i.test(codeStr)) return "Street Index (UT PCL)";
      if (/^\d+t$/i.test(codeStr)) return "Title Sheet (UT PCL)";
      if (/^v[12]-\d+$/i.test(codeStr)) {
        const [vp, sn] = codeStr.split("-");
        return `${vp.toUpperCase()} Sheet ${sn} (UT PCL)`;
      }
      if (/^\d+$/.test(codeStr)) return `Sheet ${Number(codeStr)} (UT PCL)`;
      return `Plate ${sp} (UT PCL)`;
    }
    if (/^\d+$/.test(codeStr)) {
      const n = Number(codeStr);
      if (n === 0) return `Key Map (LOC p.${sp})`;
      return n !== Number(sp) ? `Sheet ${n} (LOC p.${sp})` : `Sheet ${n}`;
    }
    if (codeStr.endsWith("s") && /^\d+$/.test(codeStr.slice(0, -1))) {
      return `Skeleton ${Number(codeStr.slice(0, -1))} (LOC p.${sp})`;
    }
    if (codeStr === "titl") return `Title Sheet (LOC p.${sp})`;
    if (codeStr.startsWith("ind")) return `Index Map (LOC p.${sp})`;
    return `Page ${sp}`;
  }

  _selectSanbornPreviewSheet(rec) {
    if (!rec) return;
    this._activeSanbornSheetRec = rec;
    const titleEl = document.getElementById("sanborn-preview-title");
    const subEl = document.getElementById("sanborn-preview-subtitle");
    const stageEl = document.getElementById("sanborn-preview-stage");
    const footerEl = document.getElementById("sanborn-preview-footer");
    const btnLoc = document.getElementById("btn-sanborn-open-loc-viewer");
    const btnJpg = document.getElementById("btn-sanborn-open-hires-jpg");

    const isUt = rec.source === "ut_pcl";
    const previewUrl = rec.previewUrl || rec.thumbUrl;
    const hiresUrl = rec.hiresUrl || previewUrl;
    const sheetTag = this._formatSanbornSheetTag(rec.code, rec.sheetNum, rec.source);

    if (titleEl) titleEl.textContent = rec.label;
    if (subEl) subEl.textContent = `${rec.volTitle} · Published ${rec.date} · ${sheetTag}`;
    if (btnLoc) {
      btnLoc.href = rec.locSheetUrl;
      btnLoc.innerHTML = isUt
        ? "Open UT PCL Houston Index &#8599;"
        : "Open Full Zoomable Sheet on LOC.gov &#8599;";
      btnLoc.title = isUt
        ? "Open UT Austin Perry-Castaneda Library Houston Sanborn Map Index (h.html)"
        : "Open interactive zoomable IIIF sheet viewer on Library of Congress (loc.gov)";
    }
    if (btnJpg) {
      btnJpg.href = hiresUrl;
      btnJpg.innerHTML = isUt
        ? "Full 3800px UT PCL Scan (.JPG) &#8599;"
        : "Hi-Res Scan (.JPG) &#8599;";
      btnJpg.title = isUt
        ? "Open full 3800x4676 archival JPEG scan directly from UT Austin Perry-Castaneda Library"
        : "Open high-resolution 2000px JPEG scan directly from Library of Congress IIIF server";
    }
    if (stageEl) {
      stageEl.innerHTML = `
        <a href="${hiresUrl}" target="_blank" rel="noopener noreferrer" title="${
          isUt
            ? "Click to open full 3800px archival JPEG scan at UT Austin Perry-Castaneda Library in a new tab"
            : "Click to open high-resolution IIIF scan in a new tab"
        }">
          <img id="sanborn-preview-stage-img" src="${rec.thumbUrl}" alt="${rec.label.replace(/"/g, "&quot;")}" loading="eager" />
        </a>
      `;
      if (previewUrl && previewUrl !== rec.thumbUrl) {
        const hiLoader = new Image();
        hiLoader.onload = () => {
          if (this._activeSanbornSheetRec?.key !== rec.key) return;
          const stageImg = document.getElementById("sanborn-preview-stage-img");
          if (stageImg) stageImg.src = previewUrl;
        };
        hiLoader.src = previewUrl;
      }
    }
    if (footerEl) {
      const archiveAttribution = isUt
        ? `UT Austin Perry-Castaneda Library / Dolph Briscoe Center for American History (${rec.volId})`
        : `Library of Congress Geography &amp; Map Division (${rec.volId})`;
      footerEl.innerHTML = `
        <strong>${archiveAttribution}</strong> ·
        Color Key: <span style="color:#fde047;">Yellow = Wood Frame</span>,
        <span style="color:#f472b6;">Pink = Brick Masonry</span>,
        <span style="color:#60a5fa;">Blue = Stone / Concrete</span>,
        <span style="color:#cbd5e1;">Gray = Iron / Metal</span>,
        <span style="color:#a3e635;">Olive = Fireproof Steel</span>.
      `;
    }
  }

  _renderSanbornBrowserSheets() {
    const grid =
      document.getElementById("sanborn-sheet-grid") ||
      document.getElementById("sanborn-sheets-grid");
    const metaBar =
      document.getElementById("sanborn-volume-meta-banner") ||
      document.getElementById("sanborn-volume-meta-bar");
    const countBadge = document.getElementById("sanborn-sheet-count-badge");
    const selectVol = document.getElementById("select-sanborn-volume");
    const inputFilter = document.getElementById("input-sanborn-filter");
    if (!grid) return;

    const volumes = Array.isArray(this.sanbornCatalog?.volumes)
      ? this.sanbornCatalog.volumes
      : [];
    if (!volumes.length) {
      grid.innerHTML = `<div style="padding:18px;color:var(--text-secondary);">Loading Houston Sanborn catalog...</div>`;
      return;
    }

    const totalSheetsCount =
      Number(this.sanbornCatalog?.total_digitized_sheets) ||
      volumes.reduce((acc, v) => acc + (Number(v.sheet_count) || 0), 0) ||
      878;
    const selectedVolId = selectVol ? selectVol.value || "featured" : "featured";
    const filterQ = String(inputFilter?.value || "")
      .trim()
      .toLowerCase();

    const buildSheetRecordFromVerified = (vol, shObj) => {
      const sp = Number(shObj.sp) || 1;
      const source = shObj.source || vol.source || (String(vol.id).startsWith("utpcl_") ? "ut_pcl" : "loc");
      const locSheetUrl =
        shObj.loc_url ||
        (source === "ut_pcl"
          ? vol.loc_url || "https://maps.lib.utexas.edu/maps/sanborn/h.html"
          : `${String(
              vol.loc_resource_url || vol.loc_url || "https://www.loc.gov/collections/sanborn-maps/"
            ).replace(/\/?$/, "/")}?sp=${sp}`);
      return {
        key: `${vol.id}__${sp}`,
        volId: vol.id,
        source,
        era: vol.era,
        date: vol.date,
        volTitle: vol.title,
        locUrl: vol.loc_url,
        locSheetUrl,
        sheetNum: sp,
        code: shObj.code || String(sp),
        label: shObj.label || `Sheet ${sp} (${vol.date})`,
        isFeatured: Boolean(shObj.is_featured),
        thumbUrl: shObj.thumb_url,
        previewUrl: shObj.preview_url || shObj.thumb_url,
        hiresUrl: shObj.hires_url || shObj.preview_url || shObj.thumb_url,
      };
    };

    const items = [];
    let selectedVolObj = null;

    for (const vol of volumes) {
      if (
        selectedVolId !== "all" &&
        selectedVolId !== "featured" &&
        vol.id !== selectedVolId
      ) {
        continue;
      }
      if (vol.id === selectedVolId) {
        selectedVolObj = vol;
      }

      if (Array.isArray(vol.sheets) && vol.sheets.length > 0) {
        for (const shObj of vol.sheets) {
          if (selectedVolId === "featured" && !filterQ && !shObj.is_featured) {
            continue;
          }
          const rec = buildSheetRecordFromVerified(vol, shObj);
          if (filterQ) {
            const hay = `${rec.label} ${rec.volTitle} ${rec.date} sheet ${rec.code} sheet ${Number(rec.code) || ""} page ${rec.sheetNum}`.toLowerCase();
            if (!hay.includes(filterQ)) continue;
          }
          items.push(rec);
        }
      }
    }

    if (countBadge) {
      countBadge.textContent = `${items.length} sheets`;
    }

    if (metaBar) {
      if (selectedVolObj) {
        const isUtVol =
          selectedVolObj.source === "ut_pcl" || String(selectedVolObj.id).startsWith("utpcl_");
        metaBar.innerHTML = `
          <div>
            <strong>${selectedVolObj.title}</strong> · Published ${selectedVolObj.date} ·
            <strong>${selectedVolObj.sheet_count}</strong> digitized plates via ${
              isUtVol
                ? "UT Austin Perry-Castaneda Library (Dolph Briscoe Center)"
                : "Library of Congress Geography &amp; Map Division"
            }
          </div>
          <a href="${selectedVolObj.loc_url}" target="_blank" rel="noopener noreferrer" class="action-pill subtle">
            ${
              isUtVol
                ? "Open UT PCL Houston Sanborn Index (h.html) &#8599;"
                : "Open Full Volume at Library of Congress (loc.gov) &#8599;"
            }
          </a>
        `;
      } else {
        metaBar.innerHTML = `
          <div>
            Showing <strong>${items.length}</strong> ${
          selectedVolId === "featured" && !filterQ ? "curated highlight sheets" : "matching sheets"
        } across <strong>${volumes.length} Library of Congress &amp; UT PCL Houston Sanborn Volumes (1877-1955, ${totalSheetsCount} sheets total)</strong>.
            Click any sheet on the left to inspect its high-resolution plate.
          </div>
          <div style="display:flex;gap:8px;flex-wrap:wrap;">
            <a href="https://www.loc.gov/collections/sanborn-maps/?fa=location:texas%7Clocation:houston" target="_blank" rel="noopener noreferrer" class="action-pill subtle">
              LOC Houston Archive &#8599;
            </a>
            <a href="https://maps.lib.utexas.edu/maps/sanborn/h.html" target="_blank" rel="noopener noreferrer" class="action-pill subtle">
              UT PCL Houston Index &#8599;
            </a>
          </div>
        `;
      }
    }

    const displayItems = items.slice(0, 120);
    const activeKey =
      displayItems.find((d) => d.key === this._activeSanbornSheetRec?.key)?.key ||
      displayItems[0]?.key ||
      "";
    const activeRec = displayItems.find((d) => d.key === activeKey) || displayItems[0] || null;

    grid.innerHTML = displayItems
      .map(
        (it) => `
        <button type="button" class="sanborn-sheet-card ${it.key === activeKey ? "active" : ""}" data-sanborn-key="${it.key}">
          <div class="sanborn-sheet-thumb-wrap">
            <img
              src="${it.thumbUrl}"
              alt="${it.label.replace(/"/g, "&quot;")}"
              class="sanborn-sheet-thumb"
              loading="lazy"
            />
          </div>
          <div class="sanborn-sheet-label">${it.label}</div>
          <div class="mono" style="font-size:10px;color:#94a3b8;">${it.date} · ${this._formatSanbornSheetTag(it.code, it.sheetNum, it.source)}</div>
        </button>`
      )
      .join("");

    if (activeRec) {
      this._selectSanbornPreviewSheet(activeRec);
    }

    grid.querySelectorAll("[data-sanborn-key]").forEach((cardBtn) => {
      cardBtn.addEventListener("click", () => {
        const k = cardBtn.getAttribute("data-sanborn-key");
        const found = displayItems.find((d) => d.key === k);
        if (found) {
          grid.querySelectorAll(".sanborn-sheet-card").forEach((b) => b.classList.remove("active"));
          cardBtn.classList.add("active");
          this._selectSanbornPreviewSheet(found);
        }
      });
    });
  }

  _syncPermitWatchCounts() {
    const payload = this.mapController?.getPermitAlertsPayload?.();
    const alerts = Array.isArray(payload?.alerts) ? payload.alerts : [];
    const total = alerts.length;
    const critCount = alerts.filter((a) => a.severity === "critical").length;
    const watchCount = total - critCount;
    const cohCount = Number(payload?.summary?.coh_permits_matched || 0);
    const dshsCount = Number(payload?.summary?.dshs_notifications_matched || 0);

    const topCountEl = document.getElementById("permit-watch-topbar-count");
    if (topCountEl) topCountEl.textContent = String(total);
    const layerCountEl = document.getElementById("permit-watch-layer-count");
    if (layerCountEl) layerCountEl.textContent = String(total);
    const platLayerCountEl = document.getElementById("plat-watch-layer-count");
    if (platLayerCountEl && this.mapController?.getPlatWatchFeatureCount) {
      const platCnt = this.mapController.getPlatWatchFeatureCount();
      if (platCnt > 0) platLayerCountEl.textContent = String(platCnt);
    }

    const critEl = document.getElementById("pw-stat-critical");
    if (critEl) critEl.textContent = String(critCount);
    const watchEl = document.getElementById("pw-stat-watch");
    if (watchEl) watchEl.textContent = String(watchCount);
    const cohEl = document.getElementById("pw-stat-coh");
    if (cohEl) cohEl.textContent = String(cohCount);
    const dshsEl = document.getElementById("pw-stat-dshs");
    if (dshsEl) dshsEl.textContent = String(dshsCount);

    const pillAll = document.getElementById("pw-pill-count-all");
    if (pillAll) pillAll.textContent = String(total);
    const pillCrit = document.getElementById("pw-pill-count-critical");
    if (pillCrit) pillCrit.textContent = String(critCount);
    const pillWatch = document.getElementById("pw-pill-count-watch");
    if (pillWatch) pillWatch.textContent = String(watchCount);

    const updatedBadge = document.getElementById("permit-watch-updated-badge");
    if (updatedBadge && payload?.generated_at) {
      const dtStr = String(payload.generated_at).slice(0, 10);
      updatedBadge.textContent = `Updated ${dtStr} · Daily Sync`;
    }

    const b = resolveAnnualDemolitionBenchmarks(payload);
    const def = DEFAULT_ANNUAL_DEMOLITION_BENCHMARKS;
    const fmt = (n) => Number(n ?? 0).toLocaleString("en-US");
    const cohYtd =
      (b.coh_structural_demolitions || [])[0] || def.coh_structural_demolitions[0];
    const coh25 =
      (b.coh_structural_demolitions || [])[1] || def.coh_structural_demolitions[1];
    const hcad25 =
      (b.hcad_countywide_demolitions || [])[0] || def.hcad_countywide_demolitions[0];
    const hahc26 =
      (b.hahc_historic_district_demolitions || [])[0] ||
      def.hahc_historic_district_demolitions[0];

    const sbYtd = document.getElementById("pw-sidebar-ytd-count");
    if (sbYtd) sbYtd.textContent = fmt(cohYtd.total_permits ?? def.coh_structural_demolitions[0].total_permits);
    const sb25 = document.getElementById("pw-sidebar-2025-count");
    if (sb25) sb25.textContent = fmt(coh25.total_permits ?? def.coh_structural_demolitions[1].total_permits);
    const sbHahc = document.getElementById("pw-sidebar-hahc-count");
    if (sbHahc) sbHahc.textContent = fmt(hahc26.main_res_approved ?? 0);

    const pillYtd = document.getElementById("pw-bench-pill-ytd");
    if (pillYtd) pillYtd.textContent = fmt(cohYtd.total_permits ?? def.coh_structural_demolitions[0].total_permits);
    const pill25 = document.getElementById("pw-bench-pill-2025");
    if (pill25) pill25.textContent = fmt(coh25.total_permits ?? def.coh_structural_demolitions[1].total_permits);
    const pillHcad = document.getElementById("pw-bench-pill-hcad");
    if (pillHcad) pillHcad.textContent = fmt(hcad25.total_permits ?? def.hcad_countywide_demolitions[0].total_permits);
    const pillHahc = document.getElementById("pw-bench-pill-hahc");
    if (pillHahc) pillHahc.textContent = fmt(hahc26.main_res_approved ?? 0);

    this._renderPermitWatchAnnualBenchmarks(payload);
  }

  _renderPermitWatchAnnualBenchmarks(payload = null) {
    const bodyEl = document.getElementById("pw-annual-benchmarks-body");
    if (!bodyEl) return;
    const resolvedPayload = payload || this.mapController?.getPermitAlertsPayload?.();
    bodyEl.innerHTML = buildDemolitionBenchmarksTablesHtml(resolvedPayload, {
      variant: "modal",
    });
  }

  _buildInspectorPlatWatchCardHtml(platItem) {
    if (!platItem) return "";
    const isCrit =
      platItem.tier === "tier1" || platItem.is_variance || platItem.is_public_hearing;
    const appId = platItem.appNo || platItem.id || "";
    const hasWatchlistEntry = platItem.tier === "tier1" || platItem.tier === "tier2";
    return `
      <div class="inspector-permit-alert-card ${isCrit ? "critical" : "watch"}" style="border-color:rgba(244,63,94,0.45);background:rgba(136,19,55,0.18);">
        <div class="inspector-permit-alert-header">
          <span class="inspector-permit-alert-kicker">
            &#128203; Planning Commission Plat &amp; Variance Monitor
          </span>
          <span class="inspector-permit-alert-badge mono">App #${appId || "N/A"} · Sec ${platItem.section || "I"}</span>
        </div>
        <div class="inspector-permit-alert-headline">${platItem.name || ""} (${platItem.type || "Plat"})</div>
        ${
          platItem.variance_summary
            ? `<div class="inspector-permit-alert-narrative">${platItem.variance_summary}</div>`
            : ""
        }
        ${
          platItem.notes
            ? `<div class="inspector-permit-alert-narrative">${platItem.notes}</div>`
            : ""
        }
        <div class="inspector-permit-alert-actions" style="display:flex;gap:8px;flex-wrap:wrap;">
          <a
            href="plat-monitor.html?tab=plat&id=${encodeURIComponent(appId)}"
            target="_blank"
            rel="noopener"
            class="inspector-permit-watch-btn primary"
          >
            &#128203; Open in Plat &amp; Property Monitor &#8599;
          </a>
          ${
            hasWatchlistEntry
              ? `<a
                  href="plat-monitor.html?tab=watchlist&watch=${encodeURIComponent("plat-" + appId)}&hcad=${encodeURIComponent(platItem.hcad_num || "")}"
                  target="_blank"
                  rel="noopener"
                  class="inspector-permit-watch-btn secondary"
                >
                  &#128269; Open 15-Point Watchlist &#8599;
                </a>`
              : ""
          }
        </div>
      </div>
    `;
  }

  _buildInspectorPermitAlertCardHtml(alert, platItem = null) {
    if (!alert) return "";
    const isCritical = alert.severity === "critical";
    const cohPermits = Array.isArray(alert.coh_permits) ? alert.coh_permits : [];
    const dshsFilings = Array.isArray(alert.dshs_notifications) ? alert.dshs_notifications : [];
    const multiFilings = Array.isArray(alert.multi_agency_filings) ? alert.multi_agency_filings : [];
    const secondaryAddrs = Array.isArray(alert.secondary_addresses) ? alert.secondary_addresses : [];
    const totalAlertsCount = Array.isArray(this.mapController?.getPermitAlertsPayload?.()?.alerts)
      ? this.mapController.getPermitAlertsPayload().alerts.length
      : 15;

    const cohRowsHtml = cohPermits
      .map(
        (cp) => `
        <div class="inspector-permit-filing-box coh">
          <div class="inspector-permit-filing-top">
            <span class="inspector-permit-agency-tag coh">COH Sold Permit #${cp.project_no || "N/A"}</span>
            <span class="mono inspector-permit-filing-date">${cp.date || ""}</span>
          </div>
          <div class="inspector-permit-filing-title">
            <strong>${cp.permit_type_label || cp.permit_type || "Permit"}:</strong>
            ${cp.use_desc || cp.permit_desc || ""}
          </div>
          <div class="inspector-permit-filing-meta">
            ${cp.fcc_group ? `<span><strong>Group:</strong> ${cp.fcc_group}</span>` : ""}
            ${cp.buyer ? `<span><strong>Applicant / Contractor:</strong> ${cp.buyer}</span>` : ""}
            ${cp.owner_occupant ? `<span><strong>Listed Occupant:</strong> ${cp.owner_occupant}</span>` : ""}
          </div>
        </div>`
      )
      .join("");

    const dshsRowsHtml = dshsFilings
      .map(
        (dn) => `
        <div class="inspector-permit-filing-box dshs">
          <div class="inspector-permit-filing-top">
            <span class="inspector-permit-agency-tag dshs">TX DSHS #${dn.notification_number || "N/A"} (${dn.amendment_number || "O"})</span>
            <span class="mono inspector-permit-filing-date">Work Window: ${dn.start_date || "?"} &#8594; ${dn.end_date || "?"}</span>
          </div>
          <div class="inspector-permit-filing-title">
            <strong>${dn.work_type || "Asbestos / Demolition Notification"}:</strong>
            ${dn.work_description || "Regulated Asbestos-Containing Material (RACM) Abatement"}${
          dn.racm_sqft ? ` (${Number(dn.racm_sqft).toLocaleString()} sq ft RACM)` : ""
        }
          </div>
          <div class="inspector-permit-filing-meta">
            ${dn.containment_method ? `<span><strong>Containment:</strong> ${dn.containment_method}</span>` : ""}
            ${dn.facility_owner ? `<span><strong>Facility Owner:</strong> ${dn.facility_owner}</span>` : ""}
            ${dn.abatement_contractor ? `<span><strong>Abatement Operator:</strong> ${dn.abatement_contractor}</span>` : ""}
            ${dn.project_consultant ? `<span><strong>Licensed Consultant:</strong> ${dn.project_consultant}</span>` : ""}
            ${dn.disposal_site ? `<span><strong>Waste Disposal:</strong> ${dn.disposal_site}</span>` : ""}
          </div>
        </div>`
      )
      .join("");

    const multiRowsHtml = multiFilings
      .map(
        (mf) => `
        <div class="inspector-permit-filing-box" style="border-left:3px solid #A78BFA;background:rgba(139,92,246,0.11);padding:8px 10px;border-radius:6px;margin-top:6px;">
          <div class="inspector-permit-filing-top">
            <span class="inspector-permit-agency-tag" style="background:rgba(139,92,246,0.25);color:#DDD6FE;border:1px solid rgba(167,139,250,0.45);">${mf.agency || "State / County Filing"} · ${mf.filing_id || ""}</span>
            <span class="mono inspector-permit-filing-date">${mf.date || ""}</span>
          </div>
          <div class="inspector-permit-filing-title">
            <strong>${mf.status || "Filing"} (${mf.address_used || alert.address || ""}):</strong>
            ${mf.title || ""}
          </div>
          <div class="inspector-permit-filing-meta">
            ${mf.details ? `<span>${mf.details}</span>` : ""}
            ${
              mf.url
                ? `<a href="${mf.url}" target="_blank" rel="noopener" style="color:#C4B5FD;text-decoration:underline;font-weight:600;margin-left:4px;">Source Portal &#8599;</a>`
                : ""
            }
          </div>
        </div>`
      )
      .join("");

    return `
      <div class="inspector-permit-alert-card ${isCritical ? "critical" : "watch"}" id="inspector-permit-alert-card">
        <div class="inspector-permit-alert-header">
          <span class="inspector-permit-alert-kicker">
            ${isCritical ? "&#128680; Active Demolition / Sewer Disconnection Alert" : "&#9888; Active Permit &amp; Abatement Watch"}
          </span>
          <span class="inspector-permit-alert-badge mono">${alert.badge || "Active Filing"}</span>
        </div>
        <div class="inspector-permit-alert-headline">${alert.headline || ""}</div>
        ${
          secondaryAddrs.length > 0
            ? `<div style="font-size:11px;color:#DDD6FE;margin-bottom:6px;"><strong>Block Perimeter / Secondary Addresses Monitored:</strong> <span class="mono">${secondaryAddrs.join(", ")}</span></div>`
            : ""
        }
        ${
          alert.preservation_note
            ? `<div class="inspector-permit-alert-narrative">${alert.preservation_note}</div>`
            : ""
        }
        <div class="inspector-permit-filings-stack">
          ${cohRowsHtml}
          ${dshsRowsHtml}
          ${multiRowsHtml}
        </div>
        <div class="inspector-permit-alert-actions" style="flex-wrap:wrap;gap:6px;">
          <button
            type="button"
            class="inspector-permit-watch-btn primary"
            data-open-permit-watchlist="all"
          >
            &#9888; Open Active Watchlist (${totalAlertsCount} Flagged Structures)
          </button>
          <a
            href="plat-monitor.html?tab=watchlist&hcad=${encodeURIComponent(alert.hcad_num || "")}&address=${encodeURIComponent(alert.address || "")}&name=${encodeURIComponent(alert.building_name || "")}"
            target="_blank"
            rel="noopener"
            class="inspector-permit-watch-btn secondary"
            style="border-color:rgba(167,139,250,0.5);color:#DDD6FE;"
          >
            &#128203; Open 15-Point Property Tracker &#8599;
          </a>
          <button
            type="button"
            class="inspector-permit-watch-btn secondary"
            data-report-site-activity="${alert.hcad_num || ""}"
            data-report-address="${String(alert.address || "").replace(/"/g, "&quot;")}"
            data-report-badge="${String(alert.badge || "Active Permit Watch").replace(/"/g, "&quot;")}"
            title="Report on-site observations or preservation updates directly to Preservation Houston"
          >
            &#9993; Report Site Activity to PH
          </button>
        </div>
      </div>
    `;
  }

  openPermitWatchModal(filterSeverity = "all", expandBenchmarks = false) {
    const modal = document.getElementById("permit-watch-modal");
    if (!modal) return;
    this._syncPermitWatchCounts();
    modal.classList.remove("hidden");

    const benchDetails = document.getElementById("pw-annual-benchmarks-details");
    if (benchDetails && expandBenchmarks) {
      benchDetails.open = true;
      setTimeout(() => {
        benchDetails.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }, 60);
    }

    modal.querySelectorAll("[data-pw-filter]").forEach((btn) => {
      const sev = btn.getAttribute("data-pw-filter") || "all";
      btn.classList.toggle("active", sev === filterSeverity);
    });

    this._renderPermitWatchModalList(filterSeverity);
  }

  _renderPermitWatchModalList(filterSeverity = "all") {
    const listEl = document.getElementById("permit-watch-modal-list");
    if (!listEl) return;

    const payload = this.mapController?.getPermitAlertsPayload?.();
    const allAlerts = Array.isArray(payload?.alerts) ? payload.alerts : [];
    const filtered = allAlerts.filter((al) => {
      if (filterSeverity === "critical") return al.severity === "critical";
      if (filterSeverity === "watch") return al.severity !== "critical";
      return true;
    });

    if (filtered.length === 0) {
      listEl.innerHTML = `<div style="padding:18px;color:var(--text-secondary);">No flagged structures match this filter.</div>`;
      return;
    }

    listEl.innerHTML = filtered
      .map((al) => {
        const isCritical = al.severity === "critical";
        const yrText =
          Number(al.year_built) > 1800
            ? `Built ${al.year_built}${al.yr_remodel ? ` (Remod ${al.yr_remodel})` : ""}`
            : "Historic Parcel";
        const cohList = Array.isArray(al.coh_permits) ? al.coh_permits : [];
        const dshsList = Array.isArray(al.dshs_notifications) ? al.dshs_notifications : [];
        const locParts = [
          al.neighborhood || "",
          al.historic_district &&
          al.historic_district !== "Outside City District" &&
          al.historic_district !== "Outside Historic District"
            ? al.historic_district
            : "",
          al.style || "",
          al.architect ? `Architect: ${al.architect}` : "",
        ].filter(Boolean);

        const cohSummaryHtml = cohList
          .map(
            (cp) => `
            <div class="pw-item-filing coh">
              <span class="pw-filing-pill coh">COH #${cp.project_no} · ${cp.date}</span>
              <span><strong>${cp.permit_type_label || cp.permit_type}:</strong> ${cp.use_desc || cp.permit_desc}${
              cp.buyer ? ` · Applicant: ${cp.buyer}` : ""
            }</span>
            </div>`
          )
          .join("");

        const dshsSummaryHtml = dshsList
          .map(
            (dn) => `
            <div class="pw-item-filing dshs">
              <span class="pw-filing-pill dshs">TX DSHS #${dn.notification_number} · ${dn.start_date} &#8594; ${dn.end_date}</span>
              <span><strong>${dn.work_type || "Asbestos Abatement"}:</strong> ${
              dn.work_description || "RACM Abatement"
            }${dn.racm_sqft ? ` (${Number(dn.racm_sqft).toLocaleString()} sq ft)` : ""}${
              dn.abatement_contractor ? ` · Operator: ${dn.abatement_contractor}` : ""
            }${dn.facility_owner ? ` · Owner: ${dn.facility_owner}` : ""}</span>
            </div>`
          )
          .join("");

        const multiList = Array.isArray(al.multi_agency_filings) ? al.multi_agency_filings : [];
        const multiSummaryHtml = multiList
          .map(
            (mf) => `
            <div class="pw-item-filing" style="border-left:3px solid #A78BFA;background:rgba(139,92,246,0.11);">
              <span class="pw-filing-pill" style="background:rgba(139,92,246,0.28);color:#DDD6FE;">${mf.agency || "State/County"} · ${mf.filing_id || ""} · ${mf.date || ""}</span>
              <span><strong>${mf.status || "Filing"} (${mf.address_used || al.address || ""}):</strong> ${mf.title || ""}${mf.details ? ` · ${mf.details}` : ""}</span>
            </div>`
          )
          .join("");

        return `
          <div class="permit-watch-item-card ${isCritical ? "critical" : "watch"}">
            <div class="pw-item-top">
              <div class="pw-item-title-group">
                <div class="pw-item-badges">
                  <span class="pw-severity-badge ${isCritical ? "critical" : "watch"}">
                    ${isCritical ? "&#128680; Critical Threat" : "&#9888; Watch"} · ${al.badge || "Active Filing"}
                  </span>
                  <span class="pw-year-badge mono">${yrText}</span>
                  ${
                    al.hcad_num
                      ? `<span class="pw-hcad-badge mono">HCAD ${al.hcad_num}</span>`
                      : ""
                  }
                </div>
                <h3 class="pw-item-title">${
                  al.building_name && al.building_name !== al.address
                    ? `${al.building_name} <span class="pw-item-addr-inline">(${al.address})</span>`
                    : al.address
                }</h3>
                ${
                  locParts.length > 0
                    ? `<div class="pw-item-loc-line">${locParts.join(" · ")}</div>`
                    : ""
                }
              </div>
              <div class="pw-item-actions" style="display:flex;gap:6px;flex-wrap:wrap;">
                <a
                  href="plat-monitor.html?tab=watchlist&hcad=${encodeURIComponent(al.hcad_num || "")}&address=${encodeURIComponent(al.address || "")}&name=${encodeURIComponent(al.building_name || "")}"
                  target="_blank"
                  rel="noopener"
                  class="action-pill"
                  style="text-decoration:none;border-color:rgba(167,139,250,0.45);color:#DDD6FE;"
                >
                  &#128203; 15-Point Tracker &#8599;
                </a>
                <button
                  type="button"
                  class="action-pill pw-inspect-btn"
                  data-pw-inspect-hcad="${al.hcad_num || ""}"
                  data-pw-inspect-id="${al.building_id || ""}"
                  data-pw-lon="${al.lon}"
                  data-pw-lat="${al.lat}"
                >
                  &#128269; Inspect on Map
                </button>
              </div>
            </div>

            ${
              al.preservation_note
                ? `<div class="pw-item-narrative">${al.preservation_note}</div>`
                : ""
            }

            <div class="pw-item-filings-list">
              ${cohSummaryHtml}
              ${dshsSummaryHtml}
              ${multiSummaryHtml}
            </div>
          </div>
        `;
      })
      .join("");

    listEl.querySelectorAll("[data-pw-inspect-hcad]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const hcad = btn.getAttribute("data-pw-inspect-hcad") || "";
        const bldId = btn.getAttribute("data-pw-inspect-id") || "";
        const lon = parseFloat(btn.getAttribute("data-pw-lon"));
        const lat = parseFloat(btn.getAttribute("data-pw-lat"));
        const modal = document.getElementById("permit-watch-modal");
        if (modal) modal.classList.add("hidden");

        if (!this.filterStore.getState().layers.permitWatch) {
          this.filterStore.setLayerVisibility("permitWatch", true);
        }

        if (this.mapController) {
          if (Number.isFinite(lon) && Number.isFinite(lat)) {
            this.mapController.flyToLocation({
              lng: lon,
              lat: lat,
              zoom: 17.6,
              hcadNum: hcad,
              featureId: bldId,
            });
          } else {
            this.mapController.selectFeatureByIdOrHcad({
              hcadNum: hcad,
              featureId: bldId,
              flyTo: true,
            });
          }
        }
      });
    });
  }

  _getSelectionUrlIds(selProps) {
    if (!selProps) {
      return { selectedHcad: "", selectedFeatureId: "" };
    }
    const rawBldId = String(selProps.building_id || selProps.id || "").trim();
    const selectedHcad = selProps.hcad_num ? String(selProps.hcad_num).trim() : "";
    const isMultiEntityParcel = Boolean(
      selectedHcad && this._multiEntityHcads && this._multiEntityHcads.has(selectedHcad)
    );
    if (rawBldId.includes("#") && (!rawBldId.endsWith("#b1") || isMultiEntityParcel)) {
      return { selectedHcad: "", selectedFeatureId: rawBldId };
    }
    const selectedFeatureId = !selectedHcad && selProps.id ? String(selProps.id).trim() : "";
    return { selectedHcad, selectedFeatureId };
  }

  _computeHistorySignature(state) {
    const selProps = this.mapController ? this.mapController.selectedFeatureProps : null;
    const { selectedHcad, selectedFeatureId } = this._getSelectionUrlIds(selProps);
    const selBnd = this.mapController?.selectedBoundaryFeature?.properties || null;
    const bndLayer = selBnd?.overlay_layer || "neighborhoods";
    const bndTarget = selBnd?.id || selBnd?.name || selBnd?.era_label || "";
    const iso = state?.isolatedBoundary;
    const isoSig =
      iso && (iso.id || iso.name)
        ? `iso:${iso.layerKey || "neighborhoods"}:${iso.id || iso.name}:${iso.mode || "contents"}`
        : "";
    const tourSig = this._activeTour
      ? `tour:${this._activeTour.id}:${this._activeTourStopIndex}`
      : "";
    const chapterSig = this._activeChapterId ? `chapter:${this._activeChapterId}` : "";
    const portfolioSig = this._activePortfolio
      ? `portfolio:${this._activePortfolio.kind}:${this._activePortfolio.query}`
      : "";
    return [
      selectedHcad ? `hcad:${selectedHcad}` : "",
      selectedFeatureId ? `id:${selectedFeatureId}` : "",
      !selectedHcad && !selectedFeatureId && bndTarget ? `bnd:${bndLayer}:${bndTarget}` : "",
      isoSig,
      tourSig,
      chapterSig,
      portfolioSig,
    ]
      .filter(Boolean)
      .join("|");
  }

  _updateUrlHash(state, { push = false } = {}) {
    if (this._suppressUrlUpdate) return;
    const vp = this.mapController
      ? this.mapController.getCurrentViewport()
      : this.lastViewportStats
      ? this.lastViewportStats.viewport
      : null;
    const selProps = this.mapController ? this.mapController.selectedFeatureProps : null;
    const { selectedHcad, selectedFeatureId } = this._getSelectionUrlIds(selProps);
    const selBnd = this.mapController?.selectedBoundaryFeature?.properties || null;
    const selectedBoundary =
      !selectedHcad && !selectedFeatureId && selBnd && (selBnd.id || selBnd.name || selBnd.era_label)
        ? {
            layerKey: selBnd.overlay_layer || "neighborhoods",
            id: selBnd.id || selBnd.name || selBnd.era_label || "",
          }
        : null;
    const activePortfolioSpec = this._activePortfolio
      ? { kind: this._activePortfolio.kind, query: this._activePortfolio.query }
      : null;

    let hash = serializeStateToHash(state, vp, {
      includeViewport: true,
      selectedHcad,
      selectedFeatureId,
      selectedBoundary,
      activeChapterId: this._activeChapterId || "",
      activePortfolio: activePortfolioSpec,
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
    const targetUrl = hash ? `${basePath}#${hash}` : basePath;
    const curPathAndHash = `${window.location.pathname}${window.location.hash}`;
    const navSig = this._computeHistorySignature(state);

    try {
      if (this._lastHistorySignature === null) {
        this._lastHistorySignature = navSig;
        if (targetUrl !== curPathAndHash) {
          window.history.replaceState({ navSig }, "", targetUrl);
        }
      } else if (!this._isRestoringHistory && (push || navSig !== this._lastHistorySignature)) {
        this._lastHistorySignature = navSig;
        window.history.pushState({ navSig }, "", targetUrl);
      } else if (targetUrl !== curPathAndHash) {
        window.history.replaceState({ navSig }, "", targetUrl);
      }
    } catch (_e) {
      // Guard against browser history.replaceState/pushState flood throttling during rapid scrubbing
    }
    this._lastHandledUrlKey = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  }

  /* ========================================================================
     Priority #4A: Guided Citywide Visual Chapters ("How Houston Was Built")
     ======================================================================== */
  activateCityChapter(chapterId, { fitViewport = true, skipHistoryPush = false } = {}) {
    const chapter =
      CITY_HISTORY_CHAPTERS.find((c) => c.id === chapterId) || CITY_HISTORY_CHAPTERS[0];
    if (!chapter) return;

    if (this._activeTour) {
      this._exitActiveTour({ restoreViewport: false });
    }
    if (this._activePortfolio) {
      this.exitPortfolioMode({ restoreState: false, skipUrlUpdate: true });
    }

    if (!this._activeChapterId && !this._preChapterSnapshot) {
      const curState = this.filterStore.getState();
      this._preChapterSnapshot = {
        state: {
          ...curState,
          layers: { ...(curState.layers || {}) },
        },
        viewport: this.mapController ? this.mapController.getCurrentViewport() : null,
      };
    }

    this._activeChapterId = chapter.id;

    if (typeof window !== "undefined" && window.innerWidth <= 900) {
      this._setSidebarCollapsed(true);
    }

    const curState = this.filterStore.getState();
    if (curState.isolatedBoundary && this.mapController) {
      this.mapController.clearIsolatedBoundary();
    }
    const nextPatch = {
      ...(chapter.statePatch || {}),
      isolatedBoundary: null,
      layers: {
        ...(curState.layers || {}),
        ...((chapter.statePatch && chapter.statePatch.layers) || {}),
      },
    };
    const prevSuppress = this._suppressUrlUpdate;
    this._suppressUrlUpdate = true;
    try {
      this.filterStore.setState(nextPatch);
    } finally {
      this._suppressUrlUpdate = prevSuppress;
    }

    const card = document.getElementById("city-chapters-card");
    const tabsEl = document.getElementById("city-chapters-step-tabs");
    const kickerEl = document.getElementById("city-chapter-kicker");
    const eraEl = document.getElementById("city-chapter-era");
    const titleEl = document.getElementById("city-chapter-title");
    const narrativeEl = document.getElementById("city-chapter-narrative");
    const takeawayEl = document.getElementById("city-chapter-takeaway");
    const sitesEl = document.getElementById("city-chapter-sites");
    const btnLaunch = document.getElementById("btn-open-city-chapters");

    if (btnLaunch) btnLaunch.classList.add("active");
    if (card) card.classList.remove("hidden");
    if (kickerEl) kickerEl.textContent = chapter.kicker || `Chapter ${chapter.chapterNum}`;
    if (eraEl) eraEl.textContent = chapter.era || "";
    if (titleEl) titleEl.textContent = chapter.title || "";
    if (narrativeEl) narrativeEl.textContent = chapter.narrative || "";
    if (takeawayEl) {
      takeawayEl.innerHTML = `<strong>Map Lens Tip:</strong> ${chapter.keyTakeaway || ""}`;
    }
    this._renderNarrativeCitationsBar(document.getElementById("city-chapter-citations"), {
      citations: chapter.citations || [],
      contextId: `chapter_${chapter.id}`,
      contextTitle: `${chapter.kicker || `Chapter ${chapter.chapterNum}`}: ${chapter.title}`,
      excerpt: chapter.narrative || "",
    });

    if (tabsEl) {
      tabsEl.innerHTML = CITY_HISTORY_CHAPTERS.map(
        (c) =>
          `<button
            type="button"
            class="city-chapter-tab-btn mono${c.id === chapter.id ? " active" : ""}"
            role="tab"
            aria-selected="${c.id === chapter.id ? "true" : "false"}"
            data-chapter-tab="${c.id}"
            title="${c.kicker}: ${c.title.replace(/"/g, "&quot;")} (${c.era})"
          >${c.chapterNum}</button>`
      ).join("");
      tabsEl.querySelectorAll("[data-chapter-tab]").forEach((tBtn) => {
        tBtn.addEventListener("click", () => {
          const targetId = tBtn.getAttribute("data-chapter-tab") || "";
          if (targetId) this.activateCityChapter(targetId);
        });
      });
    }

    if (sitesEl) {
      const sites = Array.isArray(chapter.sites) ? chapter.sites : [];
      sitesEl.innerHTML = sites
        .map(
          (s, idx) =>
            `<button
              type="button"
              class="city-chapter-site-btn"
              data-chapter-site-idx="${idx}"
              title="Fly to and inspect ${s.label.replace(/"/g, "&quot;")}"
            >&#128205; ${s.label}</button>`
        )
        .join("");
      sitesEl.querySelectorAll("[data-chapter-site-idx]").forEach((sBtn) => {
        sBtn.addEventListener("click", () => {
          const sIdx = parseInt(sBtn.getAttribute("data-chapter-site-idx"), 10) || 0;
          const site = sites[sIdx];
          if (!site || !this.mapController) return;
          if (site.boundaryLayer && site.boundaryName) {
            const normLayer =
              site.boundaryLayer === "holc1937"
                ? "holc_1937_zones"
                : site.boundaryLayer === "historicDistricts"
                ? "historic_districts"
                : site.boundaryLayer === "heritageDistricts"
                ? "heritage_districts"
                : site.boundaryLayer === "historicWards"
                ? "historic_wards"
                : site.boundaryLayer;
            this.mapController.highlightBoundaryByIdOrName({
              layerKey: normLayer,
              id: site.boundaryName,
              name: site.boundaryName,
              flyTo: true,
              inspect: true,
            });
          } else {
            this.mapController.clearHighlightedBoundary();
            this.mapController.selectFeatureByIdOrHcad({
              hcadNum: site.hcadNum || "",
              featureId: site.featureId || "",
              targetLng: site.lng,
              targetLat: site.lat,
              flyTo: false,
            });
            if (Number.isFinite(site.lng) && Number.isFinite(site.lat)) {
              this.mapController.flyToLocation({
                lng: site.lng,
                lat: site.lat,
                zoom: site.zoom || 17.0,
                pitch: 0,
                hcadNum: site.hcadNum || "",
                featureId: site.featureId || "",
              });
            }
          }
        });
      });
    }

    if (fitViewport && chapter.viewport && this.mapController) {
      this.mapController.clearSelection();
      document.getElementById("inspector-drawer")?.classList.add("hidden");
      this.mapController.flyToLocation({
        lng: chapter.viewport.lng,
        lat: chapter.viewport.lat,
        zoom: chapter.viewport.zoom,
        pitch: chapter.viewport.pitch || 0,
      });
    }

    this._updateUrlHash(this.filterStore.getState(), { push: !skipHistoryPush });
    this._refreshShareModalContent();
  }

  exitCityChapters({ restoreState = true, skipUrlUpdate = false } = {}) {
    this._activeChapterId = "";
    const card = document.getElementById("city-chapters-card");
    if (card) card.classList.add("hidden");
    const btnLaunch = document.getElementById("btn-open-city-chapters");
    if (btnLaunch) btnLaunch.classList.remove("active");

    const snap = this._preChapterSnapshot;
    this._preChapterSnapshot = null;

    if (restoreState && snap && snap.state) {
      const prevSuppress = this._suppressUrlUpdate;
      this._suppressUrlUpdate = true;
      try {
        this.filterStore.setState(snap.state);
      } finally {
        this._suppressUrlUpdate = prevSuppress;
      }
      if (snap.viewport && this.mapController) {
        this.mapController.flyToLocation({
          lng: snap.viewport.lng,
          lat: snap.viewport.lat,
          zoom: snap.viewport.zoom,
          pitch: snap.viewport.pitch || 0,
        });
      }
    }

    if (!skipUrlUpdate) {
      this._updateUrlHash(this.filterStore.getState());
      this._refreshShareModalContent();
    }
  }

  /* ========================================================================
     Priority #4B: Architect, Master Builder & Architectural Style Portfolios
     ======================================================================== */
  _populatePortfolioPresetSelect(customSpec = null) {
    const sel = document.getElementById("portfolio-preset-select");
    if (!sel) return;
    const archPresets = FEATURED_PORTFOLIO_PRESETS.filter((p) => p.kind !== "style");
    const stylePresets = FEATURED_PORTFOLIO_PRESETS.filter((p) => p.kind === "style");
    const customVal =
      customSpec && customSpec.query ? `${customSpec.kind}:${customSpec.query}` : "";
    const isPreset = FEATURED_PORTFOLIO_PRESETS.some(
      (p) => `${p.kind}:${p.query}`.toLowerCase() === customVal.toLowerCase()
    );

    const customOptHtml =
      customVal && !isPreset
        ? `<optgroup label="Selected Portfolio">
            <option value="${customVal.replace(/"/g, "&quot;")}">${
            customSpec.kind === "style" ? `Style: ${customSpec.query}` : customSpec.query
          }</option>
          </optgroup>`
        : "";

    sel.innerHTML = `
      ${customOptHtml}
      <optgroup label="Master Architects &amp; Builders">
        ${archPresets
          .map(
            (p) =>
              `<option value="${p.kind}:${p.query.replace(/"/g, "&quot;")}">${p.label}</option>`
          )
          .join("")}
      </optgroup>
      <optgroup label="Architectural Styles">
        ${stylePresets
          .map(
            (p) =>
              `<option value="${p.kind}:${p.query.replace(/"/g, "&quot;")}">${p.label}</option>`
          )
          .join("")}
      </optgroup>
    `;

    if (customVal) {
      const matchOpt = Array.from(sel.options).find(
        (o) => o.value.toLowerCase() === customVal.toLowerCase()
      );
      if (matchOpt) sel.value = matchOpt.value;
    }
  }

  activatePortfolioMode(
    kind = "architect",
    query = "",
    { fitBounds = true, skipHistoryPush = false } = {}
  ) {
    const cleanKind = kind === "style" ? "style" : "architect";
    const cleanQuery = String(query || "").trim();
    if (!cleanQuery) return;

    if (this._activeTour) {
      this._exitActiveTour({ restoreViewport: false });
    }
    if (this._activeChapterId) {
      this.exitCityChapters({ restoreState: false, skipUrlUpdate: true });
    }

    if (!this._activePortfolio && !this._prePortfolioSnapshot) {
      const curState = this.filterStore.getState();
      this._prePortfolioSnapshot = {
        state: {
          ...curState,
          layers: { ...(curState.layers || {}) },
        },
        viewport: this.mapController ? this.mapController.getCurrentViewport() : null,
      };
    }

    if (typeof window !== "undefined" && window.innerWidth <= 900) {
      this._setSidebarCollapsed(true);
    }

    const dataset = buildPortfolioDataset({
      kind: cleanKind,
      query: cleanQuery,
      searchIndex: this.searchIndex,
      overlaysData: this.mapController ? this.mapController.overlaysData : null,
      curatedOverrides: this.mapController ? this.mapController.curatedOverrides : null,
    });

    this._activePortfolio = {
      ...dataset,
      subFilter: "all",
      activeIndex: -1,
      filteredWorks: dataset.works,
    };

    this._populatePortfolioPresetSelect({ kind: cleanKind, query: dataset.query });

    // Ensure full year range & Lost Houston layer visibility when demolished works are present
    const curState = this.filterStore.getState();
    const statePatch = {
      minYear: 1836,
      maxYear: 2026,
      selectedDecade: "all",
    };
    if (dataset.stats.demolishedCount > 0 && !curState.layers?.lostHouston) {
      statePatch.layers = {
        ...(curState.layers || {}),
        lostHouston: true,
      };
    }
    const prevSuppress = this._suppressUrlUpdate;
    this._suppressUrlUpdate = true;
    try {
      this.filterStore.setState(statePatch);
    } finally {
      this._suppressUrlUpdate = prevSuppress;
    }

    const card = document.getElementById("portfolio-monograph-card");
    const btnQuick = document.getElementById("btn-open-portfolios-quick");
    const titleEl = document.getElementById("portfolio-card-title");
    const lifeEl = document.getElementById("portfolio-card-lifespan");
    const roleEl = document.getElementById("portfolio-card-role");
    const bioEl = document.getElementById("portfolio-card-bio");
    const statExtant = document.getElementById("portfolio-stat-extant");
    const statLost = document.getElementById("portfolio-stat-lost");
    const statSpan = document.getElementById("portfolio-stat-span");
    const statHonors = document.getElementById("portfolio-stat-honors");
    const pillAll = document.getElementById("portfolio-pill-all");
    const pillExtant = document.getElementById("portfolio-pill-extant");
    const pillLost = document.getElementById("portfolio-pill-lost");

    const bio = dataset.biography || {};
    const st = dataset.stats;
    if (titleEl) titleEl.textContent = bio.title || dataset.query;
    if (lifeEl) {
      lifeEl.textContent = `${st.totalCount} Mapped Work${st.totalCount === 1 ? "" : "s"}`;
    }
    if (roleEl) roleEl.textContent = bio.role || "";
    if (bioEl) bioEl.textContent = bio.summary || "";
    this._renderNarrativeCitationsBar(document.getElementById("portfolio-card-citations"), {
      citations: bio.citations || [],
      contextId: `portfolio_${cleanKind}_${dataset.query}`,
      contextTitle: `Portfolio: ${bio.title || dataset.query}`,
      excerpt: bio.summary || "",
    });
    if (statExtant) statExtant.textContent = String(st.extantCount);
    if (statLost) statLost.textContent = String(st.demolishedCount);
    if (statSpan) {
      statSpan.textContent =
        st.earliestYear && st.latestYear
          ? st.earliestYear === st.latestYear
            ? String(st.earliestYear)
            : `${st.earliestYear}-${st.latestYear}`
          : "-";
    }
    if (statHonors) {
      statHonors.textContent = `${st.landmarkCount} LM · ${st.goodBrickCount} GB`;
    }
    if (pillAll) pillAll.textContent = String(st.totalCount);
    if (pillExtant) pillExtant.textContent = String(st.extantCount);
    if (pillLost) pillLost.textContent = String(st.demolishedCount);

    document.querySelectorAll("[data-portfolio-filter]").forEach((b) => {
      b.classList.toggle("active", b.getAttribute("data-portfolio-filter") === "all");
    });

    if (btnQuick) btnQuick.classList.add("active");
    if (card) card.classList.remove("hidden");

    this._renderPortfolioWorksList({ fitBounds });
    this._updateUrlHash(this.filterStore.getState(), { push: !skipHistoryPush });
    this._refreshShareModalContent();
  }

  _renderPortfolioWorksList({ fitBounds = false } = {}) {
    const p = this._activePortfolio;
    const listEl = document.getElementById("portfolio-works-list");
    if (!p || !listEl) return;

    const filtered = (p.works || []).filter((w) => {
      if (p.subFilter === "extant") return !w.isDemolished;
      if (p.subFilter === "lost") return Boolean(w.isDemolished);
      return true;
    });
    p.filteredWorks = filtered;

    if (this.mapController) {
      this.mapController.setPortfolioWorks(filtered, p.activeIndex ?? -1, {
        fit: Boolean(fitBounds && filtered.length > 0),
      });
    }

    if (filtered.length === 0) {
      listEl.innerHTML = `<div style="padding:12px;font-size:11.5px;color:#94a3b8;">No works match this sub-filter.</div>`;
      return;
    }

    // For large style portfolios (>250 works), cap DOM list at 200 chronological works while keeping all points on WebGL map
    const displayWorks = filtered.slice(0, 200);
    const moreCount = filtered.length - displayWorks.length;

    listEl.innerHTML =
      displayWorks
        .map((w, idx) => {
          const isLost = Boolean(w.isDemolished);
          const isActive = idx === p.activeIndex;
          const yrLabel = isLost
            ? `${w.yearBuilt || "?"}-${w.yearDemolished || "Demolished"}`
            : w.yearBuilt >= 1800
            ? String(w.yearBuilt)
            : "Undated";
          const subParts = [
            w.address && w.address !== w.title ? w.address : "",
            w.district || "",
            p.kind === "style" && w.architect ? w.architect : w.style || "",
            isLost ? "Lost Houston Archive" : w.isLandmark ? "COH Landmark" : "",
          ].filter(Boolean);
          return `
            <button
              type="button"
              class="portfolio-work-row${isLost ? " is-lost" : ""}${isActive ? " active" : ""}"
              data-portfolio-work-idx="${idx}"
            >
              <span class="portfolio-work-num mono">${isLost ? `&#8224;${idx + 1}` : idx + 1}</span>
              <span class="portfolio-work-main">
                <span class="portfolio-work-title-line">
                  <span class="portfolio-work-title">${w.title}</span>
                  <span class="portfolio-work-year mono">${yrLabel}</span>
                </span>
                <span class="portfolio-work-sub">${subParts.join(" · ") || "Harris County"}</span>
              </span>
            </button>
          `;
        })
        .join("") +
      (moreCount > 0
        ? `<div style="padding:8px 10px;font-size:11px;color:#94a3b8;text-align:center;">All ${filtered.length.toLocaleString()} structures are lit on the map (${displayWorks.length} earliest chronological works listed above).</div>`
        : "");

    listEl.querySelectorAll("[data-portfolio-work-idx]").forEach((rowBtn) => {
      rowBtn.addEventListener("click", () => {
        const idx = parseInt(rowBtn.getAttribute("data-portfolio-work-idx"), 10);
        if (Number.isFinite(idx)) {
          this._selectPortfolioWorkByIndex(idx);
        }
      });
    });
  }

  _selectPortfolioWorkByIndex(idx) {
    const p = this._activePortfolio;
    if (!p || !Array.isArray(p.filteredWorks)) return;
    const work = p.filteredWorks[idx];
    if (!work || !this.mapController) return;

    p.activeIndex = idx;
    this.mapController.setPortfolioWorks(p.filteredWorks, idx, { fit: false });

    const listEl = document.getElementById("portfolio-works-list");
    if (listEl) {
      listEl.querySelectorAll("[data-portfolio-work-idx]").forEach((btn) => {
        const bIdx = parseInt(btn.getAttribute("data-portfolio-work-idx"), 10);
        const active = bIdx === idx;
        btn.classList.toggle("active", active);
        if (active && typeof btn.scrollIntoView === "function") {
          btn.scrollIntoView({ block: "nearest", behavior: "smooth" });
        }
      });
    }

    if (work.isDemolished) {
      if (!this.filterStore.getState().layers?.lostHouston) {
        this.filterStore.setLayerVisibility("lostHouston", true);
      }
      this.mapController.highlightBoundaryByIdOrName({
        layerKey: "lost_houston",
        id: work.lostHoustonId || work.featureId || work.title,
        name: work.title,
        flyTo: true,
        inspect: true,
      });
    } else {
      this.mapController.clearHighlightedBoundary();
      this.mapController.selectFeatureByIdOrHcad({
        hcadNum: work.hcadNum || "",
        featureId: work.featureId || "",
        addressQuery: work.address || "",
        fallbackYearBuilt: work.yearBuilt || 0,
        targetLng: work.lng,
        targetLat: work.lat,
        flyTo: false,
      });
      this.mapController.flyToLocation({
        lng: work.lng,
        lat: work.lat,
        zoom: 17.3,
        hcadNum: work.hcadNum || "",
        featureId: work.featureId || "",
      });
    }
  }

  exitPortfolioMode({ restoreState = true, skipUrlUpdate = false } = {}) {
    this._activePortfolio = null;
    if (this.mapController) {
      this.mapController.clearPortfolioWorks();
    }
    const card = document.getElementById("portfolio-monograph-card");
    if (card) card.classList.add("hidden");
    const btnQuick = document.getElementById("btn-open-portfolios-quick");
    if (btnQuick) btnQuick.classList.remove("active");

    const snap = this._prePortfolioSnapshot;
    this._prePortfolioSnapshot = null;

    if (restoreState && snap && snap.state) {
      const prevSuppress = this._suppressUrlUpdate;
      this._suppressUrlUpdate = true;
      try {
        this.filterStore.setState(snap.state);
      } finally {
        this._suppressUrlUpdate = prevSuppress;
      }
      if (snap.viewport && this.mapController) {
        this.mapController.flyToLocation({
          lng: snap.viewport.lng,
          lat: snap.viewport.lat,
          zoom: snap.viewport.zoom,
          pitch: snap.viewport.pitch || 0,
        });
      }
    }

    if (!skipUrlUpdate) {
      this._updateUrlHash(this.filterStore.getState());
      this._refreshShareModalContent();
    }
  }

  /* ========================================================================
     Priority #5: Monthly HAHC Agenda, COA & Designation Precedent Tracker
     ======================================================================== */
  _syncHahcCounts() {
    const payload = this.mapController?.getHahcTrackerPayload?.();
    if (!payload) return;
    const summary = payload.summary || {};
    const cycle = payload.current_cycle || {};
    const uniqueParcels = Number(summary.unique_parcels || this.mapController.getHahcFeatureCount() || 283);
    const totalFilings = Number(summary.total_filings || (payload.filings || []).length || 403);
    const activeAgendaParcels = Number(summary.active_agenda_parcels || 13);
    const activeItemsCount = Number(cycle.active_items_count || 15);
    const approvedCombined =
      Number(summary.approved_filings || 0) + Number(summary.approved_conditions_filings || 0);
    const deniedAndDemo =
      Number(summary.denied_or_cor_filings || 0) + Number(summary.demolition_relocation_filings || 0);

    const layerCountEl = document.getElementById("hahc-layer-count");
    if (layerCountEl) layerCountEl.textContent = String(uniqueParcels);
    const btnShowMap = document.getElementById("btn-hahc-enable-map-layer");
    if (btnShowMap) {
      btnShowMap.innerHTML = `&#127758; Show All ${uniqueParcels} Parcels on Map`;
    }

    const setElText = (id, val) => {
      const el = document.getElementById(id);
      if (el) el.textContent = String(val);
    };
    setElText("hahc-stat-active", activeAgendaParcels);
    setElText("hahc-stat-total", totalFilings);
    setElText("hahc-stat-approved", approvedCombined);
    setElText("hahc-stat-denied", deniedAndDemo);

    setElText("hahc-mpill-all", totalFilings);
    setElText("hahc-mpill-active", activeItemsCount);
    setElText("hahc-mpill-add", Number(summary.addition_alteration_filings || 0));
    setElText("hahc-mpill-new", Number(summary.new_construction_filings || 0));
    setElText("hahc-mpill-demo", Number(summary.demolition_relocation_filings || 0));
    setElText("hahc-mpill-desig", Number(summary.designation_filings || 0));
    setElText("hahc-mpill-denied", Number(summary.denied_or_cor_filings || 0));

    const bannerEl = document.getElementById("hahc-active-cycle-banner");
    if (bannerEl) {
      const guidelines = Array.isArray(cycle.guideline_updates) ? cycle.guideline_updates : [];
      const guideBtns = guidelines
        .filter((g) => g && g.pdf_url)
        .map(
          (g) =>
            `<a href="${g.pdf_url}" target="_blank" rel="noopener noreferrer" class="action-pill" style="text-decoration:none;border-color:rgba(251,191,36,0.5);background:rgba(245,158,11,0.18);color:#FEF3C7;">&#128214; ${
              g.title || "Design Guidelines Redline Draft"
            } (PDF) &#8599;</a>`
        )
        .join("");
      bannerEl.innerHTML = `
        <div>
          <strong style="color:#FFE4E6;">&#128340; October 8, 2026 HAHC Public Hearing Docket:</strong>
          <span>${activeItemsCount} COA &amp; Landmark Designation items (${activeAgendaParcels} geocoded parcels) with individual City of Houston Planning Staff Report PDFs, plus ${Number(
        summary.meetings_parsed || 29
      )} monthly HAHC meetings (March 2024 - October 2026).</span>
        </div>
        <div class="hahc-cycle-links">
          ${
            cycle.agenda_pdf_url
              ? `<a href="${cycle.agenda_pdf_url}" target="_blank" rel="noopener noreferrer" class="action-pill" style="text-decoration:none;border-color:rgba(251,113,133,0.55);background:rgba(244,63,94,0.22);color:#FFE4E6;">&#128196; Oct 8, 2026 Full Agenda PDF &#8599;</a>`
              : ""
          }
          ${guideBtns}
        </div>
      `;
    }

    const distSelect = document.getElementById("hahc-modal-district-select");
    if (distSelect && payload.district_precedents) {
      const curVal = distSelect.value || "all";
      const distList = Object.values(payload.district_precedents).sort(
        (a, b) => Number(b.total_filings || 0) - Number(a.total_filings || 0)
      );
      distSelect.innerHTML =
        `<option value="all">All COH Historic Districts &amp; Landmarks (${totalFilings})</option>` +
        distList
          .map(
            (d) =>
              `<option value="${String(d.historic_district).replace(/"/g, "&quot;")}">${
                d.historic_district
              } (${d.total_filings} COAs)</option>`
          )
          .join("") +
        `<option value="__landmarks_only__">Individual Landmarks Outside District</option>`;
      if (Array.from(distSelect.options).some((o) => o.value === curVal)) {
        distSelect.value = curVal;
      }
    }
  }

  _formatHahcOutcomeBadge(outcome, outcomeDetail, isActiveAgenda) {
    if (isActiveAgenda || outcome === "pending_agenda") {
      return {
        cls: "pending_agenda",
        label: "Oct 8, 2026 Active Agenda",
      };
    }
    if (outcome === "approved_with_conditions") {
      return {
        cls: "approved_with_conditions",
        label: outcomeDetail || "Approved w/ Conditions",
      };
    }
    if (outcome === "denied_or_cor") {
      return {
        cls: "denied_or_cor",
        label: outcomeDetail || "Denied / COR",
      };
    }
    if (outcome === "deferred") {
      return {
        cls: "deferred",
        label: outcomeDetail || "Deferred",
      };
    }
    return {
      cls: "approved",
      label: outcomeDetail || "Approved",
    };
  }

  _buildHahcParcelCardHtml(filings, historicDistrict = "") {
    if (!Array.isArray(filings) || filings.length === 0) return "";
    const hasActive = filings.some((f) => Boolean(f.is_active_agenda));
    const distClean = String(historicDistrict || filings[0]?.historic_district || "").trim();

    const rowsHtml = filings
      .map((f) => {
        const b = this._formatHahcOutcomeBadge(f.outcome, f.outcome_detail, f.is_active_agenda);
        const pdfLinks = [];
        if (f.staff_report_url) {
          pdfLinks.push(
            `<a href="${f.staff_report_url}" target="_blank" rel="noopener noreferrer" style="color:#5EEAD4;font-weight:700;text-decoration:underline;">Staff Report PDF &#8599;</a>`
          );
        }
        if (f.agenda_or_minutes_url) {
          pdfLinks.push(
            `<a href="${f.agenda_or_minutes_url}" target="_blank" rel="noopener noreferrer" style="color:#BAE6FD;font-weight:600;text-decoration:underline;">${
              f.is_active_agenda ? "Oct 8 Agenda PDF" : "Action Minutes PDF"
            } &#8599;</a>`
          );
        }
        return `
          <div style="padding:7px 9px;border-radius:7px;background:rgba(15,23,42,0.68);border:1px solid rgba(255,255,255,0.1);display:flex;flex-direction:column;gap:4px;">
            <div style="display:flex;align-items:center;justify-content:space-between;gap:6px;flex-wrap:wrap;">
              <span class="mono" style="font-size:11px;font-weight:700;color:#E2E8F0;">${
                f.meeting_date || ""
              }${f.item_num ? ` · Item ${f.item_num}` : ""}</span>
              <span class="hahc-outcome-badge ${b.cls}">${b.label}</span>
            </div>
            <div style="font-size:12px;font-weight:700;color:#FFFFFF;">${
              f.app_type || "Certificate of Appropriateness"
            }${f.landmark_name ? ` (${f.landmark_name})` : ""}</div>
            ${
              pdfLinks.length > 0
                ? `<div style="display:flex;gap:10px;flex-wrap:wrap;font-size:11px;">${pdfLinks.join(
                    ""
                  )}</div>`
                : ""
            }
          </div>
        `;
      })
      .join("");

    return `
      <div class="hahc-docket-card${hasActive ? " has-active-agenda" : ""}">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;">
          <span style="font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:0.04em;color:${
            hasActive ? "#FDA4AF" : "#5EEAD4"
          };">
            &#127963; ${
              hasActive
                ? "Active HAHC Public Hearing &amp; Parcel COA Docket"
                : "HAHC Certificate of Appropriateness &amp; Landmark Docket"
            }
          </span>
          <span class="mono" style="font-size:10.5px;color:#CBD5E1;">${filings.length} Filing${
      filings.length === 1 ? "" : "s"
    } (2024-2026)</span>
        </div>
        <div style="display:flex;flex-direction:column;gap:6px;">
          ${rowsHtml}
        </div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:2px;">
          <button
            type="button"
            class="inspector-permit-watch-btn secondary"
            data-open-hahc-modal="1"
            data-hahc-district="${distClean.replace(/"/g, "&quot;")}"
          >
            &#127963; Open Full HAHC COA Docket${distClean ? ` (${distClean})` : ""}
          </button>
        </div>
      </div>
    `;
  }

  _buildHahcDistrictPrecedentsCardHtml(historicDistrict, excludeHcad = "") {
    if (!historicDistrict || !this.mapController?.getHahcDistrictPrecedents) return "";
    const summary = this.mapController.getHahcDistrictPrecedents(historicDistrict);
    if (!summary || !summary.total_filings) return "";

    const distName = summary.historic_district;
    const approvedTotal =
      Number(summary.approved_count || 0) + Number(summary.approved_conditions_count || 0);
    const approvalPct =
      summary.total_filings > 0
        ? Math.round((approvedTotal / Math.max(1, summary.total_filings - (summary.active_agenda_count || 0))) * 100)
        : 0;

    const cleanExclude = String(excludeHcad || "").trim();
    const neighborExamples = (summary.recent_examples || [])
      .filter((ex) => !cleanExclude || String(ex.hcad_num || "").trim() !== cleanExclude)
      .slice(0, 4);

    const isNorhill = distName.toLowerCase().includes("norhill");
    const norhillGuidelineNote = isNorhill
      ? `<div style="padding:6px 8px;border-radius:6px;background:rgba(245,158,11,0.14);border:1px solid rgba(251,191,36,0.4);font-size:11px;color:#FEF3C7;">
          <strong>Oct 2026 Design Guidelines Update:</strong> HAHC published the <a href="https://www.houstontx.gov/planning/Commissions/hahc_100826/C_Norhill_Redline_Draft_Oct_26.pdf" target="_blank" rel="noopener noreferrer" style="color:#FDE047;text-decoration:underline;font-weight:700;">Norhill Historic District Design Guidelines Redline Draft (PDF) &#8599;</a> on the October 8, 2026 docket.
        </div>`
      : "";

    const examplesHtml = neighborExamples
      .map((ex) => {
        const b = this._formatHahcOutcomeBadge(ex.outcome, ex.outcome_detail, ex.is_active_agenda);
        return `
          <div class="hahc-neighbor-example-btn" style="cursor:default;">
            <div style="display:flex;align-items:center;justify-content:space-between;gap:6px;flex-wrap:wrap;">
              <strong style="font-size:11.5px;color:#FFFFFF;">${ex.address}</strong>
              <span class="hahc-outcome-badge ${b.cls}">${b.label}</span>
            </div>
            <div style="font-size:11px;color:#CBD5E1;">${ex.meeting_date} · ${ex.app_type}</div>
            <div style="display:flex;align-items:center;gap:10px;margin-top:2px;font-size:10.8px;">
              ${
                ex.hcad_num
                  ? `<button type="button" data-hahc-inspect-hcad="${ex.hcad_num}" style="appearance:none;background:none;border:0;padding:0;color:#5EEAD4;font-weight:700;cursor:pointer;text-decoration:underline;">Inspect Neighbor on Map &rarr;</button>`
                  : ""
              }
              ${
                ex.pdf_url
                  ? `<a href="${ex.pdf_url}" target="_blank" rel="noopener noreferrer" style="color:#BAE6FD;text-decoration:underline;">${
                      ex.is_active_agenda ? "Staff Report PDF" : "COA Record PDF"
                    } &#8599;</a>`
                  : ""
              }
            </div>
          </div>
        `;
      })
      .join("");

    return `
      <div class="hahc-district-precedents-card">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;">
          <span style="font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:0.04em;color:#5EEAD4;">
            &#127963; ${distName} HAHC COA Precedents (2024-2026)
          </span>
          <span class="mono" style="font-size:10.5px;color:#99F6E4;">${Math.min(100, approvalPct)}% Approved</span>
        </div>
        <div style="font-size:11.5px;color:#CBD5E1;line-height:1.42;">
          Exterior alterations, additions, new construction, and demolitions visible from a public street in <strong>${distName}</strong> require a Chapter 33 Certificate of Appropriateness (COA) from HAHC:
          <strong>${summary.addition_alteration_count || 0}</strong> additions/alterations,
          <strong>${summary.new_construction_count || 0}</strong> new construction, and
          <strong>${summary.demolition_relocation_count || 0}</strong> demolition/relocation filings reviewed since March 2024.
        </div>
        ${norhillGuidelineNote}
        <div class="hahc-precedent-stats-grid">
          <div class="hahc-prec-stat">
            <span class="hahc-prec-stat-val">${summary.total_filings}</span>
            <span class="hahc-prec-stat-lbl">District COAs</span>
          </div>
          <div class="hahc-prec-stat">
            <span class="hahc-prec-stat-val" style="color:#6EE7B7;">${approvedTotal}</span>
            <span class="hahc-prec-stat-lbl">Approved / Cond.</span>
          </div>
          <div class="hahc-prec-stat">
            <span class="hahc-prec-stat-val" style="color:#FCD34D;">${summary.denied_or_cor_count || 0}</span>
            <span class="hahc-prec-stat-lbl">Denied / COR</span>
          </div>
        </div>
        ${
          examplesHtml
            ? `<div style="display:flex;flex-direction:column;gap:5px;">
                <span style="font-size:10.2px;font-weight:700;text-transform:uppercase;letter-spacing:0.04em;color:#94A3B8;">Recent ${distName} Neighbor COA Precedents:</span>
                ${examplesHtml}
              </div>`
            : ""
        }
        <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:2px;">
          <button
            type="button"
            class="inspector-permit-watch-btn primary"
            data-open-hahc-modal="1"
            data-hahc-district="${distName.replace(/"/g, "&quot;")}"
            style="background:rgba(20,184,166,0.22);border-color:rgba(45,212,191,0.55);color:#CCFBF1;"
          >
            &#128269; Browse All ${summary.total_filings} ${distName} COAs &amp; Staff Reports
          </button>
        </div>
      </div>
    `;
  }

  _bindHahcCardEvents(rootEl) {
    if (!rootEl) return;
    rootEl.querySelectorAll("[data-open-hahc-modal]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const dist = btn.getAttribute("data-hahc-district") || "all";
        this.openHahcTrackerModal({ district: dist || "all" });
      });
    });
    rootEl.querySelectorAll("[data-hahc-inspect-hcad]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const hcad = btn.getAttribute("data-hahc-inspect-hcad") || "";
        if (hcad && this.mapController) {
          this.filterStore.setLayerVisibility("hahcTracker", true);
          this.mapController.clearHighlightedBoundary();
          this.mapController.selectFeatureByIdOrHcad({
            hcadNum: hcad,
            flyTo: true,
          });
        }
      });
    });
  }

  openHahcTrackerModal({ district = "", category = "", search = "" } = {}) {
    const modal = document.getElementById("hahc-tracker-modal");
    if (!modal) return;
    this._syncHahcCounts();

    if (district) {
      // Match normalized Historic District option in select if passed as "Norhill Historic District"
      const distSelect = document.getElementById("hahc-modal-district-select");
      let resolvedDist = district;
      if (distSelect && district !== "all") {
        const normTarget = district
          .toLowerCase()
          .replace(/\bhistoric\s+district\b/g, "")
          .trim();
        const optHit = Array.from(distSelect.options).find(
          (o) =>
            o.value.toLowerCase() === district.toLowerCase() ||
            o.value
              .toLowerCase()
              .replace(/\bhistoric\s+district\b/g, "")
              .trim() === normTarget
        );
        resolvedDist = optHit ? optHit.value : "all";
      }
      this._hahcModalState.district = resolvedDist;
    }
    if (category) {
      this._hahcModalState.category = category;
    }
    if (search !== undefined && search !== "") {
      this._hahcModalState.search = search;
    }

    const distSelect = document.getElementById("hahc-modal-district-select");
    if (distSelect) distSelect.value = this._hahcModalState.district || "all";
    const searchInput = document.getElementById("hahc-modal-search");
    if (searchInput) searchInput.value = this._hahcModalState.search || "";
    modal.querySelectorAll("[data-hahc-modal-cat]").forEach((b) => {
      b.classList.toggle(
        "active",
        (b.getAttribute("data-hahc-modal-cat") || "all") === (this._hahcModalState.category || "all")
      );
    });

    modal.classList.remove("hidden");
    this._renderHahcModalList();
  }

  _renderHahcModalList() {
    const listEl = document.getElementById("hahc-modal-list");
    if (!listEl) return;
    const payload = this.mapController?.getHahcTrackerPayload?.();
    const allFilings = Array.isArray(payload?.filings) ? payload.filings : [];

    const distFilter = this._hahcModalState.district || "all";
    const catFilter = this._hahcModalState.category || "all";
    const qLow = String(this._hahcModalState.search || "")
      .trim()
      .toLowerCase();

    const filtered = allFilings.filter((f) => {
      if (distFilter === "__landmarks_only__") {
        if (f.historic_district) return false;
      } else if (distFilter !== "all") {
        if (String(f.historic_district || "").toLowerCase() !== distFilter.toLowerCase()) {
          return false;
        }
      }

      if (catFilter === "active_agenda" && !f.is_active_agenda) return false;
      else if (catFilter === "addition_alteration" && f.app_category !== "addition_alteration") {
        return false;
      } else if (catFilter === "new_construction" && f.app_category !== "new_construction") {
        return false;
      } else if (
        catFilter === "demolition_relocation" &&
        f.app_category !== "demolition_relocation"
      ) {
        return false;
      } else if (catFilter === "designation" && f.app_category !== "designation") {
        return false;
      } else if (catFilter === "denied_or_cor" && f.outcome !== "denied_or_cor") {
        return false;
      }

      if (qLow) {
        const hay = `${f.address || ""} ${f.historic_district || ""} ${f.landmark_name || ""} ${
          f.app_type || ""
        } ${f.outcome_detail || ""} ${f.meeting_date || ""} ${f.hcad_num || ""}`.toLowerCase();
        if (!hay.includes(qLow)) return false;
      }
      return true;
    });

    if (filtered.length === 0) {
      listEl.innerHTML = `<div style="padding:18px;color:var(--text-secondary);">No HAHC COA or Designation filings match the current district and category filter.</div>`;
      return;
    }

    const shown = filtered.slice(0, 120);
    listEl.innerHTML =
      `<div style="padding:4px 4px 8px;font-size:11.5px;color:#94A3B8;">Showing <strong>${shown.length}</strong> of <strong>${filtered.length}</strong> matching HAHC filings (March 2024 - October 2026)</div>` +
      shown
        .map((f) => {
          const b = this._formatHahcOutcomeBadge(f.outcome, f.outcome_detail, f.is_active_agenda);
          const isCrit = Boolean(f.is_active_agenda || f.app_category === "demolition_relocation");
          const yrText = Number(f.year_built) >= 1836 ? `Built ${f.year_built}` : "";
          return `
            <div class="permit-watch-item-card ${isCrit ? "critical" : ""}">
              <div class="pw-item-top">
                <div class="pw-item-title-group">
                  <div class="pw-item-badges">
                    <span class="hahc-outcome-badge ${b.cls}">${b.label}</span>
                    <span class="pw-year-badge mono">${f.meeting_date}${
            f.item_num ? ` · Item ${f.item_num}` : ""
          }</span>
                    ${
                      f.historic_district
                        ? `<span class="pw-year-badge">${f.historic_district} Historic District</span>`
                        : `<span class="pw-year-badge">Individual Landmark Docket</span>`
                    }
                    ${yrText ? `<span class="pw-year-badge mono">${yrText}</span>` : ""}
                    ${
                      f.hcad_num
                        ? `<span class="pw-hcad-badge mono">HCAD ${f.hcad_num}</span>`
                        : ""
                    }
                  </div>
                  <h3 class="pw-item-title">${
                    f.landmark_name
                      ? `${f.landmark_name} <span class="pw-item-addr-inline">(${f.address})</span>`
                      : f.address
                  }</h3>
                  <div class="pw-item-loc-line"><strong>Application Type:</strong> ${
                    f.app_type
                  }</div>
                </div>
                <div class="pw-item-actions" style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;">
                  ${
                    f.staff_report_url
                      ? `<a href="${f.staff_report_url}" target="_blank" rel="noopener noreferrer" class="action-pill" style="text-decoration:none;border-color:rgba(45,212,191,0.5);background:rgba(20,184,166,0.18);color:#CCFBF1;">&#128196; Staff Report PDF &#8599;</a>`
                      : ""
                  }
                  ${
                    f.agenda_or_minutes_url
                      ? `<a href="${f.agenda_or_minutes_url}" target="_blank" rel="noopener noreferrer" class="action-pill subtle" style="text-decoration:none;">${
                          f.is_active_agenda ? "Oct 8 Agenda PDF" : "Action Minutes PDF"
                        } &#8599;</a>`
                      : ""
                  }
                  ${
                    Number.isFinite(Number(f.lng)) && Number.isFinite(Number(f.lat))
                      ? `<button
                          type="button"
                          class="action-pill pw-inspect-btn"
                          data-hahc-modal-inspect-hcad="${f.hcad_num || ""}"
                          data-hahc-modal-lon="${f.lng}"
                          data-hahc-modal-lat="${f.lat}"
                          data-hahc-modal-addr="${String(f.address || "").replace(/"/g, "&quot;")}"
                        >&#128269; Inspect on Map</button>`
                      : ""
                  }
                </div>
              </div>
            </div>
          `;
        })
        .join("");

    listEl.querySelectorAll("[data-hahc-modal-lon]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const hcad = btn.getAttribute("data-hahc-modal-inspect-hcad") || "";
        const lon = parseFloat(btn.getAttribute("data-hahc-modal-lon"));
        const lat = parseFloat(btn.getAttribute("data-hahc-modal-lat"));
        const addr = btn.getAttribute("data-hahc-modal-addr") || "";
        const modal = document.getElementById("hahc-tracker-modal");
        if (modal) modal.classList.add("hidden");

        const curState = this.filterStore.getState();
        this.filterStore.setState({
          hahcFilter: "all",
          layers: {
            ...(curState.layers || {}),
            hahcTracker: true,
          },
        });

        if (this.mapController && Number.isFinite(lon) && Number.isFinite(lat)) {
          this.mapController.clearHighlightedBoundary();
          if (hcad) {
            this.mapController.selectFeatureByIdOrHcad({
              hcadNum: hcad,
              addressQuery: addr,
              targetLng: lon,
              targetLat: lat,
              flyTo: false,
            });
          }
          this.mapController.flyToLocation({
            lng: lon,
            lat: lat,
            zoom: 17.6,
            hcadNum: hcad,
          });
        }
      });
    });
  }
}

if (typeof window !== "undefined" && typeof window.addEventListener === "function") {
  window.addEventListener("DOMContentLoaded", () => {
    const app = new HoustonAtlasApp();
    window.atlasApp = app;
    app.start();
  });
}
