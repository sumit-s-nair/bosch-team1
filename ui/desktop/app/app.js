// Desktop console for the agent server (same origin as /api). Polls every 2 s.
const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (x) => (typeof x === "number" ? (x * 100).toFixed(1) + "%" : "-");
const num = (x, d = 0) => (typeof x === "number" ? x.toFixed(d) : "-");
let view = "incidents", tab = "overview", selected = null, lastPending = null, lastDetail = "", lastLive = "", lastAudit = "";
let convo = { run: null, n: 0 };

async function api(path, body) {
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
}

function when(id) {   // 20261024-122709 -> 24 Oct 12:27:09
  const m = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})$/.exec(id || "");
  if (!m) return id;
  const mon = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][+m[2] - 1];
  return `${+m[3]} ${mon} ${m[4]}:${m[5]}:${m[6]}`;
}
const say = (msg) => { $("status").textContent = msg; };
const row = (k, v) => (v === undefined || v === null || v === "" ? "" : `<tr><th>${esc(k)}</th><td>${v}</td></tr>`);
const alertText = (a) => `${a.type} · ${a.service}${a.operation ? " · " + a.operation : ""}`;

// ---------------------------------------------------------------- header, pending question
function renderHealth(h) {
  const a = h.action_api || {};
  const apiTxt = !a.configured ? "Action API not configured" : a.reachable ? "Action API up" : "Action API unreachable";
  const run = h.agent_running ? `running ${h.mode} (${h.run_id})` : "idle";
  say(`Agent ${run} · ${apiTxt} · model ${h.model}` + (h.last_error ? ` · error: ${h.last_error}` : ""));
  $("run-now").disabled = $("run-watch").disabled = h.agent_running;
  const killed = a.kill_switch || h.stop_file;
  $("kill").classList.toggle("on", !!killed);
  $("kill").textContent = killed ? "Kill switch ON" : "Kill switch";
  $("kill").disabled = !a.reachable;
}

function renderPending(p) {
  const el = $("pending");
  const key = p ? p.id : null;
  if (key === lastPending) return;        // don't wipe what the user is typing
  lastPending = key;
  if (!p) { el.hidden = true; el.innerHTML = ""; return; }
  const yesNo = ["approve", "rollback"].includes(p.kind);
  const simple = ["acknowledge", "action_api_down"].includes(p.kind);
  el.hidden = false;
  el.innerHTML = `<div class="box"><h3>Agent is waiting · run ${esc(p.run_id)}</h3><pre>${esc(p.prompt)}</pre>
    <div class="actions">${
      yesNo ? `<button class="primary" data-a="yes">${p.kind === "rollback" ? "Roll back" : "Approve"}</button><button data-a="no">${p.kind === "rollback" ? "Keep" : "Reject"}</button>`
      : simple ? `<button class="primary" data-a="">${p.kind === "action_api_down" ? "Check again" : "OK"}</button>`
      : `<input id="answer" placeholder="${p.kind === "name" ? "Your name" : "Answer"}"><button class="primary" id="send">Send</button>`
    }</div></div>`;
  el.querySelectorAll("button[data-a]").forEach((b) => (b.onclick = () => answer(p.id, b.dataset.a)));
  const send = $("send");
  if (send) {
    const go = () => answer(p.id, $("answer").value);
    send.onclick = go;
    $("answer").onkeydown = (e) => { if (e.key === "Enter") go(); };
    $("answer").focus();
  }
}

async function answer(id, text) {
  try { await api("/api/answer", { id, answer: text }); } catch (e) { say(e.message); }
  lastPending = undefined;
  tick();
}

// ---------------------------------------------------------------- incidents
function renderRuns(list) {
  if (!selected && list.length) selected = list[0].id;
  $("runs").innerHTML = list.map((r) => `
    <li data-id="${esc(r.id)}" class="${r.id === selected ? "sel" : ""}">
      <div class="row"><span>${esc(when(r.id))}</span><span class="tag${r.active ? " dark" : ""}">${esc(r.status)}</span></div>
      <div class="svc">${esc(r.component || r.service || "-")}${r.category ? " · " + esc(r.category) : ""}</div>
    </li>`).join("") || `<li class="muted">No runs yet.</li>`;
}

