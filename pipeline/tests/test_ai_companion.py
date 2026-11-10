"""Unit and integration tests for AI Research Companion, /llms.txt, /llms-full.txt, and centralized environment URLs."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import zlib

import pytest

from atlas_pipeline import cli
from atlas_pipeline.build_ai_companion import (
    build_atlas_environments_payload,
    build_llms_full_txt_content,
    build_llms_txt_content,
    set_environment_urls,
    sync_ai_companion_bundle,
    write_atlas_environments_json,
)
from atlas_pipeline.schema import (
    DEFAULT_ATLAS_BASE_URL,
    DEFAULT_ATLAS_SANDBOX_URL,
    resolve_atlas_base_url,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_PY_PATH = REPO_ROOT / "skills" / "houston-building-atlas" / "scripts" / "query_atlas.py"


def _load_query_atlas_module():
    spec = importlib.util.spec_from_file_location("query_atlas", SKILL_PY_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["query_atlas"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_resolve_atlas_base_url_priority_and_trailing_slash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verify resolve_atlas_base_url priority: explicit -> env -> atlas_environments.json -> default."""
    monkeypatch.delenv("ATLAS_BASE_URL", raising=False)

    # 1. Explicit argument wins and normalizes trailing slash
    assert (
        resolve_atlas_base_url("https://atlas.preservationhouston.org")
        == "https://atlas.preservationhouston.org/"
    )

    # 2. Environment variable ATLAS_BASE_URL wins over manifest/default
    monkeypatch.setenv("ATLAS_BASE_URL", "https://staging-atlas.preservationhouston.org")
    assert (
        resolve_atlas_base_url()
        == "https://staging-atlas.preservationhouston.org/"
    )
    monkeypatch.delenv("ATLAS_BASE_URL", raising=False)

    # 3. Live default matches DEFAULT_ATLAS_BASE_URL
    assert resolve_atlas_base_url() == DEFAULT_ATLAS_BASE_URL
    assert DEFAULT_ATLAS_BASE_URL.endswith("/")
    assert DEFAULT_ATLAS_SANDBOX_URL.endswith("/")


