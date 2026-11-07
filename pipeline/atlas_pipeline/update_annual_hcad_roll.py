"""Guarded Incremental Annual HCAD Appraisal Roll & Parcel Refresher (Milestone 4).

Responsibilities:
  1. Stage 1 - Isolated Staging & Pre-Flight Validation (`stage_hcad_roll_archives`,
     `validate_staged_hcad_roll`, `RollSanityConfig`):
       - Discovers (`--discover-latest-year`) or downloads upstream HCAD CAMA/GIS archives
         into `pipeline/cache/rolls/<YYYY>/` via `.part` temporary files, validating HTTP 200,
         non-HTML Content-Type, `PK\\x03\\x04` magic bytes, `ZipFile.testzip()`, SHA-256
         checksums (`roll_manifest.json`), dynamic `latin-1` tab headers, `EPSG:2278` ->
         `HARRIS_COUNTY_BBOX` sample centroids (across GDB/GPKG/SHP/GeoJSON), >= 95% account /
         improved / parcel retention ratios, and modal `real_acct.txt:yr == ctx.tax_year`.
  2. Stage 2 - Normalized Indexing & Provenance Governance (`stream_hcad_account_snapshots`,
     `classify_parcel_footprint_governance`, `is_feature_chronology_protected`):
       - Aggregates multi-building accounts cleanly with explicit runtime `max_year` bounds
         (DF-1), writes stamped v2 `cama_<YYYY>_compact.pkl` inside `rolls/<YYYY>/`, and
         partitions multi-footprint parcels (`FULL_CURATED_REPLACE`,
         `PARTIAL_CURATED_SUBBUILDINGS`, `UNCURATED_MULTI_FOOTPRINT`, `SINGLE_FOOTPRINT`) while
         protecting all Tier 1/2 curated overrides, `keep_shard_footprints`, `"COH Landmark
         Designation Report"`, and `school_situs_hydrated` properties (DF-2).
  3. Stage 3 - 5-Category Delta, Replat Resolver & Audit Reports (`resolve_replat_lineage`,
     `classify_feature_delta`, `audit_curated_hcad_references`):
       - Classifies every parcel/footprint into `UNCHANGED`, `UPDATED_STANDING`,
         `DEMOLISHED_TO_VACANT`, `REPLATTED_OR_RETIRED`, and `NEW_CONSTRUCTION`, enforces the
         `<= 2.5%` annual demolition circuit breaker, drops overhanging parent house polygons on
         `1 -> K` townhome splits, disambiguates `#<slug>` suffixes on `M -> 1` merges, and emits
         read-only `annual_roll_delta_report_<YYYY>.json`, `orphaned_curated_entities_<YYYY>.json`,
         and `lost_houston_candidates_<YYYY>.json`.
  4. Stages 4-5 - Shadow Staging, Unified Deletion Pruning & Atomic Promotion
     (`reconcile_derived_building_indexes`, `StagedRollPromotion`):
       - Synthesizes oriented `EPSG:2278` footprints (`<= 6` decimals CCW), runs the single
         shared 4-phase undated fallback (`unknown == 0`, DF-7), deduplicates dirty `.geojsonseq`
         shards, compiles only dirty `.pmtiles` shards, reconciles `ArtifactSyncDelta` across
         inner core GeoJSON/PMTiles, address shards, search index, permits/deeds, scorecards,
         audit queue, and all 24 `export_cache/*.json` historic districts (DF-4), validates
         `< 980 MiB` / `< 95 MiB` / `version: 3` / integer `decade` / zero dash gates (DF-5),
         and executes ordered `os.replace` swaps + `AnchorRule` lockstep sync.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable
import copy
from dataclasses import dataclass, field
import datetime
from enum import Enum
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
from typing import Any
import urllib.error
import urllib.request
import zipfile

import numpy as np
import orjson
import pyogrio
import pyogrio.raw
from pyproj import Transformer
import shapely
from shapely.geometry import Polygon, mapping, shape
from shapely.strtree import STRtree
from shapely.validation import make_valid

from atlas_pipeline.audit_historical_accuracy import run_audit as run_historical_audit
from atlas_pipeline.build_neighborhood_scorecards import (
    main as rebuild_neighborhood_scorecards,
    normalize_hcad13,
)
from atlas_pipeline.enrich_deed_chains import MAX_DEEDS_PER_PARCEL
from atlas_pipeline.fix_campus_anomalies_and_schools import (
    ADDITIONAL_CURATED_PARCEL_CHRONOLOGIES,
    VALID_NUMBERED_SITUS_RE,
    _compute_address_shard_key,
)
from atlas_pipeline.fix_polygon_orientations import build_oriented_footprint_2278
from atlas_pipeline.full_build import (
    SPLIT_LAT,
    SPLIT_LON_EAST,
    SPLIT_LON_WEST,
    deduplicate_geojsonseq_shard,
)
from atlas_pipeline.lost_houston_expansion import evaluate_cohort5_teardown_candidate
from atlas_pipeline.resolve_undated_countywide import (
    PHASE1_CURATED_LANDMARKS,
    build_undated_fallback_context,
    load_parcel_tieback_map,
    normalize_owner,
    normalize_street,
    resolve_undated_feature_batch,
    seed_authoritative_dated_parcels,
    stream_extra_feature_years,
)
import atlas_pipeline.schema as _schema
from atlas_pipeline.schema import (
    ACTIVE_CACHE_BUST,
    ACTIVE_TAX_YEAR,
    EXTRA_FEATURES_CAP_PER_ACCOUNT,
    HARD_MAX_APP_BYTES,
    HARRIS_COUNTY_BBOX,
    INFERRED_FALLBACK_YEAR_SOURCES,
    MAX_SINGLE_FILE_BYTES,
    MIN_VALID_YEAR,
    OWNERSHIP_TIER_KEYS,
    QUAD_NAMES,
    SOFT_MAX_APP_BYTES,
    WATCHLIST_TOMBSTONE_STATUSES,
    RollContext,
    classify_main_structure_demolition_permit,
    classify_use_category,
    compile_quadrant_pmtiles,
    compress_deterministic_gzip_json,
    compute_decade,
    compute_decade_bins,
    download_archive_streaming,
    estimate_stories_and_height,
    extract_parcels_zip_dataset,
    finalize_hcad_account_permits,
    find_zip_member_case_insensitive,
    is_boundary_search_entry,
    is_demolished_vacant_marker,
    load_compact_cama_lookup,
    normalize_hcad_appraiser_note,
    normalize_hcad_clerk_instrument,
    normalize_hcad_date,
    normalize_hcad_extra_feature_row,
    normalize_hcad_permit_row,
    normalize_polygon_rings_6dp_ccw,
    normalize_ring_6dp,
    normalize_year,
    parse_dos_yyyymmdd,
    peek_real_acct_zip_year,
    sanitize_atlas_text,
    save_compact_cama_lookup,
    update_pmtiles_manifest_v3,
    verify_zip_file_integrity,
    write_compact_json,
)
from atlas_pipeline.tile_builder import compile_pmtiles_archive

HCAD_CAMA_BASE_URL = "https://download.hcad.org/data/CAMA"
HCAD_GIS_PARCELS_URL = "https://download.hcad.org/data/GIS/Parcels.zip"

REQUIRED_ROLL_ARCHIVES: tuple[str, ...] = (
    "Real_building_land.zip",
    "Real_acct_owner.zip",
    "Parcels.zip",
)
OPTIONAL_ROLL_ARCHIVES: tuple[str, ...] = ("Code_description_real.zip",)

# Dynamic header contract: each inner tuple lists acceptable alias column names (at least one must exist).
REQUIRED_TABLE_COLUMNS: dict[str, dict[str, tuple[tuple[str, ...], ...]]] = {
    "Real_building_land.zip": {
        "building_res.txt": (
            ("acct",),
            ("date_erected", "date_erect"),
            ("yr_remodel",),
            ("dscr",),
            ("structure_dscr",),
            ("property_use_cd",),
            ("im_sq_ft", "act_ar", "heat_ar", "gross_ar"),
        ),
        "building_other.txt": (
            ("acct",),
            ("date_erected", "date_erect"),
            ("yr_remodel",),
            ("dscr",),
            ("structure_dscr",),
            ("property_use_cd",),
            ("im_sq_ft", "act_ar", "heat_ar", "gross_ar"),
        ),
        "extra_features_detail1.txt": (
            ("acct",),
            ("cd",),
            ("dscr",),
            ("act_yr",),
            ("eff_yr",),
        ),
    },
    "Real_acct_owner.zip": {
        "real_acct.txt": (
            ("acct",),
            ("yr",),
            ("mailto",),
            ("site_addr_1",),
            ("str_num",),
            ("str",),
            ("state_class",),
            ("yr_impr",),
            ("bld_ar",),
            ("land_ar",),
            ("bld_val",),
            ("splt_dt",),
            ("certified_date",),
            ("lgl_1",),
            ("lgl_2",),
        ),
        "permits.txt": (
            ("acct",),
            ("permit_type",),
            ("dscr",),
            ("issue_date",),
            ("yr",),
        ),
        "deeds.txt": (
            ("acct",),
            ("dos",),
            ("clerk_yr",),
            ("clerk_id",),
        ),
        "parcel_tieback.txt": (
            ("acct",),
            ("related_acct",),
        ),
    },
}

OPTIONAL_TABLE_COLUMNS: dict[str, dict[str, tuple[tuple[str, ...], ...]]] = {
    "Real_building_land.zip": {
        "extra_features_detail2.txt": (
            ("acct",),
            ("cd",),
            ("dscr",),
            ("act_yr",),
            ("eff_yr",),
        ),
    },
}

PROTECTED_CHRONOLOGY_SOURCES: frozenset[str] = frozenset({
    "curated_landmark",
    "good_brick_award",
    "archival",
    "COH Landmark Designation Report",
})

CURATED_CAMPUS_SLUG_PREFIXES: tuple[str, ...] = (
    "lee_",
    "rel_hs_",
    "cy_ridge_",
)

MAX_OVERRIDES_BYTES = 8 * 1024 * 1024
MAX_SCORECARDS_BYTES = int(2.5 * 1024 * 1024)
MAX_AUDIT_QUEUE_BYTES = 1 * 1024 * 1024


class RollValidationError(RuntimeError):
    """Raised when a staged HCAD tax roll fails any pre-flight or circuit-breaker gate."""


@dataclass(frozen=True)
class RollSanityConfig:
    """Countywide pre-flight completeness and circuit-breaker thresholds (Section 4.1)."""

    min_account_retention_ratio: float = 0.95
    min_improved_retention_ratio: float = 0.95
    min_parcel_retention_ratio: float = 0.95
    min_in_bbox_ratio: float = 0.90
    max_annual_demolition_fraction: float = 0.025
    expected_tax_year_match: bool = True
    min_certified_ratio_for_flag: float = 0.50
    sample_parcel_limit: int = 256


class ParcelGovernance(str, Enum):
    """Provenance governance mode for single vs. multi-footprint parcels (DF-2)."""

    FULL_CURATED_REPLACE = "FULL_CURATED_REPLACE"
    PARTIAL_CURATED_SUBBUILDINGS = "PARTIAL_CURATED_SUBBUILDINGS"
    UNCURATED_MULTI_FOOTPRINT = "UNCURATED_MULTI_FOOTPRINT"
    SINGLE_FOOTPRINT = "SINGLE_FOOTPRINT"


class DeltaCategory(str, Enum):
    """Deterministic 5-category annual parcel/building delta taxonomy."""

    UNCHANGED = "UNCHANGED"
    UPDATED_STANDING = "UPDATED_STANDING"
    DEMOLISHED_TO_VACANT = "DEMOLISHED_TO_VACANT"
    REPLATTED_OR_RETIRED = "REPLATTED_OR_RETIRED"
    NEW_CONSTRUCTION = "NEW_CONSTRUCTION"


@dataclass
class HcadAccountSnapshot:
    """Normalized per-account aggregation across HCAD building, real_acct, and permit tables."""

    acct: str
    earliest_year: int = 0
    latest_year: int = 0
    max_remodel_year: int = 0
    eff_year: int = 0
    total_bld_area: float = 0.0
    building_count: int = 0
    state_class: str = ""
    property_use_cd: str = ""
    use_category: str = "Residential"
    bld_style: str = ""
    structure_dscr: str = ""
    quality_desc: str = ""
    cama_notes: str = ""
    numbered_address: str = ""
    raw_site_addr_1: str = ""
    zip_code: str = ""
    owner: str = ""
    mail_addr_1: str = ""
    mail_addr_2: str = ""
    mail_city: str = ""
    mail_state: str = ""
    mail_zip: str = ""
    own_tier: str = "homestead"
    port_id: str = ""
    display_mail: str = ""
    care_of: str = ""
    is_protected_homestead: bool = False
    is_sec_25_025_confidential: bool = False
    co_owners: list[str] = field(default_factory=list)
    subdivision: str = ""
    lgl_2: str = ""
    neighborhood_code: str = ""
    market_area_1: str = ""
    land_area_sqft: float = 0.0
    bld_val: float = 0.0
    yr_impr: int = 0
    splt_dt: str = ""
    new_own_dt: str = ""
    certified_date: str = ""
    roll_yr: int = 0
    has_main_demo_permit: bool = False
    demo_permit_year: int = 0
    demo_permit_type: str = ""
    demo_permit_date: str = ""
    demo_permit_desc: str = ""
    demo_permit_addr: str = ""
    demo_permit_apt: str = ""
    was_relocated: bool = False
    relocation_note: str = ""

    @property
    def is_improved(self) -> bool:
        return (
            self.earliest_year >= MIN_VALID_YEAR
            or self.yr_impr >= MIN_VALID_YEAR
            or self.total_bld_area > 0.0
        )

    def to_cama_tuple(self) -> tuple[int, int, int, str, str, str]:
        yr_val = self.earliest_year if self.earliest_year >= MIN_VALID_YEAR else self.yr_impr
        return (
            int(yr_val or 0),
            int(self.max_remodel_year or 0),
            int(round(self.total_bld_area or 0.0)),
            str(self.state_class or self.property_use_cd or ""),
            str(self.bld_style or ""),
            str(self.subdivision or ""),
        )


@dataclass
class ArtifactSyncDelta:
    """Unified delta envelope propagating deletions, re-keyings, and prefix shifts across indexes (DF-4)."""

    deleted_hcads: set[str] = field(default_factory=set)
    deleted_building_ids: set[str] = field(default_factory=set)
    old_to_new_hcads: dict[str, str] = field(default_factory=dict)
    old_to_all_new_hcads: dict[str, list[str]] = field(default_factory=dict)
    old_fid_to_new_hcad: dict[str, str] = field(default_factory=dict)
    old_to_new_building_ids: dict[str, str] = field(default_factory=dict)
    migrated_addr_prefixes_by_key: dict[str, set[tuple[str, str]]] = field(
        default_factory=lambda: defaultdict(set)
    )
    upserted_features_by_hcad: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: defaultdict(list))
    new_construction_hcads: set[str] = field(default_factory=set)
    inner_core_touched: bool = False
    dirty_quadrants: set[str] = field(default_factory=set)
    touched_permit_prefixes: set[str] = field(default_factory=set)


# ---------------------------------------------------------------------------
# Stage 1: Archive Discovery, Isolated Staging & Pre-Flight Validation
# ---------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _verify_zip_file_integrity(path: Path) -> list[str]:
    """Verify ZIP local header magic bytes and full archive CRC/readability."""
    return verify_zip_file_integrity(path, error_cls=RollValidationError)


def probe_hcad_archive_url(
    url: str,
    *,
    opener: Callable[..., Any] | None = None,
    timeout: float = 20.0,
) -> bool:
    """Probe an upstream HCAD archive URL via HEAD / Range GET without downloading full GBs."""
    open_fn = opener or urllib.request.urlopen
    for method, headers in (
        ("HEAD", {"User-Agent": "HoustonBuildingAtlas/2.0"}),
        ("GET", {"User-Agent": "HoustonBuildingAtlas/2.0", "Range": "bytes=0-3"}),
    ):
        req = urllib.request.Request(url, method=method, headers=headers)
        try:
            with open_fn(req, timeout=timeout) as resp:
                status = getattr(resp, "status", None) or resp.getcode()
                if status not in (200, 206):
                    continue
                ctype = str(resp.headers.get("Content-Type") or "").lower()
                if "text/html" in ctype:
                    return False
                if method == "GET":
                    head_bytes = resp.read(4)
                    if len(head_bytes) >= 4 and head_bytes[:4] != b"PK\x03\x04":
                        return False
                return True
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 410):
                return False
            continue
        except Exception:
            continue
    return False


def discover_latest_hcad_roll_year(
    *,
    start_year: int | None = None,
    max_probe_year: int | None = None,
    base_url: str = HCAD_CAMA_BASE_URL,
    opener: Callable[..., Any] | None = None,
) -> int:
    """Probe `https://download.hcad.org/data/CAMA/<YYYY>/` to find the newest published roll year."""
    base_start = ACTIVE_TAX_YEAR if start_year is None else int(start_year)
    now_yr = datetime.datetime.now(datetime.timezone.utc).year
    top_yr = max_probe_year if max_probe_year is not None else max(now_yr + 2, base_start + 5)
    latest_found = 0
    for cand_yr in range(base_start, int(top_yr) + 1):
        bldg_url = f"{base_url.rstrip('/')}/{cand_yr}/Real_building_land.zip"
        acct_url = f"{base_url.rstrip('/')}/{cand_yr}/Real_acct_owner.zip"
        if probe_hcad_archive_url(bldg_url, opener=opener) and probe_hcad_archive_url(acct_url, opener=opener):
            latest_found = cand_yr
    if latest_found <= 0:
        raise RollValidationError(
            f"Could not discover any valid published HCAD CAMA roll between {base_start} and {top_yr} "
            f"at {base_url}."
        )
    return latest_found


def _download_archive_streaming(
    url: str,
    dest_path: Path,
    *,
    opener: Callable[..., Any] | None = None,
    timeout: float = 120.0,
) -> None:
    """Stream an upstream HCAD `.zip` into `<name>.part`, validate, then rename atomically."""
    download_archive_streaming(
        url,
        dest_path,
        opener=opener or urllib.request.urlopen,
        timeout=timeout,
        error_cls=RollValidationError,
    )


def _link_or_copy_readonly(src: Path, dst: Path) -> None:
    if src.resolve() == dst.resolve():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f"{dst.name}.part")
    tmp.unlink(missing_ok=True)
    try:
        try:
            os.link(src, tmp)
        except OSError:
            shutil.copy2(src, tmp)
        tmp.replace(dst)
    finally:
        tmp.unlink(missing_ok=True)


def stage_hcad_roll_archives(
    ctx: RollContext,
    *,
    roll_dir: Path | None = None,
    cama_base_url: str = HCAD_CAMA_BASE_URL,
    parcels_url: str = HCAD_GIS_PARCELS_URL,
    opener: Callable[..., Any] | None = None,
    force_redownload: bool = False,
) -> tuple[Path, dict[str, Any]]:
    """Stage candidate archives inside `pipeline/cache/rolls/<YYYY>/` with SHA-256 manifest."""
    if ctx.cache_dir is None:
        raise ValueError("RollContext.cache_dir must be set before staging archives.")
    staging_dir = Path(ctx.cache_dir) / "rolls" / str(ctx.tax_year)
    staging_dir.mkdir(parents=True, exist_ok=True)

    all_names = REQUIRED_ROLL_ARCHIVES + OPTIONAL_ROLL_ARCHIVES
    archives_meta: dict[str, Any] = {}

    for name in all_names:
        is_required = name in REQUIRED_ROLL_ARCHIVES
        dest = staging_dir / name
        if roll_dir is not None:
            src = Path(roll_dir) / name
            if not src.exists():
                if is_required:
                    raise RollValidationError(
                        f"Missing required archive {name} in --roll-dir {roll_dir}"
                    )
                continue
            members = _verify_zip_file_integrity(src)
            _link_or_copy_readonly(src, dest)
        else:
            if force_redownload or not dest.exists():
                url = (
                    parcels_url
                    if name == "Parcels.zip"
                    else f"{cama_base_url.rstrip('/')}/{ctx.tax_year}/{name}"
                )
                try:
                    _download_archive_streaming(url, dest, opener=opener)
                except RollValidationError:
                    if is_required:
                        raise
                    continue
            members = _verify_zip_file_integrity(dest)

        archives_meta[name] = {
            "size_bytes": dest.stat().st_size,
            "sha256": sha256_file(dest),
            "member_count": len(members),
            "members": members[:64],
        }

    manifest = {
        "schema_version": 1,
        "tax_year": int(ctx.tax_year),
        "staged_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": str(roll_dir) if roll_dir is not None else f"{cama_base_url}/{ctx.tax_year}",
        "archives": archives_meta,
    }
    write_compact_json(staging_dir / "roll_manifest.json", manifest)
    return staging_dir, manifest


def resolve_parcels_vector_source(
    parcels_zip_path: Path,
    scratch_extract_dir: Path | None = None,
) -> tuple[str, str | None]:
    """Extract/resolve Esri OpenFileGDB, GeoPackage, Shapefile, or GeoJSON inside `Parcels.zip`."""
    try:
        ds_p = extract_parcels_zip_dataset(
            parcels_zip_path, scratch_extract_dir=scratch_extract_dir
        )
    except ValueError as exc:
        raise RollValidationError(str(exc)) from exc
    ds_path = str(ds_p)

    try:
        raw_layers = pyogrio.list_layers(ds_path)
        layer_names = [str(row[0]) for row in raw_layers]
    except Exception as exc:
        raise RollValidationError(f"Failed listing vector layers in {ds_path}: {exc}") from exc

    if not layer_names:
        raise RollValidationError(f"No vector layers found in {ds_path}.")
    chosen_layer = "Parcels" if "Parcels" in layer_names else layer_names[0]
    return ds_path, chosen_layer


def _validate_zip_table_headers(
    zip_path: Path,
    required_spec: dict[str, tuple[tuple[str, ...], ...]],
    optional_spec: dict[str, tuple[tuple[str, ...], ...]] | None = None,
) -> dict[str, list[str]]:
    """Verify tab-delimited `latin-1` table presence and dynamic column aliases by header name."""
    headers_by_member: dict[str, list[str]] = {}
    with zipfile.ZipFile(zip_path, "r") as zf:
        present = set(zf.namelist())
        for member, col_groups in required_spec.items():
            if member not in present:
                raise RollValidationError(
                    f"Archive {zip_path.name} is missing required table {member!r}."
                )
            with zf.open(member, "r") as raw_f:
                line = raw_f.readline().decode("latin-1", errors="replace").rstrip("\r\n")
            cols = [c.strip() for c in line.split("\t") if c.strip()]
            col_set = set(cols)
            missing_groups = [
                "/".join(alias_group)
                for alias_group in col_groups
                if not any(alias in col_set for alias in alias_group)
            ]
            if missing_groups:
                raise RollValidationError(
                    f"Table {zip_path.name}/{member} is missing required column(s): "
                    f"{', '.join(missing_groups)} (found headers: {cols[:20]})."
                )
            headers_by_member[member] = cols

        for member, col_groups in (optional_spec or {}).items():
            if member not in present:
                continue
            with zf.open(member, "r") as raw_f:
                line = raw_f.readline().decode("latin-1", errors="replace").rstrip("\r\n")
            cols = [c.strip() for c in line.split("\t") if c.strip()]
            col_set = set(cols)
            missing_groups = [
                "/".join(alias_group)
                for alias_group in col_groups
                if not any(alias in col_set for alias in alias_group)
            ]
            if missing_groups:
                raise RollValidationError(
                    f"Optional table {zip_path.name}/{member} is missing required column(s): "
                    f"{', '.join(missing_groups)}."
                )
            headers_by_member[member] = cols
    return headers_by_member


def _reproject_shapely_geom(geom: Any, transformer: Transformer) -> Any:
    return shapely.transform(
        geom,
        lambda coords: np.column_stack(transformer.transform(coords[:, 0], coords[:, 1])),
    )


def validate_staged_hcad_roll(
    staging_dir: Path,
    *,
    ctx: RollContext,
    sanity: RollSanityConfig | None = None,
    baseline_acct_count: int = 0,
    baseline_improved_count: int = 0,
    baseline_parcel_count: int = 0,
    allow_partial_roll: bool = False,
    scratch_extract_dir: Path | None = None,
) -> dict[str, Any]:
    """Run all Stage 1 completeness, header, CRS/bbox, and retention gates before touching live state."""
    cfg = sanity or RollSanityConfig()
    sdir = Path(staging_dir)
    for req_name in REQUIRED_ROLL_ARCHIVES:
        _verify_zip_file_integrity(sdir / req_name)

    bldg_headers = _validate_zip_table_headers(
        sdir / "Real_building_land.zip",
        REQUIRED_TABLE_COLUMNS["Real_building_land.zip"],
        OPTIONAL_TABLE_COLUMNS.get("Real_building_land.zip"),
    )
    acct_headers = _validate_zip_table_headers(
        sdir / "Real_acct_owner.zip",
        REQUIRED_TABLE_COLUMNS["Real_acct_owner.zip"],
        OPTIONAL_TABLE_COLUMNS.get("Real_acct_owner.zip"),
    )

    # Stream building_res.txt / building_other.txt to identify improved accounts
    improved_accts: set[str] = set()
    max_yr = ctx.max_valid_year
    with zipfile.ZipFile(sdir / "Real_building_land.zip", "r") as zf:
        for member in ("building_res.txt", "building_other.txt"):
            with zf.open(member, "r") as raw_f:
                tf = io.TextIOWrapper(raw_f, encoding="latin-1", errors="replace")
                hdr = [c.strip() for c in tf.readline().rstrip("\r\n").split("\t")]
                col = {name: i for i, name in enumerate(hdr)}
                i_acct = col["acct"]
                i_erect = col["date_erected"] if "date_erected" in col else col["date_erect"]
                area_idxs = [
                    col[k] for k in ("im_sq_ft", "act_ar", "heat_ar", "gross_ar") if k in col
                ]
                for line in tf:
                    parts = line.rstrip("\r\n").split("\t")
                    if len(parts) <= i_acct:
                        continue
                    acct = normalize_hcad13(parts[i_acct])
                    if not acct:
                        continue
                    yr_e = normalize_year(parts[i_erect], max_year=max_yr) if len(parts) > i_erect else 0
                    area_pos = False
                    for ai in area_idxs:
                        if len(parts) > ai:
                            s_ar = parts[ai].strip()
                            if s_ar:
                                try:
                                    if float(s_ar) > 0.0:
                                        area_pos = True
                                        break
                                except ValueError:
                                    pass
                    if yr_e >= MIN_VALID_YEAR or area_pos:
                        improved_accts.add(acct)

    # Stream real_acct.txt for account counts, modal yr, and certified_date coverage
    seen_accts: set[str] = set()
    yr_counter: Counter[int] = Counter()
    certified_count = 0
    with zipfile.ZipFile(sdir / "Real_acct_owner.zip", "r") as zf:
        with zf.open("real_acct.txt", "r") as raw_f:
            tf = io.TextIOWrapper(raw_f, encoding="latin-1", errors="replace")
            hdr = [c.strip() for c in tf.readline().rstrip("\r\n").split("\t")]
            col = {name: i for i, name in enumerate(hdr)}
            i_acct = col["acct"]
            i_yr = col["yr"]
            i_yrimpr = col["yr_impr"]
            i_bldar = col["bld_ar"]
            i_cert = col["certified_date"]
            for line in tf:
                parts = line.rstrip("\r\n").split("\t")
                if len(parts) <= i_acct:
                    continue
                acct = normalize_hcad13(parts[i_acct])
                if not acct or acct in seen_accts:
                    continue
                seen_accts.add(acct)
                if len(parts) > i_yr:
                    s_yr = parts[i_yr].strip()
                    if s_yr.isdigit():
                        yr_counter[int(s_yr)] += 1
                if len(parts) > i_cert:
                    c_iso, c_yr = normalize_hcad_date(
                        parts[i_cert].strip(),
                        allow_fallback=False,
                        min_year=2000,
                        max_year=ctx.tax_year + 2,
                    )
                    if c_iso or c_yr > 0:
                        certified_count += 1
                yr_i = normalize_year(parts[i_yrimpr], max_year=max_yr) if len(parts) > i_yrimpr else 0
                b_ar = 0.0
                if len(parts) > i_bldar and parts[i_bldar].strip():
                    try:
                        b_ar = float(parts[i_bldar].strip())
                    except ValueError:
                        b_ar = 0.0
                if yr_i >= MIN_VALID_YEAR or b_ar > 0.0:
                    improved_accts.add(acct)

    new_acct_count = len(seen_accts)
    new_improved_count = len(improved_accts & seen_accts) if seen_accts else len(improved_accts)
    if new_acct_count == 0:
        raise RollValidationError("Staged real_acct.txt contains 0 valid 13-digit HCAD accounts.")

    modal_tax_year = yr_counter.most_common(1)[0][0] if yr_counter else 0
    if cfg.expected_tax_year_match and modal_tax_year != int(ctx.tax_year):
        raise RollValidationError(
            f"Staged roll modal real_acct.txt:yr is {modal_tax_year}, expected tax_year={ctx.tax_year}."
        )
    certified_ratio = certified_count / max(1, new_acct_count)
    is_certified_roll = certified_ratio >= cfg.min_certified_ratio_for_flag

    # Validate Parcels.zip CRS, feature count, HCAD key column, and EPSG:2278 -> WGS84 bbox centroids
    ds_path, layer_name = resolve_parcels_vector_source(
        sdir / "Parcels.zip", scratch_extract_dir=scratch_extract_dir
    )
    info = pyogrio.read_info(ds_path, layer=layer_name)
    new_parcel_count = int(info.get("features") or 0)
    field_names = [str(f) for f in (info.get("fields") if info.get("fields") is not None else [])]
    field_lower = {f.lower(): f for f in field_names}
    hcad_col = None
    for cand in ("hcad_num_1", "hcad_num", "acct"):
        if cand in field_lower:
            hcad_col = field_lower[cand]
            break
    if hcad_col is None:
        raise RollValidationError(
            f"Parcels layer {layer_name!r} is missing HCAD key column (fields={field_names})."
        )
    src_crs = str(info.get("crs") or "").strip()
    if not src_crs:
        raise RollValidationError(f"Parcels layer {layer_name!r} has no CRS definition.")
    try:
        to_2278 = Transformer.from_crs(src_crs, "EPSG:2278", always_xy=True)
        from_2278_to_4326 = Transformer.from_crs("EPSG:2278", "EPSG:4326", always_xy=True)
    except Exception as exc:
        raise RollValidationError(f"Invalid Parcels CRS {src_crs!r}: {exc}") from exc

    meta_s, _fids_s, sample_wkb, sample_fields = pyogrio.raw.read(
        ds_path,
        layer=layer_name,
        columns=[hcad_col],
        max_features=max(1, int(cfg.sample_parcel_limit)),
        read_geometry=True,
    )
    if new_parcel_count <= 0:
        new_parcel_count = len(sample_wkb)
    if new_parcel_count <= 0:
        raise RollValidationError("Staged Parcels.zip contains 0 parcel polygons.")

    sample_geoms = shapely.from_wkb([w for w in sample_wkb if w is not None])
    valid_samples = [g for g in sample_geoms if g is not None and not g.is_empty]
    if not valid_samples:
        raise RollValidationError("Staged Parcels.zip contains no valid non-empty parcel geometries.")

    min_lng, min_lat, max_lng, max_lat = HARRIS_COUNTY_BBOX
    in_bbox = 0
    for g in valid_samples:
        g_2278 = g if ("2278" in src_crs or "Texas South Central" in src_crs) else _reproject_shapely_geom(g, to_2278)
        if not np.isfinite(g_2278.area) or g_2278.area <= 0.0:
            raise RollValidationError("Parcels sample transformed to non-finite/zero area in EPSG:2278.")
        c_pt = g_2278.centroid
        lon, lat = from_2278_to_4326.transform(float(c_pt.x), float(c_pt.y))
        if min_lng <= lon <= max_lng and min_lat <= lat <= max_lat:
            in_bbox += 1
    bbox_ratio = in_bbox / len(valid_samples)
    if bbox_ratio < cfg.min_in_bbox_ratio:
        raise RollValidationError(
            f"Parcels CRS/bbox check failed: only {in_bbox}/{len(valid_samples)} ({bbox_ratio:.1%}) "
            f"sample centroids landed inside HARRIS_COUNTY_BBOX {HARRIS_COUNTY_BBOX} (crs={src_crs!r})."
        )
    del meta_s, _fids_s, sample_wkb, sample_fields

    acct_retention = (new_acct_count / baseline_acct_count) if baseline_acct_count > 0 else 1.0
    improved_retention = (
        (new_improved_count / baseline_improved_count) if baseline_improved_count > 0 else 1.0
    )
    parcel_retention = (new_parcel_count / baseline_parcel_count) if baseline_parcel_count > 0 else 1.0

    if not allow_partial_roll:
        if baseline_acct_count > 0 and acct_retention < cfg.min_account_retention_ratio:
            raise RollValidationError(
                f"Account retention ratio {acct_retention:.4f} ({new_acct_count:,} vs baseline "
                f"{baseline_acct_count:,}) fell below {cfg.min_account_retention_ratio:.2f}."
            )
        if baseline_improved_count > 0 and improved_retention < cfg.min_improved_retention_ratio:
            raise RollValidationError(
                f"Improved-account retention ratio {improved_retention:.4f} ({new_improved_count:,} vs "
                f"baseline {baseline_improved_count:,}) fell below {cfg.min_improved_retention_ratio:.2f}."
            )
        if baseline_parcel_count > 0 and parcel_retention < cfg.min_parcel_retention_ratio:
            raise RollValidationError(
                f"Parcel polygon retention ratio {parcel_retention:.4f} ({new_parcel_count:,} vs "
                f"baseline {baseline_parcel_count:,}) fell below {cfg.min_parcel_retention_ratio:.2f}."
            )

    return {
        "ok": True,
        "tax_year": int(ctx.tax_year),
        "modal_roll_year": int(modal_tax_year),
        "is_certified_roll": bool(is_certified_roll),
        "certified_account_count": int(certified_count),
        "certified_ratio": round(certified_ratio, 6),
        "new_account_count": int(new_acct_count),
        "new_improved_count": int(new_improved_count),
        "new_parcel_count": int(new_parcel_count),
        "account_retention_ratio": round(acct_retention, 6),
        "improved_retention_ratio": round(improved_retention, 6),
        "parcel_retention_ratio": round(parcel_retention, 6),
        "parcels_crs": src_crs,
        "parcels_layer": layer_name,
        "parcels_hcad_column": hcad_col,
        "sample_in_bbox_ratio": round(bbox_ratio, 6),
        "validated_tables": sorted(set(bldg_headers) | set(acct_headers)),
    }


# ---------------------------------------------------------------------------
# Stage 2: Normalized Account Snapshots, Parcel STRtree & Curation Governance
# ---------------------------------------------------------------------------


def _assemble_numbered_situs(
    site_addr_1: str,
    str_num: str = "",
    str_num_sfx: str = "",
    str_pfx: str = "",
    str_name: str = "",
    str_sfx: str = "",
    str_sfx_dir: str = "",
) -> str:
    """Return a clean uppercase numbered street address, rejecting `'0 <STREET>'` corner reserves."""
    cand1 = re.sub(r"\s+", " ", (site_addr_1 or "").upper().strip())
    if cand1 and VALID_NUMBERED_SITUS_RE.match(cand1):
        return sanitize_atlas_text(cand1)
    num_tok = f"{(str_num or '').strip()}{(str_num_sfx or '').strip()}".upper().strip()
    tokens = [
        num_tok,
        (str_pfx or "").upper().strip(),
        (str_name or "").upper().strip(),
        (str_sfx or "").upper().strip(),
        (str_sfx_dir or "").upper().strip(),
    ]
    cand2 = re.sub(r"\s+", " ", " ".join(t for t in tokens if t)).strip()
    if cand2 and VALID_NUMBERED_SITUS_RE.match(cand2):
        return sanitize_atlas_text(cand2)
    return ""


def _cell(row_parts: list[str], idx: int) -> str:
    return row_parts[idx].strip() if idx >= 0 and len(row_parts) > idx else ""


def stream_hcad_account_snapshots(
    bldg_zip_path: Path,
    acct_zip_path: Path,
    *,
    tax_year: int,
    prior_tax_year: int | None = None,
    cache_dir_for_cama_pkl: Path | None = None,
) -> dict[str, HcadAccountSnapshot]:
    """Stream HCAD CAMA tables into normalized `HcadAccountSnapshot` records with dynamic `max_year` (DF-1)."""
    eff_tax = int(tax_year)
    max_bldg_yr = eff_tax + 1
    max_date_yr = eff_tax
    min_demo_yr = max(MIN_VALID_YEAR, (eff_tax - 1 if prior_tax_year is None else int(prior_tax_year)) - 2)

    snapshots: dict[str, HcadAccountSnapshot] = {}
    best_style_weight: dict[str, tuple[float, int]] = {}

    with zipfile.ZipFile(bldg_zip_path, "r") as zf:
        for member in ("building_res.txt", "building_other.txt"):
            if member not in zf.namelist():
                continue
            with zf.open(member, "r") as raw_f:
                tf = io.TextIOWrapper(raw_f, encoding="latin-1", errors="replace")
                hdr = [c.strip() for c in tf.readline().rstrip("\r\n").split("\t")]
                col = {name: i for i, name in enumerate(hdr)}
                i_acct = col["acct"]
                i_use = col.get("property_use_cd", -1)
                i_struct = col.get("structure_dscr", -1)
                i_dscr = col.get("dscr", -1)
                i_erect = col["date_erected"] if "date_erected" in col else col["date_erect"]
                i_eff = col["eff"] if "eff" in col else col.get("eff_yr", -1)
                i_remod = col.get("yr_remodel", -1)
                i_notes = col.get("notes", -1)
                area_cols = [
                    col[k] for k in ("im_sq_ft", "act_ar", "heat_ar", "gross_ar") if k in col
                ]

                for line in tf:
                    parts = line.rstrip("\r\n").split("\t")
                    if len(parts) <= i_acct:
                        continue
                    acct = normalize_hcad13(parts[i_acct])
                    if not acct:
                        continue
                    snap = snapshots.get(acct)
                    if snap is None:
                        snap = HcadAccountSnapshot(acct=acct)
                        snapshots[acct] = snap

                    yr_e = normalize_year(parts[i_erect], max_year=max_bldg_yr) if len(parts) > i_erect else 0
                    yr_rem = (
                        normalize_year(parts[i_remod], max_year=max_bldg_yr)
                        if i_remod >= 0 and len(parts) > i_remod
                        else 0
                    )
                    yr_eff = (
                        normalize_year(parts[i_eff], max_year=max_bldg_yr)
                        if i_eff >= 0 and len(parts) > i_eff
                        else 0
                    )
                    row_area = 0.0
                    for ai in area_cols:
                        if len(parts) > ai:
                            s_a = parts[ai].strip()
                            if s_a:
                                try:
                                    f_a = float(s_a)
                                    if f_a > 0.0:
                                        row_area = f_a
                                        break
                                except ValueError:
                                    pass

                    if yr_e >= MIN_VALID_YEAR or row_area > 0.0:
                        snap.building_count += 1
                    if yr_e >= MIN_VALID_YEAR:
                        if snap.earliest_year == 0 or yr_e < snap.earliest_year:
                            snap.earliest_year = yr_e
                        if yr_e > snap.latest_year:
                            snap.latest_year = yr_e
                    if yr_rem >= MIN_VALID_YEAR and yr_rem > snap.max_remodel_year:
                        snap.max_remodel_year = yr_rem
                    if yr_eff >= MIN_VALID_YEAR and yr_eff > snap.eff_year:
                        snap.eff_year = yr_eff
                    if row_area > 0.0:
                        snap.total_bld_area += row_area

                    use_cd = parts[i_use].strip() if i_use >= 0 and len(parts) > i_use else ""
                    if use_cd and not snap.property_use_cd:
                        snap.property_use_cd = use_cd
                    s_dscr = (
                        sanitize_atlas_text(parts[i_struct].strip())
                        if i_struct >= 0 and len(parts) > i_struct
                        else ""
                    )
                    q_dscr = (
                        sanitize_atlas_text(parts[i_dscr].strip())
                        if i_dscr >= 0 and len(parts) > i_dscr
                        else ""
                    )
                    raw_note = parts[i_notes].strip() if i_notes >= 0 and len(parts) > i_notes else ""
                    note_s, note_is_moved = normalize_hcad_appraiser_note(raw_note)
                    style_cand = s_dscr or q_dscr
                    weight = (row_area, -(yr_e if yr_e >= MIN_VALID_YEAR else 9999))
                    if style_cand and (acct not in best_style_weight or weight > best_style_weight[acct]):
                        best_style_weight[acct] = weight
                        snap.bld_style = style_cand
                        snap.structure_dscr = s_dscr
                        snap.quality_desc = q_dscr
                    if note_s and not snap.cama_notes:
                        snap.cama_notes = note_s
                    if note_is_moved and not snap.was_relocated:
                        snap.was_relocated = True
                        snap.relocation_note = sanitize_atlas_text(
                            f"HCAD Appraiser Relocation Record: {note_s}"
                        )[:240]

    with zipfile.ZipFile(acct_zip_path, "r") as zf:
        with zf.open("real_acct.txt", "r") as raw_f:
            tf = io.TextIOWrapper(raw_f, encoding="latin-1", errors="replace")
            hdr = [c.strip() for c in tf.readline().rstrip("\r\n").split("\t")]
            col = {name: i for i, name in enumerate(hdr)}
            i_acct = col["acct"]
            i_yr = col["yr"]
            i_mailto = col["mailto"]
            i_mail1 = col.get("mail_addr_1", -1)
            i_mail2 = col.get("mail_addr_2", -1)
            i_mcity = col.get("mail_city", -1)
            i_mstate = col.get("mail_state", -1)
            i_mzip = col.get("mail_zip", -1)
            i_spfx = col.get("str_pfx", -1)
            i_snum = col["str_num"]
            i_snumsfx = col.get("str_num_sfx", -1)
            i_sname = col["str"]
            i_ssfx = col.get("str_sfx", -1)
            i_ssfxdir = col.get("str_sfx_dir", -1)
            i_addr1 = col["site_addr_1"]
            i_addr3 = col.get("site_addr_3", -1)
            i_sc = col["state_class"]
            i_nbhd = col.get("Neighborhood_Code", -1)
            i_mkt = col.get("Market_Area_1", -1)
            i_yrimpr = col["yr_impr"]
            i_splt = col["splt_dt"]
            i_bldar = col["bld_ar"]
            i_landar = col["land_ar"]
            i_bldval = col["bld_val"]
            i_cert = col["certified_date"]
            i_newown = col.get("new_own_dt", -1)
            i_lgl1 = col["lgl_1"]
            i_lgl2 = col["lgl_2"]

            for line in tf:
                parts = line.rstrip("\r\n").split("\t")
                if len(parts) <= i_acct:
                    continue
                acct = normalize_hcad13(parts[i_acct])
                if not acct:
                    continue
                snap = snapshots.get(acct)
                if snap is None:
                    snap = HcadAccountSnapshot(acct=acct)
                    snapshots[acct] = snap

                s_yr = _cell(parts, i_yr)
                snap.roll_yr = int(s_yr) if s_yr.isdigit() else 0
                snap.owner = sanitize_atlas_text(_cell(parts, i_mailto))
                snap.mail_addr_1 = sanitize_atlas_text(_cell(parts, i_mail1))
                snap.mail_addr_2 = sanitize_atlas_text(_cell(parts, i_mail2))
                snap.mail_city = sanitize_atlas_text(_cell(parts, i_mcity))
                snap.mail_state = sanitize_atlas_text(_cell(parts, i_mstate))
                snap.mail_zip = _cell(parts, i_mzip)[:10]
                raw_addr1 = _cell(parts, i_addr1)
                snap.raw_site_addr_1 = sanitize_atlas_text(raw_addr1)
                snap.numbered_address = _assemble_numbered_situs(
                    raw_addr1,
                    str_num=_cell(parts, i_snum),
                    str_num_sfx=_cell(parts, i_snumsfx),
                    str_pfx=_cell(parts, i_spfx),
                    str_name=_cell(parts, i_sname),
                    str_sfx=_cell(parts, i_ssfx),
                    str_sfx_dir=_cell(parts, i_ssfxdir),
                )
                snap.zip_code = _cell(parts, i_addr3)[:5]
                snap.state_class = _cell(parts, i_sc).upper()
                snap.neighborhood_code = _cell(parts, i_nbhd)
                snap.market_area_1 = _cell(parts, i_mkt)
                snap.yr_impr = normalize_year(_cell(parts, i_yrimpr), max_year=max_bldg_yr)
                snap.splt_dt = _cell(parts, i_splt)
                snap.new_own_dt = _cell(parts, i_newown)
                snap.certified_date = _cell(parts, i_cert)
                snap.subdivision = sanitize_atlas_text(_cell(parts, i_lgl1))
                snap.lgl_2 = sanitize_atlas_text(_cell(parts, i_lgl2))

                for attr, idx_f in (
                    ("land_area_sqft", i_landar),
                    ("bld_val", i_bldval),
                ):
                    raw_v = _cell(parts, idx_f)
                    if raw_v:
                        try:
                            setattr(snap, attr, max(0.0, float(raw_v)))
                        except ValueError:
                            pass

                raw_bar = _cell(parts, i_bldar)
                if raw_bar:
                    try:
                        acct_bld_ar = max(0.0, float(raw_bar))
                        if acct_bld_ar > snap.total_bld_area:
                            snap.total_bld_area = acct_bld_ar
                    except ValueError:
                        pass

                snap.use_category = classify_use_category(
                    snap.state_class or snap.property_use_cd,
                    snap.structure_dscr or snap.bld_style,
                )

        if "permits.txt" in zf.namelist():
            with zf.open("permits.txt", "r") as raw_f:
                tf = io.TextIOWrapper(raw_f, encoding="latin-1", errors="replace")
                hdr = [c.strip() for c in tf.readline().rstrip("\r\n").split("\t")]
                col = {name: i for i, name in enumerate(hdr)}
                ip_acct = col["acct"]
                ip_type = col["permit_type"]
                ip_tp_dscr = col.get("permit_tp_descr", -1)
                ip_dscr = col["dscr"]
                ip_dt = col["issue_date"]
                ip_yr = col["yr"]
                ip_snum = col.get("site_num", -1)
                ip_spfx = col.get("site_pfx", -1)
                ip_sstr = col.get("site_str", -1)
                ip_stp = col.get("site_tp", -1)
                ip_apt = col.get("site_apt", -1)

                for line in tf:
                    parts = line.rstrip("\r\n").split("\t")
                    if len(parts) <= ip_acct:
                        continue
                    acct = normalize_hcad13(parts[ip_acct])
                    snap = snapshots.get(acct)
                    if snap is None:
                        continue
                    ptype = parts[ip_type].strip() if len(parts) > ip_type else ""
                    dscr = parts[ip_dscr].strip() if len(parts) > ip_dscr else ""
                    tp_dscr = (
                        parts[ip_tp_dscr].strip()
                        if ip_tp_dscr >= 0 and len(parts) > ip_tp_dscr
                        else ""
                    )
                    dt_str = parts[ip_dt].strip() if len(parts) > ip_dt else ""
                    roll_yr_str = parts[ip_yr].strip() if len(parts) > ip_yr else ""
                    apt_str = parts[ip_apt].strip() if ip_apt >= 0 and len(parts) > ip_apt else ""
                    addr_tokens = [
                        parts[idx].strip()
                        for idx in (ip_snum, ip_spfx, ip_sstr, ip_stp)
                        if idx >= 0 and len(parts) > idx and parts[idx].strip()
                    ]
                    p_addr = " ".join(addr_tokens)

                    if ptype == "15" and "OMITTED IMPS" not in dscr.upper():
                        _, rel_yr = normalize_hcad_date(
                            dt_str, roll_yr_str, allow_fallback=False, max_year=max_date_yr
                        )
                        snap.was_relocated = True
                        snap.relocation_note = sanitize_atlas_text(
                            f"Relocated permit ({rel_yr or 'recorded'}): {dscr or tp_dscr}".strip(": ")
                        )

                    is_main_demo, demo_yr, _reason = classify_main_structure_demolition_permit(
                        dscr,
                        ptype,
                        dt_str,
                        raw_address=p_addr,
                        site_apt=apt_str,
                        permit_desc=tp_dscr,
                        min_year=min_demo_yr,
                        max_year=max_date_yr,
                    )
                    if is_main_demo and demo_yr >= snap.demo_permit_year:
                        snap.has_main_demo_permit = True
                        snap.demo_permit_year = demo_yr
                        snap.demo_permit_type = ptype
                        snap.demo_permit_date = dt_str
                        snap.demo_permit_desc = sanitize_atlas_text(dscr or tp_dscr)
                        snap.demo_permit_addr = sanitize_atlas_text(p_addr)
                        snap.demo_permit_apt = sanitize_atlas_text(apt_str)

    # Hydrate single-source 6-tier ownership & portfolio classifications (DF-1, DF-2)
    try:
        from atlas_pipeline.build_ownership_and_history import build_ownership_graph

        own_graph = build_ownership_graph(
            acct_zip_path,
            tax_year=eff_tax,
            include_owners_table=True,
            include_permits_table=False,
        )
        for acct_k, orec in (own_graph.get("records") or {}).items():
            sn_target = snapshots.get(acct_k)
            if sn_target is not None:
                sn_target.own_tier = orec.own_tier
                sn_target.port_id = orec.port_id
                sn_target.display_mail = orec.display_mail
                sn_target.care_of = orec.care_of
                sn_target.is_protected_homestead = orec.is_protected_homestead
                sn_target.is_sec_25_025_confidential = orec.is_sec_25_025_confidential
                if orec.is_sec_25_025_confidential:
                    sn_target.owner = "CONFIDENTIAL OWNER"
                sn_target.co_owners = list(orec.co_owners)
    except Exception:
        pass

    if cache_dir_for_cama_pkl is not None:
        compact_lookup = {acct: snap.to_cama_tuple() for acct, snap in snapshots.items()}
        save_compact_cama_lookup(Path(cache_dir_for_cama_pkl), compact_lookup, tax_year=eff_tax)

    return snapshots


def load_parcel_geometries_2278(
    parcels_zip_path: Path,
    *,
    scratch_extract_dir: Path | None = None,
) -> tuple[list[str], np.ndarray, dict[str, int], STRtree]:
    """Load unique 13-digit parcel polygons in `EPSG:2278` plus C-level `STRtree`."""
    ds_path, layer_name = resolve_parcels_vector_source(
        parcels_zip_path, scratch_extract_dir=scratch_extract_dir
    )
    meta, _fids, geom_wkb, field_data = pyogrio.raw.read(
        ds_path,
        layer=layer_name,
        read_geometry=True,
    )
    fields_list = [str(f) for f in (meta.get("fields") if meta.get("fields") is not None else [])]
    f_idx = {f.lower(): i for i, f in enumerate(fields_list)}
    hcad_idx = None
    for cand in ("hcad_num_1", "hcad_num", "acct"):
        if cand in f_idx:
            hcad_idx = f_idx[cand]
            break
    if hcad_idx is None:
        raise RollValidationError(f"Missing HCAD column in {ds_path} (fields={fields_list}).")

    raw_hcads = field_data[hcad_idx]
    keep_rows: list[int] = []
    parcel_hcads: list[str] = []
    hcad_to_idx: dict[str, int] = {}
    for row_i in range(len(raw_hcads)):
        if geom_wkb[row_i] is None:
            continue
        hcad = normalize_hcad13(raw_hcads[row_i])
        if not hcad or hcad in hcad_to_idx:
            continue
        hcad_to_idx[hcad] = len(parcel_hcads)
        parcel_hcads.append(hcad)
        keep_rows.append(row_i)

    keep_wkb = geom_wkb[np.asarray(keep_rows, dtype=np.int64)]
    del geom_wkb, field_data, _fids
    geoms = shapely.from_wkb(keep_wkb)
    del keep_wkb

    src_crs = str(meta.get("crs") or "").strip()
    if src_crs and "2278" not in src_crs and "Texas South Central" not in src_crs:
        to_2278 = Transformer.from_crs(src_crs, "EPSG:2278", always_xy=True)
        geoms = np.asarray([_reproject_shapely_geom(g, to_2278) for g in geoms], dtype=object)

    invalid_mask = ~shapely.is_valid(geoms)
    if np.any(invalid_mask):
        geoms = np.where(invalid_mask, shapely.make_valid(geoms), geoms)

    return parcel_hcads, geoms, hcad_to_idx, STRtree(geoms)


def _unwrap_overrides_map(curated_overrides: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(curated_overrides, dict):
        return {}
    if isinstance(curated_overrides.get("overrides"), dict):
        return curated_overrides["overrides"]
    return {
        k: v
        for k, v in curated_overrides.items()
        if isinstance(v, dict)
        and k not in ("schema_version", "description", "googleSheetCsvUrl", "submissionWebhookUrl")
    }


def _override_record_is_vacant(rec: dict[str, Any]) -> bool:
    return is_demolished_vacant_marker(
        rec.get("year_built"),
        footprint_issue=str(rec.get("footprint_issue") or ""),
        footprint_notes=str(rec.get("footprint_notes") or ""),
        source_type=str(rec.get("source_type") or ""),
        source_citation=str(rec.get("source_citation") or ""),
        suppress_only=rec.get("suppress_only"),
    )


def build_override_protection_index(
    curated_overrides: dict[str, Any] | None,
    *,
    default_updated_year: int | None = None,
) -> dict[str, Any]:
    """Index standing curated IDs/HCADs, sub-building splits, and suppressed vacant lots (DF-2)."""
    eff_default_yr = _schema.ACTIVE_TAX_YEAR if default_updated_year is None else int(default_updated_year)
    ov_map = _unwrap_overrides_map(curated_overrides)
    standing_feature_ids: set[str] = set()
    standing_parcel_hcads: set[str] = set()
    full_replace_hcads: set[str] = set()
    partial_subbuilding_hcads: set[str] = set()
    keep_shard_footprint_hcads: set[str] = set()
    suppressed_shard_hcads: set[str] = set()
    suppressed_vacant_by_hcad: dict[str, dict[str, Any]] = {}
    overrides_by_hcad: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)

    for raw_key, rec in ov_map.items():
        if not isinstance(rec, dict):
            continue
        key = str(raw_key).strip()
        hcad = normalize_hcad13(rec.get("hcad_num") or key.split("#")[0])
        if hcad:
            overrides_by_hcad[hcad].append((key, rec))
        for sup_h in rec.get("suppress_shard_hcads") or ():
            sh = normalize_hcad13(sup_h)
            if sh:
                suppressed_shard_hcads.add(sh)

        is_sub = "#" in key or bool(rec.get("is_building_override"))
        if _override_record_is_vacant(rec):
            if hcad and not is_sub:
                upd_str = str(rec.get("updated_at") or rec.get("verified_at") or "")
                m_yr = re.search(r"\b(20\d{2})\b", upd_str)
                upd_yr = int(m_yr.group(1)) if m_yr else eff_default_yr
                suppressed_vacant_by_hcad[hcad] = {
                    "key": key,
                    "updated_year": upd_yr,
                    "record": rec,
                }
            continue

        standing_feature_ids.add(key)
        for id_field in ("id", "building_id"):
            val = str(rec.get(id_field) or "").strip()
            if val:
                standing_feature_ids.add(val)

        if not hcad:
            continue
        if rec.get("keep_shard_footprints") is True:
            keep_shard_footprint_hcads.add(hcad)
            partial_subbuilding_hcads.add(hcad)
            if not is_sub:
                standing_parcel_hcads.add(hcad)
        elif is_sub:
            if rec.get("replace_parcel_shards") is True:
                full_replace_hcads.add(hcad)
                standing_parcel_hcads.add(hcad)
            else:
                partial_subbuilding_hcads.add(hcad)
        else:
            standing_parcel_hcads.add(hcad)
            if rec.get("replace_parcel_shards") is True or (
                rec.get("geometry") and not rec.get("keep_shard_footprints")
            ):
                full_replace_hcads.add(hcad)

    return {
        "overrides_map": ov_map,
        "overrides_by_hcad": overrides_by_hcad,
        "standing_feature_ids": standing_feature_ids,
        "standing_parcel_hcads": standing_parcel_hcads,
        "full_replace_hcads": full_replace_hcads,
        "partial_subbuilding_hcads": partial_subbuilding_hcads,
        "keep_shard_footprint_hcads": keep_shard_footprint_hcads,
        "suppressed_shard_hcads": suppressed_shard_hcads,
        "suppressed_vacant_by_hcad": suppressed_vacant_by_hcad,
    }


def is_feature_chronology_protected(
    fid: str,
    props: dict[str, Any],
    overrides_or_index: dict[str, Any] | None,
) -> bool:
    """Return True whenever a feature's chronology/metadata is protected by Tier 1/2 curation (DF-2)."""
    if overrides_or_index and "standing_feature_ids" in overrides_or_index:
        idx = overrides_or_index
    else:
        idx = build_override_protection_index(overrides_or_index)

    clean_fid = str(fid or "").strip()
    bid = str(props.get("building_id") or "").strip()
    pid = str(props.get("id") or "").strip()
    hcad = normalize_hcad13(props.get("hcad_num") or (clean_fid.split("#")[0] if clean_fid else ""))

    for cand in (clean_fid, bid, pid):
        if cand and cand in idx["standing_feature_ids"]:
            return True
        if "#" in cand:
            slug = cand.split("#", 1)[1]
            if slug.startswith(CURATED_CAMPUS_SLUG_PREFIXES):
                return True

    if hcad and hcad in idx["standing_parcel_hcads"]:
        return True
    if bool(props.get("is_curated_override")):
        return True
    yr_src = str(props.get("year_source") or "").strip()
    if yr_src in PROTECTED_CHRONOLOGY_SOURCES:
        return True
    src_tp = str(props.get("source_type") or "").strip()
    if src_tp == "COH Landmark Designation Report":
        return True
    return False


