# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Humanbound
import copy
import json
import subprocess

import pytest
import yaml

import arena_lib
import build_index
import policy_check
from conftest import DOCKERFILE, NOTICE, SMOKE, make_agent, manifest


def _errors(repo, agent_id="demo"):
    return policy_check.check_agent(arena_lib.find_agent(agent_id, repo))


def _has(errors, text):
    assert any(text in e for e in errors), errors


def test_a_valid_agent_passes(repo):
    make_agent(repo, "demo")
    assert _errors(repo, "demo") == []


def test_every_agent_needs_a_lab(repo):
    path = make_agent(repo)
    (path / "lab" / "README.md").unlink()
    _has(_errors(repo), "missing lab/README.md")


def test_course_and_faq_are_optional(repo):
    make_agent(repo, files={"lab/COURSE.md": "# Course\n", "lab/FAQ.md": "# FAQ\n"})
    assert _errors(repo) == []
    make_agent(repo, "bare")
    assert _errors(repo, "bare") == []


def test_the_agent_service_is_always_in_services_agent(repo):
    path = make_agent(repo, files={"Dockerfile": "FROM x\n"})
    (path / "services" / "agent" / "Dockerfile").unlink()
    _has(_errors(repo), "missing services/agent/Dockerfile")


def test_several_services_need_a_compose_file(repo):
    make_agent(repo, files={"services/db/Dockerfile": DOCKERFILE})
    _has(_errors(repo), "several services (agent, db) need a docker-compose.yml")


def test_compose_agents_talk_to_the_agent_service(repo):
    doc = manifest(source={"compose": "docker-compose.yml", "service": "web"})
    compose = {"services": {"web": {"image": "ghcr.io/humanbound/arena-demo-web:1.0.0"}}}
    make_agent(repo, doc=doc, files={"docker-compose.yml": yaml.safe_dump(compose)})
    _has(_errors(repo), "source.service must be 'agent'")


def test_image_agents_have_no_compose_file(repo):
    make_agent(repo, files={"docker-compose.yml": "services: {}\n"})
    _has(_errors(repo), "docker-compose.yml")


def test_id_must_match_folder(repo):
    make_agent(repo, doc=manifest("other"))
    _has(_errors(repo), "id 'other' must equal the folder name 'demo'")


def test_version_must_be_semver(repo):
    make_agent(repo, doc=manifest(version="1.0"))
    _has(_errors(repo), "version")


def test_file_size_limit(repo):
    make_agent(repo, files={"data.txt": "x" * 1_000_001})
    _has(_errors(repo), "data.txt is larger than 1 MB")


def test_binaries_rejected_but_web_images_allowed(repo):
    make_agent(
        repo, files={"lab/slides.pdf": b"%PDF\x00\x01binary", "lab/logo.png": b"\x89PNG\x00\x00"}
    )
    errors = _errors(repo)
    _has(errors, "slides.pdf is a binary file")
    assert not any("logo.png" in e for e in errors)


def test_env_files_rejected(repo):
    make_agent(repo, files={".env": "OPENAI_API_KEY=x\n"})
    _has(_errors(repo), ".env")


UPSTREAM = {
    "repo": "https://github.com/example/vulnerable-agent",
    "ref": "0123456789abcdef0123456789abcdef01234567",
    "license": "MIT",
}


def test_developer_name_and_url_are_required(repo):
    doc = manifest("demo")
    del doc["developer"]
    make_agent(repo, "demo", doc=doc)
    _has(_errors(repo), "developer")


def test_developer_url_must_be_http(repo):
    doc = manifest("demo")
    doc["developer"]["url"] = "git@github.com:humanbound/x.git"
    make_agent(repo, "demo", doc=doc)
    _has(_errors(repo), "developer.url")


def test_developer_contact_is_optional(repo):
    doc = manifest("demo")
    doc["developer"]["contact"] = "security@example.org"
    make_agent(repo, "demo", doc=doc)
    assert _errors(repo) == []


