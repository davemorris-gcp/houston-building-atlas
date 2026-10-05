"""Command-line interface for running the Houston Building Atlas data pipeline."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from atlas_pipeline.fetch_data import fetch_core_parcels_and_footprints, fetch_full_hcad_parcels
from atlas_pipeline.spatial_join import join_footprints_to_parcels
from atlas_pipeline.tile_builder import write_atlas_bundle


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the Houston Building Atlas v2 static PMTiles + GeoJSON data bundle."
    )
    parser.add_argument(
        "--mode",
        choices=("core", "full"),
        default="core",
        help="Pipeline mode: 'core' (Historic Houston Core + Landmarks + Districts + OSM Footprints) or 'full' (Harris County bulk HCAD).",
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
    args = parser.parse_args()

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
    else:
        from atlas_pipeline.full_build import run_full_county_build

        stats = run_full_county_build(args.cache_dir, args.output_dir)

    elapsed = time.time() - t0
    print("=== Pipeline Complete ===")
    print(f"   Elapsed time          : {elapsed:.1f}s")
    print(f"   Total buildings       : {stats['total_buildings']:,} ({stats['observed_footprints']:,} observed footprints)")
    print(f"   Dated buildings       : {stats['dated_buildings']:,} ({stats['earliest_year']}–{stats['latest_year']})")
    print(f"   Historic landmarks    : {stats['total_landmarks']:,}")
    print(f"   Historic districts    : {stats['total_historic_districts']:,}")
    print(f"   THC markers           : {stats['total_thc_markers']:,}")
    print(f"   PMTiles archive size  : {stats['pmtiles_size_bytes'] / (1024 * 1024):.1f} MB")


if __name__ == "__main__":
    main()
