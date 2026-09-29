const byId = (id) => document.getElementById(id);
const canvas = byId("network");
const ctx = canvas.getContext("2d");
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

let atlas = null;
let ranking = [];
let selectedId = null;
let recall = null;
const recallTimers = [];
const recallMatchMs = 650, recallHopMs = 620;
let feedbackEvent = null;
let feedbackBusy = false;
let feedbackTimer;
const weightTransitions = new Map();
let activeFilter = "all";
let currentQuery = "";
let searchVersion = 0;
let toastTimer;
let graphNodes = new Map();
let graphLayers = [];
let layoutIsMobile = null;
let ambientPaths = [];
let visualStrands = [];
let hoveredId = null;
let transform = {scale: 1, x: 0, y: 0};
let pointer = null;
const staticCanvas = document.createElement("canvas");
let staticDirty = true;
let projectPaths = new Map();
// Relay tones (excitatory / neutral / inhibitory look) are decorative only.
const relayTones = {amber: "#b9ad8a", violet: "#a39caf", rose: "#b4919e"};
let synapseCurves = [];
let learning = false;
let converged = false;
let learnTimer;
const flashes = new Map();
let ghosts = [];
const boostColor = "#efc987";
const suppressColor = "#e58aa0";
// Same signed learning signal the server derives from a memory's manual feedback.
const labelValue = (feedback) => Math.max(-1, Math.min(1, feedback.boost / 3 + (feedback.pinned ? .34 : 0)));
const memoryTone = (signal) => signal > .05 ? boostColor : signal < -.05 ? suppressColor : "#aaa2db";
const hash01 =(seed) => { const value = Math.sin(seed * 127.1 + 78.233) * 43758.5453; return value - Math.floor(value); };

const kindLabels = {profile: "用户画像", preference: "偏好", tip: "经验", task: "任务", overview: "概览"};

async function request(path, options) {
  const response = await fetch(path, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `请求失败：${response.status}`);
  return body;
}