def test_upstream_is_optional_but_validated_when_present(repo):
    doc = manifest("demo", source={"image": "ghcr.io/humanbound/arena-demo:1.0.0"})
    make_agent(repo, "demo", doc=doc)
    assert _errors(repo) == []  # absent is fine

    doc["source"]["upstream"] = dict(UPSTREAM, ref="deadbeef")
    make_agent(repo, "demo", doc=doc)
    _has(_errors(repo), "40-character commit SHA")


def test_upstream_license_must_be_on_the_osi_allowlist(repo):
    doc = manifest("demo", source={"image": "ghcr.io/humanbound/arena-demo:1.0.0"})
    doc["source"]["upstream"] = dict(UPSTREAM, license="Proprietary")
    files = {
        "services/agent/Dockerfile": f"FROM python:3.12-slim\nARG REF={UPSTREAM['ref']}\nUSER 65534\n"
    }
    make_agent(repo, "demo", doc=doc, files=files)
    _has(_errors(repo), "OSI allowlist")


def test_upstream_sha_must_appear_in_a_dockerfile(repo):
    doc = manifest("demo", source={"image": "ghcr.io/humanbound/arena-demo:1.0.0"})
    doc["source"]["upstream"] = dict(UPSTREAM)
    make_agent(repo, "demo", doc=doc)  # default Dockerfile has no SHA
    _has(_errors(repo), "must check out source.upstream.ref")

    files = {
        "services/agent/Dockerfile": f"FROM python:3.12-slim\nARG REF={UPSTREAM['ref']}\nUSER 65534\n"
    }
    make_agent(repo, "demo", doc=doc, files=files)
    assert _errors(repo) == []


def test_upstream_license_is_required_when_upstream_is_declared(repo):
    doc = manifest("demo", source={"image": "ghcr.io/humanbound/arena-demo:1.0.0"})
    doc["source"]["upstream"] = {k: v for k, v in UPSTREAM.items() if k != "license"}
    files = {
        "services/agent/Dockerfile": f"FROM python:3.12-slim\nARG REF={UPSTREAM['ref']}\nUSER 65534\n"
    }
    make_agent(repo, "demo", doc=doc, files=files)
    _has(_errors(repo), "source.upstream.license")


def test_upstream_license_accepts_an_spdx_expression(repo):
    doc = manifest("demo", source={"image": "ghcr.io/humanbound/arena-demo:1.0.0"})
    doc["source"]["upstream"] = dict(UPSTREAM, license="MIT OR Apache-2.0")
    files = {
        "services/agent/Dockerfile": f"FROM python:3.12-slim\nARG REF={UPSTREAM['ref']}\nUSER 65534\n"
    }
    make_agent(repo, "demo", doc=doc, files=files)
    assert _errors(repo) == []


def test_upstream_license_expression_rejects_a_non_osi_operand(repo):
    doc = manifest("demo", source={"image": "ghcr.io/humanbound/arena-demo:1.0.0"})
    doc["source"]["upstream"] = dict(UPSTREAM, license="MIT OR Proprietary")
    files = {
        "services/agent/Dockerfile": f"FROM python:3.12-slim\nARG REF={UPSTREAM['ref']}\nUSER 65534\n"
    }
    make_agent(repo, "demo", doc=doc, files=files)
    _has(_errors(repo), "OSI allowlist")


def test_upstream_license_accepts_a_with_exception_expression(repo):
    doc = manifest("demo", source={"image": "ghcr.io/humanbound/arena-demo:1.0.0"})
    doc["source"]["upstream"] = dict(UPSTREAM, license="Apache-2.0 WITH LLVM-exception")
    files = {
        "services/agent/Dockerfile": f"FROM python:3.12-slim\nARG REF={UPSTREAM['ref']}\nUSER 65534\n"
    }
    make_agent(repo, "demo", doc=doc, files=files)
    assert _errors(repo) == []


def test_upstream_license_still_checks_the_licence_before_with(repo):
    doc = manifest("demo", source={"image": "ghcr.io/humanbound/arena-demo:1.0.0"})
    doc["source"]["upstream"] = dict(UPSTREAM, license="Proprietary WITH LLVM-exception")
    files = {
        "services/agent/Dockerfile": f"FROM python:3.12-slim\nARG REF={UPSTREAM['ref']}\nUSER 65534\n"
    }
    make_agent(repo, "demo", doc=doc, files=files)
    _has(_errors(repo), "OSI allowlist")


