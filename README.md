# DS Gateway

DS Gateway is a standalone web UI and local gateway for
[DwarfStar](https://github.com/antirez/ds4) (`antirez/ds4`).

Huge credit goes to DwarfStar: it is the native local inference
engine that loads and serves DeepSeek V4 Flash/PRO. DS Gateway exists because
DwarfStar makes powerful local inference possible; this project simply adds a
separate, preservation-friendly UI and gateway around it.

## Project Status

DS Gateway is a work in progress. It is being built iteratively as a practical
companion UI for DwarfStar, so expect rough edges, fast-moving features, and
changes as the DwarfStar ecosystem evolves.

This project was written with Codex 5.5 as an AI coding companion.

DS Gateway does not modify DwarfStar source code. It talks to DwarfStar through
its existing surfaces:

- `download_model.sh` for model downloads and install checks.
- `ds4-server` for OpenAI-compatible chat.
- GGUF model files and the `ds4flash.gguf` symlink for model status.

## What It Provides

- Hardware assessment and model recommendation.
- Model download controls using DwarfStar's own download script.
- ChatGPT-style local chat UI with SQLite conversation history.
- Memory, disk, model, backend, and load statistics.
- Portable web tools for search/fetch without changing DwarfStar.
- Optional OpenAI-compatible gateway endpoint for tools such as coding agents.

## First-Time Setup

Clone this repository, then run:

```sh
./install.sh
```

The installer checks for a DwarfStar (`antirez/ds4`) checkout, offers to clone
it if it is missing, prepares DS Gateway runtime folders, and writes a local
launcher under `data/run-ds-gateway.sh`.

The installer asks before cloning DwarfStar, building DwarfStar, or starting DS
Gateway. To preview actions without changing files, run:

```sh
./install.sh --dry-run
```

After setup, run:

```sh
data/run-ds-gateway.sh
```

Then open:

```text
http://127.0.0.1:8787
```

You can also start DS Gateway manually if you already know where DwarfStar is checked
out:

```sh
python3 ds4_ui.py --ds4-root /path/to/ds4
```

## Runtime Data

DS Gateway stores local runtime data under `data/`:

- `data/chat.sqlite3` for conversations and messages.
- `data/settings.json` for local UI settings and optional API keys.
- `data/server-kv/` for optional DwarfStar server KV checkpoints.
- `data/ds4-server.log` for server startup/runtime logs.

These files are intentionally ignored by Git.

If `ds4-server` is missing, DS Gateway can run `make ds4-server` in the DwarfStar
checkout before starting it. Build output is written to `data/ds4-build.log`.

## Portable Web Tools

DwarfStar accepts OpenAI-compatible `tools` on `/v1/chat/completions`. DS
Gateway keeps DwarfStar unchanged by acting as the external tool runner:

1. DS Gateway sends `web_search` and `fetch_url` tool definitions to DwarfStar.
2. DwarfStar returns a normal OpenAI-style tool call when it wants external
   context.
3. DS Gateway executes the tool, sends back a `role: tool` message, and DwarfStar
   answers using the result.

Tool use can be toggled with the `Web` checkbox in the composer. It can also be
disabled for the whole package:

```sh
DWARFSTAR_GATEWAY_WEB_TOOLS=0 python3 ds4_ui.py --ds4-root /path/to/ds4
```

Search provider order:

- Brave key saved in the UI Settings page.
- `BRAVE_SEARCH_API_KEY`: uses Brave Web Search API if no key is saved.
- `SEARXNG_URL`: uses a self-hosted SearXNG instance, for example
  `http://127.0.0.1:8080`.
- Fallback: DuckDuckGo HTML search, no key required, best effort.

## OpenAI-Compatible Gateway

The Settings page can expose DwarfStar through DS Gateway as an OpenAI-compatible
endpoint:

```text
http://127.0.0.1:8787/openai/v1
```

When enabled, this proxy forwards to the private DwarfStar server, usually:

```text
http://127.0.0.1:8000/v1
```

Use `Settings > Server Exposure > Start` to expose the endpoint and `Stop` to
close it. For clients that support OpenAI-compatible models, use:

```text
Base URL: http://127.0.0.1:8787/openai/v1
Model: deepseek-v4-flash
API key: the bearer token from Settings, or any value if token auth is disabled
```

## Relationship To DwarfStar

DS Gateway is intentionally separate from DwarfStar. It is not a fork, patch, or
replacement for [DwarfStar](https://github.com/antirez/ds4). It is a companion
interface for people who want DwarfStar's local inference capability with a friendly
browser UI, chat history, web-tool bridge, and optional OpenAI-compatible local
gateway.