def test_build_environment_manifest_and_llms_generators_hermetic(tmp_path: Path) -> None:
    """Verify build_atlas_environments_payload, build_llms_txt_content, and build_llms_full_txt_content."""
    vanity_url = "https://atlas.preservationhouston.org"
    manifest = build_atlas_environments_payload(
        production_url=vanity_url,
        sandbox_url="https://davemorris-gcp.github.io/houston-building-atlas",
        active_environment="production",
    )
    assert manifest["schema_version"] == 1
    assert manifest["canonical_base_url"] == "https://atlas.preservationhouston.org/"
    assert manifest["is_sandbox_split_active"] is True
    assert (
        manifest["environments"]["production"]["llms_full_txt_url"]
        == "https://atlas.preservationhouston.org/llms-full.txt"
    )
    assert (
        manifest["environments"]["sandbox"]["base_url"]
        == "https://davemorris-gcp.github.io/houston-building-atlas/"
    )

    out_env_path = write_atlas_environments_json(
        tmp_path / "atlas_environments.json",
        production_url=vanity_url,
        sandbox_url="https://davemorris-gcp.github.io/houston-building-atlas",
        active_environment="production",
    )
    out_env = json.loads(out_env_path.read_text(encoding="utf-8"))
    assert out_env["canonical_base_url"] == "https://atlas.preservationhouston.org/"

    # Seed minimal hermetic data_dir with en-dashes/em-dashes to test dash sanitization
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "stats_summary.json").write_text(
        json.dumps({"total_buildings": 1507897, "dated_buildings": 1507897}),
        encoding="utf-8",
    )
    (data_dir / "property_watchlist.json").write_text(
        json.dumps({
            "watchedProperties": [
                {
                    "id": "watch-kirby",
                    "trackerStatus": "Lost",
                    "demolishedDate": "2026-11-02",
                    "tier": 1,
                    "address": "2000 Smith St",
                    "propertyName": "John Henry Kirby Mansion (1894\u20132026)",
                    "yearBuilt": 1894,
                    "hcadAccount": "0152300000001",
                    "architect": "Olle J. Lorehn \u2014 James Ruskin Bailey",
                }
            ]
        }),
        encoding="utf-8",
    )
    (data_dir / "active_permit_alerts.json").write_text(
        json.dumps({
            "alerts": [
                {
                    "severity": "critical",
                    "badge": "Stage 2: Demo Permit Issued",
                    "address": "2000 SMITH ST",
                    "building_name": "John Henry Kirby Mansion",
                    "year_built": 1894,
                    "hcad_num": "0152300000001",
                    "lost_houston_id": "lost_kirby_mansion_1894",
                    "coh_permits": [{"project_no": "26090093"}],
                },
                {
                    "severity": "watch",
                    "badge": "DSHS Abatement",
                    "address": "5555 SAN FELIPE ST",
                    "building_name": "Marathon Oil Tower",
                    "year_built": 1983,
                    "hcad_num": "osm_city_260359937",
                    "building_id": "osm_city_260359937#bldg_marathon_oil_tower",
                    "dshs_notifications": [{"notification_number": "2026005618"}],
                },
            ]
        }),
        encoding="utf-8",
    )
    (data_dir / "overlays.json").write_text(
        json.dumps({
            "lost_houston": {
                "features": [
                    {
                        "properties": {
                            "id": "lost_kirby_mansion_1894",
                            "name": "John Henry Kirby Mansion",
                            "address": "2000 Smith St",
                            "built_year": 1894,
                            "demolished_year": 2026,
                            "architect": "James Ruskin Bailey",
                            "style": "Tudor Revival",
                        }
                    }
                ]
            },
            "landmarks": {
                "features": [
                    {
                        "properties": {
                            "hcad_num": "0550140020008",
                            "name": "Granada Theater",
                            "address": "3700 N Main St",
                            "year_built": 1946,
                            "protection_tier": "PLM",
                            "architect": "Jack Corgan",
                            "style": "Art Moderne",
                        }
                    }
                ]
            },
        }),
        encoding="utf-8",
    )
    (data_dir / "curated_overrides.json").write_text(
        json.dumps({
            "0550140020008": {
                "building_name": "Granada Theater (Heights Theater)",
                "address": "3700 N Main St",
                "year_built": 1946,
                "year_source": "Tier 2 Override",
                "notes": "Restored 2016\u20132018\u2014Good Brick recipient.",
            }
        }),
        encoding="utf-8",
    )

    llms_txt = build_llms_txt_content(base_url=vanity_url, data_dir=data_dir)
    llms_full = build_llms_full_txt_content(base_url=vanity_url, data_dir=data_dir)

    assert "https://atlas.preservationhouston.org/llms-full.txt" in llms_txt
    assert "Stage 2: Demo Permit Issued" in llms_full
    assert "`26090093`" in llms_full
    assert "`2026005618`" in llms_full
    assert (
        "https://atlas.preservationhouston.org/?id=osm_city_260359937#bldg_marathon_oil_tower"
        in llms_full
    )
    assert "\u2013" not in llms_txt and "\u2014" not in llms_txt
    assert "\u2013" not in llms_full and "\u2014" not in llms_full