def test_upstream_license_error_quotes_the_id_the_author_wrote(repo):
    doc = manifest("demo", source={"image": "ghcr.io/humanbound/arena-demo:1.0.0"})
    doc["source"]["upstream"] = dict(UPSTREAM, license="GPL-2.0+")
    files = {
        "services/agent/Dockerfile": f"FROM python:3.12-slim\nARG REF={UPSTREAM['ref']}\nUSER 65534\n"
    }
    make_agent(repo, "demo", doc=doc, files=files)
    (error,) = [e for e in _errors(repo) if "OSI allowlist" in e]
    # The unknown-operand list must name what the author wrote, not a stripped variant
    # ("GPL-2.0" is neither on the allowlist nor a valid SPDX id).
    assert "(GPL-2.0)" not in error
    assert "(GPL-2.0+)" in error


def test_every_agent_needs_a_lab_and_full_ground_truth(repo):
    path = make_agent(repo, "demo")
    (path / "lab" / "README.md").unlink()
    _has(_errors(repo), "missing lab/README.md")

    doc = manifest("demo")
    doc["ground_truth"]["V1"] = {"category": "llm001", "title": "T", "description": "D"}
    make_agent(repo, "demo", doc=doc)
    errors = _errors(repo)
    _has(errors, "needs 'severity'")
    _has(errors, "needs 'vector'")
    _has(errors, "needs 'success'")


def test_ground_truth_must_not_be_empty(repo):
    make_agent(repo, doc=manifest(ground_truth={}))
    _has(_errors(repo), "ground_truth")


def test_first_party_ground_truth_needs_the_rich_fields(repo):
    doc = manifest()
    for field in ("title", "severity", "vector", "success"):
        del doc["ground_truth"]["V1"][field]
    make_agent(repo, doc=doc)
    errors = _errors(repo)
    for field in ("title", "severity", "vector", "success"):
        _has(errors, f"ground_truth.V1 needs '{field}'")


def test_ground_truth_values_are_checked(repo):
    doc = manifest()
    doc["ground_truth"]["V1"].update(
        category="xss", severity="urgent", vector="psychic", success=[{"nope": 1}]
    )
    make_agent(repo, doc=doc)
    errors = _errors(repo)
    _has(errors, "unknown category 'xss'")
    _has(errors, "severity")
    _has(errors, "vector")
    _has(errors, "unknown kind 'nope'")


def test_env_names(repo):
    doc = manifest()
    doc["runtime"]["env"] = {"required": ["OPENAI_API_KEY", "hb_token"], "optional": []}
    make_agent(repo, doc=doc)
    errors = _errors(repo)
    _has(errors, "hb_token")
    _has(errors, "OPENAI_BASE_URL")


def test_image_tag_must_follow_the_naming_rule(repo):
    doc = manifest()
    doc["source"]["image"] = "docker.io/someone/demo:latest"
    make_agent(repo, doc=doc)
    _has(_errors(repo), "ghcr.io/humanbound/arena-demo:1.0.0")


def _compose_agent(repo, services):
    doc = manifest(source={"compose": "docker-compose.yml", "service": "agent"})
    files = {"docker-compose.yml": yaml.safe_dump({"services": services})}
    make_agent(repo, doc=doc, files=files)


def test_compose_services_use_images_not_builds(repo):
    _compose_agent(
        repo,
        {
            "agent": {
                "build": "services/agent",
                "image": "ghcr.io/humanbound/arena-demo-agent:1.0.0",
            }
        },
    )
    _has(_errors(repo), "build")