function notify(message) {
  const toast = byId("toast");
  toast.textContent = message;
  toast.classList.add("is-visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("is-visible"), 2600);
}

function currentMemory() {
  return atlas?.memories.find((item) => item.id === selectedId) || null;
}

function scoreFor(id) {
  return ranking.find((item) => item.id === id) || null;
}

function memoryForNode(node) {
  if (!node || !atlas?.memories.length) return null;
  if (node.kind === "memory") return atlas.memories.find((item) => item.id === node.id) || null;
  if (node.kind !== "relay") return null;
  const group = atlas.memories.filter((item) => item.project === node.project);
  const choices = group.length ? group : atlas.memories;
  return choices[(node.layer * 31 + node.slot * 7) % choices.length];
}

function displayTitle(memory) {
  const correction = memory.feedback.correction.trim();
  return correction ? `已修正：${correction.slice(0, 46)}` : memory.title;
}

function setText(id, value) {
  byId(id).textContent = value;
}

function updateRecallPhase(timestamp) {
  const panel = byId("recall-panel");
  panel.hidden = !currentQuery;
  byId("quick-exit-recall").hidden = !currentQuery;
  if (!currentQuery || !recall) return;
  let phase = recall.phase;
  if (["matching", "associating", "settled"].includes(phase)) {
    const elapsed = timestamp - recall.startedAt;
    const duration = recall.plan.edges.size ? recallMatchMs + 2 * recallHopMs + 350 : recallMatchMs;
    phase = elapsed >= duration ? "settled" : elapsed >= recallMatchMs ? "associating" : "matching";
  }
  if (panel.dataset.phase === phase && recall.rendered) return;
  recall.phase = phase;
  recall.rendered = true;
  panel.dataset.phase = phase;
  const labels = {loading: "检索中", matching: "定位匹配", associating: "联想展开", settled: "回忆完成", empty: "没有找到相关记忆", error: "检索未完成"};
  setText("recall-phase", labels[phase]);
  const plan = recall.plan;
  setText("recall-summary", phase === "loading" ? "正在检索本地记忆…" : phase === "empty" ? "换个关键词，或检查已导入的来源。" : phase === "error" ? recall.error
    : `找到 ${plan.direct.size} 条直接匹配，${plan.associatedCount} 条关联背景。`);
  setText("recall-caption", phase === "matching" ? "直接匹配先亮起，再沿真实关联展开。" : phase === "associating" ? "信号正沿真实连接传递。"
    : phase === "settled" ? plan.shown ? `保留 ${plan.shown} 条重点关联路径；点击节点查看记忆。` : "已定位直接匹配；点击节点查看记忆。" : "记忆与来源都保留在本机。");
  byId("recall-results").disabled = ["loading", "empty", "error"].includes(phase);
  document.querySelectorAll("[data-recall-step]").forEach((step) => {
    const order = ["matching", "associating", "settled"];
    step.classList.toggle("is-current", step.dataset.recallStep === phase);
    step.classList.toggle("is-done", order.indexOf(step.dataset.recallStep) < order.indexOf(phase));
  });
}

function startRecall(animate) {
  recallTimers.splice(0).forEach(clearTimeout);
  if (!currentQuery) { recall = null; byId("recall-panel").hidden = true; return; }
  const eligible = new Set(atlas.memories.filter((item) => !item.lifecycle?.dormant && !item.feedback.archived).map((item) => item.id));
  const plan = MemoryRecall.plan(ranking, atlas.network.synapses.filter((edge) => eligible.has(edge.a) && eligible.has(edge.b)));
  recall = {plan, phase: ranking.length ? "matching" : "empty", startedAt: performance.now() - (!animate || reducedMotion ? 3000 : 0)};
  updateRecallPhase(performance.now());
  if (animate && !reducedMotion && ranking.length) {
    for (const delay of [recallMatchMs + 10, recallMatchMs + 2 * recallHopMs + 360]) {
      recallTimers.push(setTimeout(() => drawGraph(performance.now()), delay));
    }
  }
}

function recallArrival(id) {
  const hop = recall?.plan?.arrivals.get(id);
  return hop == null ? Infinity : hop === 0 ? 0 : recallMatchMs + hop * recallHopMs;
}

function recallActivation(timestamp) {
  const activation = new Map();
  if (!currentQuery || !recall?.plan) return activation;
  const elapsed = timestamp - recall.startedAt;
  for (const [id] of recall.plan.arrivals) {
    if (elapsed >= recallArrival(id)) activation.set(id, scoreFor(id)?.activation || .25);
  }
  return activation;
}

function renderFeedbackNote() {
  const note = byId("feedback-note");
  const visible = feedbackEvent?.id === selectedId;
  note.hidden = !visible;
  if (visible) { note.textContent = feedbackEvent.message; note.dataset.action = feedbackEvent.action; }
}

function renderStats() {
  if (!atlas) return;
  setText("memory-count", atlas.count);
  setText("project-count", atlas.hubs.length);
  setText("link-count", atlas.network.stats.connections);
  setText("source-status", atlas.source_exists ? `已读取 ${atlas.count} 条本地记忆${atlas.dormant_count ? ` · ${atlas.dormant_count} 条已消散` : ""} · ${atlas.source}` : "尚无可读取的已选来源");
  if (document.activeElement !== byId("decay-days")) byId("decay-days").value = atlas.settings.decay_days;
  setText("decay-summary", `${atlas.settings.decay_days ? `当前 ${atlas.settings.decay_days} 天` : "消散已关闭"} · ${atlas.dormant_count} 条已消散`);
  setText("active-source-label", atlas.source || "未选择");
  byId("graph-empty").hidden = atlas.count > 0;
}

function updateProbe() {
  const probe = byId("memory-probe");
  const node = graphNodes.get(hoveredId);
  const memory = memoryForNode(node);
  probe.hidden = !memory;
  canvas.parentElement.parentElement.classList.toggle("is-probing", Boolean(memory));
  if (!memory) return;
  setText("probe-heading", node.kind === "relay" ? "ASSOCIATED MEMORY / DISPLAY RELAY" : "LIVE MEMORY PROBE");
  setText("probe-id", `ID: hl${node.layer}.${String(node.slot + 1).padStart(2, "0")} | Ref: ${memory.id.slice(0, 10)}`);
  setText("probe-kind", `Kind: ${memory.kind}`);
  setText("probe-group", `Group: ${memory.project}`);
  const activation = currentQuery ? scoreFor(memory.id)?.activation || 0 : memory.id === selectedId ? 1 : 0;
  setText("probe-activation", `Activation: ${activation.toFixed(3)} · Weight: ${memory.strength.toFixed(2)}`);
  setText("probe-text", memory.feedback.correction.trim() || memory.text);
}

function updateSourceSelectionCount() {
  const selected = [...byId("source-list").querySelectorAll("input[type=checkbox]:checked")];
  setText("source-selection-count", `已勾选 ${selected.length} 个来源`);
}

function renderSources(data) {
  const list = byId("source-list");
  list.replaceChildren();
  for (const source of data.sources) {
    const row = document.createElement("div");
    row.className = "source-row";
    const label = document.createElement("label");
    label.className = "source-row-main";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = source.id;
    checkbox.checked = source.selected;
    checkbox.disabled = !source.available;
    const title = document.createElement("span");
    title.className = "source-row-title";
    title.textContent = source.name;
    const badge = document.createElement("span");
    badge.className = `source-badge${source.available ? "" : " is-empty"}`;
    badge.textContent = source.available ? `${source.file_count} FILES` : source.installed ? "已检测 · 0 FILES" : "未检测到";
    title.append(badge);
    const meta = document.createElement("span");
    meta.className = "source-row-meta";
    meta.textContent = `${source.description} · ${source.status}`;
    label.append(checkbox, title, meta);
    const location = document.createElement("span");
    location.className = "source-row-meta source-row-examples";
    location.textContent = `位置：${source.path}`;
    label.append(location);
    if (source.examples?.length) {
      const examples = document.createElement("span");
      examples.className = "source-row-meta source-row-examples";
      examples.textContent = `文件示例：${source.examples.map((path) => path.split(/[\\/]/).slice(-2).join("/")).join("、")}`;
      label.append(examples);
    }
    row.append(label);
    if (source.custom) {
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "source-remove";
      remove.dataset.id = source.id;
      remove.setAttribute("aria-label", `移除来源 ${source.name}`);
      remove.textContent = "×";
      row.append(remove);
    }
    list.append(row);
  }
  const note = byId("source-scan-note");
  note.textContent = `已检查 ${data.workspace_count ?? 0} 个项目目录（桌面、文档、常见开发文件夹，以及 Codex 记录过的项目）。`
    + "没找到你的记忆？在下方添加文件夹，或用 --workspace 指定项目目录后重新启动。";
  updateSourceSelectionCount();
}

function renderResults() {
  const list = byId("result-list");
  list.replaceChildren();
  const memoryById = new Map(atlas.memories.map((item) => [item.id, item]));
  const candidates = activeFilter === "dormant" ? atlas.memories.filter((item) => item.lifecycle?.dormant).map((item) => ({id: item.id, score: 0, reason: "已消散 · 打开即可重新启用"})) : ranking;
  const results = candidates.filter((result) => {
    const memory = memoryById.get(result.id);
    if (!memory) return false;
    if (activeFilter === "dormant") return true;
    if (activeFilter === "preference") return ["preference", "profile"].includes(memory.kind);
    return activeFilter === "all" || memory.kind === activeFilter;
  });
  setText("results-label", activeFilter === "dormant" ? "DORMANT MEMORIES / 全部已消散" : currentQuery ? "MATCHED MEMORIES" : "TOP MEMORIES");
  setText("results-count", `${results.length} 条`);

  if (!results.length) {
    const empty = document.createElement("p");
    empty.className = "result-empty";
    empty.textContent = atlas.count ? "没有匹配的记忆。换个关键词，或切换上方类型。" : "尚未读取到记忆，请检查来源目录。";
    list.append(empty);
    return;
  }

  for (const result of results) {
    const memory = memoryById.get(result.id);
    const button = document.createElement("button");
    button.type = "button";
    button.className = `result-item${result.id === selectedId ? " is-selected" : ""}`;
    button.dataset.id = result.id;
    const top = document.createElement("span");
    top.className = "result-top";
    const type = document.createElement("span");
    type.className = "result-type";
    type.textContent = `${memory.tool || "Codex"} / ${kindLabels[memory.kind] || memory.kind}`;
    const score = document.createElement("span");
    score.className = "result-score";
    score.textContent = memory.lifecycle?.dormant ? "已消散" : `${Math.round(result.score * 100)} / 100`;
    top.append(type, score);
    const title = document.createElement("strong");
    title.className = "result-title";
    title.textContent = displayTitle(memory);
    const project = document.createElement("span");
    project.className = "result-project";
    project.textContent = `${memory.project} · ${result.reason}`;
    button.append(top, title, project);
    list.append(button);
  }
}

function renderDetail() {
  const memory = currentMemory();
  renderFeedbackNote();
  byId("detail-placeholder").hidden = Boolean(memory);
  byId("detail-content").hidden = !memory;
  if (!memory) return;

  const correction = memory.feedback.correction.trim();
  const result = scoreFor(memory.id);
  setText("detail-kind", `${memory.tool || "Codex"} / ${kindLabels[memory.kind] || memory.kind}`);
  setText("detail-date", memory.date);
  setText("detail-title", displayTitle(memory));
  setText("detail-project", memory.project);
  setText("detail-text", correction || memory.text);
  setText("original-text", memory.text);
  byId("correction-banner").hidden = !correction;
  byId("original-details").hidden = !correction;
  if (document.activeElement !== byId("correction-input")) byId("correction-input").value = correction;
  setText("base-value", memory.importance.toFixed(2));
  setText("strength-value", memory.strength.toFixed(2));
  byId("base-bar").style.width = `${memory.importance * 100}%`;
  byId("strength-bar").style.width = `${memory.strength * 100}%`;
  const synapseCount = atlas.network.synapses.filter((item) => item.a === memory.id || item.b === memory.id).length;
  setText("score-explain", result && currentQuery
    ? `本次匹配 ${Math.round(result.relevance * 100)}%${result.assoc ? ` · 联想激活 ${Math.round(result.assoc * 100)}%` : ""} · 时效 ${Math.round(result.recency * 100)}% · 综合分 ${Math.round(result.score * 100)}/100`
    : `突触 ${synapseCount} 条 · 本地调整 ${memory.feedback.boost > 0 ? "+" : ""}${memory.feedback.boost} 级${memory.feedback.pinned ? " · 已固定" : ""}${correction ? " · 修正已升权" : ""}。检索不会自动给自己升权。`);
  byId("pin-button").classList.toggle("is-active", Boolean(memory.feedback.pinned));
  byId("pin-button").textContent = memory.feedback.pinned ? "取消固定" : "固定";
  setText("source-path", `${memory.source}:${memory.source_line}`);
  const life = memory.lifecycle;
  if (life) setText("memory-lifetime", life.exempt === "pinned" ? "已固定，持续保留。" : life.exempt === "disabled" ? "已关闭自动消散。" : `${life.last_used ? `最近使用 ${new Date(life.last_used).toLocaleString()}` : "从首次导入开始计时，尚未使用"} · ${life.dormant ? "已消散，不参与正常召回" : `约 ${life.days_remaining} 天后消散，保留 ${Math.round(life.retention * 100)}%`}。`);
  renderSynapseList(memory);
}

function selectMemory(id) {
  selectedId = id;
  renderResults();
  renderDetail();
  updateProbe();
  openInspector();
  ensureSelectedVisible();
  drawGraph(performance.now());
  renewMemory(id).catch((error) => notify(`记忆续期失败：${error.message}`));
}

async function renewMemory(id) {
  const result = await post("/api/memories/use", {id});
  const memory = atlas.memories.find((item) => item.id === id);
  if (!memory) return;
  memory.lifecycle = result.lifecycle;
  memory.strength = result.strength;
  atlas.dormant_count = atlas.memories.filter((item) => item.lifecycle?.dormant).length;
  renderStats();
  layoutGraph();
  await search(currentQuery, true);
}

function openInspector() {
  byId("workspace").classList.add("is-inspecting");
  byId("detail-panel").classList.add("is-open");
  byId("detail-panel").setAttribute("aria-hidden", "false");
  byId("inspect-toggle").setAttribute("aria-expanded", "true");
}

function closeInspector() {
  byId("workspace").classList.remove("is-inspecting");
  byId("detail-panel").classList.remove("is-open");
  byId("detail-panel").setAttribute("aria-hidden", "true");
  byId("inspect-toggle").setAttribute("aria-expanded", "false");
}

function ensureSelectedVisible() {
  const node = graphNodes.get(selectedId), width = canvas.getBoundingClientRect().width;
  if (!node || width < 700 || !byId("workspace").classList.contains("is-inspecting")) return;
  const left = dock.search || dock.hud ? 248 : 40;
  const right = Math.max(left + 30, byId("detail-panel").offsetLeft - 30);
  const x = node.x * transform.scale + transform.x;
  if (x < left || x > right) {
    transform.x += Math.max(left, Math.min(right, x)) - x;
    staticDirty = true;
  }
}

async function loadState() {
  atlas = await request("/api/state");
  if (!atlas.memories.some((item) => item.id === selectedId)) selectedId = null;
  renderStats();
  layoutGraph();
  converged = false;
  updateHud();
  await search(currentQuery, true);
  byId("search-input").disabled = false;
  byId("search-form").querySelector("button").disabled = false;
  byId("quick-search-input").disabled = false;
}

async function search(query, preserveSelection = false) {
  const version = ++searchVersion;
  const nextQuery = query.trim();
  currentQuery = nextQuery;
  byId("search-input").value = nextQuery;
  byId("quick-search-input").value = nextQuery;
  if (!preserveSelection) {
    recallTimers.splice(0).forEach(clearTimeout);
    ranking = [];
    selectedId = null;
    hoveredId = null;
    transform = {scale: 1, x: 0, y: 0};
    staticDirty = true;
    closeInspector();
    recall = {phase: "loading"};
    updateProbe();
    updateRecallPhase(performance.now());
    renderResults();
    renderDetail();
    drawGraph(performance.now());
  }
  let data;
  try { data = await request(`/api/search?q=${encodeURIComponent(nextQuery)}`); }
  catch (error) {
    if (version !== searchVersion) return false;
    ranking = [];
    recall = {phase: "error", error: error.message};
    updateRecallPhase(performance.now());
    renderResults();
    drawGraph(performance.now());
    throw error;
  }
  if (version !== searchVersion) return false;
  for (const memory of atlas.memories) {
    const life = data.lifecycle?.[memory.id];
    if (!life) continue;
    memory.lifecycle = life;
    memory.strength = Math.min(1, Math.max(0, memory.importance + .12 * memory.feedback.boost + .08 * memory.feedback.pinned)) * life.retention;
    const node = graphNodes.get(memory.id);
    if (node) node.retention = life.retention;
  }
  atlas.dormant_count = atlas.memories.filter((item) => item.lifecycle?.dormant).length;
  renderStats();
  ranking = data.results;
  startRecall(!preserveSelection);
  renderResults();
  renderDetail();
  updateProbe();
  drawGraph(performance.now());
  return true;
}

async function post(path, data) {
  return request(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data)});
}

