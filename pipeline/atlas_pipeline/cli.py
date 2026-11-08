"""Command-line interface for running the Houston Building Atlas data pipeline."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from atlas_pipeline.fetch_data import fetch_core_parcels_and_footprints
from atlas_pipeline.spatial_join import join_footprints_to_parcels
from atlas_pipeline.tile_builder import write_atlas_bundle


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Build the Houston Building Atlas v2 static PMTiles + GeoJSON data bundle."
    )
    parser.add_argument(
        "--mode",
        choices=("core", "full", "campuses", "annual-refresh", "ingest-tpia"),
        default="core",
        help=(
            "Pipeline mode: 'core' (Historic Houston Core + Landmarks + Districts + OSM Footprints), "
            "'full' (Harris County bulk HCAD), 'campuses' (Campus & school dating/situs hydration), "
            "'annual-refresh' (Guarded incremental annual HCAD appraisal roll & parcel refresher), "
            "or 'ingest-tpia' (Harris County Clerk 1961-present RP/MC & pre-2005 HCAD TPIA ingestion)."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "app" / "public" / "data",
        help="Destination directory for generated PMTiles and JSON assets.",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "cache",
        help="Directory for caching raw GIS / CAMA / OSM responses.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional maximum parcel limit when running --mode full.",
    )
    parser.add_argument(
        "--tax-year",
        type=int,
        default=None,
        help="Target HCAD appraisal roll tax year (e.g. 2027) for --mode annual-refresh / full / campuses.",
    )
    parser.add_argument(
        "--discover-latest-year",
        action="store_true",
        help="Probe download.hcad.org/data/CAMA/<YYYY>/ to auto-discover the latest published roll year.",
    )
    parser.add_argument(
        "--roll-dir",
        type=Path,
        default=None,
        help="Local directory holding pre-downloaded HCAD archives (Real_building_land.zip, Real_acct_owner.zip, Parcels.zip).",
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Run Stage 1-3 validation and emit annual delta/orphan/Cohort-5 audit reports with zero live mutations (default for annual-refresh).",
    )
    mode_group.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Shadow-stage patched shards/indexes, validate bundle budgets, and atomically promote into app/ and pipeline/cache/.",
    )
    parser.add_argument(
        "--shards-only",
        action="store_true",
        default=False,
        help="Reconcile building shards and core indexes without rebuilding neighborhood_scorecards.json or historical_audit_queue.json.",
    )
    parser.add_argument(
        "--cache-bust",
        type=str,
        default=None,
        help="Explicit cache-bust token (e.g. 20270115a) applied across manifest, JS, and HTML when running --apply.",
    )
    parser.add_argument(
        "--observed-footprints-ndjson",
        type=Path,
        default=None,
        help="Optional GeoJSONSeq/NDJSON of observed Microsoft/OSM building footprints to prefer over parcel synthesis for NEW_CONSTRUCTION.",
    )
    parser.add_argument(
        "--allow-partial-roll",
        action="store_true",
        default=False,
        help="Bypass >= 95%% countywide retention gates (intended solely for hermetic subset tests).",
    )
    parser.add_argument(
        "--allow-pmtiles-fallback",
        action="store_true",
        default=False,
        help="Allow --dry-run to sample baseline features from houston_buildings_*.pmtiles when aligned_ndjson/*.geojsonseq shards are absent.",
    )
    parser.add_argument(
        "--auto-revive-rebuilt-suppressed",
        action="store_true",
        default=False,
        help="Allow suppress_only vacant lots with verified post-suppression construction (>= 400 sq ft) to clear suppression automatically.",
    )
    parser.add_argument(
        "--clerk-tpia-dir",
        "--clerk-dir",
        dest="clerk_tpia_dir",
        type=Path,
        default=None,
        help="Directory or archive holding Harris County Clerk PIA.aspx tabular index files (for --mode ingest-tpia).",
    )
    parser.add_argument(
        "--hcad-tpia-dir",
        "--hcad-dir",
        dest="hcad_tpia_dir",
        type=Path,
        default=None,
        help="Directory or archive holding pre-2005 HCAD TPIA ownership/sales tables (for --mode ingest-tpia).",
    )
    args = parser.parse_args(argv)

    t0 = time.time()
    print(f"=== Houston Building Atlas v2 Pipeline (mode={args.mode}) ===")
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    if args.mode == "core":
        parcels, footprints, overlays = fetch_core_parcels_and_footprints(args.cache_dir)
        print("-> Running spatial join (matching observed building footprints + deriving architectural footprints)...")
        joined_buildings, valid_parcels = join_footprints_to_parcels(parcels, footprints)
        print(
            f"   Spatial join complete: {len(joined_buildings):,} building footprints "
            f"and {len(valid_parcels):,} tax parcels."
        )
        print(f"-> Compiling PMTiles v3 MVT archive & static bundle into {args.output_dir}...")
        stats = write_atlas_bundle(
            output_dir=args.output_dir,
            buildings=joined_buildings,
            parcels=valid_parcels,
            overlays=overlays,
        )
    elif args.mode == "campuses":
        from atlas_pipeline.fix_campus_anomalies_and_schools import run_campus_and_school_fixes

        stats = run_campus_and_school_fixes(
            args.cache_dir,
            args.output_dir,
            compile_pmtiles=True,
            tax_year=args.tax_year,
        )
    elif args.mode == "annual-refresh":
        from atlas_pipeline.update_annual_hcad_roll import (
            format_annual_refresh_cli_summary,
            run_annual_hcad_refresh,
        )

        stats = run_annual_hcad_refresh(
            cache_dir=args.cache_dir,
            output_dir=args.output_dir,
            tax_year=args.tax_year,
            discover_latest_year=bool(args.discover_latest_year),
            roll_dir=args.roll_dir,
            apply=bool(args.apply),
            shards_only=bool(args.shards_only),
            cache_bust=args.cache_bust,
            observed_footprints_ndjson=args.observed_footprints_ndjson,
            allow_partial_roll=bool(args.allow_partial_roll),
            allow_pmtiles_fallback=bool(args.allow_pmtiles_fallback),
            auto_revive_rebuilt_suppressed=bool(args.auto_revive_rebuilt_suppressed),
        )
        elapsed = time.time() - t0
        print(format_annual_refresh_cli_summary(stats, elapsed_s=elapsed))
        return
    elif args.mode == "ingest-tpia":
        from atlas_pipeline.ingest_tpia_clerk_and_hcad import (
            format_tpia_ingestion_cli_summary,
            run_tpia_ingestion,
        )
        from atlas_pipeline.schema import ACTIVE_TAX_YEAR

        app_dir_cand = (
            args.output_dir.parents[1]
            if len(args.output_dir.parts) >= 3 and args.output_dir.parts[-2:] == ("public", "data")
            else None
        )
        stats = run_tpia_ingestion(
            output_dir=args.output_dir,
            cache_dir=args.cache_dir,
            app_dir=app_dir_cand,
            clerk_tpia_dir=args.clerk_tpia_dir,
            hcad_tpia_dir=args.hcad_tpia_dir,
            apply=bool(args.apply),
            cache_bust=args.cache_bust,
            active_tax_year=args.tax_year if args.tax_year is not None else ACTIVE_TAX_YEAR,
        )
        elapsed = time.time() - t0
        print(format_tpia_ingestion_cli_summary(stats, elapsed_s=elapsed))
        return
    else:
        from atlas_pipeline.full_build import run_full_county_build

        stats = run_full_county_build(args.cache_dir, args.output_dir, tax_year=args.tax_year)

    elapsed = time.time() - t0
    print("=== Pipeline Complete ===")
    print(f"   Elapsed time          : {elapsed:.1f}s")
    print(f"   Total buildings       : {stats['total_buildings']:,} ({stats['observed_footprints']:,} observed footprints)")
    print(f"   Dated buildings       : {stats['dated_buildings']:,} ({stats['earliest_year']}-{stats['latest_year']})")
    print(f"   Historic landmarks    : {stats['total_landmarks']:,}")
    print(f"   Historic districts    : {stats['total_historic_districts']:,}")
    print(f"   THC markers           : {stats['total_thc_markers']:,}")
    print(f"   PMTiles archive size  : {stats['pmtiles_size_bytes'] / (1024 * 1024):.1f} MB")


if __name__ == "__main__":
    main()
