const byId = (id) => document.getElementById(id);
const canvas = byId("network");
const ctx = canvas.getContext("2d");
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

let atlas = null;
let ranking = [];
let selectedId = null;
let activeFilter = "all";
let currentQuery = "";
let searchVersion = 0;
let toastTimer;
let graphNodes = new Map();
let graphLayers = [];
let graphEdges = [];
let ambientPaths = [];
let transform = {scale: 1, x: 0, y: 0};
let pointer = null;
const staticCanvas = document.createElement("canvas");
let staticDirty = true;
let ambientEdges = [];
let projectPaths = new Map();
let signals = [];
let dust = [];
let sparkles = [];
// Relay tones (excitatory / neutral / inhibitory look) are decorative only.
const relayTones = {amber: "#d6bc78", violet: "#9d96d6", rose: "#c7859d"};
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

function displayTitle(memory) {
  const correction = memory.feedback.correction.trim();
  return correction ? `已修正：${correction.slice(0, 46)}` : memory.title;
}

function setText(id, value) {
  byId(id).textContent = value;
}

function renderStats() {
  if (!atlas) return;
  setText("memory-count", atlas.count);
  setText("project-count", atlas.hubs.length);
  setText("link-count", atlas.network.stats.connections);
  setText("source-status", atlas.source_exists ? `已读取 ${atlas.count} 条本地记忆 · ${atlas.source}` : "尚无可读取的已选来源");
  setText("active-source-label", atlas.source || "未选择");
  byId("graph-empty").hidden = atlas.count > 0;
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
  const results = ranking.filter((result) => {
    const memory = memoryById.get(result.id);
    if (!memory) return false;
    if (activeFilter === "preference") return ["preference", "profile"].includes(memory.kind);
    return activeFilter === "all" || memory.kind === activeFilter;
  });
  setText("results-label", currentQuery ? "MATCHED MEMORIES" : "TOP MEMORIES");
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
    score.textContent = `${Math.round(result.score * 100)} / 100`;
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
  byId("correction-input").value = correction;
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
  renderSynapseList(memory);
}

function selectMemory(id) {
  selectedId = id;
  renderResults();
  renderDetail();
  openInspector();
  drawGraph(performance.now());
}

function openInspector() {
  byId("detail-panel").classList.add("is-open");
  byId("detail-panel").setAttribute("aria-hidden", "false");
  byId("inspect-toggle").setAttribute("aria-expanded", "true");
}

function closeInspector() {
  byId("detail-panel").classList.remove("is-open");
  byId("detail-panel").setAttribute("aria-hidden", "true");
  byId("inspect-toggle").setAttribute("aria-expanded", "false");
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
}

async function search(query, preserveSelection = false) {
  const version = ++searchVersion;
  const nextQuery = query.trim();
  const changed = nextQuery !== currentQuery;
  currentQuery = nextQuery;
  const data = await request(`/api/search?q=${encodeURIComponent(currentQuery)}`);
  if (version !== searchVersion) return;
  ranking = data.results;
  if (!preserveSelection && changed) {
    selectedId = currentQuery ? ranking[0]?.id || null : null;
    if (selectedId) openInspector(); else closeInspector();
  }
  renderResults();
  renderDetail();
  drawGraph(performance.now());
}

async function post(path, data) {
  return request(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data)});
}

