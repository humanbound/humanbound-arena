# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Humanbound
"""Check agents against the admission rules that can be automated (docs/admission-policy.md).

    python scripts/policy_check.py                         # every agent
    python scripts/policy_check.py hello-world             # some agents
    python scripts/policy_check.py --base-ref origin/main  # also: index.json untouched, versions bumped

`hb arena validate` checks the arena.yaml schema; this checks the repository's own rules.
Exit code 1 when anything fails; every problem is printed as "<id>: <problem>".
"""

from __future__ import annotations

import argparse
import contextlib
import io
import re
import subprocess
import sys
from pathlib import Path

import standards
import yaml

import arena_lib
import build_index
from arena_lib import (
    AGENT_SERVICE,
    COMPOSE_FILE,
    MAX_FILE_BYTES,
    OUR_IMAGE_PREFIX,
    SMOKE_FILE,
    Agent,
)

OSI_LICENSES = frozenset(
    {
        "0BSD", "AFL-3.0", "AGPL-3.0-only", "AGPL-3.0-or-later", "Apache-2.0", "Artistic-2.0",
        "BSD-2-Clause", "BSD-3-Clause", "BSL-1.0", "CDDL-1.0", "EPL-2.0", "EUPL-1.2",
        "GPL-2.0-only", "GPL-2.0-or-later", "GPL-3.0-only", "GPL-3.0-or-later", "ISC",
        "LGPL-2.1-only", "LGPL-2.1-or-later", "LGPL-3.0-only", "LGPL-3.0-or-later", "MIT",
        "MIT-0", "MPL-2.0", "MS-PL", "NCSA", "OSL-3.0", "PostgreSQL", "Python-2.0", "UPL-1.0",
        "Unlicense", "Zlib",
    }
)  # fmt: skip
# A vulnerability's category is its main entry in an OWASP Top 10: for LLM Applications 2025
# (llm001 prompt injection .. llm010) or for Agentic Applications (asi01 goal hijack .. asi10).
CATEGORIES = frozenset(f"llm{n:03d}" for n in range(1, 11)) | frozenset(
    f"asi{n:02d}" for n in range(1, 11)
)
STANDARD_NAMES = {
    "owasp-llm": "is not in the OWASP Top 10 for LLM Applications",
    "owasp-agentic": "is not in the OWASP Top 10 for Agentic Applications",
    "atlas": f"is not a MITRE ATLAS technique (version {standards.ATLAS_VERSION})",
}
SEVERITIES = ("low", "medium", "high", "critical")
VECTORS = ("direct", "indirect", "tool-output", "memory", "multi-agent")
DIFFICULTIES = ("easy", "medium", "hard")
IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico"})
AGENT_DOCKERFILE = f"services/{AGENT_SERVICE}/Dockerfile"
LAB_FILE = "lab/README.md"
REQUIRED_FILES = ["arena.yaml", "README.md", LAB_FILE, AGENT_DOCKERFILE, SMOKE_FILE]
# The agent folder holds the deployment and lab/, and nothing else.
AGENT_FOLDER = frozenset(
    {
        "arena.yaml",
        "README.md",
        ".dockerignore",
        COMPOSE_FILE,
        "DEPRECATED",
        "services",
        "tests",
        "lab",
    }
)
# The headings every page carries, in this order. Pages may number them and add their own.
# Every agent is built to be tricked, so both pages open with the same warning, word for word.
# The link after it may point from any depth.
SAFETY_NOTICE = (
    "**Deliberately vulnerable. Use at your own risk.** We isolate every agent as well as we "
    "can, but no isolation is complete. Run it only where there is no sensitive data and no "
    "access to critical systems."
)
README_HEADINGS = ("What you'll learn", "Quickstart", "How it's built")
LAB_HEADINGS = ("Set up", "Understand the target", "Attack", "Test", "Defend", "Clean up")
GROUND_TRUTH_FIELDS = ("category", "title", "description", "severity", "vector", "success")
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
SHA = re.compile(r"^[0-9a-f]{40}$")
RESERVED_ENV = re.compile(r"^(HB_|HUMANBOUND_)", re.IGNORECASE)
SKIP_DIRS = frozenset({".git", "__pycache__", ".pytest_cache", ".venv", "node_modules"})
SKIP_FILES = frozenset({".DS_Store", "Thumbs.db"})  # left behind by macOS and Windows


