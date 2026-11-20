"""LangGraph checkpointer backed by PostgreSQL.

PostgreSQL checkpointing enables graph execution to resume across process
restarts. An in-memory fallback is available for local development, but
in-memory checkpoints do not survive restarts.
"""

from __future__ import annotations

from typing import Any

from agent.app.core.config import get_settings
from agent.app.core.logging_config import get_logger

logger = get_logger(__name__)

_checkpointer: Any | None = None
_context: Any | None = None


def get_checkpointer() -> Any:
    """Return the process-wide checkpointer."""
    global _checkpointer, _context

    if _checkpointer is not None:
        return _checkpointer

    try:
        from langgraph.checkpoint.postgres import PostgresSaver

        _context = PostgresSaver.from_conn_string(
            get_settings().database_url
        )

        saver = _context.__enter__()
        saver.setup()

        _checkpointer = saver

        logger.info(
            "LangGraph checkpointer initialized against PostgreSQL"
        )

    except Exception as exc:
        from langgraph.checkpoint.memory import MemorySaver

        logger.error(
            "PostgreSQL checkpointer unavailable (%s); "
            "falling back to in-memory checkpointing. "
            "Checkpoint data will not survive a restart.",
            exc,
        )

        _context = None
        _checkpointer = MemorySaver()

    return _checkpointer


def close_checkpointer() -> None:
    """Release the PostgreSQL connection held by the checkpointer."""
    global _checkpointer, _context

    if _context is not None:
        try:
            _context.__exit__(None, None, None)
        except Exception as exc:
            logger.warning(
                "Error closing checkpointer: %s",
                exc,
            )

    _context = None
    _checkpointer = None