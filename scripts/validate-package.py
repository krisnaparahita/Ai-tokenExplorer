#!/usr/bin/env python3
"""Check Token Explorer's package files without external dependencies."""

from __future__ import annotations

import json
import py_compile
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SKILL_NAME = "token-audit"


def fail(message: str) -> None:
    raise SystemExit(message)


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        fail(f"Cannot read {path.relative_to(ROOT)}: {error}")


def read_json(path: Path) -> dict:
    try:
        return json.loads(read(path))
    except json.JSONDecodeError as error:
        fail(f"Fix the JSON in {path.relative_to(ROOT)}: {error}")


def need(match, message: str):
    if match is None:
        fail(message)
    return match


SKILL_PATH = ROOT / "SKILL.md"
SKILL = read(SKILL_PATH)
README = read(ROOT / "README.md")
PLUGIN = read_json(ROOT / ".claude-plugin" / "plugin.json")
MARKETPLACE = read_json(ROOT / ".claude-plugin" / "marketplace.json")

# --- SKILL.md metadata -------------------------------------------------------
metadata = need(re.match(r"\A---\n(.*?)\n---\n", SKILL, re.DOTALL), "SKILL.md must begin with YAML metadata").group(1)
for unsupported in ("version:", "compatibility:", "allowed-tools:"):
    if re.search(rf"(?m)^{re.escape(unsupported)}", metadata):
        fail(f"Remove unsupported YAML field: {unsupported[:-1]}")
name = need(re.search(r"(?m)^name:\s*(\S+)\s*$", metadata), "Add name to the SKILL.md metadata").group(1)
if name != SKILL_NAME or not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name) or len(name) > 64:
    fail(f"The skill name must be '{SKILL_NAME}' (lowercase letters, digits, hyphens): found '{name}'")
description = need(re.search(r"(?ms)^description:\s*(.+?)(?=^\S|\Z)", metadata), "Add description to the SKILL.md metadata").group(1)
description = " ".join(description.replace("|", " ").split())
if not description or len(description) > 1024:
    fail(f"Keep the description between 1 and 1024 characters: it is {len(description)}")
need(re.search(r"(?m)^license:\s*\S+", metadata), "Add license to the SKILL.md metadata")
skill_version = need(
    re.search(r'(?m)^\s+version:\s*["\']?([0-9]+\.[0-9]+\.[0-9]+)["\']?\s*$', metadata),
    "Add metadata.version to SKILL.md as a three-part version",
).group(1)

# --- one version everywhere --------------------------------------------------
readme_version = need(re.search(r"(?m)^- \*\*([0-9]+\.[0-9]+\.[0-9]+)\*\*", README), "Add a version entry to README.md").group(1)
versions = {skill_version, readme_version, str(PLUGIN.get("version", ""))}
if len(versions) != 1:
    fail(f"Use one package version in all files: {sorted(versions)}")

# --- package layout ----------------------------------------------------------
skill_files = {p.relative_to(ROOT) for p in ROOT.rglob("SKILL.md") if ".git" not in p.parts}
if SKILL_PATH.is_symlink() or skill_files != {Path("SKILL.md")}:
    fail("Keep one regular SKILL.md at the repo root")
if PLUGIN.get("skills") != ["./"]:
    fail("Point the Claude plugin skill loader at the repo root")
if PLUGIN.get("name") != SKILL_NAME:
    fail(f"Name the Claude plugin '{SKILL_NAME}'")
plugins = MARKETPLACE.get("plugins") or []
if len(plugins) != 1 or plugins[0].get("name") != SKILL_NAME or plugins[0].get("source") != "./":
    fail("List the plugin once in marketplace.json with source './'")
if len(SKILL.splitlines()) > 400:
    fail("Keep SKILL.md at 400 lines or fewer")

openai = read(ROOT / "agents" / "openai.yaml")
for field in ("display_name:", "short_description:", "default_prompt:"):
    if field not in openai:
        fail(f"Add {field[:-1]} to agents/openai.yaml")
if f"${SKILL_NAME}" not in openai:
    fail(f"Mention ${SKILL_NAME} in the agents/openai.yaml default prompt")

for heading in ("## Installation", "## Usage", "## Version history", "## License"):
    if heading not in README:
        fail(f"Add the '{heading}' section to README.md")
if not (ROOT / "LICENSE").is_file():
    fail("Add a LICENSE file")

# --- the skill documents every command the tool has --------------------------
query = read(ROOT / "scripts" / "query.py")
commands = re.findall(r"'([a-z-]+)'", need(re.search(r"add_argument\('command', choices=\[(.*?)\]", query), "Cannot find the query commands").group(1))
for command in commands:
    if f"`{command}" not in SKILL:
        fail(f"Document the '{command}' command in SKILL.md")

# --- docs use repo-root paths (the skill folder is the repo root) --------------
stale = re.compile(r"(?<!skills/)(?<!\.share/)token-audit/(scripts|references|tests)/")
for doc in ("README.md", "SKILL.md", "AGENTS.md", "docs/development.md", "references/adapters.md", "references/operation.md"):
    hit = stale.search(read(ROOT / doc))
    if hit:
        fail(f"Use repo-root paths in {doc}: found {hit.group(0)!r}")

# --- scripts compile ---------------------------------------------------------
for script in sorted((ROOT / "scripts").glob("*.py")):
    try:
        py_compile.compile(str(script), doraise=True, cfile=str(ROOT / "scripts" / "__pycache__" / (script.stem + ".validate.pyc")))
    except py_compile.PyCompileError as error:
        fail(f"Fix the syntax error in {script.relative_to(ROOT)}: {error.msg}")

# --- no price ever ships, and no personal data is committed --------------------
prices = read_json(ROOT / "prices.example.json")
for model in prices.get("models", []):
    if any(isinstance(v, (int, float)) and v != 0 for k, v in model.items() if k.endswith("_per_million")):
        fail("prices.example.json must contain placeholder zeros only; never ship real prices")

try:
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
except (OSError, subprocess.CalledProcessError):
    tracked = [str(p.relative_to(ROOT)) for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.parts and "__pycache__" not in p.parts]
personal = re.compile(r"(/home/[a-z][\w.-]*/|/Users/[A-Za-z][\w.-]*/|xox[bp]-|xapp-|sk-ant-|ghp_[A-Za-z0-9]{20,})")
for relative in tracked:
    path = ROOT / relative
    if path.suffix in {".png", ".jpg", ".gif", ".pyc"} or relative == "scripts/validate-package.py" or not path.is_file():
        continue
    hit = personal.search(read(path))
    if hit:
        fail(f"Remove personal data or a secret from {relative}: {hit.group(0)!r}")

print(f"Token Explorer package v{skill_version} is valid")