async function changeFeedback(action, correction = "") {
  if (!selectedId) return;
  try {
    await post("/api/feedback", {id: selectedId, action, correction});
    await loadState();
    notify(action === "correct" ? "修正已保存，本地检索已更新" : "权重已更新");
  } catch (error) { notify(error.message); }
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
  const layerCount = mobile ? 7 : 9;
  graphLayers = Array.from({length: layerCount}, () => []);
  graphEdges = [];
  ambientPaths = [];
  const left = mobile ? 34 : Math.min(300, Math.max(242, width * .165));
  const right = mobile ? width - 35 : width - Math.min(94, Math.max(65, width * .055));
  const top = mobile ? 35 : 34;
  const bottom = height - (mobile ? 24 : 25);
  const slots = Math.max(mobile ? 21 : 38, Math.ceil(atlas.memories.length / (layerCount - 1)) + 2, atlas.hubs.length + 2);
  const projects = atlas.hubs.map((hub) => hub.name);
  const layerMemories = Array.from({length: layerCount - 1}, () => []);
  atlas.memories.forEach((memory, index) => layerMemories[index % (layerCount - 1)].push(memory));
  const yFor = (slot) => top + (bottom - top) * slot / (slots - 1);
  const xFor = (layer) => left + (right - left) * layer / (layerCount - 1);
  const labelRow = document.querySelector(".layer-labels");
  labelRow.replaceChildren();
  labelRow.style.gridTemplateColumns = `repeat(${layerCount}, 1fr)`;
  labelRow.style.paddingLeft = `${Math.max(0, left - (right - left) / (layerCount - 1) / 2)}px`;
  labelRow.style.paddingRight = `${Math.max(0, width - right - (right - left) / (layerCount - 1) / 2)}px`;
  for (let layer = 0; layer < layerCount; layer++) {
    const label = document.createElement("span");
    label.textContent = layer === 0 ? "INPUT" : layer === layerCount - 1 ? "OUTPUT" : `HL ${layer}`;
    labelRow.append(label);
  }

  // Relay nodes are display-only routing points. Real memory records retain their own IDs.
  for (let layer = 0; layer < layerCount; layer++) {
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
      const node = {id, kind, project, layer, slot, x: xFor(layer), y: yFor(slot),
        tone: roll < .5 ? "violet" : roll < .8 ? "amber" : "rose",
        signal: kind === "memory" ? labelValue(real.feedback) : 0,
        radius: kind === "hub" ? 4.3 : kind === "memory" ? 2.7 + real.strength * 1.6 : mobile ? 2.1 : 2.5,
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
        graphEdges.push({from, to, project: from.project});
        if (!projectPaths.has(from.project)) projectPaths.set(from.project, new Path2D());
        addCurve(projectPaths.get(from.project), from, to, .47);
      }
    }
  }

  // Dense background threads create depth only; they do not represent imported memories.
  // Most strands fan out to nearby slots (the hourglass look); a minority reach anywhere in the next layer.
  ambientPaths = Array.from({length: 3}, () => new Path2D());
  ambientEdges = [];
  const strands = mobile ? 5 : 12;
  for (let layer = 0; layer < layerCount - 1; layer++) {
    const target = graphLayers[layer + 1];
    for (const from of graphLayers[layer]) {
      for (let strand = 0; strand < strands; strand++) {
        const seed = layer * 10000 + from.slot * 97 + strand * 19;
        const near = hash01(seed + 1) < .78;
        const fraction = near ? from.slot / (slots - 1) + (hash01(seed + 2) - .5) * .36 : hash01(seed + 2);
        const to = target[Math.max(0, Math.min(target.length - 1, Math.round(fraction * (target.length - 1))))];
        const bucket = Math.floor(hash01(seed + 3) * ambientPaths.length);
        addCurve(ambientPaths[bucket], from, to, .45);
        ambientEdges.push({from, to, bucket});
      }
    }
  }

  // Floating dust and travelling signals are decoration too.
  const span = right - left;
  dust = Array.from({length: mobile ? 120 : 320}, (_, i) => ({
    x: left - 40 + hash01(i * 3.3 + 1) * (span + 80), y: top + hash01(i * 5.1 + 2) * (bottom - top),
    r: .45 + hash01(i * 7.7 + 3) * .75, a: .12 + hash01(i * 2.9 + 4) * .28}));
  sparkles = Array.from({length: mobile ? 24 : 80}, (_, i) => ({
    x: left - 30 + hash01(i * 4.7 + 11) * (span + 60), y: top + hash01(i * 6.3 + 12) * (bottom - top),
    r: .7 + hash01(i * 1.9 + 13) * .7, phase: hash01(i * 8.1 + 14) * Math.PI * 2,
    speed: .0006 + hash01(i * 2.3 + 15) * .0009, drift: 3 + hash01(i * 9.7 + 16) * 7}));
  signals = Array.from({length: mobile ? 60 : 170}, (_, i) => ({
    edge: ambientEdges[Math.floor(hash01(i * 7.7 + 9) * ambientEdges.length)],
    offset: hash01(i * 3.1 + 5), period: 4200 + hash01(i * 1.7 + 6) * 4800}));

  rebuildSynapses();
  staticDirty = true;
  drawGraph(performance.now());
}