function exitRecall() {
  clearTimeout(debounce);
  clearTimeout(feedbackTimer);
  feedbackEvent = null;
  search("").catch((error) => notify(error.message));
  document.querySelector('[data-dock="search"]').focus({preventScroll: true});
  notify("已退出回忆，恢复完整网络");
}

async function changeFeedback(action, correction = "") {
  if (!selectedId || feedbackBusy) return;
  const id = selectedId;
  const before = atlas.memories.find((item) => item.id === id);
  feedbackBusy = true;
  const buttons = ["boost-button", "down-button", "pin-button", "reset-button", "save-correction"].map(byId);
  buttons.forEach((button) => { button.disabled = true; });
  let saved = false;
  try {
    await post("/api/feedback", {id, action, correction});
    saved = true;
    await loadState();
    const after = atlas.memories.find((item) => item.id === id);
    if (!after) { notify("反馈已保存，该记忆已不在当前来源中"); return; }
    const delta = after.strength - before.strength;
    const actionText = {boost: "已强化", down: "已降权", correct: "修正已保存", pin: after.feedback.pinned ? "已固定" : "已取消固定", reset: "已重置调整"}[action];
    const changed = JSON.stringify(before.feedback) !== JSON.stringify(after.feedback);
    const message = action === "correct" ? `修正已保存，这条记忆以后将按新内容检索。召回权重 ${before.strength.toFixed(2)} → ${after.strength.toFixed(2)}。`
      : !changed ? action === "reset" ? "当前没有需要重置的调整。" : `已达到调整上限，召回权重保持 ${after.strength.toFixed(2)}。`
      : `${actionText} · 召回权重 ${before.strength.toFixed(2)} → ${after.strength.toFixed(2)}。`;
    feedbackEvent = {id, action, startedAt: performance.now(), delta, changed, message,
      label: action === "correct" ? "已修正 · 按新内容检索" : `${actionText}${Math.abs(delta) > .0005 ? ` ${delta > 0 ? "+" : ""}${delta.toFixed(2)}` : ""}`};
    renderFeedbackNote();
    drawGraph(performance.now());
    clearTimeout(feedbackTimer);
    feedbackTimer = setTimeout(() => drawGraph(performance.now()), 2600);
    notify(message);
  } catch (error) { notify(saved ? `反馈已保存，页面刷新失败：${error.message}` : error.message); }
  finally { feedbackBusy = false; buttons.forEach((button) => { button.disabled = false; }); }
}