def _files(agent: Agent) -> list[Path]:
    """The agent's files that git would commit: tracked or untracked, never ignored ones (local
    databases, caches). Outside a git checkout, every file."""
    proc = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", "."],
        cwd=agent.path,
        capture_output=True,
        text=True,
    )
    if proc.returncode == 0:
        paths = (agent.path / rel for rel in proc.stdout.split("\0") if rel)
        return sorted(p for p in paths if p.is_file() and p.name not in SKIP_FILES)
    return sorted(
        p
        for p in agent.path.rglob("*")
        if p.is_file()
        and p.name not in SKIP_FILES
        and not SKIP_DIRS.intersection(p.relative_to(agent.path).parts)
    )


def _is_binary(path: Path) -> bool:
    with path.open("rb") as f:
        return b"\0" in f.read(8192)


def check_files(agent: Agent) -> list[str]:
    errors = []
    for rel in REQUIRED_FILES:
        if not (agent.path / rel).is_file():
            errors.append(f"missing {rel}")
    source = agent.manifest.get("source") or {}
    services = sorted(p.parent.name for p in agent.path.glob("services/*/Dockerfile"))
    if source.get("image"):
        if (agent.path / COMPOSE_FILE).exists():
            errors.append(f"{COMPOSE_FILE} is only for compose agents (source.compose)")
        if len(services) > 1:
            errors.append(f"several services ({', '.join(services)}) need a {COMPOSE_FILE}")
    if source.get("compose") and source.get("service") != AGENT_SERVICE:
        errors.append(
            f"source.service must be '{AGENT_SERVICE}' (the service in services/{AGENT_SERVICE}/)"
        )
    strays = set()
    for path in _files(agent):
        rel = path.relative_to(agent.path).as_posix()
        top, _, rest = rel.partition("/")
        if top not in AGENT_FOLDER:
            strays.add(f"{top}/" if rest else top)
        if path.name == ".env" or (path.name.startswith(".env.") and path.suffix != ".example"):
            errors.append(f"{rel}: .env files are not allowed (keys never belong in the repo)")
        if path.stat().st_size > MAX_FILE_BYTES:
            errors.append(f"{rel} is larger than 1 MB (put bulk data in a GitHub Release)")
        elif path.suffix.lower() not in IMAGE_EXTS and _is_binary(path):
            errors.append(f"{rel} is a binary file (only text and web images are allowed)")
    errors += [
        f"{name} does not belong in the agent folder (teaching material goes in lab/)"
        for name in sorted(strays)
    ]
    return errors


def _headings(text: str) -> list[tuple[int, str]]:
    """(level, title) of each Markdown heading, numbering removed; code blocks are skipped."""
    found, fenced = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
        elif not fenced and (m := re.match(r"(#{1,6})\s+(?:[\d.]+\s+)?(.+?)\s*$", line)):
            found.append((len(m.group(1)), m.group(2)))
    return found


def _outline_errors(name: str, headings: list[tuple[int, str]], required: tuple) -> list[str]:
    titles = [title.casefold() for level, title in headings if level == 2]
    errors = [f"{name}: missing the heading '{h}'" for h in required if h.casefold() not in titles]
    present = [h for h in required if h.casefold() in titles]
    for before, after in zip(present, present[1:], strict=False):
        if titles.index(after.casefold()) < titles.index(before.casefold()):
            errors.append(f"{name}: '{after}' must come after '{before}'")
    return errors


def _notice_errors(name: str, text: str) -> list[str]:
    at = " ".join(text.split()).find(SAFETY_NOTICE)
    if at < 0:
        return [f"{name}: missing the safety notice (copy it from agents/hello-world/{name})"]
    first_section = re.search(r"^## ", text, re.MULTILINE)
    if first_section and text.find(SAFETY_NOTICE.split(".")[0]) > first_section.start():
        return [f"{name}: the safety notice must come before the first section"]
    return []


