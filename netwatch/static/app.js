"use strict";

const $ = (sel) => document.querySelector(sel);
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const REFRESH_MS = 30_000;

const state = {
  week: null,      // YYYY-MM-DD of the Monday being viewed, null = current week
  data: null,      // /api/week
  status: null,    // /api/status
  selected: null,  // { day, h } of the open hour drill-down
  offset: 0,       // monitor's UTC offset; every time is shown in the monitor's zone
};

// ---------- formatting ----------

const local = (ts) => new Date((ts + state.offset) * 1000);
const pad = (n) => String(n).padStart(2, "0");
const hhmm = (ts) => `${pad(local(ts).getUTCHours())}:${pad(local(ts).getUTCMinutes())}`;
const hhmmss = (ts) => `${hhmm(ts)}:${pad(local(ts).getUTCSeconds())}`;
const dayIdx = (ts) => (local(ts).getUTCDay() + 6) % 7;
const monthDay = (ts) => `${MONTHS[local(ts).getUTCMonth()]} ${local(ts).getUTCDate()}`;
const shortDay = (ts) => `${DAYS[dayIdx(ts)]}, ${monthDay(ts)}`;
const longDay = (ts) => `${DAY_NAMES[dayIdx(ts)]}, ${monthDay(ts)}`;

function clock12(ts) {
  const d = local(ts);
  const h = d.getUTCHours();
  return `${h % 12 || 12}:${pad(d.getUTCMinutes())}${h < 12 ? "am" : "pm"}`;
}
function gmt(offset) {
  const sign = offset < 0 ? "-" : "+";
  const a = Math.abs(offset);
  const m = Math.floor((a % 3600) / 60);
  return `GMT${sign}${Math.floor(a / 3600)}${m ? `:${pad(m)}` : ""}`;
}
function duration(s) {
  s = Math.round(s);
  if (s < 60) return `${s}s`;
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h >= 24) return `${Math.floor(h / 24)}d ${h % 24}h`;
  if (h) return m ? `${h}h ${m}m` : `${h}h`;
  const sec = s % 60;
  return sec ? `${m}m ${sec}s` : `${m} min`;
}
function pct(x, digits = 2) {
  if (x == null) return null;
  const v = x * 100;
  if (v >= 99.995) return "100%";
  return `${Math.min(v, 99.99).toFixed(digits)}%`;
}
function mbps(x) {
  if (x == null) return "—";
  return x >= 100 ? x.toFixed(0) : x.toFixed(1);
}
function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
}
function uptimeClass(u) {
  if (u == null) return "none";
  return u >= 0.99 ? "" : u >= 0.95 ? "warn" : "bad";
}

// ---------- icons ----------

const GLYPHS = {
  up: '<path d="M5 8.3 7 10.3 11 5.9"/>',
  isp: '<path d="M5.6 5.6l4.8 4.8M10.4 5.6l-4.8 4.8"/>',
  lan: '<path d="M5.6 5.6l4.8 4.8M10.4 5.6l-4.8 4.8"/>',
  warn: '<path d="M8 4.6v4.2"/><path d="M8 11.3v.1"/>',
  none: "",
};
const ICON_FILL = { up: "var(--green)", isp: "var(--red)", lan: "var(--purple)", warn: "#f5a524", none: "var(--border-strong)" };

function icon(kind) {
  return (
    `<svg class="status-icon" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="8" fill="${ICON_FILL[kind]}"/>` +
    `<g stroke="#fff" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round">${GLYPHS[kind]}</g></svg>`
  );
}
function heroGlyph(kind) {
  return `<svg viewBox="0 0 16 16" aria-hidden="true"><g stroke="#fff" stroke-width="1.7" fill="none" stroke-linecap="round" stroke-linejoin="round">${GLYPHS[kind] || ""}</g></svg>`;
}

// ---------- tooltip ----------

const tip = $("#tip");
function showTip(html, x, y) {
  tip.innerHTML = html;
  tip.hidden = false;
  const r = tip.getBoundingClientRect();
  let left = x - r.width / 2;
  let top = y - r.height - 14;
  if (top < 8) top = y + 18;
  left = Math.min(Math.max(8, left), window.innerWidth - r.width - 8);
  tip.style.left = `${left}px`;
  tip.style.top = `${top}px`;
}
function hideTip() {
  tip.hidden = true;
}
function bindTip(el, html) {
  el.addEventListener("pointermove", (e) => showTip(html(), e.clientX, e.clientY));
  el.addEventListener("pointerleave", hideTip);
}