function layoutGraph() {
  if (!atlas) return;
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(320, rect.width);
  const height = Math.max(300, rect.height);
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  canvas.width = Math.round(width * dpr);
  canvas.height = Math.round(height * dpr);
  staticCanvas.width = canvas.width;
  staticCanvas.height = canvas.height;
  graphNodes = new Map();
  const mobile = width < 700;
  if (layoutIsMobile !== null && layoutIsMobile !== mobile) {
    transform = {scale: 1, x: 0, y: 0};
    hoveredId = null;
  }
  layoutIsMobile = mobile;
  const layerCount = mobile ? 7 : 9;
  graphLayers = Array.from({length: layerCount}, () => []);
  ambientPaths = [];
  visualStrands = [];
  const dockOpen = dock.search || dock.hud;
  const left = mobile ? 34 : dockOpen ? 262 : Math.min(190, Math.max(140, width * .115));
  const right = mobile ? width - 35 : width - Math.min(94, Math.max(65, width * .055));
  const top = mobile ? 35 : 34;
  const bottom = height - (mobile ? 24 : 25);
  const baseSlots = mobile ? [15, 21, 21, 21, 21, 21, 21] : [21, 30, 30, 30, 30, 30, 30, 30, 31];
  const projects = atlas.hubs.map((hub) => hub.name);
  const layerMemories = Array.from({length: layerCount - 1}, () => []);
  atlas.memories.forEach((memory, index) => layerMemories[index % (layerCount - 1)].push(memory));
  const slotsByLayer = baseSlots.map((count, layer) => Math.max(count, layer === 0 ? atlas.hubs.length + 2 : layerMemories[layer - 1].length + 2));
  // A slight shear and alternating depth keep the columns legible while giving them volume.
  const depthFor = (layer) => mobile || layer === 0 ? 0 : (layer % 2 ? 8 : 3);
  const yFor = (slot, layer) => top + depthFor(layer) + (bottom - top - depthFor(layer) * 2) * slot / (slotsByLayer[layer] - 1);
  const xFor = (layer, slot = (slotsByLayer[layer] - 1) / 2) => left + (right - left) * layer / (layerCount - 1)
    + (mobile ? 0 : (slot / (slotsByLayer[layer] - 1) - .5) * 18);
  const labelRow = document.querySelector(".layer-labels");
  labelRow.replaceChildren();
  labelRow.style.gridTemplateColumns = `repeat(${layerCount}, 1fr)`;
  labelRow.style.paddingLeft = `${Math.max(0, left - (right - left) / (layerCount - 1) / 2)}px`;
  labelRow.style.paddingRight = `${Math.max(0, width - right - (right - left) / (layerCount - 1) / 2)}px`;
  for (let layer = 0; layer < layerCount; layer++) {
    const label = document.createElement("span");
    label.textContent = layer === 0 ? "INPUT" : `HL ${layer}`;
    labelRow.append(label);
  }

  // Relay nodes are display-only routing points. Real memory records retain their own IDs.
  for (let layer = 0; layer < layerCount; layer++) {
    const slots = slotsByLayer[layer];
    const memorySlots = new Map();
    if (layer === 0) {
      atlas.hubs.forEach((hub, index) => memorySlots.set(Math.round((index + .5) * (slots - 1) / atlas.hubs.length), hub));
    } else {
      const entries = layerMemories[layer - 1];
      entries.forEach((memory, index) => memorySlots.set(Math.round((index + .5) * (slots - 1) / entries.length), memory));
    }
    for (let slot = 0; slot < slots; slot++) {
      const real = memorySlots.get(slot);
      const kind = !real ? "relay" : layer === 0 ? "hub" : "memory";
      const project = kind === "hub" ? real.name : kind === "memory" ? real.project : projects[(slot + layer * 3) % Math.max(1, projects.length)];
      const id = real?.id || `relay-${layer}-${slot}`;
      const roll = hash01(layer * 131 + slot * 17 + 5);
      const node = {id, kind, project, layer, slot, x: xFor(layer, slot), y: yFor(slot, layer),
        tone: roll < .5 ? "violet" : roll < .8 ? "amber" : "rose",
        signal: kind === "memory" ? labelValue(real.feedback) : 0,
        retention: kind === "memory" ? real.lifecycle?.retention ?? 1 : 1,
        radius: kind === "hub" ? 2.6 : kind === "memory" ? 1.6 + real.strength * .65 : mobile ? 1.45 : 1.3,
        label: kind === "hub" ? real.name : kind === "memory" ? displayTitle(real) : ""};
      graphNodes.set(id, node);
      graphLayers[layer].push(node);
    }
  }

  // Each visible strand stays within a project association; the source links are also drawn below.
  projectPaths = new Map();
  for (let layer = 0; layer < layerCount - 1; layer++) {
    for (const from of graphLayers[layer]) {
      const matches = graphLayers[layer + 1].filter((to) => to.project === from.project)
        .sort((a, b) => Math.abs(a.slot - from.slot) - Math.abs(b.slot - from.slot));
      for (const to of matches.slice(0, mobile ? 3 : 6)) {
        if (!projectPaths.has(from.project)) projectPaths.set(from.project, new Path2D());
        addCurve(projectPaths.get(from.project), from, to, .47);
      }
    }
  }

  // Exact desktop visual topology: 262 nodes and 3820 unique display strands at baseline.
  // Strands are illustrative routes; only synapseCurves below are learned memory links.
  ambientPaths = Array.from({length: 3}, () => new Path2D());
  const targetCount = mobile ? 1260 : 3820;
  const pairsByLayer = [];
  for (let layer = 0; layer < layerCount - 1; layer++) {
    const candidates = [];
    for (const from of graphLayers[layer]) for (const to of graphLayers[layer + 1]) {
      const distance = Math.abs(from.slot / (graphLayers[layer].length - 1) - to.slot / (graphLayers[layer + 1].length - 1));
      const seed = layer * 100000 + from.slot * 151 + to.slot * 17;
      candidates.push({from, to, rank: distance * .72 + hash01(seed) * .58});
    }
    candidates.sort((a, b) => a.rank - b.rank);
    pairsByLayer.push(candidates);
  }
  const perLayer = Math.floor(targetCount / pairsByLayer.length);
  pairsByLayer.forEach((pairs, layer) => {
    const quota = perLayer + (layer < targetCount % pairsByLayer.length ? 1 : 0);
    for (const {from, to} of pairs.slice(0, quota)) {
      const seed = layer * 100000 + from.slot * 151 + to.slot * 17;
      const bucket = Math.floor(hash01(seed + 3) * ambientPaths.length);
      addCurve(ambientPaths[bucket], from, to, .45);
      const dx = to.x - from.x;
      visualStrands.push({from, to, c1: {x: from.x + dx * .45, y: from.y}, c2: {x: to.x - dx * .45, y: to.y}, phase: hash01(seed + 7), bucket});
    }
  });
  setText("visual-neurons", graphNodes.size);
  setText("visual-connections", visualStrands.length);
  if (hoveredId && !graphNodes.has(hoveredId)) hoveredId = null;
  updateProbe();

  rebuildSynapses();
  staticDirty = true;
  ensureSelectedVisible();
  drawGraph(performance.now());
}

function addCurve(path, from, to, bend) {
  const dx = to.x - from.x;
  path.moveTo(from.x, from.y);
  path.bezierCurveTo(from.x + dx * bend, from.y, to.x - dx * bend, to.y, to.x, to.y);
}

const ambientColors = ["rgba(169, 181, 170, .058)", "rgba(166, 158, 192, .052)", "rgba(185, 166, 145, .048)"];

function drawVisualFlow(timestamp) {
  if (reducedMotion || !visualStrands.length || !atlas?.count) return;
  const mobile = canvas.getBoundingClientRect().width < 700;
  const count = Math.min(visualStrands.length, mobile ? 240 : currentQuery ? 450 : 2000);
  for (let index = 0; index < count; index++) {
    const strand = visualStrands[(index * 37) % visualStrands.length];
    const t = (timestamp / 3900 + strand.phase) % 1;
    const point = cubicPoint(strand.from, strand.c1, strand.c2, strand.to, t);
    ctx.fillStyle = currentQuery ? "rgba(194, 190, 208, .045)" : "rgba(194, 190, 208, .28)";
    ctx.beginPath(); ctx.arc(point.x, point.y, .55, 0, Math.PI * 2); ctx.fill();
  }
}

// The unselected backdrop (guides and dense threads) is cached and only redrawn when the view changes.
function renderStatic(dpr, height) {
  const sctx = staticCanvas.getContext("2d");
  sctx.setTransform(1, 0, 0, 1, 0, 0);
  sctx.clearRect(0, 0, staticCanvas.width, staticCanvas.height);
  sctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  sctx.translate(transform.x, transform.y);
  sctx.scale(transform.scale, transform.scale);
  sctx.strokeStyle = "rgba(157, 151, 170, .13)"; sctx.lineWidth = .5;
  for (const layer of graphLayers) {
    const first = layer[0], last = layer[layer.length - 1];
    if (!first || !last) continue;
    sctx.beginPath(); sctx.moveTo(first.x, first.y); sctx.lineTo(last.x, last.y); sctx.stroke();
  }
  ambientColors.forEach((color, index) => {
    sctx.strokeStyle = color; sctx.lineWidth = .45; sctx.stroke(ambientPaths[index]);
  });
  staticDirty = false;
}

