"use strict";

/* ---------------- 默认示例数据 ---------------- */

function defaultData() {
  return {
    t_start: 0,
    t_end: 12,
    n_points: 400,
    tanks: [
      { id: "A", bottom: 10, area: 20, capacity: 100, initial_volume: 0 },
      { id: "B", bottom: 8,  area: 25, capacity: 160, initial_volume: 25 },
      { id: "C", bottom: 5,  area: 20, capacity: 120, initial_volume: 30 },
    ],
    pipes: [
      { id: "P1", upstream: "A", downstream: "B", coeff: 3.0 },
      { id: "P2", upstream: "B", downstream: "C", coeff: 2.5 },
      { id: "P3", upstream: "C", downstream: "A", coeff: 0.5 },
    ],
    pumps: [
      { id: "PU1", tank: "C", flow: 14, depth_on: 3.0, depth_off: 1.0, initial_on: false },
    ],
    inflows: [
      { tank: "A", t_start: 0, t_end: 4, rate: 45 },
      { tank: "B", t_start: 1, t_end: 5, rate: 15 },
      { tank: "A", t_start: 8, t_end: 10, rate: 40 },
    ],
  };
}

let state = defaultData();

/* ---------------- 表单渲染 ---------------- */

const $ = (sel) => document.querySelector(sel);
const tankList = $("#tankList");
const pipeList = $("#pipeList");
const pumpList = $("#pumpList");
const errorBox = $("#errorBox");
const resultPanel = $("#resultPanel");

function numOrBlank(v) { return v === undefined || v === null ? "" : v; }

function renderTanks() {
  tankList.innerHTML = "";
  state.tanks.forEach((tk, ti) => {
    const div = document.createElement("div");
    div.className = "card-item";
    div.innerHTML = `
      <div class="row1">
        <span class="name"><input type="text" value="${tk.id}" data-k="id" style="width:64px"></span>
        <div class="fields">
          <label>底高程<input type="number" step="any" data-k="bottom" value="${numOrBlank(tk.bottom)}"></label>
          <label>截面积 (m²)<input type="number" step="any" data-k="area" value="${numOrBlank(tk.area)}"></label>
          <label>容量 (m³)<input type="number" step="any" data-k="capacity" value="${numOrBlank(tk.capacity)}"></label>
          <label>初始水量 (m³)<input type="number" step="any" data-k="initial_volume" value="${numOrBlank(tk.initial_volume)}"></label>
          <label>操作<div><button class="del" data-act="del-tank">删除池</button></div></label>
        </div>
      </div>
      <div class="inflows">
        <div class="inflow-head"><span>入流起点</span><span>终点</span><span>恒定流量(≥0)</span><span></span></div>
        <div data-inflows></div>
        <button class="mini" data-act="add-inflow">＋ 添加入流段</button>
      </div>`;
    const box = div.querySelector("[data-inflows]");

    const rows = () => box.querySelectorAll(".inflow-row");
    function renderInflows() {
      box.innerHTML = "";
      (tk.inflows || []).forEach((seg) => {
        const r = document.createElement("div");
        r.className = "inflow-row";
        r.innerHTML = `
          <input type="number" step="any" data-ik="t_start" value="${numOrBlank(seg.t_start)}">
          <input type="number" step="any" data-ik="t_end" value="${numOrBlank(seg.t_end)}">
          <input type="number" step="any" min="0" data-ik="rate" value="${numOrBlank(seg.rate)}">
          <button class="del" data-iact="del">删</button>`;
        r.querySelectorAll("input").forEach((inp) => {
          inp.addEventListener("input", () => {
            seg[inp.dataset.ik] = parseFloat(inp.value);
          });
        });
        r.querySelector("[data-iact]").addEventListener("click", () => {
          tk.inflows = (tk.inflows || []).filter((s) => s !== seg);
          renderInflows();
        });
        box.appendChild(r);
      });
    }
    if (!tk.inflows) {
      tk.inflows = state.inflows
        .filter((s) => s.tank === tk.id)
        .map((s) => ({ t_start: s.t_start, t_end: s.t_end, rate: s.rate }));
    }
    renderInflows();

    div.querySelector('[data-act="add-inflow"]').addEventListener("click", () => {
      tk.inflows.push({ t_start: state.t_start, t_end: state.t_end, rate: 0 });
      renderInflows();
    });
    div.querySelectorAll("[data-k]").forEach((inp) => {
      inp.addEventListener("input", () => {
        if (inp.dataset.k === "id") {
          const old = tk.id;
          tk.id = inp.value.trim();
          state.pipes.forEach((p) => {
            if (p.upstream === old) p.upstream = tk.id;
            if (p.downstream === old) p.downstream = tk.id;
          });
          state.pumps.forEach((p) => { if (p.tank === old) p.tank = tk.id; });
          renderAll();
        } else {
          tk[inp.dataset.k] = parseFloat(inp.value);
        }
      });
    });
    div.querySelector('[data-act="del-tank"]').addEventListener("click", () => {
      const id = tk.id;
      state.tanks.splice(ti, 1);
      state.pipes = state.pipes.filter((p) => p.upstream !== id && p.downstream !== id);
      state.pumps = state.pumps.filter((p) => p.tank !== id);
      renderAll();
    });
    tankList.appendChild(div);
  });
}