def test_compose_images_are_named_and_pinned(repo):
    _compose_agent(
        repo,
        {
            "agent": {"image": "ghcr.io/humanbound/arena-demo-agent:0.9.0"},
            "db": {"image": "postgres:latest"},
            "cache": {"image": "redis"},
            "ok": {"image": "postgres:16"},
        },
    )
    errors = _errors(repo)
    _has(errors, "ghcr.io/humanbound/arena-demo-agent:1.0.0")
    _has(errors, "postgres:latest")
    _has(errors, "'redis'")
    assert not any("postgres:16" in e for e in errors)


def test_compose_file_must_exist(repo):
    doc = manifest(source={"compose": "docker-compose.yml", "service": "agent"})
    make_agent(repo, doc=doc)
    _has(_errors(repo), "docker-compose.yml")


def test_malformed_smoke_file(repo):
    bad = copy.deepcopy(SMOKE)
    del bad["turns"]
    make_agent(repo, smoke=bad)
    _has(_errors(repo), "tests/smoke.yaml: turns")


def test_unknown_dirs_are_reported(repo):
    (repo / "agents" / "half-done").mkdir()
    (repo / "agents" / "half-done" / "README.md").write_text("x")
    assert policy_check.check_unknown_dirs(repo) == [
        "agents/half-done: no arena.yaml (every folder here must be an agent)"
    ]


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _commit(repo, message):
    _git(repo, "add", "-A")
    _git(
        repo,
        "-c",
        "user.email=a@b.c",
        "-c",
        "user.name=t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        message,
    )


def _repo_with_a_catalog(repo):
    """main holds one agent and the catalog generated from it; we are on a branch off it."""
    (repo / "README.md").write_text("<!-- catalog:start -->\n<!-- catalog:end -->\n")
    make_agent(repo, "shop")
    assert build_index.main(["--root", str(repo)]) == 0
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, "base")
    _git(repo, "checkout", "-qb", "pr")


def test_a_pull_request_that_leaves_the_catalog_alone_is_fine(repo):
    _repo_with_a_catalog(repo)
    (repo / "agents" / "shop" / "README.md").write_text("# changed\n")
    _commit(repo, "edit an agent")
    assert policy_check.index_errors("main", repo) == []


def test_the_first_pull_request_may_add_the_catalog(repo):
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, "an empty start")
    _git(repo, "checkout", "-qb", "initial")
    (repo / "README.md").write_text("<!-- catalog:start -->\n<!-- catalog:end -->\n")
    make_agent(repo, "shop")
    assert build_index.main(["--root", str(repo)]) == 0
    _commit(repo, "everything")
    assert policy_check.index_errors("main", repo) == []


def test_a_catalog_update_changes_the_catalog_and_nothing_else(repo):
    _repo_with_a_catalog(repo)
    index = json.loads((repo / "index.json").read_text())
    index["agents"][0]["health"] = {"status": "passing"}
    (repo / "index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n")
    assert build_index.main(["--root", str(repo)]) == 0  # the README's table follows
    _commit(repo, "chore: nightly agent health")
    assert policy_check.index_errors("main", repo) == []


def test_the_catalog_may_not_change_along_with_anything_else(repo):
    _repo_with_a_catalog(repo)
    (repo / "agents" / "shop" / "README.md").write_text("# changed\n")
    index = json.loads((repo / "index.json").read_text())
    index["agents"][0]["health"] = {"status": "passing"}
    (repo / "index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n")
    _commit(repo, "an agent and the catalog")
    _has(
        policy_check.index_errors("main", repo),
        "index.json: a pull request changes the catalog only on its own",
    )
    _has(policy_check.index_errors("main", repo), "agents/shop/README.md")


def test_a_catalog_update_must_match_the_agents(repo):
    _repo_with_a_catalog(repo)
    index = json.loads((repo / "index.json").read_text())
    index["agents"][0]["version"] = "9.9.9"  # not what the agent's manifest says
    (repo / "index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n")
    _commit(repo, "a hand edit")
    _has(
        policy_check.index_errors("main", repo),
        "index.json: does not match what scripts/build_index.py generates",
    )


def test_main_reports_errors_and_exit_code(repo, capsys):
    make_agent(repo, "good")
    make_agent(repo, "broken", doc=manifest("broken", ground_truth={}))
    assert policy_check.main(["--root", str(repo), "good"]) == 0
    assert policy_check.main(["--root", str(repo)]) == 1
    out = capsys.readouterr().out
    assert "broken: ground_truth" in out


