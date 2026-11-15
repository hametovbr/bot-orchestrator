"""Errors raised by operations, shared by every interface (web UI, MCP).

Operations raise these instead of HTTP exceptions or UI messages, so each
interface decides how to show them: the web UI as a flash message or an HTTP
status, the MCP server as a tool error the agent can read.
"""


class OpError(Exception):
    """A user-facing error. The message is written for the person or agent who asked."""

    status = 400


class Invalid(OpError):
    """The input breaks a rule (bad name, syntax error, unknown cron field...)."""


class NotFound(OpError):
    """The bot, handler, route or other object does not exist."""

    status = 404


class Conflict(OpError):
    """The request clashes with the current state (duplicate name, module still imported)."""

    status = 409


class Unavailable(OpError):
    """A dependency (local Bot API server, bot-runner) refused or could not be reached."""

    status = 502