function tankOptions(selected) {
  return state.tanks.map((t) =>
    `<option value="${t.id}"${t.id === selected ? " selected" : ""}>${t.id}</option>`).join("");
}

function renderPipes() {
  pipeList.innerHTML = "";
  state.pipes.forEach((pk, pi) => {
    const div = document.createElement("div");
    div.className = "card-item";
    div.innerHTML = `
      <div class="row1">
        <span class="name"><input type="text" value="${pk.id}" data-k="id" style="width:64px"></span>
        <div class="fields pipefields">
          <label>起点池<select data-k="upstream">${tankOptions(pk.upstream)}</select></label>
          <label>终点池<select data-k="downstream">${tankOptions(pk.downstream)}</select></label>
          <label>管系数 (＞0)<input type="number" step="any" data-k="coeff" value="${numOrBlank(pk.coeff)}"></label>
          <label>操作<div><button class="del">删除管道</button></div></label>
        </div>
      </div>`;
    div.querySelectorAll("[data-k]").forEach((el) => {
      el.addEventListener("input", change => {
        pk[el.dataset.k] = el.tagName === "SELECT" ? el.value : parseFloat(el.value);
        if (el.dataset.k === "id") renderAll();
      });
      el.addEventListener("change", () => {
        if (el.tagName === "SELECT") { pk[el.dataset.k] = el.value; }
      });
    });
    div.querySelector(".del").addEventListener("click", () => {
      state.pipes.splice(pi, 1);
      renderPipes();
    });
    pipeList.appendChild(div);
  });
}

function renderPumps() {
  pumpList.innerHTML = "";
  state.pumps.forEach((pm, pi) => {
    const div = document.createElement("div");
    div.className = "card-item";
    div.innerHTML = `
      <div class="row1">
        <span class="name"><input type="text" value="${pm.id}" data-k="id" style="width:64px"></span>
        <div class="fields pumpfields">
          <label>所属池<select data-k="tank">${tankOptions(pm.tank)}</select></label>
          <label>流量 (＞0)<input type="number" step="any" data-k="flow" value="${numOrBlank(pm.flow)}"></label>
          <label>启泵水深<input type="number" step="any" data-k="depth_on" value="${numOrBlank(pm.depth_on)}"></label>
          <label>停泵水深<input type="number" step="any" data-k="depth_off" value="${numOrBlank(pm.depth_off)}"></label>
          <label class="checkline"><input type="checkbox" data-k="initial_on" ${pm.initial_on ? "checked" : ""}> 初始开启</label>
          <button class="del">删除泵</button>
        </div>
      </div>`;
    div.querySelectorAll("[data-k]").forEach((el) => {
      el.addEventListener("input", () => {
        if (el.type === "checkbox") pm.initial_on = el.checked;
        else if (el.tagName === "SELECT") pm.tank = el.value;
        else if (el.dataset.k === "id") { pm.id = el.value.trim(); renderAll(); }
        else pm[el.dataset.k] = parseFloat(el.value);
      });
      el.addEventListener("change", () => {
        if (el.tagName === "SELECT") pm.tank = el.value;
      });
    });
    div.querySelector(".del").addEventListener("click", () => {
      state.pumps.splice(pi, 1);
      renderPumps();
    });
    pumpList.appendChild(div);
  });
}

