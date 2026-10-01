from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn


def main() -> None:
    from .project_console import create_app

    parser = argparse.ArgumentParser(description="AutoGame Localizer project console")
    parser.add_argument("project_dir")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--allow-remote", action="store_true")
    args = parser.parse_args()
    project_dir = Path(args.project_dir)
    if not (project_dir / "project.json").is_file():
        sys.exit(f"Project file not found: {project_dir / 'project.json'}")
    host = "0.0.0.0" if args.allow_remote else args.host
    if host != "127.0.0.1":
        print("WARNING: remote console access has no built-in authentication.")
    uvicorn.run(create_app(project_dir), host=host, port=args.port)


if __name__ == "__main__":
    main()