function drawGraph(timestamp) {
  if (!atlas) return;
  updateRecallPhase(timestamp);
  const rect = canvas.getBoundingClientRect();
  const width = rect.width;
  const height = rect.height;
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (staticDirty) renderStatic(dpr, height);
  ctx.globalAlpha = currentQuery ? .16 : 1;
  if (staticCanvas.width) ctx.drawImage(staticCanvas, 0, 0);
  ctx.globalAlpha = 1;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.translate(transform.x, transform.y);
  ctx.scale(transform.scale, transform.scale);
  drawVisualFlow(timestamp);
  const activation = recallActivation(timestamp);
  const activeIds = new Set(activation.keys());
  if (selectedId) activation.set(selectedId, 1);
  const selectedProject = graphNodes.get(selectedId)?.project;

  for (const [project, path] of projectPaths) {
    const selectedPath = selectedProject && project === selectedProject;
    ctx.strokeStyle = currentQuery ? "rgba(150, 155, 177, .008)" : selectedPath ? "rgba(232, 195, 132, .12)" : "rgba(150, 155, 177, .055)";
    ctx.lineWidth = selectedPath ? .9 : .65;
    ctx.stroke(path);
  }

  for (const link of atlas.links) {
    if (link.kind !== "project") continue;
    const from = graphNodes.get(link.source), to = graphNodes.get(link.target);
    if (!from || !to) continue;
    if (link.kind === "project" && to.layer > 1) continue;
    ctx.beginPath(); ctx.moveTo(from.x, from.y); ctx.lineTo(to.x, to.y);
    const lit = from.id === selectedId || to.id === selectedId;
    ctx.strokeStyle = lit ? "rgba(239, 202, 136, .33)" : currentQuery ? "rgba(169, 166, 214, .012)" : "rgba(169, 166, 214, .07)";
    ctx.lineWidth = lit ? 1 : .55; ctx.stroke();
  }

  // Only the representative routes are animated; unrelated projects stay in the background.
  drawSynapses(timestamp, activation);

  for (const node of graphNodes.values()) {
    const selected = node.id === selectedId;
    const active = activeIds.has(node.id);
    const hovered = node.id === hoveredId;
    const inProject = selectedProject && node.project === selectedProject;
    ctx.globalAlpha = currentQuery && !selected && !active && !hovered ? .2 : 1;
    if (!selected && !active && !hovered) ctx.globalAlpha *= .12 + .88 * node.retention;
    if (selected || active || hovered) {
      const energy = Math.max(0, node.signal || 0);
      const halo = node.radius + (selected ? 8 + (node.signal || 0) * 4 : active ? 6 : 4);
      ctx.beginPath(); ctx.arc(node.x, node.y, halo, 0, Math.PI * 2);
      ctx.fillStyle = selected && node.signal < -.05 ? "rgba(218, 124, 155, .11)" : selected || active ? `rgba(245, 199, 129, ${.15 + energy * .12})` : "rgba(183, 171, 244, .18)"; ctx.fill();
      if (selected || active) {
        ctx.beginPath(); ctx.arc(node.x, node.y, halo * .75, 0, Math.PI * 2);
        ctx.strokeStyle = selected && node.signal < -.05 ? "rgba(229, 138, 160, .55)" : `rgba(250, 210, 139, ${.52 + energy * .24})`;
        ctx.lineWidth = selected ? 1.4 + energy * 1.3 : 1.1; ctx.stroke();
      }
    }
    ctx.fillStyle = selected && node.signal < -.05 ? suppressColor : selected || active ? "#ffd98c" : hovered ? "#d2bdf9" : node.kind === "hub" ? "#9fcfbc" : node.kind === "memory" ? memoryTone(node.signal) : inProject ? "#c6b899" : relayTones[node.tone];
    if (node.kind === "relay" && !selected && !active) {
      // Flat halo instead of shadowBlur: hundreds of relay nodes are redrawn every frame.
      const opacity = ctx.globalAlpha;
      ctx.globalAlpha = opacity * .12;
      ctx.beginPath(); ctx.arc(node.x, node.y, node.radius + 1.5, 0, Math.PI * 2); ctx.fill();
      ctx.globalAlpha = opacity;
      ctx.beginPath(); ctx.arc(node.x, node.y, node.radius, 0, Math.PI * 2); ctx.fill();
      continue;
    }
    ctx.beginPath(); ctx.arc(node.x, node.y, node.radius + (selected || active ? Math.max(0, node.signal) * 1.4 : 0), 0, Math.PI * 2);
    ctx.shadowBlur = selected ? 16 : active ? 12 : 3;
    ctx.shadowColor = ctx.fillStyle; ctx.fill(); ctx.shadowBlur = 0;
    if (active && !reducedMotion) {
      const age = timestamp - recall.startedAt - recallArrival(node.id);
      if (age >= 0 && age < 700) {
        ctx.strokeStyle = `rgba(250, 213, 151, ${(1 - age / 700) * .65})`;
        ctx.lineWidth = .8;
        ctx.beginPath(); ctx.arc(node.x, node.y, 4 + age / 55, 0, Math.PI * 2); ctx.stroke();
      }
    }
  }
  ctx.globalAlpha = 1;
  ctx.textBaseline = "middle";
  for (const node of graphNodes.values()) {
    if (node.kind !== "hub" && node.id !== selectedId) continue;
    if (node.kind === "hub" && width < 700) continue;
    const short = node.label.length > 16 ? `${node.label.slice(0, 16)}…` : node.label;
    ctx.font = "10px Cascadia Code, Microsoft YaHei, sans-serif";
    ctx.fillStyle = node.id === selectedId ? "#f4dcae" : node.kind === "hub" ? currentQuery ? "#5c6d67" : "#b7dec9" : "#b5b3d3";
    ctx.textAlign = node.layer > graphLayers.length / 2 ? "right" : "left";
    const x = node.x + (ctx.textAlign === "right" ? -9 : 9);
    ctx.fillText(short, x, node.y);
  }
  ctx.textAlign = "left";
  drawFeedbackPulse(timestamp);
}

function curveControls(from, to) {
  const dx = to.x - from.x, dy = to.y - from.y;
  if (from.layer !== to.layer) return [{x: from.x + dx * .5, y: from.y}, {x: to.x - dx * .5, y: to.y}];
  // Same column: bow the synapse sideways so it stays visible.
  const bulge = 38 * (from.slot % 2 ? 1 : -1);
  return [{x: from.x + bulge, y: from.y + dy * .25}, {x: to.x + bulge, y: to.y - dy * .25}];
}

function cubicPoint(from, c1, c2, to, t) {
  const u = 1 - t;
  return {x: u * u * u * from.x + 3 * u * u * t * c1.x + 3 * u * t * t * c2.x + t * t * t * to.x,
    y: u * u * u * from.y + 3 * u * u * t * c1.y + 3 * u * t * t * c2.y + t * t * t * to.y};
}

function rebuildSynapses() {
  synapseCurves = [];
  (atlas?.network?.synapses || []).forEach((synapse, index) => {
    const a = graphNodes.get(synapse.a), b = graphNodes.get(synapse.b);
    if (!a || !b) return;
    const [c1, c2] = curveControls(a, b);
    const rgb = synapse.origin === "grown" ? "239, 149, 165" : synapse.w > synapse.base + .02 ? "239, 201, 135"
      : synapse.w < synapse.base - .02 ? "229, 138, 160" : "170, 164, 222";
    synapseCurves.push({...synapse, a, b, c1, c2, rgb, key: `${synapse.a}|${synapse.b}`, phase: hash01(index * 3.7 + 1)});
  });
}

function strokeCurve(s) {
  ctx.beginPath();
  ctx.moveTo(s.a.x, s.a.y);
  ctx.bezierCurveTo(s.c1.x, s.c1.y, s.c2.x, s.c2.y, s.b.x, s.b.y);
  ctx.stroke();
}

function strokeRecallRoute(s, route, progress) {
  const forward = route.from === s.a.id;
  ctx.beginPath();
  const start = forward ? s.a : s.b;
  ctx.moveTo(start.x, start.y);
  for (let step = 1; step <= 36; step++) {
    const t = progress * step / 36;
    const point = forward ? cubicPoint(s.a, s.c1, s.c2, s.b, t) : cubicPoint(s.b, s.c2, s.c1, s.a, t);
    ctx.lineTo(point.x, point.y);
  }
  ctx.stroke();
  return forward ? cubicPoint(s.a, s.c1, s.c2, s.b, progress) : cubicPoint(s.b, s.c2, s.c1, s.a, progress);
}

