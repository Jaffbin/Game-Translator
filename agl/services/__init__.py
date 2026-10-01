"""Application service boundaries for the desktop and compatibility APIs."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .player_workflow import PlayerWorkflow

__all__ = ["PlayerWorkflow"]


def __getattr__(name: str) -> Any:
    if name == "PlayerWorkflow":
        from .player_workflow import PlayerWorkflow

        return PlayerWorkflow
    raise AttributeError(name)
