from __future__ import annotations

import sys

import phase75_web
import phase11_workspace


def main() -> None:
    """
    Unified entry point.

    No arguments:
      open workspace web home

    With arguments:
      open web console directly

    Examples:

      AutoGameLocalizer.exe
      AutoGameLocalizer.exe projects/MyGame_zh
      AutoGameLocalizer.exe projects/MyGame_zh --port 8000
    """
    if len(sys.argv) > 1:
        phase75_web.main()
    else:
        phase11_workspace.main()


if __name__ == "__main__":
    main()