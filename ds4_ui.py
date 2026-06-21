#!/usr/bin/env python3
import argparse
import html
import json
import os
import platform
import queue
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from http import HTTPStatus
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urlparse
from urllib import request as urlrequest
from urllib.error import URLError, HTTPError


APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
DATA_DIR = APP_DIR / "data"
DB_PATH = DATA_DIR / "chat.sqlite3"
SETTINGS_PATH = DATA_DIR / "settings.json"

MODEL_TARGETS = {
    "q2-imatrix": {
        "file": "DeepSeek-V4-Flash-IQ2XXS-w2Q2K-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix.gguf",
        "label": "Flash Q2 imatrix",
        "disk_gb": 81,
        "ram": "96/128 GB",
        "main": True,
        "notes": "Default high-fit model for 96 GB and 128 GB machines.",
    },
    "q2-q4-imatrix": {
        "file": "DeepSeek-V4-Flash-Layers37-42Q4KExperts-OtherExpertLayersIQ2XXSGateUp-Q2KDown-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix-fixed.gguf",
        "label": "Flash Q2/Q4 imatrix",
        "disk_gb": 98,
        "ram": "128 GB",
        "main": True,
        "notes": "Higher quality Flash option for stronger 128 GB machines.",
    },
    "q4-imatrix": {
        "file": "DeepSeek-V4-Flash-Q4KExperts-F16HC-F16Compressor-F16Indexer-Q8Attn-Q8Shared-Q8Out-chat-v2-imatrix.gguf",
        "label": "Flash Q4 imatrix",
        "disk_gb": 153,
        "ram": "256 GB+",
        "main": True,
        "notes": "Recommended Flash model when memory is comfortably above 256 GB.",
    },
    "pro-q2-imatrix": {
        "file": "DeepSeek-V4-Pro-IQ2XXS-w2Q2K-AProjQ8-SExpQ8-OutQ8-Instruct-imatrix.gguf",
        "label": "PRO Q2 imatrix",
        "disk_gb": 430,
        "ram": "512 GB",
        "main": True,
        "notes": "Single-file PRO model for 512 GB class machines.",
    },
    "mtp": {
        "file": "DeepSeek-V4-Flash-MTP-Q4K-Q8_0-F32.gguf",
        "label": "Flash MTP",
        "disk_gb": 4,
        "ram": "optional",
        "main": False,
        "notes": "Optional speculative decoding component for Flash models.",
    },
}

DEFAULT_SYSTEM_PROMPT = (
    "You are DS Gateway, a helpful local AI assistant. "
    "Reply in the same language as the user's latest message. "
    "If the user's language is ambiguous or the message is a greeting like "
    "'Hello', reply in English. Be concise unless the user asks for detail. "
    "When web tools are available, use them for current facts, URLs, news, "
    "or information that may have changed, and cite the source URLs you used."
)

WEB_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the public web for current information and return concise results with URLs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The web search query."},
                    "count": {"type": "integer", "minimum": 1, "maximum": 8, "description": "Number of results to return."},
                    "freshness": {
                        "type": "string",
                        "enum": ["any", "day", "week", "month", "year"],
                        "description": "Optional recency filter.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "Fetch a public HTTP or HTTPS URL and return readable text from the page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL to fetch."},
                    "max_chars": {"type": "integer", "minimum": 1000, "maximum": 12000, "description": "Maximum text characters to return."},
                },
                "required": ["url"],
            },
        },
    },
]

SEARCH_UA = "DS4-UI/0.1 (+local tool bridge)"


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_json(handler):
    length = int(handler.headers.get("content-length", "0") or "0")
    if length <= 0:
        return {}
    data = handler.rfile.read(length)
    return json.loads(data.decode("utf-8"))