def check_pages(agent: Agent) -> list[str]:
    """README.md and the lab carry the same headings in every agent, and the lab has a
    section for each vulnerability the agent declares."""
    errors = []
    for name, required in (("README.md", README_HEADINGS), (LAB_FILE, LAB_HEADINGS)):
        path = agent.path / name
        if not path.is_file():
            continue  # reported by check_files
        text = path.read_text(encoding="utf-8", errors="replace")
        headings = _headings(text)
        errors += _outline_errors(name, headings, required)
        errors += _notice_errors(name, text)
        if name != LAB_FILE:
            continue
        titles = " \n".join(title for _, title in headings)
        for key in agent.manifest.get("ground_truth") or {}:
            if not re.search(rf"(?<![\w-]){re.escape(str(key))}(?![\w-])", titles):
                errors.append(f"{name}: no section for {key} (name it in a heading)")
    return errors


def check_identity(agent: Agent) -> list[str]:
    m = agent.manifest
    errors = []
    if m.get("id") != agent.id:
        errors.append(f"id '{m.get('id')}' must equal the folder name '{agent.id}'")
    if not SEMVER.match(str(m.get("version", ""))):
        errors.append(f"version '{m.get('version')}' must be X.Y.Z")
    if m.get("difficulty") not in DIFFICULTIES:
        errors.append(f"difficulty must be one of {', '.join(DIFFICULTIES)}")
    return errors


def check_developer(agent: Agent) -> list[str]:
    """Who wrote the agent. Descriptive credit, and somewhere to send a bug for code we
    didn't write; no tooling branches on it."""
    block = agent.manifest.get("developer")
    if not isinstance(block, dict):
        return ["developer {name, url} is required"]
    errors = []
    if not str(block.get("name", "")).strip():
        errors.append("developer.name is required")
    url = str(block.get("url", ""))
    if not url:
        errors.append("developer.url is required")
    elif not url.startswith(("http://", "https://")):
        errors.append(f"developer.url '{url}' must be an http(s) URL")
    return errors


SPDX_OPERATORS = re.compile(r"\s+(?:OR|AND)\s+")
SPDX_EXCEPTION = re.compile(r"\s+WITH\s+\S+\s*$")


def _license_operands(license_id: str) -> list[str]:
    """The licence ids in an SPDX id or expression.

    "MIT OR Apache-2.0" gives ["MIT", "Apache-2.0"]. A `WITH` clause names an exception
    rather than a licence ("Apache-2.0 WITH LLVM-exception" gives ["Apache-2.0"]), so only
    the licence it applies to is checked against the allowlist. A legacy `+` suffix ("GPL-2.0+")
    is kept, so a licence that isn't on the allowlist is reported exactly as it was written.
    """
    parts = SPDX_OPERATORS.split(license_id.strip())
    return [p for p in (SPDX_EXCEPTION.sub("", part).strip(" ()") for part in parts) if p]


def check_upstream(agent: Agent) -> list[str]:
    """An agent may be built from third-party code fetched at build time. When it declares
    that, the pin must be exact and the licence must let us redistribute."""
    upstream = (agent.manifest.get("source") or {}).get("upstream")
    if upstream is None:
        return []
    if not isinstance(upstream, dict):
        return ["source.upstream must be a mapping {repo, ref, license}"]
    errors = []
    ref = str(upstream.get("ref", ""))
    if not SHA.match(ref):
        errors.append("source.upstream.ref must be a full 40-character commit SHA")
    dockerfiles = sorted(agent.path.glob("services/*/Dockerfile"))
    if SHA.match(ref) and not any(
        p.is_file() and ref in p.read_text(errors="replace") for p in dockerfiles
    ):
        errors.append("the Dockerfile must check out source.upstream.ref (the same full SHA)")
    license_id = upstream.get("license")
    if not license_id:
        errors.append("source.upstream.license (an SPDX id) is required")
    else:
        unknown = [p for p in _license_operands(str(license_id)) if p not in OSI_LICENSES]
        if unknown:
            errors.append(
                f"source.upstream.license '{license_id}' is not on the OSI allowlist "
                f"({', '.join(unknown)})"
            )
    return errors