def is_school_situs_protected(props: dict[str, Any]) -> bool:
    """Guard hydrated school campus addresses against `'0 <STREET>'` corner regressions (DF-2)."""
    return props.get("school_situs_hydrated") is True


def classify_parcel_footprint_governance(
    hcad_num: str,
    shard_features: list[dict[str, Any]],
    curated_overrides_or_index: dict[str, Any] | None,
) -> str:
    """Classify parcel footprint governance into one of the 4 `ParcelGovernance` modes (DF-2)."""
    if curated_overrides_or_index and "standing_feature_ids" in curated_overrides_or_index:
        idx = curated_overrides_or_index
    else:
        idx = build_override_protection_index(curated_overrides_or_index)

    hcad = normalize_hcad13(hcad_num)
    if hcad in idx["full_replace_hcads"] or hcad in idx["suppressed_vacant_by_hcad"]:
        return ParcelGovernance.FULL_CURATED_REPLACE.value
    if hcad in idx["partial_subbuilding_hcads"] or hcad in idx["keep_shard_footprint_hcads"]:
        return ParcelGovernance.PARTIAL_CURATED_SUBBUILDINGS.value

    has_protected = False
    for feat in shard_features:
        fp = feat.get("properties") or {}
        fid = str(fp.get("building_id") or fp.get("id") or hcad)
        if is_feature_chronology_protected(fid, fp, idx):
            has_protected = True
            if "#" in fid or len(shard_features) > 1:
                return ParcelGovernance.PARTIAL_CURATED_SUBBUILDINGS.value

    if len(shard_features) > 1:
        return ParcelGovernance.UNCURATED_MULTI_FOOTPRINT.value
    if has_protected and shard_features:
        fp0 = shard_features[0].get("properties") or {}
        fid0 = str(fp0.get("building_id") or fp0.get("id") or "")
        if "#" in fid0:
            return ParcelGovernance.PARTIAL_CURATED_SUBBUILDINGS.value
    return ParcelGovernance.SINGLE_FOOTPRINT.value



