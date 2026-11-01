"""Shared modules: name rules, finding imports and outlines for editor suggestions."""

from __future__ import annotations

import ast
import keyword
import re
from typing import Any

PACKAGE = "shared"
NAME = re.compile(r"^[a-z_][a-z0-9_]{0,39}$")

NEW_MODULE = '''\
"""Helpers shared by handlers. Import with: from shared import {name}"""


def example(text):
    """Return the text in upper case."""
    return text.upper()
'''


def check_name(name: str) -> str | None:
    """Return an error message, or None if the name can be a module name."""
    if not NAME.match(name):
        return "Use 1-40 lowercase letters, digits and _, starting with a letter or _."
    if keyword.iskeyword(name) or name == PACKAGE:
        return f"“{name}” is a reserved word in Python."
    return None


def shared_imports(code: str) -> set[str]:
    """Names of shared modules the code imports (`from shared import x`, `import shared.x`)."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module == PACKAGE:
                found.update(alias.name for alias in node.names if alias.name != "*")
            elif node.module.startswith(PACKAGE + "."):
                found.add(node.module.split(".")[1])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(PACKAGE + "."):
                    found.add(alias.name.split(".")[1])
    return found


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    try:
        args = ast.unparse(node.args)
    except Exception:  # noqa: BLE001 - a missing signature is not worth failing for
        args = "..."
    prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
    return f"{prefix}{node.name}({args})"


def outline(code: str) -> list[dict[str, Any]]:
    """Public top-level names of a module with their signatures and docstrings."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    items = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if not node.name.startswith("_"):
                items.append(
                    {
                        "name": node.name,
                        "kind": "function",
                        "signature": _signature(node),
                        "doc": ast.get_docstring(node) or "",
                    }
                )
        elif isinstance(node, ast.ClassDef):
            if not node.name.startswith("_"):
                items.append(
                    {
                        "name": node.name,
                        "kind": "class",
                        "signature": f"class {node.name}",
                        "doc": ast.get_docstring(node) or "",
                    }
                )
        elif isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and not target.id.startswith("_"):
                    items.append(
                        {"name": target.id, "kind": "variable", "signature": target.id, "doc": ""}
                    )
    return items


def module_doc(code: str) -> str:
    try:
        return ast.get_docstring(ast.parse(code)) or ""
    except SyntaxError:
        return ""