def test_sync_ai_companion_bundle_and_cli_modes_roundtrip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify sync_ai_companion_bundle and cli.py --mode build-ai-companion / set-environment-urls."""
    res = sync_ai_companion_bundle()
    assert res["llms_txt_bytes"] > 5000
    assert res["llms_full_txt_bytes"] > 100000
    assert len(res["mirrored_files"]) == 4

    # Test CLI --mode build-ai-companion
    cli.main(["--mode", "build-ai-companion"])
    out = capsys.readouterr().out
    assert "AI Companion Bundle Synced" in out

    # Test set_environment_urls in a hermetic temporary repo root
    fake_root = tmp_path / "repo"
    (fake_root / "app" / "public" / "data").mkdir(parents=True)
    (fake_root / "pipeline" / "atlas_pipeline").mkdir(parents=True)
    (fake_root / "app" / "js").mkdir(parents=True)
    (fake_root / "skills" / "houston-building-atlas" / "scripts").mkdir(parents=True)
    (fake_root / "pipeline" / "atlas_pipeline" / "schema.py").write_text(
        'DEFAULT_ATLAS_BASE_URL = "https://old.example.org/"\nDEFAULT_ATLAS_SANDBOX_URL = "https://old-sandbox.example.org/"\n',
        encoding="utf-8",
    )
    (fake_root / "app" / "js" / "filterStore.js").write_text(
        'export const DEFAULT_ATLAS_BASE_URL = "https://old.example.org/";\nexport const DEFAULT_ATLAS_SANDBOX_URL = "https://old-sandbox.example.org/";\n',
        encoding="utf-8",
    )
    (fake_root / "skills" / "houston-building-atlas" / "scripts" / "query_atlas.py").write_text(
        'DEFAULT_BASE_URL = "https://old.example.org/"\n',
        encoding="utf-8",
    )
    (fake_root / "skills" / "houston-building-atlas" / "scripts" / "query_atlas.mjs").write_text(
        'export const DEFAULT_BASE_URL = "https://old.example.org/";\n',
        encoding="utf-8",
    )
    env_res = set_environment_urls(
        production_url="https://atlas.preservationhouston.org",
        sandbox_url=DEFAULT_ATLAS_SANDBOX_URL,
        active_environment="production",
        repo_root=fake_root,
    )
    assert env_res["production_url"] == "https://atlas.preservationhouston.org/"
    assert "pipeline/atlas_pipeline/schema.py" in env_res["updated_files"]
    assert "app/js/filterStore.js" in env_res["updated_files"]
    assert "skills/houston-building-atlas/scripts/query_atlas.py" in env_res["updated_files"]
    assert "skills/houston-building-atlas/scripts/query_atlas.mjs" in env_res["updated_files"]
    saved_env = json.loads(
        (fake_root / "app" / "public" / "data" / "atlas_environments.json").read_text(
            encoding="utf-8"
        )
    )
    assert saved_env["canonical_base_url"] == "https://atlas.preservationhouston.org/"


def test_query_atlas_python_skill_shorthand_confidentiality_and_live_queries(
    tmp_path: Path,
) -> None:
    """Verify query_atlas.py shorthand decoder, fail-closed TX Tax Code Sec. 25.025 redaction, and live queries."""
    qa = _load_query_atlas_module()

    # 1. Shorthand decoder
    decoded = qa.decode_hcad_shorthand("4/1/26 NC BLD DEMO/SEWER DISC CDU @ GD REMOD ADDN")
    assert "New Construction (NC)" in decoded
    assert "Stage 1 of 2 Demolition Prep" in decoded
    assert "Good (GD)" in decoded

    # 2. Fail-closed TX Tax Code Sec. 25.025 redaction on synthetic shard
    fake_data = tmp_path / "data"
    (fake_data / "permits").mkdir(parents=True)
    (fake_data / "owner_history").mkdir(parents=True)
    secret_owner = "CONFIDENTIAL PROTECTED HOMEOWNER"
    perm_payload = {
        "0621100000014": {
            "yr": 1925,
            "conf": 1,
            "co": [secret_owner],
            "note": "1925\u20131930 BUNGALOW\u2014GOOD",
            "p": [],
        }
    }
    (fake_data / "permits" / "permits_062.json.gz").write_bytes(
        zlib.compress(json.dumps(perm_payload).encode("utf-8"), wbits=zlib.MAX_WBITS | 16)
    )
    owner_payload = {
        "0621100000014": f"2015-2026~20150601~RP-2015-123456~{secret_owner}"
    }
    (fake_data / "owner_history" / "owners_062.json.gz").write_bytes(
        zlib.compress(json.dumps(owner_payload).encode("utf-8"), wbits=zlib.MAX_WBITS | 16)
    )
    (fake_data / "curated_overrides.json").write_text(
        json.dumps({"0621100000014": {"address": "1127 KEY ST", "year_built": 1925}}),
        encoding="utf-8",
    )

    hermetic_client = qa.AtlasDataClient(
        data_dir=fake_data, base_url="https://atlas.preservationhouston.org/"
    )
    conf_dossier = hermetic_client.build_hcad_dossier("0621100000014")
    assert conf_dossier["confidential_sec_25_025"] is True
    assert conf_dossier["current_owners"] == []
    assert conf_dossier["ownership_chain_2005_2026"] == []
    md = qa.format_dossier_markdown(conf_dossier)
    assert secret_owner not in md
    assert "TX Tax Code Sec. 25.025" in md
    assert "\u2013" not in md and "\u2014" not in md

    # 3. Live corpus queries against app/public/data
    live_client = qa.AtlasDataClient(data_dir=REPO_ROOT / "app" / "public" / "data")
    kirby = live_client.build_hcad_dossier("0152300000001")
    assert kirby["hcad_num"] == "0152300000001"
    assert kirby["is_demolished"] is True
    assert kirby["year_built"] == 1894

    addr_res = live_client.lookup_address("3700 N Main St")
    assert "0550140020008" in addr_res["matched_hcads"]

    port_res = live_client.lookup_portfolio("James Ruskin Bailey")
    assert port_res["extant_count"] >= 1
    assert port_res["lost_houston_count"] >= 1

    sc_res = live_client.lookup_scorecard("Old Sixth Ward")
    assert sc_res["match_count"] >= 1