// ---------- data ----------

async function api(path) {
  const res = await fetch(path, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

async function loadStatus() {
  try {
    state.status = await api("/api/status");
    state.offset = state.status.utc_offset;
    renderHero(state.status);
  } catch {
    renderHero(null);
  }
}

async function loadOverview() {
  const data = await api("/api/days?days=90");
  renderOverview(data);
}

async function loadWeek() {
  const data = await api(`/api/week${state.week ? `?start=${state.week}` : ""}`);
  state.data = data;
  state.offset = data.utc_offset;
  renderWeek(data);
  renderSpeed(data);
  renderIncidents(data);
  if (state.selected) openHour(state.selected.day, state.selected.h, false);
}

// ---------- hero ----------

function renderHero(s) {
  let kind = "none";
  let title = "Can't reach the monitor";
  let sub = "";
  if (s) {
    const last = s.latest;
    const since = s.since ? duration(s.now - s.since) : null;
    if (s.state === "nodata") {
      title = "Waiting for the first check";
    } else if (s.state === "stale") {
      title = "Monitor is offline";
      sub = `Last check ${monthDay(last.ts)} at ${clock12(last.ts)}`;
    } else {
      kind = s.state;
      title = { up: "Internet is online", isp: "Internet is down", lan: "Router is unreachable" }[s.state];
      const lead = s.state === "up" ? "Up" : "Down";
      sub = `${since ? `${lead} for ${since} · ` : ""}checked ${clock12(last.ts)} ${gmt(state.offset)}`;
    }
    $("#foot").textContent =
      `Checks every ${s.interval}s` + (s.speedtest_interval ? ` · speed test every ${duration(s.speedtest_interval)}` : "");
  }
  const iconEl = $("#hero-icon");
  iconEl.className = `hero-icon ${kind}`;
  iconEl.innerHTML = heroGlyph(kind);
  $("#hero-title").textContent = title;
  $("#hero-sub").innerHTML = sub || "&nbsp;";
  document.title = s && s.state !== "up" && s.state !== "nodata" ? `● ${title} · netwatch` : "netwatch";
}

// ---------- bars ----------

function makeBar(c) {
  const el = document.createElement("button");
  el.type = "button";
  el.className = "bar";
  if (c.total === 0) {
    el.classList.add(c.expected === 0 ? "future" : "nodata");
    if (c.expected === 0) el.disabled = true;
    return el;
  }
  const down = c.total - c.up;
  if (down) {
    el.classList.add("partial");
    const seg = document.createElement("span");
    seg.className = `down${c.lan > c.isp ? " lan" : ""}`;
    seg.style.height = `${(down / c.total) * 100}%`;
    el.appendChild(seg);
  }
  return el;
}

function downtimeLines(c, interval) {
  if (c.total === 0) return [`<span class="t-sub">${c.expected === 0 ? "Not yet" : "No data"}</span>`];
  const down = c.total - c.up;
  if (!down) return [`<span class="t-ok">No downtime</span>`];
  const why = [c.isp && "ISP", c.lan && "router"].filter(Boolean).join(" + ");
  return [`<span class="${c.lan > c.isp ? "t-lan" : "t-bad"}">Down ${duration(down * interval)}</span> · ${why}`];
}

function monitorRow({ name, tag, iconKind, uptime, bars, barsClass, foot, note }) {
  const row = document.createElement("div");
  row.className = "monitor";
  const u = pct(uptime);
  row.innerHTML =
    `<div class="row-head"><div class="row-name">${icon(iconKind)}<span>${name}</span>${tag ? `<span class="tag">${tag}</span>` : ""}</div>` +
    `<div class="row-uptime ${uptimeClass(uptime)}">${u ? `${u} uptime` : "No data"}</div></div>`;
  const strip = document.createElement("div");
  strip.className = `bars ${barsClass || ""}`;
  bars.forEach((b) => strip.appendChild(b));
  row.appendChild(strip);
  if (foot) {
    const f = document.createElement("div");
    f.className = "row-foot";
    f.innerHTML = foot.map((t, i) => `<span class="${i && i < foot.length - 1 ? "mid" : ""}">${t}</span>`).join("");
    row.appendChild(f);
  }
  if (note) {
    const n = document.createElement("div");
    n.className = "row-note";
    n.innerHTML = note;
    row.appendChild(n);
  }
  return row;
}

// ---------- 90-day overview ----------

function renderOverview(data) {
  state.offset = data.utc_offset;
  const st = state.status?.state;
  const firstDay = data.first_check ? data.first_check - ((data.first_check + data.utc_offset) % 86400) : Infinity;
  const bars = data.days.map((d) => {
    const el = makeBar(d);
    if (d.start < firstDay && !d.total) {
      el.classList.replace("nodata", "before");
      el.disabled = true;
      bindTip(el, () => `<div class="t-head">${shortDay(d.start)}</div><span class="t-sub">Before monitoring started</span>`);
      return el;
    }
    bindTip(el, () => [`<div class="t-head">${shortDay(d.start)}, ${local(d.start).getUTCFullYear()}</div>`, ...downtimeLines(d, data.interval)].join("<br>"));
    if (!el.disabled) el.addEventListener("click", () => {
      go(d.date);
      $("#week").scrollIntoView({ behavior: "smooth", block: "start" });
    });
    return el;
  });
  const row = monitorRow({
    name: "Internet",
    iconKind: st === "up" ? "up" : st === "isp" || st === "lan" ? st : "none",
    uptime: data.uptime,
    bars,
    barsClass: "dense",
    foot: [`${data.days.length} days ago`, "Today"],
  });
  $("#overview").replaceChildren(row);
}

// ---------- week ----------

function dayIcon(day) {
  if (!day.total) return "none";
  const down = day.total - day.up;
  if (!down) return "up";
  const lan = day.hours.reduce((a, c) => a + c.lan, 0);
  if (lan > down / 2) return "lan";
  return day.uptime >= 0.95 ? "warn" : "isp";
}

function renderWeek(d) {
  $("#week-label").textContent = `${monthDay(d.days[0].start)} – ${monthDay(d.days[6].start)}`;
  $("#prev").disabled = !d.has_earlier;
  $("#next").disabled = !d.next;
  $("#today").hidden = !d.next;

  const range = `start=${d.days[0].date}&end=${d.days[6].date}`;
  $("#csv-checks").href = `/export/checks.csv?${range}`;
  $("#csv-outages").href = `/export/outages.csv?${range}`;
  $("#csv-speed").href = `/export/speedtests.csv?${range}`;

  const nowHour = d.now - ((d.now + d.utc_offset) % 3600);
  const rows = [];
  d.days.forEach((day, i) => {
    // Days that haven't started yet would only add empty rows.
    if (!day.expected && !day.total) return;
    const bars = day.hours.map((c) => {
      const el = makeBar(c);
      el.dataset.h = c.h;
      el.setAttribute("aria-label", `${longDay(day.start)} ${pad(c.h)}:00`);
      if (state.selected && state.selected.day === i && state.selected.h === c.h) el.classList.add("selected");
      bindTip(el, () => hourTip(day, c));
      if (!el.disabled) el.addEventListener("click", () => openHour(i, c.h, true));
      return el;
    });
    const row = monitorRow({
      name: longDay(day.start),
      tag: day.hours.some((c) => c.start === nowHour) ? "Today" : null,
      iconKind: dayIcon(day),
      uptime: day.uptime,
      bars,
    });
    row.dataset.day = i;
    bindTip(row.querySelector(".row-name"), () => dayTip(day));
    rows.push(row);
  });
  $("#week").replaceChildren(...rows);
}

function dayTip(day) {
  const head = `<div class="t-head">${longDay(day.start)}</div>`;
  if (!day.total) return head + `<span class="t-sub">No data</span>`;
  const lines = [
    day.outages
      ? `<span class="t-bad">${day.outages} outage${day.outages > 1 ? "s" : ""}</span> · ${duration(day.downtime)} down`
      : `<span class="t-ok">No outages</span>`,
  ];
  if (day.longest) lines.push(`Longest ${duration(day.longest)}`);
  if (day.speed) lines.push(`Avg ↓ ${mbps(day.speed.download_avg)} ↑ ${mbps(day.speed.upload_avg)} Mbps`);
  return head + lines.join("<br>");
}

function hourTip(day, c) {
  const lines = downtimeLines(c, state.data.interval);
  if (c.speed && !c.speed.error) lines.push(`↓ ${mbps(c.speed.download_mbps)} ↑ ${mbps(c.speed.upload_mbps)} Mbps`);
  return `<div class="t-head">${shortDay(day.start)} · ${pad(c.h)}:00</div>` + lines.join("<br>");
}

// ---------- hour drill-down ----------

async function openHour(dayI, h, toggle) {
  if (toggle && state.selected && state.selected.day === dayI && state.selected.h === h) return closeHour();
  state.selected = { day: dayI, h };
  document.querySelectorAll("#week .bar.selected").forEach((b) => b.classList.remove("selected"));
  const row = document.querySelector(`#week .monitor[data-day="${dayI}"]`);
  row?.querySelector(`.bar[data-h="${h}"]`)?.classList.add("selected");

  const cell = state.data.days[dayI].hours[h];
  const data = await api(`/api/hour?start=${cell.start}`);
  document.querySelectorAll("#week .drill").forEach((el) => el.remove());
  const drill = renderDrill(data);
  row.appendChild(drill);
  if (toggle) drill.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function closeHour() {
  state.selected = null;
  document.querySelectorAll("#week .drill").forEach((el) => el.remove());
  document.querySelectorAll("#week .bar.selected").forEach((b) => b.classList.remove("selected"));
}

function renderDrill(data) {
  const now = state.data.now;
  const box = document.createElement("div");
  box.className = "drill";
  const head = document.createElement("div");
  head.className = "drill-head";
  head.innerHTML = `<b>${hhmm(data.start)} – ${hhmm(data.start + 3600)}</b><button class="close-x" type="button" aria-label="Close">×</button>`;
  head.querySelector(".close-x").addEventListener("click", closeHour);

  const strip = document.createElement("div");
  strip.className = "bars fine";
  data.slots.forEach((s, i) => {
    const ts = data.start + i * data.interval;
    const el = document.createElement("span");
    el.className = `bar ${s ? s.status : ts > now ? "future" : "nodata"}`;
    bindTip(el, () => {
      const t = `<div class="t-head">${hhmmss(ts)}</div>`;
      if (!s) return t + `<span class="t-sub">${ts > now ? "Not yet" : "No data"}</span>`;
      if (s.status === "up") return t + `<span class="t-ok">Online</span>${s.latency_ms != null ? ` · ${Math.round(s.latency_ms)} ms` : ""}`;
      return t + (s.status === "isp" ? `<span class="t-bad">ISP down</span>` : `<span class="t-lan">Router unreachable</span>`);
    });
    strip.appendChild(el);
  });

  const foot = document.createElement("div");
  foot.className = "row-foot";
  foot.innerHTML = [0, 15, 30, 45, 60].map((m) => `<span>${hhmm(data.start + m * 60)}</span>`).join("");

  const notes = document.createElement("div");
  notes.className = "drill-notes";
  notes.innerHTML = data.outages
    .map(
      (o) =>
        `<span><span class="${o.kind === "isp" ? "t-bad" : "t-lan"}">●</span> ` +
        `${hhmm(o.start)} – ${o.ongoing ? "now" : hhmm(o.end)} · ${duration(o.duration)}${o.kind === "lan" ? " · router" : ""}</span>`
    )
    .join("");

  box.append(head, strip, foot, notes);
  return box;
}

// ---------- speed ----------

const SVGNS = "http://www.w3.org/2000/svg";
function svg(tag, attrs, parent) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  if (parent) parent.appendChild(el);
  return el;
}
function niceMax(v) {
  const exp = Math.pow(10, Math.floor(Math.log10(v)));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * exp >= v) return m * exp;
  return 10 * exp;
}

function renderSpeed(d) {
  const sp = d.stats.speed;
  $("#speed-sum").textContent = sp ? `↓ ${mbps(sp.download_avg)}  ↑ ${mbps(sp.upload_avg)} Mbps avg` : "No tests yet";
  renderChart(d);
}

function renderChart(d) {
  const box = $("#speed-chart");
  if (!$("#speed").open) return; // drawn when the section is opened
  const tests = d.speedtests;
  if (!tests.length) {
    box.innerHTML = `<div class="empty">No speed tests this week yet.</div>`;
    return;
  }
  const W = Math.max(280, Math.floor(box.getBoundingClientRect().width));
  const H = 200;
  const m = { l: 34, r: 40, t: 24, b: 24 };
  const iw = W - m.l - m.r;
  const ih = H - m.t - m.b;
  const ok = tests.filter((t) => !t.error);
  const ymax = niceMax(Math.max(10, ...ok.map((t) => Math.max(t.download_mbps, t.upload_mbps))) * 1.08);
  const x = (ts) => m.l + ((ts - d.start) / (d.end - d.start)) * iw;
  const y = (v) => m.t + ih - (v / ymax) * ih;

  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", "aria-label": "Download and upload speed this week" });
  const axis = svg("g", { class: "axis" }, root);
  for (let i = 0; i <= 4; i++) {
    const v = (ymax / 4) * i;
    svg("line", { class: "gridline", x1: m.l, x2: W - m.r, y1: y(v), y2: y(v) }, axis);
    svg("text", { x: m.l - 8, y: y(v) + 4, "text-anchor": "end" }, axis).textContent = v;
  }
  svg("text", { x: m.l - 8, y: m.t - 12, "text-anchor": "end" }, axis).textContent = "Mbps";
  d.days.forEach((day, i) => {
    if (i) svg("line", { class: "daysep", x1: x(day.start), x2: x(day.start), y1: m.t, y2: m.t + ih }, axis);
    svg("text", { x: x(day.start + 43200), y: H - 4, "text-anchor": "middle" }, axis).textContent = DAYS[i];
  });

  const maxGap = 2.5 * 3600;
  const series = [
    { key: "download_mbps", cls: "down" },
    { key: "upload_mbps", cls: "up" },
  ];
  const joined = (a, b) => a && b && !a.error && !b.error && Math.abs(a.ts - b.ts) <= maxGap;
  for (const s of series) {
    let path = "";
    tests.forEach((t, i) => {
      if (t.error) return;
      path += `${joined(tests[i - 1], t) ? "L" : "M"}${x(t.ts).toFixed(1)},${y(t[s.key]).toFixed(1)}`;
      if (!joined(tests[i - 1], t) && !joined(t, tests[i + 1])) {
        svg("circle", { class: `pt ${s.cls}`, cx: x(t.ts), cy: y(t[s.key]), r: 4 }, root);
      }
    });
    svg("path", { class: `line ${s.cls}`, d: path }, root);
  }
  tests.filter((t) => t.error).forEach((t) => svg("circle", { class: "fail", cx: x(t.ts), cy: y(0), r: 4 }, root));

  const lastOk = ok[ok.length - 1];
  if (lastOk) {
    let [yd, yu] = [y(lastOk.download_mbps), y(lastOk.upload_mbps)];
    if (Math.abs(yd - yu) < 13) {
      const mid = (yd + yu) / 2;
      [yd, yu] = yd <= yu ? [mid - 6.5, mid + 6.5] : [mid + 6.5, mid - 6.5];
    }
    const lx = x(lastOk.ts);
    const right = lx > W - 36;
    [[series[0], yd], [series[1], yu]].forEach(([s, ly]) => {
      svg("text", { class: "dlabel", x: lx + (right ? -8 : 8), y: ly + 4, "text-anchor": right ? "end" : "start" }, root).textContent = mbps(lastOk[s.key]);
    });
  }

  const cross = svg("line", { class: "crosshair", y1: m.t, y2: m.t + ih, visibility: "hidden" }, root);
  const dots = series.map((s) => svg("circle", { class: `pt ${s.cls}`, r: 5, visibility: "hidden" }, root));
  const hit = svg("rect", { x: m.l, y: 0, width: iw, height: H, fill: "transparent" }, root);
  const leave = () => {
    cross.setAttribute("visibility", "hidden");
    dots.forEach((p) => p.setAttribute("visibility", "hidden"));
    hideTip();
  };
  hit.addEventListener("pointermove", (e) => {
    const r = root.getBoundingClientRect();
    const ts = d.start + (((e.clientX - r.left) / r.width) * W - m.l) / iw * (d.end - d.start);
    let best = null;
    for (const t of tests) if (!best || Math.abs(t.ts - ts) < Math.abs(best.ts - ts)) best = t;
    if (!best || Math.abs(best.ts - ts) > 3 * 3600) return leave();
    cross.setAttribute("x1", x(best.ts));
    cross.setAttribute("x2", x(best.ts));
    cross.setAttribute("visibility", "visible");
    series.forEach((s, i) => {
      if (best.error) return dots[i].setAttribute("visibility", "hidden");
      dots[i].setAttribute("cx", x(best.ts));
      dots[i].setAttribute("cy", y(best[s.key]));
      dots[i].setAttribute("visibility", "visible");
    });
    const body = best.error
      ? `<span class="t-bad">${esc(best.error)}</span>`
      : `<span class="swatch down"></span> Download <b>${mbps(best.download_mbps)}</b> Mbps<br>` +
        `<span class="swatch up"></span> Upload <b>${mbps(best.upload_mbps)}</b> Mbps<br>` +
        `<span class="t-sub">Latency ${Math.round(best.ping_ms)} ms${best.server ? ` · ${esc(best.server)}` : ""}</span>`;
    showTip(`<div class="t-head">${shortDay(best.ts)} · ${hhmm(best.ts)}</div>${body}`, e.clientX, e.clientY);
  });
  hit.addEventListener("pointerleave", leave);
  box.replaceChildren(root);
}

// ---------- incidents ----------

function renderIncidents(d) {
  const list = d.outages;
  $("#incident-sum").textContent = list.length ? `${list.length} this week · ${duration(d.stats.downtime)}` : "None this week";
  if (!list.length) {
    $("#incident-list").innerHTML = `<div class="empty">No outages this week.</div>`;
    return;
  }
  $("#incident-list").innerHTML = list
    .map((o) => {
      const end = o.ongoing ? "now" : `${dayIdx(o.end) !== dayIdx(o.start) ? `${shortDay(o.end)} ` : ""}${hhmm(o.end)}`;
      return (
        `<div class="incident" data-start="${o.start}" title="${o.kind === "isp" ? "ISP outage" : "Router/WiFi unreachable"}">` +
        `<span class="dot ${o.kind}"></span>` +
        `<span class="incident-title">${shortDay(o.start)} · ${hhmm(o.start)} – ${end}</span>` +
        `<span class="dur">${o.ongoing ? `<span class="live">ongoing</span> · ` : ""}${duration(o.duration)}</span></div>`
      );
    })
    .join("");
  $("#incident-list").querySelectorAll(".incident").forEach((el) =>
    el.addEventListener("click", () => {
      const ts = Number(el.dataset.start);
      const i = d.days.findIndex((day, k) => ts >= day.start && (k === 6 || ts < d.days[k + 1].start));
      if (i < 0) return;
      openHour(i, local(ts).getUTCHours(), false).then(() =>
        document.querySelector("#week .drill")?.scrollIntoView({ behavior: "smooth", block: "center" })
      );
    })
  );
}

// ---------- navigation ----------

function go(week) {
  state.week = week;
  closeHour();
  history.replaceState(null, "", week ? `#${week}` : location.pathname);
  loadWeek().catch(console.error);
}

$("#prev").addEventListener("click", () => go(state.data.prev));
$("#next").addEventListener("click", () => go(state.data.next));
$("#today").addEventListener("click", () => go(null));
document.addEventListener("keydown", (e) => e.key === "Escape" && closeHour());

document.addEventListener("click", (e) => {
  const menu = $(".export");
  if (menu.open && !menu.contains(e.target)) menu.open = false;
});

let lastWidth = 0;
let resizeTimer;
new ResizeObserver(([entry]) => {
  const w = Math.floor(entry.contentRect.width);
  if (w === lastWidth) return;
  lastWidth = w;
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => state.data && renderChart(state.data), 100);
}).observe($("#speed-chart"));
$("#speed").addEventListener("toggle", () => state.data && renderChart(state.data));

state.week = /^#\d{4}-\d{2}-\d{2}$/.test(location.hash) ? location.hash.slice(1) : null;
loadStatus()
  .then(() => Promise.all([loadOverview(), loadWeek()]))
  .catch(console.error);
setInterval(() => {
  loadStatus().then(() => loadOverview()).catch(console.error);
  if (!state.data || !state.data.next) loadWeek().catch(console.error);
}, REFRESH_MS);
