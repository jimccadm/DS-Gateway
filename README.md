# DS Gateway

Standalone gateway for the sibling `ds4` checkout. It does not modify or link
against DS4 code. It uses only these DS4 surfaces:

- `download_model.sh` for model downloads and install checks.
- `ds4-server` for OpenAI-compatible chat.
- GGUF files and the `ds4flash.gguf` symlink for model status.

Run it from this directory:

```sh
python3 ds4_ui.py --ds4-root ../ds4
```

Then open:

```text
http://127.0.0.1:8787
```

UI data is stored under `data/`:

- `data/chat.sqlite3` for conversations and messages.
- `data/server-kv/` for optional DS4 server KV checkpoints.
- `data/ds4-server.log` for server startup/runtime logs.

If `ds4-server` is missing, the UI runs `make ds4-server` in the sibling DS4
checkout before starting it. Build output is written to `data/ds4-build.log`.

## Portable Web Tools

DS4 already accepts OpenAI-compatible `tools` on `/v1/chat/completions`. DS Gateway
keeps DS4 unchanged by acting as the tool runner:

1. The UI sends `web_search` and `fetch_url` tool definitions to DS4.
2. DS4 returns a normal OpenAI-style tool call when it wants external context.
3. The UI executes the tool, sends back a `role: tool` message, and DS4 answers
   using the result.

Tool use can be toggled with the `Web` checkbox in the composer. It can also be
disabled for the whole package:

```sh
DS4_UI_WEB_TOOLS=0 python3 ds4_ui.py --ds4-root ../ds4
```

Search provider order:

- Brave key saved in the UI Settings page.
- `BRAVE_SEARCH_API_KEY`: uses Brave Web Search API if no key is saved.
- `SEARXNG_URL`: uses a self-hosted SearXNG instance, for example
  `http://127.0.0.1:8080`.
- Fallback: DuckDuckGo HTML search, no key required, best effort.

Saved settings are stored in `data/settings.json`.

## Server Exposure

The Settings page can expose DS4 through DS Gateway as an OpenAI-compatible
endpoint:

```text
http://127.0.0.1:8787/openai/v1
```

When enabled, this proxy forwards to the private DS4 server at
`http://127.0.0.1:8000/v1`. Use `Start` to expose the OpenAI-compatible
endpoint and `Stop` to close it. For clients that support OpenAI-compatible
models, use:

```text
Base URL: http://127.0.0.1:8787/openai/v1
Model: deepseek-v4-flash
API key: the bearer token from Settings, or any value if token auth is disabled
```

Examples:

```sh
BRAVE_SEARCH_API_KEY=... python3 ds4_ui.py --ds4-root ../ds4
SEARXNG_URL=http://127.0.0.1:8080 python3 ds4_ui.py --ds4-root ../ds4
```