function renderAll() {
  renderTanks();
  renderPipes();
  renderPumps();
}

document.querySelectorAll("[data-add]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const what = btn.dataset.add;
    if (what === "tank") {
      const id = nextId(state.tanks.map((t) => t.id), "T");
      state.tanks.push({ id, bottom: 0, area: 10, capacity: 50,
                         initial_volume: 0, inflows: [] });
    } else if (what === "pipe") {
      if (state.tanks.length === 0) return alert("请先添加调蓄池");
      const id = nextId(state.pipes.map((p) => p.id), "P");
      state.pipes.push({ id, upstream: state.tanks[0].id,
                         downstream: state.tanks[0].id, coeff: 1 });
    } else {
      if (state.tanks.length === 0) return alert("请先添加调蓄池");
      const id = nextId(state.pumps.map((p) => p.id), "PU");
      state.pumps.push({ id, tank: state.tanks[0].id, flow: 5,
                         depth_on: 3, depth_off: 1, initial_on: false });
    }
    renderAll();
  });
});

function nextId(existing, prefix) {
  let i = existing.length + 1;
  while (existing.includes(`${prefix}${i}`)) i++;
  return `${prefix}${i}`;
}

/* ---------------- 收集参数并请求 ---------------- */

function gatherConfig() {
  const top = {
    t_start: parseFloat($("#t_start").value),
    t_end: parseFloat($("#t_end").value),
    n_points: parseInt($("#n_points").value, 10),
  };
  const inflows = [];
  state.tanks.forEach((tk) => {
    (tk.inflows || []).forEach((seg) => {
      inflows.push({ tank: tk.id, t_start: seg.t_start, t_end: seg.t_end, rate: seg.rate });
    });
  });
  return {
    ...top,
    tanks: state.tanks.map(({ inflows: _x, ...rest }) => rest),
    pipes: state.pipes.map((p) => ({ ...p })),
    pumps: state.pumps.map((p) => ({ ...p })),
    inflows,
  };
}

$("#runBtn").addEventListener("click", run);
$("#resetBtn").addEventListener("click", () => {
  state = defaultData();
  renderAll();
  syncTop();
  hideResults("已恢复示例数据，请点击“开始推演”。");
});

function syncTop() {
  $("#t_start").value = state.t_start;
  $("#t_end").value = state.t_end;
  $("#n_points").value = state.n_points;
}
["t_start", "t_end", "n_points"].forEach((k) => {
  $("#" + k).addEventListener("input", (e) => {
    state[k] = k === "n_points" ? parseInt(e.target.value, 10)
                                : parseFloat(e.target.value);
  });
});

function showError(msg) {
  // 计算失败：清除旧结果，仅显示错误，不保留任何结果。
  resultPanel.classList.add("hidden");
  errorBox.textContent = "推演被拒绝：\n" + msg;
  errorBox.classList.remove("hidden");
}

function hideResults(note) {
  resultPanel.classList.add("hidden");
  errorBox.textContent = note || "";
  errorBox.classList.toggle("hidden", !note);
}

async function run() {
  errorBox.classList.add("hidden");
  let cfg;
  try {
    cfg = gatherConfig();
  } catch (e) {
    return showError("表单数据不完整：" + e.message);
  }
  $("#runBtn").disabled = true;
  $("#runBtn").textContent = "推演中…";
  try {
    const resp = await fetch("/api/simulate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(cfg),
    });
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      throw new Error(data.detail || `HTTP ${resp.status}`);
    }
    const result = await resp.json();
    renderResult(result);
  } catch (err) {
    showError(err.message);
  } finally {
    $("#runBtn").disabled = false;
    $("#runBtn").textContent = "开始推演";
  }
}

