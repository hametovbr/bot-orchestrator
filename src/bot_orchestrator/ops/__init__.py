"""Operations: every action the platform supports, independent of how it is asked for.

This is the application layer between the interfaces and the engine:

    web UI (views_*.py) ─┐
                         ├─► Operations ─► Orchestrator (polling, runner, tasks) + Database
    MCP server (mcp.py) ─┘

Rules:

- Every change of state goes through an operation, so validation and side effects
  live in one place and every interface behaves the same.
- Operations raise `errors.OpError` subclasses, never HTTP exceptions or UI messages;
  they return plain data and `warnings` where something worked with a caveat.
- Operations never return bot tokens or secret values.
- Pages may read the database directly for display-only queries.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .bots import Bots
from .files import Files
from .handlers import Handlers
from .modules import Modules
from .routes import Routes
from .schedules import Schedules, Tasks
from .system import System

if TYPE_CHECKING:
    from ..service import Orchestrator


class Operations:
    def __init__(self, orchestrator: Orchestrator):
        self.orchestrator = orchestrator
        self.bots = Bots(orchestrator)
        self.handlers = Handlers(orchestrator)
        self.routes = Routes(orchestrator)
        self.schedules = Schedules(orchestrator)
        self.tasks = Tasks(orchestrator)
        self.modules = Modules(orchestrator)
        self.files = Files(orchestrator)
        self.system = System(orchestrator)


__all__ = ["Operations"]