# ---------------------------------------------------------------------------
# Stage 3: Provenance-Partitioned 5-Category Delta Engine & Replat Resolver
# ---------------------------------------------------------------------------


def assign_quadrant(lon: float, lat: float) -> str:
    """Assign WGS84 `(lon, lat)` to one of the 5 Zoom-11-aligned PMTiles shard quadrants."""
    if lat >= SPLIT_LAT:
        if lon < SPLIT_LON_WEST:
            return "nw_w"
        if lon < SPLIT_LON_EAST:
            return "nw_e"
        return "ne"
    if lon < SPLIT_LON_EAST:
        return "sw"
    return "se"


def _feature_centroid_wgs84(geom: dict[str, Any] | None) -> tuple[float, float] | None:
    if not isinstance(geom, dict):
        return None
    gtype = geom.get("type")
    coords = geom.get("coordinates") or []
    ring: list[Any] = []
    if gtype == "Polygon" and coords and coords[0]:
        ring = coords[0]
    elif gtype == "MultiPolygon" and coords and coords[0] and coords[0][0]:
        ring = coords[0][0]
    elif gtype == "Point" and len(coords) >= 2:
        return (float(coords[0]), float(coords[1]))
    if not ring:
        return None
    pts = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
    n = max(1, len(pts))
    return (
        sum(float(p[0]) for p in pts) / n,
        sum(float(p[1]) for p in pts) / n,
    )


def _sample_pmtiles_features_for_dry_run(pmtiles_path: Path, max_tiles: int = 96) -> list[dict[str, Any]]:
    """Decode baseline vector-tile building features strictly for `--dry-run` fallback mode."""
    import gzip as _gzip

    import mapbox_vector_tile
    from pmtiles.reader import MmapSource, Reader as PmtilesReader, all_tiles

    seen_ids: set[str] = set()
    sampled: list[dict[str, Any]] = []
    with open(pmtiles_path, "rb") as fh:
        reader = PmtilesReader(MmapSource(fh))
        hdr = reader.header()
        target_z = int(hdr.get("max_zoom") or 15)
        tiles_decoded = 0
        for (z, x, y), raw_tile in all_tiles(reader.get_bytes):
            if z != target_z:
                continue
            payload = _gzip.decompress(raw_tile) if raw_tile[:2] == b"\x1f\x8b" else raw_tile
            decoded = mapbox_vector_tile.decode(payload)
            layer = decoded.get("buildings") or next(iter(decoded.values()), None)
            if not layer:
                continue
            extent = float(layer.get("extent") or 4096.0)
            n_tiles = 2.0 ** z
            for vf in layer.get("features") or ():
                vp = dict(vf.get("properties") or {})
                fid = str(vp.get("building_id") or vp.get("id") or vp.get("hcad_num") or "")
                if not fid or fid in seen_ids:
                    continue
                vg = vf.get("geometry") or {}
                if vg.get("type") != "Polygon":
                    continue
                raw_rings = vg.get("coordinates") or []
                if not raw_rings or not raw_rings[0]:
                    continue
                wgs_ring: list[list[float]] = []
                for px, py in raw_rings[0]:
                    fx = (float(x) + float(px) / extent) / n_tiles
                    fy = (float(y) + (extent - float(py)) / extent) / n_tiles
                    lon = fx * 360.0 - 180.0
                    lat_rad = math.atan(math.sinh(math.pi * (1.0 - 2.0 * fy)))
                    lat = math.degrees(lat_rad)
                    wgs_ring.append([round(lon, 6), round(lat, 6)])
                nr = normalize_ring_6dp(wgs_ring, ccw=True)
                if not nr:
                    continue
                seen_ids.add(fid)
                sampled.append({
                    "type": "Feature",
                    "id": fid,
                    "properties": vp,
                    "geometry": {"type": "Polygon", "coordinates": [nr]},
                })
            tiles_decoded += 1
            if tiles_decoded >= max_tiles:
                break
    return sampled


def load_baseline_quadrant_features(
    cache_dir: Path,
    output_dir: Path,
    *,
    allow_pmtiles_fallback: bool = False,
    apply_mode: bool = False,
) -> tuple[dict[str, list[dict[str, Any]]], bool]:
    """Load baseline building shard features by quadrant (`aligned_ndjson/*.geojsonseq`).

    Under `--apply`, missing `.geojsonseq` shards fail closed so PMTiles are never overwritten
    from partial tile samples. Under `--dry-run --allow-pmtiles-fallback`, missing `.geojsonseq`
    shards may fall back to zoom-15 vector-tile feature sampling for operator preview reports.
    """
    aligned_dir = Path(cache_dir) / "aligned_ndjson"
    ddir = Path(output_dir)

    manifest_shards: list[str] = []
    manifest_path = ddir / "pmtiles_manifest.json"
    if manifest_path.exists():
        try:
            mdoc = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(mdoc, dict) and isinstance(mdoc.get("building_shards"), list):
                manifest_shards = [str(s) for s in mdoc["building_shards"]]
        except Exception:
            manifest_shards = []

    expected_quads: list[str] = []
    for sname in manifest_shards:
        m = re.fullmatch(r"houston_buildings_([a-z_]+)\.pmtiles", sname)
        if m and m.group(1) in QUAD_NAMES:
            expected_quads.append(m.group(1))
    if not expected_quads:
        expected_quads = [q for q in QUAD_NAMES if (aligned_dir / f"buildings_{q}.geojsonseq").exists()]
    if not expected_quads:
        expected_quads = list(QUAD_NAMES)

    by_quad: dict[str, list[dict[str, Any]]] = {q: [] for q in QUAD_NAMES}
    used_pmtiles_fallback = False

    for q in expected_quads:
        seq_path = aligned_dir / f"buildings_{q}.geojsonseq"
        if seq_path.exists():
            feats_q: list[dict[str, Any]] = []
            with open(seq_path, "rb") as fh:
                for raw_line in fh:
                    ln = raw_line[1:] if raw_line.startswith(b"\x1e") else raw_line
                    ln = ln.strip()
                    if ln:
                        feats_q.append(orjson.loads(ln))
            by_quad[q] = feats_q
            continue

        if apply_mode or not allow_pmtiles_fallback:
            raise RollValidationError(
                f"Missing baseline shard {seq_path}. Full `.geojsonseq` shards are mandatory "
                f"for `--apply` (pass `--allow-pmtiles-fallback` only with `--dry-run`)."
            )
        pm_path = ddir / f"houston_buildings_{q}.pmtiles"
        if pm_path.exists():
            by_quad[q] = _sample_pmtiles_features_for_dry_run(pm_path)
            used_pmtiles_fallback = True
        else:
            raise RollValidationError(
                f"Missing both baseline {seq_path} and fallback {pm_path}."
            )

    return by_quad, used_pmtiles_fallback


def _split_numbered_address_for_shard(raw_address: str) -> tuple[str, str, str]:
    clean = re.sub(r"\s+", " ", sanitize_atlas_text(str(raw_address or ""))).strip().upper()
    m = VALID_NUMBERED_SITUS_RE.match(clean)
    if not m:
        return "", "", ""
    hn_s, st_n = m.group(1), m.group(2)
    sh_k = _compute_address_shard_key(hn_s)
    return sh_k, st_n, hn_s


def _record_addr_prefix_migration(
    delta: ArtifactSyncDelta,
    entity_id: str,
    old_address: str,
    new_address: str = "",
) -> None:
    old_k, _old_st, _old_hn = _split_numbered_address_for_shard(old_address)
    new_k, _new_st, _new_hn = _split_numbered_address_for_shard(new_address) if new_address else ("", "", "")
    ent = str(entity_id or "").strip()
    if old_k and old_k != new_k and ent:
        delta.migrated_addr_prefixes_by_key[old_k].add(
            (ent, sanitize_atlas_text(old_address).upper())
        )


def _disambiguate_replat_building_id(
    old_fid: str,
    retired_hcad: str,
    new_hcad: str,
    used_ids: set[str],
) -> str:
    """Produce a deterministic, globally unique `building_id` across `1 -> 1` and `M -> 1` replats (DF-2)."""
    tail4 = retired_hcad[-4:] if len(retired_hcad) >= 4 else retired_hcad
    if "#" in old_fid:
        slug = old_fid.split("#", 1)[1]
        base_cand = f"{new_hcad}#{slug}"
        if base_cand not in used_ids:
            return base_cand
        alt = f"{new_hcad}#{slug}_{tail4}"
        if alt not in used_ids:
            return alt
        n = 2
        while f"{alt}_{n}" in used_ids:
            n += 1
        return f"{alt}_{n}"

    if new_hcad not in used_ids:
        return new_hcad
    alt_bare = f"{new_hcad}#m_{tail4}"
    if alt_bare not in used_ids:
        return alt_bare
    n = 2
    while f"{alt_bare}_{n}" in used_ids:
        n += 1
    return f"{alt_bare}_{n}"


def _maybe_build_cohort5_candidate(
    hcad: str,
    prior_props: dict[str, Any],
    geom: dict[str, Any] | None,
    snap: HcadAccountSnapshot | None,
    *,
    detection_basis: str,
    tax_year: int,
    prior_tax_year: int,
    citydir_by_hcad: dict[str, Any] | None = None,
    partial_campus_surviving: bool = False,
) -> dict[str, Any] | None:
    """Screen a pre-1940 annual loss through Cohort 5 filters into a read-only candidate dict."""
    if partial_campus_surviving:
        return None
    if snap is not None and snap.was_relocated:
        return None

    prior_yr = int(prior_props.get("year_built") or 0)
    prior_src = str(prior_props.get("year_source") or "").strip()
    cd_map = citydir_by_hcad or {}
    cd_rec = cd_map.get(hcad) if isinstance(cd_map, dict) else None
    cd_yr = int((cd_rec or {}).get("e") or 0) if isinstance(cd_rec, dict) else 0

    has_auth_pre1940 = (
        MIN_VALID_YEAR <= prior_yr < 1940 and prior_src not in INFERRED_FALLBACK_YEAR_SOURCES
    ) or (MIN_VALID_YEAR <= cd_yr < 1940)
    if not has_auth_pre1940:
        return None

    hist_yr = prior_yr if (MIN_VALID_YEAR <= prior_yr < 1940) else cd_yr
    addr = sanitize_atlas_text(
        str(prior_props.get("address") or (snap.numbered_address if snap else "") or "")
    )
    center = _feature_centroid_wgs84(geom) or (0.0, 0.0)

    if snap is not None and snap.has_main_demo_permit:
        ok_c5, meta_c5 = evaluate_cohort5_teardown_candidate(
            hcad,
            permit_description=snap.demo_permit_desc or "DEMOLISH RESIDENCE",
            permit_type=snap.demo_permit_type,
            permit_issue_date=snap.demo_permit_date,
            permit_address=snap.demo_permit_addr or addr,
            permit_site_apt=snap.demo_permit_apt,
            permit_desc=snap.demo_permit_desc,
            cama_year_built=int(snap.earliest_year or 0),
            cama_year_remodel=int(snap.max_remodel_year or 0),
            cama_bld_area=float(snap.total_bld_area or 0.0),
            cama_state_class=snap.state_class,
            citydir_earliest_year=cd_yr,
            pre_demo_survey_year=hist_yr,
            was_relocated=snap.was_relocated,
            min_historic_year=MIN_VALID_YEAR,
            max_historic_year=1939,
            max_year=tax_year,
        )
        if not ok_c5:
            return None
        return {
            "hcad_num": hcad,
            "building_id": str(prior_props.get("building_id") or prior_props.get("id") or hcad),
            "address": addr,
            "historic_district": sanitize_atlas_text(str(prior_props.get("historic_district") or "")),
            "year_built": int(meta_c5["year_built"]),
            "year_built_qualifier": str(meta_c5["year_built_qualifier"]),
            "year_demolished": int(meta_c5["year_demolished"]),
            "age_source": str(meta_c5["age_source"]),
            "provenance_tier": int(meta_c5["provenance_tier"]),
            "post_demo_status": str(meta_c5["post_demo_status"]),
            "detection_basis": detection_basis,
            "demo_permit_date": snap.demo_permit_date,
            "demo_permit_desc": snap.demo_permit_desc,
            "cama_state_class": snap.state_class,
            "auto_graduate": True,
            "graduation_status": "auto_graduated_demo_permit_paired",
            "lon": round(float(center[0]), 6),
            "lat": round(float(center[1]), 6),
        }

    # Annual roll delta clearance / replat teardown with authoritative pre-1940 survey provenance.
    # Per the Two-Key auto-graduation rule, bare $0 improvement flips without a paired main-structure
    # demolition permit are held in the review queue (`auto_graduate=False`) rather than auto-graduated.
    post_status = "vacant_lot"
    if snap is not None and snap.earliest_year >= max(2020, prior_tax_year - 3):
        post_status = f"rebuilt_{snap.earliest_year}"
    elif detection_basis.startswith("replat"):
        post_status = "replatted_redeveloped"
    return {
        "hcad_num": hcad,
        "building_id": str(prior_props.get("building_id") or prior_props.get("id") or hcad),
        "address": addr,
        "historic_district": sanitize_atlas_text(str(prior_props.get("historic_district") or "")),
        "year_built": int(hist_yr),
        "year_built_qualifier": "before" if (cd_yr and hist_yr == cd_yr and prior_yr >= 1940) else "exact",
        "year_demolished": int(tax_year),
        "age_source": "city_directory" if (cd_yr and cd_yr <= hist_yr) else "pre_demo_survey",
        "provenance_tier": 2,
        "post_demo_status": post_status,
        "detection_basis": detection_basis,
        "demo_permit_date": "",
        "demo_permit_desc": "",
        "cama_state_class": snap.state_class if snap is not None else "",
        "auto_graduate": False,
        "graduation_status": "hold_for_review_no_demo_permit",
        "lon": round(float(center[0]), 6),
        "lat": round(float(center[1]), 6),
    }


def resolve_replat_lineage(
    retired_hcad: str,
    old_features: list[dict[str, Any]],
    *,
    new_accounts: dict[str, HcadAccountSnapshot],
    parcel_hcads: list[str],
    parcel_geoms_2278: Any,
    parcel_hcad_to_idx: dict[str, int],
    parcel_strtree: STRtree | None,
    tieback_map: dict[str, list[str]],
    overrides_index: dict[str, Any],
    used_building_ids: set[str],
    prior_tax_year: int,
    tax_year: int,
    citydir_by_hcad: dict[str, Any] | None = None,
    to_2278: Transformer | None = None,
) -> dict[str, Any]:
    """Reconcile retired HCAD accounts across `1 -> 1`, `M -> 1`, and `1 -> K` replats (DF-2)."""
    proj_2278 = to_2278 or Transformer.from_crs("EPSG:4326", "EPSG:2278", always_xy=True)
    kept_features: list[dict[str, Any]] = []
    covered_new_hcads: set[str] = set()
    deleted_building_ids: set[str] = set()
    old_to_new_hcads: dict[str, str] = {}
    old_to_all_new_hcads: dict[str, list[str]] = {}
    old_fid_to_new_hcad: dict[str, str] = {}
    old_to_new_building_ids: dict[str, str] = {}
    orphan_records: list[dict[str, Any]] = []
    cohort5_candidates: list[dict[str, Any]] = []
    migrated_entity_addresses: list[tuple[str, str, str]] = []
    dropped_townhome_splits = 0

    tb_succ = [
        normalize_hcad13(c)
        for c in tieback_map.get(retired_hcad, ())
        if normalize_hcad13(c) and (normalize_hcad13(c) in new_accounts or normalize_hcad13(c) in parcel_hcad_to_idx)
    ]
    tb_set = set(tb_succ)

    for feat in old_features:
        props = feat.get("properties") or {}
        old_fid = str(props.get("building_id") or props.get("id") or retired_hcad)
        old_addr = str(props.get("address") or "")
        old_st_num, old_st_name = normalize_street(old_addr)
        old_own = normalize_owner(str(props.get("owner") or ""))
        old_yr = int(props.get("year_built") or 0)
        old_land_area = float(props.get("land_area") or 0.0)

        geom_dict = feat.get("geometry")
        poly_2278 = None
        fp_area = 0.0
        rep_pt = None
        if isinstance(geom_dict, dict) and geom_dict.get("coordinates"):
            try:
                g_wgs = shape(geom_dict)
                if not g_wgs.is_valid:
                    g_wgs = make_valid(g_wgs)
                poly_2278 = _reproject_shapely_geom(g_wgs, proj_2278)
                if not poly_2278.is_valid:
                    poly_2278 = make_valid(poly_2278)
                fp_area = float(poly_2278.area)
                if fp_area > 0.0:
                    rep_pt = shapely.point_on_surface(poly_2278)
            except Exception:
                poly_2278 = None

        scored_cands: list[tuple[tuple[int, int, float, int, int, int, str], str, float]] = []
        seen_cands: set[str] = set()
        overlapping_child_hcads: list[str] = []

        if parcel_strtree is not None and poly_2278 is not None and fp_area > 0.0:
            hit_idxs = parcel_strtree.query(poly_2278, predicate="intersects")
            for raw_i in hit_idxs:
                idx_i = int(raw_i)
                cand_h = parcel_hcads[idx_i]
                if cand_h == retired_hcad or cand_h in seen_cands:
                    continue
                seen_cands.add(cand_h)
                p_geom = parcel_geoms_2278[idx_i]
                inter_a = float(poly_2278.intersection(p_geom).area)
                ratio = min(1.0, inter_a / max(fp_area, 1e-6))
                covers_pt = 1 if (rep_pt is not None and p_geom.covers(rep_pt)) else 0
                if ratio >= 0.08 or covers_pt:
                    overlapping_child_hcads.append(cand_h)
                if ratio < 0.03 and not covers_pt and cand_h not in tb_set:
                    continue
                csnap = new_accounts.get(cand_h)
                cst_num, cst_name = normalize_street(csnap.numbered_address if csnap else "")
                situs_m = 1 if (old_st_name and cst_name == old_st_name and old_st_num == cst_num) else 0
                own_m = 1 if (old_own and csnap and normalize_owner(csnap.owner) == old_own) else 0
                yr_m = (
                    1
                    if (
                        csnap
                        and old_yr >= MIN_VALID_YEAR
                        and csnap.earliest_year >= MIN_VALID_YEAR
                        and abs(csnap.earliest_year - old_yr) <= 2
                    )
                    else 0
                )
                rank = (covers_pt, 1 if cand_h in tb_set else 0, round(ratio, 4), situs_m, own_m, yr_m, cand_h)
                scored_cands.append((rank, cand_h, ratio))

        for tb_h in tb_succ:
            if tb_h in seen_cands or tb_h == retired_hcad:
                continue
            seen_cands.add(tb_h)
            ratio_tb = 1.0
            covers_tb = 0
            if tb_h in parcel_hcad_to_idx and poly_2278 is not None and fp_area > 0.0:
                p_geom = parcel_geoms_2278[parcel_hcad_to_idx[tb_h]]
                inter_a = float(poly_2278.intersection(p_geom).area)
                ratio_tb = min(1.0, inter_a / max(fp_area, 1e-6))
                covers_tb = 1 if (rep_pt is not None and p_geom.covers(rep_pt)) else 0
            csnap = new_accounts.get(tb_h)
            cst_num, cst_name = normalize_street(csnap.numbered_address if csnap else "")
            situs_m = 1 if (old_st_name and cst_name == old_st_name and old_st_num == cst_num) else 0
            own_m = 1 if (old_own and csnap and normalize_owner(csnap.owner) == old_own) else 0
            yr_m = (
                1
                if (
                    csnap
                    and old_yr >= MIN_VALID_YEAR
                    and csnap.earliest_year >= MIN_VALID_YEAR
                    and abs(csnap.earliest_year - old_yr) <= 2
                )
                else 0
            )
            rank = (covers_tb, 1, round(ratio_tb, 4), situs_m, own_m, yr_m, tb_h)
            scored_cands.append((rank, tb_h, ratio_tb))

        scored_cands.sort(key=lambda item: item[0], reverse=True)
        successor_hcads = [ch for _rk, ch, _rt in scored_cands]

        if is_feature_chronology_protected(old_fid, props, overrides_index):
            kept_features.append(feat)
            used_building_ids.add(old_fid)
            for _rk, ch, rt in scored_cands:
                if _rk[0] == 1 or rt >= 0.35:
                    covered_new_hcads.add(ch)
            orphan_records.append({
                "surface": "building_shard_protected",
                "hcad_num": retired_hcad,
                "building_id": old_fid,
                "address": sanitize_atlas_text(old_addr),
                "year_built": old_yr,
                "reason": "retired_or_replatted_hcad",
                "successor_hcads": successor_hcads[:10],
            })
            continue

        if not scored_cands:
            deleted_building_ids.add(old_fid)
            if old_addr:
                migrated_entity_addresses.append((old_fid, old_addr, ""))
            c5 = _maybe_build_cohort5_candidate(
                retired_hcad,
                props,
                geom_dict,
                None,
                detection_basis="replat_vacated_no_successor",
                tax_year=tax_year,
                prior_tax_year=prior_tax_year,
                citydir_by_hcad=citydir_by_hcad,
            )
            if c5 is not None:
                cohort5_candidates.append(c5)
            continue

        _best_rank, best_hcad, containment_ratio = scored_cands[0]
        best_snap = new_accounts.get(best_hcad)
        multi_child_split = len(set(overlapping_child_hcads)) >= 2

        drop_for_vacant = (
            best_snap is not None
            and not best_snap.is_improved
            and not best_snap.state_class.startswith("X")
        )
        rebuilt_recent = best_snap is not None and best_snap.earliest_year >= max(2020, int(prior_tax_year) - 1)
        shrunk_townhome_lot = (
            best_snap is not None
            and old_land_area > 0.0
            and 0.0 < best_snap.land_area_sqft < 0.65 * old_land_area
        )
        drop_for_split_or_overhang = (
            (rebuilt_recent and (shrunk_townhome_lot or multi_child_split or containment_ratio < 0.70))
            or (
                multi_child_split
                and containment_ratio < 0.70
                and (
                    best_snap is None
                    or best_snap.earliest_year == 0
                    or (
                        old_yr >= MIN_VALID_YEAR
                        and best_snap.earliest_year >= MIN_VALID_YEAR
                        and abs(best_snap.earliest_year - old_yr) >= 5
                    )
                )
            )
        )

        if drop_for_vacant or drop_for_split_or_overhang:
            dropped_townhome_splits += 1
            deleted_building_ids.add(old_fid)
            if old_addr:
                migrated_entity_addresses.append((old_fid, old_addr, ""))
            c5 = _maybe_build_cohort5_candidate(
                retired_hcad,
                props,
                geom_dict,
                best_snap,
                detection_basis="replat_1_to_k_teardown" if drop_for_split_or_overhang else "replat_cleared_vacant",
                tax_year=tax_year,
                prior_tax_year=prior_tax_year,
                citydir_by_hcad=citydir_by_hcad,
            )
            if c5 is not None:
                cohort5_candidates.append(c5)
            continue

        # Standing building survived across 1 -> 1, M -> 1, or 1 -> K account renumbering/consolidation/split
        new_feat = copy.deepcopy(feat)
        new_props = new_feat.setdefault("properties", {})
        new_fid = _disambiguate_replat_building_id(old_fid, retired_hcad, best_hcad, used_building_ids)
        used_building_ids.add(new_fid)
        new_props["hcad_num"] = best_hcad
        new_props["building_id"] = new_fid
        new_props["id"] = new_fid
        if "id" in new_feat:
            new_feat["id"] = new_fid

        if best_snap is not None:
            if best_snap.owner:
                new_props["owner"] = best_snap.owner
            new_props["own_tier"] = best_snap.own_tier or "homestead"
            if best_snap.port_id:
                new_props["port_id"] = best_snap.port_id
            else:
                new_props.pop("port_id", None)
            if best_snap.land_area_sqft > 0.0:
                new_props["land_area"] = round(best_snap.land_area_sqft, 1)
            if best_snap.max_remodel_year >= MIN_VALID_YEAR:
                new_props["remodel_year"] = best_snap.max_remodel_year
            if best_snap.use_category and new_fid == best_hcad:
                new_props["use_category"] = best_snap.use_category
            if best_snap.bld_style and new_fid == best_hcad:
                new_props["bld_style"] = best_snap.bld_style
            if best_snap.total_bld_area > 0.0 and new_fid == best_hcad:
                new_props["bld_area"] = round(best_snap.total_bld_area, 1)
            if best_snap.numbered_address and not is_school_situs_protected(new_props):
                if best_snap.numbered_address != old_addr:
                    migrated_entity_addresses.append((old_fid, old_addr, best_snap.numbered_address))
                    new_props["address"] = best_snap.numbered_address

        old_to_new_hcads.setdefault(retired_hcad, best_hcad)
        succ_list = old_to_all_new_hcads.setdefault(retired_hcad, [])
        if best_hcad not in succ_list:
            succ_list.append(best_hcad)
        for old_id_cand in (old_fid, str(props.get("id") or ""), str(feat.get("id") or "")):
            if old_id_cand:
                old_fid_to_new_hcad[old_id_cand] = best_hcad
        if new_fid != old_fid:
            old_to_new_building_ids[old_fid] = new_fid
        covered_new_hcads.add(best_hcad)
        kept_features.append(new_feat)

    return {
        "kept_features": kept_features,
        "covered_new_hcads": covered_new_hcads,
        "deleted_building_ids": deleted_building_ids,
        "old_to_new_hcads": old_to_new_hcads,
        "old_to_all_new_hcads": old_to_all_new_hcads,
        "old_fid_to_new_hcad": old_fid_to_new_hcad,
        "old_to_new_building_ids": old_to_new_building_ids,
        "orphan_records": orphan_records,
        "cohort5_candidates": cohort5_candidates,
        "migrated_entity_addresses": migrated_entity_addresses,
        "dropped_townhome_splits": dropped_townhome_splits,
    }



def _mark_hcad_touched(
    delta: ArtifactSyncDelta,
    core_hcads: set[str],
    h: str,
    q_name: str | None = None,
) -> None:
    if q_name:
        delta.dirty_quadrants.add(q_name)
    if len(h) >= 3:
        delta.touched_permit_prefixes.add(h[:3])
    if h in core_hcads:
        delta.inner_core_touched = True