function drawFeedbackPulse(timestamp) {
  if (!feedbackEvent?.changed || reducedMotion) return;
  const age = timestamp - feedbackEvent.startedAt, node = graphNodes.get(feedbackEvent.id);
  if (!node || age < 0 || age > 2500) return;
  const down = feedbackEvent.action === "down", corrected = feedbackEvent.action === "correct";
  const rgb = down ? "229, 138, 160" : corrected ? "161, 210, 191" : "239, 201, 135";
  const progress = Math.min(1, age / 1500);
  ctx.strokeStyle = `rgba(${rgb}, ${(1 - age / 2500) * .85})`;
  ctx.lineWidth = 1.2;
  ctx.beginPath(); ctx.arc(node.x, node.y, down ? 27 - progress * 22 : 5 + progress * 24, 0, Math.PI * 2); ctx.stroke();
  if (corrected) { ctx.beginPath(); ctx.arc(node.x, node.y, 3 + progress * 16, 0, Math.PI * 2); ctx.stroke(); }
  ctx.fillStyle = `rgba(${rgb}, ${1 - age / 2500})`;
  ctx.font = "11px Cascadia Code, Microsoft YaHei, sans-serif";
  ctx.textAlign = node.layer > graphLayers.length / 2 ? "right" : "left";
  ctx.fillText(feedbackEvent.label, node.x + (ctx.textAlign === "right" ? -12 : 12), node.y - 17);
  ctx.textAlign = "left";
}

// Real synapses: thickness follows learned weight and visual feedback emphasis.
function drawSynapses(timestamp, activation) {
  for (const [key, until] of flashes) if (until <= timestamp) flashes.delete(key);
  for (const s of synapseCurves) {
    const lit = s.a.id === selectedId || s.b.id === selectedId;
    const flash = (flashes.get(s.key) || 0) > timestamp;
    const route = currentQuery && recall?.plan?.edges.get(s.key);
    const progress = route ? Math.max(0, Math.min(1, (timestamp - recall.startedAt - recallMatchMs - (route.hop - 1) * recallHopMs) / recallHopMs)) : 0;
    const transition = weightTransitions.get(s.key);
    const mix = transition ? Math.min(1, (timestamp - transition.startedAt) / 700) : 1;
    const weight = transition ? transition.from + (s.w - transition.from) * mix : s.w;
    if (mix >= 1) weightTransitions.delete(s.key);
    const alpha = Math.min(.85, lit ? .3 + .45 * weight : .055 + .15 * weight) * (currentQuery && !lit ? .16 : 1)
      * (.1 + .9 * Math.min(s.a.retention, s.b.retention));
    ctx.strokeStyle = flash ? "rgba(255, 153, 171, .95)" : `rgba(${s.rgb}, ${alpha})`;
    ctx.lineWidth = (lit ? .65 + weight * 1.1 : .35 + weight * .6) + (flash ? 1.2 : 0);
    strokeCurve(s);
    if (route && progress > 0) {
      ctx.strokeStyle = `rgba(239, 201, 135, ${.3 + .5 * weight})`;
      ctx.lineWidth = .65 + weight * 1.1;
      const point = strokeRecallRoute(s, route, progress);
      if (progress < 1 && !reducedMotion) {
        ctx.fillStyle = "#fff1cc"; ctx.shadowColor = "#efc987"; ctx.shadowBlur = 9;
        ctx.beginPath(); ctx.arc(point.x, point.y, 2.2, 0, Math.PI * 2); ctx.fill(); ctx.shadowBlur = 0;
      }
    }
    if (feedbackEvent?.changed && !reducedMotion && timestamp - feedbackEvent.startedAt < 2500 && (s.a.id === feedbackEvent.id || s.b.id === feedbackEvent.id)) {
      const down = feedbackEvent.action === "down", corrected = feedbackEvent.action === "correct";
      const opacity = (1 - (timestamp - feedbackEvent.startedAt) / 2500) * .65;
      ctx.strokeStyle = `rgba(${down ? "229, 138, 160" : corrected ? "161, 210, 191" : "239, 201, 135"}, ${opacity})`;
      // Feedback briefly colours the incident edges; only actual learned weight changes affect thickness.
      ctx.lineWidth = .65 + weight * 1.1; strokeCurve(s);
    }
  }
  ghosts = ghosts.filter((ghost) => ghost.until > timestamp);
  for (const ghost of ghosts) {
    ctx.strokeStyle = `rgba(229, 138, 160, ${((ghost.until - timestamp) / 1800 * .7).toFixed(2)})`;
    ctx.lineWidth = 1.2;
    ctx.setLineDash([3, 4]); strokeCurve(ghost); ctx.setLineDash([]);
  }
  if (reducedMotion) return;
  for (const s of synapseCurves) {
    if (currentQuery) continue;
    const from = activation.get(s.a.id) || 0, to = activation.get(s.b.id) || 0;
    const strength = Math.max(from, to);
    if (strength < .05) continue;
    const t = (timestamp / 1300 + s.phase) % 1;
    const point = from >= to ? cubicPoint(s.a, s.c1, s.c2, s.b, t) : cubicPoint(s.b, s.c2, s.c1, s.a, t);
    ctx.globalAlpha = Math.min(1, .4 + .6 * s.w * strength);
    ctx.fillStyle = "#fff4d6";
    ctx.beginPath(); ctx.arc(point.x, point.y, 1.3 + s.w, 0, Math.PI * 2); ctx.fill();
  }
  ctx.globalAlpha = 1;
}

function updateHud() {
  const stats = atlas?.network?.stats;
  if (!stats) return;
  setText("hud-neurons", stats.neurons);
  setText("hud-connections", stats.connections);
  setText("hud-pruned", stats.pruned);
  setText("hud-grown", stats.grown);
  setText("hud-epoch", stats.epoch);
  setText("hud-loss", stats.loss == null ? "—" : stats.loss.toFixed(4));
  setText("hud-accuracy", stats.accuracy == null ? "n/a" : stats.accuracy.toFixed(3));
  byId("hud-accuracy").title = stats.accuracy == null ? "需要两条相互连接、且都带反馈的记忆才能评估" : `${stats.accuracy_n} 条两端都带反馈的突触，权重朝正确方向变化的比例`;
  setText("link-count", stats.connections);
  const status = byId("hud-status");
  status.textContent = learning ? "RUNNING" : converged ? "CONVERGED" : "STANDBY";
  status.className = learning ? "hud-active" : converged ? "hud-converged" : "hud-standby";
  setText("readout-generation", stats.generation);
  setText("readout-rate", stats.learning_rate.toFixed(3));
  setText("readout-fitness", stats.accuracy == null ? "—" : stats.accuracy.toFixed(3));
  byId("learn-toggle").classList.toggle("is-active", learning);
  drawChart();
  byId("dock-hud-label").textContent = learning ? "LEARNING" : "STATUS";
  byId("dock-hud").classList.toggle("is-live", learning);
  const memory = currentMemory();
  if (memory) renderSynapseList(memory);
}

const dockKey = "memory-atlas-dock";
const dock = {search: false, hud: false};
try { Object.assign(dock, JSON.parse(localStorage.getItem(dockKey) || "{}")); } catch { /* storage unavailable: keep defaults */ }

function applyDock() {
  for (const name of ["search", "hud"]) {
    const open = Boolean(dock[name]);
    byId(`${name}-panel`).classList.toggle("is-collapsed", !open);
    byId("workspace").classList.toggle(`is-${name}-collapsed`, !open);
    byId(`dock-${name}`).setAttribute("aria-pressed", String(open));
    byId(`dock-${name}`).classList.toggle("is-active", open);
  }
}

function setDock(name, open) {
  dock[name] = open;
  applyDock();
  layoutGraph();
  drawChart();
  try { localStorage.setItem(dockKey, JSON.stringify(dock)); } catch { /* per-viewer convenience only */ }
  if (open && name === "search") setTimeout(() => byId("search-input").focus(), 260);
}

