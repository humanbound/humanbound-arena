# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Humanbound
import json
from datetime import UTC, date, datetime

import pytest

import arena_lib
import build_index
from conftest import make_agent, manifest

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


def _two_agents(repo):
    doc = manifest("shop")
    doc["ground_truth"]["V2"] = dict(doc["ground_truth"]["V1"], category="llm002")
    make_agent(repo, "shop", doc=doc)
    make_agent(repo, "alpha")
    return arena_lib.discover(repo)


def test_index_fields_and_relative_manifest_urls(repo):
    index = build_index.build_index(_two_agents(repo), None, NOW)
    assert index["schema_version"] == 1
    assert index["min_hb_version"] == build_index.MIN_HB_VERSION
    assert index["generated_at"] == "2026-09-25T12:00:00Z"
    assert [a["id"] for a in index["agents"]] == ["alpha", "shop"]
    shop = index["agents"][1]
    assert shop == {
        "id": "shop",
        "version": "1.0.0",
        "name": "Shop",
        "description": "The shop agent.",
        "tags": ["demo"],
        "difficulty": "easy",
        "developer": {"name": "Humanbound team", "url": "https://github.com/humanbound"},
        "manifest_url": "agents/shop/arena.yaml",
        "categories": ["llm001", "llm002"],
        "references": ["atlas:AML.T0051.000", "atlas:AML.T0057"],
    }
    assert index["agents"][0]["manifest_url"] == "agents/alpha/arena.yaml"


def test_images_are_omitted_when_no_digest_is_known(repo):
    index = build_index.build_index(_two_agents(repo), None, NOW)
    assert all("images" not in e for e in index["agents"])


def test_digests_are_recorded_per_image_ref(repo):
    agents = _two_agents(repo)
    digests = {"ghcr.io/humanbound/arena-shop:1.0.0": "sha256:" + "a" * 64}
    index = build_index.build_index(agents, None, NOW, digests=digests)
    shop = next(e for e in index["agents"] if e["id"] == "shop")
    assert shop["images"] == [
        {"ref": "ghcr.io/humanbound/arena-shop:1.0.0", "digest": "sha256:" + "a" * 64}
    ]
    assert "images" not in next(e for e in index["agents"] if e["id"] == "alpha")


def test_digests_are_carried_forward_for_agents_this_run_did_not_rebuild(repo):
    agents = _two_agents(repo)
    first = build_index.build_index(
        agents, None, NOW, digests={"ghcr.io/humanbound/arena-shop:1.0.0": "sha256:" + "a" * 64}
    )
    # A later run rebuilt only alpha, so shop produced no metadata.
    second = build_index.build_index(
        agents, first, LATER, digests={"ghcr.io/humanbound/arena-alpha:1.0.0": "sha256:" + "b" * 64}
    )
    shop = next(e for e in second["agents"] if e["id"] == "shop")
    alpha = next(e for e in second["agents"] if e["id"] == "alpha")
    assert shop["images"][0]["digest"] == "sha256:" + "a" * 64  # preserved
    assert alpha["images"][0]["digest"] == "sha256:" + "b" * 64  # fresh


def test_fresh_metadata_overrides_a_carried_forward_digest(repo):
    agents = _two_agents(repo)
    ref = "ghcr.io/humanbound/arena-shop:1.0.0"
    first = build_index.build_index(agents, None, NOW, digests={ref: "sha256:" + "a" * 64})
    second = build_index.build_index(agents, first, LATER, digests={ref: "sha256:" + "c" * 64})
    shop = next(e for e in second["agents"] if e["id"] == "shop")
    assert shop["images"][0]["digest"] == "sha256:" + "c" * 64


def test_a_missing_developer_block_fails_the_build(repo):
    doc = manifest("shop")
    del doc["developer"]
    make_agent(repo, "shop", doc=doc)
    with pytest.raises(SystemExit, match="developer"):
        build_index.build_index(arena_lib.discover(repo), None, NOW)


def test_generated_at_only_moves_when_the_content_changes(repo):
    agents = _two_agents(repo)
    first = build_index.build_index(agents, None, NOW)
    assert build_index.build_index(agents, first, LATER)["generated_at"] == first["generated_at"]
    bumped = [a for a in agents]
    bumped[1].manifest["version"] = "1.1.0"
    changed = build_index.build_index(bumped, first, LATER)
    assert changed["generated_at"] == "2026-09-26T12:00:00Z"


def test_health_is_carried_over_from_the_previous_index(repo):
    agents = _two_agents(repo)
    previous = build_index.build_index(agents, None, NOW)
    previous["agents"][1]["health"] = {"status": "passing"}
    index = build_index.build_index(agents, previous, NOW)
    assert index["agents"][1]["health"] == {"status": "passing"}


def test_apply_health_tracks_failing_since():
    index = {"agents": [{"id": "a"}, {"id": "b"}]}
    day1, day2 = date(2026, 9, 1), date(2026, 9, 2)
    build_index.apply_health(index, {"a": True, "b": False}, day1)
    assert index["agents"][0]["health"] == {"status": "passing"}
    assert index["agents"][1]["health"] == {"status": "failing", "failing_since": "2026-09-01"}
    build_index.apply_health(index, {"b": False}, day2)
    assert index["agents"][1]["health"] == {"status": "failing", "failing_since": "2026-09-01"}
    build_index.apply_health(index, {"b": True}, day2)
    assert index["agents"][1]["health"] == {"status": "passing"}


