"""Application entrypoint.

Creates the FastAPI backend, mounts the API routes, then attaches the NiceGUI
frontend onto the same app so everything runs as a single process.

Run it with either:
    python app.py
    uvicorn app:app        # (nicegui is mounted via ui.run_with)
"""

from __future__ import annotations

from fastapi import FastAPI
from nicegui import ui

from api.routes import router
from ui.chat import register_pages

app = FastAPI(title="LLM Test Harness")
app.include_router(router)

register_pages()
ui.run_with(app, title="LLM Test Harness", storage_secret="llm-harness-dev", dark=True)


if __name__ == "__main__":
    import argparse
    import os

    import uvicorn

    from api.config import get_host, get_port

    def _env_bool(name: str, default: bool = False) -> bool:
        return os.getenv(name, str(default)).lower() in ("1", "true", "yes", "on")

    parser = argparse.ArgumentParser(description="LLM Test Harness")
    parser.add_argument(
        "--host", default=get_host(), help="Bind host (default: %(default)s)"
    )
    parser.add_argument(
        "--port", type=int, default=get_port(), help="Bind port (default: %(default)s)"
    )
    parser.add_argument(
        "--reload",
        action=argparse.BooleanOptionalAction,
        default=_env_bool("RELOAD"),
        help="Auto-reload on file changes (default from RELOAD env var)",
    )
    cli = parser.parse_args()

    # Export the chosen values so they survive into the reloader's child process
    # and the UI's self-API URL resolves to this server.
    os.environ["APP_HOST"] = cli.host
    os.environ["APP_PORT"] = str(cli.port)

    uvicorn.run(
        "app:app",
        host=cli.host,
        port=cli.port,
        reload=cli.reload,
        reload_dirs=[os.path.dirname(os.path.abspath(__file__))] if cli.reload else None,
    )