def write_json(handler, value, status=200):
    payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
    handler.send_response(status)
    handler.send_header("content-type", "application/json")
    handler.send_header("cache-control", "no-store")
    handler.send_header("content-length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


def load_settings():
    if not SETTINGS_PATH.exists():
        return {}
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_settings(settings):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(settings, indent=2, sort_keys=True), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(SETTINGS_PATH)


def public_settings():
    settings = load_settings()
    web = settings.get("web") or {}
    brave_key = (web.get("brave_api_key") or os.environ.get("BRAVE_SEARCH_API_KEY") or "").strip()
    key_source = "saved" if (web.get("brave_api_key") or "").strip() else ("env" if os.environ.get("BRAVE_SEARCH_API_KEY") else "")
    return {
        "web": {
            "brave_api_key_saved": bool(brave_key),
            "brave_api_key_source": key_source,
            "searxng_url": os.environ.get("SEARXNG_URL", ""),
        }
    }


def saved_brave_api_key():
    web = (load_settings().get("web") or {})
    return (web.get("brave_api_key") or os.environ.get("BRAVE_SEARCH_API_KEY") or "").strip()


def exposure_settings():
    exposure = (load_settings().get("exposure") or {})
    api_key = (exposure.get("api_key") or "").strip()
    return {
        "openai_enabled": bool(exposure.get("openai_enabled")),
        "require_api_key": bool(exposure.get("require_api_key")),
        "api_key_saved": bool(api_key),
    }


def exposure_api_key():
    exposure = (load_settings().get("exposure") or {})
    return (exposure.get("api_key") or "").strip()


def update_exposure_settings(body):
    settings = load_settings()
    exposure = settings.setdefault("exposure", {})
    if "openai_enabled" in body:
        exposure["openai_enabled"] = bool(body.get("openai_enabled"))
    if "require_api_key" in body:
        exposure["require_api_key"] = bool(body.get("require_api_key"))
    new_key = None
    if body.get("clear_api_key"):
        exposure.pop("api_key", None)
    elif body.get("generate_api_key"):
        new_key = "ds4_" + uuid.uuid4().hex + uuid.uuid4().hex[:16]
        exposure["api_key"] = new_key
        exposure["require_api_key"] = True
    else:
        api_key = (body.get("api_key") or "").strip()
        if api_key:
            exposure["api_key"] = api_key
    save_settings(settings)
    public = exposure_settings()
    if new_key:
        public["new_api_key"] = new_key
    return public


def run_capture(args, timeout=2):
    try:
        out = subprocess.check_output(args, stderr=subprocess.DEVNULL, timeout=timeout)
        return out.decode("utf-8", "replace").strip()
    except Exception:
        return ""


def env_enabled(name, default=True):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in ("0", "false", "no", "off")


def web_tools_enabled():
    if "DWARFSTAR_GATEWAY_WEB_TOOLS" in os.environ:
        return env_enabled("DWARFSTAR_GATEWAY_WEB_TOOLS", True)
    return env_enabled("DS4_UI_WEB_TOOLS", True)


def clean_text(value):
    value = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", value or "")
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def text_excerpt(value, limit):
    value = clean_text(value)
    if len(value) <= limit:
        return value
    return value[:limit].rsplit(" ", 1)[0] + "..."


def fetch_bytes(url, headers=None, timeout=12, max_bytes=1_250_000):
    req = urlrequest.Request(
        url,
        headers={
            "user-agent": SEARCH_UA,
            "accept": "text/html,application/xhtml+xml,application/json,text/plain;q=0.9,*/*;q=0.5",
            **(headers or {}),
        },
    )
    with urlrequest.urlopen(req, timeout=timeout) as resp:
        data = resp.read(max_bytes)
        ctype = resp.headers.get("content-type", "")
        final_url = resp.geturl()
    return data, ctype, final_url


def fetch_json(url, headers=None, timeout=12):
    data, ctype, final_url = fetch_bytes(
        url,
        headers={"accept": "application/json", **(headers or {})},
        timeout=timeout,
    )
    del ctype, final_url
    return json.loads(data.decode("utf-8", "replace"))


def normalize_count(value, default=5):
    try:
        return max(1, min(8, int(value)))
    except (TypeError, ValueError):
        return default


def brave_search(query, count, freshness):
    key = saved_brave_api_key()
    if not key:
        return None
    fresh = {"day": "pd", "week": "pw", "month": "pm", "year": "py"}.get(freshness)
    params = {"q": query, "count": min(count, 8)}
    if fresh:
        params["freshness"] = fresh
    url = "https://api.search.brave.com/res/v1/web/search?" + urlencode(params)
    obj = fetch_json(url, headers={"X-Subscription-Token": key})
    results = []
    for item in ((obj.get("web") or {}).get("results") or [])[:count]:
        snippets = item.get("extra_snippets") or []
        desc = item.get("description") or ""
        if snippets:
            desc = f"{desc} {' '.join(snippets[:2])}".strip()
        results.append({
            "title": clean_text(item.get("title") or ""),
            "url": item.get("url") or "",
            "snippet": text_excerpt(desc, 700),
        })
    return {"provider": "brave", "query": query, "results": results}


def searxng_search(query, count, freshness):
    base = os.environ.get("SEARXNG_URL", "").strip().rstrip("/")
    if not base:
        return None
    params = {
        "q": query,
        "format": "json",
        "language": os.environ.get("SEARXNG_LANGUAGE", "auto"),
        "pageno": "1",
    }
    if freshness and freshness != "any":
        params["time_range"] = {"day": "day", "week": "week", "month": "month", "year": "year"}.get(freshness, "")
    url = f"{base}/search?" + urlencode({k: v for k, v in params.items() if v})
    obj = fetch_json(url)
    results = []
    for item in (obj.get("results") or [])[:count]:
        results.append({
            "title": clean_text(item.get("title") or ""),
            "url": item.get("url") or "",
            "snippet": text_excerpt(item.get("content") or "", 700),
        })
    return {"provider": "searxng", "query": query, "results": results}


def ddg_link(value):
    value = html.unescape(value or "")
    parsed = urlparse(value)
    if parsed.path.startswith("/l/"):
        qs = parse_qs(parsed.query)
        if qs.get("uddg"):
            return unquote(qs["uddg"][0])
    return value


def duckduckgo_search(query, count, freshness):
    del freshness
    url = "https://duckduckgo.com/html/?" + urlencode({"q": query})
    data, ctype, final_url = fetch_bytes(url, timeout=12)
    del ctype, final_url
    page = data.decode("utf-8", "replace")
    blocks = re.findall(r'(?is)<div class="result results_links.*?</div>\s*</div>', page)
    if not blocks:
        blocks = re.findall(r'(?is)<a rel="nofollow" class="result__a".*?(?=<a rel="nofollow" class="result__a"|$)', page)
    results = []
    for block in blocks:
        link = re.search(r'(?is)<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block)
        if not link:
            continue
        snippet = re.search(r'(?is)<a[^>]+class="result__snippet"[^>]*>(.*?)</a>|<div[^>]+class="result__snippet"[^>]*>(.*?)</div>', block)
        results.append({
            "title": clean_text(link.group(2)),
            "url": ddg_link(link.group(1)),
            "snippet": text_excerpt((snippet.group(1) or snippet.group(2)) if snippet else "", 700),
        })
        if len(results) >= count:
            break
    return {"provider": "duckduckgo-html", "query": query, "results": results}


def tool_web_search(args):
    query = (args.get("query") or "").strip()
    if not query:
        return {"ok": False, "error": "Missing query."}
    count = normalize_count(args.get("count"))
    freshness = (args.get("freshness") or "any").strip().lower()
    providers = (brave_search, searxng_search, duckduckgo_search)
    errors = []
    for provider in providers:
        try:
            result = provider(query, count, freshness)
            if result is not None:
                result["ok"] = True
                return result
        except Exception as exc:
            errors.append(f"{provider.__name__}: {exc}")
    return {"ok": False, "query": query, "results": [], "error": "; ".join(errors) or "No search provider is configured."}


def tool_fetch_url(args):
    raw_url = (args.get("url") or "").strip()
    parsed = urlparse(raw_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return {"ok": False, "error": "Only public http:// and https:// URLs are supported."}
    try:
        max_chars = max(1000, min(12000, int(args.get("max_chars") or 6000)))
    except (TypeError, ValueError):
        max_chars = 6000
    data, ctype, final_url = fetch_bytes(raw_url)
    raw = data.decode("utf-8", "replace")
    title_match = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw)
    return {
        "ok": True,
        "url": final_url,
        "content_type": ctype,
        "title": clean_text(title_match.group(1)) if title_match else "",
        "text": text_excerpt(raw, max_chars),
    }


def execute_tool_call(tool_call):
    fn = ((tool_call.get("function") or {}).get("name") or "").strip()
    raw_args = (tool_call.get("function") or {}).get("arguments") or "{}"
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
    except json.JSONDecodeError as exc:
        return {"ok": False, "tool": fn, "error": f"Invalid JSON arguments: {exc}"}
    try:
        if fn == "web_search":
            result = tool_web_search(args)
        elif fn == "fetch_url":
            result = tool_fetch_url(args)
        else:
            result = {"ok": False, "error": f"Unknown tool: {fn}"}
    except Exception as exc:
        result = {"ok": False, "error": str(exc)}
    result["tool"] = fn
    return result


def sysctl_int(name):
    out = run_capture(["sysctl", "-n", name])
    try:
        return int(out)
    except ValueError:
        return 0


def bytes_gib(value):
    if not value:
        return 0.0
    return round(float(value) / (1024 ** 3), 2)


def process_rss_bytes(pid):
    if not pid:
        return 0
    out = run_capture(["ps", "-o", "rss=", "-p", str(pid)])
    try:
        return int(out.strip()) * 1024
    except ValueError:
        return 0


def process_exists(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def memory_stats():
    system = platform.system()
    if system == "Darwin":
        total = sysctl_int("hw.memsize")
        vm = run_capture(["vm_stat"])
        page_size = 4096
        pages = {}
        for line in vm.splitlines():
            if "page size of" in line:
                for part in line.split():
                    if part.isdigit():
                        page_size = int(part)
                        break
            if ":" not in line:
                continue
            key, raw = line.split(":", 1)
            raw = raw.strip().strip(".")
            try:
                pages[key] = int(raw)
            except ValueError:
                pass
        free = pages.get("Pages free", 0) * page_size
        speculative = pages.get("Pages speculative", 0) * page_size
        active = pages.get("Pages active", 0) * page_size
        inactive = pages.get("Pages inactive", 0) * page_size
        wired = pages.get("Pages wired down", 0) * page_size
        compressed = pages.get("Pages occupied by compressor", 0) * page_size
        available = min(total, free + speculative + inactive)
        used = max(0, total - available)
        pressure = 0 if total <= 0 else min(1.0, (active + wired + compressed) / total)
        return {
            "total_bytes": total,
            "used_bytes": used,
            "available_bytes": available,
            "free_bytes": free + speculative,
            "pressure": round(pressure, 3),
            "detail": {"active_bytes": active, "inactive_bytes": inactive, "wired_bytes": wired, "compressed_bytes": compressed, "reclaimable_bytes": inactive},
        }
    if Path("/proc/meminfo").exists():
        vals = {}
        for line in Path("/proc/meminfo").read_text(errors="replace").splitlines():
            parts = line.split()
            if len(parts) >= 2:
                try:
                    vals[parts[0].rstrip(":")] = int(parts[1]) * 1024
                except ValueError:
                    pass
        total = vals.get("MemTotal", 0)
        avail = vals.get("MemAvailable", vals.get("MemFree", 0))
        used = max(0, total - avail)
        pressure = 0 if total <= 0 else min(1.0, used / total)
        return {"total_bytes": total, "used_bytes": used, "available_bytes": avail, "pressure": round(pressure, 3), "detail": {}}
    return {"total_bytes": 0, "used_bytes": 0, "available_bytes": 0, "pressure": 0, "detail": {}}


def detect_backend(ds4_root):
    system = platform.system()
    if system == "Darwin":
        return "metal"
    if shutil.which("hipcc") or Path("/opt/rocm/bin/hipcc").exists():
        return "rocm"
    if shutil.which("nvidia-smi") or Path("/usr/local/cuda").exists():
        return "cuda"
    if (ds4_root / "ds4-server").exists():
        return "cpu"
    return "unknown"


class BridgeState:
    def __init__(self, ds4_root, host, port):
        self.ds4_root = Path(ds4_root).resolve()
        self.host = host
        self.port = port
        self.server_proc = None
        self.server_log = None
        self.downloads = {}
        self.lock = threading.Lock()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def db(self):
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        with self.db() as conn:
            conn.executescript(
                """
                create table if not exists conversations (
                    id text primary key,
                    title text not null,
                    created_at text not null,
                    updated_at text not null
                );
                create table if not exists messages (
                    id text primary key,
                    conversation_id text not null references conversations(id) on delete cascade,
                    role text not null,
                    content text not null,
                    reasoning text,
                    usage_json text,
                    created_at text not null
                );
                create index if not exists messages_conversation_idx on messages(conversation_id, created_at);
                """
            )

    def gguf_dir(self):
        custom = os.environ.get("DS4_GGUF_DIR")
        if custom:
            p = Path(custom)
            return p if p.is_absolute() else self.ds4_root / p
        return self.ds4_root / "gguf"

    def model_status(self):
        gguf = self.gguf_dir()
        link = self.ds4_root / "ds4flash.gguf"
        link_target = None
        if link.exists() or link.is_symlink():
            try:
                link_target = link.resolve()
            except OSError:
                link_target = None
        out = []
        for target, meta in MODEL_TARGETS.items():
            path = gguf / meta["file"]
            part = Path(str(path) + ".part")
            size = path.stat().st_size if path.exists() else 0
            part_size = part.stat().st_size if part.exists() else 0
            out.append({
                "target": target,
                "label": meta["label"],
                "file": meta["file"],
                "path": str(path),
                "installed": size > 0,
                "bytes": size,
                "partial_bytes": part_size,
                "expected_gb": meta["disk_gb"],
                "main": meta["main"],
                "linked": bool(meta["main"] and link_target and path.resolve() == link_target),
                "ram": meta["ram"],
                "notes": meta["notes"],
            })
        return out

    def hardware(self):
        mem = memory_stats()
        disk = shutil.disk_usage(self.gguf_dir().parent if self.gguf_dir().parent.exists() else self.ds4_root)
        backend = detect_backend(self.ds4_root)
        machine = platform.machine()
        model = run_capture(["sysctl", "-n", "hw.model"]) if platform.system() == "Darwin" else ""
        cpu = run_capture(["sysctl", "-n", "machdep.cpu.brand_string"]) if platform.system() == "Darwin" else platform.processor()
        ram_gb = bytes_gib(mem["total_bytes"])
        recommendation = self.recommend_model(ram_gb, bytes_gib(disk.free), backend)
        return {
            "platform": platform.platform(),
            "machine": machine,
            "model": model,
            "cpu": cpu,
            "backend": backend,
            "memory": mem,
            "disk": {"total_bytes": disk.total, "used_bytes": disk.used, "free_bytes": disk.free},
            "recommendation": recommendation,
        }

    def recommend_model(self, ram_gb, free_disk_gb, backend):
        streaming = False
        ctx = 32768
        flags = []
        reason = "No DS4 GPU backend was detected; CPU is suitable only for diagnostics."
        target = "q2-imatrix"
        if ram_gb >= 500:
            target = "pro-q2-imatrix"
            ctx = 100000
            reason = "512 GB class memory can run the single-file PRO Q2 target."
        elif ram_gb >= 250:
            target = "q4-imatrix"
            ctx = 100000
            reason = "256 GB class memory is a good fit for Flash Q4."
        elif ram_gb >= 120:
            target = "q2-q4-imatrix"
            ctx = 100000
            reason = "128 GB class memory can use the mixed Q2/Q4 Flash target."
        elif ram_gb >= 90:
            target = "q2-imatrix"
            ctx = 100000
            reason = "96 GB class memory is the intended fit for Flash Q2."
        elif ram_gb >= 60:
            target = "q2-imatrix"
            streaming = True
            flags = ["--ssd-streaming", "--ssd-streaming-cache-experts", "32GB"]
            reason = "64 GB class memory should use Flash Q2 with SSD streaming."
        if backend == "cpu":
            reason = "A DS4 server binary exists, but no GPU backend was detected; use CPU only for diagnostics."
        enough_disk = free_disk_gb >= MODEL_TARGETS[target]["disk_gb"] + 10
        return {
            "target": target,
            "label": MODEL_TARGETS[target]["label"],
            "ctx": ctx,
            "streaming": streaming,
            "flags": flags,
            "reason": reason,
            "enough_disk": enough_disk,
        }

    def stats(self):
        mem = memory_stats()
        pids = self.ds4_server_pids()
        rss = sum(process_rss_bytes(pid) for pid in pids)
        disk = shutil.disk_usage(self.gguf_dir().parent if self.gguf_dir().parent.exists() else self.ds4_root)
        load = os.getloadavg()[0] if hasattr(os, "getloadavg") else 0
        server = self.server_status()
        return {
            "time": time.time(),
            "memory": mem,
            "model_rss_bytes": rss,
            "disk": {"total_bytes": disk.total, "used_bytes": disk.used, "free_bytes": disk.free},
            "load_1m": round(load, 2),
            "server": server,
        }

    def ds4_server_pids(self):
        binary = str(self.ds4_root / "ds4-server")
        out = run_capture(["ps", "-axo", "pid=,command="], timeout=3)
        pids = set()
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            try:
                pid = int(parts[0])
            except ValueError:
                continue
            cmd = parts[1]
            if binary in cmd or (str(self.ds4_root) in cmd and "/ds4-server" in cmd):
                pids.add(pid)
        if self.server_proc and self.server_proc.poll() is None:
            pids.add(self.server_proc.pid)
        return sorted(pids)

    def server_status(self):
        spawned = False
        pid = None
        code = None
        if self.server_proc:
            pid = self.server_proc.pid
            code = self.server_proc.poll()
            spawned = code is None
        api = self.ping_server()
        pids = self.ds4_server_pids()
        return {"spawned": spawned, "pid": pid, "pids": pids, "exit_code": code, "api": api, "url": f"http://{self.host}:{self.port}"}

    def ping_server(self):
        try:
            with urlrequest.urlopen(f"http://{self.host}:{self.port}/v1/models", timeout=0.8) as resp:
                return {"ok": True, "models": json.loads(resp.read().decode("utf-8"))}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def start_server(self, body):
        with self.lock:
            if self.server_proc and self.server_proc.poll() is None:
                if not body.get("restart"):
                    return {"ok": True, "status": self.server_status()}
            existing = self.ping_server()
            if existing["ok"] and not body.get("restart"):
                return {"ok": True, "status": self.server_status()}
            if body.get("restart"):
                stopped = self._stop_server_processes()
                if not stopped["ok"]:
                    return stopped
            binary = self.ds4_root / "ds4-server"
            if not binary.exists():
                build = self.build_server_binary()
                if not build["ok"]:
                    return build
            port = int(body.get("port") or self.port)
            self.port = port
            model_path = body.get("model_path") or str(self.ds4_root / "ds4flash.gguf")
            ctx = int(body.get("ctx") or self.hardware()["recommendation"]["ctx"])
            cmd = [str(binary), "-m", model_path, "--host", self.host, "--port", str(port), "--ctx", str(ctx), "--cors"]
            backend = body.get("backend") or self.hardware()["backend"]
            if backend in ("metal", "cuda", "cpu"):
                cmd.append(f"--{backend}")
            if backend == "rocm":
                cmd.append("--rocm")
            if body.get("ssd_streaming"):
                cmd.append("--ssd-streaming")
            cache_experts = body.get("ssd_streaming_cache")
            if cache_experts:
                cmd.extend(["--ssd-streaming-cache-experts", str(cache_experts)])
            kv_dir = DATA_DIR / "server-kv"
            kv_dir.mkdir(parents=True, exist_ok=True)
            cmd.extend(["--kv-disk-dir", str(kv_dir), "--kv-disk-space-mb", str(int(body.get("kv_mb") or 8192))])
            log_path = DATA_DIR / "ds4-server.log"
            self.server_log = open(log_path, "ab", buffering=0)
            self.server_proc = subprocess.Popen(cmd, cwd=self.ds4_root, stdout=self.server_log, stderr=subprocess.STDOUT)
        deadline = time.time() + int(body.get("readiness_timeout_sec") or 240)
        while time.time() < deadline:
            status = self.server_status()
            if status["api"]["ok"]:
                return {"ok": True, "status": status}
            if self.server_proc and self.server_proc.poll() is not None:
                break
            time.sleep(0.5)
        return {"ok": False, "status": self.server_status(), "error": "DS4 server did not become ready. Check data/ds4-server.log."}

    def build_server_binary(self):
        makefile = self.ds4_root / "Makefile"
        binary = self.ds4_root / "ds4-server"
        if not makefile.exists():
            return {"ok": False, "error": f"Missing {makefile}; cannot build ds4-server."}
        log_path = DATA_DIR / "ds4-build.log"
        with open(log_path, "wb") as log:
            log.write(b"Building ds4-server with: make ds4-server\n\n")
            log.flush()
            try:
                proc = subprocess.Popen(
                    ["make", "ds4-server"],
                    cwd=self.ds4_root,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
                code = proc.wait(timeout=1800)
            except subprocess.TimeoutExpired:
                proc.kill()
                return {"ok": False, "error": f"Timed out building ds4-server. Check {log_path}."}
            except Exception as exc:
                return {"ok": False, "error": f"Could not build ds4-server: {exc}. Check {log_path}."}
        if code != 0 or not binary.exists():
            return {"ok": False, "error": f"Building ds4-server failed. Check {log_path}."}
        return {"ok": True, "build_log": str(log_path)}

    def stop_server(self):
        with self.lock:
            return self._stop_server_processes()

    def _stop_server_processes(self):
        pids = set(self.ds4_server_pids())
        proc = self.server_proc
        if proc and proc.poll() is None:
            pids.add(proc.pid)
        if not pids:
            self.server_proc = None
            return {"ok": True, "status": self.server_status()}
        for pid in sorted(pids):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        def still_live(pid):
            if proc and pid == proc.pid:
                return proc.poll() is None and process_exists(pid)
            return process_exists(pid)
        deadline = time.time() + 20
        while time.time() < deadline:
            live = [pid for pid in pids if still_live(pid)]
            if not live and not self.ping_server()["ok"]:
                self.server_proc = None
                return {"ok": True, "status": self.server_status()}
            time.sleep(0.4)
        for pid in sorted(pids):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        deadline = time.time() + 8
        while time.time() < deadline:
            live = [pid for pid in pids if still_live(pid)]
            if not live:
                self.server_proc = None
                return {"ok": True, "status": self.server_status()}
            time.sleep(0.3)
        return {"ok": False, "error": f"Could not stop ds4-server process(es): {sorted(pids)}", "status": self.server_status()}

    def start_download(self, target):
        if target not in MODEL_TARGETS:
            return {"ok": False, "error": "Unknown model target."}
        job_id = uuid.uuid4().hex
        q = queue.Queue()
        self.downloads[job_id] = {"id": job_id, "target": target, "status": "running", "lines": [], "started_at": now_iso()}

        def runner():
            cmd = ["./download_model.sh", target]
            try:
                proc = subprocess.Popen(cmd, cwd=self.ds4_root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
                self.downloads[job_id]["pid"] = proc.pid
                for line in proc.stdout:
                    q.put(line.rstrip())
                code = proc.wait()
                self.downloads[job_id]["status"] = "done" if code == 0 else "failed"
                self.downloads[job_id]["exit_code"] = code
            except Exception as exc:
                self.downloads[job_id]["status"] = "failed"
                self.downloads[job_id]["error"] = str(exc)
            finally:
                self.downloads[job_id]["finished_at"] = now_iso()

        def collector():
            while self.downloads[job_id]["status"] == "running" or not q.empty():
                try:
                    line = q.get(timeout=0.5)
                except queue.Empty:
                    continue
                lines = self.downloads[job_id]["lines"]
                lines.append(line)
                del lines[:-80]

        threading.Thread(target=runner, daemon=True).start()
        threading.Thread(target=collector, daemon=True).start()
        return {"ok": True, "job": self.downloads[job_id]}

    def conversations(self):
        with self.db() as conn:
            rows = conn.execute(
                "select c.*, (select content from messages m where m.conversation_id=c.id order by created_at desc limit 1) as last_message "
                "from conversations c order by updated_at desc"
            ).fetchall()
        return [dict(row) for row in rows]

    def conversation(self, cid):
        with self.db() as conn:
            conv = conn.execute("select * from conversations where id=?", (cid,)).fetchone()
            if not conv:
                return None
            messages = conn.execute("select * from messages where conversation_id=? order by created_at", (cid,)).fetchall()
        data = dict(conv)
        data["messages"] = [dict(row) for row in messages]
        return data

    def create_conversation(self, title="New chat"):
        cid = uuid.uuid4().hex
        ts = now_iso()
        with self.db() as conn:
            conn.execute("insert into conversations(id,title,created_at,updated_at) values(?,?,?,?)", (cid, title, ts, ts))
        return self.conversation(cid)

    def delete_conversation(self, cid):
        with self.db() as conn:
            conn.execute("delete from messages where conversation_id=?", (cid,))
            cur = conn.execute("delete from conversations where id=?", (cid,))
        return cur.rowcount > 0

    def save_message(self, cid, role, content, reasoning=None, usage=None):
        mid = uuid.uuid4().hex
        ts = now_iso()
        with self.db() as conn:
            conn.execute(
                "insert into messages(id,conversation_id,role,content,reasoning,usage_json,created_at) values(?,?,?,?,?,?,?)",
                (mid, cid, role, content, reasoning, json.dumps(usage) if usage else None, ts),
            )
            title = content.strip().splitlines()[0][:48] if role == "user" else None
            if title:
                current = conn.execute("select title from conversations where id=?", (cid,)).fetchone()
                if current and current["title"] == "New chat":
                    conn.execute("update conversations set title=?, updated_at=? where id=?", (title, ts, cid))
                else:
                    conn.execute("update conversations set updated_at=? where id=?", (ts, cid))
            else:
                conn.execute("update conversations set updated_at=? where id=?", (ts, cid))
        return mid

    def messages_for_api(self, cid):
        conv = self.conversation(cid)
        if not conv:
            return []
        messages = [{"role": m["role"], "content": m["content"]} for m in conv["messages"] if m["role"] in ("system", "user", "assistant")]
        if not messages or messages[0]["role"] != "system":
            messages.insert(0, {"role": "system", "content": DEFAULT_SYSTEM_PROMPT})
        return messages


class UIHandler(BaseHTTPRequestHandler):
    server_version = "DSGateway/0.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.log_date_time_string(), fmt % args))

    @property
    def state(self):
        return self.server.state

    def do_GET(self):
        if self.path.startswith("/openai/v1/"):
            return self.proxy_openai()
        if self.path == "/" or self.path == "/index.html":
            return self.serve_file(STATIC_DIR / "index.html", "text/html")
        if self.path.startswith("/static/"):
            name = self.path[len("/static/"):]
            path = (STATIC_DIR / name).resolve()
            if STATIC_DIR.resolve() not in path.parents:
                return self.send_error(404)
            mime = "application/javascript" if path.suffix == ".js" else "text/css"
            return self.serve_file(path, mime)
        if self.path == "/api/state":
            return write_json(self, {"hardware": self.state.hardware(), "models": self.state.model_status(), "stats": self.state.stats(), "conversations": self.state.conversations()})
        if self.path == "/api/stats":
            return write_json(self, self.state.stats())
        if self.path == "/api/settings":
            return write_json(self, public_settings())
        if self.path == "/api/exposure":
            return write_json(self, self.public_exposure())
        if self.path == "/api/conversations":
            return write_json(self, {"conversations": self.state.conversations()})
        if self.path.startswith("/api/conversations/"):
            cid = self.path.rsplit("/", 1)[-1]
            conv = self.state.conversation(cid)
            return write_json(self, conv or {"error": "not found"}, 200 if conv else 404)
        if self.path.startswith("/api/downloads/"):
            jid = self.path.rsplit("/", 1)[-1]
            return write_json(self, self.state.downloads.get(jid, {"error": "not found"}))
        return self.send_error(404)

    def serve_file(self, path, mime):
        if not path.exists():
            return self.send_error(404)
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("content-type", mime)
        self.send_header("cache-control", "no-store")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        try:
            if self.path.startswith("/openai/v1/"):
                return self.proxy_openai()
            if self.path == "/api/conversations":
                body = read_json(self)
                return write_json(self, self.state.create_conversation(body.get("title") or "New chat"))
            if self.path == "/api/models/download":
                body = read_json(self)
                return write_json(self, self.state.start_download(body.get("target")))
            if self.path == "/api/server/start":
                return write_json(self, self.state.start_server(read_json(self)))
            if self.path == "/api/server/stop":
                return write_json(self, self.state.stop_server())
            if self.path == "/api/settings":
                body = read_json(self)
                settings = load_settings()
                web = settings.setdefault("web", {})
                if body.get("clear_brave_api_key"):
                    web.pop("brave_api_key", None)
                else:
                    key = (body.get("brave_api_key") or "").strip()
                    if key:
                        web["brave_api_key"] = key
                save_settings(settings)
                return write_json(self, public_settings())
            if self.path == "/api/exposure":
                return write_json(self, self.public_exposure(update_exposure_settings(read_json(self))))
            if self.path == "/api/chat/stream":
                return self.chat_stream(read_json(self))
        except json.JSONDecodeError:
            return write_json(self, {"error": "invalid json"}, 400)
        except Exception as exc:
            return write_json(self, {"error": str(exc)}, 500)
        return self.send_error(404)

    def do_OPTIONS(self):
        if self.path.startswith("/openai/v1/"):
            self.send_response(204)
            self.send_header("access-control-allow-origin", "*")
            self.send_header("access-control-allow-methods", "GET,POST,OPTIONS")
            self.send_header("access-control-allow-headers", "authorization,content-type,x-api-key")
            self.end_headers()
            return
        return self.send_error(404)

    def do_DELETE(self):
        try:
            if self.path.startswith("/api/conversations/"):
                cid = self.path.rsplit("/", 1)[-1]
                deleted = self.state.delete_conversation(cid)
                return write_json(self, {"ok": deleted}, 200 if deleted else 404)
        except Exception as exc:
            return write_json(self, {"error": str(exc)}, 500)
        return self.send_error(404)

    def sse_send(self, event, data):
        payload = f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode("utf-8")
        self.wfile.write(payload)
        self.wfile.flush()

    def request_base_url(self):
        host = self.headers.get("host") or f"{self.server.server_address[0]}:{self.server.server_address[1]}"
        return f"http://{host}"

    def public_exposure(self, data=None):
        out = data or exposure_settings()
        ds4_ready = self.state.ping_server()["ok"]
        if not out["openai_enabled"]:
            status = "disabled"
            label = "Disabled"
            detail = "Endpoint is closed."
        elif out["require_api_key"] and not out["api_key_saved"]:
            status = "blocked"
            label = "Token required"
            detail = "Add or generate a bearer token before exposing."
        elif ds4_ready:
            status = "running"
            label = "Running"
            detail = "Endpoint is exposed and DS4 is responding."
        else:
            status = "waiting"
            label = "Waiting for model"
            detail = "Endpoint is exposed, but the DS4 model server is not responding yet."
        out["status"] = status
        out["status_label"] = label
        out["status_detail"] = detail
        out["ds4_ready"] = ds4_ready
        out["openai_base_url"] = self.request_base_url() + "/openai/v1"
        out["direct_ds4_base_url"] = f"http://{self.state.host}:{self.state.port}/v1"
        return out

    def exposure_allowed(self):
        exposure = exposure_settings()
        if not exposure["openai_enabled"]:
            return False, "OpenAI exposure is disabled."
        if exposure["require_api_key"]:
            expected = exposure_api_key()
            if not expected:
                return False, "OpenAI exposure requires an API key, but none is saved."
            auth = self.headers.get("authorization") or ""
            supplied = self.headers.get("x-api-key") or ""
            if auth.lower().startswith("bearer "):
                supplied = auth.split(None, 1)[1].strip()
            if supplied != expected:
                return False, "Unauthorized."
        return True, ""

    def proxy_openai(self):
        allowed, reason = self.exposure_allowed()
        if not allowed:
            return write_json(self, {"error": reason}, 403)
        if not self.state.ping_server()["ok"]:
            return write_json(self, {"error": "DS4 server is not ready."}, 503)
        length = int(self.headers.get("content-length", "0") or "0")
        body = self.rfile.read(length) if length else None
        target_path = self.path.replace("/openai/v1", "/v1", 1)
        target_url = f"http://{self.state.host}:{self.state.port}{target_path}"
        headers = {"content-type": self.headers.get("content-type", "application/json")}
        req = urlrequest.Request(target_url, data=body, headers=headers, method=self.command)
        try:
            with urlrequest.urlopen(req, timeout=86400) as resp:
                self.send_response(resp.status)
                self.send_header("content-type", resp.headers.get("content-type", "application/json"))
                self.send_header("cache-control", "no-store")
                self.send_header("access-control-allow-origin", "*")
                self.end_headers()
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except HTTPError as exc:
            detail = exc.read()
            self.send_response(exc.code)
            self.send_header("content-type", exc.headers.get("content-type", "application/json"))
            self.send_header("cache-control", "no-store")
            self.send_header("access-control-allow-origin", "*")
            self.end_headers()
            self.wfile.write(detail)
        except URLError as exc:
            return write_json(self, {"error": f"Cannot reach DS4 server: {exc}"}, 502)

    def post_ds4_chat(self, req, timeout=86400):
        payload = json.dumps(req).encode("utf-8")
        http_req = urlrequest.Request(
            f"http://{self.state.host}:{self.state.port}/v1/chat/completions",
            data=payload,
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urlrequest.urlopen(http_req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))

    def stream_ds4_chat(self, req, cid):
        payload = json.dumps(req).encode("utf-8")
        assistant = []
        reasoning = []
        usage = None
        try:
            http_req = urlrequest.Request(
                f"http://{self.state.host}:{self.state.port}/v1/chat/completions",
                data=payload,
                headers={"content-type": "application/json"},
                method="POST",
            )
            with urlrequest.urlopen(http_req, timeout=86400) as resp:
                for raw in resp:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    obj = json.loads(data)
                    if obj.get("usage"):
                        usage = obj["usage"]
                        self.sse_send("usage", usage)
                        continue
                    choices = obj.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    part = delta.get("content") or ""
                    rpart = delta.get("reasoning_content") or delta.get("reasoning") or ""
                    if rpart:
                        reasoning.append(rpart)
                        self.sse_send("reasoning", {"text": rpart})
                    if part:
                        assistant.append(part)
                        self.sse_send("delta", {"text": part})
            final = "".join(assistant).strip()
            self.state.save_message(cid, "assistant", final, "".join(reasoning).strip() or None, usage)
            self.sse_send("done", {"conversation_id": cid, "usage": usage})
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            self.sse_send("error", {"error": detail or str(exc)})
        except URLError as exc:
            self.sse_send("error", {"error": f"Cannot reach DS4 server: {exc}"})
        except Exception as exc:
            self.sse_send("error", {"error": str(exc)})

    def chat_with_tools(self, req, cid):
        messages = [dict(msg) for msg in req["messages"]]
        max_tokens = int(req.get("max_tokens") or 8192)
        used_tools = False
        usage = None
        for round_idx in range(4):
            tool_req = {
                **req,
                "messages": messages,
                "stream": False,
                "tools": WEB_TOOL_SCHEMAS,
                "tool_choice": "auto",
                "max_tokens": max_tokens,
            }
            tool_req.pop("stream_options", None)
            self.sse_send("tool", {"status": "thinking", "round": round_idx + 1})
            obj = self.post_ds4_chat(tool_req)
            usage = obj.get("usage") or usage
            if obj.get("usage"):
                self.sse_send("usage", obj["usage"])
            choices = obj.get("choices") or []
            if not choices:
                self.sse_send("error", {"error": "DS4 returned no choices."})
                return
            message = choices[0].get("message") or {}
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                final = (message.get("content") or "").strip()
                reasoning = (message.get("reasoning_content") or message.get("reasoning") or "").strip()
                if reasoning:
                    self.sse_send("reasoning", {"text": reasoning})
                if final:
                    self.sse_send("delta", {"text": final})
                else:
                    final = "I did not receive a text response from DS4."
                    self.sse_send("delta", {"text": final})
                self.state.save_message(cid, "assistant", final, reasoning or None, usage)
                self.sse_send("done", {"conversation_id": cid, "usage": usage, "tools_used": used_tools})
                return

            used_tools = True
            assistant_tool_message = {
                "role": "assistant",
                "content": message.get("content") or "",
                "tool_calls": [],
            }
            for call in tool_calls:
                if not call.get("id"):
                    call["id"] = "call_" + uuid.uuid4().hex[:12]
                call["type"] = call.get("type") or "function"
                assistant_tool_message["tool_calls"].append(call)
            messages.append(assistant_tool_message)

            for call in assistant_tool_message["tool_calls"]:
                fn = ((call.get("function") or {}).get("name") or "").strip()
                self.sse_send("tool", {"status": "calling", "name": fn})
                result = execute_tool_call(call)
                content = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
                if len(content) > 24000:
                    content = content[:24000] + "...[truncated]"
                messages.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": content,
                })
                self.sse_send("tool", {
                    "status": "result",
                    "name": fn,
                    "ok": bool(result.get("ok")),
                })

        messages.append({
            "role": "user",
            "content": "Use the available tool results above to answer now. Cite source URLs when web results were used.",
        })
        final_req = {**req, "messages": messages, "stream": True, "stream_options": {"include_usage": True}}
        self.stream_ds4_chat(final_req, cid)

    def chat_stream(self, body):
        cid = body.get("conversation_id")
        if not cid or not self.state.conversation(cid):
            cid = self.state.create_conversation("New chat")["id"]
        text = (body.get("message") or "").strip()
        if not text:
            return write_json(self, {"error": "empty message"}, 400)
        self.state.save_message(cid, "user", text)
        messages = self.state.messages_for_api(cid)
        model = body.get("model") or "deepseek-v4-flash"
        think = body.get("think") or "high"
        if think == "none":
            model = "deepseek-chat"
        req = {
            "model": model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_tokens": int(body.get("max_tokens") or 8192),
        }
        if think == "max":
            req["reasoning_effort"] = "max"
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        self.end_headers()
        self.sse_send("meta", {"conversation_id": cid})
        ping = self.state.ping_server()
        if not ping["ok"]:
            self.sse_send("error", {"error": "DS4 server is not ready yet. Click Start and wait for the status to show online."})
            return
        tools_requested = bool(body.get("tools_enabled", True)) and web_tools_enabled()
        if tools_requested:
            try:
                return self.chat_with_tools(req, cid)
            except HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")
                self.sse_send("error", {"error": detail or str(exc)})
                return
            except URLError as exc:
                self.sse_send("error", {"error": f"Cannot reach DS4 server: {exc}"})
                return
            except Exception as exc:
                self.sse_send("error", {"error": str(exc)})
                return
        self.stream_ds4_chat(req, cid)


def main():
    parser = argparse.ArgumentParser(description="DS Gateway for DwarfStar DS4")
    parser.add_argument("--ds4-root", default=os.environ.get("DS4_ROOT", str(APP_DIR.parent / "ds4")))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--ds4-server-port", type=int, default=8000)
    args = parser.parse_args()

    state = BridgeState(args.ds4_root, "127.0.0.1", args.ds4_server_port)
    httpd = ThreadingHTTPServer((args.host, args.port), UIHandler)
    httpd.state = state
    print(f"DS Gateway: http://{args.host}:{args.port}")
    print(f"DS4 root: {state.ds4_root}")

    def shutdown(signum, frame):
        del signum, frame
        try:
            state.stop_server()
        finally:
            httpd.shutdown()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