function drawChart() {
  const chart = byId("hud-chart");
  const history = atlas?.network?.history || [];
  byId("hud-chart-empty").hidden = history.length > 1;
  const rect = chart.getBoundingClientRect();
  if (!rect.width) return;
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  chart.width = Math.round(rect.width * dpr);
  chart.height = Math.round(rect.height * dpr);
  const c = chart.getContext("2d");
  c.setTransform(dpr, 0, 0, dpr, 0, 0);
  if (history.length < 2) return;
  const width = rect.width, height = rect.height, pad = 4;
  const x = (index) => pad + (width - pad * 2) * index / (history.length - 1);
  const y = (value) => height - pad - (height - pad * 2) * value;
  const maxLoss = Math.max(1e-6, ...history.map((row) => row.loss ?? 0));
  history.forEach((row, index) => {
    if (row.event === "learn") return;
    c.strokeStyle = row.event === "grow" ? "rgba(255, 153, 171, .8)" : "rgba(229, 138, 160, .8)";
    c.lineWidth = 1;
    c.beginPath(); c.moveTo(x(index), 1); c.lineTo(x(index), height - 1); c.stroke();
  });
  const line = (color, pick) => {
    c.strokeStyle = color; c.lineWidth = 1.3; c.beginPath();
    let started = false;
    history.forEach((row, index) => {
      const value = pick(row);
      if (value == null) return;
      if (started) c.lineTo(x(index), y(value)); else { c.moveTo(x(index), y(value)); started = true; }
    });
    c.stroke();
  };
  line("#efc987", (row) => row.loss == null ? null : row.loss / maxLoss);
  line("#a1d2bf", (row) => row.accuracy);
}

function renderSynapseList(memory) {
  const list = byId("synapse-list");
  list.replaceChildren();
  const byIdMap = new Map(atlas.memories.map((item) => [item.id, item]));
  const links = atlas.network.synapses.filter((item) => item.a === memory.id || item.b === memory.id)
    .sort((left, right) => right.w - left.w);
  if (!links.length) {
    const empty = document.createElement("p");
    empty.className = "synapse-empty";
    empty.textContent = "这条记忆目前没有突触。可在 HUD 中触发进化，或等它被更多反馈连接。";
    list.append(empty);
    return;
  }
  for (const link of links.slice(0, 8)) {
    const other = byIdMap.get(link.a === memory.id ? link.b : link.a);
    if (!other) continue;
    const trend = link.origin === "grown" ? "new" : link.w > link.base + .02 ? "up" : link.w < link.base - .02 ? "down" : "";
    const item = document.createElement("button");
    item.type = "button";
    item.className = `synapse-item${trend ? ` is-${trend}` : ""}`;
    item.dataset.id = other.id;
    const top = document.createElement("span");
    top.className = "synapse-top";
    const kind = document.createElement("span");
    kind.textContent = `${other.tool || "Codex"} / ${kindLabels[other.kind] || other.kind}`;
    const weight = document.createElement("b");
    weight.className = trend ? `is-${trend}` : "";
    weight.textContent = trend === "new" ? `NEW ${link.w.toFixed(2)}` : `${link.base.toFixed(2)} → ${link.w.toFixed(2)}${trend === "up" ? " ↑" : trend === "down" ? " ↓" : ""}`;
    top.append(kind, weight);
    const title = document.createElement("span");
    title.className = "synapse-title";
    title.textContent = displayTitle(other);
    const bar = document.createElement("span");
    bar.className = "synapse-bar";
    const fill = document.createElement("i");
    fill.style.width = `${Math.round(link.w * 100)}%`;
    bar.append(fill);
    item.append(top, title, bar);
    list.append(item);
  }
  if (links.length > 8) {
    const more = document.createElement("p");
    more.className = "synapse-empty";
    more.textContent = `另有 ${links.length - 8} 条较弱的突触未显示`;
    list.append(more);
  }
}

function applyNetwork(network) {
  const previous = new Map(atlas.network.synapses.map((edge) => [MemoryRecall.edgeKey(edge.a, edge.b), edge.w]));
  if (!reducedMotion) for (const edge of network.synapses) {
    const key = MemoryRecall.edgeKey(edge.a, edge.b), from = previous.get(key);
    if (from != null && Math.abs(from - edge.w) > .00001) weightTransitions.set(key, {from, startedAt: performance.now()});
  }
  atlas.network = network;
  rebuildSynapses();
  updateHud();
  drawGraph(performance.now());
}

function setLearning(on) {
  learning = on;
  clearTimeout(learnTimer);
  updateHud();
  if (on) learnTick();
}

async function learnTick() {
  if (!learning) return;
  try {
    const data = await post("/api/learn/step", {});
    applyNetwork(data.network);
    if (currentQuery) await search(currentQuery, true);
    if (data.converged) {
      converged = true;
      setLearning(false);
      notify("学习已收敛：突触权重与反馈目标一致");
      return;
    }
    if (learning) learnTimer = setTimeout(learnTick, 650);
  } catch (error) { setLearning(false); notify(error.message); }
}

function toggleLearning() {
  if (!atlas?.network) return;
  setDock("hud", true);
  if (learning) { setLearning(false); return; }
  const drifting = atlas.network.synapses.some((synapse) => Math.abs(synapse.w - synapse.base) > 1e-3);
  if (!atlas.network.stats.labeled && !drifting) {
    notify("还没有反馈信号：先给记忆升权、降权或修正，网络才有可学习的目标");
    return;
  }
  converged = false;
  setLearning(true);
}

async function evolveNetwork(action) {
  if (!atlas?.network) return;
  setDock("hud", true);
  try {
    const previous = new Map(synapseCurves.map((synapse) => [synapse.key, synapse]));
    const data = await post("/api/evolve", {action});
    const now = performance.now();
    const current = new Set(data.network.synapses.map((synapse) => `${synapse.a}|${synapse.b}`));
    for (const key of current) if (!previous.has(key)) flashes.set(key, now + 3000);
    for (const [key, synapse] of previous) if (!current.has(key)) ghosts.push({...synapse, until: now + 1800});
    applyNetwork(data.network);
    if (currentQuery) await search(currentQuery, true);
    notify(action === "prune"
      ? (data.changed ? `已剪除 ${data.changed} 条弱突触` : `没有低于阈值的突触；先让网络学习几轮`)
      : (data.changed ? `进化：新增 ${data.changed} 条突触` : "进化完成，但暂无满足条件的新连接"));
  } catch (error) { notify(error.message); }
}

function graphPoint(event) {
  const rect = canvas.getBoundingClientRect();
  return {x: event.clientX - rect.left, y: event.clientY - rect.top};
}

function nodeAt(point) {
  const x = (point.x - transform.x) / transform.scale;
  const y = (point.y - transform.y) / transform.scale;
  return [...graphNodes.values()].reverse().find((node) => Math.hypot(node.x - x, node.y - y) <= Math.max(9, node.radius + 3)) || null;
}

function zoom(factor, center = null) {
  const rect = canvas.getBoundingClientRect();
  const point = center || {x: rect.width / 2, y: rect.height / 2};
  const next = Math.max(0.55, Math.min(2.4, transform.scale * factor));
  const worldX = (point.x - transform.x) / transform.scale;
  const worldY = (point.y - transform.y) / transform.scale;
  transform.x = point.x - worldX * next;
  transform.y = point.y - worldY * next;
  transform.scale = next;
  staticDirty = true;
  drawGraph(performance.now());
}

