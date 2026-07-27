# LLM Playground

A simple test harness for local LLMs. NiceGUI chat frontend + FastAPI backend,
running as a single process. Connects to any OpenAI-compatible API — LM Studio
by default, but Ollama, OpenAI, or any external endpoint work by configuration
alone.

## Layout

```
llm-playground/
├── api/                # FastAPI backend — the functions the UI calls
│   ├── config.py       # provider presets + env-based selection
│   ├── providers.py    # modular OpenAI-compatible client
│   ├── routes.py       # /api/provider, /api/models, /api/chat
│   └── schemas.py      # shared pydantic models
├── ui/                 # NiceGUI frontend
│   ├── chat.py         # full-screen chat page
│   └── client.py       # async HTTP client → API layer
├── app.py              # entrypoint: mounts API + UI on one FastAPI app
└── requirements.txt
```

## Setup

Requires python 3.12

```bash
cd llm-playground
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

Start your LLM server first (e.g. LM Studio's local server on port 1234), then:

```bash
./run_local.sh                      # loads .env, then runs the app
./run_local.sh --port 9000          # extra flags pass through to app.py
```

`run_local.sh` reads configuration from `.env` (provider, host, port, reload).
Edit `.env` to change the defaults. `RELOAD=true` (the default in `.env`)
auto-restarts the server when any source file changes — handy during
development. Toggle per-run with `--reload` / `--no-reload`. You can also run the app directly, in which case
configuration comes from the shell environment and CLI flags:

```bash
python app.py                       # default 127.0.0.1:8080
python app.py --port 9000           # custom port
python app.py --host 0.0.0.0 --port 9000   # expose on the network
```

Open http://127.0.0.1:8080 (or whatever host/port you chose).

- Left sidebar: your stored chats (+ New chat).
- Center: chat history.
- Right sidebar: model selector and temperature.
- Bottom: type a message and press Enter (or the send button).

## Configuration (modular provider)

The connection is fully driven by environment variables — no code changes to
switch backends.

| Variable        | Default                        | Purpose                                   |
| --------------- | ------------------------------ | ----------------------------------------- |
| `LLM_PROVIDER`  | `lmstudio`                     | Preset: `lmstudio`, `ollama`, `openai`.   |
| `LLM_BASE_URL`  | preset value                   | Override the endpoint base URL.           |
| `LLM_API_KEY`   | preset value                   | Override the API key.                     |
| `APP_HOST`      | `127.0.0.1`                    | Server bind host.                         |
| `APP_PORT`      | `8080`                         | Server port.                              |
| `CHATS_DIR`     | `<project>/data/chats`         | Directory for persisted chat JSON files.  |

Examples:

```bash
# Ollama (OpenAI-compatible endpoint)
LLM_PROVIDER=ollama python app.py

# OpenAI proper
LLM_PROVIDER=openai LLM_API_KEY=sk-... python app.py

# Any external OpenAI-compatible API
LLM_BASE_URL=https://my-endpoint/v1 LLM_API_KEY=xyz python app.py
```

Add new presets in `api/config.py::PROVIDERS`.

## API

Provider / models:

- `GET  /api/provider` — active provider name + base URL
- `GET  /api/models`   — list available models

Stored chats (server-side conversation store, see `api/store.py`):

- `POST   /api/chats`               — create a chat → full `Chat`
- `GET    /api/chats`               — list chats (summaries, newest first)
- `GET    /api/chats/{id}`          — fetch a full chat with all messages
- `DELETE /api/chats/{id}`          — delete a chat
- `POST   /api/chats/{id}/messages` — `{content, model, temperature}`; appends
  the user message, calls the model, stores the reply, and returns the updated
  `Chat`. On a provider error the stored chat is left unchanged (safe to retry).

The UI remembers the open chat id in browser storage, so refreshing reloads the
whole conversation from the backend. Chats are persisted as one JSON file per
chat under `CHATS_DIR` (default `<project>/data/chats`), so they survive server
restarts. Swap `ChatStore` in `api/store.py` for a DB implementation if needed.

Interactive docs at http://127.0.0.1:8080/docs.
```
