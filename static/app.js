const state = {
  currentConversation: null,
  pressure: [],
  downloadJob: null,
  busy: false,
  serverOnline: false,
  maxTokensTouched: false,
  settingsOpen: false,
  exposureOpenAIEnabled: false,
};

const $ = (id) => document.getElementById(id);

function fmtBytes(bytes) {
  if (!bytes) return "-";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${value.toFixed(i < 2 ? 0 : 1)} ${units[i]}`;
}

function escapeText(text) {
  return text.replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  }[ch]));
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: {"content-type": "application/json", ...(options.headers || {})},
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

function renderConversations(items) {
  const list = $("conversation-list");
  list.innerHTML = "";
  for (const conv of items) {
    const row = document.createElement("div");
    row.className = `conversation ${conv.id === state.currentConversation ? "active" : ""}`;
    row.innerHTML = `
      <button class="conversation-open" title="${escapeText(conv.title)}">
        <strong>${escapeText(conv.title)}</strong>
        <small>${escapeText(conv.last_message || "")}</small>
      </button>
      <button class="conversation-delete" aria-label="Delete chat" title="Delete chat">
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M3 6h18"></path>
          <path d="M8 6V4h8v2"></path>
          <path d="M6 6l1 15h10l1-15"></path>
          <path d="M10 10v7"></path>
          <path d="M14 10v7"></path>
        </svg>
      </button>`;
    row.querySelector(".conversation-open").onclick = () => loadConversation(conv.id);
    row.querySelector(".conversation-delete").onclick = () => deleteConversation(conv.id);
    list.appendChild(row);
  }
}

function appendMessage(role, content, reasoning = "") {
  const wrap = document.createElement("article");
  wrap.className = `message ${role}`;
  wrap.innerHTML = `
    <div class="avatar">${role === "assistant" ? "D" : "U"}</div>
    <div class="bubble">
      ${reasoning ? `<div class="reasoning">${escapeText(reasoning)}</div>` : ""}
      <div class="content">${escapeText(content)}</div>
    </div>`;
  $("messages").appendChild(wrap);
  $("messages").scrollTop = $("messages").scrollHeight;
  return wrap.querySelector(".content");
}

function setResponding(node, active) {
  const bubble = node.closest(".bubble");
  if (!bubble) return;
  let indicator = bubble.querySelector(".typing");
  if (active && !indicator) {
    indicator = document.createElement("div");
    indicator.className = "typing";
    indicator.innerHTML = "<span></span><span></span><span></span>";
    bubble.appendChild(indicator);
  } else if (!active && indicator) {
    indicator.remove();
  }
}

function clearToolStatus(node) {
  const bubble = node.closest(".bubble");
  if (!bubble) return;
  const note = bubble.querySelector(".tool-status");
  if (note) note.remove();
}

function updateToolStatus(node, data) {
  const bubble = node.closest(".bubble");
  if (!bubble) return;
  let note = bubble.querySelector(".tool-status");
  if (!note) {
    note = document.createElement("div");
    note.className = "tool-status";
    bubble.insertBefore(note, node);
  }
  let text = "Thinking";
  if (data.status === "thinking") {
    text = "Checking sources";
  } else if (data.status === "calling") {
    text = data.name === "fetch_url" ? "Reading page" : "Searching web";
  } else if (data.status === "result") {
    text = data.ok ? "Reviewing results" : "Trying another source";
  }
  note.textContent = text;
  $("messages").scrollTop = $("messages").scrollHeight;
}

function renderSettings(settings) {
  const web = settings.web || {};
  const saved = Boolean(web.brave_api_key_saved);
  const source = web.brave_api_key_source;
  $("brave-key-status").textContent = saved ? `Brave key saved${source === "env" ? " from environment" : ""}` : "Not saved";
  $("brave-api-key").placeholder = saved ? "Saved key unchanged" : "Paste API key";
}

function renderExposure(exposure) {
  state.exposureOpenAIEnabled = Boolean(exposure.openai_enabled);
  $("expose-auth").checked = Boolean(exposure.require_api_key);
  $("openai-base-url").textContent = exposure.openai_base_url || "-";
  $("expose-api-key").placeholder = exposure.api_key_saved ? "Saved token unchanged" : "Bearer token";
  $("exposure-runtime").textContent = exposure.status_label || "Disabled";
  $("exposure-runtime").className = `runtime-pill ${exposure.status || "disabled"}`;
  $("start-exposure").disabled = state.exposureOpenAIEnabled;
  $("stop-exposure").disabled = !state.exposureOpenAIEnabled;
  const auth = exposure.require_api_key ? (exposure.api_key_saved ? "token saved" : "token missing") : "no token";
  $("exposure-status").textContent = `${exposure.status_detail || "OpenAI endpoint"} · ${auth}`;
}

async function loadSettings() {
  const settings = await api("/api/settings");
  renderSettings(settings);
  const exposure = await api("/api/exposure");
  renderExposure(exposure);
}

async function refreshExposureStatus() {
  if (!state.settingsOpen) return;
  try {
    renderExposure(await api("/api/exposure"));
  } catch {}
}

function openSettings() {
  state.settingsOpen = true;
  document.querySelector(".workspace").classList.add("settings-open");
  $("settings-page").classList.remove("hidden");
  $("settings-save-state").textContent = "";
  $("brave-api-key").value = "";
  loadSettings();
}

function closeSettings() {
  state.settingsOpen = false;
  document.querySelector(".workspace").classList.remove("settings-open");
  $("settings-page").classList.add("hidden");
}

async function saveSettings(ev) {
  ev.preventDefault();
  const key = $("brave-api-key").value.trim();
  if (!key) {
    $("settings-save-state").textContent = "No changes";
    return;
  }
  $("settings-save-state").textContent = "Saving";
  const settings = await api("/api/settings", {
    method: "POST",
    body: JSON.stringify({brave_api_key: key}),
  });
  $("brave-api-key").value = "";
  $("settings-save-state").textContent = "Saved";
  renderSettings(settings);
}

async function clearBraveKey() {
  $("settings-save-state").textContent = "Clearing";
  const settings = await api("/api/settings", {
    method: "POST",
    body: JSON.stringify({clear_brave_api_key: true}),
  });
  $("brave-api-key").value = "";
  $("settings-save-state").textContent = "Cleared";
  renderSettings(settings);
}

async function saveExposure(ev) {
  ev.preventDefault();
  $("exposure-save-state").textContent = "Saving";
  const body = {
    openai_enabled: state.exposureOpenAIEnabled,
    require_api_key: $("expose-auth").checked,
  };
  const key = $("expose-api-key").value.trim();
  if (key) body.api_key = key;
  const exposure = await api("/api/exposure", {
    method: "POST",
    body: JSON.stringify(body),
  });
  $("expose-api-key").value = "";
  $("exposure-save-state").textContent = "Saved";
  renderExposure(exposure);
}

async function startExposure() {
  $("exposure-save-state").textContent = "Starting";
  const body = {
    openai_enabled: true,
    require_api_key: $("expose-auth").checked,
  };
  const key = $("expose-api-key").value.trim();
  if (key) body.api_key = key;
  const exposure = await api("/api/exposure", {
    method: "POST",
    body: JSON.stringify(body),
  });
  $("expose-api-key").value = "";
  $("exposure-save-state").textContent = "Started";
  renderExposure(exposure);
}

async function stopExposure() {
  $("exposure-save-state").textContent = "Stopping";
  const exposure = await api("/api/exposure", {
    method: "POST",
    body: JSON.stringify({
      openai_enabled: false,
      require_api_key: $("expose-auth").checked,
    }),
  });
  $("exposure-save-state").textContent = "Stopped";
  renderExposure(exposure);
}

async function generateExposureKey() {
  $("exposure-save-state").textContent = "Generating";
  const exposure = await api("/api/exposure", {
    method: "POST",
    body: JSON.stringify({
      openai_enabled: state.exposureOpenAIEnabled,
      require_api_key: true,
      generate_api_key: true,
    }),
  });
  $("expose-api-key").value = exposure.new_api_key || "";
  $("exposure-save-state").textContent = "Generated";
  renderExposure(exposure);
  $("expose-api-key").select();
}

async function clearExposureKey() {
  $("exposure-save-state").textContent = "Clearing";
  const exposure = await api("/api/exposure", {
    method: "POST",
    body: JSON.stringify({
      openai_enabled: state.exposureOpenAIEnabled,
      require_api_key: $("expose-auth").checked,
      clear_api_key: true,
    }),
  });
  $("expose-api-key").value = "";
  $("exposure-save-state").textContent = "Cleared";
  renderExposure(exposure);
}

async function copyOpenAIUrl() {
  await navigator.clipboard.writeText($("openai-base-url").textContent);
  $("exposure-save-state").textContent = "Copied";
}

async function loadConversation(id) {
  closeSettings();
  const conv = await api(`/api/conversations/${id}`);
  state.currentConversation = id;
  $("messages").innerHTML = "";
  for (const msg of conv.messages) {
    appendMessage(msg.role, msg.content, msg.reasoning || "");
  }
  refreshState();
}

async function newConversation() {
  closeSettings();
  const conv = await api("/api/conversations", {method: "POST", body: JSON.stringify({title: "New chat"})});
  state.currentConversation = conv.id;
  $("messages").innerHTML = "";
  refreshState();
}

async function deleteConversation(id) {
  await api(`/api/conversations/${id}`, {method: "DELETE"});
  if (state.currentConversation === id) {
    state.currentConversation = null;
    $("messages").innerHTML = "";
  }
  const data = await api("/api/conversations");
  renderConversations(data.conversations);
  if (!state.currentConversation && data.conversations[0]) {
    await loadConversation(data.conversations[0].id);
  }
}

function renderModels(models, recommendation) {
  const select = $("model-select");
  const selected = select.value;
  select.innerHTML = "";
  for (const model of models.filter((m) => m.main)) {
    const opt = document.createElement("option");
    opt.value = model.target;
    opt.textContent = `${model.label}${model.installed ? " installed" : ""}`;
    select.appendChild(opt);
  }
  select.value = selected || recommendation.target;
  const rec = models.find((m) => m.target === recommendation.target);
  $("recommended-model").textContent = rec ? rec.label : recommendation.label;
  $("recommendation-reason").textContent = recommendation.reason;
  $("download-model").textContent = models.find((m) => m.target === select.value)?.installed ? "Downloaded" : "Download";
  $("download-model").disabled = Boolean(models.find((m) => m.target === select.value)?.installed);
}

function drawPressure(value) {
  state.pressure.push(value || 0);
  if (state.pressure.length > 44) state.pressure.shift();
  const canvas = $("pressure-chart");
  const ctx = canvas.getContext("2d");
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  ctx.strokeStyle = "#d9ddd7";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(0, h - 0.5);
  ctx.lineTo(w, h - 0.5);
  ctx.stroke();
  ctx.strokeStyle = value > 0.82 ? "#b42318" : value > 0.68 ? "#a16207" : "#0f766e";
  ctx.lineWidth = 2;
  ctx.beginPath();
  state.pressure.forEach((p, i) => {
    const x = state.pressure.length === 1 ? w : (i / (state.pressure.length - 1)) * w;
    const y = h - Math.max(1, p * h);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
}

function renderStats(stats, hardware) {
  const mem = stats.memory;
  const available = mem.available_bytes;
  const total = mem.total_bytes;
  $("memory-stat").textContent = `${fmtBytes(available)} avail / ${fmtBytes(total)}`;
  $("model-stat").textContent = fmtBytes(stats.model_rss_bytes);
  $("disk-stat").textContent = `${fmtBytes(stats.disk.free)} free`;
  $("load-stat").textContent = String(stats.load_1m ?? "-");
  $("backend-label").textContent = hardware.backend || "-";
  state.serverOnline = Boolean(stats.server.api.ok);
  $("server-pill").textContent = state.serverOnline ? "online" : "offline";
  $("start-server").textContent = state.serverOnline ? "Restart" : "Start";
  $("start-server").disabled = false;
  $("stop-server").disabled = !state.serverOnline;
  $("send").disabled = state.busy || !state.serverOnline;
  applyOptimizedMaxTokens(stats);
  drawPressure(mem.pressure);
}

function activeContextLength(stats) {
  const models = stats?.server?.api?.models?.data || [];
  return Number(models[0]?.context_length || models[0]?.top_provider?.context_length || 0);
}

function optimizedMaxTokens(stats) {
  const ctx = activeContextLength(stats);
  if (!ctx) return 8192;
  const raw = Math.round((ctx * 0.08) / 1024) * 1024;
  return Math.max(4096, Math.min(16384, raw || 8192));
}

function applyOptimizedMaxTokens(stats) {
  if (state.maxTokensTouched) return;
  const input = $("max-tokens");
  input.value = String(optimizedMaxTokens(stats));
}

async function refreshState() {
  const data = await api("/api/state");
  renderConversations(data.conversations);
  renderModels(data.models, data.hardware.recommendation);
  renderStats(data.stats, data.hardware);
}

async function pollStats() {
  try {
    const stats = await api("/api/stats");
    const hardware = {backend: $("backend-label").textContent};
    renderStats(stats, hardware);
  } catch {}
}

async function startServer() {
  const st = await api("/api/state");
  const rec = st.hardware.recommendation;
  const selected = $("model-select").value;
  const model = st.models.find((m) => m.target === selected);
  $("start-server").disabled = true;
  $("start-server").textContent = state.serverOnline ? "Restarting" : "Loading model";
  try {
    const body = {
      model_path: model?.path,
      ctx: rec.ctx,
      backend: st.hardware.backend,
      ssd_streaming: rec.streaming,
      ssd_streaming_cache: rec.streaming ? "32GB" : "",
      readiness_timeout_sec: 240,
      restart: state.serverOnline,
    };
    const out = await api("/api/server/start", {method: "POST", body: JSON.stringify(body)});
    if (!out.ok) alert(out.error || "DS4 server did not start");
  } catch (err) {
    alert(`Could not start DS4 server: ${err.message || err}`);
  } finally {
    $("start-server").disabled = false;
    refreshState();
  }
}

async function stopServer() {
  $("stop-server").disabled = true;
  $("stop-server").textContent = "Stopping";
  try {
    const out = await api("/api/server/stop", {method: "POST", body: JSON.stringify({})});
    if (!out.ok) alert(out.error || "DS4 server did not stop");
  } catch (err) {
    alert(`Could not stop DS4 server: ${err.message || err}`);
  } finally {
    $("stop-server").textContent = "Stop";
    refreshState();
  }
}

async function startDownload() {
  const target = $("model-select").value;
  const out = await api("/api/models/download", {method: "POST", body: JSON.stringify({target})});
  if (!out.ok) {
    alert(out.error || "Download failed to start");
    return;
  }
  state.downloadJob = out.job.id;
  $("download-panel").classList.remove("hidden");
  $("download-title").textContent = target;
  pollDownload();
}

async function pollDownload() {
  if (!state.downloadJob) return;
  const job = await api(`/api/downloads/${state.downloadJob}`);
  $("download-state").textContent = job.status || "";
  $("download-log").textContent = (job.lines || []).join("\n");
  if (job.status === "running") {
    setTimeout(pollDownload, 1200);
  } else {
    refreshState();
  }
}

async function sendMessage() {
  if (state.busy) return;
  if (!state.serverOnline) {
    alert("DS4 server is not online yet. Click Start and wait for the status to show online.");
    return;
  }
  const prompt = $("prompt").value.trim();
  if (!prompt) return;
  if (!state.currentConversation) {
    await newConversation();
  }
  state.busy = true;
  $("send").disabled = true;
  $("prompt").value = "";
  appendMessage("user", prompt);
  const assistantNode = appendMessage("assistant", "");
  setResponding(assistantNode, true);
  let assistantText = "";
  try {
    const res = await fetch("/api/chat/stream", {
      method: "POST",
      headers: {"content-type": "application/json"},
      body: JSON.stringify({
        conversation_id: state.currentConversation,
        message: prompt,
        think: $("think-mode").value,
        max_tokens: Number($("max-tokens").value || 8192),
        tools_enabled: $("web-tools").checked,
      }),
    });
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const {value, done} = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, {stream: true});
      const events = buffer.split("\n\n");
      buffer = events.pop();
      for (const eventText of events) {
        const dataLine = eventText.split("\n").find((line) => line.startsWith("data: "));
        const eventLine = eventText.split("\n").find((line) => line.startsWith("event: "));
        if (!dataLine) continue;
        const event = eventLine ? eventLine.slice(7) : "message";
        const data = JSON.parse(dataLine.slice(6));
        if (event === "meta") state.currentConversation = data.conversation_id;
        if (event === "delta") {
          setResponding(assistantNode, false);
          clearToolStatus(assistantNode);
          assistantText += data.text;
          assistantNode.textContent = assistantText;
          $("messages").scrollTop = $("messages").scrollHeight;
        }
        if (event === "tool") {
          updateToolStatus(assistantNode, data);
        }
        if (event === "error") {
          setResponding(assistantNode, false);
          clearToolStatus(assistantNode);
          assistantNode.textContent = data.error;
        }
      }
    }
  } finally {
    setResponding(assistantNode, false);
    clearToolStatus(assistantNode);
    state.busy = false;
    $("send").disabled = false;
    refreshState();
  }
}

$("new-chat").onclick = newConversation;
$("settings-nav").onclick = openSettings;
$("settings-close").onclick = closeSettings;
$("settings-form").onsubmit = saveSettings;
$("clear-brave-key").onclick = clearBraveKey;
$("exposure-form").onsubmit = saveExposure;
$("start-exposure").onclick = startExposure;
$("stop-exposure").onclick = stopExposure;
$("generate-expose-key").onclick = generateExposureKey;
$("clear-expose-key").onclick = clearExposureKey;
$("copy-openai-url").onclick = copyOpenAIUrl;
$("start-server").onclick = startServer;
$("stop-server").onclick = stopServer;
$("download-model").onclick = startDownload;
$("send").onclick = sendMessage;
$("prompt").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.shiftKey) {
    ev.preventDefault();
    sendMessage();
  }
});
$("prompt").addEventListener("input", () => {
  $("prompt").style.height = "44px";
  $("prompt").style.height = `${Math.min(180, $("prompt").scrollHeight)}px`;
});
$("max-tokens").addEventListener("input", () => {
  state.maxTokensTouched = true;
});

refreshState().then(async () => {
  const data = await api("/api/conversations");
  if (data.conversations[0]) loadConversation(data.conversations[0].id);
});
setInterval(pollStats, 2500);
setInterval(refreshExposureStatus, 2500);
