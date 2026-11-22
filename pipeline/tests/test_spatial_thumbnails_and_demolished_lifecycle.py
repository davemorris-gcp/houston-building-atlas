"""Unit tests for general demolished permit lifecycle rules, PMTiles footprint enrichment, and llms-full.txt retention."""

from __future__ import annotations

import json
from pathlib import Path

from atlas_pipeline.build_ai_companion import build_llms_full_txt_content
from atlas_pipeline.fetch_live_permits import (
    build_general_demolished_catalog,
    compute_alerts_summary_counts,
    enrich_alerts_with_pmtiles_footprints,
    extract_simplified_footprint_ring,
    index_curated_overrides_into_lookup,
    permit_alert_sort_key,
)
from atlas_pipeline.schema import ACTIVE_TAX_YEAR, extract_curated_demolished_hcads, is_sub_building_override

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "app" / "public" / "data"

CHAR_ENDASH = chr(0x2013)
CHAR_EMDASH = chr(0x2014)


def test_extract_simplified_footprint_ring_polygon_multipolygon_and_vertex_cap() -> None:
    """Verify outer-ring extraction, MultiPolygon largest-ring selection, and closed-ring vertex decimation."""
    assert extract_simplified_footprint_ring(None) == []
    assert extract_simplified_footprint_ring({"type": "Point", "coordinates": [-95.36, 29.76]}) == []
    assert extract_simplified_footprint_ring({"type": "Polygon", "coordinates": [[[-95.36, 29.76], [-95.35, 29.76]]]}) == []

    small_ring = [[-95.391, 29.789], [-95.390, 29.789], [-95.390, 29.790], [-95.391, 29.789]]
    large_ring = [
        [-95.3972 + 0.0001 * i, 29.7895 + 0.00005 * (i % 5)]
        for i in range(30)
    ] + [[-95.3972, 29.7895]]

    mp = {
        "type": "MultiPolygon",
        "coordinates": [[small_ring], [large_ring]],
    }
    simplified = extract_simplified_footprint_ring(mp, max_vertices=10)
    assert 3 <= len(simplified) <= 12
    assert simplified[0] == [-95.3972, 29.7895]
    assert simplified[-1] == [-95.3972, 29.7895]