function renderDetail(d) {
  const diag = d.diagnosis || {}, rc = diag.root_cause || {}, plan = d.plan, val = d.validation, appr = d.approval;
  const alerts = (d.alerts || []).map((a) => `${esc(alertText(a))} (${esc(a.value)} vs ${esc(a.baseline)})`).join("<br>");
  const ev = (diag.evidence || []).map((e) => `<tr><td class="mono">${esc(e.id)}</td><td>${esc(e.observed || e.why)}${e.anomalous ? ' <span class="tag">abnormal</span>' : ""}</td></tr>`).join("");
  const conf = typeof diag.confidence === "number" ? Math.round(diag.confidence * 100) + "%" : null;
  const tools = (diag.tool_trail || []).map((t) => `${t.tool}${t.by === "sweep" ? " (sweep)" : ""}`).join(", ");
  const html = `
    <div class="box"><h2>${esc(d.title)}</h2>
      <div class="muted">${esc(when(d.id))} · <span class="tag">${esc(d.status)}</span>${d.outcome ? " · " + esc(d.outcome) : ""}</div></div>
    ${alerts ? `<div class="box"><h3>Alerts</h3>${alerts}</div>` : ""}
    ${diag.root_cause ? `<div class="box"><h3>Diagnosis</h3><table>
      ${row("Service", esc(rc.service))}${row("Category", esc(rc.category))}${row("Cause", esc(rc.description))}
      ${row("Confidence", conf)}${row("Decision", esc(diag.decision))}${row("Model", esc(diag.model))}
      ${row("Tools used", esc(tools))}${row("Took", diag.investigation_seconds ? esc(diag.investigation_seconds) + " s" : null)}
      ${row("Checks", (diag.validation_notes || []).map(esc).join("<br>"))}</table>
      ${ev ? `<details class="mt"><summary>Evidence (${(diag.evidence || []).length})</summary><table>${ev}</table></details>` : ""}</div>` : ""}
    ${plan ? `<div class="box"><h3>Fix</h3><table>
      ${row("Action", `<span class="mono">${esc(plan.action)}</span>`)}${row("Params", `<span class="mono">${esc(JSON.stringify(plan.params || {}))}</span>`)}
      ${row("Dry run", esc(plan.dry_run_preview))}${row("Rollback", esc(plan.rollback))}
      ${appr ? row("Approval", esc(appr.approved ? `approved by ${appr.by}` : `rejected${appr.by ? " by " + appr.by : ""}`)) : ""}</table></div>` : ""}
    ${val ? `<div class="box"><h3>Verification</h3><table>
      ${row("Recovered", val.recovered ? "yes" : "no")}${row("Target recovered", val.target_recovered === undefined ? null : val.target_recovered ? "yes" : "no")}
      ${row("Still alerting", (val.other_alerts || []).map((a) => esc(alertText(a))).join("<br>"))}</table></div>` : ""}
    ${d.report_md ? `<div class="box"><details><summary>Full report</summary><pre class="mt">${esc(d.report_md)}</pre></details></div>` : ""}`;
  if (html !== lastDetail) { lastDetail = html; $("detail").innerHTML = html; }
}

// conversation: what the agent sent the model, every tool call and its output, thinking, the diagnosis,
// the code checks, and every question to the human with the answer, interleaved with the run's timeline
const WHO = {
  prompt: ["note", "Agent → model", "alert and sweep results sent to the model"],
  sweep: ["sweep", "Sweep", ""], tool: ["tool", "Model called", ""], thinking: ["model", "Model thinking", ""],
  model: ["model", "Model", ""], diagnosis: ["model", "Model diagnosis", ""], retry: ["note", "Check", ""],
  limit: ["note", "Limit", ""], validation: ["note", "Code validation", ""],
  question: ["human", "Agent asks", ""], answer: ["human", "Human", ""],
};

