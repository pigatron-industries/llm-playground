"""Application and provider configuration.

The LLM connection is intentionally modular: every supported backend is just an
OpenAI-compatible endpoint described by a `base_url` and an `api_key`. LM Studio,
Ollama and OpenAI itself all speak this protocol, so switching providers is a
matter of changing configuration only — no code changes required.

Selection order (later overrides earlier):
  1. The preset chosen via the ``LLM_PROVIDER`` env var (default: ``lmstudio``).
  2. Explicit ``LLM_BASE_URL`` / ``LLM_API_KEY`` env vars, if set.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class ProviderConfig:
    """An OpenAI-compatible backend."""

    name: str
    base_url: str
    api_key: str = "not-needed"


# Built-in presets. Add your own or point an existing one elsewhere.
PROVIDERS: dict[str, ProviderConfig] = {
    "lmstudio": ProviderConfig("lmstudio", "http://localhost:1234/v1", "lm-studio"),
    "ollama": ProviderConfig("ollama", "http://localhost:11434/v1", "ollama"),
    "openai": ProviderConfig(
        "openai", "https://api.openai.com/v1", os.getenv("OPENAI_API_KEY", "")
    ),
}

DEFAULT_PROVIDER = "lmstudio"


def get_active_provider() -> ProviderConfig:
    """Resolve the currently active provider from environment configuration."""
    name = os.getenv("LLM_PROVIDER", DEFAULT_PROVIDER)
    preset = PROVIDERS.get(name)
    if preset is None:
        known = ", ".join(sorted(PROVIDERS))
        raise ValueError(f"Unknown LLM_PROVIDER '{name}'. Known providers: {known}")

    return ProviderConfig(
        name=name,
        base_url=os.getenv("LLM_BASE_URL", preset.base_url),
        api_key=os.getenv("LLM_API_KEY", preset.api_key),
    )


# --- Server / self-connection settings ------------------------------------

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080


def get_host() -> str:
    return os.getenv("APP_HOST", DEFAULT_HOST)


def get_port() -> int:
    return int(os.getenv("APP_PORT", str(DEFAULT_PORT)))


def get_self_api_url() -> str:
    """Base URL the UI uses to reach the API (same process, own server).

    Resolved dynamically so a host/port chosen at startup is always honoured,
    even when set after this module is first imported.
    """
    return os.getenv("SELF_API_URL", f"http://{get_host()}:{get_port()}/api")


# --- Image generation API --------------------------------------------------

DEFAULT_IMAGE_API_URL = "http://localhost:8070"


def get_image_api_url() -> str:
    """Base URL of the external image-generation API (the ``image`` workflow).

    Override with the ``IMAGE_API_URL`` env var. Expects a base that exposes
    ``/api/generate`` and ``/api/models`` (e.g. ``http://localhost:8070``).
    """
    return os.getenv("IMAGE_API_URL", DEFAULT_IMAGE_API_URL)


# Well-known image-model families. The image API reports its models grouped by
# this "base"; when none is specified it must be probed until one reports
# models (see ``api.workflows.image.image_tools``). Shared with the UI, which
# offers these as the base-model dropdown.
DEFAULT_MODEL_BASES = (
    "flux",
    "sdxl_1_0",
    "sd_1_5",
    "krea",
    "zimage",
)

# Per-base inference parameters. The image-generation tool resolves the
# correct steps and CFG scale from the selected model's base rather than
# accepting them as tool parameters.
MODEL_BASE_PARAMS: dict[str, dict[str, float]] = {
    "flux": {"steps": 30, "cfgscale": 5.0},
    "sdxl_1_0": {"steps": 30, "cfgscale": 5.0},
    "sd_1_5": {"steps": 30, "cfgscale": 7.0},
    "krea": {"steps": 30, "cfgscale": 5.0},
    "zimage": {"steps": 10, "cfgscale": 0.0}
}

# Fallback when the model's base is not in MODEL_BASE_PARAMS.
DEFAULT_GENERATE_PARAMS = {"steps": 30, "cfgscale": 7.0}


# --- Storage --------------------------------------------------------------


def get_chats_dir() -> Path:
    """Directory where chats are persisted as JSON files.

    Override with the ``CHATS_DIR`` env var (relative paths resolve against the
    working directory). Defaults to ``<project>/data/chats``.
    """
    raw = os.getenv("CHATS_DIR")
    if raw:
        return Path(raw).expanduser()
    return PROJECT_ROOT / "data" / "chats"


def get_projects_file() -> Path:
    """Path to the JSON file that stores the project list."""
    raw = os.getenv("PROJECTS_FILE")
    if raw:
        return Path(raw).expanduser()
    return PROJECT_ROOT / "data" / "projects.json"


def get_images_dir() -> Path:
    """Directory where generated images are saved.

    Override with the ``IMAGES_DIR`` env var (relative paths resolve against
    the working directory). Defaults to ``<project>/data/images``.
    """
    raw = os.getenv("IMAGES_DIR")
    if raw:
        return Path(raw).expanduser()
    return PROJECT_ROOT / "data" / "images"