def _pinned(image: str) -> bool:
    if "$" in image:
        return False  # a variable: what it names is decided elsewhere
    if "@sha256:" in image:
        return True
    name, _, tag = image.rpartition(":")
    return bool(name) and "/" not in tag and tag != "latest"


def _instructions(text: str) -> list[tuple[str, str]]:
    """(INSTRUCTION, arguments) for each line of a Dockerfile, comments left out."""
    found = []
    for line in text.splitlines():
        word, _, rest = line.strip().partition(" ")
        if word and not word.startswith("#"):
            found.append((word.upper(), rest.strip()))
    return found


def check_dockerfiles(agent: Agent) -> list[str]:
    """hb drops privileges and limits resources when it starts a container, but two things are
    the image's own: the user it runs as, and what it was built on."""
    errors = []
    for path in sorted(agent.path.glob("services/*/Dockerfile")):
        rel = path.relative_to(agent.path).as_posix()
        stages, user = set(), None
        for word, args in _instructions(path.read_text(encoding="utf-8", errors="replace")):
            if word == "FROM":
                parts = [a for a in args.split() if not a.startswith("--")]
                image = parts[0] if parts else ""
                # A later stage may build on an earlier one, named with AS; scratch is empty.
                if image not in stages and image != "scratch" and not _pinned(image):
                    errors.append(
                        f"{rel}: FROM {image} must name a version "
                        "(a tag other than latest, or a digest)"
                    )
                if len(parts) == 3 and parts[1].upper() == "AS":
                    stages.add(parts[2])
                user = None  # each stage starts again as root
            elif word == "USER":
                user = args.split(":")[0]
        if user in (None, "", "root", "0"):
            errors.append(
                f"{rel}: must end as an unprivileged user "
                "(the last USER of the final stage, and not root)"
            )
    return errors


def check_images(agent: Agent) -> list[str]:
    source = agent.manifest.get("source") or {}
    version = agent.version
    if source.get("image"):
        expected = arena_lib.expected_image(agent.id, version)
        if source["image"] != expected:
            return [f"source.image must be {expected}"]
        return []
    compose = source.get("compose")
    if not compose:
        return []
    try:
        doc = arena_lib.load_compose(agent)
    except (OSError, yaml.YAMLError) as e:
        return [f"cannot read {compose}: {e}"]
    errors = []
    for name, service in (doc.get("services") or {}).items():
        service = service if isinstance(service, dict) else {}
        where = f"{compose} service '{name}'"
        if "build" in service:
            errors.append(
                f"{where}: use a pre-built image, not 'build' (hb fetches only {compose})"
            )
        image = service.get("image")
        if not isinstance(image, str):
            errors.append(f"{where} needs an 'image'")
        elif image.startswith(OUR_IMAGE_PREFIX):
            expected = arena_lib.expected_image(agent.id, version, str(name))
            if image != expected:
                errors.append(f"{where}: image must be {expected}")
        elif not _pinned(image):
            errors.append(f"{where}: third-party image '{image}' must be pinned to a tag or digest")
    return errors


def _reference_errors(references: object) -> list[str]:
    """Problems with a vulnerability's references to published standards. Each is written
    <standard>:<id>, and at least one names a MITRE ATLAS technique."""
    if references in (None, "", []):
        return [" needs 'references' with at least one MITRE ATLAS technique (atlas:AML.T…)"]
    if not isinstance(references, list):
        return [".references must be a list"]
    errors, seen = [], set()
    for ref in references:
        standard, colon, ident = str(ref).partition(":")
        if not isinstance(ref, str) or not colon or not standard or not ident:
            errors.append(f"'{ref}' must be written <standard>:<id>")
        elif standard not in standards.STANDARDS:
            known = (
                ", ".join(list(standards.STANDARDS)[:-1]) + f" or {list(standards.STANDARDS)[-1]}"
            )
            errors.append(f"unknown standard '{standard}' (use {known})")
        elif ident not in standards.STANDARDS[standard]:
            errors.append(f"'{ref}' {STANDARD_NAMES[standard]}")
        elif ref in seen:
            errors.append(f"'{ref}' is listed twice")
        seen.add(ref)
    if not errors and not any(str(r).startswith("atlas:") for r in references):
        errors.append("name at least one MITRE ATLAS technique (atlas:AML.T…)")
    return [f".references: {e}" for e in errors]


