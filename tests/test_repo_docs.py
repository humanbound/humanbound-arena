# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Humanbound
"""The repository's own pages: the front page carries the warnings, and no link is broken."""

import re

import pytest
import yaml

import arena_lib
import policy_check

ROOT = arena_lib.REPO_ROOT
README = (ROOT / "README.md").read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    match = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return match.group(1) if match else ""


def test_the_front_page_opens_with_the_safety_notice():
    first_section = README.index("\n## ")
    assert policy_check.SAFETY_NOTICE in " ".join(README[:first_section].split())


def test_the_front_page_disclaims_warranty_and_liability():
    disclaimer = " ".join(_section(README, "Disclaimer").split()).lower()
    assert disclaimer, "README.md needs a '## Disclaimer' section"
    for phrase in (
        "deliberately vulnerable",
        "at your own risk",
        "without warranty",
        "no liability",
    ):
        assert phrase in disclaimer, f"the disclaimer must say '{phrase}'"


def test_the_front_page_says_who_may_contribute():
    section = " ".join(_section(README, "Feedback and contributions").split()).lower()
    assert "pull requests" in section and "issue" in section


def _pages():
    pages = [*ROOT.glob("*.md"), *ROOT.glob("docs/*.md"), *ROOT.glob(".github/*.md")]
    for agent in arena_lib.discover(ROOT):
        if (agent.path / policy_check.LAB_FILE).is_file():  # an agent brought to the structure
            pages += [agent.path / "README.md", *agent.path.glob("lab/**/*.md")]
    return sorted(pages)


def _anchors(path):
    found, fenced = set(), False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
        elif not fenced and (m := re.match(r"#{1,6}\s+(.+?)\s*$", line)):
            title = re.sub(r"[`*]", "", m.group(1)).lower()
            found.add(re.sub(r"\s", "-", re.sub(r"[^\w\s-]", "", title)))
    return found


@pytest.mark.parametrize("page", _pages(), ids=lambda p: str(p.relative_to(ROOT)))
def test_every_link_in_a_page_resolves(page):
    text = re.sub(r"```.*?```", "", page.read_text(encoding="utf-8"), flags=re.S)
    broken = []
    for link in re.findall(r"\]\(([^)\s]+)\)", text):
        if link.startswith(("http://", "https://", "mailto:")):
            continue
        target, _, fragment = link.partition("#")
        dest = (page.parent / target) if target else page
        if not dest.exists():
            broken.append(f"{link} (no such file)")
            continue
        dest = dest / "README.md" if dest.is_dir() else dest
        if fragment and dest.suffix == ".md" and fragment not in _anchors(dest):
            broken.append(f"{link} (no such heading)")
    assert not broken, broken


WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_every_workflow_is_valid_yaml_with_jobs(workflow):
    doc = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    assert isinstance(doc, dict) and doc.get("jobs"), f"{workflow.name} has no jobs"
    for name, job in doc["jobs"].items():
        assert job.get("steps"), f"{workflow.name}: job '{name}' has no steps"


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_no_workflow_pushes_to_main(workflow):
    # main takes changes through pull requests only; CI proposes with scripts/open_catalog_pr.sh.
    for line in workflow.read_text(encoding="utf-8").splitlines():
        assert "git push" not in line, f"{workflow.name} pushes by itself: {line.strip()}"


def test_the_checks_main_requires_can_be_started_on_a_catalog_update():
    # A pull request opened by CI starts no workflow, so both required checks must accept being
    # started by hand, and keep the names that main's protection asks for.
    names = set()
    for name in ("validate.yml", "dco.yml"):
        doc = yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))
        triggers = doc.get("on") or doc.get(True)  # YAML reads a bare `on` as true
        assert "workflow_dispatch" in triggers, f"{name} cannot be started on a catalog update"
        names |= {job.get("name") for job in doc["jobs"].values()}
    assert {"Policy and unit tests", "Signed-off-by check"} <= names