canvas.addEventListener("pointerdown", (event) => {
  canvas.setPointerCapture(event.pointerId);
  pointer = {start: graphPoint(event), previous: graphPoint(event), moved: false};
});
canvas.addEventListener("pointermove", (event) => {
  const point = graphPoint(event);
  if (!pointer) {
    const next = nodeAt(point)?.id || null;
    if (next !== hoveredId) {
      hoveredId = next;
      canvas.style.cursor = next ? "pointer" : "grab";
      updateProbe();
      drawGraph(performance.now());
    }
    return;
  }
  if (Math.hypot(point.x - pointer.start.x, point.y - pointer.start.y) > 4) pointer.moved = true;
  if (pointer.moved) {
    if (hoveredId) { hoveredId = null; updateProbe(); }
    transform.x += point.x - pointer.previous.x;
    transform.y += point.y - pointer.previous.y;
    staticDirty = true;
    drawGraph(performance.now());
  }
  pointer.previous = point;
});
canvas.addEventListener("pointerup", (event) => {
  if (!pointer) return;
  if (!pointer.moved) {
    const node = nodeAt(graphPoint(event));
    if (node?.kind === "memory" || node?.kind === "relay") {
      const memory = memoryForNode(node);
      if (memory) selectMemory(memory.id);
    }
    if (node?.kind === "hub") {
      setDock("search", true);
      byId("search-input").value = node.label;
      search(node.label).catch((error) => notify(error.message));
    }
  }
  pointer = null;
});
canvas.addEventListener("pointercancel", () => { pointer = null; });
canvas.addEventListener("pointerleave", () => {
  if (!pointer && hoveredId) { hoveredId = null; updateProbe(); drawGraph(performance.now()); }
});
canvas.addEventListener("wheel", (event) => {
  event.preventDefault();
  zoom(event.deltaY < 0 ? 1.12 : 1 / 1.12, graphPoint(event));
}, {passive: false});

byId("zoom-in").addEventListener("click", () => zoom(1.2));
byId("zoom-out").addEventListener("click", () => zoom(1 / 1.2));
byId("zoom-reset").addEventListener("click", () => {
  transform = {scale: 1, x: 0, y: 0};
  staticDirty = true;
  drawGraph(performance.now());
});
byId("inspect-toggle").addEventListener("click", () => {
  if (byId("detail-panel").classList.contains("is-open")) closeInspector(); else openInspector();
});
byId("detail-close").addEventListener("click", closeInspector);
byId("synapse-list").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-id]");
  if (button) selectMemory(button.dataset.id);
});
byId("result-list").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-id]");
  if (button) selectMemory(button.dataset.id);
});
byId("search-form").addEventListener("submit", (event) => {
  event.preventDefault();
  search(byId("search-input").value).catch((error) => notify(error.message));
});
byId("quick-search").addEventListener("submit", (event) => {
  event.preventDefault();
  const value = byId("quick-search-input").value;
  search(value).then((applied) => {
    if (applied && !ranking.length && value.trim()) notify("没有匹配记忆，请换个关键词");
  }).catch((error) => notify(error.message));
});
document.querySelectorAll(".quick-chip").forEach((button) => button.addEventListener("click", () => {
  byId("quick-search-input").value = button.dataset.query;
  byId("quick-search").requestSubmit();
}));
byId("recall-results").addEventListener("click", () => setDock("search", true));
byId("exit-recall").addEventListener("click", exitRecall);
byId("quick-exit-recall").addEventListener("click", exitRecall);
let debounce;
byId("search-input").addEventListener("input", () => {
  clearTimeout(debounce);
  debounce = setTimeout(() => search(byId("search-input").value).catch((error) => notify(error.message)), 280);
});
document.querySelectorAll(".filter-chip").forEach((button) => button.addEventListener("click", () => {
  document.querySelector(".filter-chip.is-active")?.classList.remove("is-active");
  button.classList.add("is-active");
  activeFilter = button.dataset.filter;
  renderResults();
}));
byId("sync-button").addEventListener("click", async () => {
  try { await post("/api/sync", {}); await loadState(); notify("已重新读取所选记忆来源"); }
  catch (error) { notify(error.message); }
});
byId("source-button").addEventListener("click", async () => {
  try {
    renderSources(await request("/api/sources"));
    byId("source-dialog").showModal();
  } catch (error) { notify(error.message); }
});
byId("source-close").addEventListener("click", () => byId("source-dialog").close());
byId("source-list").addEventListener("change", updateSourceSelectionCount);
byId("source-list").addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-id]");
  if (!button) return;
  try {
    renderSources(await post("/api/sources/remove", {id: button.dataset.id}));
    await loadState();
    notify("已移除该来源");
  } catch (error) { notify(error.message); }
});
byId("source-add-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const path = byId("source-folder-input").value.trim();
  if (!path) { notify("请输入记忆文件夹路径"); return; }
  try {
    renderSources(await post("/api/sources/add", {path}));
    byId("source-folder-input").value = "";
    notify("文件夹已添加；勾选后点击导入");
  } catch (error) { notify(error.message); }
});
byId("source-import").addEventListener("click", async () => {
  const ids = [...byId("source-list").querySelectorAll("input[type=checkbox]:checked")].map((item) => item.value);
  try {
    await post("/api/sources/select", {ids});
    await loadState();
    byId("source-dialog").close();
    notify(`已导入 ${atlas.count} 条记忆`);
  } catch (error) { notify(error.message); }
});
byId("learn-toggle").addEventListener("click", toggleLearning);
byId("evolve-button").addEventListener("click", () => evolveNetwork("grow"));
byId("prune-button").addEventListener("click", () => evolveNetwork("prune"));
document.querySelectorAll("[data-dock]").forEach((button) => button.addEventListener("click", () => {
  const name = button.dataset.dock;
  setDock(name, !dock[name]);
}));
document.addEventListener("keydown", (event) => {
  if (event.isComposing) return;
  if (event.key === "Escape" && currentQuery && !document.querySelector("dialog[open]") && !event.target.closest?.("textarea")) {
    event.preventDefault();
    exitRecall();
    return;
  }
  if (event.ctrlKey || event.metaKey || event.altKey || event.target.closest?.("input, textarea, button, select, summary, dialog")) return;
  if (event.key === "/") { event.preventDefault(); setDock("search", true); }
  else if (event.code === "Space") { event.preventDefault(); toggleLearning(); }
  else if (event.key.toLowerCase() === "e") evolveNetwork("grow");
});
byId("boost-button").addEventListener("click", () => changeFeedback("boost"));
byId("down-button").addEventListener("click", () => changeFeedback("down"));
byId("pin-button").addEventListener("click", () => changeFeedback("pin"));
byId("reset-button").addEventListener("click", () => changeFeedback("reset"));
byId("renew-memory").addEventListener("click", () => {
  if (selectedId) renewMemory(selectedId).then(() => notify("记忆已重新启用，寿命重新计时")).catch((error) => notify(error.message));
});
byId("decay-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = byId("decay-save");
  button.disabled = true;
  try {
    const days = Number(byId("decay-days").value);
    await post("/api/settings", {decay_days: days});
    await loadState();
    notify(days ? `消散时间已设为 ${days} 天` : "已关闭自动消散");
  } catch (error) { notify(error.message); }
  finally { button.disabled = false; }
});
byId("save-correction").addEventListener("click", () => {
  const correction = byId("correction-input").value.trim();
  if (!correction) { notify("请输入修正内容；如需清除修正，请使用重置调整"); return; }
  changeFeedback("correct", correction);
});

applyDock();
new ResizeObserver(() => { layoutGraph(); drawChart(); }).observe(canvas.parentElement);
if (!reducedMotion) {
  let lastFrame = 0;
  const animate = (time) => {
    if (!document.hidden && time - lastFrame > 45 && (atlas?.count || currentQuery || selectedId || ghosts.length || flashes.size)) {
      drawGraph(time);
      lastFrame = time;
    }
    requestAnimationFrame(animate);
  };
  requestAnimationFrame(animate);
}
loadState().catch((error) => {
  setText("source-status", `加载失败：${error.message}`);
  notify(`加载失败：${error.message}`);
});
// Re-evaluate elapsed time while the page is open, without renewing any memory.
setInterval(() => {
  if (atlas && !document.hidden && !feedbackBusy && !document.activeElement?.closest("textarea, input")) {
    search(currentQuery, true).catch(() => {});
  }
}, 60000);
