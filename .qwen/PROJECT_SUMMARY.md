# Project Summary

## Overall Goal
Explore and map the LLM Playground project's architecture, key entry points, important dependencies, available scripts, and likely areas to inspect first.

## Key Knowledge

**Project Identity**
- **LLM Playground** (`/Users/rob/workspace/projects/ai/llm-playground`) — a single-process local-LLM test harness: NiceGUI chat frontend + FastAPI backend in one process.
- Connects to any **OpenAI-compatible** API (LM Studio default, Ollama, OpenAI, or custom base URL) via env vars — no code changes to switch backends.
- Python **3.12** required.

**Tech Stack / Dependencies** (`requirements.txt`)
- `nicegui`, `fastapi`, `openai` (async SDK), `httpx`, `uvicorn`, `pydantic`, `pyyaml`

**Entry Points**
| Entry | Role |
|---|---|
| `app.py` | Main entrypoint: builds `FastAPI`, mounts `api.routes.router`, attaches NiceGUI via `ui.run_with()`. Also the CLI (`--host/--port/--reload`) that launches uvicorn. |
| `run_local.sh` | Dev runner — loads `.env` (auto-export), prefers `.venv/bin/python`, then `exec python app.py "$@"`. |
| `api/routes/main.py` | All REST endpoints under `/api`. |
| `ui/chat.py` | The NiceGUI page (`register_pages()`); entire UI is one large `chat_page()`. |

**Layering (top → bottom)**
```
ui/ (NiceGUI)  →  api/routes/ (FastAPI)  →  api/service/ (chat loop + NDJSON streaming)
        ↓                                          ↓
   api/client.py (httpx)                  api/workflows/ (pluggable chat strategies)
                                                  ↓
                                        api/providers.py (OpenAI-SDK client + tool loop)
                                                  ↓
                                   api/tools/ (local tool registry) · api/store.py (JSON persistence)
```

**Key Architectural Decisions**
- **Provider abstraction** (`api/providers.py` + `api/config.py`): one `AsyncOpenAI` client against a configurable base URL. Presets in `config.py::PROVIDERS`; env vars (`LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_API_KEY`) override.
- **Workflow plugin system** (`api/workflows/`): `Workflow` ABC with a Pydantic `settings_model` (UI renders settings forms from JSON schema) and `run()` yielding `ChatEvent`s. Registered via `@register_workflow`. Five built-ins: `simple_chat`, `project_context`, `project_tools`, `image` (external image-gen API + LoRAs), `world_explorer` (text-RPG with character/item/location tools).
- **Decoupled streaming** (`api/service/chat.py`): generation runs in a background `asyncio.Task` (`StreamState`) so a client disconnect doesn't cancel it — clients can reattach and replay buffered NDJSON events. Event types: `delta`, `tool_call`, `tool_result`, `done`/`stopped`, `error`.
- **Tool loop** (`providers.py::chat_stream` + `api/tools/registry.py`): models call locally-registered tools; loop executes them and continues until final text answer. Tools defined via `@register_tool` with a Pydantic param model.
- **File-backed persistence** (`api/store.py`, `api/project_store.py`): one JSON file per chat + `projects.json`; atomic writes (temp file + `os.replace`). Swappable for a DB.

**Configuration / Env Vars** (see `.env.example`, `api/config.py`)
- `LLM_PROVIDER` (lmstudio|ollama|openai), `LLM_BASE_URL`, `LLM_API_KEY`
- `APP_HOST` (default 127.0.0.1), `APP_PORT` (default 8080), `RELOAD` (default true)
- `CHATS_DIR` (default `<project>/data/chats`), `PROJECTS_FILE` (default `<project>/data/projects.json`), `IMAGES_DIR` (default `<project>/data/images`)
- `IMAGE_API_URL` (default `http://localhost:8070`) for the image workflow; `MODEL_BASES` = flux, sdxl_1_0, sd_1_5, krea, zimage.

**Run Commands**
```bash
./run_local.sh                    # loads .env, runs app (default 127.0.0.1:8080)
./run_local.sh --port 9000        # flags pass through to app.py
python app.py --host 0.0.0.0 --port 9000
```

**Areas to Inspect First (by change type)**
- New backend/provider → `api/config.py` + `api/providers.py`
- New chat behavior → add a `Workflow` in `api/workflows/`, register it
- New LLM tool → `api/tools/*.py` via `@register_tool`
- UI changes → `ui/chat.py`, `ui/chat_components.py`, `ui/chat_settings.py`, `ui/client.py`
- Data model / persistence → `api/schemas.py` + `api/store.py`
- External image API → `api/workflows/image/`

**⚠️ Important Caveat**
- The **README's Layout section is stale** — it documents the old flat `api/routes.py`/`api/store.py` structure, but the real tree has `api/routes/`, `api/service/`, `api/tools/`, `api/workflows/` subdirectories. **Treat the code as the source of truth, not the README.**

## Recent Actions
- [DONE] Listed project root, `api/`, `ui/`, and all `api/workflows/` subdirectories.
- [DONE] Read `README.md`, `requirements.txt`, `run_local.sh`, `.env.example`.
- [DONE] Read core modules: `app.py`, `api/routes/main.py`, `api/workflows/base.py`, `api/workflows/registry.py`, `api/service/chat.py`, `api/providers.py`, `api/config.py`, `api/tools/registry.py`, `api/store.py`, `ui/chat.py`.
- [DONE] Grepped for all registered workflows and their IDs.
- [DONE] Produced a full architecture map, layering diagram, dependency list, and prioritized inspection areas.

## Current Plan
This was an exploration/mapping session — no code changes were made. No development roadmap was established. Potential next steps (pending user direction):
1. [TODO] Dive deeper into the workflow system (e.g., `image` or `world_explorer` implementation)
2. [TODO] Examine the NDJSON streaming protocol end-to-end
3. [TODO] Consider updating the stale README Layout section to match the real directory tree
4. [TODO] Await user's specific feature/fix request

**Note:** No testing procedures, linting commands, or CI setup were observed in the project during this session. No tests directory was present in the folder structure.

---

## Summary Metadata
**Update time**: 2026-08-29T15:43:43.712Z