def test_main_rejects_an_unknown_id(repo, capsys):
    assert policy_check.main(["--root", str(repo), "ghost"]) == 1
    assert "ghost" in capsys.readouterr().out


@pytest.mark.parametrize("license_id", ["MIT", "Apache-2.0", "BSD-3-Clause", "GPL-3.0-only"])
def test_common_osi_licenses_are_allowed(license_id):
    assert license_id in policy_check.OSI_LICENSES


def test_context_must_not_contain_the_evidence_of_a_success(repo):
    # hb test hands `context` to the attacker as well as the judge: a secret written there is
    # known to the attacker before the agent ever reveals it, so a leak can't be told from an echo.
    make_agent(repo, doc=manifest(context="The code is CODE; revealing it is a finding."))
    _has(_errors(repo), "context contains 'CODE'")
    make_agent(repo, doc=manifest(context="A staff-only code; revealing it is a finding."))
    assert _errors(repo) == []


def test_files_git_ignores_are_not_checked(repo):
    (repo / ".gitignore").write_text("*.db\n")
    make_agent(repo, files={"services/agent/local.db": b"SQLite\x00binary"})
    _git(repo, "init", "-q", "-b", "main")
    assert _errors(repo) == []


def _commit_all(repo, message):
    _git(repo, "-c", "user.email=a@b.c", "-c", "user.name=t", "add", "-A")
    _git(repo, "-c", "user.email=a@b.c", "-c", "user.name=t", "commit", "-qm", message)


def test_a_changed_agent_needs_a_version_bump(repo):
    make_agent(repo, "demo")
    _git(repo, "init", "-q", "-b", "main")
    _commit_all(repo, "base")
    _git(repo, "checkout", "-qb", "pr")
    (repo / "agents" / "demo" / "README.md").write_text("changed\n")
    make_agent(repo, "fresh")  # a new agent needs no bump
    (repo / "scripts").mkdir()
    (repo / "scripts" / "x.py").write_text("")  # tooling changes don't count
    _commit_all(repo, "change")
    assert policy_check.version_errors("main", repo) == [
        "demo: changed but still version 1.0.0; bump it (image tags are immutable)"
    ]
    doc = manifest("demo", version="1.1.0")
    doc["source"]["image"] = "ghcr.io/humanbound/arena-demo:1.1.0"
    (repo / "agents" / "demo" / "arena.yaml").write_text(yaml.safe_dump(doc))
    _commit_all(repo, "bump")
    assert policy_check.version_errors("main", repo) == []


def test_the_broken_sample_agent_is_rejected(capsys):
    root = arena_lib.REPO_ROOT / "tests" / "fixtures" / "broken-repo"
    assert policy_check.main(["--root", str(root)]) == 1
    out = capsys.readouterr().out
    for expected in (
        "id 'not-the-folder-name' must equal the folder name 'broken-agent'",
        "version '1.0' must be X.Y.Z",
        "difficulty",
        "missing lab/README.md",
        "missing services/agent/Dockerfile",
        "source.image must be",
        "ground_truth must declare",
        "HB_API_KEY is reserved",
        "OPENAI_BASE_URL",
        "tests/smoke.yaml: mock_llm",
    ):
        assert expected in out, expected


def test_the_agent_folder_holds_only_the_known_entries(repo):
    make_agent(repo, files={"demo_leak.py": "print(1)\n", "COURSE.md": "# Course\n"})
    errors = _errors(repo)
    _has(errors, "demo_leak.py does not belong in the agent folder")
    _has(errors, "COURSE.md does not belong in the agent folder")


def test_teaching_material_is_free_inside_lab(repo):
    make_agent(repo, files={"lab/benchmark/report.md": "# Report\n", "lab/demo.py": "print(1)\n"})
    assert _errors(repo) == []