def classify_feature_delta(
    baseline_by_quad: dict[str, list[dict[str, Any]]],
    new_accounts: dict[str, HcadAccountSnapshot],
    *,
    parcel_hcads: list[str],
    parcel_geoms_2278: Any,
    parcel_hcad_to_idx: dict[str, int],
    parcel_strtree: STRtree | None,
    tieback_map: dict[str, list[str]],
    curated_overrides: dict[str, Any] | None,
    tax_year: int,
    prior_tax_year: int | None = None,
    citydir_by_hcad: dict[str, Any] | None = None,
    sanity_cfg: RollSanityConfig | None = None,
    enforce_circuit_breaker: bool = True,
    auto_revive_rebuilt_suppressed: bool = False,
    historic_core_hcads: set[str] | frozenset[str] | None = None,
) -> dict[str, Any]:
    """Classify all baseline footprints and new accounts into the 5 annual delta categories."""
    cfg = sanity_cfg or RollSanityConfig()
    eff_tax = int(tax_year)
    prev_tax = (eff_tax - 1) if prior_tax_year is None else int(prior_tax_year)
    ov_idx = build_override_protection_index(curated_overrides, default_updated_year=prev_tax)
    core_hcads = set(historic_core_hcads or ())
    to_2278 = Transformer.from_crs("EPSG:4326", "EPSG:2278", always_xy=True)

    by_hcad: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    passthrough_by_quad: dict[str, list[dict[str, Any]]] = {q: [] for q in QUAD_NAMES}
    used_building_ids: set[str] = set(ov_idx["standing_feature_ids"])

    for q in QUAD_NAMES:
        for feat in baseline_by_quad.get(q, ()):
            props = feat.get("properties") or {}
            hcad = normalize_hcad13(props.get("hcad_num"))
            if not hcad:
                passthrough_by_quad[q].append(feat)
                fid_raw = str(props.get("building_id") or props.get("id") or "")
                if fid_raw:
                    used_building_ids.add(fid_raw)
                continue
            by_hcad[hcad].append((q, feat))
            fid_chk = str(props.get("building_id") or props.get("id") or hcad)
            if fid_chk and (
                (hcad in new_accounts)
                or (hcad in parcel_hcad_to_idx)
                or is_feature_chronology_protected(fid_chk, props, ov_idx)
            ):
                used_building_ids.add(fid_chk)
                raw_pid = str(props.get("id") or "")
                if raw_pid:
                    used_building_ids.add(raw_pid)

    patched_by_quad: dict[str, list[dict[str, Any]]] = {
        q: list(passthrough_by_quad[q]) for q in QUAD_NAMES
    }
    delta = ArtifactSyncDelta()
    feature_counts: Counter[str] = Counter()
    parcel_counts: Counter[str] = Counter()
    governance_counts: Counter[str] = Counter()
    covered_new_hcads: set[str] = set()
    orphan_records: list[dict[str, Any]] = []
    cohort5_by_hcad: dict[str, dict[str, Any]] = {}
    protected_cama_disagreements: list[dict[str, Any]] = []
    rebuilt_on_suppressed_lots: list[dict[str, Any]] = []
    revived_suppressed_hcads: set[str] = set()
    dropped_townhome_splits = 0

    for sup_hcad, sup_info in sorted(ov_idx["suppressed_vacant_by_hcad"].items()):
        snap_sup = new_accounts.get(sup_hcad)
        if snap_sup is None:
            continue
        min_post_yr = max(int(sup_info["updated_year"]), prev_tax)
        if snap_sup.earliest_year >= min_post_yr and snap_sup.total_bld_area >= 400.0:
            rebuilt_on_suppressed_lots.append({
                "hcad_num": sup_hcad,
                "override_key": sup_info["key"],
                "suppressed_year": int(sup_info["updated_year"]),
                "new_year_built": int(snap_sup.earliest_year),
                "new_bld_area": round(float(snap_sup.total_bld_area), 1),
                "address": snap_sup.numbered_address or snap_sup.raw_site_addr_1,
                "auto_revived": bool(auto_revive_rebuilt_suppressed),
            })
            if auto_revive_rebuilt_suppressed:
                revived_suppressed_hcads.add(sup_hcad)

    for hcad in sorted(by_hcad.keys()):
        q_feats = by_hcad[hcad]
        raw_feats = [f for _q, f in q_feats]
        gov = classify_parcel_footprint_governance(hcad, raw_feats, ov_idx)
        governance_counts[gov] += 1

        in_roll = (hcad in new_accounts) or (hcad in parcel_hcad_to_idx)
        if not in_roll:
            parcel_counts[DeltaCategory.REPLATTED_OR_RETIRED.value] += 1
            feature_counts[DeltaCategory.REPLATTED_OR_RETIRED.value] += len(q_feats)
            rep_res = resolve_replat_lineage(
                hcad,
                raw_feats,
                new_accounts=new_accounts,
                parcel_hcads=parcel_hcads,
                parcel_geoms_2278=parcel_geoms_2278,
                parcel_hcad_to_idx=parcel_hcad_to_idx,
                parcel_strtree=parcel_strtree,
                tieback_map=tieback_map,
                overrides_index=ov_idx,
                used_building_ids=used_building_ids,
                prior_tax_year=prev_tax,
                tax_year=eff_tax,
                citydir_by_hcad=citydir_by_hcad,
                to_2278=to_2278,
            )
            dropped_townhome_splits += int(rep_res["dropped_townhome_splits"])
            covered_new_hcads.update(rep_res["covered_new_hcads"])
            delta.deleted_hcads.add(hcad)
            delta.deleted_building_ids.update(rep_res["deleted_building_ids"])
            delta.old_to_new_hcads.update(rep_res["old_to_new_hcads"])
            delta.old_to_all_new_hcads.update(rep_res.get("old_to_all_new_hcads", {}))
            delta.old_fid_to_new_hcad.update(rep_res.get("old_fid_to_new_hcad", {}))
            delta.old_to_new_building_ids.update(rep_res["old_to_new_building_ids"])
            orphan_records.extend(rep_res["orphan_records"])
            for c5 in rep_res["cohort5_candidates"]:
                cohort5_by_hcad.setdefault(c5["hcad_num"], c5)
            for ent_id, old_a, new_a in rep_res.get("migrated_entity_addresses", ()):
                _record_addr_prefix_migration(delta, ent_id, old_a, new_a)

            for q_orig, _f in q_feats:
                _mark_hcad_touched(delta, core_hcads, hcad, q_orig)
            for kfeat in rep_res["kept_features"]:
                kp = kfeat.get("properties") or {}
                kh = normalize_hcad13(kp.get("hcad_num")) or hcad
                ctr = _feature_centroid_wgs84(kfeat.get("geometry"))
                kq = assign_quadrant(ctr[0], ctr[1]) if ctr else q_feats[0][0]
                patched_by_quad[kq].append(kfeat)
                delta.upserted_features_by_hcad[kh].append(kfeat)
                _mark_hcad_touched(delta, core_hcads, kh, kq)
            continue

        snap = new_accounts.get(hcad)
        st_cls = (snap.state_class if snap else "").upper()
        is_tax_exempt = st_cls.startswith("X")
        cama_vacant_cleared = (
            snap is not None
            and not is_tax_exempt
            and snap.total_bld_area <= 0.0
            and snap.earliest_year == 0
            and (
                (st_cls.startswith(("C1", "C2", "C3", "D")) and snap.bld_val <= 0.0)
                or snap.has_main_demo_permit
            )
        )

        if cama_vacant_cleared:
            surviving_protected: list[tuple[str, dict[str, Any]]] = []
            droppable: list[tuple[str, dict[str, Any]]] = []
            for q_name, feat in q_feats:
                fp = feat.get("properties") or {}
                fid = str(fp.get("building_id") or fp.get("id") or hcad)
                if is_feature_chronology_protected(fid, fp, ov_idx):
                    surviving_protected.append((q_name, feat))
                else:
                    droppable.append((q_name, feat))

            for q_name, pfeat in surviving_protected:
                pp = pfeat.get("properties") or {}
                pfid = str(pp.get("building_id") or pp.get("id") or hcad)
                used_building_ids.add(pfid)
                patched_by_quad[q_name].append(pfeat)
                covered_new_hcads.add(hcad)
                feature_counts[DeltaCategory.UNCHANGED.value] += 1
                orphan_records.append({
                    "surface": "building_shard_protected",
                    "hcad_num": hcad,
                    "building_id": pfid,
                    "address": sanitize_atlas_text(str(pp.get("address") or "")),
                    "year_built": int(pp.get("year_built") or 0),
                    "reason": "cama_bld_area_zero_on_protected_entity",
                    "cama_state_class": st_cls,
                    "has_main_demo_permit": bool(snap.has_main_demo_permit if snap else False),
                    "successor_hcads": [hcad],
                })

            if droppable:
                parcel_counts[DeltaCategory.DEMOLISHED_TO_VACANT.value] += 1
                partial_surv = len(surviving_protected) > 0
                if not partial_surv:
                    delta.deleted_hcads.add(hcad)
                for q_name, dfeat in droppable:
                    feature_counts[DeltaCategory.DEMOLISHED_TO_VACANT.value] += 1
                    dp = dfeat.get("properties") or {}
                    dfid = str(dp.get("building_id") or dp.get("id") or hcad)
                    delta.deleted_building_ids.add(dfid)
                    if dp.get("address"):
                        _record_addr_prefix_migration(delta, dfid, str(dp["address"]), "")
                    _mark_hcad_touched(delta, core_hcads, hcad, q_name)
                    c5 = _maybe_build_cohort5_candidate(
                        hcad,
                        dp,
                        dfeat.get("geometry"),
                        snap,
                        detection_basis="demolished_to_vacant",
                        tax_year=eff_tax,
                        prior_tax_year=prev_tax,
                        citydir_by_hcad=citydir_by_hcad,
                        partial_campus_surviving=partial_surv,
                    )
                    if c5 is not None:
                        cohort5_by_hcad.setdefault(hcad, c5)
            else:
                parcel_counts[DeltaCategory.UNCHANGED.value] += 1
            continue

        covered_new_hcads.add(hcad)
        parcel_any_updated = False

        for q_name, feat in q_feats:
            fp = feat.get("properties") or {}
            fid = str(fp.get("building_id") or fp.get("id") or hcad)
            used_building_ids.add(fid)

            if snap is None:
                patched_by_quad[q_name].append(feat)
                feature_counts[DeltaCategory.UNCHANGED.value] += 1
                continue

            changed = False
            new_feat = copy.deepcopy(feat)
            np_props = new_feat.setdefault("properties", {})
            old_addr = str(fp.get("address") or "")
            old_yr = int(fp.get("year_built") or 0)
            old_src = str(fp.get("year_source") or "").strip()

            if is_feature_chronology_protected(fid, fp, ov_idx):
                if snap.earliest_year >= MIN_VALID_YEAR and snap.earliest_year != old_yr:
                    protected_cama_disagreements.append({
                        "hcad_num": hcad,
                        "building_id": fid,
                        "protected_year": old_yr,
                        "cama_year": int(snap.earliest_year),
                        "year_source": old_src,
                    })
                if snap.owner and not np_props.get("owner"):
                    np_props["owner"] = snap.owner
                    changed = True
                if snap.land_area_sqft > 0.0 and not np_props.get("land_area"):
                    np_props["land_area"] = round(snap.land_area_sqft, 1)
                    changed = True
                if (
                    snap.numbered_address
                    and not old_addr
                    and not is_school_situs_protected(np_props)
                ):
                    np_props["address"] = snap.numbered_address
                    changed = True
            elif gov in (
                ParcelGovernance.UNCURATED_MULTI_FOOTPRINT.value,
                ParcelGovernance.PARTIAL_CURATED_SUBBUILDINGS.value,
            ):
                if old_yr < MIN_VALID_YEAR and snap.earliest_year >= MIN_VALID_YEAR:
                    np_props["year_built"] = int(snap.earliest_year)
                    np_props["decade"] = compute_decade(int(snap.earliest_year))
                    np_props["year_source"] = "hcad_cama"
                    changed = True
                elif isinstance(np_props.get("decade"), str):
                    np_props["decade"] = compute_decade(int(np_props.get("year_built") or 0))
                    changed = True
                if snap.owner and np_props.get("owner") != snap.owner:
                    np_props["owner"] = snap.owner
                    changed = True
                if snap.land_area_sqft > 0.0:
                    new_la = round(snap.land_area_sqft, 1)
                    if float(np_props.get("land_area") or 0.0) != new_la:
                        np_props["land_area"] = new_la
                        changed = True
                if snap.max_remodel_year >= MIN_VALID_YEAR and int(np_props.get("remodel_year") or 0) != snap.max_remodel_year:
                    np_props["remodel_year"] = int(snap.max_remodel_year)
                    changed = True
                if (
                    snap.numbered_address
                    and not is_school_situs_protected(np_props)
                    and np_props.get("address") != snap.numbered_address
                ):
                    _record_addr_prefix_migration(delta, fid, old_addr, snap.numbered_address)
                    np_props["address"] = snap.numbered_address
                    changed = True
            else:
                cama_yr = int(snap.earliest_year or 0)
                year_jumped = False
                if cama_yr >= MIN_VALID_YEAR and (
                    cama_yr != old_yr or old_src in INFERRED_FALLBACK_YEAR_SOURCES
                ):
                    if (
                        MIN_VALID_YEAR <= old_yr < 1940
                        and old_src not in INFERRED_FALLBACK_YEAR_SOURCES
                        and cama_yr >= max(2020, prev_tax - 3)
                    ):
                        c5_rb = _maybe_build_cohort5_candidate(
                            hcad,
                            fp,
                            feat.get("geometry"),
                            snap,
                            detection_basis="same_account_teardown_rebuild",
                            tax_year=eff_tax,
                            prior_tax_year=prev_tax,
                            citydir_by_hcad=citydir_by_hcad,
                        )
                        if c5_rb is not None:
                            cohort5_by_hcad.setdefault(hcad, c5_rb)
                    year_jumped = abs(cama_yr - old_yr) >= 5
                    np_props["year_built"] = cama_yr
                    np_props["decade"] = compute_decade(cama_yr)
                    np_props["year_source"] = "hcad_cama"
                    changed = True
                elif isinstance(np_props.get("decade"), str):
                    np_props["decade"] = compute_decade(old_yr)
                    changed = True

                old_ba = float(np_props.get("bld_area") or 0.0)
                area_shifted = False
                if snap.total_bld_area > 0.0:
                    new_ba = round(snap.total_bld_area, 1)
                    if abs(new_ba - old_ba) >= 1.0:
                        np_props["bld_area"] = new_ba
                        changed = True
                        if old_ba <= 0.0 or abs(new_ba - old_ba) / max(old_ba, 1.0) >= 0.10:
                            area_shifted = True
                if snap.land_area_sqft > 0.0:
                    new_la = round(snap.land_area_sqft, 1)
                    if abs(new_la - float(np_props.get("land_area") or 0.0)) >= 1.0:
                        np_props["land_area"] = new_la
                        changed = True
                if snap.max_remodel_year >= MIN_VALID_YEAR and int(np_props.get("remodel_year") or 0) != snap.max_remodel_year:
                    np_props["remodel_year"] = int(snap.max_remodel_year)
                    changed = True
                if snap.owner and np_props.get("owner") != snap.owner:
                    np_props["owner"] = snap.owner
                    changed = True
                if snap.use_category and np_props.get("use_category") != snap.use_category:
                    np_props["use_category"] = snap.use_category
                    changed = True
                if snap.bld_style and np_props.get("bld_style") != snap.bld_style:
                    np_props["bld_style"] = snap.bld_style
                    changed = True
                if (
                    snap.numbered_address
                    and not is_school_situs_protected(np_props)
                    and np_props.get("address") != snap.numbered_address
                ):
                    _record_addr_prefix_migration(delta, fid, old_addr, snap.numbered_address)
                    np_props["address"] = snap.numbered_address
                    changed = True
                if year_jumped or area_shifted:
                    fp_sq = float(np_props.get("footprint_area_sqft") or 0.0)
                    st_est, ht_est = estimate_stories_and_height(
                        float(np_props.get("bld_area") or 0.0),
                        footprint_area_sqft=fp_sq,
                        use_category=str(np_props.get("use_category") or "Residential"),
                    )
                    np_props["stories"] = st_est
                    np_props["height_m"] = ht_est

            if changed:
                np_props["own_tier"] = snap.own_tier or "homestead"
                if snap.port_id:
                    np_props["port_id"] = snap.port_id
                else:
                    np_props.pop("port_id", None)
                parcel_any_updated = True
                patched_by_quad[q_name].append(new_feat)
                delta.upserted_features_by_hcad[hcad].append(new_feat)
                _mark_hcad_touched(delta, core_hcads, hcad, q_name)
                feature_counts[DeltaCategory.UPDATED_STANDING.value] += 1
            else:
                fp_mut = feat.setdefault("properties", {})
                fp_mut["own_tier"] = snap.own_tier or str(fp_mut.get("own_tier") or "homestead")
                if snap.port_id:
                    fp_mut["port_id"] = snap.port_id
                else:
                    fp_mut.pop("port_id", None)
                patched_by_quad[q_name].append(feat)
                feature_counts[DeltaCategory.UNCHANGED.value] += 1

        if parcel_any_updated:
            parcel_counts[DeltaCategory.UPDATED_STANDING.value] += 1
        else:
            parcel_counts[DeltaCategory.UNCHANGED.value] += 1

    active_suppressed_vacant = set(ov_idx["suppressed_vacant_by_hcad"].keys()) - revived_suppressed_hcads
    for acct, snap in sorted(new_accounts.items()):
        if acct not in parcel_hcad_to_idx:
            continue
        if not snap.is_improved:
            continue
        if acct in covered_new_hcads:
            continue
        if acct in ov_idx["suppressed_shard_hcads"]:
            continue
        if acct in active_suppressed_vacant:
            continue
        if acct in ov_idx["full_replace_hcads"]:
            continue
        delta.new_construction_hcads.add(acct)
        if len(acct) >= 3:
            delta.touched_permit_prefixes.add(acct[:3])
        parcel_counts[DeltaCategory.NEW_CONSTRUCTION.value] += 1
        feature_counts[DeltaCategory.NEW_CONSTRUCTION.value] += 1

    total_baseline_parcels = len(by_hcad)
    demo_parcels = parcel_counts[DeltaCategory.DEMOLISHED_TO_VACANT.value]
    demo_fraction = (demo_parcels / total_baseline_parcels) if total_baseline_parcels > 0 else 0.0
    if enforce_circuit_breaker and total_baseline_parcels > 0 and demo_fraction > cfg.max_annual_demolition_fraction:
        raise RollValidationError(
            f"Mass-demolition circuit breaker tripped: {demo_parcels:,} of "
            f"{total_baseline_parcels:,} baseline parcels ({demo_fraction:.2%}) classified as "
            f"DEMOLISHED_TO_VACANT, exceeding max_annual_demolition_fraction="
            f"{cfg.max_annual_demolition_fraction:.2%}."
        )

    sorted_c5 = sorted(cohort5_by_hcad.values(), key=lambda r: (r["year_built"], r["hcad_num"]))
    return {
        "patched_by_quad": patched_by_quad,
        "artifact_delta": delta,
        "feature_counts": {c.value: int(feature_counts.get(c.value, 0)) for c in DeltaCategory},
        "parcel_counts": {c.value: int(parcel_counts.get(c.value, 0)) for c in DeltaCategory},
        "governance_counts": dict(governance_counts),
        "demolition_fraction": round(demo_fraction, 6),
        "dropped_townhome_splits": int(dropped_townhome_splits),
        "orphan_records": orphan_records,
        "lost_houston_candidates": sorted_c5,
        "auto_graduated_lost_houston_candidates": [c for c in sorted_c5 if c.get("auto_graduate") is True],
        "bare_zero_value_flips_hold_for_review": [c for c in sorted_c5 if not c.get("auto_graduate")],
        "protected_cama_disagreements": protected_cama_disagreements,
        "rebuilt_on_suppressed_lots": rebuilt_on_suppressed_lots,
        "used_building_ids": used_building_ids,
        "overrides_index": ov_idx,
    }



def _lookup_successor_hcads_for_ref(
    hcad: str,
    geom_or_pt: dict[str, Any] | tuple[float, float] | None,
    *,
    new_accounts: dict[str, HcadAccountSnapshot],
    parcel_hcads: list[str],
    parcel_hcad_to_idx: dict[str, int],
    parcel_strtree: STRtree | None,
    tieback_map: dict[str, list[str]],
    to_2278: Transformer,
) -> list[str]:
    succ: list[str] = []
    seen: set[str] = set()
    for rel in tieback_map.get(hcad, ()):
        rh = normalize_hcad13(rel)
        if rh and rh != hcad and rh not in seen and (rh in new_accounts or rh in parcel_hcad_to_idx):
            seen.add(rh)
            succ.append(rh)

    if parcel_strtree is not None and geom_or_pt is not None:
        try:
            if isinstance(geom_or_pt, tuple) and len(geom_or_pt) >= 2:
                px, py = to_2278.transform(float(geom_or_pt[0]), float(geom_or_pt[1]))
                q_geom = shapely.points(px, py)
            elif isinstance(geom_or_pt, dict) and geom_or_pt.get("coordinates"):
                g_wgs = shape(geom_or_pt)
                if not g_wgs.is_valid:
                    g_wgs = make_valid(g_wgs)
                q_geom = _reproject_shapely_geom(g_wgs, to_2278)
            else:
                q_geom = None
            if q_geom is not None and not q_geom.is_empty:
                for raw_i in parcel_strtree.query(q_geom, predicate="intersects"):
                    cand = parcel_hcads[int(raw_i)]
                    if cand != hcad and cand not in seen:
                        seen.add(cand)
                        succ.append(cand)
        except Exception:
            pass
    return succ[:10]


def _audit_curated_hcad_ref(
    surface: str,
    hcad_raw: Any,
    ref_id: str,
    label: str = "",
    geom_or_pt: Any = None,
    *,
    check_bld_zero: bool = True,
    new_accounts: dict[str, HcadAccountSnapshot],
    parcel_hcads: list[str],
    parcel_hcad_to_idx: dict[str, int],
    parcel_strtree: STRtree | None,
    tieback_map: dict[str, list[str]],
    to_2278: Transformer,
    seen_keys: set[tuple[str, str, str, str]],
    by_surface: Counter[str],
    items: list[dict[str, Any]],
) -> None:
    h = normalize_hcad13(hcad_raw)
    if not h:
        return
    in_roll = (h in new_accounts) or (h in parcel_hcad_to_idx)
    snap = new_accounts.get(h)
    reason = ""
    if not in_roll:
        reason = "retired_or_replatted_hcad"
    elif check_bld_zero and snap is not None:
        sc = snap.state_class.upper()
        if (
            not sc.startswith("X")
            and snap.total_bld_area <= 0.0
            and snap.earliest_year == 0
        ):
            reason = "cama_bld_area_zero_non_exempt"
    if not reason:
        return
    dedup_k = (surface, h, str(ref_id), reason)
    if dedup_k in seen_keys:
        return
    seen_keys.add(dedup_k)
    by_surface[surface] += 1
    succ = (
        _lookup_successor_hcads_for_ref(
            h,
            geom_or_pt,
            new_accounts=new_accounts,
            parcel_hcads=parcel_hcads,
            parcel_hcad_to_idx=parcel_hcad_to_idx,
            parcel_strtree=parcel_strtree,
            tieback_map=tieback_map,
            to_2278=to_2278,
        )
        if reason == "retired_or_replatted_hcad"
        else [h]
    )
    items.append({
        "surface": surface,
        "hcad_num": h,
        "reference_id": str(ref_id),
        "label": sanitize_atlas_text(label),
        "reason": reason,
        "cama_state_class": snap.state_class if snap else "",
        "has_main_demo_permit": bool(snap.has_main_demo_permit if snap else False),
        "successor_hcads": succ,
    })