function addCurve(path, from, to, bend) {
  const dx = to.x - from.x;
  path.moveTo(from.x, from.y);
  path.bezierCurveTo(from.x + dx * bend, from.y, to.x - dx * bend, to.y, to.x, to.y);
}

function bezierPoint(edge, t, bend) {
  const {from, to} = edge;
  const dx = to.x - from.x, u = 1 - t;
  const c1 = from.x + dx * bend, c2 = to.x - dx * bend;
  return {x: u * u * u * from.x + 3 * u * u * t * c1 + 3 * u * t * t * c2 + t * t * t * to.x,
    y: u * u * u * from.y + 3 * u * u * t * from.y + 3 * u * t * t * to.y + t * t * t * to.y};
}

const ambientColors = ["rgba(169, 181, 170, .085)", "rgba(166, 158, 192, .075)", "rgba(185, 166, 145, .07)"];
const signalColors = ["#b4e2cb", "#bebaf0", "#eed6a0"];

// The unselected backdrop (guides, dense threads, dust) is cached and only redrawn when the view changes.
function renderStatic(dpr, height) {
  const sctx = staticCanvas.getContext("2d");
  sctx.setTransform(1, 0, 0, 1, 0, 0);
  sctx.clearRect(0, 0, staticCanvas.width, staticCanvas.height);
  sctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  sctx.translate(transform.x, transform.y);
  sctx.scale(transform.scale, transform.scale);
  sctx.strokeStyle = "rgba(137, 133, 176, .12)"; sctx.lineWidth = .7;
  for (const layer of graphLayers) {
    const x = layer[0]?.x;
    if (x == null) continue;
    sctx.beginPath(); sctx.moveTo(x, 42); sctx.lineTo(x, height - 25); sctx.stroke();
  }
  ambientColors.forEach((color, index) => {
    sctx.strokeStyle = color; sctx.lineWidth = .6; sctx.stroke(ambientPaths[index]);
  });
  sctx.fillStyle = "#d8dcec";
  for (const mote of dust) {
    sctx.globalAlpha = mote.a;
    sctx.beginPath(); sctx.arc(mote.x, mote.y, mote.r, 0, Math.PI * 2); sctx.fill();
  }
  sctx.globalAlpha = 1;
  staticDirty = false;
}