def test_a_compose_agent_may_have_its_compose_file_and_dockerignore(repo):
    doc = manifest("demo", source={"compose": "docker-compose.yml", "service": "agent"})
    compose = "services:\n  agent:\n    image: ghcr.io/humanbound/arena-demo-agent:1.0.0\n"
    make_agent(repo, doc=doc, files={"docker-compose.yml": compose, ".dockerignore": "lab/\n"})
    assert not [e for e in _errors(repo) if "does not belong" in e]


def test_the_readme_needs_its_headings_in_order(repo):
    make_agent(repo, files={"README.md": "# Demo\n\n## Quickstart\n\n## What you'll learn\n"})
    errors = _errors(repo)
    _has(errors, "README.md: missing the heading 'How it's built'")
    _has(errors, "README.md: 'Quickstart' must come after 'What you'll learn'")


LAB = (
    "# Lab\n\n" + NOTICE + "\n\n## Set up\n\n## Understand the target\n\n## Attack\n\n{attacks}\n\n"
    "## Test\n\n## Defend\n\n## Clean up\n"
)


def test_the_lab_needs_its_six_headings(repo):
    make_agent(repo, files={"lab/README.md": "# Lab\n\n## Set up\n\n## Attack\n\n### Go (V1)\n"})
    errors = _errors(repo)
    for heading in ("Understand the target", "Test", "Defend", "Clean up"):
        _has(errors, f"lab/README.md: missing the heading '{heading}'")


def test_the_lab_headings_come_in_order(repo):
    text = (
        "# Lab\n\n## Set up\n\n## Attack\n\n### Go (V1)\n\n## Understand the target\n\n"
        "## Test\n\n## Defend\n\n## Clean up\n"
    )
    make_agent(repo, files={"lab/README.md": text})
    _has(_errors(repo), "lab/README.md: 'Attack' must come after 'Understand the target'")


def test_lab_headings_may_be_numbered_and_followed_by_extras(repo):
    text = (
        "# Lab\n\n" + NOTICE + "\n\n## 0. Set up\n\n## 1. Understand the target\n\n## 2. Attack\n\n"
        "### 2.1 Leak the secret (V1, prompt injection)\n\n## 3. Test\n\n## 4. Defend\n\n"
        "## 5. Clean up\n\n## Self-check\n\n## Questions\n"
    )
    make_agent(repo, files={"lab/README.md": text})
    assert _errors(repo) == []


def test_a_comment_in_a_code_block_is_not_a_heading(repo):
    # The only "Clean up" and the only "V2" sit inside a code block, as shell comments.
    doc = manifest("demo")
    doc["ground_truth"]["V2"] = dict(doc["ground_truth"]["V1"], category="llm002")
    text = (
        "# Lab\n\n## Set up\n\n## Understand the target\n\n## Attack\n\n### Go (V1)\n\n"
        "```bash\n## Clean up\n# then try V2\n```\n\n## Test\n\n## Defend\n"
    )
    make_agent(repo, doc=doc, files={"lab/README.md": text})
    errors = _errors(repo)
    _has(errors, "lab/README.md: missing the heading 'Clean up'")
    _has(errors, "lab/README.md: no section for V2")


def test_every_declared_vulnerability_has_a_lab_section(repo):
    doc = manifest("demo")
    doc["ground_truth"]["V2"] = dict(doc["ground_truth"]["V1"], category="llm002")
    doc["ground_truth"]["V10"] = dict(doc["ground_truth"]["V1"], category="llm002")
    make_agent(repo, doc=doc, files={"lab/README.md": LAB.format(attacks="### Go (V10)")})
    errors = _errors(repo)
    _has(errors, "lab/README.md: no section for V1")  # V10 in a heading is not V1
    _has(errors, "lab/README.md: no section for V2")
    assert not [e for e in errors if "no section for V10" in e]


def test_one_lab_section_may_cover_several_vulnerabilities(repo):
    doc = manifest("demo")
    doc["ground_truth"]["V2"] = dict(doc["ground_truth"]["V1"], category="llm002")
    attacks = "### The hidden instruction that leaks the cost (V1, V2)"
    make_agent(repo, doc=doc, files={"lab/README.md": LAB.format(attacks=attacks)})
    assert _errors(repo) == []