def test_health_that_has_not_changed_leaves_the_catalog_as_it_was(repo):
    # Each change to the catalog is a pull request, so a quiet night must change nothing.
    (repo / "README.md").write_text("<!-- catalog:start -->\n<!-- catalog:end -->\n")
    make_agent(repo, "shop")
    results = repo / "results.json"
    results.write_text(json.dumps([{"id": "shop", "ok": True}]))
    assert build_index.main(["--root", str(repo), "--health-results", str(results)]) == 0
    first = (repo / "index.json").read_text()
    assert build_index.main(["--root", str(repo), "--health-results", str(results)]) == 0
    assert (repo / "index.json").read_text() == first
    assert build_index.main(["--root", str(repo), "--check"]) == 0


def test_deprecated_after_30_failing_days(repo):
    agents = _two_agents(repo)
    previous = build_index.build_index(agents, None, NOW)
    previous["agents"][1]["health"] = {
        "status": "failing",
        "failing_since": "2026-08-26",
    }
    index = build_index.build_index(agents, previous, NOW)
    assert index["agents"][1]["deprecated"] is True
    previous["agents"][1]["health"]["failing_since"] = "2026-08-27"
    assert "deprecated" not in build_index.build_index(agents, previous, NOW)["agents"][1]


def test_deprecated_file_marks_an_agent(repo):
    agents = _two_agents(repo)
    (repo / "agents" / "shop" / "DEPRECATED").write_text("Maintainer unresponsive since ...\n")
    index = build_index.build_index(agents, None, NOW)
    assert index["agents"][1]["deprecated"] is True


def test_render_catalog(repo):
    index = build_index.build_index(_two_agents(repo), None, NOW)
    index["agents"][0]["health"] = {"status": "passing"}
    index["agents"][1]["health"] = {"status": "failing", "failing_since": "2026-09-20"}
    index["agents"][1]["deprecated"] = True
    table = build_index.render_catalog(index)
    lines = table.splitlines()
    assert lines[0] == "| Agent | What it teaches | Category | Difficulty | Health |"
    assert lines[2].startswith("| [Alpha](agents/alpha) | The alpha agent. | LLM01 Prompt")
    assert "passing" in lines[2]
    assert "(deprecated)" in lines[3] and "failing since 2026-09-20" in lines[3]
    assert "LLM02 Sensitive Information Disclosure" in lines[3]


def test_render_catalog_with_no_agents():
    assert "No agents yet" in build_index.render_catalog({"agents": []})


def test_update_readme_replaces_only_between_markers():
    text = "before\n<!-- catalog:start -->\nold\n<!-- catalog:end -->\nafter\n"
    out = build_index.update_readme(text, "| new |")
    assert out == "before\n<!-- catalog:start -->\n| new |\n<!-- catalog:end -->\nafter\n"
    with pytest.raises(ValueError):
        build_index.update_readme("no markers", "x")


def test_main_writes_and_checks(repo, capsys):
    _two_agents(repo)
    (repo / "README.md").write_text("# X\n<!-- catalog:start -->\n<!-- catalog:end -->\n")
    assert build_index.main(["--root", str(repo), "--check"]) == 1
    assert build_index.main(["--root", str(repo)]) == 0
    index = json.loads((repo / "index.json").read_text())
    assert [a["id"] for a in index["agents"]] == ["alpha", "shop"]
    assert "[Shop](agents/shop)" in (repo / "README.md").read_text()
    assert build_index.main(["--root", str(repo), "--check"]) == 0


def test_main_applies_health_results(repo, tmp_path):
    _two_agents(repo)
    (repo / "README.md").write_text("<!-- catalog:start -->\n<!-- catalog:end -->\n")
    results = tmp_path / "results.json"
    results.write_text(json.dumps([{"id": "shop", "ok": False, "detail": "boom"}]))
    assert build_index.main(["--root", str(repo), "--health-results", str(results)]) == 0
    shop = json.loads((repo / "index.json").read_text())["agents"][1]
    assert shop["health"]["status"] == "failing"


def test_the_index_parses_with_the_cli_model(repo):
    catalog = pytest.importorskip("humanbound_cli.arena.catalog")
    index = build_index.build_index(_two_agents(repo), None, NOW)
    parsed = catalog.Index.model_validate(index)
    assert parsed.agents[1].manifest_url == "agents/shop/arena.yaml"


def test_an_entry_lists_every_standard_its_vulnerabilities_refer_to(repo):
    doc = manifest("shop")
    doc["ground_truth"]["V1"]["references"] = ["atlas:AML.T0057", "owasp-llm:LLM02"]
    doc["ground_truth"]["V2"] = dict(
        doc["ground_truth"]["V1"],
        category="asi02",
        references=["atlas:AML.T0086", "atlas:AML.T0057"],
    )
    make_agent(repo, "shop", doc=doc)
    (entry,) = build_index.build_index(arena_lib.discover(repo), None, NOW)["agents"]
    assert entry["categories"] == ["asi02", "llm001"]
    assert entry["references"] == ["atlas:AML.T0057", "atlas:AML.T0086", "owasp-llm:LLM02"]


def test_the_catalog_names_agentic_categories_too(repo):
    doc = manifest("shop")
    doc["ground_truth"]["V1"]["category"] = "asi02"
    make_agent(repo, "shop", doc=doc)
    index = build_index.build_index(arena_lib.discover(repo), None, NOW)
    assert "ASI02 Tool Misuse" in build_index.render_catalog(index)