def audit_curated_hcad_references(
    output_dir: Path,
    new_accounts: dict[str, HcadAccountSnapshot],
    *,
    parcel_hcads: list[str],
    parcel_hcad_to_idx: dict[str, int],
    parcel_strtree: STRtree | None,
    tieback_map: dict[str, list[str]],
    extra_shard_orphans: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Audit all 5 curated/operational surfaces for retired or cleared HCAD references (Section 4.3)."""
    ddir = Path(output_dir)
    to_2278 = Transformer.from_crs("EPSG:4326", "EPSG:2278", always_xy=True)
    items: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str, str, str]] = set()
    by_surface: Counter[str] = Counter()
    ctx_kw = {
        "new_accounts": new_accounts,
        "parcel_hcads": parcel_hcads,
        "parcel_hcad_to_idx": parcel_hcad_to_idx,
        "parcel_strtree": parcel_strtree,
        "tieback_map": tieback_map,
        "to_2278": to_2278,
        "seen_keys": seen_keys,
        "by_surface": by_surface,
        "items": items,
    }

    # 1. curated_overrides.json (primary, composite #slug, and suppress_shard_hcads)
    ov_path = ddir / "curated_overrides.json"
    if ov_path.exists():
        ov_doc = json.loads(ov_path.read_text(encoding="utf-8"))
        for key, rec in _unwrap_overrides_map(ov_doc).items():
            is_vac = _override_record_is_vacant(rec)
            prim_h = normalize_hcad13(rec.get("hcad_num") or str(key).split("#")[0])
            lbl = str(rec.get("landmark_name") or rec.get("building_name") or rec.get("address") or key)
            _audit_curated_hcad_ref(
                "curated_overrides",
                prim_h,
                str(key),
                lbl,
                rec.get("geometry"),
                check_bld_zero=not is_vac,
                **ctx_kw,
            )
            for sup_h in rec.get("suppress_shard_hcads") or ():
                _audit_curated_hcad_ref(
                    "curated_overrides_suppress_shard_hcads",
                    sup_h,
                    str(key),
                    lbl,
                    None,
                    check_bld_zero=False,
                    **ctx_kw,
                )

    # 2. building_photos.json (skip lost_*)
    ph_path = ddir / "building_photos.json"
    if ph_path.exists():
        ph_doc = json.loads(ph_path.read_text(encoding="utf-8"))
        if isinstance(ph_doc, dict):
            for rec in ph_doc.get("photos") or ():
                if not isinstance(rec, dict) or rec.get("is_lost_houston"):
                    continue
                bid = str(rec.get("building_id") or "")
                if bid.startswith("lost_"):
                    continue
                h_ref = rec.get("hcad_num") or (bid.split("#")[0] if bid else "")
                _audit_curated_hcad_ref(
                    "building_photos",
                    h_ref,
                    bid or str(h_ref),
                    str(rec.get("landmark_name") or ""),
                    **ctx_kw,
                )
            for dict_key in ("byHcad", "byBuildingId", "buildings"):
                sub = ph_doc.get(dict_key)
                if isinstance(sub, dict):
                    for k_ph in sub.keys():
                        ks = str(k_ph)
                        if ks.startswith("lost_"):
                            continue
                        _audit_curated_hcad_ref(
                            "building_photos",
                            ks.split("#")[0],
                            ks,
                            str(dict_key),
                            **ctx_kw,
                        )

    # 3. overlays.json (standing landmarks + good_brick_awards)
    ovl_path = ddir / "overlays.json"
    if ovl_path.exists():
        ovl_doc = json.loads(ovl_path.read_text(encoding="utf-8"))
        for feat in (ovl_doc.get("landmarks") or {}).get("features") or ():
            lp = feat.get("properties") or {}
            if lp.get("demolished"):
                continue
            _audit_curated_hcad_ref(
                "overlays_landmarks",
                lp.get("hcad_num"),
                str(lp.get("id") or lp.get("lm_num") or lp.get("plm_num") or lp.get("hcad_num") or ""),
                str(lp.get("name") or lp.get("address") or ""),
                feat.get("geometry"),
                **ctx_kw,
            )
        for feat in (ovl_doc.get("good_brick_awards") or {}).get("features") or ():
            gp = feat.get("properties") or {}
            gb_h = gp.get("hcad_num") or str(gp.get("building_id") or "").split("#")[0]
            _audit_curated_hcad_ref(
                "overlays_good_brick_awards",
                gb_h,
                str(gp.get("id") or gp.get("building_id") or gb_h or ""),
                str(gp.get("recipient") or gp.get("project_name") or gp.get("name") or gp.get("address") or ""),
                feat.get("geometry"),
                **ctx_kw,
            )

    # 4. property_watchlist.json (active non-tombstoned rows)
    wl_path = ddir / "property_watchlist.json"
    if wl_path.exists():
        wl_doc = json.loads(wl_path.read_text(encoding="utf-8"))
        wl_rows: list[dict[str, Any]] = []
        if isinstance(wl_doc, dict):
            if isinstance(wl_doc.get("primaryProperty"), dict):
                wl_rows.append(wl_doc["primaryProperty"])
            for list_key in ("watchedProperties", "properties"):
                if isinstance(wl_doc.get(list_key), list):
                    wl_rows.extend(r for r in wl_doc[list_key] if isinstance(r, dict))
        tombstone_statuses_lc = {s.strip().lower() for s in WATCHLIST_TOMBSTONE_STATUSES}
        for wrec in wl_rows:
            status_norm = str(wrec.get("trackerStatus") or wrec.get("status") or "").strip().lower()
            if status_norm in tombstone_statuses_lc:
                continue
            wid = str(wrec.get("id") or wrec.get("slug") or wrec.get("name") or "")
            wlbl = str(wrec.get("name") or wrec.get("title") or wrec.get("address") or wid)
            pt = None
            if wrec.get("lng") is not None and wrec.get("lat") is not None:
                pt = (float(wrec["lng"]), float(wrec["lat"]))
            cand_hs: list[Any] = []
            if wrec.get("hcadAccount"):
                cand_hs.append(wrec["hcadAccount"])
            if wrec.get("hcad_num"):
                cand_hs.append(wrec["hcad_num"])
            for item_h in wrec.get("hcadAccounts") or ():
                cand_hs.append(item_h)
            for ch in cand_hs:
                _audit_curated_hcad_ref(
                    "property_watchlist",
                    ch,
                    wid or str(ch),
                    wlbl,
                    pt,
                    check_bld_zero=True,
                    **ctx_kw,
                )

    # 5. buildings.geojson (Historic Core standing structures)
    core_path = ddir / "buildings.geojson"
    if core_path.exists():
        core_doc = json.loads(core_path.read_text(encoding="utf-8"))
        for feat in core_doc.get("features") or ():
            bp = feat.get("properties") or {}
            bh = normalize_hcad13(bp.get("hcad_num"))
            if not bh:
                continue
            # Historic core tracks all standing core buildings; surface retired HCADs and curated landmark zeroings.
            is_cur = bool(bp.get("is_curated_override")) or str(bp.get("year_source") or "") in PROTECTED_CHRONOLOGY_SOURCES
            _audit_curated_hcad_ref(
                "buildings_geojson_core",
                bh,
                str(bp.get("building_id") or bp.get("id") or bh),
                str(bp.get("landmark_name") or bp.get("building_name") or bp.get("address") or ""),
                feat.get("geometry"),
                check_bld_zero=is_cur,
                **ctx_kw,
            )

    for ext in extra_shard_orphans or ():
        h_ext = normalize_hcad13(ext.get("hcad_num"))
        ref_ext = str(ext.get("building_id") or h_ext)
        rsn_ext = str(ext.get("reason") or "retired_or_replatted_hcad")
        dk = ("building_shard_protected", h_ext, ref_ext, rsn_ext)
        if h_ext and dk not in seen_keys:
            seen_keys.add(dk)
            by_surface["building_shard_protected"] += 1
            items.append({
                "surface": "building_shard_protected",
                "hcad_num": h_ext,
                "reference_id": ref_ext,
                "label": sanitize_atlas_text(str(ext.get("address") or "")),
                "reason": rsn_ext,
                "cama_state_class": str(ext.get("cama_state_class") or ""),
                "has_main_demo_permit": bool(ext.get("has_main_demo_permit")),
                "successor_hcads": list(ext.get("successor_hcads") or ()),
            })

    return {
        "total_orphaned_or_flagged": len(items),
        "counts_by_surface": dict(by_surface),
        "items": items,
    }


def _load_observed_footprints_by_parcel(
    observed_ndjson: Path | None,
    parcel_hcads: list[str],
    parcel_geoms_2278: Any,
    parcel_strtree: STRtree | None,
    target_hcads: set[str],
) -> dict[str, list[dict[str, Any]]]:
    """Match optional observed footprints (`--observed-footprints-ndjson`) to `NEW_CONSTRUCTION` parcels."""
    matched: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if observed_ndjson is None or not Path(observed_ndjson).exists() or not target_hcads or parcel_strtree is None:
        return matched
    to_2278 = Transformer.from_crs("EPSG:4326", "EPSG:2278", always_xy=True)
    with open(observed_ndjson, "rb") as fh:
        for raw_line in fh:
            ln = raw_line[1:] if raw_line.startswith(b"\x1e") else raw_line
            ln = ln.strip()
            if not ln:
                continue
            obj = orjson.loads(ln)
            geom = obj.get("geometry") if obj.get("type") == "Feature" else obj
            if not isinstance(geom, dict) or geom.get("type") not in ("Polygon", "MultiPolygon"):
                continue
            norm_geom = normalize_polygon_rings_6dp_ccw(geom)
            props = obj.get("properties") or {}
            explicit_h = normalize_hcad13(props.get("hcad_num"))
            if explicit_h and explicit_h in target_hcads:
                matched[explicit_h].append(norm_geom)
                continue
            try:
                g_wgs = shape(norm_geom)
                if not g_wgs.is_valid:
                    g_wgs = make_valid(g_wgs)
                g_2278 = _reproject_shapely_geom(g_wgs, to_2278)
                rep = shapely.point_on_surface(g_2278)
                for raw_i in parcel_strtree.query(rep, predicate="intersects"):
                    ph = parcel_hcads[int(raw_i)]
                    if ph in target_hcads and parcel_geoms_2278[int(raw_i)].covers(rep):
                        matched[ph].append(norm_geom)
                        break
            except Exception:
                continue
    return matched


def synthesize_new_construction_features(
    patched_by_quad: dict[str, list[dict[str, Any]]],
    delta: ArtifactSyncDelta,
    new_accounts: dict[str, HcadAccountSnapshot],
    *,
    parcel_hcads: list[str],
    parcel_geoms_2278: Any,
    parcel_hcad_to_idx: dict[str, int],
    parcel_strtree: STRtree | None,
    bldg_zip_path: Path,
    acct_zip_path: Path,
    cache_dir: Path,
    tax_year: int,
    used_building_ids: set[str],
    observed_footprints_ndjson: Path | None = None,
    overlays_doc: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Synthesize oriented footprints for `NEW_CONSTRUCTION` and enforce `unknown == 0` via shared Phases 2A-4."""
    eff_tax = int(tax_year)
    from_2278 = Transformer.from_crs("EPSG:2278", "EPSG:4326", always_xy=True)
    to_2278 = Transformer.from_crs("EPSG:4326", "EPSG:2278", always_xy=True)
    min_lng, min_lat, max_lng, max_lat = HARRIS_COUNTY_BBOX

    # Prepare Historic District STRtree for tagging new in-district buildings
    hd_polys: list[Any] = []
    hd_names: list[str] = []
    ovl = overlays_doc or {}
    for layer_k in ("historic_districts", "heritage_districts"):
        for df in (ovl.get(layer_k) or {}).get("features") or ():
            dg = df.get("geometry")
            dname = str((df.get("properties") or {}).get("name") or "").strip()
            if dg and dname:
                try:
                    sg = shape(dg)
                    if not sg.is_valid:
                        sg = make_valid(sg)
                    hd_polys.append(sg)
                    hd_names.append(dname)
                except Exception:
                    pass
    hd_tree = STRtree(hd_polys) if hd_polys else None

    observed_map = _load_observed_footprints_by_parcel(
        observed_footprints_ndjson,
        parcel_hcads,
        parcel_geoms_2278,
        parcel_strtree,
        delta.new_construction_hcads,
    )

    synthesized_count = 0
    observed_used_count = 0

    for acct in sorted(delta.new_construction_hcads):
        p_idx = parcel_hcad_to_idx.get(acct)
        snap = new_accounts.get(acct)
        if p_idx is None or snap is None:
            continue
        p_geom_2278 = parcel_geoms_2278[p_idx]
        obs_geoms = observed_map.get(acct) or []

        feat_geoms_and_src: list[tuple[dict[str, Any], str, float]] = []
        if obs_geoms:
            for og in obs_geoms:
                try:
                    g22 = _reproject_shapely_geom(shape(og), to_2278)
                    fp_sq = max(100.0, float(g22.area))
                except Exception:
                    fp_sq = max(400.0, float(snap.total_bld_area or 1200.0))
                feat_geoms_and_src.append((normalize_polygon_rings_6dp_ccw(og), "observed_footprint", fp_sq))
            observed_used_count += len(obs_geoms)
        else:
            est_stories_hint = 2 if snap.total_bld_area >= 2600.0 else 1
            mrr_2278 = shapely.oriented_envelope(p_geom_2278)
            synth_bld_area = (
                float(snap.total_bld_area)
                if snap.total_bld_area > 0.0
                else min(max(600.0, float(p_geom_2278.area) * 0.28), 3200.0)
            )
            poly_2278 = build_oriented_footprint_2278(
                p_geom_2278,
                mrr_2278,
                synth_bld_area,
                est_stories_hint,
            )
            if poly_2278 is None or poly_2278.is_empty:
                rep_c = shapely.point_on_surface(p_geom_2278)
                half = 12.0
                poly_2278 = Polygon([
                    (rep_c.x - half, rep_c.y - half),
                    (rep_c.x + half, rep_c.y - half),
                    (rep_c.x + half, rep_c.y + half),
                    (rep_c.x - half, rep_c.y + half),
                ])
            poly_wgs = _reproject_shapely_geom(poly_2278, from_2278)
            norm_geom = normalize_polygon_rings_6dp_ccw(dict(mapping(poly_wgs)))
            feat_geoms_and_src.append((norm_geom, "derived_parcel_oriented", float(poly_2278.area)))

        base_yr = snap.earliest_year if snap.earliest_year >= MIN_VALID_YEAR else snap.yr_impr
        yr_src = "hcad_cama" if base_yr >= MIN_VALID_YEAR else ""
        addr = snap.numbered_address or snap.raw_site_addr_1

        for idx_g, (norm_geom, g_src, fp_area_sq) in enumerate(feat_geoms_and_src):
            ctr = _feature_centroid_wgs84(norm_geom)
            if ctr is None:
                continue
            lon, lat = ctr
            if not (min_lng <= lon <= max_lng and min_lat <= lat <= max_lat):
                raise RollValidationError(
                    f"Synthesized footprint for {acct} landed outside HARRIS_COUNTY_BBOX at ({lon}, {lat})."
                )
            if idx_g == 0 and acct not in used_building_ids:
                fid = acct
            else:
                seq_k = idx_g + 1
                cand_id = f"{acct}#n{seq_k}"
                while cand_id in used_building_ids:
                    seq_k += 1
                    cand_id = f"{acct}#n{seq_k}"
                fid = cand_id
            used_building_ids.add(fid)

            hd_name = ""
            contrib = "Outside Historic District"
            if hd_tree is not None:
                pt_w = shapely.points(lon, lat)
                for hi in hd_tree.query(pt_w, predicate="intersects"):
                    if hd_polys[int(hi)].covers(pt_w):
                        hd_name = hd_names[int(hi)]
                        contrib = "Non-Contributing"
                        break

            bld_ar = round(float(snap.total_bld_area or fp_area_sq), 1)
            stories_val, height_m = estimate_stories_and_height(
                bld_ar,
                footprint_area_sqft=fp_area_sq,
                use_category=snap.use_category,
            )
            props_new: dict[str, Any] = {
                "id": fid,
                "building_id": fid,
                "hcad_num": acct,
                "address": addr,
                "owner": snap.owner,
                "year_built": int(base_yr) if base_yr >= MIN_VALID_YEAR else 0,
                "decade": compute_decade(int(base_yr)) if base_yr >= MIN_VALID_YEAR else 0,
                "year_source": yr_src,
                "remodel_year": int(snap.max_remodel_year or 0),
                "use_category": snap.use_category,
                "bld_style": snap.bld_style,
                "bld_area": bld_ar,
                "footprint_area_sqft": round(fp_area_sq, 1),
                "land_area": round(float(snap.land_area_sqft or float(p_geom_2278.area)), 1),
                "stories": stories_val,
                "height_m": height_m,
                "geometry_source": g_src,
                "is_footprint": g_src == "observed_footprint",
                "historic_district": hd_name,
                "contributing": contrib,
                "own_tier": snap.own_tier or "homestead",
            }
            if snap.port_id:
                props_new["port_id"] = snap.port_id
            feat_obj = {
                "type": "Feature",
                "id": fid,
                "properties": props_new,
                "geometry": norm_geom,
            }
            q_dst = assign_quadrant(lon, lat)
            patched_by_quad[q_dst].append(feat_obj)
            delta.upserted_features_by_hcad[acct].append(feat_obj)
            delta.dirty_quadrants.add(q_dst)
            synthesized_count += 1

    # Run shared 4-phase fallback for ANY feature still carrying `year_built < MIN_VALID_YEAR` (DF-7)
    undated_targets: set[str] = set()
    undated_coords: dict[str, tuple[float, float]] = {}
    undated_rows: dict[str, dict[str, Any]] = {}
    all_feats_flat: list[tuple[str, dict[str, Any]]] = []
    for q in QUAD_NAMES:
        for feat in patched_by_quad.get(q, ()):
            all_feats_flat.append((q, feat))
            p = feat.get("properties") or {}
            yr = int(p.get("year_built") or 0)
            h = normalize_hcad13(p.get("hcad_num"))
            if yr < MIN_VALID_YEAR and h:
                undated_targets.add(h)
                ctr = _feature_centroid_wgs84(feat.get("geometry"))
                if ctr and h not in undated_coords:
                    undated_coords[h] = ctr
                snap_u = new_accounts.get(h)
                use_cat = str(p.get("use_category") or (snap_u.use_category if snap_u else ""))
                undated_rows.setdefault(h, {
                    "hcad_num": h,
                    "owner": str(p.get("owner") or (snap_u.owner if snap_u else "")),
                    "address": str(p.get("address") or (snap_u.numbered_address if snap_u else "")),
                    "state_class": snap_u.state_class if snap_u else "",
                    "use_category": use_cat,
                    "strategy_tier": "Tier 2" if use_cat == "Civic / Institutional" else "Tier 3",
                })

    phase_counts: Counter[str] = Counter()
    if undated_targets:
        cama_compact = {acct: sn.to_cama_tuple() for acct, sn in new_accounts.items()}
        curated_map: dict[str, tuple[Any, ...]] = dict(PHASE1_CURATED_LANDMARKS)
        curated_map.update(ADDITIONAL_CURATED_PARCEL_CHRONOLOGIES)
        dated_yr, dated_coord = seed_authoritative_dated_parcels(
            (f for _q, f in all_feats_flat),
            cama_compact,
            set(curated_map.keys()),
            max_year=eff_tax,
        )
        pre_res: dict[str, dict[str, Any]] = {}
        for h in sorted(undated_targets):
            if h in curated_map:
                ctup = curated_map[h]
                if len(ctup) >= 5:
                    cyr, lm_nm, sty, arch, nts = ctup[:5]
                else:
                    cyr, lm_nm, sty, nts = ctup[:4]
                    arch = ""
                pre_entry: dict[str, Any] = {
                    "year_built": int(cyr),
                    "year_source": "curated_landmark",
                    "landmark_name": lm_nm,
                    "bld_style": sty,
                    "notes": nts,
                }
                if arch:
                    pre_entry["architect"] = arch
                pre_res[h] = pre_entry
                phase_counts["p1_curated_landmark"] += 1
        xf_res = stream_extra_feature_years(
            bldg_zip_path,
            undated_targets,
            exclude_accts=set(pre_res.keys()),
            max_year=eff_tax,
        )
        for h, ri in pre_res.items():
            dated_yr[h] = int(ri["year_built"])
            if h in undated_coords:
                dated_coord[h] = undated_coords[h]
        for h, ri in xf_res.items():
            dated_yr[h] = int(ri["year_built"])
            if h in undated_coords:
                dated_coord[h] = undated_coords[h]

        fb_ctx = build_undated_fallback_context(
            acct_zip_path,
            undated_targets,
            dated_yr,
            dated_coord,
            max_year=eff_tax,
        )
        resolved_map, fb_counts = resolve_undated_feature_batch(
            undated_targets,
            undated_coords,
            dated_yr,
            fb_ctx,
            extra_feature_resolutions=xf_res,
            pre_resolved=pre_res,
            target_rows=undated_rows,
            thc_markers_path=Path(cache_dir) / "thc_markers_harris.json",
            use_nearest_dated_tree=True,
            dated_parcel_coord=dated_coord,
            max_year=eff_tax,
        )
        phase_counts.update(fb_counts)

        for q_name, feat in all_feats_flat:
            p = feat.get("properties") or {}
            h = normalize_hcad13(p.get("hcad_num"))
            if int(p.get("year_built") or 0) < MIN_VALID_YEAR and h in resolved_map:
                rinfo = resolved_map[h]
                ryr = int(rinfo["year_built"])
                p["year_built"] = ryr
                p["decade"] = compute_decade(ryr)
                p["year_source"] = str(rinfo["year_source"])
                if rinfo.get("landmark_name") and not p.get("landmark_name"):
                    p["landmark_name"] = sanitize_atlas_text(str(rinfo["landmark_name"]))
                if rinfo.get("bld_style") and not p.get("bld_style"):
                    p["bld_style"] = sanitize_atlas_text(str(rinfo["bld_style"]))
                if rinfo.get("architect") and not p.get("architect"):
                    p["architect"] = sanitize_atlas_text(str(rinfo["architect"]))
                delta.dirty_quadrants.add(q_name)
                delta.inner_core_touched = True
                if len(h) >= 3:
                    delta.touched_permit_prefixes.add(h[:3])
                bucket = delta.upserted_features_by_hcad.setdefault(h, [])
                if all(existing is not feat for existing in bucket):
                    bucket.append(feat)

    return {
        "synthesized_footprints": synthesized_count,
        "observed_footprints_matched": observed_used_count,
        "undated_parcels_resolved": len(undated_targets),
        "fallback_phase_counts": dict(phase_counts),
    }



# ---------------------------------------------------------------------------
# Stage 4: ArtifactSyncDelta Reconciliation Across Derived Indexes & Exports
# ---------------------------------------------------------------------------

EXPORT_DISTRICT_SLUG_ALIASES: dict[str, str] = {
    "courtland_place_historic_district": "courtlandt_place_historic_district",
}


def _slugify_export_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")


def _stream_targeted_enrichment_records(
    target_hcads: set[str],
    new_accounts: dict[str, HcadAccountSnapshot],
    bldg_zip_path: Path,
    acct_zip_path: Path,
    *,
    tax_year: int,
) -> dict[str, dict[str, Any]]:
    """Build compact `permits_<prefix3>.json` + deed records strictly for touched HCADs (DF-1, DF-5)."""
    if not target_hcads:
        return {}
    eff_tax = int(tax_year)
    records: dict[str, dict[str, Any]] = {}
    for acct in target_hcads:
        sn = new_accounts.get(acct)
        if sn is None:
            continue
        rec: dict[str, Any] = {}
        yr_v = sn.earliest_year if sn.earliest_year >= MIN_VALID_YEAR else sn.yr_impr
        if yr_v >= MIN_VALID_YEAR:
            rec["yr"] = int(yr_v)
        if sn.eff_year >= MIN_VALID_YEAR and sn.eff_year != yr_v:
            rec["eff"] = int(sn.eff_year)
        if sn.max_remodel_year >= MIN_VALID_YEAR:
            rec["remod"] = int(sn.max_remodel_year)
        if sn.quality_desc:
            rec["qual"] = sn.quality_desc
        if sn.cama_notes:
            rec["note"] = sn.cama_notes[:240]
        if sn.was_relocated and sn.relocation_note:
            rec["reloc"] = sn.relocation_note[:240]
        records[acct] = rec

    xf_by_acct: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if bldg_zip_path.exists():
        with zipfile.ZipFile(bldg_zip_path, "r") as zf:
            for member in ("extra_features_detail1.txt", "extra_features_detail2.txt"):
                xf_member = find_zip_member_case_insensitive(zf, member)
                if xf_member is None:
                    continue
                with zf.open(xf_member, "r") as raw_f:
                    tf = io.TextIOWrapper(raw_f, encoding="latin-1", errors="replace")
                    hdr = [c.strip().lower() for c in tf.readline().rstrip("\r\n").split("\t")]
                    col = {n: i for i, n in enumerate(hdr)}
                    i_a = col.get("acct", 0)
                    i_d = col.get("dscr", -1)
                    i_ay = col.get("act_yr", -1)
                    for line in tf:
                        parts = line.rstrip("\r\n").split("\t")
                        if len(parts) <= i_a:
                            continue
                        ac = normalize_hcad13(parts[i_a])
                        if ac not in records:
                            continue
                        dscr_raw = parts[i_d] if i_d >= 0 and len(parts) > i_d else ""
                        ay_raw = parts[i_ay] if i_ay >= 0 and len(parts) > i_ay else ""
                        item_xf = normalize_hcad_extra_feature_row(
                            dscr_raw, ay_raw, max_year=eff_tax
                        )
                        if item_xf is None:
                            continue
                        if len(xf_by_acct[ac]) < EXTRA_FEATURES_CAP_PER_ACCOUNT:
                            xf_by_acct[ac].append(item_xf)

    permits_by_acct: dict[str, list[dict[str, Any]]] = defaultdict(list)
    reloc_by_acct: dict[str, dict[str, str]] = {}
    deeds_by_acct: dict[str, dict[str, str]] = defaultdict(dict)
    max_deed_year = eff_tax + 1
    if acct_zip_path.exists():
        with zipfile.ZipFile(acct_zip_path, "r") as zf:
            permits_member = find_zip_member_case_insensitive(zf, "permits.txt")
            if permits_member is not None:
                with zf.open(permits_member, "r") as raw_f:
                    tf = io.TextIOWrapper(raw_f, encoding="latin-1", errors="replace")
                    hdr = [c.strip().lower() for c in tf.readline().rstrip("\r\n").split("\t")]
                    col = {n: i for i, n in enumerate(hdr)}
                    ip_a = col.get("acct", 0)
                    ip_id = col.get("id", -1)
                    ip_ds = col.get("dscr", -1)
                    ip_tp = col.get("permit_type", -1)
                    ip_td = col.get("permit_tp_descr", -1)
                    ip_dt = col.get("issue_date", -1)
                    for line in tf:
                        parts = line.rstrip("\r\n").split("\t")
                        if len(parts) <= ip_a:
                            continue
                        ac = normalize_hcad13(parts[ip_a])
                        if ac not in records:
                            continue
                        dt_raw = parts[ip_dt].strip() if ip_dt >= 0 and len(parts) > ip_dt else ""
                        td_raw = parts[ip_td].strip() if ip_td >= 0 and len(parts) > ip_td else ""
                        tp_raw = parts[ip_tp].strip() if ip_tp >= 0 and len(parts) > ip_tp else ""
                        ds_raw = parts[ip_ds].strip() if ip_ds >= 0 and len(parts) > ip_ds else ""
                        id_raw = parts[ip_id].strip() if ip_id >= 0 and len(parts) > ip_id else ""
                        p_obj, reloc_meta = normalize_hcad_permit_row(
                            id_raw, ds_raw, tp_raw, td_raw, dt_raw, max_year=eff_tax
                        )
                        if reloc_meta is not None:
                            reloc_by_acct[ac] = reloc_meta
                        permits_by_acct[ac].append(p_obj)

            deeds_member = find_zip_member_case_insensitive(zf, "deeds.txt")
            if deeds_member is not None:
                with zf.open(deeds_member, "r") as raw_f:
                    tf = io.TextIOWrapper(raw_f, encoding="latin-1", errors="replace")
                    hdr = [c.strip().lower() for c in tf.readline().rstrip("\r\n").split("\t")]
                    col = {n: i for i, n in enumerate(hdr)}
                    id_a = col.get("acct", 0)
                    id_dos = col.get("dos", 1)
                    id_cyr = col.get("clerk_yr", 2)
                    id_cid = col.get("clerk_id", 3)
                    for line in tf:
                        parts = line.rstrip("\r\n").split("\t")
                        if len(parts) <= id_a:
                            continue
                        ac = normalize_hcad13(parts[id_a])
                        if ac not in records:
                            continue
                        dos_s = parts[id_dos].strip() if len(parts) > id_dos else ""
                        cyr_s = parts[id_cyr].strip() if len(parts) > id_cyr else ""
                        cid_s = parts[id_cid].strip() if len(parts) > id_cid else ""
                        inst_tok = normalize_hcad_clerk_instrument(
                            cyr_s, cid_s, max_year=max_deed_year
                        )
                        if not inst_tok:
                            continue
                        dt8 = parse_dos_yyyymmdd(dos_s, cyr_s, max_year=max_deed_year)
                        prev_dt = deeds_by_acct[ac].get(inst_tok)
                        dt_key = (dt8 != "00000000" and not dt8.endswith("0000"), dt8)
                        prev_key = (
                            (prev_dt != "00000000" and not prev_dt.endswith("0000"), prev_dt)
                            if prev_dt is not None
                            else None
                        )
                        if prev_key is None or dt_key > prev_key:
                            deeds_by_acct[ac][inst_tok] = dt8

    from atlas_pipeline.build_ownership_and_history import (
        AccountOwnershipRecord,
        build_account_enrichment_fields,
    )

    for ac, rec in records.items():
        if reloc_by_acct.get(ac):
            rm = reloc_by_acct[ac]
            rec["reloc"] = (rm.get("note") or rm.get("reason") or "Relocated Structure")[:240]
        if xf_by_acct.get(ac):
            rec["xf"] = xf_by_acct[ac]
        if permits_by_acct.get(ac):
            rec["p"] = finalize_hcad_account_permits(permits_by_acct[ac])
        sorted_pairs: list[tuple[str, str]] = []
        if deeds_by_acct.get(ac):
            sorted_pairs = sorted(
                deeds_by_acct[ac].items(),
                key=lambda kv: (kv[1], kv[0]),
                reverse=True,
            )[:MAX_DEEDS_PER_PARCEL]

        sn_ac = new_accounts.get(ac)
        if sn_ac is not None:
            orec = AccountOwnershipRecord(
                acct=ac,
                own_tier=sn_ac.own_tier or "homestead",
                port_id=sn_ac.port_id or "",
                owner_name=sn_ac.owner or "",
                situs_addr=sn_ac.numbered_address or sn_ac.raw_site_addr_1 or "",
                state_class=sn_ac.state_class or "",
                subdivision=sn_ac.subdivision or "",
                display_mail=sn_ac.display_mail or "",
                care_of=sn_ac.care_of or "",
                co_owners=list(sn_ac.co_owners),
                is_protected_homestead=bool(sn_ac.is_protected_homestead),
                is_sec_25_025_confidential=bool(sn_ac.is_sec_25_025_confidential),
                latest_demo_permit_date=sn_ac.demo_permit_date if sn_ac.has_main_demo_permit else "",
            )
            own_fields = build_account_enrichment_fields(
                orec, sorted_pairs, include_non_homestead_mail=True
            )
            rec.update(own_fields)
        elif sorted_pairs:
            rec["d"] = "|".join(f"{dt8}:{tok}" for tok, dt8 in sorted_pairs)

    return {ac: r for ac, r in records.items() if r}


def _recompute_export_district_stats(
    bldg_features: list[dict[str, Any]],
    landmark_hcads: set[str],
    good_brick_hcads: set[str],
) -> dict[str, Any]:
    years = sorted(
        int((f.get("properties") or {}).get("year_built") or 0)
        for f in bldg_features
        if int((f.get("properties") or {}).get("year_built") or 0) >= MIN_VALID_YEAR
    )
    seen_lm: set[str] = set()
    seen_gb: set[str] = set()
    for f in bldg_features:
        p = f.get("properties") or {}
        h = normalize_hcad13(p.get("hcad_num"))
        if h:
            if h in landmark_hcads:
                seen_lm.add(h)
            if h in good_brick_hcads:
                seen_gb.add(h)
    med_yr = int(round(float(np.median(years)))) if years else 0
    return {
        "buildingCount": len(bldg_features),
        "earliestYear": int(years[0]) if years else 0,
        "medianYear": med_yr,
        "landmarkCount": len(seen_lm),
        "goodBrickCount": len(seen_gb),
    }


def _resolve_split_successor_hcad(
    delta: ArtifactSyncDelta,
    old_hcad: str,
    candidate_ids: tuple[str, ...],
    *,
    wgs84_lon_lat: tuple[float, float] | None,
    to_2278: Transformer,
    parcel_geoms_2278: Any,
    parcel_hcad_to_idx: dict[str, int],
) -> str:
    """Resolve exact per-footprint successor HCAD across `1 -> 1`, `M -> 1`, and `1 -> K` splits."""
    for cid in candidate_ids:
        if cid and cid in delta.old_fid_to_new_hcad:
            return delta.old_fid_to_new_hcad[cid]
    succ_cands = delta.old_to_all_new_hcads.get(old_hcad) or ()
    if len(succ_cands) > 1 and wgs84_lon_lat is not None:
        try:
            px, py = to_2278.transform(float(wgs84_lon_lat[0]), float(wgs84_lon_lat[1]))
            pt_2278 = shapely.points(px, py)
            best_dist = float("inf")
            best_h = ""
            for sh in succ_cands:
                p_idx = parcel_hcad_to_idx.get(sh)
                if p_idx is None:
                    continue
                pg = parcel_geoms_2278[p_idx]
                if pg.covers(pt_2278):
                    return sh
                d = float(pg.distance(pt_2278))
                if d < best_dist:
                    best_dist = d
                    best_h = sh
            if best_h:
                return best_h
        except Exception:
            pass
    return delta.old_to_new_hcads.get(old_hcad, "")


def reconcile_derived_building_indexes(
    staged_data_dir: Path,
    patched_by_quad: dict[str, list[dict[str, Any]]],
    delta: ArtifactSyncDelta,
    new_accounts: dict[str, HcadAccountSnapshot],
    *,
    parcel_geoms_2278: Any,
    parcel_hcad_to_idx: dict[str, int],
    bldg_zip_path: Path,
    acct_zip_path: Path,
    tax_year: int,
    cache_bust: str,
    shard_filenames: list[str],
    total_pmtiles_bytes: int,
    compile_core_pmtiles: bool = True,
    live_output_dir: Path | None = None,
) -> dict[str, Any]:
    """Propagate `ArtifactSyncDelta` deletions, re-keyings, and insertions across all derived indexes (DF-4)."""
    ddir = Path(staged_data_dir)
    eff_tax = int(tax_year)
    from_2278 = Transformer.from_crs("EPSG:2278", "EPSG:4326", always_xy=True)
    to_2278 = Transformer.from_crs("EPSG:4326", "EPSG:2278", always_xy=True)

    ov_path = ddir / "curated_overrides.json"
    curated_doc = json.loads(ov_path.read_text(encoding="utf-8")) if ov_path.exists() else {}
    overrides_index = build_override_protection_index(curated_doc, default_updated_year=eff_tax)

    pure_deleted_hcads = set(delta.deleted_hcads) - set(delta.old_to_new_hcads.keys())
    all_retired_or_deleted_hcads = set(delta.deleted_hcads) | set(delta.old_to_new_hcads.keys())
    all_successor_hcads = set(delta.old_to_new_hcads.values()) | set(delta.old_fid_to_new_hcad.values())
    for succ_list in delta.old_to_all_new_hcads.values():
        all_successor_hcads.update(succ_list)

    upserted_by_entity_id: dict[str, dict[str, Any]] = {}
    single_upsert_by_hcad: dict[str, dict[str, Any]] = {}
    for h, flist in delta.upserted_features_by_hcad.items():
        for uf in flist:
            up_props = uf.get("properties") or {}
            for uk in (up_props.get("building_id"), up_props.get("id"), uf.get("id")):
                if uk:
                    upserted_by_entity_id[str(uk)] = uf
        if len(flist) == 1:
            only_props = flist[0].get("properties") or {}
            only_bid = str(only_props.get("building_id") or only_props.get("id") or "")
            if "#" not in only_bid:
                single_upsert_by_hcad[h] = flist[0]

    ovl_path = ddir / "overlays.json"
    ovl_doc = json.loads(ovl_path.read_text(encoding="utf-8")) if ovl_path.exists() else {}
    landmark_hcads = {
        normalize_hcad13((f.get("properties") or {}).get("hcad_num"))
        for f in (ovl_doc.get("landmarks") or {}).get("features") or ()
        if not (f.get("properties") or {}).get("demolished")
    } - {""}
    good_brick_hcads = {
        normalize_hcad13(
            (f.get("properties") or {}).get("hcad_num")
            or str((f.get("properties") or {}).get("building_id") or "").split("#")[0]
        )
        for f in (ovl_doc.get("good_brick_awards") or {}).get("features") or ()
    } - {""}

    # 1. Reconcile inner-core buildings.geojson, parcels.geojson, and houston_atlas.pmtiles
    core_blds_path = ddir / "buildings.geojson"
    core_pcls_path = ddir / "parcels.geojson"
    core_changed = bool(delta.inner_core_touched)
    core_blds_added = 0
    core_blds_removed = 0

    if core_blds_path.exists():
        core_doc = json.loads(core_blds_path.read_text(encoding="utf-8"))
        orig_core_feats = core_doc.get("features") or []
        core_hcad_counts = Counter(
            normalize_hcad13((f.get("properties") or {}).get("hcad_num"))
            for f in orig_core_feats
        )
        core_geoms_sample = []
        for cf in orig_core_feats[:2000]:
            cg = cf.get("geometry")
            if cg:
                try:
                    core_geoms_sample.append(shape(cg))
                except Exception:
                    pass
        core_hull = (
            shapely.unary_union(core_geoms_sample).convex_hull.buffer(0.002)
            if core_geoms_sample
            else None
        )

        new_core_feats: list[dict[str, Any]] = []
        seen_core_hcads: set[str] = set()
        seen_core_ids: set[str] = set()
        for cf in orig_core_feats:
            cp = cf.get("properties") or {}
            orig_bid = str(cp.get("building_id") or "")
            orig_pid = str(cp.get("id") or cf.get("id") or "")
            cid = orig_bid or orig_pid
            ch = normalize_hcad13(cp.get("hcad_num"))
            is_prot = is_feature_chronology_protected(cid, cp, overrides_index)
            if not is_prot and (cid in delta.deleted_building_ids or orig_pid in delta.deleted_building_ids or ch in pure_deleted_hcads):
                core_blds_removed += 1
                core_changed = True
                continue
            cf_out = copy.deepcopy(cf)
            cp_out = cf_out.setdefault("properties", {})
            mapped_h = (
                _resolve_split_successor_hcad(
                    delta,
                    ch,
                    (orig_bid, orig_pid, cid),
                    wgs84_lon_lat=_feature_centroid_wgs84(cf.get("geometry")),
                    to_2278=to_2278,
                    parcel_geoms_2278=parcel_geoms_2278,
                    parcel_hcad_to_idx=parcel_hcad_to_idx,
                )
                if not is_prot
                else ""
            )
            is_multi_core = core_hcad_counts.get(ch, 0) > 1
            if mapped_h and mapped_h != ch:
                cp_out["hcad_num"] = mapped_h
                ch = mapped_h
                core_changed = True
            if not is_prot:
                mapped_id = delta.old_to_new_building_ids.get(orig_bid) or delta.old_to_new_building_ids.get(cid)
                if mapped_id:
                    cp_out["building_id"] = mapped_id
                    if not str(cp_out.get("id") or "").startswith("bld_"):
                        cp_out["id"] = mapped_id
                        if "id" in cf_out:
                            cf_out["id"] = mapped_id
                    cid = str(cp_out.get("id") or mapped_id)
                    core_changed = True

            eff_bid = str(cp_out.get("building_id") or "")
            matched_up = (
                upserted_by_entity_id.get(eff_bid)
                or upserted_by_entity_id.get(cid)
                or (
                    single_upsert_by_hcad.get(ch)
                    if (not is_multi_core and "#" not in eff_bid and "#" not in cid)
                    else None
                )
            )
            if not is_prot and matched_up is not None:
                src_p = matched_up.get("properties") or {}
                src_h = normalize_hcad13(src_p.get("hcad_num"))
                if src_h and src_h != ch:
                    ch = src_h
                    cp_out["hcad_num"] = ch
                    core_changed = True
                for k_sync in (
                    "year_built",
                    "decade",
                    "year_source",
                    "remodel_year",
                    "owner",
                    "bld_area",
                    "land_area",
                    "bld_style",
                    "use_category",
                    "address",
                ):
                    if k_sync in src_p and src_p[k_sync] not in (None, "") and cp_out.get(k_sync) != src_p[k_sync]:
                        if k_sync == "address" and is_school_situs_protected(cp_out):
                            continue
                        cp_out[k_sync] = src_p[k_sync]
                        core_changed = True
            elif not is_prot and is_multi_core and ch in new_accounts:
                snap_m = new_accounts[ch]
                if snap_m.owner and cp_out.get("owner") != snap_m.owner:
                    cp_out["owner"] = snap_m.owner
                    core_changed = True
                if snap_m.land_area_sqft > 0.0:
                    la_v = round(snap_m.land_area_sqft, 1)
                    if cp_out.get("land_area") != la_v:
                        cp_out["land_area"] = la_v
                        core_changed = True
                if snap_m.max_remodel_year >= MIN_VALID_YEAR and int(cp_out.get("remodel_year") or 0) != snap_m.max_remodel_year:
                    cp_out["remodel_year"] = int(snap_m.max_remodel_year)
                    core_changed = True
                if (
                    snap_m.numbered_address
                    and not is_school_situs_protected(cp_out)
                    and cp_out.get("address") != snap_m.numbered_address
                ):
                    cp_out["address"] = snap_m.numbered_address
                    core_changed = True
            if ch:
                seen_core_hcads.add(ch)
            if cid:
                seen_core_ids.add(cid)
            if eff_bid:
                seen_core_ids.add(eff_bid)
            new_core_feats.append(cf_out)

        if core_hull is not None:
            for nh in sorted(delta.new_construction_hcads):
                for nfeat in delta.upserted_features_by_hcad.get(nh, ()):
                    ctr = _feature_centroid_wgs84(nfeat.get("geometry"))
                    if ctr and core_hull.covers(shapely.points(ctr[0], ctr[1])):
                        nf_copy = copy.deepcopy(nfeat)
                        nf_copy["geometry"] = normalize_polygon_rings_6dp_ccw(nf_copy["geometry"])
                        np_copy = nf_copy.get("properties") or {}
                        nid = str(np_copy.get("building_id") or np_copy.get("id") or nh)
                        if nid not in seen_core_ids:
                            new_core_feats.append(nf_copy)
                            seen_core_ids.add(nid)
                            seen_core_hcads.add(nh)
                            core_blds_added += 1
                            core_changed = True

        if core_changed:
            core_doc["features"] = new_core_feats
            write_compact_json(core_blds_path, core_doc)

            if core_pcls_path.exists():
                pcl_doc = json.loads(core_pcls_path.read_text(encoding="utf-8"))
                kept_pcls: list[dict[str, Any]] = []
                have_pcl_hcads: set[str] = set()
                for pf in pcl_doc.get("features") or ():
                    ph = normalize_hcad13((pf.get("properties") or {}).get("hcad_num"))
                    if ph in all_retired_or_deleted_hcads:
                        continue
                    if ph:
                        have_pcl_hcads.add(ph)
                    kept_pcls.append(pf)
                need_pcls = (seen_core_hcads - have_pcl_hcads) & set(parcel_hcad_to_idx.keys())
                for ph in sorted(need_pcls):
                    pg22 = parcel_geoms_2278[parcel_hcad_to_idx[ph]]
                    pg_wgs = _reproject_shapely_geom(pg22, from_2278)
                    norm_pg = normalize_polygon_rings_6dp_ccw(dict(mapping(pg_wgs)))
                    sn_p = new_accounts.get(ph)
                    kept_pcls.append({
                        "type": "Feature",
                        "id": f"pcl_{ph}",
                        "properties": {
                            "id": f"pcl_{ph}",
                            "hcad_num": ph,
                            "address": (sn_p.numbered_address or sn_p.raw_site_addr_1) if sn_p else "",
                            "owner": sn_p.owner if sn_p else "",
                            "year_built": int(sn_p.earliest_year or sn_p.yr_impr or 0) if sn_p else 0,
                            "land_area": round(float(sn_p.land_area_sqft if sn_p else pg22.area), 1),
                        },
                        "geometry": norm_pg,
                    })
                pcl_doc["features"] = kept_pcls
                write_compact_json(core_pcls_path, pcl_doc)

            if compile_core_pmtiles and core_pcls_path.exists():
                pcl_fc_now = json.loads(core_pcls_path.read_text(encoding="utf-8"))
                compile_pmtiles_archive(
                    ddir / "houston_atlas.pmtiles",
                    new_core_feats,
                    pcl_fc_now.get("features") or [],
                )

    # 2. Reconcile address_shards/addr_<2d>.json (deletions, prefix shifts, and 5/6/7-column upserts)
    addr_dir = ddir / "address_shards"
    addr_shards_updated = 0
    if addr_dir.exists():
        upserts_by_shard: dict[str, list[tuple[str, list[Any]]]] = defaultdict(list)
        upsert_entity_ids: set[str] = set()
        for h, flist in delta.upserted_features_by_hcad.items():
            for feat in flist:
                fp = feat.get("properties") or {}
                addr_s = str(fp.get("address") or "")
                sh_k, st_n, hn_s = _split_numbered_address_for_shard(addr_s)
                ctr = _feature_centroid_wgs84(feat.get("geometry"))
                if not sh_k or not st_n or not hn_s or ctr is None:
                    continue
                row_hcad = str(normalize_hcad13(fp.get("hcad_num")) or h)
                feat_bid = str(fp.get("building_id") or fp.get("id") or row_hcad)
                bname = sanitize_atlas_text(
                    str(fp.get("building_name") or fp.get("landmark_name") or fp.get("name") or "")
                ).strip()
                upsert_entity_ids.add(feat_bid)
                if fp.get("id"):
                    upsert_entity_ids.add(str(fp["id"]))
                row_out: list[Any] = [
                    hn_s,
                    round(ctr[0], 5),
                    round(ctr[1], 5),
                    int(fp.get("year_built") or 0),
                    row_hcad,
                ]
                if feat_bid and feat_bid != row_hcad:
                    row_out.extend([bname, feat_bid])
                elif bname:
                    row_out.append(bname)
                upserts_by_shard[sh_k].append((st_n, row_out))

        prune_parcel_hcads = set(all_retired_or_deleted_hcads)
        prune_entity_ids = (
            set(delta.deleted_building_ids)
            | set(delta.old_to_new_building_ids.keys())
            | (upsert_entity_ids - {""})
        )
        candidate_addr_files = (
            sorted(addr_dir.glob("addr_*.json"))
            if (prune_parcel_hcads or prune_entity_ids)
            else [addr_dir / f"addr_{k}.json" for k in sorted(upserts_by_shard.keys())]
        )
        seen_shard_keys: set[str] = set()
        for apath in candidate_addr_files:
            m_ak = re.fullmatch(r"addr_(\d{2})\.json", apath.name)
            if not m_ak:
                continue
            sk = m_ak.group(1)
            seen_shard_keys.add(sk)
            doc_a: dict[str, list[list[Any]]] = (
                json.loads(apath.read_text(encoding="utf-8")) if apath.exists() else {}
            )
            shard_dirty = False
            mig_pairs = delta.migrated_addr_prefixes_by_key.get(sk, set())
            for st_key in list(doc_a.keys()):
                orig_rows = doc_a[st_key]
                kept_rows: list[list[Any]] = []
                for row in orig_rows:
                    rhcad = str(row[4]) if len(row) > 4 else ""
                    rent = str(row[6]) if len(row) > 6 and row[6] else rhcad
                    full_a = f"{row[0]} {st_key}".strip().upper() if row else ""
                    addr_prot = is_feature_chronology_protected(
                        rent,
                        {"hcad_num": rhcad, "id": rent},
                        overrides_index,
                    )
                    if (
                        (
                            not addr_prot
                            and (rhcad in prune_parcel_hcads or rent in prune_entity_ids)
                        )
                        or (
                            mig_pairs
                            and (
                                (rent, full_a) in mig_pairs
                                or (rent == rhcad and (rhcad, full_a) in mig_pairs)
                            )
                        )
                    ):
                        shard_dirty = True
                        continue
                    kept_rows.append(row)
                if kept_rows:
                    doc_a[st_key] = kept_rows
                else:
                    del doc_a[st_key]
                    shard_dirty = True

            for st_n, new_row in upserts_by_shard.get(sk, ()):
                bucket = doc_a.setdefault(st_n, [])
                if new_row not in bucket:
                    bucket.append(new_row)
                    shard_dirty = True

            if shard_dirty:
                write_compact_json(apath, doc_a)
                addr_shards_updated += 1

        for sk in sorted(set(upserts_by_shard.keys()) - seen_shard_keys):
            apath = addr_dir / f"addr_{sk}.json"
            doc_a = json.loads(apath.read_text(encoding="utf-8")) if apath.exists() else {}
            for st_n, new_row in upserts_by_shard[sk]:
                doc_a.setdefault(st_n, []).append(new_row)
            write_compact_json(apath, doc_a)
            addr_shards_updated += 1

    # 3. Reconcile search_index.json (preserve boundaries, Lost Houston lost_*, school_*, and protected entities)
    sidx_path = ddir / "search_index.json"
    search_items_removed = 0
    if sidx_path.exists():
        sidx = json.loads(sidx_path.read_text(encoding="utf-8"))
        sidx_hcad_counts = Counter(
            normalize_hcad13(it.get("hcad_num"))
            for it in sidx
            if isinstance(it, dict)
            and not is_boundary_search_entry(it)
            and not str(it.get("id") or "").startswith(("lost_", "school_"))
        )
        new_sidx: list[dict[str, Any]] = []
        existing_sidx_ids: set[str] = set()
        sidx_dirty = False
        for item in sidx:
            if not isinstance(item, dict):
                continue
            iid = str(item.get("id") or "")
            if is_boundary_search_entry(item) or iid.startswith("lost_"):
                new_sidx.append(item)
                if iid:
                    existing_sidx_ids.add(iid)
                continue
            raw_lon = item.get("lon") if item.get("lon") is not None else item.get("lng")
            sidx_ll = (
                (float(raw_lon), float(item["lat"]))
                if raw_lon is not None and item.get("lat") is not None
                else None
            )
            if iid.startswith("school_"):
                ih_s = normalize_hcad13(item.get("hcad_num"))
                s_bid = str(item.get("building_id") or "")
                s_prot = is_feature_chronology_protected(s_bid or iid, item, overrides_index)
                if not s_prot and (
                    ih_s in pure_deleted_hcads
                    or iid in delta.deleted_building_ids
                    or (s_bid and s_bid in delta.deleted_building_ids)
                ):
                    search_items_removed += 1
                    sidx_dirty = True
                    continue
                mapped_sh = _resolve_split_successor_hcad(
                    delta,
                    ih_s,
                    (s_bid, iid),
                    wgs84_lon_lat=sidx_ll,
                    to_2278=to_2278,
                    parcel_geoms_2278=parcel_geoms_2278,
                    parcel_hcad_to_idx=parcel_hcad_to_idx,
                )
                mapped_sbid = delta.old_to_new_building_ids.get(s_bid, "") if s_bid else ""
                if (mapped_sh and mapped_sh != ih_s) or (mapped_sbid and mapped_sbid != s_bid):
                    item = dict(item)
                    if mapped_sh and mapped_sh != ih_s:
                        item["hcad_num"] = mapped_sh
                    if mapped_sbid and "building_id" in item:
                        item["building_id"] = mapped_sbid
                    sidx_dirty = True
                new_sidx.append(item)
                existing_sidx_ids.add(iid)
                continue

            ih = normalize_hcad13(item.get("hcad_num"))
            ibid = str(item.get("building_id") or iid)
            sidx_prot = is_feature_chronology_protected(ibid, item, overrides_index)
            if not sidx_prot and (
                ih in pure_deleted_hcads
                or iid in delta.deleted_building_ids
                or ibid in delta.deleted_building_ids
            ):
                search_items_removed += 1
                sidx_dirty = True
                continue
            item_out = dict(item)
            is_multi_sidx = sidx_hcad_counts.get(ih, 0) > 1
            mapped_ih = (
                _resolve_split_successor_hcad(
                    delta,
                    ih,
                    (ibid, iid),
                    wgs84_lon_lat=sidx_ll,
                    to_2278=to_2278,
                    parcel_geoms_2278=parcel_geoms_2278,
                    parcel_hcad_to_idx=parcel_hcad_to_idx,
                )
                if not sidx_prot
                else ""
            )
            if mapped_ih and mapped_ih != ih:
                ih = mapped_ih
                item_out["hcad_num"] = ih
                sidx_dirty = True
            if not sidx_prot and ibid in delta.old_to_new_building_ids:
                mapped_b = delta.old_to_new_building_ids[ibid]
                if "building_id" in item_out:
                    item_out["building_id"] = mapped_b
                if not iid.startswith("bld_"):
                    item_out["id"] = mapped_b
                    iid = mapped_b
                ibid = mapped_b
                sidx_dirty = True
            eff_ibid = str(item_out.get("building_id") or iid)
            matched_sidx_up = (
                upserted_by_entity_id.get(eff_ibid)
                or upserted_by_entity_id.get(iid)
                or (
                    single_upsert_by_hcad.get(ih)
                    if (not is_multi_sidx and "#" not in eff_ibid and "#" not in iid)
                    else None
                )
            )
            if not sidx_prot and matched_sidx_up is not None:
                up_p = matched_sidx_up.get("properties") or {}
                up_h = normalize_hcad13(up_p.get("hcad_num"))
                if up_h and up_h != ih:
                    ih = up_h
                    item_out["hcad_num"] = ih
                    sidx_dirty = True
                ny = int(up_p.get("year_built") or 0)
                na = str(up_p.get("address") or "")
                if ny >= MIN_VALID_YEAR and int(item_out.get("year_built") or 0) != ny:
                    item_out["year_built"] = ny
                    sub = str(item_out.get("sublabel") or "")
                    if "Built " in sub:
                        item_out["sublabel"] = re.sub(r"Built \d{4}", f"Built {ny}", sub)
                    sidx_dirty = True
                if (
                    na
                    and item_out.get("address")
                    and item_out.get("address") != na
                    and not is_school_situs_protected(item_out)
                ):
                    item_out["address"] = na
                    sidx_dirty = True
            new_sidx.append(item_out)
            if iid:
                existing_sidx_ids.add(iid)

        for nh in sorted(delta.new_construction_hcads):
            for nfeat in delta.upserted_features_by_hcad.get(nh, ()):
                np_f = nfeat.get("properties") or {}
                nm = str(np_f.get("landmark_name") or np_f.get("building_name") or "").strip()
                nid = str(np_f.get("id") or nh)
                ctr = _feature_centroid_wgs84(nfeat.get("geometry"))
                if nm and nid not in existing_sidx_ids and ctr:
                    ny = int(np_f.get("year_built") or 0)
                    na = str(np_f.get("address") or "")
                    new_sidx.append({
                        "type": "building",
                        "id": nid,
                        "hcad_num": nh,
                        "label": nm,
                        "name": nm,
                        "address": na,
                        "sublabel": sanitize_atlas_text(f"{na} · Built {ny}".strip(" ·")),
                        "category": str(np_f.get("use_category") or "Building"),
                        "year_built": ny,
                        "lat": round(ctr[1], 6),
                        "lon": round(ctr[0], 6),
                        "zoom": 17.5,
                    })
                    existing_sidx_ids.add(nid)
                    sidx_dirty = True

        if sidx_dirty:
            write_compact_json(sidx_path, new_sidx)

    # 4. Reconcile permits/permits_<prefix3>.json & embedded deed chains
    permits_dir = ddir / "permits"
    permit_files_updated = 0
    if permits_dir.exists():
        existing_permit_hcads: set[str] = set()
        declassified_conf_hcads: set[str] = set()
        for existing_ppath in sorted(permits_dir.glob("permits_*.json")):
            try:
                existing_pdoc = json.loads(existing_ppath.read_text(encoding="utf-8"))
            except Exception:
                existing_pdoc = {}
            if not isinstance(existing_pdoc, dict):
                continue
            for eh, erec in existing_pdoc.items():
                existing_permit_hcads.add(eh)
                if (
                    isinstance(erec, dict)
                    and erec.get("conf")
                    and eh in new_accounts
                    and not new_accounts[eh].is_sec_25_025_confidential
                ):
                    declassified_conf_hcads.add(eh)

        conf_hcads = {
            ac
            for ac, sn in new_accounts.items()
            if sn.is_sec_25_025_confidential and (sn.is_improved or ac in existing_permit_hcads)
        }
        refresh_permit_hcads = (
            set(delta.upserted_features_by_hcad.keys())
            | set(delta.new_construction_hcads)
            | all_successor_hcads
            | conf_hcads
            | declassified_conf_hcads
        )
        fresh_permits = _stream_targeted_enrichment_records(
            refresh_permit_hcads,
            new_accounts,
            bldg_zip_path,
            acct_zip_path,
            tax_year=eff_tax,
        )
        touched_pfxs = set(delta.touched_permit_prefixes)
        for h in all_retired_or_deleted_hcads | refresh_permit_hcads:
            if len(h) >= 3:
                touched_pfxs.add(h[:3])

        for pfx in sorted(touched_pfxs):
            ppath = permits_dir / f"permits_{pfx}.json"
            pdoc: dict[str, Any] = (
                json.loads(ppath.read_text(encoding="utf-8")) if ppath.exists() else {}
            )
            p_dirty = False
            for dh in list(pdoc.keys()):
                if dh in all_retired_or_deleted_hcads:
                    del pdoc[dh]
                    p_dirty = True
                elif (
                    isinstance(pdoc.get(dh), dict)
                    and pdoc[dh].get("conf")
                    and dh not in fresh_permits
                    and dh in new_accounts
                    and not new_accounts[dh].is_sec_25_025_confidential
                ):
                    pdoc[dh].pop("conf", None)
                    if not pdoc[dh]:
                        del pdoc[dh]
                    p_dirty = True
            for fh, frec in fresh_permits.items():
                if fh.startswith(pfx):
                    if pdoc.get(fh) != frec:
                        pdoc[fh] = frec
                        p_dirty = True
            if p_dirty:
                if pdoc:
                    write_compact_json(ppath, pdoc)
                elif ppath.exists():
                    write_compact_json(ppath, {})
                permit_files_updated += 1

    # 5. Reconcile all 24 export_cache/*.json historic district bundles
    exp_dir = ddir / "export_cache"
    exports_updated = 0
    if exp_dir.exists():
        overlay_poly_by_key: dict[tuple[str, str], Any] = {}
        for lyr_k, lyr_fc in ovl_doc.items():
            if not isinstance(lyr_fc, dict) or not isinstance(lyr_fc.get("features"), list):
                continue
            for feat_o in lyr_fc["features"]:
                nm_o = str((feat_o.get("properties") or {}).get("name") or "")
                g_o = feat_o.get("geometry")
                if nm_o and g_o and g_o.get("type") in ("Polygon", "MultiPolygon"):
                    try:
                        sg = shape(g_o)
                        if not sg.is_valid:
                            sg = make_valid(sg)
                        overlay_poly_by_key[(lyr_k, _slugify_export_name(nm_o))] = sg
                    except Exception:
                        pass

        candidate_inserts: list[tuple[Any, dict[str, Any]]] = []
        for h in sorted(delta.new_construction_hcads | all_successor_hcads):
            for feat in delta.upserted_features_by_hcad.get(h, ()):
                ctr = _feature_centroid_wgs84(feat.get("geometry"))
                if ctr:
                    candidate_inserts.append((shapely.points(ctr[0], ctr[1]), feat))

        for epath in sorted(exp_dir.glob("*.json")):
            edoc = json.loads(epath.read_text(encoding="utf-8"))
            if not isinstance(edoc, dict) or not isinstance(edoc.get("buildings"), list):
                continue
            stem = epath.stem
            parts_s = stem.split("__", 1)
            dist_poly = None
            if len(parts_s) == 2:
                lyr_s, slug_s = parts_s[0], parts_s[1]
                canon_slug = EXPORT_DISTRICT_SLUG_ALIASES.get(slug_s, slug_s)
                dist_poly = overlay_poly_by_key.get((lyr_s, slug_s)) or overlay_poly_by_key.get(
                    (lyr_s, canon_slug)
                )
            if dist_poly is None and edoc["buildings"]:
                try:
                    b_geoms = [shape(bf["geometry"]) for bf in edoc["buildings"] if bf.get("geometry")]
                    if b_geoms:
                        dist_poly = shapely.unary_union(b_geoms).convex_hull.buffer(0.001)
                except Exception:
                    dist_poly = None

            exp_hcad_counts = Counter(
                normalize_hcad13((bf.get("properties") or {}).get("hcad_num"))
                for bf in edoc["buildings"]
            )
            kept_exp: list[dict[str, Any]] = []
            seen_exp_ids: set[str] = set()
            e_dirty = False
            for bf in edoc["buildings"]:
                bp = bf.get("properties") or {}
                orig_ebid = str(bp.get("building_id") or "")
                orig_epid = str(bp.get("id") or "")
                bid = orig_ebid or orig_epid
                bh = normalize_hcad13(bp.get("hcad_num"))
                exp_prot = is_feature_chronology_protected(bid, bp, overrides_index)
                if not exp_prot and (
                    bh in pure_deleted_hcads
                    or bid in delta.deleted_building_ids
                    or orig_epid in delta.deleted_building_ids
                ):
                    e_dirty = True
                    continue
                bf_out = copy.deepcopy(bf)
                bp_out = bf_out.setdefault("properties", {})
                is_multi_exp = exp_hcad_counts.get(bh, 0) > 1
                mapped_bh = (
                    _resolve_split_successor_hcad(
                        delta,
                        bh,
                        (orig_ebid, orig_epid, bid),
                        wgs84_lon_lat=_feature_centroid_wgs84(bf.get("geometry")),
                        to_2278=to_2278,
                        parcel_geoms_2278=parcel_geoms_2278,
                        parcel_hcad_to_idx=parcel_hcad_to_idx,
                    )
                    if not exp_prot
                    else ""
                )
                if mapped_bh and mapped_bh != bh:
                    bh = mapped_bh
                    bp_out["hcad_num"] = bh
                    e_dirty = True
                if not exp_prot and bid in delta.old_to_new_building_ids:
                    nbid = delta.old_to_new_building_ids[bid]
                    if "building_id" in bp_out:
                        bp_out["building_id"] = nbid
                    if not orig_epid.startswith("bld_"):
                        bp_out["id"] = nbid
                    bid = str(bp_out.get("building_id") or bp_out.get("id") or nbid)
                    e_dirty = True
                matched_exp_up = (
                    upserted_by_entity_id.get(bid)
                    or upserted_by_entity_id.get(str(bp_out.get("id") or ""))
                    or (
                        single_upsert_by_hcad.get(bh)
                        if (not is_multi_exp and "#" not in bid)
                        else None
                    )
                )
                if not exp_prot and matched_exp_up is not None:
                    up_p = matched_exp_up.get("properties") or {}
                    up_bh = normalize_hcad13(up_p.get("hcad_num"))
                    if up_bh and up_bh != bh:
                        bh = up_bh
                        bp_out["hcad_num"] = bh
                        e_dirty = True
                    ny = int(up_p.get("year_built") or 0)
                    na = str(up_p.get("address") or "")
                    if ny >= MIN_VALID_YEAR and int(bp_out.get("year_built") or 0) != ny:
                        bp_out["year_built"] = ny
                        e_dirty = True
                    if na and bp_out.get("address") != na and not is_school_situs_protected(bp_out):
                        bp_out["address"] = na
                        e_dirty = True
                elif not exp_prot and is_multi_exp and bh in new_accounts:
                    snap_e = new_accounts[bh]
                    if (
                        snap_e.numbered_address
                        and not is_school_situs_protected(bp_out)
                        and bp_out.get("address") != snap_e.numbered_address
                    ):
                        bp_out["address"] = snap_e.numbered_address
                        e_dirty = True
                if bid:
                    seen_exp_ids.add(bid)
                if bp_out.get("id"):
                    seen_exp_ids.add(str(bp_out["id"]))
                kept_exp.append(bf_out)

            if dist_poly is not None:
                for pt_c, feat_c in candidate_inserts:
                    fp_c = feat_c.get("properties") or {}
                    ch = normalize_hcad13(fp_c.get("hcad_num"))
                    cid = str(fp_c.get("building_id") or fp_c.get("id") or ch)
                    if cid in seen_exp_ids:
                        continue
                    if dist_poly.covers(pt_c):
                        kept_exp.append({
                            "type": "Feature",
                            "geometry": normalize_polygon_rings_6dp_ccw(feat_c["geometry"]),
                            "properties": {
                                "id": cid,
                                "building_id": cid,
                                "hcad_num": ch,
                                "year_built": int(fp_c.get("year_built") or 0),
                                "address": str(fp_c.get("address") or ""),
                                "building_name": str(
                                    fp_c.get("landmark_name") or fp_c.get("building_name") or ""
                                ),
                            },
                        })
                        seen_exp_ids.add(cid)
                        e_dirty = True

            if e_dirty:
                edoc["buildings"] = kept_exp
                edoc["stats"] = _recompute_export_district_stats(
                    kept_exp, landmark_hcads, good_brick_hcads
                )
                write_compact_json(epath, edoc)
                exports_updated += 1

    # 6. Recompute countywide stats_summary.json and v3 pmtiles_manifest.json
    decade_counter: Counter[str] = Counter()
    contrib_counter: Counter[str] = Counter()
    use_counter: Counter[str] = Counter()
    district_counter: Counter[str] = Counter()
    source_counter: Counter[str] = Counter()
    own_tier_counter: Counter[str] = Counter()
    total_blds = 0
    dated_blds = 0
    observed_fps = 0
    earliest_yr = 9999
    latest_yr = 0

    for q in QUAD_NAMES:
        for feat in patched_by_quad.get(q, ()):
            p = feat.get("properties") or {}
            yr = int(p.get("year_built") or 0)
            dec = int(compute_decade(yr))
            p["decade"] = dec
            total_blds += 1
            if p.get("is_footprint") or str(p.get("geometry_source") or "") in (
                "observed_footprint",
                "ms_footprint",
                "osm_footprint",
            ):
                observed_fps += 1
            if yr >= MIN_VALID_YEAR and dec >= 1830:
                dated_blds += 1
                decade_counter[str(dec)] += 1
                if yr < earliest_yr:
                    earliest_yr = yr
                if yr > latest_yr:
                    latest_yr = yr
            else:
                decade_counter["unknown"] += 1
            contrib_counter[str(p.get("contributing") or "Outside Historic District")] += 1
            use_counter[str(p.get("use_category") or "Residential")] += 1
            src_lbl = str(p.get("year_source") or "hcad_cama")
            source_counter[src_lbl] += 1
            own_tier_counter[str(p.get("own_tier") or "homestead")] += 1
            hd_s = str(p.get("historic_district") or "").strip()
            if hd_s and hd_s.lower() not in ("none", "null", "outside historic district"):
                district_counter[hd_s] += 1

    if decade_counter.get("unknown", 0) != 0 or total_blds != dated_blds:
        raise RollValidationError(
            f"Countywide dated-building completeness gate failed: total={total_blds:,}, "
            f"dated={dated_blds:,}, unknown={decade_counter.get('unknown', 0):,}."
        )

    top_bin_yr = max(eff_tax, latest_yr if latest_yr > 0 else eff_tax)
    ordered_decades = {str(d): int(decade_counter.get(str(d), 0)) for d in compute_decade_bins(top_bin_yr)}
    ordered_decades["unknown"] = 0

    core_pm_staged = ddir / "houston_atlas.pmtiles"
    if live_output_dir is not None:
        core_pm_live = Path(live_output_dir) / "houston_atlas.pmtiles"
        core_pm_bytes = (
            core_pm_staged.stat().st_size
            if core_pm_staged.exists()
            else (core_pm_live.stat().st_size if core_pm_live.exists() else 0)
        )
    else:
        core_pm_bytes = (
            core_pm_staged.stat().st_size
            if (core_changed and compile_core_pmtiles and core_pm_staged.exists())
            else 0
        )
    effective_pm_bytes = int(total_pmtiles_bytes) + int(core_pm_bytes)

    stats_path = ddir / "stats_summary.json"
    stats_doc: dict[str, Any] = (
        json.loads(stats_path.read_text(encoding="utf-8")) if stats_path.exists() else {}
    )
    stats_doc.update({
        "data_tax_year": eff_tax,
        "total_buildings": total_blds,
        "observed_footprints": observed_fps,
        "dated_buildings": dated_blds,
        "earliest_year": earliest_yr if earliest_yr != 9999 else MIN_VALID_YEAR,
        "latest_year": latest_yr if latest_yr != 0 else eff_tax,
        "decade_counts": ordered_decades,
        "contributing_counts": dict(contrib_counter),
        "use_category_counts": dict(use_counter),
        "ownership_tier_counts": {k: int(own_tier_counter.get(k, 0)) for k in OWNERSHIP_TIER_KEYS},
        "top_historic_districts": dict(district_counter.most_common(25)),
        "year_source_counts": dict(source_counter),
        "pmtiles_size_bytes": effective_pm_bytes,
        "pmtiles_shards": list(shard_filenames),
    })
    stats_path.write_text(json.dumps(stats_doc, indent=2) + "\n", encoding="utf-8")

    manifest_doc = update_pmtiles_manifest_v3(
        ddir / "pmtiles_manifest.json",
        total_buildings=total_blds,
        building_shards=shard_filenames,
        data_tax_year=eff_tax,
        cache_bust=cache_bust,
    )

    return {
        "core_changed": core_changed,
        "core_buildings_added": core_blds_added,
        "core_buildings_removed": core_blds_removed,
        "address_shards_updated": addr_shards_updated,
        "search_items_removed": search_items_removed,
        "permit_files_updated": permit_files_updated,
        "export_bundles_updated": exports_updated,
        "stats_summary": stats_doc,
        "pmtiles_manifest": manifest_doc,
    }



# ---------------------------------------------------------------------------
# Stage 5: Pre-Promotion Budget Gates & Atomic StagedRollPromotion Coordinator
# ---------------------------------------------------------------------------


def _compute_projected_app_size_bytes(
    live_output_dir: Path,
    staged_data_dir: Path,
    *,
    obsolete_live_rels: set[str] | None = None,
) -> tuple[int, list[tuple[str, Path, int]]]:
    """Compute projected total `app/` bundle size combining staged overlays with untouched live files."""
    live_data = Path(live_output_dir).resolve()
    staged_data = Path(staged_data_dir).resolve()

    staged_files: dict[str, tuple[Path, int]] = {}
    for p in staged_data.rglob("*"):
        if p.is_file() and not p.name.startswith("."):
            rel = p.relative_to(staged_data).as_posix()
            staged_files[rel] = (p, p.stat().st_size)

    entries: list[tuple[str, Path, int]] = []
    seen_data_rels: set[str] = set()

    if live_data.exists():
        for p in live_data.rglob("*"):
            if not p.is_file() or p.name.startswith("."):
                continue
            rel = p.relative_to(live_data).as_posix()
            if obsolete_live_rels and rel in obsolete_live_rels and rel not in staged_files:
                continue
            seen_data_rels.add(rel)
            if rel in staged_files:
                sp, sz = staged_files[rel]
                entries.append((f"public/data/{rel}", sp, sz))
            else:
                entries.append((f"public/data/{rel}", p, p.stat().st_size))

    for rel, (sp, sz) in sorted(staged_files.items()):
        if rel not in seen_data_rels:
            entries.append((f"public/data/{rel}", sp, sz))

    # Include non-data `app/` assets (JS, CSS, HTML, public/assets) when inside an `app/public/data` layout
    if live_data.parts[-3:] == ("app", "public", "data"):
        app_root = live_data.parents[1]
        for p in app_root.rglob("*"):
            if not p.is_file() or p.name.startswith("."):
                continue
            try:
                if p.resolve().is_relative_to(live_data):
                    continue
            except Exception:
                continue
            rel_app = p.relative_to(app_root).as_posix()
            entries.append((rel_app, p, p.stat().st_size))

    total_bytes = sum(sz for _r, _p, sz in entries)
    return total_bytes, entries


def prune_optional_enrichment_for_soft_budget(
    staged_data_dir: Path,
    live_output_dir: Path,
    standing_hcads: set[str],
    *,
    soft_max_bytes: int = SOFT_MAX_APP_BYTES,
    obsolete_live_rels: set[str] | None = None,
) -> dict[str, int]:
    """Trim optional vacant-lot deed/permit details when projected `app/` exceeds `SOFT_MAX_APP_BYTES` (DF-5)."""
    eff_obsolete = obsolete_live_rels if obsolete_live_rels is not None else set()
    total_before, _ = _compute_projected_app_size_bytes(
        live_output_dir, staged_data_dir, obsolete_live_rels=eff_obsolete
    )
    if total_before <= int(soft_max_bytes):
        return {"trimmed_accounts": 0, "bytes_saved": 0, "projected_bytes_after": total_before}

    permits_dir = Path(staged_data_dir) / "permits"
    trimmed_accounts = 0
    if permits_dir.exists():
        # Pass 1: drop non-standing vacant-lot enrichment rows from permits_*.json first
        for ppath in sorted(permits_dir.glob("permits_*.json")):
            total_now, _ = _compute_projected_app_size_bytes(
                live_output_dir, staged_data_dir, obsolete_live_rels=eff_obsolete
            )
            if total_now <= int(soft_max_bytes):
                break
            pdoc = json.loads(ppath.read_text(encoding="utf-8"))
            dirty = False
            for acct in list(pdoc.keys()):
                if acct not in standing_hcads:
                    del pdoc[acct]
                    trimmed_accounts += 1
                    dirty = True
            if dirty:
                write_compact_json(ppath, pdoc)

    # Pass 2: drop non-standing accounts from owner_history/owners_*.json.gz before capping standing buildings
    owner_hist_dir = Path(staged_data_dir) / "owner_history"
    if owner_hist_dir.exists():
        oh_dirty_any = False
        visited_counts: dict[str, int] = {}
        all_gz_paths = sorted(owner_hist_dir.glob("owners_*.json.gz"))
        for gz_path in all_gz_paths:
            total_now, _ = _compute_projected_app_size_bytes(
                live_output_dir, staged_data_dir, obsolete_live_rels=eff_obsolete
            )
            if total_now <= int(soft_max_bytes):
                break
            pfx_cur = gz_path.name.removeprefix("owners_").removesuffix(".json.gz")
            oh_doc = json.loads(gzip.decompress(gz_path.read_bytes()).decode("utf-8"))
            dirty_oh = False
            for acct in list(oh_doc.keys()):
                if acct not in standing_hcads:
                    del oh_doc[acct]
                    trimmed_accounts += 1
                    dirty_oh = True
            if dirty_oh:
                oh_dirty_any = True
                if oh_doc:
                    gz_path.write_bytes(compress_deterministic_gzip_json(oh_doc))
                else:
                    gz_path.unlink(missing_ok=True)
                    eff_obsolete.add(f"owner_history/{gz_path.name}")
            visited_counts[pfx_cur] = len(oh_doc)
        if oh_dirty_any:
            mpath = Path(staged_data_dir) / "pmtiles_manifest.json"
            if mpath.exists():
                rem_prefixes: list[str] = []
                rem_accounts = 0
                for gz_p in all_gz_paths:
                    pfx = gz_p.name.removeprefix("owners_").removesuffix(".json.gz")
                    if pfx in visited_counts:
                        cnt_p = visited_counts[pfx]
                    elif gz_p.exists():
                        cnt_p = len(json.loads(gzip.decompress(gz_p.read_bytes()).decode("utf-8")))
                    else:
                        cnt_p = 0
                    if cnt_p > 0:
                        rem_prefixes.append(pfx)
                        rem_accounts += cnt_p
                update_pmtiles_manifest_v3(
                    mpath,
                    owner_history_prefixes=rem_prefixes,
                    owner_history_accounts=rem_accounts,
                )

    # Pass 3: cap per-account permit/deed history lengths on remaining files if still over soft ceiling
    if permits_dir.exists():
        for ppath in sorted(permits_dir.glob("permits_*.json")):
            total_now, _ = _compute_projected_app_size_bytes(
                live_output_dir, staged_data_dir, obsolete_live_rels=eff_obsolete
            )
            if total_now <= int(soft_max_bytes):
                break
            pdoc = json.loads(ppath.read_text(encoding="utf-8"))
            dirty = False
            for acct, rec in pdoc.items():
                if not isinstance(rec, dict):
                    continue
                if isinstance(rec.get("d"), str) and rec["d"].count("|") > 1:
                    rec["d"] = "|".join(rec["d"].split("|")[:2])
                    trimmed_accounts += 1
                    dirty = True
                if isinstance(rec.get("p"), list) and len(rec["p"]) > 5:
                    rec["p"] = rec["p"][:5]
                    trimmed_accounts += 1
                    dirty = True
            if dirty:
                write_compact_json(ppath, pdoc)

    total_after, _ = _compute_projected_app_size_bytes(
        live_output_dir, staged_data_dir, obsolete_live_rels=eff_obsolete
    )
    return {
        "trimmed_accounts": trimmed_accounts,
        "bytes_saved": max(0, total_before - total_after),
        "projected_bytes_after": total_after,
    }


def validate_staged_app_bundle(
    staged_data_dir: Path,
    live_output_dir: Path,
    *,
    hard_max_app_bytes: int = HARD_MAX_APP_BYTES,
    max_single_file_bytes: int = MAX_SINGLE_FILE_BYTES,
    obsolete_live_rels: set[str] | None = None,
) -> dict[str, Any]:
    """Enforce size ceilings, compact JSON, v3 manifest, integer decades, `unknown == 0`, and zero dashes (DF-5)."""
    sdir = Path(staged_data_dir)
    total_bytes, entries = _compute_projected_app_size_bytes(
        live_output_dir, sdir, obsolete_live_rels=obsolete_live_rels
    )

    if total_bytes >= int(hard_max_app_bytes):
        raise RollValidationError(
            f"Projected app/ bundle size {total_bytes / (1024 * 1024):.2f} MiB meets or exceeds "
            f"hard ceiling {hard_max_app_bytes / (1024 * 1024):.1f} MiB."
        )
    for rel_name, _fpath, sz in entries:
        if sz >= int(max_single_file_bytes):
            raise RollValidationError(
                f"File {rel_name} ({sz / (1024 * 1024):.2f} MiB) meets or exceeds single-file "
                f"ceiling {max_single_file_bytes / (1024 * 1024):.1f} MiB."
            )

    component_caps = (
        ("curated_overrides.json", MAX_OVERRIDES_BYTES),
        ("neighborhood_scorecards.json", MAX_SCORECARDS_BYTES),
        ("historical_audit_queue.json", MAX_AUDIT_QUEUE_BYTES),
    )
    for fname, cap_bytes in component_caps:
        cand = sdir / fname
        if cand.exists() and cand.stat().st_size >= cap_bytes:
            raise RollValidationError(
                f"Staged {fname} ({cand.stat().st_size:,} bytes) exceeds cap ({cap_bytes:,} bytes)."
            )

    # Manifest v3 gate
    mpath = sdir / "pmtiles_manifest.json"
    if not mpath.exists():
        raise RollValidationError("Staged bundle is missing pmtiles_manifest.json.")
    mdoc = json.loads(mpath.read_text(encoding="utf-8"))
    if mdoc.get("version") != 3:
        raise RollValidationError(
            f"Staged pmtiles_manifest.json version must equal 3, got {mdoc.get('version')!r}."
        )
    if not isinstance(mdoc.get("data_tax_year"), int):
        raise RollValidationError("Staged pmtiles_manifest.json is missing integer data_tax_year.")

    # Stats completeness gate
    spath = sdir / "stats_summary.json"
    if not spath.exists():
        raise RollValidationError("Staged bundle is missing stats_summary.json.")
    sdoc = json.loads(spath.read_text(encoding="utf-8"))
    dec_counts = sdoc.get("decade_counts") or {}
    if int(dec_counts.get("unknown", -1)) != 0:
        raise RollValidationError(
            f"Staged stats_summary.json decade_counts['unknown'] must be 0, got {dec_counts.get('unknown')!r}."
        )
    tot_b = int(sdoc.get("total_buildings") or 0)
    dat_b = int(sdoc.get("dated_buildings") or 0)
    sum_dec = sum(int(v) for k, v in dec_counts.items() if k != "unknown")
    if tot_b != dat_b or tot_b != sum_dec:
        raise RollValidationError(
            f"Staged stats_summary mismatch: total={tot_b}, dated={dat_b}, sum(decade_counts)={sum_dec}."
        )

    # Single-line compact formatting & dash-free check on staged JSON artifacts
    pretty_json_exempt = {"stats_summary.json", "pmtiles_manifest.json"}
    for jpath in sorted(sdir.rglob("*")):
        if not jpath.is_file() or jpath.suffix.lower() not in (".json", ".geojson"):
            continue
        raw_txt = jpath.read_text(encoding="utf-8")
        if "\u2014" in raw_txt or "\u2013" in raw_txt:
            raise RollValidationError(
                f"Staged {jpath.relative_to(sdir)} contains forbidden U+2014/U+2013 dash character."
            )
        if jpath.name not in pretty_json_exempt:
            non_empty_lines = [ln for ln in raw_txt.splitlines() if ln.strip()]
            if len(non_empty_lines) > 1:
                # Re-compact automatically if copied baseline file was multi-line, unless malformed
                parsed_j = json.loads(raw_txt)
                write_compact_json(jpath, parsed_j)

    # Single-line compact formatting & dash-free check on staged gzipped JSON artifacts (*.json.gz)
    for gzpath in sorted(sdir.rglob("*.json.gz")):
        if not gzpath.is_file() or gzpath.name.startswith("."):
            continue
        try:
            raw_gz_txt = gzip.decompress(gzpath.read_bytes()).decode("utf-8")
        except Exception as exc:
            raise RollValidationError(
                f"Staged {gzpath.relative_to(sdir)} failed gzip/UTF-8 decompression: {exc}"
            ) from exc
        if "\u2014" in raw_gz_txt or "\u2013" in raw_gz_txt:
            raise RollValidationError(
                f"Staged {gzpath.relative_to(sdir)} contains forbidden U+2014/U+2013 dash character."
            )
        gz_lines = [ln for ln in raw_gz_txt.splitlines() if ln.strip()]
        if len(gz_lines) > 1:
            raise RollValidationError(
                f"Staged {gzpath.relative_to(sdir)} must contain single-line compact JSON."
            )
        try:
            parsed_gz = json.loads(raw_gz_txt)
        except Exception as exc:
            raise RollValidationError(
                f"Staged {gzpath.relative_to(sdir)} contains invalid JSON: {exc}"
            ) from exc
        if not isinstance(parsed_gz, dict):
            raise RollValidationError(
                f"Staged {gzpath.relative_to(sdir)} must decode to a JSON object."
            )

    # Integer decade check on buildings.geojson if present
    core_p = sdir / "buildings.geojson"
    if core_p.exists():
        cdoc = json.loads(core_p.read_text(encoding="utf-8"))
        for cf in cdoc.get("features") or ():
            dec_v = (cf.get("properties") or {}).get("decade")
            if dec_v is not None and (not isinstance(dec_v, int) or isinstance(dec_v, bool)):
                raise RollValidationError(
                    f"Non-integer decade {dec_v!r} detected in staged buildings.geojson."
                )

    return {
        "projected_app_bytes": int(total_bytes),
        "projected_app_mib": round(total_bytes / (1024 * 1024), 2),
        "manifest_version": int(mdoc["version"]),
        "total_buildings": tot_b,
    }


def _resolve_repo_root_if_full_checkout(cache_dir: Path, output_dir: Path) -> Path | None:
    """Return `repo_root` only when both `cache_dir` and `output_dir` live inside a full Atlas checkout."""
    out_r = Path(output_dir).resolve()
    c_r = Path(cache_dir).resolve()
    if len(out_r.parts) < 4 or out_r.parts[-3:] != ("app", "public", "data"):
        return None
    cand = out_r.parents[2]
    if (
        (cand / "app" / "index.html").exists()
        and (cand / "pipeline" / "atlas_pipeline" / "schema.py").exists()
        and c_r.is_relative_to(cand)
    ):
        return cand
    return None


class StagedRollPromotion:
    """Shadow-stages all shard/index updates under `rolls/<YYYY>/staged/` and promotes atomically (DF-5)."""

    def __init__(self, ctx: RollContext, *, repo_root: Path | None = None) -> None:
        self.ctx = ctx
        self.staged_root = ctx.staged_dir
        self.staged_aligned = self.staged_root / "aligned_ndjson"
        self.staged_data = self.staged_root / "data"
        self.staged_cache_view = self.staged_root / "cache_view"
        self.repo_root = repo_root

    def reset_staging_dirs(self) -> None:
        if self.staged_root.exists():
            shutil.rmtree(self.staged_root)
        self.staged_aligned.mkdir(parents=True, exist_ok=True)
        self.staged_data.mkdir(parents=True, exist_ok=True)
        (self.staged_cache_view / "aligned_ndjson").mkdir(parents=True, exist_ok=True)

    def seed_mutable_data_tree(self) -> None:
        """Copy baseline JSON/GeoJSON trees into `staged/data/` (skipping `.pmtiles` binaries)."""
        src_data = self.ctx.output_dir
        for p in sorted(src_data.rglob("*")):
            if not p.is_file() or p.name.startswith(".") or p.suffix.lower() == ".pmtiles":
                continue
            rel = p.relative_to(src_data)
            dst = self.staged_data / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dst)

    def write_and_deduplicate_dirty_shards(
        self,
        patched_by_quad: dict[str, list[dict[str, Any]]],
        dirty_quadrants: set[str],
        *,
        art_delta: ArtifactSyncDelta | None = None,
    ) -> dict[str, int]:
        dedup_dropped: dict[str, int] = {}
        dropped_hcads_candidates: set[str] = set()
        for q in sorted(dirty_quadrants):
            seq_path = self.staged_aligned / f"buildings_{q}.geojsonseq"
            raw_feats = patched_by_quad.get(q, [])
            id_to_hcad: dict[str, str] = {}
            pre_ids: set[str] = set()
            for f in raw_feats:
                fp = f.get("properties") or {}
                fid = str(fp.get("building_id") or fp.get("id") or "")
                if fid:
                    pre_ids.add(fid)
                    fh = normalize_hcad13(fp.get("hcad_num"))
                    if fh:
                        id_to_hcad[fid] = fh
            with open(seq_path, "wb") as out_f:
                for feat in raw_feats:
                    if isinstance(feat.get("geometry"), dict):
                        feat["geometry"] = normalize_polygon_rings_6dp_ccw(feat["geometry"])
                    p = feat.get("properties") or {}
                    yr = int(p.get("year_built") or 0)
                    if yr < MIN_VALID_YEAR:
                        raise RollValidationError(
                            f"Refusing to stage undated feature {p.get('id')!r} in shard {q}."
                        )
                    p["decade"] = int(compute_decade(yr))
                    out_f.write(b"\x1e" + orjson.dumps(feat) + b"\n")
            _shard_name, initial_cnt, kept_cnt = deduplicate_geojsonseq_shard(seq_path)
            dropped_cnt = max(0, int(initial_cnt) - int(kept_cnt))
            dedup_dropped[q] = dropped_cnt
            # Reload exact post-dedup features so downstream stats match the shard 1:1
            reloaded: list[dict[str, Any]] = []
            with open(seq_path, "rb") as in_f:
                for raw_line in in_f:
                    ln = raw_line[1:] if raw_line.startswith(b"\x1e") else raw_line
                    ln = ln.strip()
                    if ln:
                        reloaded.append(orjson.loads(ln))
            if len(reloaded) != kept_cnt:
                raise RollValidationError(
                    f"Deduplicated shard count mismatch on {q}: expected {kept_cnt}, read {len(reloaded)}."
                )
            patched_by_quad[q] = reloaded
            if dropped_cnt > 0 and art_delta is not None:
                kept_ids = {
                    str((f.get("properties") or {}).get("building_id") or (f.get("properties") or {}).get("id") or "")
                    for f in reloaded
                } - {""}
                dropped_ids = pre_ids - kept_ids
                if dropped_ids:
                    art_delta.deleted_building_ids.update(dropped_ids)
                    art_delta.inner_core_touched = True
                    for did in dropped_ids:
                        dh = id_to_hcad.get(did)
                        if dh:
                            dropped_hcads_candidates.add(dh)
                    for h_k in list(art_delta.upserted_features_by_hcad.keys()):
                        filt = [
                            uf
                            for uf in art_delta.upserted_features_by_hcad[h_k]
                            if str(
                                (uf.get("properties") or {}).get("building_id")
                                or (uf.get("properties") or {}).get("id")
                                or ""
                            )
                            not in dropped_ids
                        ]
                        if filt:
                            art_delta.upserted_features_by_hcad[h_k] = filt
                        else:
                            del art_delta.upserted_features_by_hcad[h_k]

        if dropped_hcads_candidates and art_delta is not None:
            surviving_hcads = {
                normalize_hcad13((f.get("properties") or {}).get("hcad_num"))
                for flist in patched_by_quad.values()
                for f in flist
            } - {""}
            exhausted_hcads = dropped_hcads_candidates - surviving_hcads
            if exhausted_hcads:
                for eh in exhausted_hcads:
                    art_delta.deleted_hcads.add(eh)
                    art_delta.new_construction_hcads.discard(eh)
                    if len(eh) >= 3:
                        art_delta.touched_permit_prefixes.add(eh[:3])
                for old_fid, new_h in list(art_delta.old_fid_to_new_hcad.items()):
                    if new_h in exhausted_hcads:
                        del art_delta.old_fid_to_new_hcad[old_fid]
                for old_h in list(art_delta.old_to_all_new_hcads.keys()):
                    rem = [nh for nh in art_delta.old_to_all_new_hcads[old_h] if nh not in exhausted_hcads]
                    if rem:
                        art_delta.old_to_all_new_hcads[old_h] = rem
                        if art_delta.old_to_new_hcads.get(old_h) in exhausted_hcads:
                            art_delta.old_to_new_hcads[old_h] = rem[0]
                    else:
                        del art_delta.old_to_all_new_hcads[old_h]
                        art_delta.old_to_new_hcads.pop(old_h, None)
                        art_delta.deleted_hcads.add(old_h)
                        if len(old_h) >= 3:
                            art_delta.touched_permit_prefixes.add(old_h[:3])
                for old_h, new_h in list(art_delta.old_to_new_hcads.items()):
                    if new_h in exhausted_hcads:
                        del art_delta.old_to_new_hcads[old_h]
                        art_delta.deleted_hcads.add(old_h)
                        if len(old_h) >= 3:
                            art_delta.touched_permit_prefixes.add(old_h[:3])
        return dedup_dropped

    def populate_cache_view(self, dirty_quadrants: set[str]) -> None:
        """Build an isolated `cache_view` directory pointing to staged roll zips & shards."""
        for arc in REQUIRED_ROLL_ARCHIVES + OPTIONAL_ROLL_ARCHIVES:
            src_arc = self.ctx.roll_dir / arc
            if src_arc.exists():
                _link_or_copy_readonly(src_arc, self.staged_cache_view / arc)
        extracted_parcels = self.ctx.roll_dir / "_parcels_extracted"
        staged_extracted = self.staged_cache_view / "_parcels_extracted"
        if extracted_parcels.is_dir() and not staged_extracted.exists():
            try:
                os.symlink(extracted_parcels.resolve(), staged_extracted)
            except OSError:
                pass
        active_cache_acct_zip = self.ctx.cache_dir / "Real_acct_owner.zip"
        if active_cache_acct_zip.exists():
            prior_zip_yr = peek_real_acct_zip_year(active_cache_acct_zip)
            if prior_zip_yr and prior_zip_yr != self.ctx.tax_year:
                staged_prior_roll_zip = (
                    self.staged_cache_view / "rolls" / str(prior_zip_yr) / "Real_acct_owner.zip"
                )
                if not staged_prior_roll_zip.exists():
                    _link_or_copy_readonly(active_cache_acct_zip, staged_prior_roll_zip)
        rolls_root = self.ctx.cache_dir / "rolls"
        if rolls_root.is_dir():
            for yr_dir in sorted(rolls_root.iterdir()):
                if not yr_dir.is_dir() or not yr_dir.name.isdigit():
                    continue
                hist_acct_zip = yr_dir / "Real_acct_owner.zip"
                if hist_acct_zip.exists():
                    _link_or_copy_readonly(
                        hist_acct_zip,
                        self.staged_cache_view / "rolls" / yr_dir.name / "Real_acct_owner.zip",
                    )
        hist_rolls = self.ctx.cache_dir / "hcad_historical_rolls"
        staged_hist_rolls = self.staged_cache_view / "hcad_historical_rolls"
        if hist_rolls.is_dir() and not staged_hist_rolls.exists():
            try:
                os.symlink(hist_rolls.resolve(), staged_hist_rolls)
            except OSError:
                pass
        cama_pkl = self.ctx.roll_dir / f"cama_{self.ctx.tax_year}_compact.pkl"
        if cama_pkl.exists():
            _link_or_copy_readonly(cama_pkl, self.staged_cache_view / cama_pkl.name)
        for aux_name in (
            "coh_council_districts_2024.geojson",
            "coh_landmarks.json",
            "coh_landmarks_enriched.json",
            "thc_markers_harris.json",
            f"coh_{self.ctx.tax_year}_structural_demo_permits.json",
        ):
            src_aux = self.ctx.cache_dir / aux_name
            if src_aux.exists():
                _link_or_copy_readonly(src_aux, self.staged_cache_view / aux_name)

        view_aligned = self.staged_cache_view / "aligned_ndjson"
        for q in QUAD_NAMES:
            if q in dirty_quadrants:
                src_seq = self.staged_aligned / f"buildings_{q}.geojsonseq"
            else:
                src_seq = self.ctx.cache_dir / "aligned_ndjson" / f"buildings_{q}.geojsonseq"
            if src_seq.exists():
                _link_or_copy_readonly(src_seq, view_aligned / f"buildings_{q}.geojsonseq")

    def compile_dirty_pmtiles(self, dirty_quadrants: set[str]) -> tuple[list[str], int, list[str]]:
        rebuilt: list[str] = []
        if dirty_quadrants:
            dirty_sorted = tuple(q for q in QUAD_NAMES if q in dirty_quadrants)
            # Only compile shards that have >= 1 feature (Tippecanoe requires non-empty input when empty quadrant in mini fixtures)
            non_empty_dirty = tuple(
                q
                for q in dirty_sorted
                if (self.staged_aligned / f"buildings_{q}.geojsonseq").exists()
                and (self.staged_aligned / f"buildings_{q}.geojsonseq").stat().st_size > 0
            )
            if non_empty_dirty:
                built_names, _built_sz = compile_quadrant_pmtiles(
                    self.staged_aligned,
                    self.staged_data,
                    quad_names=non_empty_dirty,
                )
                rebuilt.extend(built_names)

        final_shards: list[str] = []
        total_pm_bytes = 0
        for q in QUAD_NAMES:
            fname = f"houston_buildings_{q}.pmtiles"
            sp = self.staged_data / fname
            lp = self.ctx.output_dir / fname
            if sp.exists():
                final_shards.append(fname)
                total_pm_bytes += sp.stat().st_size
            elif lp.exists() and q not in dirty_quadrants:
                final_shards.append(fname)
                total_pm_bytes += lp.stat().st_size
        return final_shards, total_pm_bytes, rebuilt

    @staticmethod
    def _stage_file_swap(
        src: Path,
        dst: Path,
        rb_dir: Path,
        swaps: list[tuple[Path, Path, Path | None, bool]],
        *,
        link_source: bool = False,
        count_as_promoted: bool = True,
    ) -> None:
        """Prepare a same-filesystem `.promote.tmp` file + rollback backup BEFORE any live mutation."""
        dst.parent.mkdir(parents=True, exist_ok=True)
        if (
            dst.exists()
            and dst.stat().st_size == src.stat().st_size
            and sha256_file(dst) == sha256_file(src)
        ):
            return
        tmp_p = dst.with_name(f".{dst.name}.promote.tmp")
        tmp_p.unlink(missing_ok=True)
        rb_p: Path | None = None
        if dst.exists():
            rb_p = rb_dir / f"rb_{len(swaps):04d}_{dst.name}"
            _link_or_copy_readonly(dst, rb_p)
        if link_source:
            _link_or_copy_readonly(src, tmp_p)
        else:
            shutil.copy2(src, tmp_p)
        swaps.append((tmp_p, dst, rb_p, count_as_promoted))

    def commit(self, *, dirty_quadrants: set[str]) -> dict[str, Any]:
        """Atomically promote staged shards, `.pmtiles`, JSON indexes, CAMA zips, and AnchorRule edits."""
        from atlas_pipeline.schema import sync_repo_tax_year_and_cache_bust

        if self.repo_root is not None:
            sync_repo_tax_year_and_cache_bust(
                self.repo_root,
                tax_year=self.ctx.tax_year,
                cache_bust=self.ctx.cache_bust,
                dry_run=True,
            )

        backup_dir = self.ctx.roll_dir / "prior_cache_backup"
        backup_dir.mkdir(parents=True, exist_ok=True)
        rb_dir = self.staged_root / "_rollback_journal"
        if rb_dir.exists():
            shutil.rmtree(rb_dir, ignore_errors=True)
        rb_dir.mkdir(parents=True, exist_ok=True)

        swaps: list[tuple[Path, Path, Path | None, bool]] = []
        obsolete_deletes: list[tuple[Path, Path]] = []
        promoted_files = 0

        try:
            # Phase A (Pre-Commit Preparation): stage ALL backups and .promote.tmp files before mutating live paths
            live_aligned = self.ctx.cache_dir / "aligned_ndjson"
            live_aligned.mkdir(parents=True, exist_ok=True)
            for q in sorted(dirty_quadrants):
                src_seq = self.staged_aligned / f"buildings_{q}.geojsonseq"
                if not src_seq.exists():
                    continue
                self._stage_file_swap(src_seq, live_aligned / src_seq.name, rb_dir, swaps)
                fname_pm = f"houston_buildings_{q}.pmtiles"
                if not (self.staged_data / fname_pm).exists():
                    live_pm = self.ctx.output_dir / fname_pm
                    if live_pm.exists():
                        rb_del = rb_dir / f"rb_del_{q}_{fname_pm}"
                        _link_or_copy_readonly(live_pm, rb_del)
                        obsolete_deletes.append((live_pm, rb_del))

            staged_oh_dir = self.staged_data / "owner_history"
            live_oh_dir = self.ctx.output_dir / "owner_history"
            if staged_oh_dir.is_dir() and live_oh_dir.is_dir():
                for live_gz in sorted(live_oh_dir.glob("owners_*.json.gz")):
                    if not (staged_oh_dir / live_gz.name).exists():
                        rb_oh = rb_dir / f"rb_del_oh_{live_gz.name}"
                        _link_or_copy_readonly(live_gz, rb_oh)
                        obsolete_deletes.append((live_gz, rb_oh))

            staged_all = [
                p for p in sorted(self.staged_data.rglob("*")) if p.is_file() and not p.name.startswith(".")
            ]
            ordered_staged = sorted(
                staged_all,
                key=lambda p: (
                    2
                    if p.name in ("pmtiles_manifest.json", "stats_summary.json")
                    else (0 if p.suffix == ".pmtiles" else 1),
                    p.as_posix(),
                ),
            )
            for sp in ordered_staged:
                rel = sp.relative_to(self.staged_data)
                dst = self.ctx.output_dir / rel
                self._stage_file_swap(sp, dst, rb_dir, swaps)

            for arc in REQUIRED_ROLL_ARCHIVES + OPTIONAL_ROLL_ARCHIVES:
                src_arc = self.ctx.roll_dir / arc
                if not src_arc.exists():
                    continue
                dst_arc = self.ctx.cache_dir / arc
                if arc == "Real_acct_owner.zip" and dst_arc.exists():
                    prior_zip_yr = peek_real_acct_zip_year(dst_arc)
                    if prior_zip_yr and prior_zip_yr != self.ctx.tax_year:
                        hist_dst = (
                            self.ctx.cache_dir / "rolls" / str(prior_zip_yr) / "Real_acct_owner.zip"
                        )
                        if not hist_dst.exists():
                            _link_or_copy_readonly(dst_arc, hist_dst)
                if dst_arc.exists():
                    bkp_arc = backup_dir / arc
                    if not bkp_arc.exists():
                        _link_or_copy_readonly(dst_arc, bkp_arc)
                self._stage_file_swap(
                    src_arc,
                    dst_arc,
                    rb_dir,
                    swaps,
                    link_source=True,
                    count_as_promoted=False,
                )

            src_pkl = self.ctx.roll_dir / f"cama_{self.ctx.tax_year}_compact.pkl"
            if src_pkl.exists():
                dst_pkl = self.ctx.cache_dir / src_pkl.name
                self._stage_file_swap(
                    src_pkl,
                    dst_pkl,
                    rb_dir,
                    swaps,
                    count_as_promoted=False,
                )
        except Exception:
            for tmp_p, _dst_p, _rb_p, _cnt in swaps:
                tmp_p.unlink(missing_ok=True)
            shutil.rmtree(rb_dir, ignore_errors=True)
            raise

        # Phase B (Atomic Swap + Rollback Guard): execute ordered os.replace calls + AnchorRule commit
        committed: list[tuple[Path, Path | None]] = []
        repo_sync_res: dict[str, Any] = {"skipped": True}
        try:
            for tmp_p, dst_p, rb_p, cnt_flag in swaps:
                os.replace(tmp_p, dst_p)
                committed.append((dst_p, rb_p))
                if cnt_flag:
                    promoted_files += 1
            for del_p, rb_del_p in obsolete_deletes:
                del_p.unlink(missing_ok=True)
                committed.append((del_p, rb_del_p))
            if self.repo_root is not None:
                repo_sync_res = sync_repo_tax_year_and_cache_bust(
                    self.repo_root,
                    tax_year=self.ctx.tax_year,
                    cache_bust=self.ctx.cache_bust,
                    dry_run=False,
                )
        except Exception:
            for dst_p, rb_p in reversed(committed):
                try:
                    if rb_p is not None and rb_p.exists():
                        try:
                            os.replace(rb_p, dst_p)
                        except OSError:
                            shutil.copy2(rb_p, dst_p)
                    else:
                        dst_p.unlink(missing_ok=True)
                except OSError:
                    pass
            for tmp_p, _dst_p, _rb_p, _cnt in swaps:
                tmp_p.unlink(missing_ok=True)
            shutil.rmtree(rb_dir, ignore_errors=True)
            raise

        shutil.rmtree(rb_dir, ignore_errors=True)
        return {
            "promoted_files": promoted_files,
            "repo_sync": repo_sync_res,
        }