def check_ground_truth(agent: Agent) -> list[str]:
    ground_truth = agent.manifest.get("ground_truth")
    if not isinstance(ground_truth, dict) or not ground_truth:
        return ["ground_truth must declare every planted vulnerability (it is empty)"]
    errors = []
    for key, entry in ground_truth.items():
        where = f"ground_truth.{key}"
        if not isinstance(entry, dict):
            errors.append(f"{where} must be a mapping")
            continue
        for field in GROUND_TRUTH_FIELDS:
            if entry.get(field) in (None, "", []):
                errors.append(f"{where} needs '{field}'")
        if "category" in entry and entry["category"] not in CATEGORIES:
            errors.append(
                f"{where}: unknown category '{entry['category']}' (llm001–llm010, asi01–asi10)"
            )
        errors += [f"{where}{e}" for e in _reference_errors(entry.get("references"))]
        if "severity" in entry and entry["severity"] not in SEVERITIES:
            errors.append(f"{where}: severity must be one of {', '.join(SEVERITIES)}")
        if "vector" in entry and entry["vector"] not in VECTORS:
            errors.append(f"{where}: vector must be one of {', '.join(VECTORS)}")
        if entry.get("success") not in (None, []):
            errors += [f"{where}.{e}" for e in arena_lib.success_errors(entry["success"])]
    return errors


def check_context(agent: Agent) -> list[str]:
    """`context` goes to hb test's attacker as well as its judge, so it must describe secrets,
    never contain them: an attacker that already knows a secret makes its leak untestable."""
    context = str(agent.manifest.get("context") or "")
    errors = []
    for key, entry in (agent.manifest.get("ground_truth") or {}).items():
        for cond in (entry or {}).get("success") or [] if isinstance(entry, dict) else []:
            value = cond.get("reply_contains") if isinstance(cond, dict) else None
            if isinstance(value, str) and value and value in context:  # planted secrets are exact
                errors.append(
                    f"context contains {value!r} (ground_truth.{key} evidence); hb test gives "
                    "context to the attacker, so describe the secret instead of quoting it"
                )
    return errors


def check_env(agent: Agent) -> list[str]:
    env = (agent.manifest.get("runtime") or {}).get("env") or {}
    names = [*(env.get("required") or []), *(env.get("optional") or [])]
    errors = [
        f"runtime.env: {n} is reserved for hb's own credentials"
        for n in names
        if RESERVED_ENV.match(str(n))
    ]
    if "OPENAI_BASE_URL" not in names:
        errors.append("runtime.env must declare OPENAI_BASE_URL (every agent honours it)")
    return errors


def check_smoke(agent: Agent) -> list[str]:
    path = agent.path / SMOKE_FILE
    if not path.is_file():
        return []  # reported by check_files
    try:
        doc = arena_lib.load_yaml(path)
    except (OSError, yaml.YAMLError) as e:
        return [f"{SMOKE_FILE}: cannot parse: {e}"]
    return [f"{SMOKE_FILE}: {e}" for e in arena_lib.smoke_errors(doc)]


def check_agent(agent: Agent) -> list[str]:
    """Every problem with one agent (empty when it passes)."""
    return [
        *check_identity(agent),
        *check_developer(agent),
        *check_files(agent),
        *check_pages(agent),
        *check_upstream(agent),
        *check_dockerfiles(agent),
        *check_images(agent),
        *check_ground_truth(agent),
        *check_context(agent),
        *check_env(agent),
        *check_smoke(agent),
    ]