/* ---------------- Canvas 折线图 ---------------- */

const PALETTE = ["#1d6fa5", "#d97706", "#059669", "#dc2626", "#7c3aed",
                 "#0891b2", "#be185d", "#65a30d", "#9333ea", "#0f766e"];

class LineChart {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.series = [];
    this.xs = [];
    this.metricName = "";
    this._hover = null;
    canvas.addEventListener("mousemove", (e) => this._onMove(e));
    canvas.addEventListener("mouseleave", () => { this._hover = null; this.draw(); });
    window.addEventListener("resize", () => this.draw());
  }

  setData(xs, series, yLabel) {
    this.xs = xs;
    this.series = series;
    this.yLabel = yLabel || "";
    this.draw();
  }

  setMarkers(markers) {
    // markers: [{x, label, on}] 竖线（泵切换时刻）
    this.markers = markers || [];
    this.draw();
  }

  _geom() {
    const dpr = window.devicePixelRatio || 1;
    const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
    if (this.canvas.width !== Math.round(w * dpr)) {
      this.canvas.width = Math.round(w * dpr);
      this.canvas.height = Math.round(h * dpr);
    }
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { w, h };
  }

  _onMove(e) {
    const rect = this.canvas.getBoundingClientRect();
    this._hover = { x: e.clientX - rect.left, y: e.clientY - rect.top };
    this.draw();
  }

  draw() {
    const { w, h } = this._geom();
    const ctx = this.ctx;
    ctx.clearRect(0, 0, w, h);
    if (!this.series.length || !this.xs.length) {
      ctx.fillStyle = "#64748b";
      ctx.font = "13px sans-serif";
      ctx.fillText("无数据", w / 2 - 20, h / 2);
      return;
    }
    const ml = 64, mr = 140, mt = 16, mb = 38;
    const pw = w - ml - mr, ph = h - mt - mb;

    let xmin = Math.min(...this.xs), xmax = Math.max(...this.xs);
    let ymin = Infinity, ymax = -Infinity;
    this.series.forEach((s) => s.ys.forEach((v) => {
      if (v < ymin) ymin = v;
      if (v > ymax) ymax = v;
    }));
    if (!isFinite(ymin) || !isFinite(ymax)) return;
    if (ymax - ymin < 1e-12) { ymax += 1; ymin = Math.max(0, ymin - 1); }
    if (ymin > 0) ymin = Math.max(0, ymin - (ymax - ymin) * 0.05);
    ymax += (ymax - ymin) * 0.06;

    const X = (x) => ml + (x - xmin) / (xmax - xmin || 1) * pw;
    const Y = (y) => mt + (ymax - y) / (ymax - ymin) * ph;

    // 网格与刻度
    ctx.strokeStyle = "#eef2f7";
    ctx.fillStyle = "#64748b";
    ctx.font = "11px sans-serif";
    ctx.lineWidth = 1;
    const ny = 5;
    for (let i = 0; i <= ny; i++) {
      const yv = ymin + (ymax - ymin) * i / ny;
      const yy = Y(yv);
      ctx.beginPath(); ctx.moveTo(ml, yy); ctx.lineTo(ml + pw, yy); ctx.stroke();
      ctx.textAlign = "right";
      ctx.fillText(fmt(yv), ml - 6, yy + 3);
    }
    const nx = 6;
    for (let i = 0; i <= nx; i++) {
      const xv = xmin + (xmax - xmin) * i / nx;
      ctx.textAlign = "center";
      ctx.fillText(fmt(xv), X(xv), mt + ph + 16);
    }

    // 曲线
    this.series.forEach((s, si) => {
      const color = s.color || PALETTE[si % PALETTE.length];
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.beginPath();
      s.ys.forEach((v, i) => {
        const xx = X(this.xs[i]), yy = Y(v);
        i === 0 ? ctx.moveTo(xx, yy) : ctx.lineTo(xx, yy);
      });
      ctx.stroke();
    });

    // 泵切换竖线（画在曲线之上、十字线之下）
    (this.markers || []).forEach((m) => {
      if (m.x < xmin || m.x > xmax) return;
      const xx = X(m.x);
      ctx.strokeStyle = m.on ? "rgba(5,150,105,.55)" : "rgba(148,163,184,.8)";
      ctx.setLineDash([5, 4]);
      ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(xx, mt); ctx.lineTo(xx, mt + ph); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = m.on ? "#059669" : "#64748b";
      ctx.textAlign = "center";
      ctx.font = "10px sans-serif";
      ctx.fillText(m.on ? `▲${fmt(m.x)}` : `▼${fmt(m.x)}`, xx, mt + 10);
    });

    // 图例
    this.series.forEach((s, si) => {
      const color = s.color || PALETTE[si % PALETTE.length];
      const lx = ml + pw + 14, ly = mt + 14 + si * 18;
      ctx.fillStyle = color;
      ctx.fillRect(lx, ly - 9, 16, 3);
      ctx.fillStyle = "#334155";
      ctx.textAlign = "left";
      ctx.font = "12px sans-serif";
      ctx.fillText(s.name, lx + 22, ly - 4);
    });

    // 悬浮十字线与数值
    if (this._hover && this._hover.x >= ml && this._hover.x <= ml + pw) {
      const xv = xmin + (this._hover.x - ml) / pw * (xmax - xmin);
      let idx = 0, best = Infinity;
      this.xs.forEach((x, i) => {
        const d = Math.abs(x - xv);
        if (d < best) { best = d; idx = i; }
      });
      const xx = X(this.xs[idx]);
      ctx.strokeStyle = "#94a3b8";
      ctx.setLineDash([4, 4]);
      ctx.beginPath(); ctx.moveTo(xx, mt); ctx.lineTo(xx, mt + ph); ctx.stroke();
      ctx.setLineDash([]);
      let ty = mt + 10;
      ctx.textAlign = "left";
      ctx.fillStyle = "#334155";
      ctx.font = "11px sans-serif";
      const label = `t=${fmt(this.xs[idx])}`;
      ctx.fillText(label, Math.min(xx + 8, ml + pw - 90), ty);
      this.series.forEach((s, si) => {
        const color = s.color || PALETTE[si % PALETTE.length];
        ty += 14;
        ctx.fillStyle = color;
        ctx.fillText(`${s.name}: ${fmt(s.ys[idx])}`,
                     Math.min(xx + 8, ml + pw - 130), ty);
        ctx.beginPath();
        ctx.arc(xx, Y(s.ys[idx]), 3, 0, Math.PI * 2);
        ctx.fill();
      });
    }
  }
}