def _count_baseline_parcels(cache_dir: Path, staged_parcels_zip: Path) -> int:
    """Read baseline `Parcels.zip` / `Parcels.gdb` polygon count directly without confusing CAMA accounts."""
    direct_gdb = cache_dir / "Parcels" / "Parcels.gdb"
    if direct_gdb.exists():
        try:
            info_g = pyogrio.read_info(str(direct_gdb))
            cnt_g = int(info_g.get("features") or 0)
            if cnt_g > 0:
                return cnt_g
        except Exception:
            pass

    prior_zip = cache_dir / "Parcels.zip"
    if not prior_zip.exists() or prior_zip.resolve() == staged_parcels_zip.resolve():
        return 0
    try:
        with zipfile.ZipFile(prior_zip, "r") as pzf:
            pnames = pzf.namelist()
            gdbs = sorted({n.split(".gdb/")[0] + ".gdb" for n in pnames if ".gdb/" in n})
            gpkgs = sorted(n for n in pnames if n.lower().endswith(".gpkg") and not n.endswith("/"))
            shps = sorted(n for n in pnames if n.lower().endswith(".shp") and not n.endswith("/"))
            gjs = sorted(
                n for n in pnames if n.lower().endswith((".geojson", ".json")) and not n.endswith("/")
            )
            vtarget = (gdbs or gpkgs or shps or gjs or [""])[0]
        if vtarget:
            cached_ext = cache_dir / "_parcels_extracted" / vtarget
            if cached_ext.exists():
                cnt_c = int(pyogrio.read_info(str(cached_ext)).get("features") or 0)
                if cnt_c > 0:
                    return cnt_c
            cnt_v = int(pyogrio.read_info(f"/vsizip/{prior_zip}/{vtarget}").get("features") or 0)
            if cnt_v > 0:
                return cnt_v
    except Exception:
        pass
    try:
        ds_prior, lyr_prior = resolve_parcels_vector_source(
            prior_zip, scratch_extract_dir=cache_dir / "_parcels_extracted"
        )
        return int(pyogrio.read_info(ds_prior, layer=lyr_prior).get("features") or 0)
    except Exception:
        return 0


