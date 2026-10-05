/**
 * Main application UI controller for The Houston Building Atlas v2 (Preservation Houston).
 */

import {
  CURATED_TOURS,
  getLegendItems,
  getYearColorHex,
} from "./palettes.js";
import {
  createFilterStore,
  parseHashToState,
  serializeStateToHash,
} from "./filterStore.js";
import { AtlasMapController } from "./mapController.js";
import { fetchHcadDeepLink } from "./hcadLink.js";
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
} from "./curatedEdits.js";

class HoustonAtlasApp {
  constructor() {
    const { patch, viewport } = parseHashToState(window.location.hash);
    this.initialViewport = viewport;
    this.filterStore = createFilterStore(patch);
    this.searchIndex = [];
    this.globalStats = null;
    this.lastViewportStats = null;
    this.timelapseTimer = null;

    this.mapController = new AtlasMapController({
      containerId: "map-canvas",
      filterStore: this.filterStore,
      onSelectFeature: (props) => this.renderInspectorDrawer(props),
      onViewportStats: (stats) => this.handleViewportStats(stats),
      onPitchChange: (pitch) => this._syncTiltControls(pitch),
    });
  }

  async start() {
    this._bindControls();
    this._renderTourPills();
    this._renderLegend();
    this._syncControlsFromState(this.filterStore.getState());

    await Promise.all([
      this.mapController.init(this.initialViewport),
      this._loadMetadataFiles(),
    ]);

    this._mergeCuratedOverridesIntoSearchIndex();

    this.filterStore.subscribe((state) => {
      this._syncControlsFromState(state);
      this._renderLegend();
      this._manageTimelapseLoop(state);
      this._updateUrlHash(state);
    });
  }

  async _loadMetadataFiles() {
    try {
      const [searchRes, statsRes] = await Promise.all([
        fetch("public/data/search_index.json"),
        fetch("public/data/stats_summary.json"),
      ]);
      this.searchIndex = await searchRes.json();
      this.globalStats = await statsRes.json();
      this._renderGlobalDatasetSummary();
    } catch (err) {
      console.error("Failed to load metadata files:", err);
    }
  }

  _mergeCuratedOverridesIntoSearchIndex() {
    const ovMap = (this.mapController && this.mapController.curatedOverrides) || {};
    const existingByHcad = new Map();
    for (let i = 0; i < this.searchIndex.length; i++) {
      const item = this.searchIndex[i];
      if (item.hcad_num) {
        existingByHcad.set(String(item.hcad_num).trim(), i);
      }
    }

    for (const [hcad, ov] of Object.entries(ovMap)) {
      let lon = -95.38718;
      let lat = 29.79175;
      if (ov.geometry && ov.geometry.coordinates && ov.geometry.coordinates[0]?.[0]) {
        const ring = ov.geometry.type === "Polygon" ? ov.geometry.coordinates[0] : ov.geometry.coordinates[0][0];
        if (ring && ring.length) {
          lon = ring[0][0];
          lat = ring[0][1];
        }
      }
      const entry = {
        type: "building",
        id: ov.id || `ov_${hcad}`,
        hcad_num: hcad,
        label: ov.landmark_name || ov.address || `HCAD ${hcad}`,
        sublabel: `${ov.historic_district || "Harris County"} • Built ${ov.year_built} (✓ PH Verified)`,
        category: `Built ${ov.year_built} ✓`,
        year_built: ov.year_built,
        lon,
        lat,
        zoom: 17.6,
      };
      if (existingByHcad.has(hcad)) {
        this.searchIndex[existingByHcad.get(hcad)] = {
          ...this.searchIndex[existingByHcad.get(hcad)],
          ...entry,
        };
      } else {
        this.searchIndex.unshift(entry);
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
        const next3D = !this.filterStore.getState().extrude3D;
        this.filterStore.setState({ extrude3D: next3D });
        this.mapController.toggle3DPitch(next3D);
      });
    }