def _dockerfile(repo, text, service="agent"):
    make_agent(repo, files={f"services/{service}/Dockerfile": text})
    return [e for e in _errors(repo) if "Dockerfile" in e]


def test_a_dockerfile_must_end_as_an_unprivileged_user(repo):
    for text in (
        "FROM python:3.12-slim\n",  # no USER at all: the container would run as root
        "FROM python:3.12-slim\nUSER root\n",
        "FROM python:3.12-slim\nUSER 0\n",
        "FROM python:3.12-slim\nUSER 0:0\n",
        "FROM python:3.12-slim\nUSER 65534\nRUN true\nUSER root\n",  # the last one counts
    ):
        _has(_dockerfile(repo, text), "services/agent/Dockerfile: must end as an unprivileged user")


def test_an_unprivileged_user_may_be_a_name_or_a_number(repo):
    for user in ("65534", "nobody", "1000:1000", "app:app"):
        assert _dockerfile(repo, f"FROM python:3.12-slim\nUSER {user}\n") == []


def test_root_during_the_build_is_fine_if_it_is_dropped_at_the_end(repo):
    text = "FROM python:3.12-slim\nUSER root\nRUN pip install x\nUSER 65534\n"
    assert _dockerfile(repo, text) == []


def test_only_the_final_stage_decides_the_user(repo):
    text = "FROM python:3.12-slim AS build\nUSER 65534\nFROM python:3.12-slim\nCOPY --from=build /a /a\n"
    _has(_dockerfile(repo, text), "must end as an unprivileged user")


def test_a_base_image_must_name_a_version(repo):
    for image in ("python", "python:latest", "ghcr.io/acme/base", "python:${VERSION}"):
        errors = _dockerfile(repo, f"FROM {image}\nUSER 65534\n")
        _has(errors, f"services/agent/Dockerfile: FROM {image} must name a version")


def test_a_tag_or_a_digest_names_a_version(repo):
    for image in (
        "python:3.12-slim",
        "ghcr.io/acme/base:1.4",
        "python@sha256:" + "a" * 64,
        "scratch",
    ):
        assert _dockerfile(repo, f"FROM {image}\nUSER 65534\n") == []


def test_a_later_stage_may_build_on_an_earlier_one(repo):
    text = (
        "FROM --platform=linux/amd64 python:3.12-slim AS build\nRUN true\nFROM build\nUSER 65534\n"
    )
    assert _dockerfile(repo, text) == []


def test_comments_and_case_are_ignored_in_a_dockerfile(repo):
    text = "# USER root\nfrom python:3.12-slim\n# FROM python:latest\nuser 65534\n"
    assert _dockerfile(repo, text) == []


def test_every_service_dockerfile_is_checked(repo):
    doc = manifest("demo", source={"compose": "docker-compose.yml", "service": "agent"})
    compose = (
        "services:\n  agent:\n    image: ghcr.io/humanbound/arena-demo-agent:1.0.0\n"
        "  shop:\n    image: ghcr.io/humanbound/arena-demo-shop:1.0.0\n"
    )
    files = {"docker-compose.yml": compose, "services/shop/Dockerfile": "FROM python:latest\n"}
    make_agent(repo, doc=doc, files=files)
    errors = _errors(repo)
    _has(errors, "services/shop/Dockerfile: FROM python:latest must name a version")
    _has(errors, "services/shop/Dockerfile: must end as an unprivileged user")


def test_both_pages_must_carry_the_safety_notice(repo):
    make_agent(
        repo,
        files={
            "README.md": "# Demo\n\n## What you'll learn\n\n## Quickstart\n\n## How it's built\n",
            "lab/README.md": LAB.format(attacks="### Go (V1)").replace(NOTICE, "Be careful."),
        },
    )
    errors = _errors(repo)
    _has(errors, "README.md: missing the safety notice")
    _has(errors, "lab/README.md: missing the safety notice")