function convoItem(c) {
  if (c.phase) return `<div class="event"><span class="mono">${esc(c.t)}</span>${esc(c.phase)} · ${esc(c.msg)}</div>`;
  const [cls, who] = WHO[c.kind] || ["note", c.kind];
  const head = `<div class="who"><span class="mono">${esc(c.t)}</span><b>${esc(who)}</b>${c.round > 1 ? `<span>round ${c.round}</span>` : ""}</div>`;
  if (c.kind === "sweep" || c.kind === "tool") {
    const args = c.args && Object.keys(c.args).length ? JSON.stringify(c.args) : "";
    return `<div class="msg ${cls}">${head}<span class="mono">${esc(c.tool)}(${esc(args)})</span>
      <details><summary>output (${(c.output || "").length} chars)</summary><pre class="out">${esc(c.output)}</pre></details></div>`;
  }
  if (c.kind === "prompt" || c.kind === "thinking" || c.kind === "diagnosis")
    return `<div class="msg ${cls}">${head}<details${c.kind === "diagnosis" ? " open" : ""}><summary>${c.kind === "prompt" ? "show prompt" : "show"}</summary><pre class="out">${esc(c.text)}</pre></details></div>`;
  if (c.kind === "answer") return `<div class="msg ${cls}">${head}${c.text ? esc(c.text) : '<span class="muted">(enter)</span>'}</div>`;
  if (c.kind === "validation") return `<div class="msg ${cls}">${head}${esc(c.decision)} · confidence ${esc(c.confidence)}<br>${esc(c.text)}</div>`;
  return `<div class="msg ${cls}">${head}<pre>${esc(c.text)}</pre></div>`;
}

async function renderConversation() {
  const data = await api(`/api/incidents/${selected}/conversation`);
  const items = [...data.timeline, ...data.conversation].map((x, i) => ({ ...x, i }))
    .sort((a, b) => (a.t < b.t ? -1 : a.t > b.t ? 1 : a.i - b.i));
  const el = $("conversation");
  if (convo.run !== selected || items.length < convo.n) { el.innerHTML = ""; convo = { run: selected, n: 0 }; }
  if (!items.length) { el.innerHTML = '<p class="muted">Nothing recorded for this run (runs before this feature only have the timeline).</p>'; return; }
  if (items.length === convo.n) return;               // append only, so open sections stay open
  if (convo.n === 0) el.innerHTML = "";
  el.insertAdjacentHTML("beforeend", items.slice(convo.n).map(convoItem).join(""));
  convo.n = items.length;
}

// ---------------------------------------------------------------- live system, action log
function renderLive(l) {
  const hot = new Set((l.alerts || []).map((a) => a.service));
  const svcs = Object.entries(l.services || {}).sort((a, b) => a[0].localeCompare(b[0]));
  const html = `
    <div class="box"><h3>Detector right now · ${esc(l.at)}</h3>${
      (l.alerts || []).length ? l.alerts.map((a) => `${esc(alertText(a))} (${esc(a.value)} vs ${esc(a.baseline)})`).join("<br>") : '<span class="muted">no alerts</span>'}
      ${(l.errors || []).length ? `<div class="muted mt">${l.errors.map(esc).join("<br>")}</div>` : ""}</div>
    <div class="box"><h3>Services (last 2 min vs baseline)</h3><table>
      <tr><th>Service</th><th class="num">req/s</th><th class="num">errors</th><th class="num">base errors</th><th class="num">p95 ms</th><th class="num">base p95 ms</th></tr>
      ${svcs.map(([s, v]) => `<tr class="${hot.has(s) ? "hot" : ""}"><td>${esc(s)}</td><td class="num">${num(v.rps, 2)}</td><td class="num">${pct(v.err_rate)}</td>
        <td class="num">${pct(v.base_err_rate)}</td><td class="num">${num(v.p95_ms)}</td><td class="num">${num(v.base_p95_ms)}</td></tr>`).join("")}
    </table></div>
    <div class="box"><h3>Containers</h3><table>
      <tr><th>Name</th><th>State</th><th>Status</th></tr>
      ${(l.containers || []).sort((a, b) => a.name.localeCompare(b.name)).map((c) => `<tr class="${c.state !== "running" ? "hot" : ""}"><td>${esc(c.name)}</td><td>${esc(c.state)}</td><td>${esc(c.status)}</td></tr>`).join("")}
    </table></div>`;
  if (html !== lastLive) { lastLive = html; $("live").innerHTML = html; }
}

