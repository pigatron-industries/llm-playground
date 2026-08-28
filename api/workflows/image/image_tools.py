"""Image-generation tools that call an external image API.

The target API is any server exposing:

* ``GET  /api/models``   -> the model names available for generation
* ``POST /api/generate`` -> renders ``prompt`` with the chosen model(s)

Its base URL comes from configuration: the ``IMAGE_API_URL`` env var, falling
back to the built-in default. Generated images are saved under ``data/images``
(override with ``IMAGES_DIR``).
"""

from __future__ import annotations

import asyncio
import base64
import time
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ...config import (
    DEFAULT_GENERATE_PARAMS,
    DEFAULT_MODEL_BASES,
    MODEL_BASE_PARAMS,
    get_image_api_url,
    get_images_dir,
)
from ...tools.registry import register_tool
from .image_context import get_image_base, get_image_model

MODELS_TIMEOUT = 15.0
GENERATE_TIMEOUT = 300.0  # image generation can take a while
POLL_INTERVAL = 1.5  # seconds between job-status polls


def _base() -> str:
    return get_image_api_url().rstrip("/")


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
    width: int = Field(default=512, ge=16, description="Image width in pixels.")
    height: int = Field(default=512, ge=16, description="Image height in pixels.")
    batch: int = Field(default=1, ge=1, le=16, description="How many images to generate.")


async def _poll_status(client: httpx.AsyncClient) -> dict:
    """Fetch the image API's current job status (``GET /api/async``)."""
    resp = await client.get(f"{_base()}/api/async")
    if resp.status_code >= 400:
        raise httpx.HTTPStatusError(
            f"GET /api/async returned {resp.status_code}", request=resp.request, response=resp
        )
    try:
        data = resp.json()
    except ValueError:
        raise httpx.HTTPError(f"non-JSON response from /api/async: {resp.text[:200]}")
    return data if isinstance(data, dict) else {}


def _collect_images(status: dict) -> str:
    """Save a finished job's base64 PNG images and report their URLs.

    The URL (``/api/images/<file>``, served by the app's routes) is what goes
    into the model's context — the UI recognises it in the tool result and
    renders the image as its own bubble, so the user sees the picture rather
    than a file path."""
    images = status.get("images", [])
    if not images:
        return "Error: job finished but returned no images."
    urls: list[str] = []
    for index, entry in enumerate(images, start=1):
        b64 = entry.get("image", "") if isinstance(entry, dict) else str(entry)
        if not b64:
            continue
        try:
            data = base64.b64decode(b64)
        except ValueError as exc:
            return f"Error: could not decode image from job status: {exc}"
        if data:
            urls.append(f"/api/images/{_save_image(data, index).name}")
    if not urls:
        return "Error: job finished but no decodable images were found."
    return "Generated image(s) at: " + ", ".join(urls)


@register_tool(GenerateImageArgs, description="Generate an image via the external image API and report its viewable URL.", category="Image")
async def generate_image(
    prompt: str,
    model: str | None = None,
    negprompt: str = "",
    width: int = 512,
    height: int = 512,
    batch: int = 1,
) -> str:
    """Start a background generation job and poll it to completion.

    Uses the image API's async endpoints (``POST /api/async/generate`` +
    ``GET /api/async``): the start request returns immediately, and we poll the
    shared job slot until it reports a terminal state. The poll loop is ``async``
    (it awaits the sleep and each HTTP call), so the app's event loop — and the
    UI it serves — stays responsive while the image renders.
    """
    base = _base()

    # Resolve the model: explicit arg > context selection > first in list.
    ctx_model = get_image_model()
    ctx_base = get_image_base()
    if model is None:
        model = ctx_model

    try:
        models = _list_models(ctx_base if ctx_base else None)
    except RuntimeError as exc:
        return f"Error: could not list models: {exc}"

    if model is None:
        if not models:
            return "Error: no model specified and the API reported no models."
        model = models[0]["name"]

    # Resolve the model's base to determine the correct inference parameters.
    model_base = ctx_base or ""
    if not model_base:
        for m in models:
            if m["name"] == model:
                model_base = m["base"]
                break

    params = MODEL_BASE_PARAMS.get(model_base, DEFAULT_GENERATE_PARAMS)
    steps = int(params["steps"])
    cfgscale = float(params["cfgscale"])

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
        async with httpx.AsyncClient(timeout=GENERATE_TIMEOUT) as client:
            resp = await client.post(f"{base}/api/async/generate", json=payload)
            if resp.status_code >= 400:
                return f"Error: image API returned {resp.status_code}: {resp.text[:500]}"

            deadline = time.monotonic() + GENERATE_TIMEOUT
            while True:
                status = await _poll_status(client)
                state = status.get("status")
                if state == "finished":
                    return _collect_images(status)
                if state == "error":
                    return f"Error: generation failed: {status.get('error', 'unknown error')}"
                if time.monotonic() >= deadline:
                    return "Error: timed out waiting for the image job to finish."
                await asyncio.sleep(POLL_INTERVAL)
    except httpx.HTTPError as exc:
        return f"Error: image API unreachable at {base}: {exc}"
