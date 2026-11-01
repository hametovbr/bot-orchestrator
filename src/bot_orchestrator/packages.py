"""Extra Python packages for handlers.

The orchestrator stores the requirements list; the runner installs it. The hash below must
match bot_runner.packages.requirements_hash, so both sides can tell whether the runner has
the list the orchestrator wants (for example after the runner's volume was recreated).
"""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from .service import Orchestrator

log = logging.getLogger("bot_orchestrator")
SETTING = "packages_requirements"


def normalize(requirements: str) -> str:
    lines = []
    for raw in requirements.splitlines():
        line = raw.split(" #", 1)[0].strip()
        if line and not line.startswith("#"):
            lines.append(line)
    return "\n".join(lines)


def requirements_hash(requirements: str) -> str:
    return hashlib.sha256(normalize(requirements).encode()).hexdigest()[:16]


def validate(requirements: str) -> str | None:
    for line in normalize(requirements).splitlines():
        if line.startswith("-"):
            return f"Options are not supported, only package specifications: {line}"
    return None


class PackagesError(Exception):
    """The runner could not be reached or refused the request."""


class Packages:
    def __init__(self, orchestrator: Orchestrator):
        self._o = orchestrator

    async def wanted(self) -> str:
        return await self._o.db.get_setting(SETTING, "")

    async def _call(self, method: str, json: dict | None = None) -> dict[str, Any]:
        s = self._o.settings
        try:
            response = await self._o.http.request(
                method,
                s.runner_url.rstrip("/") + "/packages",
                json=json,
                headers={"X-Runner-Secret": s.runner_secret},
                timeout=15,
            )
        except httpx.HTTPError as exc:
            raise PackagesError(f"bot-runner is unreachable: {type(exc).__name__}") from None
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except ValueError:
                detail = response.text[:300]
            raise PackagesError(str(detail))
        return response.json()

    async def status(self) -> dict[str, Any]:
        """The runner's state plus whether it matches the saved list."""
        wanted = await self.wanted()
        result: dict[str, Any] = {"wanted": wanted, "wanted_hash": requirements_hash(wanted)}
        try:
            runner = await self._call("GET")
        except PackagesError as exc:
            return {**result, "error": str(exc), "runner": None, "in_sync": False}
        return {**result, "runner": runner, "in_sync": runner["hash"] == result["wanted_hash"]}

    async def save_and_install(self, requirements: str) -> dict[str, Any]:
        problem = validate(requirements)
        if problem:
            raise PackagesError(problem)
        await self._o.db.set_setting(SETTING, requirements.strip())
        return await self._call("POST", {"requirements": requirements})

    async def sync(self) -> bool:
        """Ask the runner to install the saved list if it has something else.

        Returns True if an install was started.
        """
        wanted = await self.wanted()
        try:
            runner = await self._call("GET")
            if runner["installing"] or runner["hash"] == requirements_hash(wanted):
                return False
            if runner.get("error") and runner.get("target_hash") == requirements_hash(wanted):
                # It already failed for this list; wait for the user to change it.
                return False
            await self._call("POST", {"requirements": wanted})
            log.info("asked bot-runner to install the saved packages")
            return True
        except PackagesError as exc:
            log.warning("could not sync packages: %s", exc)
            return False
