# DS Gateway

DS Gateway is a standalone web UI and local gateway for
[antirez/ds4](https://github.com/antirez/ds4).

Huge credit goes to the `antirez/ds4` project: DS4 is the native local inference
engine that loads and serves DeepSeek V4 Flash/PRO. DS Gateway exists because
DS4 makes powerful local inference possible; this project simply adds a
separate, preservation-friendly UI and gateway around it.

DS Gateway does not modify DS4 source code. It talks to DS4 through its existing
surfaces:

- `download_model.sh` for model downloads and install checks.
- `ds4-server` for OpenAI-compatible chat.
- GGUF model files and the `ds4flash.gguf` symlink for model status.

## What It Provides

- Hardware assessment and model recommendation.
- Model download controls using DS4's own download script.
- ChatGPT-style local chat UI with SQLite conversation history.
- Memory, disk, model, backend, and load statistics.
- Portable web tools for search/fetch without changing DS4.
- Optional OpenAI-compatible gateway endpoint for tools such as coding agents.

## First-Time Setup

Clone this repository, then run:

```sh
./install.sh
```

The installer checks for an `antirez/ds4` checkout, offers to clone it if it is
missing, prepares DS Gateway runtime folders, and writes a local launcher under
`data/run-ds-gateway.sh`.

The installer asks before cloning DS4, building DS4, or starting DS Gateway. To
preview actions without changing files, run:

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

You can also start DS Gateway manually if you already know where DS4 is checked
out:

```sh
python3 ds4_ui.py --ds4-root /path/to/ds4
```

## Runtime Data

DS Gateway stores local runtime data under `data/`:

- `data/chat.sqlite3` for conversations and messages.
- `data/settings.json` for local UI settings and optional API keys.
- `data/server-kv/` for optional DS4 server KV checkpoints.
- `data/ds4-server.log` for server startup/runtime logs.

These files are intentionally ignored by Git.

If `ds4-server` is missing, DS Gateway can run `make ds4-server` in the DS4
checkout before starting it. Build output is written to `data/ds4-build.log`.

## Portable Web Tools

DS4 accepts OpenAI-compatible `tools` on `/v1/chat/completions`. DS Gateway keeps
DS4 unchanged by acting as the external tool runner:

1. DS Gateway sends `web_search` and `fetch_url` tool definitions to DS4.
2. DS4 returns a normal OpenAI-style tool call when it wants external context.
3. DS Gateway executes the tool, sends back a `role: tool` message, and DS4
   answers using the result.

Tool use can be toggled with the `Web` checkbox in the composer. It can also be
disabled for the whole package:

```sh
DS4_UI_WEB_TOOLS=0 python3 ds4_ui.py --ds4-root /path/to/ds4
```

Search provider order:

- Brave key saved in the UI Settings page.
- `BRAVE_SEARCH_API_KEY`: uses Brave Web Search API if no key is saved.
- `SEARXNG_URL`: uses a self-hosted SearXNG instance, for example
  `http://127.0.0.1:8080`.
- Fallback: DuckDuckGo HTML search, no key required, best effort.

## OpenAI-Compatible Gateway

The Settings page can expose DS4 through DS Gateway as an OpenAI-compatible
endpoint:

```text
http://127.0.0.1:8787/openai/v1
```

When enabled, this proxy forwards to the private DS4 server, usually:

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

## Relationship To DS4

DS Gateway is intentionally separate from DS4. It is not a fork, patch, or
replacement for [antirez/ds4](https://github.com/antirez/ds4). It is a companion
interface for people who want DS4's local inference capability with a friendly
browser UI, chat history, web-tool bridge, and optional OpenAI-compatible local
gateway.
