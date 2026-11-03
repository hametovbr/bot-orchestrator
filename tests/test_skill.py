"""The handler skill must keep up with the code.

skills/bot-orchestrator-handlers is what other agents read to write handlers. These
checks fail when the SDK hints, routes or version change without the skill.
"""

import ast
import re
import tomllib
from pathlib import Path

import pytest

from bot_orchestrator.routing import ROUTE_KINDS, UPDATE_TYPES
from bot_orchestrator.updates import CONTENT_TYPES, MESSAGE_KINDS

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "bot-orchestrator-handlers"
HINTS = ROOT / "src" / "bot_orchestrator" / "static" / "sdk_hints.js"
HINT = "Update skills/bot-orchestrator-handlers (see AGENTS.md)."


def skill_text() -> str:
    return "\n".join(p.read_text() for p in sorted(SKILL.rglob("*.md")))


def hint_members(type_name: str) -> list[str]:
    """Keys of one type block in sdk_hints.js, e.g. Context: { reply: M(...), ... }."""
    source = HINTS.read_text()
    block = re.search(rf"\n    {type_name}: \{{(.*?)\n    \}},", source, re.S)
    assert block, f"{type_name} not found in sdk_hints.js"
    members = re.findall(r"^\s+(\w+): [FM]\(", block.group(1), re.M)
    assert members, f"no members parsed for {type_name}"
    return members


def test_frontmatter():
    head = (SKILL / "SKILL.md").read_text().split("---")[1]
    assert re.search(r"^name: bot-orchestrator-handlers$", head, re.M)
    assert re.search(r"^description: .{50,1024}$", head, re.M)


def test_version_matches_project():
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert f"bot-orchestrator {version}" in (SKILL / "SKILL.md").read_text(), (
        f"The skill states another bot-orchestrator version than {version}. {HINT}"
    )


@pytest.mark.parametrize(
    ("type_name", "prefix"),
    [
        ("Context", "ctx."),
        ("Files", "ctx.files."),
        ("KV", "ctx.kv."),
        ("Tasks", "ctx.tasks."),
    ],
)
def test_sdk_members_documented(type_name, prefix):
    text = skill_text()
    missing = [m for m in hint_members(type_name) if f"{prefix}{m}" not in text]
    assert not missing, f"Not in the skill: {[prefix + m for m in missing]}. {HINT}"


@pytest.mark.parametrize("type_name", ["Download", "Schedule"])
def test_fields_documented(type_name):
    text = skill_text()
    missing = [m for m in hint_members(type_name) if f"`{m}`" not in text]
    assert not missing, f"{type_name} fields not in the skill: {missing}. {HINT}"


def test_routes_and_types_documented():
    text = (SKILL / "references" / "platform.md").read_text()
    names = [*ROUTE_KINDS, *CONTENT_TYPES, *UPDATE_TYPES, *MESSAGE_KINDS]
    missing = [n for n in names if f"`{n}`" not in text]
    assert not missing, f"Not in references/platform.md: {missing}. {HINT}"


def test_python_examples_compile():
    for path in sorted(SKILL.rglob("*.md")):
        blocks = re.findall(r"```python\n(.*?)```", path.read_text(), re.S)
        for n, code in enumerate(blocks, 1):
            # Short snippets use `await` at the top level, as if inside a handler.
            compile(code, f"{path.name} block {n}", "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)


def test_links_resolve():
    for path in sorted(SKILL.rglob("*.md")):
        for target in re.findall(r"\]\(([^)#:]+)(?:#[^)]*)?\)", path.read_text()):
            assert (path.parent / target).exists(), f"{path.name}: broken link {target}"
