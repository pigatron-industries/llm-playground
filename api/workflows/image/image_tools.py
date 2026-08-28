"""Image-generation tools that call an external image API.

The target API is any server exposing:

* ``GET  /api/models``   -> the model names available for generation
* ``POST /api/generate`` -> renders ``prompt`` with the chosen model(s)

Its base URL comes from configuration: the ``IMAGE_API_URL`` env var, falling
back to the built-in default. Generated images are saved under ``data/images``
(override with ``IMAGES_DIR``).
"""

from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ...config import DEFAULT_MODEL_BASES, get_image_api_url, get_images_dir
from ...tools.registry import register_tool

MODELS_TIMEOUT = 15.0
GENERATE_TIMEOUT = 300.0  # image generation can take a while


def _base() -> str:
    return get_image_api_url().rstrip("/")


# --- Response extraction ---------------------------------------------------
# The /api/generate response shape is API-specific, so we accept the common
# ones: raw image bytes, a base64 string (optionally data-URI prefixed), a
# URL, or JSON nesting any of those under well-known keys.

_B64_RE = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")
_IMAGE_KEYS = ("image", "images", "b64_json", "output", "result", "data", "url", "image_url", "path")


def _save_image(data: bytes, index: int) -> Path:
    directory = get_images_dir()
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"image-{time.strftime('%Y%m%d-%H%M%S')}-{index:02d}"
    path = directory / f"{stem}.png"
    counter = 1
    while path.exists():
        path = directory / f"{stem}-{counter}.png"
        counter += 1
    path.write_bytes(data)
    return path


def _extract_image_values(payload: object) -> list[str]:
    """Pull candidate image values (b64 strings, URLs, paths) out of a JSON
    response, whatever shape it happens to use."""
    if isinstance(payload, str):
        return [payload]
    if isinstance(payload, list):
        values: list[str] = []
        for entry in payload:
            values.extend(_extract_image_values(entry))
        return values
    if isinstance(payload, dict):
        for key in _IMAGE_KEYS:
            if key in payload:
                values = _extract_image_values(payload[key])
                if values:
                    return values
    return []


def _resolve_values(values: list[str]) -> list[str]:
    """Turn raw values into final locations: saved local paths or URLs."""
    resolved: list[str] = []
    for index, value in enumerate(values, start=1):
        value = value.strip()
        if value.startswith(("http://", "https://")):
            resolved.append(value)
            continue
        if _B64_RE.match(value):
            data = base64.b64decode(value)
            if data:
                resolved.append(str(_save_image(data, index)))
            continue
        # Not b64, not a URL — treat as a server-side file path.
        resolved.append(value)
    return resolved


# The diffusers-playground API returns an empty list when /api/models is
# queried without a base, so when the caller doesn't specify one we probe the
# well-known bases (``DEFAULT_MODEL_BASES``) until one reports models.


def _parse_models(payload: object) -> list[dict]:
    """Normalise a /api/models response into [{name, base}] entries.

    The diffusers-playground API reports each model as
    ``{"modelid": ..., "base": ..., "stylephrase": ...}``; the `modelid` is
    what /api/generate expects in `models[].name`.
    """
    if not isinstance(payload, list):
        return []
    models: list[dict] = []
    for entry in payload:
        if isinstance(entry, dict):
            name = entry.get("modelid") or entry.get("name") or entry.get("id") or entry.get("model")
            base = entry.get("base")
        else:
            name, base = str(entry), None
        if name:
            models.append({"name": str(name), "base": str(base) if base else ""})
    return models


def _list_models(base: str | None = None) -> list[dict]:
    """List generation models from the image API (type=generate)."""
    candidates = [base] if base else [None, *DEFAULT_MODEL_BASES]
    for candidate in candidates:
        params: dict[str, str] = {"type": "generate"}
        if candidate:
            params["base"] = candidate
        try:
            with httpx.Client(timeout=MODELS_TIMEOUT) as client:
                resp = client.get(f"{_base()}/api/models", params=params)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"image API unreachable at {_base()}: {exc}")
        if resp.status_code >= 400:
            raise RuntimeError(f"GET /api/models returned {resp.status_code}: {resp.text[:300]}")
        try:
            models = _parse_models(resp.json())
        except ValueError:
            raise RuntimeError(f"non-JSON response from /api/models: {resp.text[:300]}")
        if models:
            return models
    return []


def list_available_models(base: str | None = None) -> list[dict]:
    """Public accessor for the API routes (``GET /api/image/models``) and the
    UI, so they can list generation models without reaching into the private
    helpers above. Raises ``RuntimeError`` when the image API is unreachable."""
    return _list_models(base)


# --- Tools -----------------------------------------------------------------


class ListImageModelsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base: str | None = Field(
        default=None,
        description="Model base to filter by (e.g. flux, sdxl_1_0). Omit to search common bases automatically.",
    )


@register_tool(ListImageModelsArgs, description="List the image model names available on the image API.", category="Image")
def list_image_models(base: str | None = None) -> str:
    try:
        models = _list_models(base)
    except RuntimeError as exc:
        return f"Error: {exc}"
    if not models:
        return "No image models reported by the API."
    return "Available models: " + ", ".join(model["name"] for model in models)


class GenerateImageArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str | None = Field(
        default=None,
        description="Model name to use, e.g. 'black-forest-labs/FLUX.1-schnell' (see list_image_models). Omit to use the first available model.",
    )
    prompt: str = Field(description="What to generate.")
    negprompt: str = Field(default="", description="What to avoid in the image.")
    steps: int = Field(default=40, ge=1, description="Number of diffusion steps.")
    cfgscale: float = Field(default=7.0, ge=0.0, description="Prompt-guidance (CFG) scale.")
    width: int = Field(default=512, ge=16, description="Image width in pixels.")
    height: int = Field(default=512, ge=16, description="Image height in pixels.")
    batch: int = Field(default=1, ge=1, le=16, description="How many images to generate.")


@register_tool(GenerateImageArgs, description="Generate an image via the external image API and report where it was saved.", category="Image")
def generate_image(
    prompt: str,
    model: str | None = None,
    negprompt: str = "",
    steps: int = 40,
    cfgscale: float = 7.0,
    width: int = 512,
    height: int = 512,
    batch: int = 1,
) -> str:
    base = _base()

    if model is None:
        try:
            models = _list_models(None)
        except RuntimeError as exc:
            return f"Error: no model specified and could not list models: {exc}"
        if not models:
            return "Error: no model specified and the API reported no models."
        model = models[0]["name"]

    payload = {
        "prompt": prompt,
        "negprompt": negprompt,
        "steps": steps,
        "cfgscale": cfgscale,
        "width": width,
        "height": height,
        "batch": batch,
        "models": [{"name": model, "weight": 1.0}],
        "loras": [],
    }

    try:
        with httpx.Client(timeout=GENERATE_TIMEOUT) as client:
            resp = client.post(f"{base}/api/generate", json=payload)
    except httpx.HTTPError as exc:
        return f"Error: image API unreachable at {base}: {exc}"
    if resp.status_code >= 400:
        return f"Error: image API returned {resp.status_code}: {resp.text[:500]}"

    if resp.headers.get("content-type", "").startswith("image/"):
        return f"Generated image saved to {_save_image(resp.content, 1)}"

    try:
        data = resp.json()
    except ValueError:
        return f"Error: unexpected non-image, non-JSON response from {base}/api/generate"

    locations = _resolve_values(_extract_image_values(data))
    if not locations:
        return f"Error: could not find an image in the API response: {json.dumps(data)[:500]}"
    return "Generated image(s) at: " + ", ".join(locations)