def check_unknown_dirs(root: Path) -> list[str]:
    errors = []
    base = Path(root) / arena_lib.AGENTS_DIR
    for path in sorted(base.iterdir()) if base.is_dir() else []:
        if path.is_dir() and not (path / "arena.yaml").exists():
            errors.append(
                f"{arena_lib.AGENTS_DIR}/{path.name}: no arena.yaml (every folder here must be an agent)"
            )
    return errors


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)


def index_errors(base_ref: str, root: Path) -> list[str]:
    """A pull request leaves index.json alone, because CI generates it. Two may touch it: the
    first one, which adds it, and a catalog update, which CI opens and which changes nothing
    else. A catalog update must still say what the agents' manifests say."""
    fork = _git(root, "merge-base", base_ref, "HEAD").stdout.strip() or base_ref
    if _git(root, "cat-file", "-e", f"{fork}:index.json").returncode != 0:
        return []  # there was no catalog yet: this pull request introduces it
    changed = set(_git(root, "diff", "--name-only", fork, "HEAD").stdout.split())
    if "index.json" not in changed:
        return []
    others = sorted(changed - {"index.json", "README.md"})
    if others:
        shown = ", ".join(others[:3]) + (f" and {len(others) - 3} more" if len(others) > 3 else "")
        return [
            "index.json: a pull request changes the catalog only on its own, as a catalog "
            f"update that CI opens; this one also changes {shown}"
        ]
    with contextlib.redirect_stdout(io.StringIO()):
        up_to_date = build_index.main(["--root", str(root), "--check"]) == 0
    if not up_to_date:
        return ["index.json: does not match what scripts/build_index.py generates"]
    return []


def version_errors(base_ref: str, root: Path) -> list[str]:
    """Agents whose own files changed since base_ref without a version bump.

    Image tags are immutable, so a changed agent that keeps its version would never be
    republished. New agents, and changes to shared tooling only, need no bump.
    """
    import changed_agents

    paths = subprocess.run(
        ["git", "diff", "--name-only", f"{base_ref}...HEAD"],
        cwd=root, capture_output=True, text=True, check=True,
    ).stdout.split()  # fmt: skip
    agent_paths = [p for p in paths if p.split("/", 1)[0] == arena_lib.AGENTS_DIR]
    errors = []
    for agent_id in changed_agents.changed_ids(agent_paths, root):
        agent = arena_lib.find_agent(agent_id, root)
        before = subprocess.run(
            ["git", "show", f"{base_ref}:{agent.rel_path}/arena.yaml"],
            cwd=root, capture_output=True, text=True,
        )  # fmt: skip
        if before.returncode != 0:
            continue  # a new agent
        old = yaml.safe_load(before.stdout) or {}
        if str(old.get("version")) == agent.version:
            errors.append(
                f"{agent.id}: changed but still version {agent.version}; bump it (image tags are immutable)"
            )
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check agents against the admission policy.")
    parser.add_argument("ids", nargs="*", help="agent ids (default: all)")
    parser.add_argument("--root", type=Path, default=arena_lib.REPO_ROOT)
    parser.add_argument(
        "--base-ref", help="also check against this git ref: index.json untouched, versions bumped"
    )
    args = parser.parse_args(argv)

    problems: list[str] = []
    try:
        agents = arena_lib.discover(args.root)
    except ValueError as e:
        print(e)
        return 1
    by_id = {a.id: a for a in agents}
    for agent_id in args.ids:
        if agent_id not in by_id:
            problems.append(f"{agent_id}: no such agent under {arena_lib.AGENTS_DIR}/")
    selected = [by_id[i] for i in args.ids if i in by_id] if args.ids else agents
    if not args.ids:
        problems += check_unknown_dirs(args.root)
    for agent in selected:
        problems += [f"{agent.id}: {e}" for e in check_agent(agent)]
    if args.base_ref:
        problems += index_errors(args.base_ref, args.root)
        problems += version_errors(args.base_ref, args.root)

    for problem in problems:
        print(problem)
    if problems:
        print(f"\n{len(problems)} problem(s). See docs/admission-policy.md.", file=sys.stderr)
        return 1
    print(f"policy check passed for {len(selected)} agent(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
