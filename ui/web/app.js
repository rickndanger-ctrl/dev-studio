/* Dev Studio board. Draws the team as a chain (architect → main session → router → workers),
   the real message channels between them (everything travels agent → JEV → agent), the live
   feed, and details on click. Plain JavaScript; the only data source is /api/state. */
(function () {
  "use strict";
  const $ = (s, r) => (r || document).querySelector(s);
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const SVG = "http://www.w3.org/2000/svg";
  const ROUTER = "jev";
  const POLL_MS = 1500;
  const ACTIVE_MS = 8000;

  const state = { data: null, filter: "all", selected: null, seenHops: null, drawer: null, lastGood: 0 };
  const nodes = new Map();   // name -> card element
  const edges = new Map();   // key -> {path, arrow, label, a, b, color}

  // ---------- helpers ----------
  function age(t) {
    const s = Math.max(0, Math.round(Date.now() / 1000 - t));
    if (s < 60) return s + "s";
    if (s < 3600) return Math.round(s / 60) + "m";
    return Math.round(s / 3600) + "h";
  }
  function clock(t) { return new Date(t * 1000).toLocaleTimeString([], { hour12: false }); }
  function shortName(n) { const a = state.data && state.data.agents.find(x => x.name === n); return a ? a.short : (n === ROUTER ? "JEV" : "@" + n); }
  function roleOf(n) { const a = state.data && state.data.agents.find(x => x.name === n); return a ? a.role : (n === ROUTER ? "router" : "agent"); }
  function statusWord(st) { return { working: "working", blocked: "needs you", done: "finished", idle: "ready", unknown: "starting", offline: "offline" }[st] || st; }
  function trunc(s, n) { s = (s || "").replace(/\s+/g, " ").trim(); return s.length > n ? s.slice(0, n - 1) + "…" : s; }
  function pct(p) { return Math.round((p || 0) * 100) + "%"; }

  // ---------- header ----------
  function renderHeader(d) {
    $("#name").textContent = d.name + " · Dev Studio";
    const live = d.agents.filter(a => a.status !== "offline").length;
    const active = d.agents.filter(a => a.status === "working" || a.status === "blocked").length;
    const needs = d.agents.filter(a => a.status === "blocked").length;
    $("#sub").textContent = needs ? needs + " agent" + (needs > 1 ? "s need" : " needs") + " you" : active ? active + " working" : live ? "team ready" : "no team running";
    const chips = $("#chips"); chips.innerHTML = "";
    const chip = (k, v, title) => { const c = el("span", "chip", null); c.title = title || ""; c.append(k + " "); const b = el("b", null, v); c.append(b); chips.append(c); };
    chip("agents", live + "/" + d.agents.length);
    chip("router", (d.router.backend || "jev") + " · " + d.router.decisions, "decisions so far");
    chip("rate", d.router.rate_per_min + "/min", "messages in the last minute");
    chip("queued", String(d.mail.queued), "messages waiting for the router");
    chip("memory", Math.round(d.mem_free_mb / 1024 * 10) / 10 + " GB free");
    if (d.peer) { const c = el("span", "chip"); const a = el("a", null, d.peer.name + " →"); a.href = d.peer.url; c.append(a); chips.append(c); }
  }

  // ---------- nodes ----------
  function cardFor(a) {
    let card = nodes.get(a.name);
    if (!card) {
      card = el("article", "node role-" + a.role);
      card.dataset.name = a.name;
      card.addEventListener("click", () => openAgent(a.name));
      nodes.set(a.name, card);
    }
    card.className = "node role-" + a.role + (state.selected === a.name ? " selected" : "");
    const roleTitle = { architect: (a.model || "opus").toUpperCase() + " · session architect", main: (a.model || "sonnet") + " · main session",
      worker: (a.model || "haiku") + " · worker" }[a.role] || a.role;
    const sent = a.last_sent, got = a.last_got;
    card.innerHTML = "";
    const top = el("div", "node-top"); top.append(el("span", "role", roleTitle)); top.append(el("span", "pill st-" + a.status, statusWord(a.status))); card.append(top);
    const name = el("div", "node-name"); name.append(el("span", null, a.short)); name.append(el("span", "model", a.model)); card.append(name);
    const now = el("div", "now"); now.append(el("span", "k", "now")); now.append(el("span", "v" + (a.now ? "" : " quiet"), a.now || (a.status === "offline" ? "not running" : "quiet"))); card.append(now);
    const io = el("div", "io");
    const out = el("span", "v"); out.append(el("span", "k", "sent "));
    if (sent) { const to = (sent.to || []).map(shortName).join(", ") || "nobody"; out.append(el("b", null, "→ " + to)); out.append(el("span", "age", age(sent.t))); out.append(" " + trunc(sent.text, 48)); }
    else out.append("nothing yet");
    const inn = el("span", "v"); inn.append(el("span", "k", "got "));
    if (got) { inn.append(el("b", null, "← " + shortName(got.from))); inn.append(el("span", "age", age(got.t))); inn.append(" " + trunc(got.text, 48)); }
    else inn.append("nothing yet");
    io.append(el("span", "k", "out"), out, el("span", "k", "in"), inn); card.append(io);
    return card;
  }

  function routerCard(d) {
    let card = nodes.get(ROUTER);
    if (!card) { card = el("article", "node role-router"); card.dataset.name = ROUTER; card.addEventListener("click", () => openRouter()); nodes.set(ROUTER, card); }
    card.className = "node role-router" + (state.selected === ROUTER ? " selected" : "");
    const r = d.router, lm = r.last_model, last = r.last;
    card.innerHTML = "";
    const top = el("div", "node-top"); top.append(el("span", "role", "JEV · message router"));
    top.append(el("span", "pill " + (d.mail.queued ? "st-working" : "st-idle"), d.mail.queued ? d.mail.queued + " queued" : "listening")); card.append(top);
    const bars = el("div", "bars");
    const row = (k, p, note, cls) => { bars.append(el("span", "k", k)); const b = el("div", "bar" + (p == null ? " dim" : "")); const i = el("i"); i.style.width = pct(p); b.append(i); bars.append(b);
      const n = el("span", "n" + (cls ? " " + cls : ""), p == null ? "–" : (p).toFixed(2) + (note ? "  " + note : "")); bars.append(n); };
    if (lm) {
      row("which peer", lm.p_peer, "→ " + shortName(lm.peer), (lm.c_peer || 0) >= 0.5 ? "sharp" : "soft");
      row("action", lm.p_action, lm.softened || lm.action);
      row("no token leak", 1 - (lm.leak || 0), lm.trimmed ? "trimmed" : "");
    } else { row("which peer", null); row("action", null); row("no token leak", null); }
    card.append(bars);
    const st = el("div", "rstat");
    st.append(el("span", null, "decisions ")); st.lastChild.append(el("b", null, String(r.decisions)));
    st.append(el("span", null, "latency ")); st.lastChild.append(el("b", null, lm && lm.ms ? Math.round(lm.ms) + " ms" : "–"));
    st.append(el("span", null, "backend ")); st.lastChild.append(el("b", null, r.backend || "jev"));
    card.append(st);
    const rl = el("div", "rlast");
    if (last) { rl.append(el("b", null, shortName(last.from) + " → " + ((last.to || []).map(shortName).join(", ") || "nobody"))); rl.append("  " + (last.decision || "") + " · " + trunc(last.text, 60)); }
    else rl.append("waiting for the first message");
    card.append(rl);
    return card;
  }

  function renderNodes(d) {
    const layers = { architect: $('[data-layer="architect"]'), main: $('[data-layer="main"]'), router: $('[data-layer="router"]'), worker: $('[data-layer="worker"]') };
    const keep = new Set();
    for (const a of d.agents) {
      const card = cardFor(a); keep.add(a.name);
      const layer = layers[a.role] || layers.worker;
      if (card.parentElement !== layer) layer.append(card);
    }
    const rc = routerCard(d); keep.add(ROUTER); if (rc.parentElement !== layers.router) layers.router.append(rc);
    for (const [name, card] of nodes) if (!keep.has(name)) { card.remove(); nodes.delete(name); }
    $("#empty").hidden = d.agents.length > 0;
  }

  // ---------- wires ----------
  function rectOf(name) {
    const card = nodes.get(name); if (!card) return null;
    const s = $("#stage").getBoundingClientRect(), r = card.getBoundingClientRect(), stage = $("#stage");
    return { x: r.left - s.left + stage.scrollLeft, y: r.top - s.top + stage.scrollTop, w: r.width, h: r.height,
      cx: r.left - s.left + stage.scrollLeft + r.width / 2, cy: r.top - s.top + stage.scrollTop + r.height / 2 };
  }
  function edgeGeometry(a, b) {
    // a is always the agent, b the router. Path direction: from a to b.
    const A = rectOf(a), B = rectOf(b); if (!A || !B) return null;
    const role = roleOf(a);
    if (role === "architect") {   // two layers up: swing out to the left so it does not cross the main session
      const x0 = A.x, y0 = A.cy, x1 = B.x, y1 = B.cy;
      const bulge = Math.max(40, Math.min(Math.max(90, (B.y - A.y) * 0.35), Math.min(x0, x1) - 24));   // stay inside the stage
      return { d: `M ${x0} ${y0} C ${x0 - bulge} ${y0}, ${x1 - bulge} ${y1}, ${x1} ${y1}`, mid: { x: Math.max(140, Math.min(x0, x1) - bulge * 0.72), y: (y0 + y1) / 2 } };
    }
    if (role === "worker") {       // below the router: from the router's bottom down to the worker's top
      const x0 = B.cx, y0 = B.y + B.h, x1 = A.cx, y1 = A.y, k = Math.max(30, (y1 - y0) * 0.5);
      return { d: `M ${x0} ${y0} C ${x0} ${y0 + k}, ${x1} ${y1 - k}, ${x1} ${y1}`, mid: { x: (x0 + x1) / 2, y: (y0 + y1) / 2 }, reversed: true };
    }
    const x0 = A.cx, y0 = A.y + A.h, x1 = B.cx, y1 = B.y, k = Math.max(30, (y1 - y0) * 0.5);
    return { d: `M ${x0} ${y0} C ${x0} ${y0 + k}, ${x1} ${y1 - k}, ${x1} ${y1}`, mid: { x: (x0 + x1) / 2, y: (y0 + y1) / 2 } };
  }
  function chainGeometry(a, b) {   // the chain of command, architect → main session, dotted
    const A = rectOf(a), B = rectOf(b); if (!A || !B) return null;
    const x0 = A.cx, y0 = A.y + A.h, x1 = B.cx, y1 = B.y, k = Math.max(20, (y1 - y0) * 0.5);
    return { d: `M ${x0} ${y0} C ${x0} ${y0 + k}, ${x1} ${y1 - k}, ${x1} ${y1}` };
  }

  function ensureEdge(key, color, chain) {
    let e = edges.get(key);
    if (e) return e;
    const svg = $("#wires");
    const path = document.createElementNS(SVG, "path"); path.setAttribute("class", "edge c-" + color + (chain ? " chain" : "")); path.id = "edge-" + key.replace(/[^a-z0-9_-]/gi, "_");
    svg.append(path);
    let label = null;
    if (!chain) { label = el("div", "wire-label"); label.hidden = true; $("#labels").append(label); }
    e = { path, label, color, lastT: 0, hopKey: key };
    edges.set(key, e);
    return e;
  }

  function renderWires(d) {
    const svg = $("#wires"), stage = $("#stage");
    svg.setAttribute("width", stage.scrollWidth); svg.setAttribute("height", stage.scrollHeight);
    const arch = d.agents.find(a => a.role === "architect"), main = d.agents.find(a => a.role === "main");
    const keep = new Set();
    if (arch && main) { const g = chainGeometry(arch.name, main.name); if (g) { const e = ensureEdge("chain", "chain", true); e.path.setAttribute("d", g.d); keep.add("chain"); } }
    for (const a of d.agents) {
      const g = edgeGeometry(a.name, ROUTER); if (!g) continue;
      const e = ensureEdge(a.name, a.role, false); keep.add(a.name);
      e.path.setAttribute("d", g.d); e.reversed = !!g.reversed; e.geom = g;
      // the newest hop on this channel, either direction
      const hop = lastHop(d, a.name);
      const active = hop && (Date.now() / 1000 - hop.t) < ACTIVE_MS / 1000;
      e.path.classList.toggle("active", !!active);
      if (e.label) {
        if (hop) {
          e.label.hidden = false; e.label.className = "wire-label" + (active ? " active" : "");
          e.label.innerHTML = ""; const dir = hop.from === a.name ? "→ JEV" : "← JEV";
          e.label.append(el("b", null, dir)); e.label.append(" " + trunc(hop.text, 42)); e.label.append(el("span", "age", age(hop.t)));
          e.label.style.left = g.mid.x + "px"; e.label.style.top = g.mid.y + "px";
          e.label.onclick = () => openMessage(hop.id);
        } else e.label.hidden = true;
      }
    }
    for (const [k, e] of edges) if (!keep.has(k)) { e.path.remove(); if (e.label) e.label.remove(); edges.delete(k); }
  }
  function lastHop(d, name) {
    for (let i = d.hops.length - 1; i >= 0; i--) { const h = d.hops[i]; if (h.from === name || h.to === name) return h; }
    return null;
  }

  // a message travels sender → JEV → recipient: one pulse on each channel, the second after the first
  function pulse(edgeKey, towardsRouter, color, delayMs) {
    const e = edges.get(edgeKey); if (!e) return;
    const svg = $("#wires");
    const c = document.createElementNS(SVG, "circle"); c.setAttribute("r", "4.5"); c.setAttribute("class", "pulse c-" + color);
    const m = document.createElementNS(SVG, "animateMotion"); m.setAttribute("dur", "1.1s"); m.setAttribute("fill", "freeze"); m.setAttribute("begin", (delayMs / 1000) + "s");
    // path direction is agent → router for architect/main, router → worker for workers
    const forward = e.reversed ? !towardsRouter : towardsRouter;
    if (!forward) { m.setAttribute("keyPoints", "1;0"); m.setAttribute("keyTimes", "0;1"); m.setAttribute("calcMode", "linear"); }
    const mp = document.createElementNS(SVG, "mpath"); mp.setAttributeNS("http://www.w3.org/1999/xlink", "href", "#" + e.path.id); mp.setAttribute("href", "#" + e.path.id);
    m.append(mp); c.append(m); svg.append(c);
    setTimeout(() => c.remove(), delayMs + 1400);
  }
  function animateNewHops(d) {
    const seen = state.seenHops;
    const now = Date.now() / 1000;
    if (!seen) { state.seenHops = new Set(d.hops.map(h => h.id + ":" + h.to)); return; }
    for (const h of d.hops) {
      const k = h.id + ":" + h.to; if (seen.has(k)) continue; seen.add(k);
      if (now - h.t > 30) continue;
      pulse(h.from, true, roleOf(h.from), 0);
      pulse(h.to, false, roleOf(h.to), 900);
    }
  }

  // ---------- feed ----------
  function feedRow(r) {
    const li = el("li", "f " + r.kind); li.dataset.id = r.id || ""; li.dataset.kind = r.kind;
    li.append(el("time", null, clock(r.t)));
    const body = el("div"); const l1 = el("div", "l1"); body.append(l1);
    if (r.kind === "route") {
      l1.append(el("span", "tag route", "jev"));
      if (r.verified === false) l1.append(el("span", "unv", "unverified"));
      l1.append(el("b", null, shortName(r.from))); l1.append(el("span", "arrow-t", "→")); l1.append(el("b", null, (r.to || []).map(shortName).join(", ") || "nobody"));
      l1.append(el("span", null, r.softened || r.decision || ""));
      if (r.p_peer != null) l1.append(el("span", "pv " + ((r.c_peer || 0) >= 0.5 ? "sharp" : "soft"), r.p_peer.toFixed(2) + ((r.c_peer || 0) >= 0.5 ? " sharp" : " soft")));
      else if (r.note) l1.append(el("span", "pv", trunc(r.note, 40)));
      body.append(el("div", "l2", trunc(r.text, 120)));
      li.addEventListener("click", () => openMessage(r.id));
    } else if (r.kind === "state") {
      l1.append(el("span", "tag state", "status")); l1.append(el("b", null, shortName(r.who)));
      l1.append(el("span", "st-word " + r.was, r.was)); l1.append(el("span", "arrow-t", "→")); l1.append(el("span", "st-word " + r.now, r.now));
      li.addEventListener("click", () => openAgent(r.who));
    } else if (r.kind === "delivered" || r.kind === "held" || r.kind === "failed") {
      l1.append(el("span", "tag " + r.kind, r.kind)); l1.append(el("b", null, shortName(r.from))); l1.append(el("span", "arrow-t", "→")); l1.append(el("b", null, shortName(r.to)));
      if (r.code) l1.append(el("span", "pv", r.code));
      li.addEventListener("click", () => openMessage(r.id));
    } else {
      l1.append(el("span", "tag " + r.kind, r.kind)); l1.append(el("span", null, r.note || r.error || (r.router ? "router " + r.router + " up" : "")));
    }
    li.append(body);
    return li;
  }
  function renderFeed(d) {
    const list = $("#feed"); const atTop = list.scrollTop < 8;
    list.innerHTML = "";
    const f = state.filter;
    const rows = d.feed.filter(r => f === "all" || (f === "route" && r.kind === "route") || (f === "state" && r.kind === "state") || (f === "delivery" && ["delivered", "held", "failed"].includes(r.kind)));
    for (let i = rows.length - 1; i >= 0 && list.children.length < 150; i--) list.append(feedRow(rows[i]));
    if (!rows.length) { const li = el("li", "f"); li.append(el("time", null, "")); li.append(el("div", "l2", "Nothing yet. Give the architect a task in the team tab and watch here.")); list.append(li); }
    if (atTop) list.scrollTop = 0;
  }

  // ---------- drawers ----------
  function openDrawer(title) { $("#drawer").hidden = false; $("#drawer-title").textContent = title; const b = $("#drawer-body"); b.innerHTML = ""; return b; }
  function closeDrawer() { $("#drawer").hidden = true; state.drawer = null; state.selected = null; if (state.data) renderNodes(state.data); }
  $("#drawer-close").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", e => { if (e.key === "Escape") closeDrawer(); });

  function kv(parent, pairs) { const g = el("div", "kv"); for (const [k, v] of pairs) { g.append(el("span", "k", k)); const s = el("span", "v"); if (v instanceof Node) s.append(v); else s.textContent = v; g.append(s); } parent.append(g); }

  async function openAgent(name) {
    state.selected = name; state.drawer = { kind: "agent", name }; renderNodes(state.data);
    const b = openDrawer(shortName(name));
    b.append(el("div", "section", "loading…"));
    await refreshAgentDrawer();
  }
  async function refreshAgentDrawer() {
    if (!state.drawer || state.drawer.kind !== "agent") return;
    const name = state.drawer.name;
    let d; try { d = await (await fetch("/api/agent?name=" + encodeURIComponent(name))).json(); } catch (e) { return; }
    if (!state.drawer || state.drawer.name !== name) return;
    const b = $("#drawer-body"); const scroll = b.scrollTop; b.innerHTML = "";
    const pill = el("span", "pill st-" + d.status, statusWord(d.status));
    kv(b, [["status", pill], ["role", d.desc], ["model", d.model], ["now", d.now || "quiet"]]);
    b.append(el("div", "section", "terminal · last " + d.tail.length + " lines"));
    b.append(el("pre", "tail", d.tail.length ? d.tail.join("\n") : "(not running)"));
    b.append(el("div", "section", "messages"));
    const msgs = d.messages.filter(m => m.kind === "route");
    if (!msgs.length) b.append(el("div", "msg", "none yet"));
    for (let i = msgs.length - 1; i >= 0; i--) {
      const m = msgs[i]; const box = el("div", "msg"); const h = el("div", "h");
      h.append(el("b", null, shortName(m.from) + " → " + ((m.to || []).map(shortName).join(", ") || "nobody"))); h.append(el("span", null, clock(m.t) + " · " + (m.softened || m.decision)));
      box.append(h); box.append(el("div", "body", m.text)); box.addEventListener("click", () => openMessage(m.id)); b.append(box);
    }
    b.append(el("div", "section", "history"));
    const hist = el("div", "hist");
    for (let i = d.history.length - 1; i >= 0; i--) { const e = d.history[i]; hist.append(el("span", null, clock(e.t))); hist.append(el("span", null, e.was + " → " + e.now)); }
    if (!d.history.length) hist.append(el("span", null, "–"), el("span", null, "no changes seen since this page started"));
    b.append(hist);
    b.scrollTop = scroll;
  }

  function openMessage(id) {
    const d = state.data; if (!d || !id) return;
    const r = d.feed.find(x => x.kind === "route" && x.id === id); if (!r) return;
    state.drawer = { kind: "message", id }; state.selected = null; renderNodes(d);
    const b = openDrawer("Message " + id);
    kv(b, [["from", shortName(r.from)], ["addressed to", r.addressed === "auto" ? "auto (router decides)" : shortName(r.addressed)],
      ["delivered to", (r.to || []).map(shortName).join(", ") || "nobody"], ["decision", (r.softened || r.decision || "") + (r.trimmed ? " · trimmed" : "")],
      ["router", (r.router || "") + (r.ms ? " · " + Math.round(r.ms) + " ms" : "")], ["sender", r.verified === false ? "UNVERIFIED · " + (r.unverified || "") : "verified by its own token"],
      ["time", clock(r.t)]]);
    b.append(el("div", "section", "text"));
    b.append(el("pre", "tail", r.text));
    if (r.probabilities) {
      const P = r.probabilities;
      b.append(el("div", "section", "which peer · confidence " + ((r.c_peer || 0)).toFixed(2)));
      const g1 = el("div", "probs");
      for (const [k, v] of Object.entries(P.peer || {}).sort((a, b) => b[1] - a[1])) { g1.append(el("span", "k", shortName(k))); const bar = el("div", "bar" + (v < 0.05 ? " dim" : "")); const i = el("i"); i.style.width = pct(v); bar.append(i); g1.append(bar); g1.append(el("span", "n", v.toFixed(2))); }
      b.append(g1);
      b.append(el("div", "section", "action"));
      const g2 = el("div", "probs");
      for (const [k, v] of Object.entries(P.action || {}).sort((a, b) => b[1] - a[1])) { g2.append(el("span", "k", k)); const bar = el("div", "bar" + (v < 0.05 ? " dim" : "")); const i = el("i"); i.style.width = pct(v); bar.append(i); g2.append(bar); g2.append(el("span", "n", v.toFixed(2))); }
      b.append(g2);
      b.append(el("div", "section", "token leak"));
      const g3 = el("div", "probs"); g3.append(el("span", "k", "pasted dump?")); const bar = el("div", "bar"); const i = el("i"); i.style.width = pct(r.leak); bar.append(i); g3.append(bar); g3.append(el("span", "n", (r.leak || 0).toFixed(2))); b.append(g3);
    } else if (r.note) { b.append(el("div", "section", "router note")); b.append(el("div", "msg", r.note)); }
    const dl = d.deliveries.filter(x => x.id === id);
    if (dl.length) { b.append(el("div", "section", "delivery")); for (const x of dl) b.append(el("div", "msg", clock(x.t) + " · " + x.kind + " → " + shortName(x.to) + (x.code ? " · " + x.code : ""))); }
    document.querySelectorAll("#feed .f").forEach(li => li.classList.toggle("selected", li.dataset.id === id && li.dataset.kind === "route"));
  }

  function openRouter() {
    const d = state.data; if (!d) return;
    state.selected = ROUTER; state.drawer = { kind: "router" }; renderNodes(d);
    const b = openDrawer("JEV · message router");
    kv(b, [["backend", d.router.backend], ["decisions", String(d.router.decisions)], ["last minute", d.router.rate_per_min + " messages"],
      ["mailbox", "queued " + d.mail.queued + " · delivered " + d.mail.delivered + " · held " + d.mail.held]]);
    b.append(el("div", "section", "how it decides"));
    b.append(el("div", "msg", "Every message an agent sends with `studio send` lands in the mailbox. The router asks Jev three typed questions in one request — which peer needs this first, forward / broadcast / escalate / drop, and is it a note or a pasted dump — then decides in code: an unsure answer goes to the architect, a dump is trimmed, and the note is typed into the recipient's terminal. Click any route in the feed to see the full probabilities."));
    b.append(el("div", "section", "recent decisions"));
    const routes = d.feed.filter(r => r.kind === "route");
    for (let i = routes.length - 1; i >= Math.max(0, routes.length - 25); i--) {
      const r = routes[i]; const box = el("div", "msg"); const h = el("div", "h");
      h.append(el("b", null, shortName(r.from) + " → " + ((r.to || []).map(shortName).join(", ") || "nobody")));
      h.append(el("span", null, clock(r.t) + " · " + (r.softened || r.decision) + (r.p_peer != null ? " · " + r.p_peer.toFixed(2) : "")));
      box.append(h); box.append(el("div", "body", trunc(r.text, 160))); box.addEventListener("click", () => openMessage(r.id)); b.append(box);
    }
  }

  // ---------- filters ----------
  $("#filters").addEventListener("click", e => {
    const btn = e.target.closest("button"); if (!btn) return;
    state.filter = btn.dataset.f; document.querySelectorAll("#filters button").forEach(x => x.classList.toggle("on", x === btn));
    if (state.data) renderFeed(state.data);
  });

  // ---------- loop ----------
  async function tick() {
    let d;
    try { const r = await fetch("/api/state", { cache: "no-store" }); if (!r.ok) throw new Error(r.status); d = await r.json(); }
    catch (e) { $("#pulse").className = "live-dot stale"; $("#sub").textContent = "reconnecting…"; return; }
    state.data = d; state.lastGood = Date.now();
    $("#pulse").className = "live-dot " + ((Date.now() / 1000 - d.updated) > 6 ? "stale" : "ok");
    renderHeader(d); renderNodes(d);
    renderWires(d); animateNewHops(d);   // synchronous: a hidden tab never gets an animation frame
    renderFeed(d);
    if (state.drawer && state.drawer.kind === "agent") refreshAgentDrawer();
  }
  tick(); setInterval(tick, POLL_MS);
  window.addEventListener("resize", () => { if (state.data) renderWires(state.data); });
  $("#stage").addEventListener("scroll", () => { if (state.data) renderWires(state.data); });
})();
