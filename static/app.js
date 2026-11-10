/* Glücksgrube – Client
   Alle Texte werden als Textknoten eingefügt (nie als HTML), damit Spielernamen nichts einschleusen können. */
(() => {
  "use strict";

  /* ---------- Helfer ---------- */
  const $ = (s, r = document) => r.querySelector(s);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const fmt = (n) => Number(n || 0).toLocaleString("de-DE");
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* egal */ } },
    del(k) { try { localStorage.removeItem(k); } catch (e) { /* egal */ } },
  };

  function el(tag, attrs, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") n.className = Array.isArray(v) ? v.filter(Boolean).join(" ") : v;
      else if (k.startsWith("on") && typeof v === "function") n.addEventListener(k.slice(2), v);
      else if (k === "style" && typeof v === "object") Object.assign(n.style, v);
      else n.setAttribute(k, v === true ? "" : v);
    }
    const add = (c) => {
      if (c === null || c === undefined || c === false) return;
      if (Array.isArray(c)) c.forEach(add);
      else n.append(c instanceof Node ? c : document.createTextNode(String(c)));
    };
    kids.forEach(add);
    return n;
  }

  const S = {
    token: store.get("grube_token"), me: null, cat: null, code: null, st: null, v: 0,
    hold: new Set(), busy: false, auto: false, turbo: store.get("grube_turbo") === "1",
    muted: store.get("grube_muted") === "1", lastEvent: null, deadlineAt: null, dispCoins: 0,
    poll: null, polling: false, lastFull: 0, built: false, ovSig: "", emoteSeen: {}, mode: "login", pendingRender: false,
  };
  let M = {};      // Maschine (DOM-Referenzen)

  /* ---------- API ---------- */
  async function api(path, opts = {}) {
    const res = await fetch("/api" + path, {
      method: opts.method || "GET",
      headers: { "Content-Type": "application/json", "X-Token": S.token || "" },
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    let data = {};
    try { data = await res.json(); } catch (e) { /* leer */ }
    if (res.status === 401 && S.token && !path.startsWith("/login")) { logoutLocal(true); }
    if (!res.ok) { const err = new Error(data.error || "Fehler (" + res.status + ")"); err.status = res.status; throw err; }
    return data;
  }

  function toast(msg, kind = "", ms = 3200) {
    const t = el("div", { class: ["toast", kind] }, msg);
    $("#toasts").append(t);
    setTimeout(() => t.remove(), ms);
  }

  /* ---------- Sound (WebAudio, alles synthetisch) ---------- */
  const sfx = (() => {
    let ctx = null, master = null;
    const ensure = () => {
      if (!ctx) {
        try { ctx = new (window.AudioContext || window.webkitAudioContext)(); master = ctx.createGain(); master.gain.value = 0.32; master.connect(ctx.destination); } catch (e) { return null; }
      }
      if (ctx.state === "suspended") ctx.resume();
      return ctx;
    };
    function tone(f, at = 0, d = 0.12, type = "sine", v = 0.4, slide) {
      if (S.muted || !ensure()) return;
      const o = ctx.createOscillator(), g = ctx.createGain(), t0 = ctx.currentTime + at;
      o.type = type; o.frequency.setValueAtTime(f, t0);
      if (slide) o.frequency.exponentialRampToValueAtTime(slide, t0 + d);
      g.gain.setValueAtTime(0.0001, t0); g.gain.exponentialRampToValueAtTime(v, t0 + 0.01); g.gain.exponentialRampToValueAtTime(0.0001, t0 + d);
      o.connect(g); g.connect(master); o.start(t0); o.stop(t0 + d + 0.03);
    }
    const scale = [523, 659, 784, 1047, 1319, 1568];
    return {
      unlock: ensure,
      lever() { tone(260, 0, 0.22, "sawtooth", 0.18, 80); },
      stop(i) { tone(210 + i * 45, 0, 0.09, "square", 0.16); },
      win(level) { const n = [3, 5, 8, 11][level] || 3; for (let i = 0; i < n; i++) tone(scale[i % 6] * (i > 5 ? 2 : 1), i * 0.075, 0.2, "triangle", 0.28); },
      jackpot() { for (let i = 0; i < 18; i++) tone(scale[i % 6] * (1 + Math.floor(i / 6)), i * 0.06, 0.25, "square", 0.2); },
      skull() { tone(160, 0, 0.35, "sawtooth", 0.3, 50); tone(120, 0.15, 0.35, "square", 0.2, 40); },
      buy() { tone(880, 0, 0.08, "square", 0.2); tone(1320, 0.08, 0.14, "square", 0.2); },
      hit() { tone(140, 0, 0.3, "sawtooth", 0.35, 45); },
      knock() { tone(90, 0, 0.12, "square", 0.4); tone(90, 0.22, 0.12, "square", 0.4); tone(90, 0.44, 0.2, "square", 0.4); },
      pay() { tone(988, 0, 0.1, "triangle", 0.3); tone(1319, 0.1, 0.2, "triangle", 0.3); },
      fail() { tone(300, 0, 0.5, "sawtooth", 0.3, 70); },
      tick() { tone(1200, 0, 0.04, "square", 0.1); },
      fanfare() { [523, 659, 784, 1047, 784, 1047, 1319].forEach((f, i) => tone(f, i * 0.13, 0.3, "triangle", 0.3)); },
    };
  })();

  /* ---------- Partikel ---------- */
  const fx = (() => {
    const cv = $("#fx"), ctx = cv.getContext("2d");
    let parts = [], raf = null, dpr = 1;
    function size() { dpr = window.devicePixelRatio || 1; cv.width = innerWidth * dpr; cv.height = innerHeight * dpr; ctx.setTransform(dpr, 0, 0, dpr, 0, 0); }
    window.addEventListener("resize", size); size();
    const colors = ["#ff3ea5", "#ffd23f", "#2de2e6", "#7cff6b", "#8b5cf6", "#ff7a59"];
    function loop() {
      ctx.clearRect(0, 0, innerWidth, innerHeight);
      parts = parts.filter((p) => p.life > 0 && p.y < innerHeight + 40);
      for (const p of parts) {
        p.vy += p.g; p.x += p.vx; p.y += p.vy; p.rot += p.vr; p.life -= 1;
        ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(p.rot); ctx.globalAlpha = Math.min(1, p.life / 25);
        if (p.emoji) { ctx.font = p.s + "px serif"; ctx.textAlign = "center"; ctx.fillText(p.emoji, 0, 0); }
        else { ctx.fillStyle = p.c; ctx.fillRect(-p.s / 2, -p.s / 4, p.s, p.s / 2); }
        ctx.restore();
      }
      raf = parts.length ? requestAnimationFrame(loop) : null;
      if (!raf) ctx.clearRect(0, 0, innerWidth, innerHeight);
    }
    const kick = () => { if (!raf) raf = requestAnimationFrame(loop); };
    return {
      confetti(power = 1) {
        for (let i = 0; i < 50 * power; i++) parts.push({ x: Math.random() * innerWidth, y: -20 - Math.random() * 120, vx: (Math.random() - 0.5) * 4, vy: Math.random() * 2, g: 0.12, s: 8 + Math.random() * 8, c: colors[i % colors.length], rot: Math.random() * 6, vr: (Math.random() - 0.5) * 0.4, life: 140 + Math.random() * 60 });
        kick();
      },
      coins(x, y, n = 14, emoji = "🪙") {
        for (let i = 0; i < n; i++) { const a = -Math.PI / 2 + (Math.random() - 0.5) * 1.8, sp = 5 + Math.random() * 7; parts.push({ x, y, vx: Math.cos(a) * sp, vy: Math.sin(a) * sp, g: 0.35, s: 20 + Math.random() * 12, emoji, rot: 0, vr: (Math.random() - 0.5) * 0.3, life: 90 }); }
        kick();
      },
    };
  })();

  /* ---------- Katalog ---------- */
  const SYM = {}, CHARM = {}, SABO = {};
  const emoji = (id) => (SYM[id] ? SYM[id].emoji : "?");
  const hue = (s) => { let h = 0; for (const c of s) h = (h * 31 + c.charCodeAt(0)) % 360; return h; };
  const avatar = (name, size) => el("div", { class: "avatar", style: { background: "hsl(" + hue(name) + " 85% 65%)", width: (size || 40) + "px", height: (size || 40) + "px" } }, (name || "?")[0].toUpperCase());
  const RAR = { common: "Gewöhnlich", rare: "Selten", epic: "Episch" };

  /* ---------- Ansichten ---------- */
  const VIEWS = ["auth", "menu", "room", "game"];
  function show(name) {
    for (const v of VIEWS) $("#view-" + v).hidden = v !== name;
    $("#topbar").hidden = name === "auth";
    if (name !== "game") setOverlay(null);
  }

  function updateChip() {
    const chip = $("#chip");
    if (!S.me) { chip.hidden = true; return; }
    chip.hidden = false;
    chip.replaceChildren("Lv ", el("b", null, S.me.level), " · ", S.me.name);
  }

  function logoutLocal(silent) {
    stopPoll();
    S.token = null; S.me = null; S.code = null; S.st = null; S.auto = false; S.built = false;
    store.del("grube_token");
    show("auth");
    if (!silent) toast("Abgemeldet.");
  }

  /* ---------- Login ---------- */
  function setAuthMode(mode) {
    S.mode = mode;
    $("#tab-login").classList.toggle("active", mode === "login");
    $("#tab-register").classList.toggle("active", mode === "register");
    $("#auth-submit").textContent = mode === "login" ? "Anmelden" : "Spieler anlegen";
    $("#auth-hint").textContent = mode === "register" ? "Name: 2–16 Zeichen · PIN: mindestens 4 Zeichen. Merk sie dir – ohne sie kommst du nicht mehr rein." : "";
    $("#auth-pin").setAttribute("autocomplete", mode === "login" ? "current-password" : "new-password");
  }

  async function submitAuth(e) {
    e.preventDefault();
    const btn = $("#auth-submit");
    btn.disabled = true;
    try {
      const res = await api(S.mode === "login" ? "/login" : "/register", { method: "POST", body: { name: $("#auth-name").value, pin: $("#auth-pin").value } });
      S.token = res.token; store.set("grube_token", res.token); S.me = res.me; updateChip();
      $("#auth-pin").value = "";
      sfx.unlock();
      await afterLogin();
    } catch (err) { toast(err.message, "error"); } finally { btn.disabled = false; }
  }

  async function afterLogin() {
    if (!S.cat) S.cat = await api("/catalog");
    for (const s of S.cat.symbols) SYM[s.id] = s;
    for (const c of S.cat.charms) CHARM[c.id] = c;
    for (const c of S.cat.sabotage) SABO[c.id] = c;
    const params = new URLSearchParams(location.search);
    const code = params.get("code");
    if (S.me.lobby) {
      try { const r = await api("/lobby/join", { method: "POST", body: { code: S.me.lobby } }); enterLobby(r.code, r.state); return; } catch (e) { /* Tisch weg */ }
    }
    renderMenu();
    if (code) { history.replaceState(null, "", location.pathname); joinByCode(code); }
  }

  /* ---------- Menü ---------- */
  async function renderMenu() {
    stopPoll(); S.code = null; S.st = null; S.built = false; S.auto = false;
    show("menu");
    const root = $("#view-menu");
    try { S.me = (await api("/me")).me; updateChip(); } catch (e) { return; }
    const me = S.me, L = me.level, lo = 40 * (L - 1) ** 2, hi = 40 * L * L;
    const stats = [["Spiele", me.stats.games], ["Siege", me.stats.wins], ["Beste Runde", me.stats.best_round], ["Bester Gewinn", fmt(me.stats.best_win) + " 🪙"], ["Jackpots", me.stats.jackpots], ["Verdient", fmt(me.stats.total_earned)]];
    const lbList = el("ol", { class: "lb" });
    root.replaceChildren(
      el("div", { class: "menu-head" }, el("h1", null, "Willkommen, ", el("span", null, me.name), " ", "🍀"), el("p", { class: "muted" }, "Wähle deinen Spielmodus – allein oder mit Freunden am selben Tisch.")),
      el("div", { class: "tiles" },
        tile("🎰", "Tisch eröffnen", "Neue Lobby mit Beitritts-Code für deine Freunde.", openCreate),
        tile("🔑", "Tisch beitreten", "Gib den 5-stelligen Code ein.", () => openJoin("")),
        tile("📜", "Regeln & Auszahlung", "So funktioniert die Grube.", openHelp),
        tile("🎲", "Allein spielen", "Tisch eröffnen und sofort solo loslegen.", () => createTable("Solo", 10, true))),
      el("div", { class: "cols" },
        el("div", { class: "panel" }, el("h3", null, "Dein Profil"),
          el("div", null, "Level ", el("b", null, L), " · ", el("span", { class: "muted" }, fmt(me.ruhm) + " Ruhm")),
          el("div", { class: "level-bar" }, el("i", { style: { width: Math.min(100, ((me.ruhm - lo) / (hi - lo)) * 100) + "%" } })),
          el("dl", { class: "stats" }, stats.map(([k, v]) => el("div", null, el("dt", null, k), el("dd", null, v))))),
        el("div", { class: "panel" }, el("h3", null, "Bestenliste (Ruhm)"), lbList)),
      el("div", { class: "row", style: { marginTop: "16px" } }, el("button", { class: "btn btn-ghost", type: "button", onclick: () => logoutLocal() }, "Abmelden")));
    try {
      const lb = await api("/leaderboard");
      lbList.replaceChildren(...lb.rows.map((r, i) => el("li", { class: r.name === me.name ? "me" : "" }, el("span", null, ["🥇", "🥈", "🥉"][i] || i + 1), el("span", null, r.name, " ", el("span", { class: "muted small" }, "Lv " + r.level)), el("b", null, fmt(r.ruhm)))));
    } catch (e) { /* egal */ }
  }

  function tile(ico, title, text, onclick) {
    return el("button", { class: "tile", type: "button", onclick }, el("span", { class: "ico" }, ico), el("h3", null, title), el("p", null, text));
  }

  /* ---------- Modals ---------- */
  function openModal(title, build) {
    const root = $("#modal-root");
    const close = () => bg.remove();
    const body = build(close);
    const bg = el("div", { class: "modal-bg", onclick: (e) => { if (e.target === bg) close(); } },
      el("div", { class: "modal", role: "dialog", "aria-label": title },
        el("div", { class: "modal-head" }, el("h2", null, title), el("button", { class: "icon-btn", type: "button", onclick: close, "aria-label": "Schließen" }, "✕")), body));
    root.append(bg);
    const first = bg.querySelector("input,button.btn");
    if (first) first.focus();
    return close;
  }

  function openCreate() {
    let rounds = 10;
    openModal("Tisch eröffnen", (close) => {
      const input = el("input", { type: "text", maxlength: "24", placeholder: "z. B. Freitagsrunde" });
      const seg = el("div", { class: "seg" });
      const drawSeg = () => seg.replaceChildren(...S.cat.rounds.map((r) => el("button", { type: "button", class: r === rounds ? "on" : "", onclick: () => { rounds = r; drawSeg(); } }, r + " Runden")));
      drawSeg();
      const go = async () => { close(); await createTable(input.value, rounds); };
      input.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
      return el("div", null,
        el("p", { class: "muted" }, "Du bekommst einen Code, den deine Freunde zum Beitreten eingeben. Bis zu " + S.cat.max_players + " Spieler."),
        el("label", { class: "field" }, el("span", null, "Name des Tisches (optional)"), input),
        el("div", { class: "field" }, el("span", null, "Länge des Spiels"), seg),
        el("button", { class: "btn btn-pink btn-block", type: "button", onclick: go }, "Tisch eröffnen"));
    });
  }

  async function createTable(name, rounds, autoStart) {
    try {
      const r = await api("/lobby/create", { method: "POST", body: { name, rounds } });
      enterLobby(r.code, r.state);
      if (autoStart) await call("start");
    } catch (e) { toast(e.message, "error"); }
  }

  function openJoin(prefill) {
    openModal("Tisch beitreten", (close) => {
      const input = el("input", { class: "code-input", type: "text", maxlength: "5", placeholder: "•••••", autocomplete: "off", spellcheck: "false" });
      input.value = prefill || "";
      input.addEventListener("input", () => { input.value = input.value.toUpperCase().replace(/[^A-Z0-9]/g, ""); });
      const go = async () => { if (input.value.length < 5) { toast("Der Code hat 5 Zeichen.", "error"); return; } close(); await joinByCode(input.value); };
      input.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
      return el("div", null, el("p", { class: "muted" }, "Gib den Code deiner Freunde ein."), input, el("div", { style: { height: "12px" } }), el("button", { class: "btn btn-pink btn-block", type: "button", onclick: go }, "Beitreten"));
    });
  }

  async function joinByCode(code) {
    try { const r = await api("/lobby/join", { method: "POST", body: { code } }); enterLobby(r.code, r.state); } catch (e) { toast(e.message, "error"); }
  }

  function openHelp() {
    openModal("Regeln & Auszahlung", () => {
      const cat = S.cat, lm = cat.line_mult;
      const rows = cat.symbols.filter((s) => s.value > 0).map((s) => el("tr", null, el("td", null, s.emoji + " " + s.name), el("td", null, s.value * lm["3"]), el("td", null, s.value * lm["4"]), el("td", null, s.value * lm["5"])));
      const sk = cat.skull_pen;
      return el("div", null,
        el("h4", null, "SO GEHT'S"),
        el("ul", null,
          el("li", null, "Jede Runde hast du ", cat.base_spins, " Dreh am Automaten (mehr mit Glücksbringern). Gewinne = Münzen 🪙."),
          el("li", null, "Am Rundenende kommt der ", el("b", null, "Eintreiber 💀"), " und will seine Schulden. Sie wachsen jede Runde!"),
          el("li", null, "Wer nicht zahlen kann, verliert ein ❤️. Kein Leben mehr = raus. Du startest mit ", cat.start_lives, " Leben."),
          el("li", null, "Zwischen den Runden: ", el("b", null, "Schwarzmarkt"), " – kaufe Glücksbringer, die deinen Automaten verbessern, oder sabotiere Mitspieler."),
          el("li", null, "Letzter Überlebender gewinnt. Wer alle Runden schafft, hat die Grube besiegt.")),
        el("h4", null, "GEWINNLINIEN"),
        el("p", null, "5 Linien (3 waagerecht + 2 diagonal). 3, 4 oder 5 gleiche Symbole von links nach rechts zahlen. ✨ Joker ersetzen alles außer 💀."),
        el("table", { class: "pay-t" }, el("thead", null, el("tr", null, el("th", null, "Symbol"), el("th", null, "×3"), el("th", null, "×4"), el("th", null, "×5"))), el("tbody", null, rows)),
        el("p", null, "💀 Pechlinien kosten dich Münzen: ×3 −" + sk["3"] + ", ×4 −" + sk["4"] + ", ×5 −" + sk["5"] + "."),
        el("h4", null, "TRICKS"),
        el("ul", null,
          el("li", null, el("b", null, "Halten 🔒:"), " Tippe vor dem Dreh auf eine Walze, um sie zu behalten."),
          el("li", null, el("b", null, "Risiko 🎲:"), " Nach einem Gewinn kannst du ihn mit ~45 % Chance verdoppeln – oder verlieren."),
          el("li", null, el("b", null, "Jackpot 🎰:"), " 3 oder mehr 7️⃣ irgendwo auf dem Feld knacken den gemeinsamen Topf."),
          el("li", null, el("b", null, "Sabotage:"), " Taschendieb 🧲, Frostbeule 🧊, Pechfluch 🕷️ – oder schütze dich mit dem Zauberspiegel 🪞."),
          el("li", null, "Emojis unten schicken Grüße (und Gemeinheiten) an den Tisch.")));
    });
  }

  /* ---------- Lobby betreten/verlassen ---------- */
  function enterLobby(code, state) {
    stopPoll();
    S.code = code; S.st = null; S.v = 0; S.lastEvent = null; S.built = false; S.hold.clear(); S.busy = false; S.auto = false; S.ovSig = ""; S.emoteSeen = {}; S.dispCoins = 0;
    applyState(state);
    startPoll();
  }

  async function leaveTable() {
    const code = S.code;
    try { await api("/lobby/" + code + "/leave", { method: "POST" }); } catch (e) { /* egal */ }
    renderMenu();
  }

  function startPoll() { stopPoll(); S.poll = setInterval(pollState, 800); }
  function stopPoll() { if (S.poll) clearInterval(S.poll); S.poll = null; }

  async function pollState() {
    if (!S.code || S.polling) return;
    S.polling = true;
    const code = S.code;
    try {
      const full = Date.now() - S.lastFull > 4000;
      const st = await api("/lobby/" + code + "/state?v=" + (full ? 0 : S.v));
      if (code !== S.code) return;
      if (!st.same) { S.lastFull = Date.now(); applyState(st); }
    } catch (e) {
      if (e.status === 404 || e.status === 403) { toast("Der Tisch existiert nicht mehr.", "error"); renderMenu(); }
    } finally { S.polling = false; }
  }

  async function call(action, body) {
    try {
      const st = await api("/lobby/" + S.code + "/" + action, { method: "POST", body: body || {} });
      applyState(st);
      return st;
    } catch (e) { toast(e.message, "error"); return null; }
  }

  /* ---------- Zustand anwenden ---------- */
  function applyState(st) {
    if (!st || st.same) return;
    if (S.st && st.code === S.st.code && st.v < S.st.v) return;
    const prev = S.st;
    S.st = st; S.v = st.v;
    S.deadlineAt = st.deadline_in == null ? null : performance.now() + st.deadline_in * 1000;
    processEvents(st);
    watchEmotes(st);
    render(prev);
  }

  function processEvents(st) {
    const evs = st.events || [];
    const maxId = evs.length ? evs[evs.length - 1].id : 0;
    if (S.lastEvent === null) { S.lastEvent = maxId; return; }
    const me = st.me ? st.me.name : "";
    for (const ev of evs) {
      if (ev.id <= S.lastEvent) continue;
      const mine = !!me && ev.who === me, aimed = !!me && ev.txt.includes(me) && !mine && ["steal", "frost", "curse"].includes(ev.k);
      if (ev.k === "jackpot") { toast(ev.txt, "gold", 5000); fx.confetti(3); if (!mine) sfx.jackpot(); }
      else if (ev.k === "big" && !mine) { toast(ev.txt, "gold"); sfx.win(1); }
      else if (["steal", "frost", "curse", "mirror"].includes(ev.k)) {
        toast(ev.txt, aimed ? "bad" : "cyan", 4500);
        if (aimed) { feedback("flash-red"); sfx.hit(); }
      }
      else if (ev.k === "fail" || ev.k === "out") { toast(ev.txt, "bad", 4500); }
      else if (ev.k === "gamble" && !mine) { toast(ev.txt, ev.amt > 0 ? "gold" : "bad"); }
    }
    S.lastEvent = maxId;
  }

  function feedback(cls) {
    const app = $("#app");
    app.classList.remove("shake", cls);
    void app.offsetWidth;
    app.classList.add(cls);
    if (cls === "flash-red") app.classList.add("shake");
    setTimeout(() => app.classList.remove("shake", "flash-red", "flash-gold"), 700);
  }

  function watchEmotes(st) {
    for (const p of st.players) {
      if (p.emote && S.emoteSeen[p.key] !== p.emote_at) {
        S.emoteSeen[p.key] = p.emote_at;
        const card = document.querySelector('[data-pk="' + CSS.escape(p.key) + '"]');
        const r = card ? card.getBoundingClientRect() : { right: innerWidth / 2, top: 120 };
        const b = el("div", { class: "bubble", style: { position: "fixed", left: Math.max(8, r.right - 50) + "px", top: Math.max(60, r.top - 10) + "px" } }, p.emote);
        document.body.append(b);
        setTimeout(() => b.remove(), 3900);
      } else if (!p.emote) S.emoteSeen[p.key] = p.emote_at;
    }
  }

  /* ---------- Rendering ---------- */
  function render(prev) {
    const st = S.st;
    if (!st) return;
    if (st.phase === "wait") { S.built = false; show("room"); renderRoom(); return; }
    if (!S.built) { buildGame(); }
    show("game");
    if (S.busy) { renderSide(); renderOverlay(); return; }
    renderGame();
  }

  /* ----- Wartezimmer ----- */
  function renderRoom() {
    const st = S.st, root = $("#view-room"), isHost = st.host === st.you;
    const link = location.origin + "/?code=" + st.code;
    const seg = el("div", { class: "seg" }, S.cat.rounds.map((r) => el("button", { type: "button", disabled: !isHost, class: r === st.max_rounds ? "on" : "", onclick: () => call("rounds", { rounds: r }) }, r + " Runden")));
    root.replaceChildren(el("div", { class: "room" },
      el("div", { class: "panel code-box" },
        el("div", { class: "code-label" }, "Beitritts-Code · " + st.name),
        el("div", { class: "code" }, st.code),
        el("div", { class: "row" },
          el("button", { class: "btn btn-cyan btn-small", type: "button", onclick: () => copy(st.code, "Code kopiert!") }, "Code kopieren"),
          el("button", { class: "btn btn-ghost btn-small", type: "button", onclick: () => copy(link, "Einladungslink kopiert!") }, "Einladungslink"))),
      el("div", { class: "plist" }, st.players.map((p) => el("div", { class: ["pcard", p.key === st.you && "me"] }, avatar(p.name),
        el("div", null, el("div", { class: "nm" }, p.name, p.host && el("span", { class: "badge gold" }, "HOST")), el("div", { class: "muted small" }, "Level " + p.level + (p.online ? "" : " · offline")))))),
      el("div", { class: "panel" },
        el("div", { class: "row", style: { justifyContent: "space-between" } },
          el("div", null, el("b", null, "Spiellänge"), el("div", { class: "muted small" }, isHost ? "Du bestimmst als Host." : "Der Host bestimmt.")), seg),
        el("p", { class: "muted small" }, "Allein spielen geht auch – zu zweit und mehr kommen Sabotage, Topf und Duell dazu. Ihr startet je mit " + S.cat.start_coins + " 🪙 und " + S.cat.start_lives + " ❤️."),
        el("div", { class: "row" },
          isHost ? el("button", { class: "btn btn-pink", type: "button", onclick: () => call("start") }, st.players.length > 1 ? "Spiel starten (" + st.players.length + " Spieler)" : "Solo starten")
            : el("div", { class: "muted" }, "Warte, bis der Host startet …"),
          el("button", { class: "btn btn-ghost", type: "button", onclick: openHelp }, "Regeln"),
          el("button", { class: "btn btn-ghost", type: "button", onclick: leaveTable }, "Tisch verlassen")))));
  }

  async function copy(text, msg) {
    try { await navigator.clipboard.writeText(text); toast(msg, "good"); } catch (e) { toast(text); }
  }

  /* ----- Spielaufbau ----- */
  function buildGame() {
    const root = $("#view-game");
    const reels = [];
    const reelsEl = el("div", { class: "reels" });
    for (let c = 0; c < S.cat.reels; c++) {
      const strip = el("div", { class: "strip" });
      const reel = el("div", { class: "reel", onclick: () => toggleHold(c) }, el("div", { class: "window" }, strip));
      reels.push({ reel, strip });
      reelsEl.append(reel);
    }
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "lines"); svg.setAttribute("viewBox", "0 0 5 3"); svg.setAttribute("preserveAspectRatio", "none");
    M = {
      reels, svg,
      banner: el("div", { class: "banner" }),
      readout: el("div", { class: "readout" }),
      hud: el("div", { class: "panel hud" }),
      ctl: el("div", { class: "controls" }),
      side: el("div", { class: "side" }),
      machine: null,
      lever: el("div", { class: "lever", onclick: () => doSpin() }, el("span", { class: "stick" }), el("span", { class: "ball" })),
    };
    M.machine = el("div", { class: "machine" },
      el("div", { class: "marquee" }, Array.from({ length: 12 }, () => el("i"))),
      el("div", { class: "screen" }, reelsEl, svg, M.banner), M.readout);
    root.replaceChildren(
      el("div", { class: "main-col" }, M.hud, el("div", { class: "machine-wrap" }, M.machine, M.lever), M.ctl),
      M.side);
    S.built = true; S.ovSig = ""; S.gridSig = "";
    S.dispCoins = S.st && S.st.me ? S.st.me.coins : 0;
    setGrid(randomGrid());
  }

  const FILL = ["cherry", "lemon", "clover", "bell", "diamond", "seven", "cherry", "lemon", "skull", "clover"];
  const randSym = () => FILL[Math.floor(Math.random() * FILL.length)];
  const randomGrid = () => Array.from({ length: 3 }, () => Array.from({ length: 5 }, randSym));
  const cellEl = (s) => el("div", { class: "cell sym-" + s }, el("span", null, emoji(s)));

  function setGrid(grid) {
    M.reels.forEach((r, c) => r.strip.replaceChildren(...[0, 1, 2].map((row) => cellEl(grid[row][c]))));
  }

  function clearHighlights() {
    M.reels.forEach((r) => r.strip.querySelectorAll(".win,.bad").forEach((n) => n.classList.remove("win", "bad")));
    M.svg.replaceChildren();
    M.readout.replaceChildren();
  }

  const LINE_COLORS = ["#ffd23f", "#2de2e6", "#ff3ea5", "#7cff6b", "#b48bff"];
  function highlight(last) {
    clearHighlights();
    if (!last) return;
    for (const ln of last.lines) {
      const bad = ln.coins < 0;
      ln.cells.forEach(([r, c]) => { const n = M.reels[c].strip.children[r]; if (n) n.classList.add(bad ? "bad" : "win"); });
      const pts = ln.cells.map(([r, c]) => (c + 0.5) + "," + (r + 0.5)).join(" ");
      const pl = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
      pl.setAttribute("points", pts); pl.setAttribute("stroke", bad ? "#ff4d5e" : LINE_COLORS[ln.line % 5]); pl.style.color = bad ? "#ff4d5e" : LINE_COLORS[ln.line % 5];
      M.svg.append(pl);
      M.readout.append(el("span", { class: bad ? "neg" : "pos" }, emoji(ln.sym) + "×" + ln.n + " " + (ln.coins > 0 ? "+" : "−") + Math.abs(ln.coins)));
    }
    if (last.flat > 0) M.readout.append(el("span", { class: "pos" }, "👛 +" + last.flat));
    if (last.jackpot > 0) M.readout.append(el("span", { class: "pos" }, "🎰 Jackpot +" + fmt(last.jackpot)));
    if (!last.lines.length && !last.jackpot) M.readout.append(el("span", null, "Diesmal nichts …"));
  }

  /* ----- Drehen ----- */
  function toggleHold(c) {
    const st = S.st;
    if (!st || st.phase !== "spin" || S.busy || !st.me || !st.me.alive || st.me.spins_left <= 0) return;
    if (S.hold.has(c)) S.hold.delete(c);
    else if (S.hold.size < st.me.holds_left && S.hold.size < 4) S.hold.add(c);
    else { toast("Keine Halten mehr übrig.", "error", 1500); return; }
    sfx.tick();
    syncHold();
    renderControls();
  }
  function syncHold() { M.reels.forEach((r, c) => r.reel.classList.toggle("held", S.hold.has(c))); }

  function startSpinning(hold) {
    clearHighlights();
    M.reels.forEach((r, c) => {
      if (hold.includes(c)) return;
      const cells = Array.from({ length: 12 }, randSym);
      r.strip.replaceChildren(...cells.map(cellEl), ...cells.map(cellEl));
      r.strip.classList.add("rolling");
    });
  }

  function landReels(last, hold) {
    return new Promise((resolve) => {
      let longest = 0;
      M.reels.forEach((r, c) => {
        if (hold.includes(c)) return;
        const d = S.turbo ? 120 + c * 90 : 480 + c * 320;
        longest = Math.max(longest, d);
        setTimeout(() => {
          r.strip.classList.remove("rolling");
          r.strip.replaceChildren(...[0, 1, 2].map((row) => cellEl(last.grid[row][c])));
          r.strip.animate([{ transform: "translateY(-26%)" }, { transform: "translateY(0)" }], { duration: 260, easing: "cubic-bezier(.2,1.5,.4,1)" });
          sfx.stop(c);
        }, d);
      });
      setTimeout(resolve, longest + 280);
    });
  }

  async function doSpin() {
    const st = S.st;
    if (S.busy || !st || st.phase !== "spin" || !st.me || !st.me.alive || st.me.spins_left <= 0) return false;
    S.busy = true;
    const hold = [...S.hold];
    M.lever.classList.add("pull"); setTimeout(() => M.lever.classList.remove("pull"), 260);
    startSpinning(hold); sfx.lever(); renderControls(true);
    let ns;
    try { ns = await api("/lobby/" + S.code + "/spin", { method: "POST", body: { hold } }); } catch (e) {
      M.reels.forEach((r) => r.strip.classList.remove("rolling"));
      setGrid(st.me.grid || randomGrid());
      S.busy = false; toast(e.message, "error"); renderControls(); return false;
    }
    S.hold.clear(); syncHold();
    const last = ns.me.last;
    await landReels(last, hold);
    S.gridSig = JSON.stringify(last.grid);
    S.busy = false;
    applyState(ns);
    render();
    highlight(last);
    celebrate(last, ns);
    return true;
  }

  function celebrate(last, ns) {
    const total = last.net + last.jackpot, cat = S.cat;
    let text = null, bad = false, level = 0;
    if (last.jackpot > 0) { text = "JACKPOT!"; level = 3; sfx.jackpot(); fx.confetti(4); M.machine.classList.add("mega"); feedback("flash-gold"); }
    else if (total >= cat.mega_win) { text = "MEGA WIN"; level = 3; sfx.win(3); fx.confetti(3); M.machine.classList.add("mega"); feedback("flash-gold"); }
    else if (total >= cat.big_win) { text = "BIG WIN"; level = 2; sfx.win(2); fx.confetti(1); }
    else if (total > 0) { text = "+" + fmt(total); level = 1; sfx.win(0); }
    if (last.pen > 0 && total <= 0) { text = "−" + last.pen; bad = true; sfx.skull(); feedback("flash-red"); }
    setTimeout(() => M.machine.classList.remove("mega"), 3200);
    if (total > 0) { const r = M.machine.getBoundingClientRect(); fx.coins(r.left + r.width / 2, r.top + r.height / 2, Math.min(40, 8 + level * 10)); }
    if (text) {
      M.banner.className = "banner" + (bad ? " bad" : "");
      M.banner.replaceChildren(text, level >= 2 ? el("small", null, "+" + fmt(total) + " 🪙") : null);
      void M.banner.offsetWidth; M.banner.classList.add("show");
    }
  }

  function renderGame() {
    const st = S.st, me = st.me;
    if (!me) return;
    if (!S.busy && me.grid) {
      // Raster nur neu aufbauen, wenn es sich wirklich geändert hat (z. B. Neustart/Rejoin)
      const sig = JSON.stringify(me.grid);
      if (sig !== S.gridSig) {
        S.gridSig = sig; setGrid(me.grid); highlight(me.last);
      }
    }
    renderHud(); renderControls(); renderSide(); renderOverlay();
    if (!S.busy && Math.round(S.dispCoins) !== me.coins) tweenCoins(me.coins);
  }

  /* ----- HUD ----- */
  function setDisp(v) {
    S.dispCoins = v;
    document.querySelectorAll(".js-coins").forEach((n) => { n.textContent = fmt(Math.round(v)); });
    const st = S.st, bar = $(".debt .bar");
    if (st && st.me && bar) { bar.classList.toggle("ok", v >= st.me.due); bar.firstChild.style.width = Math.min(100, (v / Math.max(1, st.me.due)) * 100) + "%"; }
  }
  let tweenId = 0;
  function tweenCoins(to) {
    const from = S.dispCoins, t0 = performance.now(), id = ++tweenId;
    const step = (t) => {
      if (id !== tweenId) return;
      const k = Math.min(1, (t - t0) / 600);
      setDisp(from + (to - from) * (1 - Math.pow(1 - k, 3)));
      if (k < 1) requestAnimationFrame(step); else setDisp(to);
    };
    requestAnimationFrame(step);
  }

  function renderHud() {
    const st = S.st, me = st.me;
    const hearts = el("div", { class: "hearts" }, Array.from({ length: Math.max(me.lives, S.cat.start_lives) }, (_, i) => el("span", { class: i < me.lives ? "" : "off" }, "❤️")));
    const label = st.phase === "spin" || st.phase === "collect" ? "Eintreiber fordert" : "Nächste Forderung";
    const due = st.phase === "shop" ? me.due_next : me.due;
    M.hud.replaceChildren(
      el("div", { class: "round-tag" }, "RUNDE " + Math.max(1, st.round) + "/" + st.max_rounds, el("small", null, st.phase === "spin" ? "DREHEN" : st.phase === "shop" ? "SCHWARZMARKT" : st.phase === "collect" ? "EINTREIBER" : "ENDE")),
      el("div", { class: "debt" }, el("div", { class: "label" }, el("span", null, "🪙 ", el("span", { class: "js-coins" }, fmt(S.dispCoins))), el("em", null, "💀 " + label + " " + fmt(due) + " 🪙")),
        el("div", { class: ["bar", S.dispCoins >= me.due && "ok"] }, el("i", { style: { width: Math.min(100, (S.dispCoins / Math.max(1, me.due)) * 100) + "%" } }))),
      el("div", { class: "hud-right" }, hearts, el("div", { class: "timer js-timer" }, "–")));
  }

  /* ----- Steuerung ----- */
  function renderControls(busyNow) {
    const st = S.st, me = st.me, c = M.ctl;
    if (!me) return;
    if (S.busy && !busyNow) return;
    c.replaceChildren();
    if (!me.alive) {
      c.append(el("p", { class: "hint" }, "☠️ Du bist ausgeschieden – schau den anderen zu."), emoteBar());
      return;
    }
    M.lever.classList.toggle("off", !(st.phase === "spin" && me.spins_left > 0 && !S.busy));
    if (st.phase === "spin") {
      const total = me.spins_total || S.cat.base_spins;
      const pips = el("div", { class: "pips" }, Array.from({ length: Math.max(total, me.spins_left) }, (_, i) => el("i", { class: i < me.spins_left ? "" : "off" })));
      const canSpin = me.spins_left > 0 && !S.busy;
      const spinBtn = el("button", { class: ["spin-btn", canSpin && "ready"], type: "button", disabled: !canSpin, onclick: () => doSpin() },
        S.busy ? "DREHT …" : me.spins_left > 0 ? "HEBEL ZIEHEN" : "ALLE DREH GENUTZT");
      const row = el("div", { class: "ctl-row" }, spinBtn,
        el("button", { class: ["btn btn-ghost toggle", S.auto && "on"], type: "button", onclick: toggleAuto, disabled: me.spins_left <= 0 && !S.auto }, S.auto ? "⏸ Auto" : "⏩ Auto"),
        el("button", { class: ["btn btn-ghost toggle", S.turbo && "on"], type: "button", onclick: () => { S.turbo = !S.turbo; store.set("grube_turbo", S.turbo ? "1" : "0"); renderControls(); } }, "⚡ Turbo"));
      c.append(pips, row);
      if (me.gamble_pending > 0 && me.gambles_left > 0 && !S.busy) {
        c.append(el("div", { class: "ctl-row" }, el("button", { class: "btn btn-gold", type: "button", onclick: doGamble },
          "🎲 RISIKO: " + fmt(me.gamble_pending) + " → " + fmt(me.gamble_pending * 2) + " (" + Math.round(me.gamble_odds * 100) + " %)")));
      }
      if (me.spins_left <= 0 && !me.done) c.append(el("div", { class: "ctl-row" }, el("button", { class: "btn btn-lime", type: "button", onclick: () => call("finish") }, "Fertig – Eintreiber rufen ✓")));
      else if (me.spins_left <= 0 && me.done) c.append(el("p", { class: "hint" }, "Warte auf die anderen …"));
      const bits = [];
      if (me.holds_left > 0) bits.push("Tippe auf Walzen zum Halten 🔒 (" + me.holds_left + " übrig)");
      if (me.curse_next || me.cursed) bits.push("🕷️ verflucht: mehr 💀!");
      if (st.me.streak > 0) bits.push("🔥 Serie " + st.me.streak);
      if (bits.length) c.append(el("p", { class: "hint" }, bits.join(" · ")));
    } else if (st.phase === "collect") {
      c.append(el("p", { class: "hint" }, "Der Eintreiber ist da …"));
    }
    c.append(inventory(me), emoteBar());
  }

  async function doGamble() {
    const before = S.st.me.coins;
    const st = await call("gamble");
    if (st) {
      const diff = st.me.coins - before;
      toast(diff > 0 ? "🎲 Verdoppelt! +" + fmt(diff) : "🎲 Verloren … " + fmt(diff), diff > 0 ? "gold" : "bad");
      if (diff > 0) { sfx.win(1); fx.coins(innerWidth / 2, innerHeight / 2, 18); } else sfx.skull();
    }
  }

  async function toggleAuto() {
    S.auto = !S.auto;
    renderControls();
    if (!S.auto) return;
    sfx.unlock();
    while (S.auto && S.st && S.st.phase === "spin" && S.st.me && S.st.me.alive && S.st.me.spins_left > 0) {
      if (!S.busy) { const ok = await doSpin(); if (!ok) break; }
      await sleep(S.turbo ? 250 : 650);
    }
    S.auto = false;
    if (S.st) renderControls();
  }

  function inventory(me) {
    const items = Object.entries(me.charms || {});
    if (!items.length) return el("p", { class: "hint" }, "Noch keine Glücksbringer – im Schwarzmarkt gibt's welche.");
    return el("div", { class: "inv" }, items.map(([id, n]) => {
      const c = CHARM[id];
      return el("div", { class: ["charm", c.rarity], title: c.name + (n > 1 ? " ×" + n : "") + " – " + c.desc }, c.emoji, n > 1 && el("b", null, "×" + n));
    }));
  }

  function emoteBar() {
    return el("div", { class: "emotes" }, S.cat.emotes.map((e) => el("button", { type: "button", onclick: () => call("emote", { e }) }, e)));
  }

  /* ----- Seitenleiste ----- */
  function renderSide() {
    const st = S.st;
    const miniGrid = (p) => {
      const win = new Set(), bad = new Set();
      if (p.last) for (const ln of p.last.lines) ln.cells.forEach(([r, c]) => (ln.coins < 0 ? bad : win).add(r * 5 + c));
      const g = p.grid || randomGrid();
      return el("div", { class: "mini" }, [0, 1, 2].flatMap((r) => [0, 1, 2, 3, 4].map((c) => el("i", { class: win.has(r * 5 + c) ? "win" : bad.has(r * 5 + c) ? "bad" : "" }, emoji(g[r][c])))));
    };
    const status = (p) => {
      if (!p.alive) return p.quit ? "🚪 aufgegeben" : "☠️ raus (R" + p.out_round + ")";
      if (!p.online) return "📴 offline";
      if (st.phase === "spin") return p.spins_left > 0 ? "🎰 " + p.spins_left + " Dreh" : p.done ? "✅ fertig" : "⌛ gleich fertig";
      if (st.phase === "shop") return p.ready ? "✅ bereit" : "🛒 shoppt";
      return "";
    };
    const cards = [...st.players].sort((a, b) => (b.key === st.you) - (a.key === st.you)).map((p) =>
      el("div", { class: ["opp", p.key === st.you && "me", !p.alive && "out"], "data-pk": p.key },
        el("div", { class: "opp-head" }, avatar(p.name, 28), el("span", { class: "nm" }, p.name, p.key === st.you ? " (du)" : ""), el("span", { class: "co" }, fmt(p.coins) + " 🪙")),
        miniGrid(p),
        el("div", { class: "opp-foot" }, el("span", null, "❤️".repeat(Math.max(0, p.lives)) || "—", p.mirrors > 0 ? " 🪞" : "", p.cursed ? " 🕷️" : ""), el("span", null, status(p))),
        el("div", { class: "opp-foot" }, el("span", { class: "ch" }, Object.entries(p.charms).slice(0, 9).map(([id, n]) => CHARM[id].emoji + (n > 1 ? n : "")).join(" ")))));
    const feed = el("div", { class: "feed" }, [...st.events].reverse().slice(0, 18).map((e) => el("div", { class: e.k }, e.txt)));
    M.side.replaceChildren(
      el("div", { class: "pot" }, el("small", null, "GEMEINSAMER TOPF"), el("b", null, "🎰 " + fmt(st.pot)), el("span", null, "Knackt ihn mit 3× 7️⃣ irgendwo auf dem Feld")),
      ...cards,
      el("div", { class: "panel" }, el("h3", null, "Live-Ticker"), feed),
      el("div", { class: "row" }, el("button", { class: "btn btn-ghost btn-small", type: "button", onclick: () => { if (confirm("Wirklich aufgeben und den Tisch verlassen?")) leaveTable(); } }, "Aufgeben & verlassen")));
  }

  /* ----- Overlays ----- */
  function setOverlay(node) {
    const ov = $("#overlay");
    if (!node) { ov.hidden = true; ov.replaceChildren(); S.ovSig = ""; return; }
    const keep = ov.scrollTop;
    ov.replaceChildren(node); ov.hidden = false; ov.scrollTop = keep;
  }

  function renderOverlay() {
    const st = S.st, me = st.me;
    if (!me) return;
    if (st.phase === "shop") { setOverlay(shopView(st, me)); S.ovSig = "shop"; }
    else if (st.phase === "collect" && st.result) {
      const sig = "collect" + st.result.round;
      if (S.ovSig !== sig) { S.ovSig = sig; setOverlay(collectView(st)); }
    } else if (st.phase === "over" && st.final) {
      const sig = "over" + st.v;
      if (S.ovSig !== sig) { const first = !S.ovSig.startsWith("over"); S.ovSig = sig; setOverlay(overView(st, first)); }
    } else { if (S.ovSig) setOverlay(null); S.ovSig = ""; }
  }

  function shopView(st, me) {
    const opp = st.players.filter((p) => p.alive && p.key !== st.you);
    if (!me.alive) {
      return el("div", { class: "ov" }, el("h2", { class: "ov-title" }, "☠️ AUSGESCHIEDEN"), el("p", { class: "ov-sub" }, "Du schaust jetzt zu. Die anderen shoppen …"),
        el("div", { class: "row" }, el("div", { class: "chip js-timer" }, "–")), el("div", { class: "row", style: { marginTop: "16px" } }, el("button", { class: "btn btn-ghost", type: "button", onclick: leaveTable }, "Tisch verlassen")));
    }
    const offers = me.offers.map((o) => {
      const c = CHARM[o.id], owned = me.charms[o.id] || 0, can = me.coins >= o.price && !(st.starter && me.ready);
      return el("div", { class: ["offer", c.rarity] },
        el("div", { class: "rar" }, RAR[c.rarity]), el("div", { class: "big-ico" }, c.emoji), el("h4", null, c.name), el("p", null, c.desc),
        owned > 0 && el("div", { class: "muted small" }, "Du hast schon " + owned + "×"),
        el("button", { class: ["btn", st.starter ? "btn-lime" : "btn-gold"], type: "button", disabled: !can, onclick: async () => { const r = await call("buy", { id: o.id }); if (r) { sfx.buy(); fx.coins(innerWidth / 2, innerHeight / 2, 10, c.emoji); } } },
          st.starter ? "Wählen (gratis)" : "Kaufen · " + fmt(o.price) + " 🪙"));
    });
    const sab = (!st.starter && opp.length > 0) ? S.cat.sabotage.map((s) =>
      el("div", { class: "sab-card" }, el("h4", null, s.emoji + " " + s.name + " · " + s.price + " 🪙"), el("p", null, s.desc),
        s.target ? el("div", { class: "row", style: { justifyContent: "flex-start" } }, opp.map((p) => el("button", { class: "btn btn-pink btn-small", type: "button", disabled: me.coins < s.price || me.sabos_left <= 0, onclick: async () => { const r = await call("sabotage", { id: s.id, target: p.key }); if (r) sfx.buy(); } }, "→ " + p.name)))
          : el("button", { class: "btn btn-cyan btn-small", type: "button", disabled: me.coins < s.price || me.sabos_left <= 0, onclick: async () => { const r = await call("sabotage", { id: s.id }); if (r) sfx.buy(); } }, "Aufstellen" + (me.mirrors ? " (hast " + me.mirrors + ")" : "")))) : [];
    const rdy = st.players.filter((p) => p.alive).map((p) => el("span", { class: ["rdy", p.ready && "on"] }, (p.ready ? "✅ " : "🛒 ") + p.name));
    return el("div", { class: "ov" },
      el("h2", { class: "ov-title" }, st.starter ? "WÄHLE DEIN START-GLÜCKSBRINGER" : "SCHWARZMARKT"),
      el("p", { class: "ov-sub" }, st.starter ? "Einer geht aufs Haus – er prägt deinen ganzen Lauf." : "Runde " + st.round + " überstanden. Der Eintreiber will nächstes Mal " + fmt(me.due_next) + " 🪙."),
      el("div", { class: "shop-head" }, el("div", { class: "chip" }, "🪙 ", el("b", null, fmt(me.coins))), el("div", { class: "chip" }, "❤️ " + me.lives), el("div", { class: "chip" }, "⏱ ", el("span", { class: "js-timer" }, "–"))),
      el("div", { class: "offers" }, offers.length ? offers : el("p", { class: "muted" }, "Keine Angebote mehr.")),
      !st.starter && el("div", { class: "row", style: { marginTop: "12px" } }, el("button", { class: "btn btn-ghost", type: "button", disabled: me.coins < me.reroll_cost, onclick: () => call("reroll") }, "🔄 Neu mischen · " + me.reroll_cost + " 🪙")),
      sab.length > 0 && el("div", { class: "sec-title red" }, "SABOTAGE & SCHUTZ (noch " + me.sabos_left + " diese Runde)"),
      sab.length > 0 && el("div", { class: "sab" }, sab),
      el("div", { class: "sec-title" }, "DEINE GLÜCKSBRINGER"), inventory(me),
      el("div", { class: "readyrow" },
        el("button", { class: "btn btn-lime", type: "button", disabled: me.ready || (st.starter && !me.ready), onclick: () => call("ready") }, me.ready ? "Warte auf die anderen …" : "Bereit – weiter ➜"), rdy),
      el("div", { class: "row", style: { marginTop: "10px" } }, el("button", { class: "btn btn-ghost btn-small", type: "button", onclick: openHelp }, "Regeln")));
  }

  function collectView(st) {
    const r = st.result;
    sfx.knock();
    setTimeout(() => { const anyFail = r.rows.some((x) => !x.paid); if (anyFail) sfx.fail(); else sfx.pay(); }, 700);
    return el("div", { class: "ov collector" },
      el("div", { class: "skullhead" }, "💀"), el("h2", { class: "ov-title" }, "DER EINTREIBER KOMMT"),
      el("div", { class: "demand" }, "Runde " + r.round + ": " + fmt(r.due) + " 🪙"),
      el("div", { class: "pay-list" }, r.rows.map((x, i) => el("div", { class: ["pay", x.paid ? "ok" : "no"], style: { animationDelay: (0.5 + i * 0.5) + "s" } },
        avatar(x.name, 34), el("div", null, el("b", null, x.name), el("div", { class: "muted small" }, "zahlt " + fmt(x.due) + " 🪙" + (x.bonus ? " · +" + x.bonus + " Zinsen 🐷" : ""))),
        el("div", { class: "st stamp" }, x.paid ? "✅ bezahlt" : x.out ? "☠️ ausgeschieden" : "💔 −1 Leben")))),
      el("p", { class: "ov-sub", style: { marginTop: "16px" } }, "Weiter in ", el("span", { class: "js-timer" }, "–")));
  }

  function overView(st, first) {
    const f = st.final, champ = f.rows.find((r) => r.won);
    if (first) { if (champ) { sfx.fanfare(); fx.confetti(5); } else sfx.fail(); }
    const isHost = st.host === st.you;
    return el("div", { class: "ov" },
      el("h2", { class: "ov-title" }, champ ? "🏆 " + champ.name.toUpperCase() + " GEWINNT!" : "DIE GRUBE HAT ALLE VERSCHLUCKT"),
      el("p", { class: "ov-sub" }, f.rounds + " Runden gespielt · Ruhm gibt's für Runden, Gewinn und Platz."),
      el("div", { class: "podium" }, f.rows.map((r, i) => el("div", { class: ["place", i === 0 && r.won && "first"], style: { animationDelay: (i * 0.25) + "s" } },
        el("div", { class: "pl" }, ["🥇", "🥈", "🥉"][i] || "#" + r.place),
        el("div", null, el("b", null, r.name, r.key === st.you ? " (du)" : ""), el("small", null, r.rounds + " Runden · " + fmt(r.earned) + " 🪙 verdient · bester Treffer " + fmt(r.best_win) + (r.jackpots ? " · 🎰×" + r.jackpots : ""))),
        el("div", { class: "ru" }, "+" + r.ruhm + " Ruhm")))),
      el("div", { class: "row" },
        isHost ? el("button", { class: "btn btn-pink", type: "button", onclick: () => call("restart") }, "🔁 Nochmal spielen") : el("div", { class: "muted" }, "Der Host kann eine neue Runde starten."),
        el("button", { class: "btn btn-ghost", type: "button", onclick: leaveTable }, "Zum Hauptmenü")));
  }

  /* ---------- Timer ---------- */
  setInterval(() => {
    const nodes = document.querySelectorAll(".js-timer");
    if (!nodes.length) return;
    const st = S.st;
    let txt = "–", low = false;
    if (S.deadlineAt != null) {
      const left = Math.max(0, Math.ceil((S.deadlineAt - performance.now()) / 1000));
      txt = left + "s"; low = left <= 5 && st && st.phase !== "collect";
      if (low && st.phase === "shop" && left > 0 && left <= 3) sfx.tick();
    }
    nodes.forEach((n) => { n.textContent = txt; n.classList.toggle("low", low); });
  }, 250);

  /* ---------- Start ---------- */
  function bind() {
    $("#tab-login").addEventListener("click", () => setAuthMode("login"));
    $("#tab-register").addEventListener("click", () => setAuthMode("register"));
    $("#auth-form").addEventListener("submit", submitAuth);
    $("#brand").addEventListener("click", () => { if (S.me && !S.code) renderMenu(); });
    $("#btn-help").addEventListener("click", () => { if (S.cat) openHelp(); });
    const snd = $("#btn-sound");
    const paint = () => { snd.textContent = S.muted ? "🔇" : "🔊"; };
    snd.addEventListener("click", () => { S.muted = !S.muted; store.set("grube_muted", S.muted ? "1" : "0"); paint(); sfx.unlock(); });
    paint();
    document.addEventListener("keydown", (e) => {
      if (e.target.matches("input,textarea")) return;
      if (e.code === "Space" && S.st && S.st.phase === "spin" && S.built) { e.preventDefault(); doSpin(); }
    });
    document.addEventListener("pointerdown", () => sfx.unlock(), { once: true });
  }

  async function init() {
    bind();
    setAuthMode("login");
    S.cat = await api("/catalog").catch(() => null);
    if (S.token) {
      try { S.me = (await api("/me")).me; updateChip(); await afterLogin(); return; } catch (e) { /* abgelaufen */ }
    }
    show("auth");
  }
  init();
})();