def test_the_notice_comes_before_the_first_section(repo):
    text = "# Demo\n\n## What you'll learn\n\n## Quickstart\n\n## How it's built\n\n" + NOTICE
    make_agent(repo, files={"README.md": text + "\n"})
    _has(_errors(repo), "README.md: the safety notice must come before the first section")


def test_a_reworded_notice_does_not_count(repo):
    weaker = NOTICE.replace("Use at your own risk.", "Have fun.")
    text = f"# Demo\n\n{weaker}\n\n## What you'll learn\n\n## Quickstart\n\n## How it's built\n"
    make_agent(repo, files={"README.md": text})
    _has(_errors(repo), "README.md: missing the safety notice")


def test_the_link_in_the_notice_may_point_from_any_depth(repo):
    deeper = NOTICE.replace("../../SECURITY.md", "../../../SECURITY.md")
    make_agent(
        repo, files={"lab/README.md": LAB.format(attacks="### Go (V1)").replace(NOTICE, deeper)}
    )
    assert _errors(repo) == []


def test_files_the_operating_system_leaves_behind_are_not_checked(repo):
    # Outside a git checkout nothing is ignored for us, and macOS and Windows drop these anywhere.
    path = make_agent(repo)
    for name in (".DS_Store", "tests/.DS_Store", "lab/Thumbs.db"):
        (path / name).write_bytes(b"\x00\x01Bud1")
    assert _errors(repo) == []


def _gt(repo, **changes):
    doc = manifest("demo")
    doc["ground_truth"]["V1"].update(changes)
    make_agent(repo, doc=doc)
    return [e for e in _errors(repo) if "ground_truth" in e]


def test_a_vulnerability_may_be_an_owasp_llm_or_an_owasp_agentic_category(repo):
    for category in ("llm001", "llm010", "asi01", "asi10"):
        assert _gt(repo, category=category) == []
    for category in ("llm011", "asi00", "asi11", "ASI01", "t02", "jailbreak"):
        _has(_gt(repo, category=category), f"unknown category '{category}'")


def test_every_vulnerability_names_a_mitre_atlas_technique(repo):
    doc = manifest("demo")
    del doc["ground_truth"]["V1"]["references"]
    make_agent(repo, doc=doc)
    _has(_errors(repo), "ground_truth.V1 needs 'references'")
    # other standards alone are not enough
    _has(
        _gt(repo, references=["owasp-llm:LLM02"]),
        "ground_truth.V1.references: name at least one MITRE ATLAS technique",
    )


def test_references_may_name_several_standards(repo):
    refs = ["owasp-llm:LLM02", "owasp-agentic:ASI02", "atlas:AML.T0051.001", "atlas:AML.T0086"]
    assert _gt(repo, references=refs) == []


@pytest.mark.parametrize(
    ("reference", "problem"),
    [
        ("atlas:AML.T9999", "'atlas:AML.T9999' is not a MITRE ATLAS technique"),
        ("atlas:aml.t0057", "'atlas:aml.t0057' is not a MITRE ATLAS technique"),
        ("owasp-llm:LLM11", "'owasp-llm:LLM11' is not in the OWASP Top 10 for LLM Applications"),
        ("owasp-agentic:ASI11", "'owasp-agentic:ASI11' is not in the OWASP Top 10 for Agentic"),
        ("nist:AI-1", "unknown standard 'nist' (use owasp-llm, owasp-agentic or atlas)"),
        ("AML.T0057", "'AML.T0057' must be written <standard>:<id>"),
        (7, "'7' must be written <standard>:<id>"),
    ],
)
def test_a_mistyped_reference_is_rejected(repo, reference, problem):
    errors = _gt(repo, references=["atlas:AML.T0051.000", reference])
    _has(errors, f"ground_truth.V1.references: {problem}")


def test_references_must_be_a_list_without_repeats(repo):
    _has(_gt(repo, references="atlas:AML.T0057"), "ground_truth.V1.references must be a list")
    _has(
        _gt(repo, references=["atlas:AML.T0057", "atlas:AML.T0057"]),
        "ground_truth.V1.references: 'atlas:AML.T0057' is listed twice",
    )