function drawGraph(timestamp) {
  if (!atlas) return;
  const rect = canvas.getBoundingClientRect();
  const width = rect.width;
  const height = rect.height;
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (staticDirty) renderStatic(dpr, height);
  if (staticCanvas.width) ctx.drawImage(staticCanvas, 0, 0);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.translate(transform.x, transform.y);
  ctx.scale(transform.scale, transform.scale);
  const activeIds = new Set(currentQuery ? ranking.slice(0, 12).map((item) => item.id) : []);
  const activeProjects = new Set([...activeIds].map((id) => graphNodes.get(id)?.project).filter(Boolean));
  const selectedProject = graphNodes.get(selectedId)?.project;

  for (const [project, path] of projectPaths) {
    const selectedPath = selectedProject && project === selectedProject;
    const activePath = activeProjects.has(project);
    ctx.strokeStyle = selectedPath ? "rgba(232, 195, 132, .26)" : activePath ? "rgba(162, 217, 191, .2)" : "rgba(150, 155, 177, .13)";
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
    ctx.strokeStyle = lit ? "rgba(239, 202, 136, .33)" : "rgba(169, 166, 214, .07)";
    ctx.lineWidth = lit ? 1 : .55; ctx.stroke();
  }

  // Activation: the top recall results plus the selected memory; it spreads along real synapses.
  const activation = new Map();
  if (currentQuery) for (const item of ranking.slice(0, 24)) activation.set(item.id, item.activation || item.score);
  if (selectedId) activation.set(selectedId, 1);
  drawSynapses(timestamp, activation);

  if (!reducedMotion) {
    for (const signal of signals) {
      const t = (timestamp / signal.period + signal.offset) % 1;
      const point = bezierPoint(signal.edge, t, .45);
      ctx.globalAlpha = .15 + .6 * Math.sin(Math.PI * t);
      ctx.fillStyle = signalColors[signal.edge.bucket];
      ctx.beginPath(); ctx.arc(point.x, point.y, 1.05, 0, Math.PI * 2); ctx.fill();
    }
    ctx.fillStyle = "#e9ecf7";
    for (const spark of sparkles) {
      ctx.globalAlpha = .12 + .3 * (.5 + .5 * Math.sin(timestamp * spark.speed * 3 + spark.phase));
      ctx.beginPath();
      ctx.arc(spark.x + Math.sin(timestamp * spark.speed + spark.phase) * spark.drift,
        spark.y + Math.cos(timestamp * spark.speed * .8 + spark.phase) * spark.drift, spark.r, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;

    const moving = selectedProject || (currentQuery && activeProjects.values().next().value);
    graphEdges.filter((edge) => moving ? edge.project === moving && edge.from.slot % 3 === 0 : edge.from.slot % 11 === 0).slice(0, moving ? 32 : 18).forEach((edge, index) => {
      const phase = (timestamp / 3100 + index * .13) % 1;
      const x = edge.from.x + (edge.to.x - edge.from.x) * phase;
      const y = edge.from.y + (edge.to.y - edge.from.y) * phase;
      ctx.beginPath(); ctx.arc(x, y, moving ? 1.2 : .75, 0, Math.PI * 2);
      ctx.fillStyle = selectedProject ? "rgba(249, 213, 153, .75)" : moving ? "rgba(167, 230, 201, .7)" : "rgba(220, 211, 180, .36)"; ctx.fill();
    });
  }

  for (const node of graphNodes.values()) {
    const selected = node.id === selectedId;
    const active = activeIds.has(node.id);
    const inProject = selectedProject && node.project === selectedProject;
    if (selected || active) {
      ctx.beginPath(); ctx.arc(node.x, node.y, node.radius + (selected ? 11 : 7), 0, Math.PI * 2);
      ctx.fillStyle = selected ? "rgba(245, 199, 129, .14)" : "rgba(166, 226, 193, .1)"; ctx.fill();
    }
    ctx.fillStyle = selected ? "#f2cf91" : active ? "#b5e5ca" : node.kind === "hub" ? "#9fcfbc" : node.kind === "memory" ? memoryTone(node.signal) : inProject ? "#c6b899" : relayTones[node.tone];
    if (node.kind === "relay" && !selected && !active) {
      // Flat halo instead of shadowBlur: hundreds of relay nodes are redrawn every frame.
      ctx.globalAlpha = .16;
      ctx.beginPath(); ctx.arc(node.x, node.y, node.radius + 2.2, 0, Math.PI * 2); ctx.fill();
      ctx.globalAlpha = 1;
      ctx.beginPath(); ctx.arc(node.x, node.y, node.radius, 0, Math.PI * 2); ctx.fill();
      continue;
    }
    ctx.beginPath(); ctx.arc(node.x, node.y, node.radius, 0, Math.PI * 2);
    ctx.shadowBlur = selected ? 16 : active ? 11 : 7;
    ctx.shadowColor = ctx.fillStyle; ctx.fill(); ctx.shadowBlur = 0;
  }

  ctx.textBaseline = "middle";
  for (const node of graphNodes.values()) {
    if (node.kind !== "hub" && node.id !== selectedId) continue;
    if (node.kind === "hub" && width < 700) continue;
    const short = node.label.length > 16 ? `${node.label.slice(0, 16)}…` : node.label;
    ctx.font = "10px Cascadia Code, Microsoft YaHei, sans-serif";
    ctx.fillStyle = node.id === selectedId ? "#f4dcae" : node.kind === "hub" ? "#b7dec9" : "#b5b3d3";
    ctx.textAlign = node.layer > graphLayers.length / 2 ? "right" : "left";
    const x = node.x + (ctx.textAlign === "right" ? -9 : 9);
    ctx.fillText(short, x, node.y);
  }
  ctx.textAlign = "left";
}

function curveControls(from, to) {
  const dx = to.x - from.x, dy = to.y - from.y;
  if (Math.abs(dx) > 1) return [{x: from.x + dx * .5, y: from.y}, {x: to.x - dx * .5, y: to.y}];
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
    const rgb = synapse.origin === "grown" ? "158, 226, 190" : synapse.w > synapse.base + .02 ? "239, 201, 135"
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

// Real synapses: thickness = learned weight; amber = strengthened, rose = weakened, mint = grown.
function drawSynapses(timestamp, activation) {
  for (const s of synapseCurves) {
    const lit = s.a.id === selectedId || s.b.id === selectedId;
    const flash = (flashes.get(s.key) || 0) > timestamp;
    const hot = (activation.get(s.a.id) || 0) + (activation.get(s.b.id) || 0) > .05;
    const alpha = Math.min(.95, .16 + .5 * s.w + (lit ? .3 : hot ? .15 : 0));
    ctx.strokeStyle = flash ? "rgba(158, 226, 190, .95)" : `rgba(${s.rgb}, ${alpha})`;
    ctx.lineWidth = (.6 + s.w * 1.8) * (lit ? 1.4 : 1) + (flash ? 1.6 : 0);
    strokeCurve(s);
  }
  ghosts = ghosts.filter((ghost) => ghost.until > timestamp);
  for (const ghost of ghosts) {
    ctx.strokeStyle = `rgba(229, 138, 160, ${((ghost.until - timestamp) / 1800 * .7).toFixed(2)})`;
    ctx.lineWidth = 1.2;
    ctx.setLineDash([3, 4]); strokeCurve(ghost); ctx.setLineDash([]);
  }
  if (reducedMotion) return;
  for (const s of synapseCurves) {
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
  status.textContent = learning ? "ACTIVE" : converged ? "CONVERGED" : "STANDBY";
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
    c.strokeStyle = row.event === "grow" ? "rgba(158, 226, 190, .8)" : "rgba(229, 138, 160, .8)";
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
  try {
    const previous = new Map(synapseCurves.map((synapse) => [synapse.key, synapse]));
    const data = await post("/api/evolve", {action});
    const now = performance.now();
    const current = new Set(data.network.synapses.map((synapse) => `${synapse.a}|${synapse.b}`));
    for (const key of current) if (!previous.has(key)) flashes.set(key, now + 3000);
    for (const [key, synapse] of previous) if (!current.has(key)) ghosts.push({...synapse, until: now + 1800});
    applyNetwork(data.network);
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
  return [...graphNodes.values()].reverse().find((node) => node.kind !== "relay" && Math.hypot(node.x - x, node.y - y) <= Math.max(11, node.radius + 4)) || null;
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
  if (!pointer) return;
  const point = graphPoint(event);
  if (Math.hypot(point.x - pointer.start.x, point.y - pointer.start.y) > 4) pointer.moved = true;
  if (pointer.moved) {
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
    if (node?.kind === "memory") selectMemory(node.id);
    if (node?.kind === "hub") {
      setDock("search", true);
      byId("search-input").value = node.label;
      search(node.label).catch((error) => notify(error.message));
    }
  }
  pointer = null;
});
canvas.addEventListener("pointercancel", () => { pointer = null; });
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
  if (event.ctrlKey || event.metaKey || event.altKey || event.target.closest?.("input, textarea, button, select, summary, dialog")) return;
  if (event.key === "/") { event.preventDefault(); setDock("search", true); }
  else if (event.code === "Space") { event.preventDefault(); toggleLearning(); }
  else if (event.key.toLowerCase() === "e") evolveNetwork("grow");
});
byId("boost-button").addEventListener("click", () => changeFeedback("boost"));
byId("down-button").addEventListener("click", () => changeFeedback("down"));
byId("pin-button").addEventListener("click", () => changeFeedback("pin"));
byId("reset-button").addEventListener("click", () => changeFeedback("reset"));
byId("save-correction").addEventListener("click", () => {
  const correction = byId("correction-input").value.trim();
  if (!correction) { notify("请输入修正内容；如需清除修正，请使用重置调整"); return; }
  changeFeedback("correct", correction);
});

applyDock();
new ResizeObserver(() => { layoutGraph(); drawChart(); }).observe(canvas.parentElement);
if (!reducedMotion) {
  let lastFrame = 0;
  const animate = (time) => { if (!document.hidden && time - lastFrame > 45) { drawGraph(time); lastFrame = time; } requestAnimationFrame(animate); };
  requestAnimationFrame(animate);
}
loadState().catch((error) => {
  setText("source-status", `加载失败：${error.message}`);
  notify(`加载失败：${error.message}`);
});