def test_build_general_demolished_catalog_sources_and_standing_building_safety() -> None:
    """Verify general demolished catalog across overrides, recent Lost Houston, and Watchlist without demoting standing homes or landmarks."""
    overrides_fixture = {
        "overrides": {
            # 1) Whole-parcel demolished landmark override -> MUST be in catalog
            "0152300000001": {
                "hcad_num": "0152300000001",
                "is_demolished": True,
                "demolished_date": "2026-11-02",
                "lost_houston_id": "lost_kirby_mansion_1894",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-95.375, 29.750], [-95.374, 29.750], [-95.374, 29.751], [-95.375, 29.750]]],
                },
            },
            # 2) Whole-parcel vacant suppress_only -> MUST be in catalog
            "0621100000014": {
                "hcad_num": "0621100000014",
                "suppress_only": True,
            },
            # 3) Sub-building #b2 demolished detached garage on parcel with standing #b1 home -> MUST NOT pollute bare 13-digit HCAD!
            "0202150000018#b2": {
                "hcad_num": "0202150000018",
                "building_id": "0202150000018#b2",
                "is_building_override": True,
                "is_demolished": True,
                "demolished_date": "2024-06-01",
                "suppress_only": True,
            },
            # 4) Non-# feature override (osm_city_*) without is_building_override -> MUST stay scoped to osm_city_999999!
            "osm_city_999999": {
                "hcad_num": "0202150000018",
                "building_id": "osm_city_999999",
                "suppress_only": True,
            },
            # 5) Whole-parcel metadata override without year_built -> protects standing parcel from Lost Houston auto-demotion
            "0550140020008": {
                "hcad_num": "0550140020008",
                "building_name": "Standing Historic Building",
            },
        }
    }
    overlays_fixture = {
        "lost_houston": {
            "features": [
                # Historical 1882 predecessor on Rice Hotel block -> MUST NOT mark 0010570000009 parcel demolished!
                {
                    "properties": {
                        "id": "lost_republic_of_texas_capitol",
                        "hcad_num": "0010570000009",
                        "year_built": 1837,
                        "year_demolished": 1882,
                    },
                    "geometry": {"type": "Point", "coordinates": [-95.363, 29.759]},
                },
                # Active-year Lost Houston entry colliding with standing parcel override -> MUST NOT demote!
                {
                    "properties": {
                        "id": "lost_carriage_house_active_yr",
                        "hcad_num": "0550140020008",
                        "year_built": 1915,
                        "year_demolished": ACTIVE_TAX_YEAR,
                    },
                    "geometry": {"type": "Point", "coordinates": [-95.39, 29.78]},
                },
                # Recent active-year graduation to Lost Houston -> MUST be included!
                {
                    "properties": {
                        "id": "lost_recent_bungalow_2026",
                        "hcad_num": "0350240460099",
                        "year_built": 1924,
                        "demolished_date": f"{ACTIVE_TAX_YEAR}-10-20",
                        "year_demolished": ACTIVE_TAX_YEAR,
                    },
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[-95.39, 29.79], [-95.389, 29.79], [-95.389, 29.791], [-95.39, 29.79]]],
                    },
                },
            ]
        }
    }
    watchlist_fixture = {
        "watchedProperties": [
            {
                "id": "watch-lost-pair",
                "trackerStatus": "Lost",
                "demolishedDate": "2026-11-15",
                "lostHoustonId": "lost_pair_site",
                "hcadAccount": "0410500030998; 0410500030999",
            },
            {
                "id": "watch-removed-tombstone",
                "trackerStatus": "Lost",
                "demolishedDate": "2026-11-10",
                "removedFromWatchlist": True,
                "hcadAccount": "0180250000014",
            },
            {
                "id": "watch-active",
                "trackerStatus": "Active / High Priority",
                "hcadAccount": "0231000000006",
            },
        ]
    }

    cat = build_general_demolished_catalog(overrides_fixture, overlays_fixture, watchlist_fixture)
    assert cat["0152300000001"]["is_demolished"] is True
    assert cat["0152300000001"]["lost_houston_id"] == "lost_kirby_mansion_1894"
    assert cat["0621100000014"]["is_demolished"] is True
    assert cat["0350240460099"]["is_demolished"] is True
    assert cat["0350240460099"]["lost_houston_id"] == "lost_recent_bungalow_2026"
    assert cat["0410500030998"]["is_demolished"] is True
    assert cat["0410500030999"]["is_demolished"] is True
    assert cat["osm_city_999999"]["is_demolished"] is True
    assert "0231000000006" not in cat
    assert "0180250000014" not in cat
    assert "0550140020008" not in cat

    # Sub-building #b2 garage & osm_city_* must NEVER pollute parcel-level 0202150000018
    assert "0202150000018" not in cat

    # Countywide lookup guard: non-suppress_only demolished #b2 garage must NEVER hijack by_hcad['0410500030255']
    countywide_ov_map = {
        "0410500030255#b2": {
            "hcad_num": "0410500030255",
            "building_id": "0410500030255#b2",
            "address": "500 EXAMPLE AVE",
            "is_building_override": True,
            "is_demolished": True,
            "suppress_only": False,
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[-95.39, 29.79], [-95.389, 29.79], [-95.389, 29.791], [-95.39, 29.79]]],
            },
        }
    }
    cw_cat = build_general_demolished_catalog({"overrides": countywide_ov_map}, {}, {})
    cw_by_hcad: dict = {}
    cw_by_addr: dict = {}
    index_curated_overrides_into_lookup(countywide_ov_map, cw_cat, cw_by_hcad, cw_by_addr)
    assert "0410500030255#b2" in cw_by_hcad
    assert "0410500030255" not in cw_by_hcad
    assert cw_by_addr == {}

    # Bare 13-digit parcel override carrying legacy bld_* building_id is a whole-parcel override
    bare_bld_ov = {"hcad_num": "0011470000001", "building_id": "bld_003423", "year_built": 1912}
    assert is_sub_building_override("0011470000001", bare_bld_ov) is False
    assert extract_curated_demolished_hcads(overrides_fixture, include_suppressed=True) == {
        "0152300000001",
        "0621100000014",
    }
    # 1882 predecessor must NEVER pollute standing 1913 Rice Hotel parcel 0010570000009
    assert "0010570000009" not in cat

    # Verify live corpus: standing landmarks in buildings.geojson must NOT be falsely flagged demolished
    live_ov = json.loads((DATA_DIR / "curated_overrides.json").read_text(encoding="utf-8"))
    live_overlays = json.loads((DATA_DIR / "overlays.json").read_text(encoding="utf-8"))
    live_wl = json.loads((DATA_DIR / "property_watchlist.json").read_text(encoding="utf-8"))
    live_cat = build_general_demolished_catalog(live_ov, live_overlays, live_wl)

    assert "0152300000001" in live_cat  # Kirby Mansion (demolished Nov 2, 2026)
    for standing_landmark_hcad in (
        "0010570000009",  # 1913 Rice Hotel
        "0010310000001",  # 1910 Harris County Courthouse
        "0010450000009",  # 1909 Scanlan Building
        "0011010000001",  # 1869 Church of the Annunciation
        "0010010000013",  # 1962 POST Houston
        "0010320000001",  # 1975 Harris County Administration Building (1001 Preston St)
    ):
        assert standing_landmark_hcad not in live_cat, (
            f"Standing landmark HCAD {standing_landmark_hcad} falsely marked demolished: {live_cat.get(standing_landmark_hcad)}"
        )


