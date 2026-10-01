from __future__ import annotations

import sys

from agl.api import console, launcher


def main() -> None:
    """Unified desktop entry point.

    No args or launcher flags -> integrated pywebview launcher.
    A project path as the first positional arg -> preserve direct Console CLI.
    """
    argv = sys.argv[1:]
    if argv and not argv[0].startswith("-"):
        console.main()
    else:
        launcher.main()


if __name__ == "__main__":
    main()