function fmt(v) {
  if (!isFinite(v)) return "-";
  const a = Math.abs(v);
  if (a !== 0 && (a >= 1e5 || a < 1e-3)) return v.toExponential(2);
  return (Math.round(v * 1000) / 1000).toString();
}

/* ---------------- 结果渲染 ---------------- */

let lastResult = null;
const levelChart = new LineChart($("#levelChart"));
const cumChart = new LineChart($("#cumChart"));
let levelMetric = "depth";
let cumMetric = "pipe";

$("#levelSeg").addEventListener("click", (e) => {
  if (!e.target.dataset.metric) return;
  levelMetric = e.target.dataset.metric;
  $("#levelSeg").querySelectorAll("button").forEach((b) =>
    b.classList.toggle("on", b === e.target));
  renderLevel();
});
$("#cumSeg").addEventListener("click", (e) => {
  if (!e.target.dataset.metric) return;
  cumMetric = e.target.dataset.metric;
  $("#cumSeg").querySelectorAll("button").forEach((b) =>
    b.classList.toggle("on", b === e.target));
  renderCum();
});

function renderResult(r) {
  lastResult = r;
  errorBox.classList.add("hidden");
  resultPanel.classList.remove("hidden");

  const b = r.balance;
  const tol = Math.max(1e-6, 1e-7 * Math.max(1, b.initial + b.inflow));
  const balanced = Math.abs(b.residual) <= tol;
  $("#balanceCards").innerHTML = `
    ${stat("初存总量", b.initial)}
    ${stat("外部入流", b.inflow)}
    ${stat("末存总量", b.final)}
    ${stat("泵排出", b.pumped)}
    ${stat("溢流", b.overflow, b.overflow > tol ? "bad" : "")}
    ${stat("收支残差", b.residual, balanced ? "ok" : "bad")}`;
  $("#balanceLine").innerHTML =
    `水量收支校验：初存 ${fmt(b.initial)} ＋ 入流 ${fmt(b.inflow)} ` +
    `＝ 末存 ${fmt(b.final)} ＋ 泵排 ${fmt(b.pumped)} ＋ 溢流 ${fmt(b.overflow)}` +
    `（残差 ${fmt(b.residual)}，${balanced ? "闭合 ✔" : "未闭合 ✘"}）`;

  renderLevel();
  renderCum();

  // 泵切换
  const tb = $("#switchTable").querySelector("tbody");
  tb.innerHTML = "";
  if (!r.switches.length) {
    tb.innerHTML = `<tr class="empty"><td colspan="4">全程无泵状态切换</td></tr>`;
  } else {
    r.switches.forEach((s) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${fmt(s.time)}</td><td>${s.pump}</td><td>${s.tank}</td>
        <td class="${s.on ? "on" : "off"}">${s.on ? "▲ 开启" : "▼ 停止"}</td>`;
      tb.appendChild(tr);
    });
  }

  // 汇总
  const sb = $("#sumTable").querySelector("tbody");
  sb.innerHTML = "";
  r.pipes.forEach((p) => addSum(sb, `管 ${p.id}（${p.upstream}→${p.downstream}）`, p.total));
  r.pumps.forEach((p) => addSum(sb, `泵 ${p.id}（${p.tank}）`, p.total));
  r.tanks.forEach((t) => addSum(sb, `溢流 ${t.id}`, t.overflow_total,
                                t.overflow_total > tol));
  r.tanks.forEach((t) => addSum(sb, `入流 ${t.id}`, t.inflow_total));
  r.tanks.forEach((t) => addSum(sb, `末存 ${t.id}`, t.volume.at(-1)));
}

function stat(label, v, cls) {
  return `<div class="stat ${cls || ""}"><div class="v">${fmt(v)}</div>
          <div class="l">${label}</div></div>`;
}
function addSum(tb, name, v, warn) {
  const tr = document.createElement("tr");
  tr.innerHTML = `<td>${name}</td><td${warn ? ' style="color:#b42318;font-weight:600"' : ""}>${fmt(v)}</td>`;
  tb.appendChild(tr);
}

function renderLevel() {
  const r = lastResult;
  const key = levelMetric;
  const labelMap = { depth: "水深", head: "水头(水位高程)", volume: "存水量" };
  levelChart.setData(r.times, r.tanks.map((t, i) => ({
    name: `池 ${t.id}`,
    ys: t[key],
    color: PALETTE[i % PALETTE.length],
  })), labelMap[key]);
  levelChart.setMarkers(r.switches.map((s) =>
    ({ x: s.time, on: s.on, label: s.pump })));
}

function renderCum() {
  const r = lastResult;
  if (cumMetric === "pipe") {
    cumChart.setData(r.times, r.pipes.map((p, i) => ({
      name: `管 ${p.id} ${p.upstream}→${p.downstream}`,
      ys: p.cumulative,
      color: PALETTE[i % PALETTE.length],
    })));
  } else if (cumMetric === "pump") {
    cumChart.setData(r.times, r.pumps.map((p, i) => ({
      name: `泵 ${p.id}(${p.tank})`,
      ys: p.cumulative,
      color: PALETTE[(i + 2) % PALETTE.length],
    })));
  } else {
    cumChart.setData(r.times, r.tanks.map((t, i) => ({
      name: `溢流 ${t.id}`,
      ys: t.overflow_series,
      color: PALETTE[(i + 3) % PALETTE.length],
    })));
  }
}

/* ---------------- 启动 ---------------- */

syncTop();
renderAll();