def test_permit_alert_sort_key_and_summary_counts_parity() -> None:
    """Verify standing-before-demolished sorting and summary counts across all demolished flags."""
    alerts = [
        {
            "id": "demo_kirby",
            "severity": "critical",
            "landmark_name": "Kirby Mansion",
            "year_built": 1894,
            "is_demolished": True,
            "footprint_ring": [[-95.37, 29.75], [-95.36, 29.75], [-95.36, 29.76]],
            "coh_permits": [{"project_no": "1"}],
            "dshs_notifications": [],
        },
        {
            "id": "demo_lost_id_only",
            "severity": "critical",
            "landmark_name": "Graduated Site",
            "year_built": 1905,
            "lost_houston_id": "lost_graduated_1905",
            "footprint_ring": [[-95.37, 29.75], [-95.36, 29.75], [-95.36, 29.76]],
            "coh_permits": [{"project_no": "2"}],
            "dshs_notifications": [],
        },
        {
            "id": "standing_watch",
            "severity": "watch",
            "year_built": 1930,
            "footprint_ring": [[-95.37, 29.75], [-95.36, 29.75], [-95.36, 29.76]],
            "coh_permits": [],
            "dshs_notifications": [{"notification_number": "D1"}],
        },
        {
            "id": "standing_crit_plain",
            "severity": "critical",
            "year_built": 1920,
            "footprint_ring": [[-95.37, 29.75], [-95.36, 29.75], [-95.36, 29.76]],
            "coh_permits": [{"project_no": "3"}],
            "dshs_notifications": [],
        },
        {
            "id": "standing_crit_landmark",
            "severity": "critical",
            "landmark_name": "Protected Landmark",
            "year_built": 1928,
            "footprint_ring": [[-95.37, 29.75], [-95.36, 29.75], [-95.36, 29.76]],
            "coh_permits": [{"project_no": "4"}],
            "dshs_notifications": [],
        },
    ]

    ordered = sorted(alerts, key=permit_alert_sort_key)
    assert [a["id"] for a in ordered] == [
        "standing_crit_landmark",
        "standing_crit_plain",
        "standing_watch",
        "demo_kirby",
        "demo_lost_id_only",
    ]

    counts = compute_alerts_summary_counts(ordered)
    assert counts == {
        "total_flagged_buildings": 5,
        "standing_count": 3,
        "standing_critical_count": 2,
        "standing_watch_count": 1,
        "demolished_count": 2,
        "critical_count": 4,
        "watch_count": 1,
        "coh_permits_matched": 4,
        "dshs_notifications_matched": 1,
    }