    // Bottom-Right Camera Tilt Preset Buttons (2D / 30° / 50° / 65°)
    document.querySelectorAll("[data-tilt-pitch]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const targetPitch = parseInt(btn.getAttribute("data-tilt-pitch"), 10) || 0;
        const enable3D = targetPitch > 0;
        this.filterStore.setState({ extrude3D: enable3D });
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

    // Overlay Layer Toggles
    const layerCheckboxes = [
      ["chk-layer-landmarks", "landmarks"],
      ["chk-layer-historic-districts", "historicDistricts"],
      ["chk-layer-heritage-districts", "heritageDistricts"],
      ["chk-layer-nrhp-districts", "nrhpDistricts"],
      ["chk-layer-thc-markers", "thcMarkers"],
      ["chk-layer-annexations", "annexations"],
    ];
    for (const [domId, layerKey] of layerCheckboxes) {
      const el = document.getElementById(domId);
      if (el) {
        el.addEventListener("change", (e) => {
          this.filterStore.setLayerVisibility(layerKey, e.target.checked);
        });
      }
    }

    // Search Input & Autocomplete
    const searchInput = document.getElementById("search-input");
    const searchResults = document.getElementById("search-results");
    if (searchInput && searchResults) {
      searchInput.addEventListener("input", (e) => {
        const q = e.target.value.trim().toLowerCase();
        if (q.length < 2) {
          searchResults.classList.add("hidden");
          searchResults.innerHTML = "";
          return;
        }
        const matches = this.searchIndex
          .filter(
            (item) =>
              item.label.toLowerCase().includes(q) ||
              (item.sublabel && item.sublabel.toLowerCase().includes(q)) ||
              (item.hcad_num && item.hcad_num.toLowerCase().includes(q))
          )
          .slice(0, 8);

        if (!matches.length) {
          searchResults.innerHTML = `<div class="search-empty">No matching addresses, landmarks, or districts found.</div>`;
          searchResults.classList.remove("hidden");
          return;
        }

        searchResults.innerHTML = matches
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

        searchResults.querySelectorAll(".search-result-item").forEach((btn) => {
          btn.addEventListener("click", () => {
            const idx = Number(btn.getAttribute("data-idx"));
            const chosen = matches[idx];
            if (chosen) {
              searchResults.classList.add("hidden");
              searchInput.value = chosen.label;
              this.mapController.flyToLocation({
                lng: chosen.lon,
                lat: chosen.lat,
                zoom: chosen.zoom || 17.2,
                hcadNum: chosen.hcad_num || "",
                featureId: chosen.id || "",
              });
              if (
                (chosen.type === "building" || chosen.type === "landmark") &&
                document.getElementById("inspector-drawer")?.classList.contains("hidden")
              ) {
                this.renderInspectorDrawer({
                  landmark_name: chosen.label,
                  year_built: chosen.year_built,
                  hcad_num: chosen.hcad_num,
                  landmark_type: chosen.category,
                  historic_district: chosen.sublabel,
                });
              }
            }
          });
        });
      });

      document.addEventListener("click", (e) => {
        if (!searchInput.contains(e.target) && !searchResults.contains(e.target)) {
          searchResults.classList.add("hidden");
        }
      });
    }

    // Inspector Drawer Close Button
    const btnCloseInspector = document.getElementById("btn-close-inspector");
    if (btnCloseInspector) {
      btnCloseInspector.addEventListener("click", () => {
        const drawer = document.getElementById("inspector-drawer");
        if (drawer) drawer.classList.add("hidden");
        this.mapController.clearSelection();
      });
    }

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

    // Suggest a Property Data Correction Modal & Staff Admin Gate
    const corrModal = document.getElementById("correction-modal");
    const btnCloseCorrModal = document.getElementById("btn-close-correction-modal");
    const corrForm = document.getElementById("form-property-correction");
    const btnExportPendingCsv = document.getElementById("btn-export-pending-csv");
    const btnSaveSheetConfig = document.getElementById("btn-save-sheet-config");
    const btnToggleAdminAuth = document.getElementById("btn-toggle-admin-auth");
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

    if (btnToggleAdminAuth && adminAuthPanel) {
      btnToggleAdminAuth.addEventListener("click", () => {
        adminAuthPanel.classList.toggle("hidden");
        syncAdminUiState();
      });
    }

    if (btnVerifyAdminUnlock) {
      btnVerifyAdminUnlock.addEventListener("click", async () => {
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
      });
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

          if (res.ok && res.override && res.override.hcad_num) {
            const hcadNum = res.override.hcad_num;
            const existingOv = this.mapController.curatedOverrides[hcadNum] || {};
            this.mapController.curatedOverrides[hcadNum] = {
              ...existingOv,
              ...res.override,
              geometry: existingOv.geometry || null,
            };
            this.mapController._captureDynamicOverrideGeometries();
            this.mapController._refreshCuratedOverridesSource();
            this.mapController._applyCurrentFilterStateToMap();
            this._mergeCuratedOverridesIntoSearchIndex();
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

    if (corrForm) {
      corrForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const submitBtn = document.getElementById("btn-submit-correction");
        const feedbackEl = document.getElementById("corr-submit-feedback");
        if (submitBtn) submitBtn.disabled = true;

        const payload = {
          address: document.getElementById("corr-address")?.value || "",
          hcad_num: document.getElementById("corr-hcad-num")?.value || "",
          current_year_built: document.getElementById("corr-current-year")?.value || "",
          suggested_year_built: document.getElementById("corr-suggested-year")?.value || "",
          historic_district: document.getElementById("corr-district")?.value || "",
          source_type: document.getElementById("corr-source-type")?.value || "Houston City Directory",
          architect: document.getElementById("corr-style-arch")?.value || "",
          source_citation: document.getElementById("corr-citation")?.value || "",
          submitter_name: document.getElementById("corr-submitter-name")?.value || "",
          submitter_email: document.getElementById("corr-submitter-email")?.value || "",
        };

        const webhookUrl =
          document.getElementById("admin-webhook-url")?.value ||
          this.mapController?.sheetSyncStatus?.webhookUrl ||
          "";

        const res = await submitCorrectionSuggestion(payload, webhookUrl);
        if (submitBtn) submitBtn.disabled = false;

        if (feedbackEl) {
          const deliveryNote = res.webhookDelivered
            ? "Sent directly to Preservation Houston's Google Sheet Pending Review Queue."
            : "Queued in Pending Review Queue (use 'Export Pending Queue (.CSV)' or connect a Google Sheet webhook below).";
          feedbackEl.innerHTML = `<strong>&#10003; Thank you!</strong> Suggestion for <strong>${payload.address || payload.hcad_num}</strong> (Built <strong>${payload.suggested_year_built}</strong>, source: <em>${payload.source_type}</em>) has been recorded with status <code>Pending</code>. ${deliveryNote} It will go live on the public map once approved by an administrator.`;
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

    // Sidebar Collapse Toggle (for smaller screens)
    const btnToggleSidebar = document.getElementById("btn-toggle-sidebar");
    const sidebar = document.getElementById("atlas-sidebar");
    if (btnToggleSidebar && sidebar) {
      btnToggleSidebar.addEventListener("click", () => {
        sidebar.classList.toggle("collapsed");
      });
    }
  }

  openCorrectionModal(props) {
    const modal = document.getElementById("correction-modal");
    if (!modal || !props) return;

    const addrEl = document.getElementById("corr-address");
    const hcadEl = document.getElementById("corr-hcad-num");
    const currYrEl = document.getElementById("corr-current-year");
    const suggYrEl = document.getElementById("corr-suggested-year");
    const distEl = document.getElementById("corr-district");
    const styleEl = document.getElementById("corr-style-arch");
    const citeEl = document.getElementById("corr-citation");
    const feedbackEl = document.getElementById("corr-submit-feedback");

    if (addrEl) addrEl.value = props.address || props.landmark_name || "Unknown Address";
    if (hcadEl) hcadEl.value = props.hcad_num || "";
    if (currYrEl) currYrEl.value = props.original_hcad_year || props.year_built || "";
    if (suggYrEl) suggYrEl.value = props.is_curated_override ? props.year_built : "";
    if (distEl) distEl.value = props.historic_district || "";
    if (styleEl) styleEl.value = props.architect || props.bld_style || "";
    if (citeEl) citeEl.value = props.source_citation || "";
    if (feedbackEl) {
      feedbackEl.classList.add("hidden");
      feedbackEl.innerHTML = "";
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

        container.querySelectorAll(".tour-pill").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");

        const banner = document.getElementById("tour-narrative-banner");
        const bannerTitle = document.getElementById("tour-banner-title");
        const bannerDesc = document.getElementById("tour-banner-desc");
        if (banner && bannerTitle && bannerDesc) {
          bannerTitle.textContent = `${tour.name} (${tour.era})`;
          bannerDesc.textContent = tour.description;
          banner.classList.remove("hidden");
        }

        this.mapController.flyToLocation({
          lng: tour.center[0],
          lat: tour.center[1],
          zoom: tour.zoom,
          pitch: this.filterStore.getState().extrude3D ? Math.max(45, tour.pitch) : 0,
        });
      });
    });

    const btnCloseBanner = document.getElementById("btn-close-tour-banner");
    if (btnCloseBanner) {
      btnCloseBanner.addEventListener("click", () => {
        const banner = document.getElementById("tour-narrative-banner");
        if (banner) banner.classList.add("hidden");
        container.querySelectorAll(".tour-pill").forEach((b) => b.classList.remove("active"));
      });
    }
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

    // Layer Checkboxes
    const mapLayerIds = {
      "chk-layer-landmarks": state.layers.landmarks,
      "chk-layer-historic-districts": state.layers.historicDistricts,
      "chk-layer-heritage-districts": state.layers.heritageDistricts,
      "chk-layer-nrhp-districts": state.layers.nrhpDistricts,
      "chk-layer-thc-markers": state.layers.thcMarkers,
      "chk-layer-annexations": state.layers.annexations,
    };
    for (const [id, checked] of Object.entries(mapLayerIds)) {
      const el = document.getElementById(id);
      if (el) el.checked = Boolean(checked);
    }
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

    const props = applyOverrideToProperties(
      rawProps,
      this.mapController ? this.mapController.curatedOverrides : {}
    );

    const yr = Number(props.year_built) || 0;
    const currentYear = 2026;
    const ageText = yr >= 1836 ? `${currentYear - yr} yrs old` : "Date unrecorded in HCAD";
    const yearDisplay = yr >= 1836 ? `Built ${yr}` : "Undated / Vacant";
    const yearColor = getYearColorHex(yr, this.filterStore.getState().paletteStyle);

    const title =
      props.landmark_name ||
      props.name ||
      props.address ||
      "Houston Historic Structure";
    const subtitle =
      props.address && props.address !== title
        ? props.address
        : props.historic_district || "Harris County, Texas";

    const statusBadge =
      props.landmark_type ||
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
            props.source_url
              ? `<a href="${props.source_url}" target="_blank" rel="noopener noreferrer" class="ph-verified-link">
                  View Historical Directory / Source Archive &#8599;
                </a>`
              : ""
          }
        </div>`
      : "";

    content.innerHTML = `
      <div class="inspector-hero">
        <div class="inspector-badges">
          <span class="inspector-year-pill" style="background:${yearColor};">${yearDisplay}${props.is_curated_override ? " &#10003;" : ""}</span>
          <span class="inspector-age-pill">${ageText}</span>
        </div>
        <h2 class="inspector-title" id="inspector-property-title">${title}</h2>
        <p class="inspector-subtitle">${subtitle}</p>
        <div class="inspector-status-banner">
          <span class="status-dot"></span>
          <span>${statusBadge}</span>
        </div>
      </div>

      ${verifiedBannerHtml}

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
          <span class="cell-label">Historic District</span>
          <span class="cell-value">${props.historic_district || "Outside City District"}</span>
        </div>
        <div class="inspector-cell">
          <span class="cell-label">Building Floor Area</span>
          <span class="cell-value mono">${bldSqft}</span>
        </div>
        <div class="inspector-cell">
          <span class="cell-label">Parcel Lot Size</span>
          <span class="cell-value mono">${landSqft}</span>
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
          <span class="cell-label">Architectural Style / Building Class</span>
          <span class="cell-value">${props.bld_style || props.style || props.use_category || "Residential Structure"}</span>
        </div>
        ${
          props.architect
            ? `<div class="inspector-cell full">
                <span class="cell-label">Architect / Builder (COH Landmark Record)</span>
                <span class="cell-value">${props.architect}</span>
              </div>`
            : ""
        }
        <div class="inspector-cell full">
          <span class="cell-label">Land Use Classification</span>
          <span class="cell-value">${props.landuse_desc || props.use_category || "Residential"}</span>
        </div>
        <div class="inspector-cell full">
          <span class="cell-label">Subdivision / Legal Description</span>
          <span class="cell-value">${props.subdivision || "Not listed"}</span>
        </div>
        <div class="inspector-cell full">
          <span class="cell-label">Recorded Property Owner (HCAD)</span>
          <span class="cell-value">${props.owner || "Public / Unlisted"}</span>
        </div>
        <div class="inspector-cell full">
          <span class="cell-label">Geometry Provenance</span>
          <span class="cell-value">${fpSourceLabel}</span>
        </div>
      </div>

      <div class="inspector-actions">
        <button type="button" class="inspector-btn suggest-edit" id="btn-suggest-correction">
          &#9998; Suggest a Date / Data Correction
        </button>
        ${
          hcadNum
            ? `<a
                href="https://search.hcad.org/"
                target="_blank"
                rel="noopener noreferrer"
                class="inspector-btn primary"
                id="btn-open-hcad"
                data-hcad-num="${hcadNum}"
                title="Generating direct HCAD SearchResults deep link for ${hcadNum}..."
              >
                Open HCAD Record (${hcadNum}) &#8599;
              </a>
              <a
                href="https://arcweb.hcad.org/parcel-viewer-v2.0/?hcad_num=${encodeURIComponent(hcadNum)}"
                target="_blank"
                rel="noopener noreferrer"
                class="inspector-btn secondary"
                id="btn-open-hcad-gis"
                title="Open parcel ${hcadNum} in HCAD GIS Parcel Viewer"
              >
                HCAD GIS Map &#8599;
              </a>`
            : ""
        }
        <button type="button" class="inspector-btn secondary" id="btn-copy-share-link">
          Copy Shareable Link
        </button>
      </div>
    `;

    drawer.classList.remove("hidden");

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

    const btnOpenHcad = document.getElementById("btn-open-hcad");
    if (btnOpenHcad && hcadNum) {
      // Immediately mint a fresh encrypted SearchResults deep-link token from HCAD's API
      fetchHcadDeepLink(hcadNum)
        .then((deepUrl) => {
          if (deepUrl && btnOpenHcad.getAttribute("data-hcad-num") === hcadNum) {
            btnOpenHcad.href = deepUrl;
            btnOpenHcad.setAttribute("data-deep-ready", "true");
            btnOpenHcad.title = `Direct HCAD Property Record deep link ready (${hcadNum})`;
          }
        })
        .catch(() => {
          // Keep fallback https://search.hcad.org/ if offline
        });

      // Also copy account number to clipboard on click as a convenient backup
      btnOpenHcad.addEventListener("click", () => {
        if (navigator.clipboard) {
          navigator.clipboard.writeText(hcadNum);
        }
      });
    }

    const btnShare = document.getElementById("btn-copy-share-link");
    if (btnShare) {
      btnShare.addEventListener("click", () => {
        const url = window.location.href;
        if (navigator.clipboard) {
          navigator.clipboard.writeText(url);
        }
        btnShare.textContent = "Link Copied to Clipboard!";
        setTimeout(() => {
          btnShare.textContent = "Copy Shareable Link";
        }, 2000);
      });
    }
  }

  _updateUrlHash(state) {
    const vp = this.lastViewportStats ? this.lastViewportStats.viewport : null;
    const hash = serializeStateToHash(state, vp);
    if (hash) {
      window.history.replaceState(null, "", `#${hash}`);
    }
  }
}

window.addEventListener("DOMContentLoaded", () => {
  const app = new HoustonAtlasApp();
  window.atlasApp = app;
  app.start();
});
