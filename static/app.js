// KOTSU dashboard: Listen → Dictionary → Ask → Coach → Drill → Passport → Robot.
(function () {
  const $ = (id) => document.getElementById(id);
  const S = { state: null, dict: [], byKey: {}, view: "capture", intent: "correction", lastStrokes: [], coach: null,
              coachWord: null, question: null, askLog: [], drill: null, live: {} };
  const hostId = (() => { let v = null; try { v = localStorage.getItem("kotsu-host"); } catch (e) {} if (!v) { v = Math.random().toString(36).slice(2, 6); try { localStorage.setItem("kotsu-host", v); } catch (e) {} } return v; })();
  const DRAG_SRC = "drag-" + hostId;

  // ---------------- socket ----------------
  // Projector view: open the dashboard as /?demo to hide the extra tabs and enlarge everything a little.
  if (/[?&]demo\b/.test(location.search)) document.body.classList.add("demo");
  const EMBED = /[?&]embed=graph\b/.test(location.search);
  if (EMBED) document.body.classList.add("embed");
  const sock = K.connect({ src: DRAG_SRC, role: "ui" }, onEvent, (ok) => { if (ok) refresh(); });

  function onEvent(ev) {
    if (ev.type === "stroke") onStroke(ev);
    else if (ev.type === "lesson") { addLesson(ev.lesson, ev.word); refresh(); if (S.view === "graph") loadGraph().then(() => traceKG(ev.lesson.word_key)); }
    else if (ev.type === "utterance") showUtterance(ev);
    else if (ev.type === "word" || ev.type === "answer") refresh();
    else if (ev.type === "session") { if (S.state) { S.state.session = ev.session; renderSources(); syncSession(); } }
    else if (ev.type === "live") onLive(ev);
    else if (ev.type === "reset") { S.lastStrokes = []; $("lessons").innerHTML = ""; refresh(); }
    else if (ev.type === "source") refresh();
  }

  // ---------------- state ----------------
  async function refresh() {
    try { S.state = await K.api("/api/state"); } catch (e) { return; }
    S.dict = S.state.dictionary;
    if (!S.lastStrokes.length) S.lastStrokes = (S.state.recent || []).slice(-6);
    S.byKey = {}; S.dict.forEach((w) => { S.byKey[w.key] = w; });
    fillSelect($("sel-master"), S.state.masters.map((m) => [m.id, m.name]), S.state.session.master_id);
    fillSelect($("sel-learner"), S.state.learners.map((l) => [l.id, l.name]), S.state.session.learner_id, "— add a learner in Setup —");
    const opts = S.dict.map((w) => [w.key, `${w.word}  ·  ${w.master}  ·  ${w.stype}`]);
    ["ask-word", "coach-word", "drill-word", "robot-word"].forEach((id) => fillSelect($(id), opts, $(id).value || (opts[0] && opts[0][0])));
    renderSources();
    renderModeBanner();
    renderWordButtons();
    if (!$("lessons").childElementCount) S.state.lessons.slice().reverse().forEach((l) => addLesson(l, S.byKey[l.word_key]));
    if (S.view === "dictionary") renderDictionary();
    if (S.view === "ask") renderAskBands();
    if (S.view === "setup") renderSetup();
    if (S.view === "passport") renderPassport();
    if (S.view === "results") renderResults();
    if (S.view === "capture" && !$("lessons").childElementCount) drawCapture();
  }

  function fillSelect(sel, pairs, value, emptyLabel) {
    const cur = value || sel.value;
    sel.innerHTML = "";
    if (!pairs.length && emptyLabel) { const o = document.createElement("option"); o.value = ""; o.textContent = emptyLabel; sel.appendChild(o); }
    pairs.forEach(([v, t]) => { const o = document.createElement("option"); o.value = v; o.textContent = t; sel.appendChild(o); });
    if (cur && pairs.some((p) => p[0] === cur)) sel.value = cur;
  }

  function renderSources() {
    const box = $("sources"); box.innerHTML = "";
    const src = (S.state && S.state.session.sources) || {};
    Object.keys(src).sort().forEach((k) => {
      const age = src[k]; const s = document.createElement("span");
      s.className = "src" + (age < 6 ? " on" : ""); s.textContent = k; s.title = age < 6 ? "connected" : `last seen ${age}s ago`;
      box.appendChild(s);
    });
  }
  setInterval(async () => { try { const st = await K.api("/api/state"); if (S.state) { S.state.session = st.session; renderSources(); } } catch (e) {} }, 4000);

  $("sel-master").addEventListener("change", async (e) => { await K.api("/api/session", { master_id: e.target.value }); refresh(); });
  $("sel-learner").addEventListener("change", async (e) => { if (e.target.value) await K.api("/api/session", { learner_id: e.target.value }); refresh(); });

  // ---------------- tabs ----------------
  document.querySelectorAll("nav.tabs button").forEach((b) => b.addEventListener("click", () => show(b.dataset.view)));
  async function show(view) {
    S.view = view;
    document.querySelectorAll("nav.tabs button").forEach((b) => b.setAttribute("aria-selected", b.dataset.view === view ? "true" : "false"));
    document.querySelectorAll(".view").forEach((v) => { v.hidden = v.id !== "view-" + view; });
    renderModeBanner();
    if (["dictionary", "ask", "drill", "robot"].includes(view) && S.state && S.state.session.mode === "coach") { /* keep coaching running */ }
    if (view === "dictionary") renderDictionary();
    if (view === "ask") renderAskBands();
    if (view === "passport") renderPassport();
    if (view === "setup") renderSetup();
    if (view === "robot") drawRobotIdle();
    if (view === "results") renderResults();
    if (view === "graph") loadGraph();
    if (view === "coach") renderCoachHeader();
    redrawPlots();
  }

  // One server, several screens: a second laptop may be coaching while this one shows Listen.
  function renderModeBanner() {
    const mode = S.state && S.state.session.mode;
    const b = $("mode-banner");
    b.hidden = !mode || mode === "capture";
    if (!b.hidden) $("mode-banner-text").textContent = mode === "coach" ? "KOTSU is coaching right now: every stroke is scored for the learner, not captured from the master." : "KOTSU is only naming strokes right now.";
  }
  $("btn-to-listen").addEventListener("click", async () => { await K.api("/api/session", { mode: "capture" }); await refresh(); renderModeBanner(); });
  function syncSession() {
    renderModeBanner();
    const c = S.state && S.state.session.coach;
    if (S.state.session.mode === "coach" && c && S.byKey[c.key]) {
      if (!S.coachWord || S.coachWord.key !== c.key) {
        S.coachWord = S.byKey[c.key]; $("coach-word").value = c.key;
        S.condition = c.condition || "kotsu"; $("coach-cond").value = S.condition;
        S.coach = { level: c.level, attempts: c.attempts, test: !!(c.test && !c.test.done) };
        renderCoachHeader(); renderLevels(c.level); drawCoachPlot(null);
      }
    }
  }

  // ---------------- LISTEN ----------------
  K.dragPad($("cap-pad"), DRAG_SRC, sock);
  K.dragPad($("coach-pad"), DRAG_SRC, sock);

  const live = document.createElement("canvas"); live.className = "plot small"; live.id = "cap-live";
  $("cap-plot").parentNode.insertBefore(live, $("cap-plot"));
  function onLive(ev) {
    const arr = (S.live[ev.src] = S.live[ev.src] || []);
    arr.push(ev.v || 0); if (arr.length > 160) arr.shift();
    if (S.view === "capture" || S.view === "coach") {
      const series = Object.keys(S.live).slice(0, 3).map((k, i) => ({ y: S.live[k], fs: 25, color: ["#D9480F", "#24427A", "#1E7F4F"][i], label: "live " + k, width: 2 }));
      K.plot(live, series, {});
    }
  }

  function onStroke(ev) {
    const st = ev.stroke;
    if (S.view === "graph" && ev.coach && ev.coach.test && ev.coach.test.done && ev.coach.test.passed) loadGraph().then(() => traceKG(ev.coach.key, st.who));
    S.lastStrokes.push(st); if (S.lastStrokes.length > 6) S.lastStrokes.shift();
    if (S.view === "capture") drawCapture();
    const n = ev.named;
    $("cap-named").innerHTML = n ? `Last stroke looks like <b style="font-size:28px">${K.esc(n.word)}</b> <span class="muted">(${Math.round(n.similarity * 100)}% match with this master)</span>` : `<span class="muted">Stroke recorded from ${K.esc(st.src)}.</span>`;
    if (ev.coach) onCoachResult(ev.coach, st);
  }

  function drawCapture(lesson) {
    const series = [];
    if (lesson && lesson.tryP) series.push({ y: lesson.tryP.profile, fs: lesson.tryP.fs, color: "#D9480F", dash: [6, 5], label: "apprentice’s try" });
    if (lesson && lesson.demoP) series.push({ y: lesson.demoP.profile, fs: lesson.demoP.fs, color: "#24427A", width: 3.5, label: "master’s demo " + (lesson.word || "") });
    if (!lesson) {
      const last = S.lastStrokes.slice(-3);
      last.forEach((s, i) => series.push({ y: s.profile, fs: s.fs, color: i === last.length - 1 ? "#15191E" : "#B8BEC6", width: i === last.length - 1 ? 3 : 2, label: i === last.length - 1 ? "latest stroke" : "" }));
    }
    const unit = (S.lastStrokes.length && S.lastStrokes[S.lastStrokes.length - 1].stype) || "";
    K.plot($("cap-plot"), series, { unit: unit === "force" ? "pressure" : unit ? "speed" : "" });
  }

  function addLesson(lesson, word) {
    if (!lesson) return;
    const box = $("lessons");
    const div = document.createElement("div"); div.className = "lesson";
    const w = word ? word.word : (lesson.word_key || "").split("|")[1];
    div.innerHTML = `<div><div class="w">${K.esc(w)}</div><div class="muted" style="font-size:12px">${K.esc(lesson.text || "")}</div></div><canvas></canvas>`;
    box.prepend(div);
    while (box.childElementCount > 30) box.lastChild.remove();
    const strokes = (S.state && S.state.recent) || [];
    const find = (id) => strokes.find((s) => s.id === id) || S.lastStrokes.find((s) => s.id === id);
    const tryP = lesson.try_id ? find(lesson.try_id) : null;
    const demoP = lesson.demo_id ? find(lesson.demo_id) : null;
    requestAnimationFrame(() => {
      const series = [];
      if (tryP) series.push({ y: tryP.profile, fs: tryP.fs, color: "#D9480F", dash: [5, 4] });
      if (demoP) series.push({ y: demoP.profile, fs: demoP.fs, color: "#24427A", width: 3 });
      K.plot(div.querySelector("canvas"), series, {});
    });
    if (S.view === "capture" && (tryP || demoP)) drawCapture({ tryP, demoP, word: w });
  }

  // Japanese speech recognition (Chrome)
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  let rec = null, listening = false;
  $("btn-listen").addEventListener("click", () => {
    if (!SR) { $("listen-status").textContent = "This browser has no speech recognition. Use Chrome, or tap the words below."; return; }
    if (listening) { listening = false; rec && rec.stop(); $("btn-listen").textContent = "Start listening (Japanese)"; $("btn-listen").classList.remove("on"); $("listen-status").textContent = "Microphone off"; return; }
    rec = new SR(); rec.lang = "ja-JP"; rec.continuous = true; rec.interimResults = true;
    rec.onresult = (e) => {
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        if (r.isFinal) { sendUtterance(r[0].transcript, "speech"); }
        else interim += r[0].transcript;
      }
      if (interim) $("speech").innerHTML = `<span class="interim">${K.esc(interim)}</span>`;
    };
    rec.onerror = (e) => { $("listen-status").textContent = "Speech error: " + e.error + (e.error === "network" ? " (needs internet)" : ""); };
    rec.onend = () => { if (listening) { try { rec.start(); } catch (e) {} } };
    if (S.state && S.state.session.mode !== "capture") K.api("/api/session", { mode: "capture" }).then(refresh).catch(() => {});
    try { rec.start(); listening = true; $("btn-listen").textContent = "Stop listening"; $("btn-listen").classList.add("on"); $("listen-status").textContent = "Listening in Japanese…"; }
    catch (e) { $("listen-status").textContent = "Could not start: " + e.message; }
  });

  async function sendUtterance(text, source) {
    $("speech").textContent = "「" + text + "」";
    try { const r = await K.api("/api/utterance", { text, source }); showUtterance(r); }
    catch (e) { $("speech-action").textContent = e.message; }
  }
  function showUtterance(r) {
    if (!r || !r.text) return;
    $("speech").textContent = "「" + r.text + "」";
    const what = { correction: "Correction", demonstration: "Demo", approval: "Approval", bad_example: "Wrong-way example", none: "No craft word" }[r.intent] || r.intent;
    $("speech-action").textContent = `${what}${r.word ? " · word " + r.word + (r.new ? " (new word)" : "") : ""} → ${r.action || ""}`;
  }

  document.querySelectorAll("#intent-pick button").forEach((b) => b.addEventListener("click", () => {
    S.intent = b.dataset.intent;
    document.querySelectorAll("#intent-pick button").forEach((x) => x.setAttribute("aria-pressed", x === b ? "true" : "false"));
  }));
  const extraWords = [];
  function renderWordButtons() {
    const box = $("word-buttons");
    const lex = (S.state && S.state.lexicon) || [];
    const words = lex.map((l) => l).concat(extraWords.map((w) => ({ word: w, gloss_en: "new word" })));
    box.innerHTML = "";
    words.forEach((l) => {
      const b = document.createElement("button"); b.type = "button"; b.className = "wbtn";
      b.innerHTML = `<span class="w">${K.esc(l.word)}</span><span class="g">${K.esc(l.gloss_en)}</span>`;
      b.addEventListener("click", () => sendUtterance(phrase(l.word), "button"));
      box.appendChild(b);
    });
  }
  function phrase(w) {
    if (S.intent === "correction") return "ちがう、もっと" + w + "と";
    if (S.intent === "bad") return "ダメな例、" + w;
    if (S.intent === "ok") return "そうそう";
    return w + "と";
  }
  $("btn-new-word").addEventListener("click", () => {
    const v = $("new-word").value.trim(); if (!v) return;
    extraWords.push(v); $("new-word").value = ""; renderWordButtons(); sendUtterance(phrase(v), "button");
  });

  // ---------------- DICTIONARY ----------------
  function renderDictionary() {
    const box = $("dict"); box.innerHTML = "";
    renderCompare();
    const mid = S.state && S.state.session.master_id;
    const master = S.state && S.state.masters.find((m) => m.id === mid);
    $("dict-note").textContent = `Each word as ${master ? master.name : "this master"}’s hands mean it. The shaded band is the master’s own range.`;
    const mine = S.dict.filter((w) => w.master_id === mid);
    if (!mine.length) { box.innerHTML = `<div class="card"><h2>No words yet</h2><p class="muted" style="margin:0">Go to Listen and let the master correct a stroke: 「ちがう、もっとグッと」. Or choose another master at the top, or load sample data in Setup.</p></div>`; return; }
    mine.forEach((w) => {
      const c = document.createElement("div"); c.className = "card";
      c.innerHTML = `
        <div class="row" style="align-items:flex-end"><div class="wordmid">${K.esc(w.word)}</div><div class="muted">${K.esc(w.romaji || "")} · ${K.esc(w.master)} · ${K.esc(w.stype)}</div></div>
        <div class="gloss">${K.esc(w.gloss_en)}</div>
        <div><b>For this master:</b> ${K.esc(w.meaning.en)}</div>
        <canvas class="plot small"></canvas>
        <div class="chips">${w.bands.map((b) => `<span class="chip">${K.esc(b.feature)} <b>${K.fmtFeature(b.feature, b.lo)}–${K.fmtFeature(b.feature, b.hi)}</b></span>`).join("")}</div>
        <div class="muted" style="font-size:13px">${w.n_pos} demo${w.n_pos === 1 ? "" : "s"} · ${w.n_neg} corrected ${w.n_neg === 1 ? "try" : "tries"} · ${w.n_lessons} lesson${w.n_lessons === 1 ? "" : "s"} · ${w.n_questions} questions answered${w.is_new_word ? " · new word" : ""}</div>
        <div class="row">
          <button class="btn" data-a="hear" type="button">▶ Hear stroke</button>
          <button class="btn" data-a="voice" type="button">▶ Voice</button>
          <button class="btn" data-a="rec" type="button">● Record voice</button>
          <button class="btn" data-a="ask" type="button">Ask</button>
          <button class="btn primary" data-a="coach" type="button">Coach</button>
        </div>`;
      box.appendChild(c);
      requestAnimationFrame(() => K.plot(c.querySelector("canvas"), [{ y: w.prototype, fs: w.fs, color: "#24427A", width: 3 }], { envelope: w.envelope }));
      c.querySelector('[data-a="hear"]').addEventListener("click", () => K.sonify(w.prototype, w.fs));
      c.querySelector('[data-a="voice"]').addEventListener("click", () => { const k = K.playClip(w); if (k === "synth") K.toast("No recording yet: playing a synthesized voice. Record the master’s voice."); });
      c.querySelector('[data-a="rec"]').addEventListener("click", (e) => recordClip(w, e.target));
      c.querySelector('[data-a="ask"]').addEventListener("click", () => { $("ask-word").value = w.key; show("ask"); });
      c.querySelector('[data-a="coach"]').addEventListener("click", () => { $("coach-word").value = w.key; show("coach"); });
    });
  }

  async function recordClip(w, btn) {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mr = new MediaRecorder(stream); const chunks = [];
      mr.ondataavailable = (e) => chunks.push(e.data);
      mr.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(chunks, { type: mr.mimeType || "audio/webm" });
        const fd = new FormData(); fd.append("key", w.key); fd.append("file", blob, "clip.webm");
        try { await K.api("/api/clip", fd); K.toast("Saved the master’s voice for " + w.word); refresh(); } catch (e) { K.toast(e.message); }
        btn.textContent = "● Record voice";
      };
      mr.start(); btn.textContent = "Recording… say 「" + w.word + "と」";
      setTimeout(() => mr.state !== "inactive" && mr.stop(), 2500);
    } catch (e) { K.toast("Microphone unavailable: " + e.message); }
  }

  // ---------------- SAME WORD, DIFFERENT MASTERS ----------------
  const CMP = [["#24427A", "rgba(36,66,122,0.15)"], ["#D9480F", "rgba(217,72,15,0.15)"], ["#1E7F4F", "rgba(30,127,79,0.15)"], ["#8E2D7A", "rgba(142,45,122,0.15)"]];
  let cmpData = null;
  function comparable() {
    const g = {};
    S.dict.forEach((w) => { const k = w.word + "|" + w.stype; (g[k] = g[k] || new Set()).add(w.master_id); });
    return Object.keys(g).filter((k) => g[k].size >= 2).map((k) => { const [word, stype] = k.split("|"); return { word, stype, n: g[k].size }; });
  }
  async function renderCompare() {
    const list = comparable();
    $("cmp-card").hidden = !list.length;
    if (!list.length) return;
    const sel = $("cmp-word");
    fillSelect(sel, list.map((c) => [c.word + "|" + c.stype, `${c.word} · ${c.stype} · ${c.n} masters`]), sel.value || (list[0].word + "|" + list[0].stype));
    const [word, stype] = sel.value.split("|");
    try { cmpData = await K.api("/api/compare?word=" + encodeURIComponent(word) + "&stype=" + encodeURIComponent(stype)); } catch (e) { return; }
    const ms = cmpData.masters.slice(0, CMP.length);
    K.plot($("cmp-plot"), ms.map((m, i) => ({ y: m.prototype, fs: m.fs, color: CMP[i][0], width: 3.5, label: m.master })),
      { envelopes: ms.map((m, i) => m.envelope ? Object.assign({ fill: CMP[i][1] }, m.envelope) : null), unit: stype === "force" ? "pressure" : "speed" });
    $("cmp-meanings").innerHTML = ms.map((m, i) => `<div class="cmpm" style="border-color:${CMP[i][0]}"><b>${K.esc(m.master)}</b>
      <div>「${K.esc(m.word)}」 means: ${K.esc(m.meaning.en)}</div>
      <div class="chips">${m.bands.map((b) => `<span class="chip">${K.esc(b.feature)} <b>${K.fmtFeature(b.feature, b.lo)}–${K.fmtFeature(b.feature, b.hi)}</b></span>`).join("")}</div>
      <div class="muted" style="font-size:13px">${m.n_pos} demo${m.n_pos === 1 ? "" : "s"} · ${m.n_neg} corrected ${m.n_neg === 1 ? "try" : "tries"}</div></div>`).join("");
    const who = cmpData.who;
    $("cmp-who").textContent = who && who.n ? `Whose ${word} is it? With each demo held out, KOTSU named the right master ${who.correct} of ${who.n} times (chance ${Math.round(who.chance * 100)}%).` : "";
  }
  $("cmp-word").addEventListener("change", renderCompare);
  $("cmp-hear").addEventListener("click", () => {
    if (!cmpData) return; let t = 0;
    cmpData.masters.slice(0, CMP.length).forEach((m) => { t += K.sonify(m.prototype, m.fs, t) + 0.35; });
  });

  // ---------------- RESULTS ----------------
  $("btn-res-refresh").addEventListener("click", renderResults);
  let resBusy = false;
  async function renderResults() {
    if (resBusy) return; resBusy = true;
    let r = null;
    try { r = await K.api("/api/results"); } catch (e) { $("res-note").textContent = e.message; resBusy = false; return; }
    resBusy = false;
    const pct = (x) => Math.round(x * 100) + "%";
    const rec = r.recognition;
    $("res-rec").innerHTML = rec.n
      ? `<div class="bignum">${pct(rec.accuracy)}<small>${rec.correct} of ${rec.n}</small></div>
         <div>held-out demos named correctly (chance ${pct(rec.chance)})</div>
         ${rec.per_master.map((m) => `<div class="muted" style="font-size:13px">${K.esc(m.master)}: ${m.correct}/${m.n}</div>`).join("")}
         <div class="honest">${K.esc(rec.how)}</div>`
      : `<p class="muted" style="margin:0">Needs one master with at least two words of the same kind, two demos each.</p>`;
    const who = r.who || [];
    $("res-who").innerHTML = who.length
      ? who.map((w) => `<div class="row" style="align-items:baseline"><div class="bignum" style="font-size:40px">${pct(w.accuracy)}</div><div>「${K.esc(w.word)}」 · ${K.esc(w.stype)} · ${w.correct}/${w.n} demos assigned to the right master (chance ${pct(w.chance)})</div></div>`).join("")
        + `<div class="honest">If one dictionary served every master, this would sit at chance.</div>`
      : `<p class="muted" style="margin:0">Needs the same word from two masters. Let a second person teach a word the first one taught.</p>`;
    const p = r.pilot || {};
    const row = (k, label) => { const g = p[k]; if (!g) return `<tr><td>${label}</td><td colspan="4" class="muted">no sessions yet</td></tr>`;
      return `<tr><td>${label}</td><td>${g.sessions}</td><td>${g.learners}</td><td>${g.reached}/${g.sessions}</td><td><b>${g.mean_tries == null ? "–" : g.mean_tries}</b></td></tr>`; };
    $("res-pilot").innerHTML = `<table class="mini"><thead><tr><th>Condition</th><th>Sessions</th><th>Learners</th><th>Reached the range</th><th>Mean tries</th></tr></thead>
      <tbody>${row("word_only", "Word only")}${row("kotsu", "KOTSU feedback")}</tbody></table>
      <div class="honest">A small pilot, not a study. Keep the master, word and input the same for both conditions, and give each learner one condition.</div>`;
    const c = r.counts;
    $("res-counts").innerHTML = [["masters", c.masters], ["words grounded", c.words], ["lessons caught", c.lessons], ["corrections", c.corrections], ["questions answered", c.questions], ["passport entries", c.passports]]
      .map(([k, v]) => `<span class="chip">${k} <b>${v}</b></span>`).join("") + (c.synthetic_masters ? `<span class="chip" style="background:#FDECE7">includes ${c.synthetic_masters} synthetic rehearsal masters</span>` : "");
    $("res-note").textContent = c.synthetic_masters ? "Numbers include synthetic rehearsal data. Reset before presenting live numbers." : "";
  }

  // ---------------- TRANSCRIPTS ----------------
  $("btn-mine").addEventListener("click", runMine);
  $("mine-file").addEventListener("change", async (e) => { const f = e.target.files && e.target.files[0]; if (!f) return; $("mine-text").value = await f.text(); runMine(); });
  function fmtT(t) { if (t == null) return "–"; const m = Math.floor(t / 60), s = Math.floor(t % 60); return m + ":" + String(s).padStart(2, "0"); }
  async function runMine() {
    const text = $("mine-text").value;
    if (!text.trim()) { $("mine-status").textContent = "Paste a transcript first."; return; }
    $("mine-status").textContent = "Reading…";
    try {
      const r = await K.api("/api/mine", { text });
      const by = r.by_intent || {};
      $("mine-status").textContent = `${r.lines} lines read`;
      $("mine-summary").innerHTML = [["corrections", by["correction"] || 0], ["demos", by["demo"] || 0], ["wrong-way examples", by["wrong-way example"] || 0], ["approvals", by["approval"] || 0], ["full lessons (correction → approval)", r.triplets.length]]
        .map(([k, v]) => `<span class="chip">${k} <b>${v}</b></span>`).join("")
        + r.words.map((w) => `<span class="chip">${K.esc(w.word)} <b>×${w.count}</b>${w.known ? "" : " new"}</span>`).join("");
      $("mine-table").querySelector("tbody").innerHTML = r.moments.length ? r.moments.map((m) => `<tr><td>${fmtT(m.t)}</td><td>${K.esc(m.text)}</td><td><b>${K.esc(m.word || "")}</b>${m.new_word ? ' <span class="chip">new</span>' : ""}</td><td>${K.esc(m.intent)}</td>
        <td class="muted" style="font-size:13px">${m.try_clip ? "try " + fmtT(m.try_clip[0]) + "–" + fmtT(m.try_clip[1]) : ""}${m.try_clip && m.demo_clip ? " · " : ""}${m.demo_clip ? "demo " + fmtT(m.demo_clip[0]) + "–" + fmtT(m.demo_clip[1]) : ""}</td></tr>`).join("")
        : `<tr><td colspan="5" class="muted">No craft-word corrections found in this transcript.</td></tr>`;
    } catch (e) { $("mine-status").textContent = e.message; }
  }

  // ---------------- ASK ----------------
  $("btn-ask").addEventListener("click", ask);
  async function ask() {
    const key = $("ask-word").value; if (!key) return;
    $("ask-status").textContent = "";
    try {
      const q = await K.api("/api/nearmiss", { key });
      S.question = q;
      $("ask-q").innerHTML = `「これも${K.esc(q.word)}？」 <span class="muted" style="font-size:22px">Is this still ${K.esc(q.word)}?</span>`;
      K.plot($("ask-plot"), [{ y: q.prototype, fs: q.fs, color: "#24427A", width: 3, label: "master’s stroke" }, { y: q.profile, fs: q.fs, color: "#D9480F", width: 3, label: "changed: " + q.feature }], {});
      $("ask-yes").disabled = false; $("ask-no").disabled = false;
      $("ask-status").textContent = `Only “${q.feature}” changed: ${K.fmtFeature(q.feature, q.value)} (the master’s own is about ${K.fmtFeature(q.feature, q.band.center)}).`;
    } catch (e) { $("ask-status").textContent = e.message; }
  }
  $("ask-play-m").addEventListener("click", () => S.question && K.sonify(S.question.prototype, S.question.fs));
  $("ask-play-q").addEventListener("click", () => S.question && K.sonify(S.question.profile, S.question.fs));
  $("ask-yes").addEventListener("click", () => answer(true));
  $("ask-no").addEventListener("click", () => answer(false));
  async function answer(yes) {
    const q = S.question; if (!q) return;
    $("ask-yes").disabled = true; $("ask-no").disabled = true;
    try {
      const w = await K.api("/api/nearmiss/" + q.id, { yes });
      S.askLog.unshift({ f: q.feature, v: q.value, yes });
      const tb = $("ask-log").querySelector("tbody");
      tb.innerHTML = S.askLog.slice(0, 12).map((r) => `<tr><td>${K.esc(r.f)}</td><td>${K.fmtFeature(r.f, r.v)}</td><td>${r.yes ? "はい Yes" : "いいえ No"}</td></tr>`).join("");
      S.byKey[w.key] = w; renderAskBands(w);
      $("ask-status").textContent = yes ? "Recorded: still counts. The band widened on that side." : "Recorded: does not count. The band now stops before this point.";
      S.question = null;
      setTimeout(ask, 900);
    } catch (e) { $("ask-status").textContent = e.message; }
  }
  function renderAskBands(w) {
    w = w || S.byKey[$("ask-word").value];
    const box = $("ask-bands"); box.innerHTML = "";
    if (!w) return;
    w.bands.forEach((b) => box.appendChild(bandBar(b, null)));
  }
  function bandBar(b, value) {
    const lo = Math.min(b.lo, value == null ? b.lo : value), hi = Math.max(b.hi, value == null ? b.hi : value);
    const span = (hi - lo) || 1, pad = span * 0.6, a = Math.max(0, lo - pad), z = hi + pad;
    const pct = (v) => ((v - a) / (z - a)) * 100;
    const d = document.createElement("div"); d.className = "bar";
    d.innerHTML = `<div>${K.esc(b.name_en || b.feature)}<div class="muted" style="font-size:12px">${K.fmtFeature(b.feature, b.lo)} – ${K.fmtFeature(b.feature, b.hi)}</div></div>
      <div class="track"><div class="bandbox" style="left:${pct(b.lo)}%;width:${pct(b.hi) - pct(b.lo)}%"></div>${value == null ? "" : `<div class="val" style="left:calc(${pct(value)}% - 2px)"></div>`}</div>`;
    return d;
  }
  $("ask-word").addEventListener("change", () => renderAskBands());

  // ---------------- COACH ----------------
  $("btn-coach").addEventListener("click", async () => {
    const key = $("coach-word").value; if (!key) return;
    let lid = $("sel-learner").value;
    if (!lid) { const l = await K.api("/api/learner", { name: "Learner", lang: "en" }); lid = l.id; await refresh(); $("sel-learner").value = lid; }
    S.condition = $("coach-cond").value;
    await K.api("/api/coach/start", { key, learner_id: lid, condition: S.condition });
    S.coachWord = S.byKey[key]; S.coach = { level: S.condition === "word_only" ? 0 : 3, attempts: 0 };
    renderCoachHeader(); renderLevels(S.coach.level);
    $("coach-score").textContent = "–"; $("coach-score").className = "score";
    $("coach-feedback").innerHTML = "Listen to the master’s word, then try.";
    $("coach-bars").innerHTML = ""; $("btn-test").disabled = false;
    $("coach-status").textContent = S.condition === "word_only" ? "Control condition: the learner gets only the master’s word and a tick or a cross." : "Coaching started. Every stroke is scored.";
    drawCoachPlot(null);
    K.playClip(S.coachWord);
  });
  function renderCoachHeader() {
    const w = S.coachWord || S.byKey[$("coach-word").value]; if (!w) return;
    $("coach-w").textContent = w.word;
    const control = S.condition === "word_only" && S.coach;  // the control gets a dictionary gloss, not the master's measured meaning
    $("coach-gloss").innerHTML = `${K.esc(w.gloss_en)}${control ? "" : " · " + K.esc(w.meaning.en)}`;
    $("coach-master").textContent = "As taught by " + w.master + " (" + w.stype + ")";
  }
  $("coach-word").addEventListener("change", () => { S.coachWord = S.byKey[$("coach-word").value]; renderCoachHeader(); drawCoachPlot(null); });
  $("coach-voice").addEventListener("click", () => { const w = S.coachWord || S.byKey[$("coach-word").value]; if (w) K.playClip(w); });
  $("coach-hear").addEventListener("click", () => { const w = S.coachWord || S.byKey[$("coach-word").value]; if (w) K.sonify(w.prototype, w.fs); });
  $("btn-test").addEventListener("click", async () => {
    try { await K.api("/api/coach/test", {}); S.coach.test = true; $("coach-status").textContent = "Test: 3 strokes with all help switched off."; $("coach-feedback").innerHTML = "Now three strokes, no help."; $("coach-bars").innerHTML = ""; drawCoachPlot(null, true); }
    catch (e) { K.toast(e.message); }
  });

  function renderLevels(level) {
    const box = $("coach-levels"); box.innerHTML = '<span class="muted" style="font-size:13px">help</span>';
    for (let i = 1; i <= 3; i++) { const d = document.createElement("i"); if (i <= level) d.className = "on"; box.appendChild(d); }
  }

  function drawCoachPlot(stroke, hideHelp) {
    const w = S.coachWord || S.byKey[$("coach-word").value]; if (!w) return;
    const level = S.coach ? S.coach.level : 3;
    const wordOnly = S.condition === "word_only" && S.coach;
    const showHelp = !wordOnly && !hideHelp && !(S.coach && S.coach.test) && level >= 3;
    const series = [];
    if (wordOnly) { if (stroke) series.push({ y: stroke.profile, fs: stroke.fs, color: "#D9480F", width: 3, label: "you" }); K.plot($("coach-plot"), series, {}); return; }
    if (showHelp || !stroke) series.push({ y: w.prototype, fs: w.fs, color: "#24427A", width: 3, label: "master" });
    if (stroke) series.push({ y: stroke.profile, fs: stroke.fs, color: "#D9480F", width: 3, label: "you" });
    K.plot($("coach-plot"), series, { envelope: showHelp ? w.envelope : null });
  }

  function onCoachResult(c, stroke) {
    if (c.error) { $("coach-status").textContent = c.error; return; }
    if (!S.coach) S.coach = { level: c.level };
    S.coach.level = c.level;
    const inTest = !!c.test;
    drawCoachPlot(stroke, inTest);
    renderLevels(c.level);
    const sc = $("coach-score");
    if (inTest) {
      sc.textContent = c.test.done ? (c.test.passed ? "合格" : "もう一度") : `${c.test.n}/${c.test.needed}`;
      sc.className = "score " + (c.test.done ? (c.test.passed ? "pass" : "fail") : "");
      if (c.test.done) {
        S.coach.test = false;
        $("coach-feedback").innerHTML = c.test.passed
          ? `Passed. 「${K.esc(c.word)}」 is now in your skill passport.`
          : "Not yet. A little more practice, then test again.";
        if (c.test.passed) { K.toast("Added to the skill passport"); K.chime(true); }
      } else $("coach-feedback").innerHTML = `<span class="muted">Recorded. Next one.</span>`;
      return;
    }
    S.coach.attempts = (S.coach.attempts || 0) + 1;
    if (c.passed) K.chime(true);
    if (c.condition === "word_only") {
      sc.textContent = c.passed ? "✓" : "✗"; sc.className = "score " + (c.passed ? "pass" : "fail");
      $("coach-feedback").innerHTML = c.passed ? `That’s it — 「${K.esc(c.word)}」` : `Again — 「${K.esc(c.word)}」`;
      $("coach-bars").innerHTML = "";
      $("coach-status").textContent = `Attempt ${S.coach.attempts} · word only (control)`;
      return;
    }
    const level = c.level;
    sc.textContent = level >= 1 ? Math.round(c.score) : (c.passed ? "✓" : "·");
    sc.className = "score " + (c.passed ? "pass" : "fail");
    let fb = "";
    if (c.passed) fb = `That’s it — 「${K.esc(c.word)}」`;
    else if (level >= 2 && c.feedback) fb = K.esc(c.feedback.en);
    else if (level >= 1) fb = "Try again.";
    else fb = "On your own now.";
    if (c.ready_for_test) fb += `<div style="font-size:20px;margin-top:6px">Ready for the test.</div>`;
    $("coach-feedback").innerHTML = fb;
    const bars = $("coach-bars"); bars.innerHTML = "";
    if (level >= 3) (c.features || []).forEach((r) => bars.appendChild(bandBar({ feature: r.feature, lo: r.lo, hi: r.hi, name_en: r.name_en }, r.value)));
    $("coach-status").textContent = `Attempt ${S.coach.attempts} · help level ${level} of 3 · help fades as you improve`;
    if (!c.passed && level >= 3) { const w = S.coachWord; if (w && w.clip) K.playClip(w); }
  }

  // ---------------- DRILL ----------------
  $("btn-drill").addEventListener("click", nextDrill);
  async function nextDrill() {
    const key = $("drill-word").value; if (!key) return;
    $("drill-result").textContent = "";
    try {
      const d = await K.api("/api/drill", { key, learner_id: $("sel-learner").value || null });
      S.drill = d;
      $("drill-q").innerHTML = `Which one is 「${K.esc(d.word)}」?`;
      K.plot($("drill-a"), [{ y: d.A, fs: d.fs, color: "#15191E", width: 3 }], {});
      K.plot($("drill-b"), [{ y: d.B, fs: d.fs, color: "#15191E", width: 3 }], {});
      $("drill-pick-a").disabled = false; $("drill-pick-b").disabled = false;
      $("drill-stats").textContent = `difference size ×${d.gap}`;
    } catch (e) { $("drill-result").textContent = e.message; }
  }
  $("drill-play-a").addEventListener("click", () => S.drill && K.sonify(S.drill.A, S.drill.fs));
  $("drill-play-b").addEventListener("click", () => S.drill && K.sonify(S.drill.B, S.drill.fs));
  $("drill-pick-a").addEventListener("click", () => pick("A"));
  $("drill-pick-b").addEventListener("click", () => pick("B"));
  async function pick(choice) {
    const d = S.drill; if (!d) return;
    $("drill-pick-a").disabled = true; $("drill-pick-b").disabled = true;
    const r = await K.api("/api/drill/" + d.id, { choice });
    $("drill-result").innerHTML = r.correct ? `<span style="color:var(--pass)">Right. ${K.esc(r.answer)} is the master’s ${K.esc(d.word)}.</span>` : `<span style="color:var(--fail)">Not quite. ${K.esc(r.answer)} was the master’s ${K.esc(d.word)}; the other changed “${K.esc(d.feature)}”.</span>`;
    $("drill-stats").textContent = `${r.stats.right}/${r.stats.total} right`;
    S.drill = null;
    setTimeout(nextDrill, 1400);
  }

  // ---------------- PASSPORT ----------------
  $("btn-pp-refresh").addEventListener("click", renderPassport);
  async function renderPassport() {
    const box = $("passport");
    const lid = $("sel-learner").value;
    if (!lid) { box.innerHTML = `<div class="card"><p class="muted" style="margin:0">Add or choose a learner first.</p></div>`; return; }
    const p = await K.api("/api/passport/" + lid);
    const entries = p.entries || [];
    box.innerHTML = `<div class="passport">
      <div class="row" style="justify-content:space-between"><div><div class="muted">技能パスポート · Skill passport</div><div class="wordmid">${K.esc(p.learner ? p.learner.name : "")}</div></div>
      <div class="muted">Drills: ${p.drills.right}/${p.drills.total} right</div></div>
      ${entries.length ? "" : `<p class="muted" style="margin:0">No words passed yet. Pass an unassisted test in Coach.</p>`}
      ${entries.map((e, i) => `<div class="pentry"><div><div class="wordmid">${K.esc(e.word)}</div><div class="muted">${K.esc(e.stype)}</div></div>
        <div><div>Taught by <b>${K.esc(e.master)}</b> · unassisted test passed · average score ${e.score}</div><div class="muted" style="font-size:13px">${new Date(e.t * 1000).toLocaleString()} · ${e.attempts} practice strokes · check code ${K.esc(e.verify)}</div><canvas class="plot small" data-i="${i}"></canvas></div>
        <div class="stamp">合格<br>PASSED</div></div>`).join("")}
      <div class="honest">Evidence for the employer’s training record (for example a 育成就労 training plan): who taught the word, the unassisted stroke, the score. Not an official skills test.</div>
    </div>`;
    entries.forEach((e, i) => { const c = box.querySelector(`canvas[data-i="${i}"]`); if (c) K.plot(c, [{ y: e.profile, fs: e.fs, color: "#D9480F", width: 3 }], {}); });
  }

  // ---------------- ROBOT ----------------
  const rc = $("robot-canvas");
  function drawRobotIdle() { robotFrame(0, null, []); }
  $("btn-robot").addEventListener("click", () => {
    const w = S.byKey[$("robot-word").value]; if (!w || !w.prototype.length) return;
    const y = w.prototype, fs = w.fs, dur = (y.length - 1) / fs;
    let max = 0; y.forEach((v) => { if (v > max) max = v; });
    const cum = [0]; for (let i = 1; i < y.length; i++) cum.push(cum[i - 1] + y[i]);
    const total = cum[cum.length - 1] || 1;
    const trail = []; const t0 = performance.now();
    K.sonify(y, fs);
    $("robot-status").textContent = `Replaying ${w.master}’s ${w.word} (${w.stype}) · ${dur.toFixed(2)} s`;
    function tick(now) {
      const t = Math.min(dur, (now - t0) / 1000); const i = Math.min(y.length - 1, Math.round(t * fs));
      const st = { x: w.stype === "force" ? 0.5 : cum[i] / total, press: w.stype === "force" ? y[i] / (max || 1) : 0.15 + 0.2 * (y[i] / (max || 1)), v: y[i] / (max || 1) };
      trail.push(st); robotFrame(t / dur, st, trail, w);
      if (t < dur) requestAnimationFrame(tick); else $("robot-status").textContent += " · done";
    }
    requestAnimationFrame(tick);
  });
  function robotFrame(p, st, trail, w) {
    const dpr = window.devicePixelRatio || 1, W = rc.clientWidth || 800, H = rc.clientHeight || 420;
    if (rc.width !== Math.round(W * dpr)) { rc.width = Math.round(W * dpr); rc.height = Math.round(H * dpr); }
    const c = rc.getContext("2d"); c.setTransform(dpr, 0, 0, dpr, 0, 0); c.clearRect(0, 0, W, H);
    const surf = H * 0.72, bx = W * 0.5, by = H * 0.12, L1 = H * 0.42, L2 = H * 0.38;
    c.fillStyle = "#E4E1D8"; c.fillRect(W * 0.12, surf, W * 0.76, H * 0.12); c.strokeStyle = "#B9B4A6"; c.strokeRect(W * 0.12, surf, W * 0.76, H * 0.12);
    c.fillStyle = "#4A5260"; c.fillRect(bx - 40, by - 24, 80, 24);
    trail.forEach((s) => { const x = W * 0.18 + s.x * W * 0.64; c.fillStyle = `rgba(217,72,15,${0.15 + 0.6 * s.v})`; c.beginPath(); c.arc(x, surf - 2, 2 + 10 * s.press, 0, Math.PI * 2); c.fill(); });
    const s = st || { x: 0, press: 0, v: 0 };
    const tx = W * 0.18 + s.x * W * 0.64, ty = surf - 6 + 10 * s.press;
    const dx = tx - bx, dy = ty - by, d = Math.min(Math.hypot(dx, dy), L1 + L2 - 1);
    const a2 = Math.acos(Math.max(-1, Math.min(1, (d * d - L1 * L1 - L2 * L2) / (2 * L1 * L2))));
    const a1 = Math.atan2(dy, dx) - Math.atan2(L2 * Math.sin(a2), L1 + L2 * Math.cos(a2));
    const ex = bx + L1 * Math.cos(a1), ey = by + L1 * Math.sin(a1);
    const hx = ex + L2 * Math.cos(a1 + a2), hy = ey + L2 * Math.sin(a1 + a2);
    c.lineCap = "round"; c.strokeStyle = "#24427A"; c.lineWidth = 18; c.beginPath(); c.moveTo(bx, by); c.lineTo(ex, ey); c.stroke();
    c.lineWidth = 14; c.beginPath(); c.moveTo(ex, ey); c.lineTo(hx, hy); c.stroke();
    c.fillStyle = "#15191E"; [[bx, by], [ex, ey]].forEach(([x, y]) => { c.beginPath(); c.arc(x, y, 10, 0, Math.PI * 2); c.fill(); });
    c.fillStyle = "#D9480F"; c.beginPath(); c.arc(hx, hy, 9 + 10 * s.press, 0, Math.PI * 2); c.fill();
    c.fillStyle = "#4A5260"; c.font = "16px " + getComputedStyle(document.body).fontFamily;
    c.fillText(w ? `Simulated robot · ${w.word} · ${w.master}` : "Choose a word and press Replay", 16, H - 14);
  }

  // ---------------- KNACK GRAPH: masters and learners -> words -> measured feel ----------------
  const KG = { data: null, sel: null, auto: true, colors: ["#24427A", "#D9480F", "#1E7F4F", "#8E2D7A", "#7A5B00", "#0E6E78"] };
  const SHORT = { rise: "rise time", peak: "peak", duration: "length", steady_cv: "evenness", hold: "hold", stop_time: "stop",
                  jitter: "shake", mean_mid: "middle level", pulses: "taps", peak_acc: "push", wobble: "wobble", sound_level: "loudness", sound_rough: "roughness" };
  const STAGE_TEXT = { "守": "practising with help", "破": "reached the master’s range", "離": "passed alone (skill passport)" };
  const SVGNS = "http://www.w3.org/2000/svg";
  function sv(tag, attrs, parent) { const e = document.createElementNS(SVGNS, tag); Object.entries(attrs || {}).forEach(([k, v]) => e.setAttribute(k, v)); if (parent) parent.appendChild(e); return e; }
  function curve(x1, y1, x2, y2) { const dx = (x2 - x1) * 0.45; return `M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}`; }
  async function loadGraph() { try { KG.data = await K.api("/api/graph"); } catch (e) { return; } drawGraph(); }
  function mcolor(id) { const i = KG.data.masters.findIndex((m) => m.id === id); return KG.colors[(i < 0 ? 0 : i) % KG.colors.length]; }
  function drawGraph() {
    const g = KG.data, svg = $("kg"); if (!g) return;
    svg.innerHTML = "";
    if (!g.words.length) {
      svg.setAttribute("viewBox", "0 0 1200 200");
      sv("text", { x: 600, y: 105, "text-anchor": "middle", "font-size": 20, class: "muted-t" }, svg).textContent = "No words yet. Capture a lesson on Listen: a try, the master’s correction, his demo.";
      return;
    }
    const W = 1200, X = { people: 150, word: 560, feat: 930 };
    const words = g.words.slice().sort((a, b) => (a.master + a.word).localeCompare(b.master + b.word));
    const people = g.masters.map((m) => ({ kind: "m", d: m })).concat(g.learners.length ? [{ kind: "gap" }] : [], g.learners.map((l) => ({ kind: "l", d: l })));
    const rows = Math.max(words.length, g.features.length, people.length, 3);
    const top = 92, step = 80, avail = (rows - 1) * step, H = top + avail + 66;
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    const ys = (n) => { const gap = n <= 1 ? 0 : Math.min(step * 1.35, avail / (n - 1)); const start = top + (avail - gap * (n - 1)) / 2; return (i) => start + i * gap; };
    const pos = {};
    const yp = ys(people.length); people.forEach((p, i) => { const y = yp(i); if (p.kind === "m") pos[p.d.id] = { x: X.people, y }; else if (p.kind === "l") pos["L:" + p.d.id] = { x: X.people, y }; else pos.gap = { x: X.people, y }; });
    const yw = ys(words.length); words.forEach((w, i) => { pos[w.key] = { x: X.word, y: yw(i) }; });
    const yf = ys(g.features.length); g.features.forEach((f, i) => { pos["F:" + f.id] = { x: X.feat, y: yf(i) }; });
    // headers
    [["師匠 Masters", X.people], ["言葉 Word", X.word], ["感覚 Feel, measured", X.feat]].forEach(([t, x]) => { sv("text", { x, y: 44, "text-anchor": "middle", class: "hdr" }, svg).textContent = t; });
    if (pos.gap) sv("text", { x: X.people, y: pos.gap.y + 6, "text-anchor": "middle", class: "hdr" }, svg).textContent = "弟子 Learners";
    const edges = sv("g", {}, svg), nodes = sv("g", {}, svg);
    KG.edgeEls = [];
    const addEdge = (a, b, d, attrs, meta) => { const p = sv("path", Object.assign({ d, class: "edge" }, attrs), edges); p.dataset.a = a; p.dataset.b = b; KG.edgeEls.push({ el: p, a, b, meta }); return p; };
    words.forEach((w) => {
      const pm = pos[w.master_id], pw = pos[w.key];
      if (pm) addEdge(w.master_id, w.key, curve(pm.x + 28, pm.y, pw.x - 38, pw.y), { stroke: mcolor(w.master_id), "stroke-width": 3, opacity: 0.6 }, { kind: "teach" });
    });
    g.links.forEach((l) => {
      const pl = pos["L:" + l.learner], pw = pos[l.word]; if (!pl || !pw) return;
      const width = l.stage === "離" ? 4 : l.stage === "破" ? 3 : 2;
      addEdge("L:" + l.learner, l.word, curve(pl.x + 20, pl.y, pw.x - 38, pw.y), { stroke: "#5E6673", "stroke-width": width, "stroke-dasharray": l.stage === "守" ? "6 6" : "none", opacity: 0.55 }, { kind: "learn", stage: l.stage });
      const t = 0.28, x = (1 - t) * (pl.x + 20) + t * (pw.x - 38), y = (1 - t) * pl.y + t * pw.y;
      const lab = sv("text", { x, y: y - 6, "text-anchor": "middle", class: "stage edgelab", fill: l.stage === "離" ? "#1E7F4F" : "#4A5260" }, edges); lab.textContent = l.stage; lab.dataset.a = "L:" + l.learner; lab.dataset.b = l.word;
      KG.edgeEls.push({ el: lab, a: "L:" + l.learner, b: l.word, meta: { kind: "label" } });
    });
    g.edges.forEach((e) => {
      const pw = pos[e.word], pf = pos["F:" + e.feature]; if (!pw || !pf) return;
      const color = e.direction > 0 ? "#D9480F" : e.direction < 0 ? "#24427A" : "#9AA1AB";
      addEdge(e.word, "F:" + e.feature, curve(pw.x + 38, pw.y, pf.x - 24, pf.y), { stroke: color, "stroke-width": Math.max(2, Math.min(9, 1.5 + e.salience * 1.1)), opacity: 0.75 }, { kind: "means", e });
    });
    const addNode = (id, label, draw) => { const n = sv("g", { class: "node", tabindex: 0, role: "button", "aria-label": label }, nodes); n.dataset.id = id; draw(n); n.addEventListener("click", (ev) => { ev.stopPropagation(); selectKG(id); }); n.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); selectKG(id); } }); return n; };
    g.masters.forEach((m) => { const p = pos[m.id]; addNode(m.id, "Master " + m.name, (n) => {
      sv("circle", { cx: p.x, cy: p.y, r: 27, fill: mcolor(m.id), class: "ring" }, n);
      sv("text", { x: p.x, y: p.y + 6, "text-anchor": "middle", "font-size": 17, "font-weight": 800, fill: "#FFFFFF" }, n).textContent = m.name.split(/\s+/).filter((x) => /^[A-Za-z]/.test(x)).map((x) => x[0]).join("").slice(0, 2).toUpperCase() || "M";
      sv("text", { x: p.x, y: p.y + 48, "text-anchor": "middle", "font-size": 16, "font-weight": 600 }, n).textContent = m.name; }); });
    g.learners.forEach((l) => { const p = pos["L:" + l.id]; addNode("L:" + l.id, "Learner " + l.name, (n) => {
      sv("circle", { cx: p.x, cy: p.y, r: 18, fill: "#F2EDE3", stroke: "#78808C", "stroke-width": 2, class: "ring" }, n);
      sv("text", { x: p.x, y: p.y + 37, "text-anchor": "middle", "font-size": 14 }, n).textContent = l.name; }); });
    words.forEach((w) => { const p = pos[w.key]; addNode(w.key, `${w.word} taught by ${w.master}`, (n) => {
      sv("circle", { cx: p.x, cy: p.y, r: 37, fill: "#FFFFFF", stroke: mcolor(w.master_id), "stroke-width": 4, class: "ring" }, n);
      sv("text", { x: p.x, y: p.y + 8, "text-anchor": "middle", "font-size": w.word.length > 3 ? 19 : 23, "font-weight": 800 }, n).textContent = w.word;
      sv("text", { x: p.x, y: p.y + 56, "text-anchor": "middle", "font-size": 13, class: "muted-t" }, n).textContent = `${w.romaji || "new word"} · ${w.master}`; }); });
    g.features.forEach((f) => { const p = pos["F:" + f.id]; addNode("F:" + f.id, "Measured feature " + (SHORT[f.id] || f.name), (n) => {
      sv("circle", { cx: p.x, cy: p.y, r: 23, fill: "#EEF1F6", stroke: "#9AA1AB", "stroke-width": 2, class: "ring" }, n);
      sv("text", { x: p.x + 34, y: p.y + 6, "font-size": 18, "font-weight": 600 }, n).textContent = SHORT[f.id] || f.name; }); });
    svg.onclick = () => selectKG(null);
    KG.pos = pos;
    if (KG.auto && KG.sel == null) { const f = contestedFeature(); if (f) { selectKG("F:" + f); return; } }
    if (KG.sel && (pos[KG.sel] || pos[KG.sel.replace(/^L:/, "")])) selectKG(KG.sel); else renderKGDetail();
  }
  function contestedFeature() {
    const g = KG.data; let best = null, bestScore = 0;
    g.features.forEach((f) => {
      const es = g.edges.filter((e) => e.feature === f.id);
      const byWord = {}; es.forEach((e) => { const w = g.words.find((x) => x.key === e.word); if (!w) return; (byWord[w.word] = byWord[w.word] || new Set()).add(w.master_id); });
      const score = Math.max(0, ...Object.values(byWord).map((st) => st.size));
      if (score > bestScore) { best = f.id; bestScore = score; }
    });
    return bestScore >= 2 ? best : null;
  }
  function selectKG(id) {
    KG.sel = id; KG.auto = false;
    const svg = $("kg"); if (!KG.data) return;
    const on = new Set(id ? [id] : []);
    if (id) {
      const g = KG.data;
      const wordsOf = (pred) => g.words.filter(pred).map((w) => w.key);
      let wk = [];
      if (id.startsWith("F:")) wk = g.edges.filter((e) => "F:" + e.feature === id).map((e) => e.word);
      else if (id.startsWith("L:")) wk = g.links.filter((l) => "L:" + l.learner === id).map((l) => l.word);
      else if (g.words.some((w) => w.key === id)) wk = [id];
      else wk = wordsOf((w) => w.master_id === id);
      wk.forEach((k) => { on.add(k); const w = g.words.find((x) => x.key === k); if (w) on.add(w.master_id); });
      if (!id.startsWith("F:")) g.edges.filter((e) => wk.includes(e.word)).forEach((e) => on.add("F:" + e.feature));
      if (!id.startsWith("F:") && !id.startsWith("L:")) g.links.filter((l) => wk.includes(l.word)).forEach((l) => on.add("L:" + l.learner));
      if (id.startsWith("L:")) on.add(id);
    }
    svg.classList.toggle("has-sel", !!id);
    svg.querySelectorAll(".node").forEach((n) => n.classList.toggle("on", on.has(n.dataset.id)));
    (KG.edgeEls || []).forEach(({ el, a, b }) => el.classList.toggle("on", on.has(a) && on.has(b) && (!id.startsWith("F:") || a === id || b === id || !(a.startsWith("F:") || b.startsWith("F:")))));
    renderKGDetail();
  }
  function rangeRows(rows, unit, scale) {
    let lo = scale ? scale[0] : Math.min(...rows.map((r) => r.lo)), hi = scale ? scale[1] : Math.max(...rows.map((r) => r.hi));
    const pad = (hi - lo) * 0.08 || Math.max(0.05, hi * 0.1); lo = Math.max(0, lo - pad); hi += pad;
    const pct = (v) => ((v - lo) / (hi - lo)) * 100;
    return `<div class="kg-range">${rows.map((r) => `<div><b>${K.esc(r.word)}</b> · ${K.esc(r.master)}<div class="muted" style="font-size:13px">${K.fmtFeature(r.feature, r.lo)} – ${K.fmtFeature(r.feature, r.hi)}${unit && !/ s$/.test(K.fmtFeature(r.feature, r.lo)) ? " " + K.esc(unit) : ""}</div></div>
      <div class="tr"><i style="left:${pct(r.lo)}%;width:${Math.max(2, pct(r.hi) - pct(r.lo))}%;background:${r.color}"></i></div>`).join("")}</div>`;
  }
  function renderKGDetail() {
    const box = $("kg-detail"), g = KG.data, id = KG.sel;
    if (!g) return;
    if (!id) {
      const shared = g.comparable && g.comparable[0];
      box.innerHTML = `<h3>${g.masters.length} masters · ${g.words.length} words · ${g.features.length} measured features · ${g.learners.length} learners</h3>
        <p class="muted" style="margin:0">Masters and learners on the left, the master’s words in the middle, what each word means in measured feel on the right. The coloured lines are the meaning KOTSU learned from each master’s own corrections.</p>
        ${shared ? `<p style="margin:0">Click a feature to compare masters: 「${K.esc(shared.word)}」 was taught by ${shared.masters} masters.</p>` : ""}`;
      return;
    }
    if (id.startsWith("F:")) {
      const f = g.features.find((x) => "F:" + x.id === id); if (!f) return;
      const rows = g.edges.filter((e) => "F:" + e.feature === id).map((e) => { const w = g.words.find((x) => x.key === e.word) || {}; return { word: w.word, master: w.master, lo: e.lo, hi: e.hi, feature: e.feature, color: mcolor(w.master_id), stype: w.stype }; });
      const groups = {}; rows.forEach((r) => { const k = r.word + "|" + r.stype; (groups[k] = groups[k] || []).push(r); });
      const keys = Object.keys(groups).sort((a, b) => groups[b].length - groups[a].length);
      const name = SHORT[f.id] || f.name;
      box.innerHTML = `<h3 style="font-size:22px">${K.esc(name)}</h3><div class="muted">${K.esc(f.name)}</div>
        ${keys.map((k) => { const gr = groups[k].sort((a, b) => a.lo - b.lo); const w = gr[0].word;
          return `${gr.length >= 2 ? `<p style="margin:0;font-size:18px"><b>Same word, different hands.</b> 「${K.esc(w)}」 means a different ${K.esc(name)} for each of the ${gr.length} masters who taught it.</p>` : `<p style="margin:6px 0 0;font-weight:600">「${K.esc(w)}」</p>`}
            ${rangeRows(gr, f.unit)}`; }).join("")}
        <p class="muted" style="margin:0;font-size:13px">Each bar is one master’s own range (型), learned from his demos against the tries he corrected.</p>`;
      return;
    }
    if (id.startsWith("L:")) {
      const l = g.learners.find((x) => "L:" + x.id === id); if (!l) return;
      const ls = g.links.filter((x) => "L:" + x.learner === id);
      box.innerHTML = `<h3 style="font-size:22px">${K.esc(l.name)}</h3><div class="muted">弟子 · learner</div>
        ${ls.map((x) => { const w = g.words.find((y) => y.key === x.word) || {}; return `<div class="row"><span style="font-size:28px;font-weight:800;width:36px">${x.stage}</span><div><b>${K.esc(w.word)}</b> · taught by ${K.esc(w.master)}<div class="muted" style="font-size:13px">${STAGE_TEXT[x.stage]}</div></div></div>`; }).join("")}`;
      return;
    }
    const w = g.words.find((x) => x.key === id);
    if (w) {
      const es = g.edges.filter((e) => e.word === id);
      box.innerHTML = `<div class="row" style="align-items:flex-end"><div class="wordmid">${K.esc(w.word)}</div><div class="muted">${K.esc(w.romaji || "")} · ${K.esc(w.gloss_en || "")}</div></div>
        <div>Taught by <b>${K.esc(w.master)}</b> · ${w.n_pos} demo${w.n_pos === 1 ? "" : "s"} · ${w.n_neg} corrected ${w.n_neg === 1 ? "try" : "tries"}</div>
        <div><b>For this master it means:</b> ${K.esc(w.meaning.en)}</div>
        <canvas class="plot small" id="kg-plot"></canvas>
        <h3>型 Form: the master’s range</h3>
        ${es.map((e) => { const all = g.edges.filter((x) => x.feature === e.feature);
            return rangeRows([{ word: SHORT[e.feature] || e.feature, master: e.direction > 0 ? "more than his usual" : e.direction < 0 ? "less than his usual" : "", lo: e.lo, hi: e.hi, feature: e.feature, color: e.direction > 0 ? "#D9480F" : "#24427A" }], "", [Math.min(...all.map((x) => x.lo)), Math.max(...all.map((x) => x.hi))]); }).join("")}
        <p class="muted" style="margin:0;font-size:13px">Each bar sits on the scale of every master’s range for that feature.</p>`;
      requestAnimationFrame(() => { const c = $("kg-plot"); if (c) K.plot(c, [{ y: w.prototype, fs: w.fs, color: mcolor(w.master_id), width: 3 }], {}); });
      return;
    }
    const m = g.masters.find((x) => x.id === id);
    if (m) {
      const ws = g.words.filter((x) => x.master_id === id);
      box.innerHTML = `<h3 style="font-size:22px">${K.esc(m.name)}</h3><div class="muted">師匠 · master</div>
        ${ws.map((x) => `<div><b style="font-size:20px">${K.esc(x.word)}</b> · ${K.esc(x.stype)} · ${x.n_lessons} lessons<div class="muted" style="font-size:13px">${K.esc(x.meaning.en)}</div></div>`).join("")}`;
    }
  }
  // a light running along the graph: master -> word -> its measured feel (and the learner when he passes)
  function traceKG(wordKey, learnerId) {
    if (!KG.edgeEls || !wordKey) return;
    const svg = $("kg"); const w = KG.data.words.find((x) => x.key === wordKey); if (!w) return;
    const runs = (pred) => KG.edgeEls.filter((x) => x.el.tagName === "path" && pred(x)).map((x) => x.el);
    const first = runs((x) => x.a === w.master_id && x.b === wordKey);
    const second = runs((x) => x.a === wordKey && x.b.startsWith("F:"));
    const third = learnerId ? runs((x) => x.a === "L:" + learnerId && x.b === wordKey) : [];
    const anim = (paths, ms, reverse) => new Promise((res) => {
      if (!paths.length) return res();
      const dots = paths.map(() => sv("circle", { r: 9, class: "kg-dot" }, svg));
      const lens = paths.map((p) => p.getTotalLength()); const t0 = performance.now();
      paths.forEach((p) => { p.classList.add("on"); p.style.opacity = 1; });
      const step = (now) => { const t = Math.min(1, (now - t0) / ms);
        paths.forEach((p, i) => { const pt = p.getPointAtLength((reverse ? 1 - t : t) * lens[i]); dots[i].setAttribute("cx", pt.x); dots[i].setAttribute("cy", pt.y); });
        if (t < 1) requestAnimationFrame(step); else { dots.forEach((d) => d.remove()); res(); } };
      requestAnimationFrame(step);
    });
    anim(first, 650).then(() => anim(second, 800)).then(() => anim(third, 650, true)).then(() => { (KG.edgeEls || []).forEach(({ el }) => { el.style.opacity = ""; }); selectKG(KG.sel); });
  }
  $("btn-kg-trace").addEventListener("click", () => {
    const g = KG.data; if (!g || !g.words.length) return;
    const sel = KG.sel && g.words.some((w) => w.key === KG.sel) ? KG.sel : null;
    const last = (S.state && S.state.lessons && S.state.lessons[0] && S.state.lessons[0].word_key) || g.words[0].key;
    traceKG(sel || last);
  });

  // ---------------- SETUP + CONNECT ----------------
  function phoneUrls() {
    const u = (S.state && S.state.urls) || {};
    const base = u.public || u.lan || location.origin;
    return { base, phone: base + "/phone", force: (u.lan || location.origin) + "/force", isHttps: base.startsWith("https://") };
  }
  function renderSetup() {
    const u = phoneUrls();
    if (S.state) fillSelect($("rename-master"), S.state.masters.map((m) => [m.id, m.name]), $("rename-master").value);
    if (S.state) fillSelect($("rename-learner"), S.state.learners.map((l) => [l.id, l.name]), $("rename-learner").value);
    $("setup-urls").innerHTML = `
      <div><b>Phone page:</b> <span class="kbd">${K.esc(u.phone)}</span> ${u.isHttps ? "" : '<span style="color:var(--fail)">(not https: phone motion sensors will be blocked; use the tunnel)</span>'}</div>
      <img alt="QR code for the phone page" src="/qr.svg?url=${encodeURIComponent(u.phone)}" style="width:220px;height:220px;background:#fff;border:1px solid var(--rule);border-radius:8px">
      <div><b>Force Touch station (Safari on a MacBook):</b> <span class="kbd">${K.esc(u.force)}</span></div>
      <div><b>This dashboard on another laptop:</b> <span class="kbd">${K.esc(u.base)}/</span></div>`;
    $("public-url").value = (S.state && S.state.urls.public) || "";
  }
  $("btn-public").addEventListener("click", async () => { await K.api("/api/public_url", { url: $("public-url").value.trim() }); await refresh(); renderSetup(); K.toast("Saved"); });
  $("btn-rename-learner").addEventListener("click", async () => {
    const id = $("rename-learner").value, n = $("rename-learner-to").value.trim(); if (!id || !n) return;
    try { await K.api("/api/learner/" + encodeURIComponent(id) + "/rename", { name: n }); $("rename-learner-to").value = ""; await refresh(); renderSetup(); K.toast("Renamed"); } catch (e) { K.toast(e.message); }
  });
  $("btn-rename-master").addEventListener("click", async () => {
    const id = $("rename-master").value, n = $("rename-to").value.trim(); if (!id || !n) return;
    try { await K.api("/api/master/" + encodeURIComponent(id) + "/rename", { name: n }); $("rename-to").value = ""; K.toast("Renamed"); await refresh(); renderSetup(); } catch (e) { K.toast(e.message); }
  });
  $("btn-add-master").addEventListener("click", async () => { const n = $("in-master").value.trim(); if (!n) return; await K.api("/api/master", { name: n }); $("in-master").value = ""; refresh(); });
  $("btn-add-learner").addEventListener("click", async () => { const n = $("in-learner").value.trim(); if (!n) return; const l = await K.api("/api/learner", { name: n, lang: "en" }); $("in-learner").value = ""; await refresh(); $("sel-learner").value = l.id; await K.api("/api/session", { learner_id: l.id }); });
  $("btn-seed").addEventListener("click", async () => { await K.api("/api/seed", {}); K.toast("Loaded synthetic sample master"); refresh(); });
  $("btn-reset").addEventListener("click", async () => {
    const box = document.createElement("div");
    $("modal-body").innerHTML = `<p>This deletes every word, stroke, lesson and passport. Type <b>RESET</b> to confirm.</p><div class="row"><input id="reset-in" style="flex:1;padding:8px;border:1px solid var(--rule);border-radius:8px"><button class="btn warn" id="reset-go" type="button">Delete everything</button></div>`;
    $("modal").hidden = false;
    $("reset-go").addEventListener("click", async () => { try { await K.api("/api/reset", { confirm: $("reset-in").value.trim() }); $("modal").hidden = true; K.toast("Everything was reset"); refresh(); } catch (e) { K.toast(e.message); } });
  });
  $("btn-connect").addEventListener("click", () => {
    const u = phoneUrls();
    $("modal-body").innerHTML = `<p style="margin:0">Scan with the phone’s camera, open the page, and tap <b>Start</b>.</p>
      <img alt="QR code for the phone page" src="/qr.svg?url=${encodeURIComponent(u.phone)}" style="width:260px;height:260px;align-self:center;background:#fff">
      <div class="kbd" style="word-break:break-all">${K.esc(u.phone)}</div>
      ${u.isHttps ? "" : '<p style="color:var(--fail);margin:0">This address is not https, so the phone’s motion sensors will be blocked. Start the tunnel (python run.py --tunnel) or paste its address in Setup. The phone’s Draw mode still works.</p>'}`;
    $("modal").hidden = false;
  });
  $("modal-close").addEventListener("click", () => { $("modal").hidden = true; });

  function redrawPlots() {
    if (S.view === "capture") drawCapture();
    if (S.view === "coach") drawCoachPlot(null);
  }
  window.addEventListener("resize", redrawPlots);
  refresh().then(() => { drawCapture(); renderCoachHeader(); if (EMBED) { show("graph"); setTimeout(() => { const b = $("btn-kg-trace"); if (b) b.click(); }, 1400); } });
})();