def test_enrich_alerts_with_pmtiles_footprints_live_and_empty_guard(tmp_path: Path) -> None:
    """Verify z=15 PMTiles footprint backfill on real Harris County HCAD and clean 0 on empty dir."""
    empty_dir = tmp_path / "empty_data"
    empty_dir.mkdir()
    probe = [{"hcad_num": "0202150000018", "lon": -95.39724, "lat": 29.78962}]
    assert enrich_alerts_with_pmtiles_footprints(probe, data_dir=empty_dir) == 0
    assert "footprint_ring" not in probe[0]

    # Live PMTiles shard lookup for 1020 Cortlandt St (0202150000018)
    live_probe = [
        {"hcad_num": "0202150000018", "lon": -95.39724, "lat": 29.78962},
        {
            "hcad_num": "0152300000001",
            "lon": -95.3749,
            "lat": 29.7501,
            "footprint_ring": [[-95.375, 29.75], [-95.374, 29.75], [-95.374, 29.751]],
        },
    ]
    enriched_cnt = enrich_alerts_with_pmtiles_footprints(live_probe, data_dir=DATA_DIR)
    assert enriched_cnt == 1
    ring = live_probe[0].get("footprint_ring")
    assert isinstance(ring, list) and len(ring) >= 3
    for lng_v, lat_v in ring:
        assert -96.2 <= lng_v <= -94.7
        assert 29.3 <= lat_v <= 30.3


def test_build_ai_companion_llms_full_retains_and_labels_demolished_alerts(tmp_path: Path) -> None:
    """Verify Section 2 of llms-full.txt retains demolished sites and tags DEMOLISHED consistently."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "stats_summary.json").write_text(
        json.dumps({"total_buildings": 1507897, "dated_buildings": 1507897}),
        encoding="utf-8",
    )
    # Generate 26 standing critical alerts ahead of 2 demolished alerts (1 via is_demolished, 1 via lost_houston_id)
    alerts = [
        {
            "severity": "critical",
            "badge": "Stage 2: Demo Permit Issued",
            "address": f"{100 + i} TEST ST",
            "building_name": f"{100 + i} TEST ST",
            "year_built": 1920,
            "hcad_num": f"0202150000{i:03d}",
            "coh_permits": [{"project_no": f"2609{i:04d}"}],
        }
        for i in range(26)
    ] + [
        {
            "severity": "critical",
            "badge": f"Demolished (Nov 2, 2026){CHAR_EMDASH}Lost",
            "address": "2000 SMITH ST",
            "building_name": f"John Henry Kirby Mansion (1894{CHAR_ENDASH}2026)",
            "year_built": 1894,
            "hcad_num": "0152300000001",
            "is_demolished": True,
            "demolition_status": "demolished",
            "lost_houston_id": "lost_kirby_mansion_1894",
            "coh_permits": [{"project_no": "26090093"}],
        },
        {
            "severity": "critical",
            "badge": "Graduated to Lost Houston",
            "address": "1127 KEY ST",
            "building_name": "1127 KEY ST",
            "year_built": 1920,
            "hcad_num": "0621100000014",
            "lost_houston_id": "lost_1127_key",
            "coh_permits": [{"project_no": "26080001"}],
        },
    ]
    (data_dir / "active_permit_alerts.json").write_text(
        json.dumps({"alerts": alerts}),
        encoding="utf-8",
    )
    (data_dir / "property_watchlist.json").write_text(json.dumps({"watchedProperties": []}), encoding="utf-8")
    (data_dir / "overlays.json").write_text(json.dumps({"lost_houston": {"features": []}, "landmarks": {"features": []}}), encoding="utf-8")
    (data_dir / "curated_overrides.json").write_text(json.dumps({}), encoding="utf-8")

    llms_full = build_llms_full_txt_content(base_url="https://atlas.preservationhouston.org", data_dir=data_dir)
    assert "| DEMOLISHED |" in llms_full
    assert "2000 SMITH ST" in llms_full
    assert "1127 KEY ST" in llms_full
    # Both demolished entries (including lost_houston_id-only) must render with DEMOLISHED severity
    key_lines = [ln for ln in llms_full.splitlines() if "1127 KEY ST" in ln]
    assert len(key_lines) == 1 and key_lines[0].startswith("| DEMOLISHED |")
    assert CHAR_ENDASH not in llms_full and CHAR_EMDASH not in llms_full