function renderAudit(entries) {
  const html = `<div class="box"><h3>Action API audit log (newest first)</h3><table>
    <tr><th>Time</th><th>Call</th><th>Action</th><th>Result</th><th>Approved by</th></tr>
    ${entries.map((e) => `<tr><td class="mono">${esc((e.t || "").slice(11))}</td><td>${esc(e.endpoint)}</td>
      <td class="mono">${esc(e.action || (e.endpoint === "kill" ? `kill ${e.on ? "on" : "off"}` : ""))}${e.params ? " " + esc(JSON.stringify(e.params)) : ""}</td>
      <td>${esc(e.status || "")}${e.why ? `<div class="muted">${esc(e.why)}</div>` : ""}</td>
      <td>${esc((e.approval && e.approval.approved_by) || e.by || "")}</td></tr>`).join("") || '<tr><td class="muted">empty</td></tr>'}
  </table></div>`;
  if (html !== lastAudit) { lastAudit = html; $("audit").innerHTML = html; }
}

// ---------------------------------------------------------------- polling
async function tick() {
  try {
    renderHealth(await api("/api/health"));
    renderPending((await api("/api/pending")).pending);
    if (view === "incidents") {
      renderRuns((await api("/api/incidents")).incidents);
      if (selected && tab === "overview") renderDetail(await api(`/api/incidents/${selected}`));
      if (selected && tab === "conversation") await renderConversation();
    } else if (view === "live") {
      renderLive(await api("/api/live"));
    } else if (view === "audit") {
      try { renderAudit((await api("/api/audit")).entries || []); }
      catch (e) { $("audit").innerHTML = `<div class="box muted">${esc(e.message)}</div>`; lastAudit = ""; }
    }
  } catch (e) {
    say(`Agent server unreachable (${e.message})`);
  }
}

$("nav").onclick = (e) => {
  const a = e.target.closest("a[data-view]");
  if (!a) return;
  view = a.dataset.view;
  document.querySelectorAll("#nav a").forEach((x) => x.classList.toggle("on", x === a));
  ["incidents", "live", "audit"].forEach((v) => ($(`view-${v}`).hidden = v !== view));
  tick();
};
$("tabs").onclick = (e) => {
  const a = e.target.closest("a[data-tab]");
  if (!a) return;
  tab = a.dataset.tab;
  document.querySelectorAll("#tabs a").forEach((x) => x.classList.toggle("on", x === a));
  $("detail").hidden = tab !== "overview";
  $("conversation").hidden = tab !== "conversation";
  tick();
};
$("runs").onclick = (e) => {
  const li = e.target.closest("li[data-id]");
  if (!li) return;
  selected = li.dataset.id; lastDetail = ""; convo = { run: null, n: 0 };
  tick();
};
async function start(mode) {
  try { const r = await api("/api/run", { mode }); selected = r.run_id; lastDetail = ""; convo = { run: null, n: 0 }; } catch (e) { say(e.message); }
  tick();
}
$("run-now").onclick = () => start("now");
$("run-watch").onclick = () => start("watch");
$("kill").onclick = async () => {
  const on = !$("kill").classList.contains("on");
  try { await api("/api/kill", { on, by: "desktop" }); } catch (e) { say(e.message); }
  tick();
};

tick();
setInterval(tick, 2000);