def run_annual_hcad_refresh(
    *,
    cache_dir: Path,
    output_dir: Path,
    tax_year: int | None = None,
    discover_latest_year: bool = False,
    roll_dir: Path | None = None,
    apply: bool = False,
    shards_only: bool = False,
    cache_bust: str | None = None,
    observed_footprints_ndjson: Path | None = None,
    allow_partial_roll: bool = False,
    allow_pmtiles_fallback: bool = False,
    auto_revive_rebuilt_suppressed: bool = False,
    sanity_cfg: RollSanityConfig | None = None,
    opener: Callable[..., Any] | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Execute the end-to-end Milestone 4 Annual HCAD Appraisal Roll & Parcel Refresher."""
    cdir = Path(cache_dir)
    ddir = Path(output_dir)
    cfg = sanity_cfg or RollSanityConfig()

    if discover_latest_year and tax_year is None:
        resolved_yr = discover_latest_hcad_roll_year(opener=opener)
    elif tax_year is not None:
        resolved_yr = int(tax_year)
    else:
        raise RollValidationError(
            "Pass `--tax-year <YYYY>` or `--discover-latest-year` for `--mode annual-refresh`."
        )

    eff_cb = str(cache_bust or "").strip()
    if not eff_cb:
        m_path = ddir / "pmtiles_manifest.json"
        if m_path.exists():
            try:
                eff_cb = str(json.loads(m_path.read_text(encoding="utf-8")).get("cache_bust") or "")
            except Exception:
                eff_cb = ""
        if not eff_cb:
            eff_cb = ACTIVE_CACHE_BUST

    prior_yr = ACTIVE_TAX_YEAR
    stats_live_path = ddir / "stats_summary.json"
    if stats_live_path.exists():
        try:
            s_live = json.loads(stats_live_path.read_text(encoding="utf-8"))
            prior_yr = int(s_live.get("data_tax_year") or s_live.get("latest_year") or (resolved_yr - 1))
        except Exception:
            prior_yr = resolved_yr - 1

    ctx = RollContext(
        tax_year=resolved_yr,
        cache_bust=eff_cb,
        cache_dir=cdir,
        output_dir=ddir,
    )

    # Stage 1: Stage archives into pipeline/cache/rolls/<YYYY>/ and validate pre-flight contract
    _staged_roll_dir, manifest_roll = stage_hcad_roll_archives(
        ctx, roll_dir=roll_dir, opener=opener
    )
    baseline_by_quad, used_pmtiles_fb = load_baseline_quadrant_features(
        cdir,
        ddir,
        allow_pmtiles_fallback=allow_pmtiles_fallback,
        apply_mode=apply,
    )

    baseline_blds_total = sum(len(v) for v in baseline_by_quad.values())
    baseline_hcads = {
        normalize_hcad13((f.get("properties") or {}).get("hcad_num"))
        for fl in baseline_by_quad.values()
        for f in fl
    } - {""}

    prior_cama = load_compact_cama_lookup(cdir, tax_year=prior_yr) or {}
    base_acct_cnt = len(prior_cama) if prior_cama else len(baseline_hcads)
    base_impr_cnt = (
        sum(1 for v in prior_cama.values() if int(v[0] or 0) >= MIN_VALID_YEAR or int(v[2] or 0) > 0)
        if prior_cama
        else len(baseline_hcads)
    )
    base_pcl_cnt = _count_baseline_parcels(cdir, ctx.roll_dir / "Parcels.zip")
    if base_pcl_cnt <= 0:
        base_pcl_cnt = len(baseline_hcads)

    scratch_pcl_dir = ctx.roll_dir / "_parcels_extracted"
    preflight = validate_staged_hcad_roll(
        ctx.roll_dir,
        ctx=ctx,
        sanity=cfg,
        baseline_acct_count=base_acct_cnt,
        baseline_improved_count=base_impr_cnt,
        baseline_parcel_count=base_pcl_cnt,
        allow_partial_roll=allow_partial_roll,
        scratch_extract_dir=scratch_pcl_dir,
    )

    # Stage 2: Stream normalized HCAD snapshots + parcel STRtree + tiebacks
    bldg_zip = ctx.roll_dir / "Real_building_land.zip"
    acct_zip = ctx.roll_dir / "Real_acct_owner.zip"
    parcels_zip = ctx.roll_dir / "Parcels.zip"

    new_accounts = stream_hcad_account_snapshots(
        bldg_zip,
        acct_zip,
        tax_year=ctx.tax_year,
        prior_tax_year=prior_yr,
        cache_dir_for_cama_pkl=ctx.roll_dir,
    )
    parcel_hcads, parcel_geoms_2278, parcel_hcad_to_idx, parcel_strtree = load_parcel_geometries_2278(
        parcels_zip, scratch_extract_dir=scratch_pcl_dir
    )
    tieback_map = load_parcel_tieback_map(acct_zip)

    ov_path = ddir / "curated_overrides.json"
    curated_doc = json.loads(ov_path.read_text(encoding="utf-8")) if ov_path.exists() else {}
    cd_path = ddir / "citydir_index.json"
    cd_by_hcad = (
        (json.loads(cd_path.read_text(encoding="utf-8")).get("by_hcad") or {})
        if cd_path.exists()
        else {}
    )
    core_bpath = ddir / "buildings.geojson"
    core_hcads: set[str] = set()
    if core_bpath.exists():
        for cf in json.loads(core_bpath.read_text(encoding="utf-8")).get("features") or ():
            ch = normalize_hcad13((cf.get("properties") or {}).get("hcad_num"))
            if ch:
                core_hcads.add(ch)

    # Stage 3: 5-category delta + circuit breaker + orphan & Cohort 5 audit reports
    delta_res = classify_feature_delta(
        baseline_by_quad,
        new_accounts,
        parcel_hcads=parcel_hcads,
        parcel_geoms_2278=parcel_geoms_2278,
        parcel_hcad_to_idx=parcel_hcad_to_idx,
        parcel_strtree=parcel_strtree,
        tieback_map=tieback_map,
        curated_overrides=curated_doc,
        tax_year=ctx.tax_year,
        prior_tax_year=prior_yr,
        citydir_by_hcad=cd_by_hcad,
        sanity_cfg=cfg,
        enforce_circuit_breaker=not allow_partial_roll,
        auto_revive_rebuilt_suppressed=auto_revive_rebuilt_suppressed,
        historic_core_hcads=core_hcads,
    )
    orphan_report = audit_curated_hcad_references(
        ddir,
        new_accounts,
        parcel_hcads=parcel_hcads,
        parcel_hcad_to_idx=parcel_hcad_to_idx,
        parcel_strtree=parcel_strtree,
        tieback_map=tieback_map,
        extra_shard_orphans=delta_res["orphan_records"],
    )

    orphan_report_path = ctx.roll_dir / f"orphaned_curated_entities_{ctx.tax_year}.json"
    c5_report_path = ctx.roll_dir / f"lost_houston_candidates_{ctx.tax_year}.json"
    delta_report_path = ctx.roll_dir / f"annual_roll_delta_report_{ctx.tax_year}.json"

    orphan_payload = {
        "tax_year": ctx.tax_year,
        "prior_tax_year": prior_yr,
        **orphan_report,
    }
    c5_payload = {
        "tax_year": ctx.tax_year,
        "prior_tax_year": prior_yr,
        "read_only_review_queue": True,
        "candidate_count": len(delta_res["lost_houston_candidates"]),
        "candidates": delta_res["lost_houston_candidates"],
    }
    delta_payload: dict[str, Any] = {
        "mode": "apply" if apply else "dry_run",
        "tax_year": ctx.tax_year,
        "prior_tax_year": prior_yr,
        "cache_bust": ctx.cache_bust,
        "used_pmtiles_dry_run_fallback": used_pmtiles_fb,
        "baseline_buildings": baseline_blds_total,
        "baseline_parcels_with_buildings": len(baseline_hcads),
        "preflight": preflight,
        "roll_manifest_archives": sorted((manifest_roll.get("archives") or {}).keys()),
        "parcel_counts": delta_res["parcel_counts"],
        "feature_counts": delta_res["feature_counts"],
        "governance_counts": delta_res["governance_counts"],
        "demolition_fraction": delta_res["demolition_fraction"],
        "dropped_townhome_splits": delta_res["dropped_townhome_splits"],
        "orphaned_curated_count": orphan_report["total_orphaned_or_flagged"],
        "lost_houston_candidate_count": len(delta_res["lost_houston_candidates"]),
        "rebuilt_on_suppressed_lots": delta_res["rebuilt_on_suppressed_lots"],
        "protected_cama_disagreement_count": len(delta_res["protected_cama_disagreements"]),
        "reports": {
            "delta_report": str(delta_report_path),
            "orphaned_curated_entities": str(orphan_report_path),
            "lost_houston_candidates": str(c5_report_path),
        },
    }

    orphan_report_path.write_text(json.dumps(orphan_payload, indent=2) + "\n", encoding="utf-8")
    c5_report_path.write_text(json.dumps(c5_payload, indent=2) + "\n", encoding="utf-8")
    delta_report_path.write_text(json.dumps(delta_payload, indent=2) + "\n", encoding="utf-8")

    if not apply:
        return delta_payload

    # Stages 4 & 5: Shadow-stage shards & indexes, run budget/schema gates, promote atomically
    eff_repo_root = (
        Path(repo_root)
        if repo_root is not None
        else _resolve_repo_root_if_full_checkout(cdir, ddir)
    )
    promoter = StagedRollPromotion(ctx, repo_root=eff_repo_root)
    promoter.reset_staging_dirs()
    promoter.seed_mutable_data_tree()

    ovl_staged_path = promoter.staged_data / "overlays.json"
    ovl_staged = (
        json.loads(ovl_staged_path.read_text(encoding="utf-8")) if ovl_staged_path.exists() else {}
    )
    patched_by_quad = delta_res["patched_by_quad"]
    art_delta: ArtifactSyncDelta = delta_res["artifact_delta"]

    synth_stats = synthesize_new_construction_features(
        patched_by_quad,
        art_delta,
        new_accounts,
        parcel_hcads=parcel_hcads,
        parcel_geoms_2278=parcel_geoms_2278,
        parcel_hcad_to_idx=parcel_hcad_to_idx,
        parcel_strtree=parcel_strtree,
        bldg_zip_path=bldg_zip,
        acct_zip_path=acct_zip,
        cache_dir=cdir,
        tax_year=ctx.tax_year,
        used_building_ids=delta_res["used_building_ids"],
        observed_footprints_ndjson=observed_footprints_ndjson,
        overlays_doc=ovl_staged,
    )

    dedup_dropped = promoter.write_and_deduplicate_dirty_shards(
        patched_by_quad, art_delta.dirty_quadrants, art_delta=art_delta
    )
    shard_filenames, shard_pm_bytes, rebuilt_shards = promoter.compile_dirty_pmtiles(
        art_delta.dirty_quadrants
    )

    sync_stats = reconcile_derived_building_indexes(
        promoter.staged_data,
        patched_by_quad,
        art_delta,
        new_accounts,
        parcel_geoms_2278=parcel_geoms_2278,
        parcel_hcad_to_idx=parcel_hcad_to_idx,
        bldg_zip_path=bldg_zip,
        acct_zip_path=acct_zip,
        tax_year=ctx.tax_year,
        cache_bust=ctx.cache_bust,
        shard_filenames=shard_filenames,
        total_pmtiles_bytes=shard_pm_bytes,
        live_output_dir=ddir,
    )

    scorecard_stats: dict[str, Any] = {"skipped": bool(shards_only)}
    audit_refresh_stats: dict[str, Any] = {"skipped": bool(shards_only)}
    owner_history_stats: dict[str, Any] = {"skipped": bool(shards_only)}
    if not shards_only:
        promoter.populate_cache_view(art_delta.dirty_quadrants)
        if (
            (promoter.staged_data / "overlays.json").exists()
            and (promoter.staged_data / "search_index.json").exists()
            and (promoter.staged_data / "curated_overrides.json").exists()
        ):
            scorecard_stats = rebuild_neighborhood_scorecards(
                cache_dir=promoter.staged_cache_view,
                data_dir=promoter.staged_data,
                tax_year=ctx.tax_year,
                fetch_network=False,
            )
        if (
            (promoter.staged_data / "haif_index.json").exists()
            and (promoter.staged_data / "citydir_index.json").exists()
            and (promoter.staged_data / "buildings.geojson").exists()
        ):
            iso_day = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
            audit_res = run_historical_audit(
                apply_fixes=False,
                tax_year=ctx.tax_year,
                cache_dir=promoter.staged_cache_view,
                data_dir=promoter.staged_data,
                generated_on=iso_day,
            )
            write_compact_json(
                promoter.staged_data / "historical_audit_queue.json", audit_res["queue"]
            )
            write_compact_json(
                promoter.staged_data / "historical_audit_summary.json", audit_res["summary"]
            )
            audit_refresh_stats = {
                "total_queued_contradictions": int(
                    audit_res["summary"]["total_queued_contradictions"]
                )
            }
        from atlas_pipeline.build_historical_ownership_2005_2026 import (
            MIN_PARSED_ACCOUNTS_PER_ROLL,
            maybe_rebuild_owner_history_for_staged_roll,
        )

        eff_oh_min_parsed = (
            1
            if allow_partial_roll
            else (
                MIN_PARSED_ACCOUNTS_PER_ROLL
                if int(preflight.get("new_acct_count") or 0) >= MIN_PARSED_ACCOUNTS_PER_ROLL
                else max(
                    1,
                    int(
                        min(base_acct_cnt, int(preflight.get("new_acct_count") or 1))
                        * cfg.min_account_retention_ratio
                    ),
                )
            )
        )
        owner_history_stats = maybe_rebuild_owner_history_for_staged_roll(
            cache_dir=promoter.staged_cache_view,
            staged_roll_zip=acct_zip,
            tax_year=ctx.tax_year,
            staged_data_dir=promoter.staged_data,
            min_parsed_accounts=eff_oh_min_parsed,
        )

    standing_hcads_now = {
        normalize_hcad13((f.get("properties") or {}).get("hcad_num"))
        for fl in patched_by_quad.values()
        for f in fl
    } - {""}
    obsolete_live_rels = {
        f"houston_buildings_{q}.pmtiles"
        for q in art_delta.dirty_quadrants
        if not (promoter.staged_data / f"houston_buildings_{q}.pmtiles").exists()
        and (ddir / f"houston_buildings_{q}.pmtiles").exists()
    }
    if (promoter.staged_data / "owner_history").is_dir() and (ddir / "owner_history").is_dir():
        for live_oh_p in (ddir / "owner_history").glob("owners_*.json.gz"):
            if not (promoter.staged_data / "owner_history" / live_oh_p.name).exists():
                obsolete_live_rels.add(f"owner_history/{live_oh_p.name}")
    soft_trim_stats = prune_optional_enrichment_for_soft_budget(
        promoter.staged_data,
        ddir,
        standing_hcads_now,
        obsolete_live_rels=obsolete_live_rels,
    )
    bundle_gate = validate_staged_app_bundle(
        promoter.staged_data,
        ddir,
        obsolete_live_rels=obsolete_live_rels,
    )
    commit_stats = promoter.commit(dirty_quadrants=art_delta.dirty_quadrants)

    delta_payload.update({
        "synthesis": synth_stats,
        "dedup_dropped_by_quad": dedup_dropped,
        "dirty_quadrants": sorted(art_delta.dirty_quadrants),
        "rebuilt_pmtiles_shards": rebuilt_shards,
        "index_reconciliation": {
            k: v
            for k, v in sync_stats.items()
            if k not in ("stats_summary", "pmtiles_manifest")
        },
        "total_buildings_after": int(sync_stats["stats_summary"]["total_buildings"]),
        "dated_buildings_after": int(sync_stats["stats_summary"]["dated_buildings"]),
        "latest_year_after": int(sync_stats["stats_summary"]["latest_year"]),
        "scorecards": {
            "skipped": scorecard_stats.get("skipped", False),
            "feature_count": len(scorecard_stats.get("scorecards") or ())
            if isinstance(scorecard_stats, dict)
            else 0,
        },
        "historical_audit": audit_refresh_stats,
        "owner_history": {
            "skipped": bool(owner_history_stats.get("skipped", False)),
            "shard_count": int(owner_history_stats.get("shard_count") or 0),
            "owner_history_accounts": int(owner_history_stats.get("owner_history_accounts") or 0),
        },
        "soft_budget_trim": soft_trim_stats,
        "bundle_gate": bundle_gate,
        "promotion": commit_stats,
    })
    delta_report_path.write_text(json.dumps(delta_payload, indent=2) + "\n", encoding="utf-8")
    return delta_payload


def format_annual_refresh_cli_summary(stats: dict[str, Any], *, elapsed_s: float = 0.0) -> str:
    """Format a crisp operator CLI summary without touching `full_build` keys (DF-8)."""
    pc = stats.get("parcel_counts") or {}
    fc = stats.get("feature_counts") or {}
    pre = stats.get("preflight") or {}
    rpts = stats.get("reports") or {}
    mode_lbl = str(stats.get("mode") or "dry_run").upper()
    lines = [
        f"=== Annual HCAD Roll Refresh Complete ({mode_lbl}) ===",
        f"   Elapsed time             : {elapsed_s:.1f}s",
        f"   Tax roll transition      : {stats.get('prior_tax_year')} -> {stats.get('tax_year')} "
        f"(certified={pre.get('is_certified_roll')}, ratio={pre.get('certified_ratio', 0.0):.1%})",
        f"   Account retention        : {pre.get('new_account_count', 0):,} accts "
        f"({pre.get('account_retention_ratio', 0.0):.2%}), "
        f"{pre.get('new_parcel_count', 0):,} parcels ({pre.get('parcel_retention_ratio', 0.0):.2%})",
        f"   UNCHANGED                : {pc.get('UNCHANGED', 0):,} parcels ({fc.get('UNCHANGED', 0):,} footprints)",
        f"   UPDATED_STANDING         : {pc.get('UPDATED_STANDING', 0):,} parcels ({fc.get('UPDATED_STANDING', 0):,} footprints)",
        f"   DEMOLISHED_TO_VACANT     : {pc.get('DEMOLISHED_TO_VACANT', 0):,} parcels "
        f"({fc.get('DEMOLISHED_TO_VACANT', 0):,} footprints, {float(stats.get('demolition_fraction') or 0.0):.2%})",
        f"   REPLATTED_OR_RETIRED     : {pc.get('REPLATTED_OR_RETIRED', 0):,} parcels ({fc.get('REPLATTED_OR_RETIRED', 0):,} footprints)",
        f"   NEW_CONSTRUCTION         : {pc.get('NEW_CONSTRUCTION', 0):,} parcels ({fc.get('NEW_CONSTRUCTION', 0):,} footprints)",
        f"   Orphaned curated alerts  : {stats.get('orphaned_curated_count', 0):,}",
        f"   Cohort 5 Lost candidates : {stats.get('lost_houston_candidate_count', 0):,} (read-only)",
        f"   Delta report             : {rpts.get('delta_report', '')}",
        f"   Orphan report            : {rpts.get('orphaned_curated_entities', '')}",
        f"   Lost Houston queue       : {rpts.get('lost_houston_candidates', '')}",
    ]
    if stats.get("mode") == "apply":
        bg = stats.get("bundle_gate") or {}
        lines.extend([
            f"   Rebuilt PMTiles shards   : {stats.get('rebuilt_pmtiles_shards', [])}",
            f"   Total dated buildings    : {stats.get('dated_buildings_after', 0):,} / "
            f"{stats.get('total_buildings_after', 0):,} (0 unknown)",
            f"   Projected app/ bundle    : {bg.get('projected_app_mib', 0.0):.2f} MiB (< 980.0 MiB ceiling)",
        ])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    """Standalone CLI entry point forcing `--mode annual-refresh` by default."""
    from atlas_pipeline.cli import main as _cli_main

    eff_argv = list(sys.argv[1:] if argv is None else argv)
    if not any(a == "--mode" or a.startswith("--mode=") for a in eff_argv):
        eff_argv = ["--mode", "annual-refresh", *eff_argv]
    _cli_main(eff_argv)


if __name__ == "__main__":
    main()
