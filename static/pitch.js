// KOTSU pitch screen: every tab reads this laptop's own KOTSU data; the Live tab reacts to real presses.
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const NS = "http://www.w3.org/2000/svg";
  const COLORS = ["#24427A", "#D9480F", "#1E7F4F", "#8E2D7A", "#7A5B00", "#0E6E78"];
  const LIGHT = ["#9db7ff", "#ff8f63", "#63d49c", "#e09ad8", "#e0c060", "#62d0d8"];
  const INK = "#1f1b16", MUTED = "#8a8276";
  const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const scenes = Array.from(document.querySelectorAll(".scene"));
  const ids = scenes.map((s) => s.dataset.id);
  let D = null, cur = 0, frag = 0, judgeSids = [], live = null, reloadTimer = 0;

  // ---------------- helpers ----------------
  function el(tag, attrs, parent) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs || {}) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const numWord = (n) => ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"][n] || String(n);
  const masterIndex = (mid) => { const i = D ? D.masters.findIndex((m) => m.id === mid) : -1; return i < 0 ? 0 : i; };
  const masterColor = (mid) => COLORS[masterIndex(mid) % COLORS.length];
  const masterLight = (mid) => LIGHT[masterIndex(mid) % LIGHT.length];
  const fmt = (v, d) => (v == null || isNaN(v) ? "–" : Number(v).toFixed(d == null ? 2 : d));
  const FEAT = {
    rise: (b) => `full strength in ${fmt(b.lo)}–${fmt(b.hi)} s`,
    peak: (b) => `peak pressure ${fmt(b.lo, 1)}–${fmt(b.hi, 1)}`,
    hold: (b) => `held ${fmt(b.lo)}–${fmt(b.hi)} s`,
    duration: (b) => `lasts ${fmt(b.lo)}–${fmt(b.hi)} s`,
    steady_cv: (b) => `unevenness ${fmt(b.lo)}–${fmt(b.hi)}`,
    jitter: (b) => `shake ${fmt(b.lo)}–${fmt(b.hi)}`,
    stop_time: (b) => `stops within ${fmt(b.lo)}–${fmt(b.hi)} s`,
    mean_mid: (b) => `middle strength ${fmt(b.lo, 1)}–${fmt(b.hi, 1)}`,
    pulses: (b) => `${fmt(b.lo, 0)}–${fmt(b.hi, 0)} pulses`,
  };
  const featText = (b) => (FEAT[b.feature] ? FEAT[b.feature](b) : `${b.feature} ${fmt(b.lo)}–${fmt(b.hi)}`);

  // A chart in pixel units: the viewBox matches the element, so text stays the same size on any screen.
  function chart(svg, xmax, ymax, opts) {
    opts = opts || {};
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    const r = svg.getBoundingClientRect();
    const w = Math.max(320, r.width), h = Math.max(200, r.height);
    svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
    const rem = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
    const L = rem * 3.2, R = rem * (opts.right || 1.2), T = rem * (opts.top || 1.2), B = rem * 2.6;
    const X = (t) => L + (t / xmax) * (w - L - R);
    const Y = (v) => h - B - (v / ymax) * (h - T - B);
    const g = el("g", {}, svg);
    for (let v = 0; v <= ymax + 1e-9; v += opts.ystep || 1) {
      el("line", { x1: L, x2: w - R, y1: Y(v), y2: Y(v), class: v === 0 ? "axis" : "grid" }, g);
      const t = el("text", { x: L - rem * 0.6, y: Y(v) + rem * 0.3, "text-anchor": "end" }, g); t.textContent = String(v);
    }
    const xs = opts.xstep || 0.2;
    for (let t = 0; t <= xmax + 1e-9; t += xs) {
      const tx = el("text", { x: X(t), y: h - B + rem * 1.3, "text-anchor": "middle" }, g); tx.textContent = (Math.round(t * 10) / 10) + " s";
    }
    const yl = el("text", { x: L, y: T - rem * 0.2 }, g); yl.textContent = opts.ylabel || "pressure";
    return { svg, g, X, Y, w, h, L, R, T, B, rem };
  }
  function pathD(profile, fs, c) {
    let d = "";
    for (let i = 0; i < profile.length; i++) d += (i ? "L" : "M") + c.X(i / fs).toFixed(1) + "," + c.Y(Math.max(0, profile[i])).toFixed(1);
    return d;
  }
  function curve(c, profile, fs, color, cls, delay, dur) {
    const p = el("path", { d: pathD(profile || [], fs || 100, c), class: "curve " + (cls || ""), stroke: color }, c.g);
    if (!reduce && dur !== 0) {
      const len = p.getTotalLength ? p.getTotalLength() : 1000;
      p.style.strokeDasharray = cls && cls.includes("try") ? "" : len + " " + len;
      if (!(cls && cls.includes("try"))) {
        p.style.strokeDashoffset = len;
        p.getBoundingClientRect();
        p.style.transition = `stroke-dashoffset ${dur || 900}ms cubic-bezier(.4,.1,.2,1) ${delay || 0}ms`;
        requestAnimationFrame(() => { p.style.strokeDashoffset = 0; });
      } else {
        p.style.opacity = 0; p.style.transition = `opacity 500ms ease ${delay || 0}ms`;
        requestAnimationFrame(() => { p.style.opacity = 1; });
      }
    }
    return p;
  }
  function envelope(c, env, color) {
    if (!env || !env.mean) return null;
    const n = env.mean.length, dur = env.duration || 1;
    let d = "";
    for (let i = 0; i < n; i++) d += (i ? "L" : "M") + c.X((i / (n - 1)) * dur).toFixed(1) + "," + c.Y(env.hi[i]).toFixed(1);
    for (let i = n - 1; i >= 0; i--) d += "L" + c.X((i / (n - 1)) * dur).toFixed(1) + "," + c.Y(env.lo[i]).toFixed(1);
    return el("path", { d: d + "Z", class: "env", fill: color }, c.g);
  }
  function label(c, x, y, a, b, anchor) {
    const t = el("text", { x, y, class: "lab", "text-anchor": anchor || "start" }, c.g); t.textContent = a;
    if (b) { const t2 = el("text", { x, y: y + c.rem * 1.2, class: "lab2", "text-anchor": anchor || "start" }, c.g); t2.textContent = b; }
  }
  const dur = (p, fs) => (p && p.length ? (p.length - 1) / (fs || 100) : 0);
  const peakIndex = (p) => p.reduce((bi, v, i) => (v > p[bi] ? i : bi), 0);
  function riseIndex(p) { const pk = Math.max(...p); const i = p.findIndex((v) => v >= 0.8 * pk); return i < 0 ? peakIndex(p) : i; }

  // ---------------- tabs and navigation ----------------
  const tabs = $("tabs");
  scenes.forEach((s, i) => {
    if (s.dataset.appx && !tabs.querySelector(".gap")) { const g = document.createElement("span"); g.className = "gap"; tabs.appendChild(g); }
    const b = document.createElement("button");
    b.type = "button"; b.textContent = s.dataset.tab; b.dataset.i = i;
    if (s.dataset.appx) b.className = "appx";
    b.addEventListener("click", () => { go(i, 99); b.blur(); });
    tabs.appendChild(b);
  });

  function steps(s) {
    const fr = Array.from(s.querySelectorAll(".f"));
    let auto = 0;
    const vals = fr.map((f) => (f.dataset.f ? Number(f.dataset.f) : ++auto));
    return { fr, vals, uniq: Array.from(new Set(vals)).sort((a, b) => a - b) };
  }
  function applyFrags() {
    const s = scenes[cur], st = steps(s);
    const shownUpTo = frag >= st.uniq.length ? Infinity : (frag ? st.uniq[frag - 1] : 0);
    st.fr.forEach((f, i) => f.classList.toggle("on", st.vals[i] <= shownUpTo));
  }
  function go(i, f) {
    i = Math.max(0, Math.min(scenes.length - 1, i));
    const changed = i !== cur || !scenes[i].classList.contains("on");
    scenes.forEach((s, j) => s.classList.toggle("on", j === i));
    cur = i;
    const s = scenes[i], st = steps(s);
    frag = f === 99 ? st.uniq.length : (f || 0);
    document.body.classList.toggle("on-ink", s.classList.contains("ink"));
    document.body.classList.toggle("on-paper", s.classList.contains("paper"));
    tabs.querySelectorAll("button").forEach((b) => b.setAttribute("aria-current", String(Number(b.dataset.i) === i)));
    $("count").textContent = `${i + 1} / ${scenes.length}`;
    if (location.hash.slice(1) !== s.dataset.id) history.replaceState(null, "", "#" + s.dataset.id);
    applyFrags();
    if (changed) enter(s.dataset.id);
    if (frag) for (let k = 1; k <= frag; k++) onFrag(s.dataset.id, k, true);
  }
  function next() {
    const s = scenes[cur], st = steps(s);
    if (frag < st.uniq.length) { frag++; applyFrags(); onFrag(s.dataset.id, frag); }
    else if (cur < scenes.length - 1) go(cur + 1);
  }
  function prev() { if (cur > 0) go(cur - 1, 99); }

  document.addEventListener("keydown", (e) => {
    const tag = (e.target && e.target.tagName) || "";
    if (tag === "INPUT" || tag === "TEXTAREA") return;
    if (tag === "BUTTON" && (e.key === " " || e.key === "Enter")) return;
    const k = e.key;
    if (k === "ArrowRight" || k === "PageDown" || k === " " || k === "Enter") { e.preventDefault(); next(); }
    else if (k === "ArrowLeft" || k === "PageUp" || k === "Backspace") { e.preventDefault(); prev(); }
    else if (k === "Home") go(0);
    else if (k === "End") go(ids.indexOf("close"));
    else if (k === "l" || k === "L") go(ids.indexOf("live"), 99);
    else if (k === "g" || k === "G") go(ids.indexOf("graph"), 99);
    else if (k === "a" || k === "A") go(ids.indexOf("ai"), 99);
    else if (k === "r" || k === "R") load(true);
    else if (k === "f" || k === "F") { if (!document.fullscreenElement) document.documentElement.requestFullscreen && document.documentElement.requestFullscreen(); else document.exitFullscreen && document.exitFullscreen(); }
    else if (k === "?") $("help").hidden = !$("help").hidden;
    else if (k === "Escape") { $("help").hidden = true; closeClip(); }
    else if (/^[1-9]$/.test(k)) go(Number(k) - 1, 99);
    else if (k === "0") go(9, 99);
  });

  // ---------------- data ----------------
  async function load(rerender) {
    try { D = await K.api("/api/pitch"); } catch (e) { console.error(e); return; }
    fill();
    if (rerender) enter(ids[cur], true);
  }
  function scheduleReload() { clearTimeout(reloadTimer); reloadTimer = setTimeout(() => load(false), 1200); }

  function fill() {
    if (!D) return;
    const r = D.results || {}, cmp = D.compare || {}, word = (D.featured && D.featured.word) || "グッ";
    // proof
    const who = cmp.who;
    const nM = (cmp.masters || []).length;
    document.querySelector('[data-id="proof"] h2').textContent = `Same word, ${numWord(nM)} hands`;
    if (who && who.n) {
      $("who-num").textContent = `${who.correct} of ${who.n}`;
      $("who-text").textContent = `times KOTSU named whose ${word} it was. Guessing: 1 in ${Math.round(1 / who.chance)}.`;
    }
    if (who && who.n) { $("ai-who").textContent = `${who.correct} of ${who.n}`; $("ai-who-text").textContent = `presses named to the right master, with each one hidden first. Guessing: 1 in ${Math.round(1 / who.chance)}.`; }
    const rec = r.recognition;
    if (rec && rec.n) $("rec-num").textContent = `${rec.correct} of ${rec.n}`;
    const counts = r.counts || {};
    $("hands-foot").textContent = `Our captures, 25 Oct 2026: ${counts.lessons || 0} lessons from ${counts.masters || nM} people in the master’s role. Each press is hidden from KOTSU before it guesses.`;
    // coaching replay
    const rp = D.replay;
    if (rp) {
      $("replay-title").textContent = `This morning: a learner on ${rp.master}’s ${rp.word}`;
      const box = $("tries"); box.innerHTML = "";
      rp.attempts.forEach((a, i) => {
        const row = document.createElement("div");
        row.className = "try-row f"; row.dataset.f = String(i + 1);
        row.innerHTML = `<span class="n">Try ${i + 1}</span><span class="s">${fmt(a.score, a.score % 1 ? 1 : 0)}</span><span class="t">${a.passed ? "inside the master’s range" : esc(a.note || "outside the range")}</span>`;
        box.appendChild(row);
      });
    }
    const pp = D.passport;
    if (pp) {
      const d = new Date((pp.t || 0) * 1000);
      $("passport-dl").innerHTML = `<dt>Learner</dt><dd>${esc(pp.learner)}</dd><dt>Word</dt><dd lang="ja">${esc(pp.word)}</dd><dt>Taught by</dt><dd>${esc(pp.master)}</dd><dt>Score</dt><dd>${fmt(pp.score, 1)}, three presses, no help</dd><dt>Check code</dt><dd>${esc(pp.verify || "")}</dd><dt>Date</dt><dd>${d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })}</dd>`;
    }
    const pk = r.pilot && r.pilot.kotsu;
    $("pilot-line").textContent = pk && pk.sessions ? `This morning, ${pk.reached} of ${pk.sessions} coached sessions reached the master’s range, in ${fmt(pk.mean_tries, 1)} tries on average.` : "";
    // lesson
    const L = D.lesson;
    if (L) {
      $("lesson-text").textContent = L.text || "ちがう、もっとグッと";
      $("lesson-meaning").innerHTML = `In ${esc(L.master)}’s hands, <span class="jp" lang="ja">${esc(L.word)}</span> means: ${esc(L.meaning && L.meaning.en)}.`;
      $("lesson-chips").innerHTML = (L.bands || []).slice(0, 3).map((b) => `<span class="chip">${esc(featText(b))}</span>`).join("");
    }
    // field clip
    const clip = (D.media || []).find((n) => /\.(mp4|mov|m4v|webm)$/i.test(n));
    $("clip-btn").hidden = !clip;
    if (clip) $("clip-video").src = "/media/" + encodeURIComponent(clip);
  }

  // ---------------- scene entry ----------------
  function enter(id) {
    if (!D && id !== "arch" && id !== "graph") return;
    if (id === "learns") drawLesson(0);
    if (id === "proof") drawProof();
    if (id === "coaching") drawReplay(0);
    if (id === "live") enterLive();
    if (id === "graph") $("graph-frame").src = "/?embed=graph&t=" + Date.now();
    if (id === "arch") drawArch();
  }
  function onFrag(id, k, silent) {
    if (id === "learns") drawLesson(k, silent);
    if (id === "coaching") drawReplay(k, silent);
  }

  // ---------------- How it learns ----------------
  let lessonC = null;
  function drawLesson(k, silent) {
    const L = D && D.lesson; if (!L) return;
    const svg = $("lesson-chart");
    const stepDivs = document.querySelectorAll("#lesson-steps div");
    stepDivs.forEach((d) => d.classList.toggle("now", (k <= 2 && d.dataset.s === "1") || (k >= 3 && d.dataset.s === "3")));
    if (k === 0 || !lessonC) {
      const xmax = Math.max(0.8, Math.ceil(Math.max(dur(L.try.profile, L.try.fs), dur(L.demo.profile, L.demo.fs)) * 5) / 5);
      lessonC = chart(svg, xmax, 3);
      lessonC.drawn = {};
    }
    const c = lessonC, col = masterColor(L.master_id);
    if (k >= 1 && !c.drawn.try) {
      c.drawn.try = true;
      curve(c, L.try.profile, L.try.fs, MUTED, "try", 0, silent ? 0 : 600);
      const i = peakIndex(L.try.profile);
      label(c, c.X(i / L.try.fs) + c.rem * 0.4, c.Y(L.try.profile[i]) - c.rem * 0.8, "Learner’s press", "", "start");
    }
    if (k >= 3 && !c.drawn.demo) {
      c.drawn.demo = true;
      const rise = (L.bands || []).find((b) => b.feature === "rise");
      const peak = (L.bands || []).find((b) => b.feature === "peak");
      if (rise) el("rect", { x: c.X(rise.lo), y: c.T, width: Math.max(2, c.X(rise.hi) - c.X(rise.lo)), height: c.h - c.T - c.B, class: "band" }, c.g);
      if (peak) el("rect", { x: c.L, y: c.Y(peak.hi), width: c.w - c.L - c.R, height: Math.max(2, c.Y(peak.lo) - c.Y(peak.hi)), class: "band" }, c.g);
      curve(c, L.demo.profile, L.demo.fs, col, "", 0, silent ? 0 : 1100);
      const i = peakIndex(L.demo.profile);
      label(c, c.X(i / L.demo.fs), c.Y(L.demo.profile[i]) - c.rem * 0.8, `${L.master}’s demo`, "", "end");
      if (rise) { const t = el("text", { x: c.X((rise.lo + rise.hi) / 2), y: c.h - c.B - c.rem * 0.6, "text-anchor": "middle", class: "lab2" }, c.g); t.textContent = "his range"; }
    }
  }

  // ---------------- Proof ----------------
  function drawProof() {
    const cmp = D.compare; if (!cmp || !cmp.masters) return;
    const ms = cmp.masters.filter((m) => m.prototype && m.prototype.length > 2);
    const xmax = Math.max(0.8, Math.ceil(Math.max(...ms.map((m) => dur(m.prototype, m.fs))) * 5) / 5);
    const c = chart($("hands-chart"), xmax, 3, { top: 4.6 });
    const order = ms.slice().sort((a, b) => riseIndex(a.prototype) / a.fs - riseIndex(b.prototype) / b.fs);
    const slots = order.length;
    order.forEach((m, i) => {
      const col = masterColor(m.master_id);
      envelope(c, m.envelope, col);
      curve(c, m.prototype, m.fs, col, "", i * 650, 900);
      const ri = riseIndex(m.prototype);
      const dot = el("circle", { cx: c.X(ri / m.fs), cy: c.Y(m.prototype[ri]), r: c.rem * 0.42, fill: col, class: "dot" }, c.g);
      if (!reduce) { dot.style.opacity = 0; dot.style.transition = `opacity 300ms ease ${i * 650 + 700}ms`; requestAnimationFrame(() => { dot.style.opacity = 1; }); }
      const rise = (m.bands || []).find((b) => b.feature === "rise");
      const x = c.L + (i + 0.02) * ((c.w - c.L - c.R) / slots);
      const g = el("g", {}, c.g);
      el("rect", { x: x - c.rem * 0.55, y: c.rem * 0.4, width: c.rem * 0.28, height: c.rem * 2.6, fill: col, rx: c.rem * 0.14 }, g);
      const t1 = el("text", { x, y: c.rem * 1.4, class: "lab" }, g); t1.textContent = m.master;
      const t2 = el("text", { x, y: c.rem * 2.75, class: "lab2" }, g); t2.textContent = rise ? `full strength in ${fmt(rise.lo)}–${fmt(rise.hi)} s` : (m.meaning && m.meaning.en) || "";
      if (!reduce) { g.style.opacity = 0; g.style.transition = `opacity 400ms ease ${i * 650 + 400}ms`; requestAnimationFrame(() => { g.style.opacity = 1; }); }
    });
  }

  // ---------------- Coaching replay ----------------
  let replayC = null;
  function drawReplay(k, silent) {
    const rp = D && D.replay; if (!rp) return;
    const nTries = rp.attempts.length;
    if (k === 0 || !replayC) {
      const all = [dur(rp.target.profile, rp.target.fs)].concat(rp.attempts.map((a) => (a.stroke ? dur(a.stroke.profile, a.stroke.fs) : 0)));
      if (rp.target.envelope && rp.target.envelope.duration) all.push(rp.target.envelope.duration);
      replayC = chart($("replay-chart"), Math.max(0.8, Math.ceil(Math.max(...all) * 5) / 5), 3);
      const col = masterColor(rp.master_id);
      envelope(replayC, rp.target.envelope, col);
      curve(replayC, rp.target.profile, rp.target.fs, col, "faint", 0, 0);
      const i = peakIndex(rp.target.profile);
      label(replayC, replayC.X(i / rp.target.fs), replayC.Y(rp.target.profile[i]) - replayC.rem * 0.8, `${rp.master}’s グッ, his range shaded`, "", "end");
      replayC.drawn = 0;
    }
    const c = replayC;
    while (c.drawn < Math.min(k, nTries)) {
      const a = rp.attempts[c.drawn];
      if (a && a.stroke) {
        c.g.querySelectorAll(".attempt").forEach((p) => { p.style.opacity = 0.35; });
        const p = curve(c, a.stroke.profile, a.stroke.fs, a.passed ? "#1E7F4F" : INK, a.passed ? "attempt" : "attempt try", 0, silent ? 0 : 800);
        p.classList.add("attempt");
      }
      c.drawn++;
    }
    if (k > nTries && !silent) K.chime && K.chime(true);
  }

  // ---------------- Live: your グッ ----------------
  let liveC = null;
  function enterLive() {
    K.api("/api/session", { mode: "pitch" }).catch(() => {});
    judgeSids = [];
    $("btn-keep").disabled = true; $("keep-note").textContent = "";
    resetLiveChart();
    $("verdict-h").innerHTML = `Whose <span class="jp" lang="ja">${esc((D && D.featured && D.featured.word) || "グッ")}</span> is yours closest to?`;
    $("verdict-h").classList.add("muted");
    $("verdict-bars").innerHTML = ""; $("verdict-feats").textContent = "";
  }
  function resetLiveChart(highlight) {
    const cmp = D && D.compare;
    const ms = (cmp && cmp.masters || []).filter((m) => m.prototype && m.prototype.length > 2);
    liveC = chart($("live-chart"), 1.2, 3.2, { ystep: 1 });
    ms.forEach((m) => {
      const col = masterColor(m.master_id);
      const p = curve(liveC, m.prototype, m.fs, col, highlight === m.master_id ? "" : "faint", 0, 0);
      if (highlight === m.master_id) p.style.strokeWidth = 3.6;
      const n = m.prototype.length - 1;
      const pi = peakIndex(m.prototype);
      const t = el("text", { x: liveC.X(pi / m.fs), y: liveC.Y(m.prototype[pi]) - liveC.rem * 0.5, "text-anchor": "middle", class: "lab2", fill: col }, liveC.g);
      t.textContent = m.master; t.style.fill = col; if (highlight && highlight !== m.master_id) t.style.opacity = 0.45;
    });
    liveC.livePath = el("path", { d: "", class: "curve", stroke: INK }, liveC.g);
    liveC.livePath.style.strokeWidth = 4.2;
    live = { t0: 0, pts: [] };
  }
  function onLive(v) {
    const gu = $("gu");
    const x = Math.max(0, Math.min(3.2, Number(v) || 0));
    gu.style.transform = `scale(${1 + 0.05 * x}, ${1 - 0.075 * x})`;
    gu.classList.toggle("hot", x > 1.9);
    if (!liveC || !live) return;
    const now = performance.now();
    if (!live.t0 || now - (live.last || 0) > 600) { live.t0 = now; live.pts = []; }
    live.last = now;
    live.pts.push([(now - live.t0) / 1000, x]);
    liveC.livePath.setAttribute("d", live.pts.map((p, i) => (i ? "L" : "M") + liveC.X(Math.min(p[0], 1.2)).toFixed(1) + "," + liveC.Y(p[1]).toFixed(1)).join(""));
  }
  async function onStroke(st) {
    if (ids[cur] !== "live" || !st || st.stype !== "force") return;
    $("gu").style.transform = ""; $("gu").classList.remove("hot");
    $("live-src").textContent = "Press received. Press again any time.";
    let w;
    try { w = await K.api("/api/pitch/whose", { sid: st.id }); } catch (e) { $("live-src").textContent = e.message; return; }
    judgeSids.push(st.id);
    $("btn-keep").disabled = false;
    const best = w.closest;
    resetLiveChart(best && best.master_id);
    const prof = (w.stroke && w.stroke.profile) || st.profile || [];
    const p = curve(liveC, prof, (w.stroke && w.stroke.fs) || st.fs || 100, INK, "", 0, 500);
    p.style.strokeWidth = 4.2;
    if (!best) { $("verdict-h").textContent = "No master has taught this word yet."; return; }
    $("verdict-h").classList.remove("muted");
    $("verdict-h").innerHTML = `Closest to ${esc(best.master)}’s <span class="jp" lang="ja">${esc(w.word)}</span>`;
    $("verdict-bars").innerHTML = w.ranking.map((r) => `
      <span class="name" style="color:${masterLight(r.master_id)}">${esc(r.master)}</span>
      <span class="track"><span class="fill" style="background:${masterLight(r.master_id)}" data-w="${Math.max(2, r.score)}"></span></span>
      <span class="num">${fmt(r.score, 0)}</span>
      ${r.note ? `<span class="note">For ${esc(r.master)}: ${esc(r.note)}</span>` : `<span class="note">Inside ${esc(r.master)}’s range</span>`}`).join("");
    requestAnimationFrame(() => document.querySelectorAll("#verdict-bars .fill").forEach((f) => { f.style.width = f.dataset.w + "%"; }));
    const f = w.features || {};
    $("verdict-feats").textContent = `Your press: full strength in ${fmt(f.rise)} s, peak ${fmt(f.peak, 1)}, held ${fmt(f.hold)} s.`;
  }
  $("btn-keep").addEventListener("click", async () => {
    if (!judgeSids.length) return;
    $("btn-keep").disabled = true;
    try {
      const r = await K.api("/api/pitch/judge", { sids: judgeSids });
      $("keep-note").textContent = `Kept: “Judge” now has ${r.word.n_pos} presses of ${r.word.word}. See the knack graph.`;
      judgeSids = [];
      await load(false);
    } catch (e) { $("keep-note").textContent = e.message; $("btn-keep").disabled = false; }
  });

  // Safari on a Force Touch MacBook: the Live tab itself is a pressure station.
  (function localForce() {
    const pad = $("press-col");
    const hasForce = "WEBKIT_FORCE_AT_MOUSE_DOWN" in MouseEvent || "webkitForce" in MouseEvent.prototype;
    if (!hasForce) return;
    $("live-src").textContent = "Press on this trackpad, or on the pressure station.";
    let pressing = false, force = 0, rec = null, raf = 0, lastLive = 0;
    pad.addEventListener("webkitmouseforcewillbegin", (e) => e.preventDefault());
    pad.addEventListener("webkitmouseforcedown", (e) => e.preventDefault());
    pad.addEventListener("webkitmouseforcechanged", (e) => { force = e.webkitForce || 0; });
    pad.addEventListener("pointerdown", (e) => {
      if (ids[cur] !== "live") return;
      e.preventDefault(); pressing = true; rec = { t: [], f: [] }; force = e.webkitForce || 1;
      const loop = (now) => {
        if (!pressing) return;
        rec.t.push(now); rec.f.push(force);
        if (now - lastLive > 40) { sock.send({ type: "live", src: "force-pitch", v: Math.round(force * 100) / 100 }); lastLive = now; }
        raf = requestAnimationFrame(loop);
      };
      raf = requestAnimationFrame(loop);
    });
    const up = () => {
      if (!pressing) return; pressing = false; cancelAnimationFrame(raf);
      rec.t.push(performance.now()); rec.f.push(0);
      if (rec.t.length > 4) sock.send({ type: "stroke", src: "force-pitch", stype: "force", t: rec.t, f: rec.f });
    };
    pad.addEventListener("pointerup", up); pad.addEventListener("pointercancel", up); window.addEventListener("blur", up);
  })();

  // ---------------- Architecture ----------------
  let archDrawn = false;
  function drawArch() {
    if (archDrawn) return; archDrawn = true;
    const s = $("arch");
    while (s.firstChild) s.removeChild(s.firstChild);
    const defs = el("defs", {}, s);
    const mk = el("marker", { id: "ah", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse" }, defs);
    el("path", { d: "M0,0 L10,5 L0,10 z", fill: INK }, mk);
    const box = (x, y, w, h, t1, t2, tag, tagCls) => {
      el("rect", { x, y, width: w, height: h, rx: 8, class: "box" }, s);
      const a = el("text", { x: x + 18, y: y + 34, class: "t1" }, s); a.textContent = t1;
      (Array.isArray(t2) ? t2 : [t2]).forEach((line, i) => { const b = el("text", { x: x + 18, y: y + 60 + i * 21, class: "t2" }, s); b.textContent = line; });
      if (tag) { const c = el("text", { x: x + w - 16, y: y + 34, class: "t3 " + tagCls, "text-anchor": "end" }, s); c.textContent = tag; }
    };
    const arrow = (x1, y1, x2, y2) => { const mx = (x1 + x2) / 2; el("path", { d: `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`, class: "arrow" }, s); };
    // inputs
    const IN = [["Trackpad pressure", ["Safari Force Touch, 0–3"]], ["Phone motion", ["any phone on the tool"]], ["Touch and drag", ["screen or trackpad path"]], ["The master’s voice", ["Japanese, through Chrome"]]];
    IN.forEach((b, i) => box(20, 40 + i * 132, 300, 108, b[0], b[1]));
    // core
    el("rect", { x: 400, y: 14, width: 640, height: 560, rx: 12, class: "core" }, s);
    const cl = el("text", { x: 420, y: 44, class: "core-label" }, s); cl.textContent = "KOTSU server on one laptop";
    const CORE = [
      ["Hearing", ["speech to text, then the craft word and", "the intent: correction, demo, approval"], "pre-trained AI + rules", "ai"],
      ["Measuring", ["13 numbers per stroke: peak, rise,", "hold, evenness, stop, shake"], "signal processing", "rules"],
      ["Lesson builder", ["the try he corrected, his word, his demo,", "matched in time"], "rules", "rules"],
      ["One model per master and word", ["his ranges, from his demos against the", "tries he corrected; はい/いいえ sharpen them"], "learned from him", "learned"],
    ];
    CORE.forEach((b, i) => box(424, 62 + i * 126, 592, 108, b[0], b[1], b[2], b[3]));
    // outputs
    const OUT = [["Dictionary of the hands", ["each master’s words, measured"]], ["Coach", ["his word + one line of feedback;", "help fades as the learner improves"]], ["Test and skill passport", ["three presses with no help"]], ["Knack graph", ["who taught what, who learned it"]]];
    OUT.forEach((b, i) => box(1120, 40 + i * 132, 460, 108, b[0], b[1]));
    // arrows: sensors to measuring, voice to hearing; core to outputs
    arrow(320, 94, 424, 228); arrow(320, 226, 424, 242); arrow(320, 358, 424, 256); arrow(320, 490, 424, 116);
    [94, 226, 358, 490].forEach((y) => arrow(1040, y, 1120, y));
    [170, 296, 422].forEach((y) => el("path", { d: `M720,${y} L720,${y + 17}`, class: "arrow" }, s));
    // legend + storage
    const fy = 620;
    const lg = [["pre-trained AI: only the speech-to-text", "ai"], ["learned from the master: his word models", "learned"], ["rules and signal processing: everything else", "rules"]];
    lg.forEach((l, i) => { el("circle", { cx: 30 + i * 520, cy: fy - 6, r: 7, class: l[1] }, s); const t = el("text", { x: 46 + i * 520, y: fy, class: "foot" }, s); t.textContent = l[0]; });
    const st = el("text", { x: 20, y: fy + 46, class: "foot" }, s); st.textContent = "No camera. No chatbot. Data stays in one file on the employer’s laptop, and works offline except for speech.";
  }

  // ---------------- field clip ----------------
  function closeClip() { const w = $("clip-wrap"); if (w.classList.contains("on")) { w.classList.remove("on"); $("clip-video").pause(); } }
  $("clip-btn").addEventListener("click", () => { $("clip-wrap").classList.add("on"); $("clip-video").play().catch(() => {}); });
  $("clip-wrap").addEventListener("click", (e) => { if (e.target.id === "clip-wrap") closeClip(); });

  // ---------------- live connection ----------------
  const sock = K.connect({ src: "pitch", role: "view" }, (ev) => {
    if (ev.type === "live") onLive(ev.v);
    else if (ev.type === "stroke") onStroke(ev.stroke);
    else if (ev.type === "word" || ev.type === "lesson" || ev.type === "reset") scheduleReload();
  }, (ok) => { $("live-dot").classList.toggle("ok", ok); });

  window.addEventListener("resize", () => { clearTimeout(window.__rz); window.__rz = setTimeout(() => { archDrawn = false; enter(ids[cur]); if (frag) for (let k = 1; k <= frag; k++) onFrag(ids[cur], k, true); }, 250); });

  const start = Math.max(0, ids.indexOf(location.hash.slice(1)));
  load(false).then(() => go(start, start ? 99 : 0));
})();
