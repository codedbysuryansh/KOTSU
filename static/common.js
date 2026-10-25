// Shared helpers for every KOTSU page: API calls, the event socket, plots and sound.
(function () {
  const K = {};

  K.api = async function (path, body, method) {
    const opt = { method: method || (body !== undefined ? "POST" : "GET"), headers: {} };
    if (body instanceof FormData) opt.body = body;
    else if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
    const r = await fetch(path, opt);
    let data = null;
    try { data = await r.json(); } catch (e) { data = null; }
    if (!r.ok) { const msg = (data && (data.detail || data.message)) || ("HTTP " + r.status); throw new Error(msg); }
    return data;
  };

  K.connect = function (params, onMessage, onStatus) {
    let ws = null, closed = false, retry = 500;
    const qs = new URLSearchParams(params || {}).toString();
    const url = (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws" + (qs ? "?" + qs : "");
    const api = { send: (m) => { if (ws && ws.readyState === 1) { ws.send(JSON.stringify(m)); return true; } return false; }, close: () => { closed = true; if (ws) ws.close(); }, get ready() { return ws && ws.readyState === 1; } };
    function open() {
      ws = new WebSocket(url);
      ws.onopen = () => { retry = 500; onStatus && onStatus(true); };
      ws.onmessage = (e) => { try { onMessage(JSON.parse(e.data)); } catch (err) { console.error(err); } };
      ws.onclose = () => { onStatus && onStatus(false); if (!closed) setTimeout(open, retry = Math.min(retry * 1.6, 5000)); };
      ws.onerror = () => { try { ws.close(); } catch (e) {} };
    }
    open();
    return api;
  };

  K.toast = function (msg, ms) {
    const t = document.createElement("div");
    t.className = "toast"; t.textContent = msg; document.body.appendChild(t);
    setTimeout(() => t.remove(), ms || 2600);
  };

  // ---------- plots ----------
  // series: [{y: [...], fs: 100, color, width, dash, label}] ; opts: {envelope, ymax, xmax, title, unit}
  K.plot = function (canvas, series, opts) {
    opts = opts || {};
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth || 600, h = canvas.clientHeight || 240;
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) { canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr); }
    const c = canvas.getContext("2d");
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    c.clearRect(0, 0, w, h);
    const L = 44, R = 10, T = 12, B = 26;
    const valid = (series || []).filter((s) => s && s.y && s.y.length > 1);
    let xmax = opts.xmax || 0, ymax = opts.ymax || 0;
    valid.forEach((s) => { xmax = Math.max(xmax, (s.y.length - 1) / (s.fs || 100)); s.y.forEach((v) => { if (v > ymax) ymax = v; }); });
    const envs = (opts.envelopes || []).filter((e) => e && e.mean && e.mean.length > 1);
    if (opts.envelope) envs.unshift(Object.assign({ fill: "rgba(36,66,122,0.13)" }, opts.envelope));
    envs.forEach((e) => { xmax = Math.max(xmax, e.duration || 0); e.hi.forEach((v) => { if (v > ymax) ymax = v; }); });
    if (!xmax) xmax = 1; if (!ymax) ymax = 1; ymax *= 1.08;
    const X = (t) => L + (t / xmax) * (w - L - R);
    const Y = (v) => h - B - (v / ymax) * (h - T - B);
    // grid
    c.strokeStyle = "#E3E6E0"; c.lineWidth = 1; c.font = "11px " + getComputedStyle(document.body).fontFamily; c.fillStyle = "#78808C";
    for (let i = 0; i <= 4; i++) { const v = (ymax / 1.08) * i / 4; const y = Y(v); c.beginPath(); c.moveTo(L, y); c.lineTo(w - R, y); c.stroke(); c.fillText(fmt(v), 4, y + 4); }
    const step = niceStep(xmax);
    for (let t = 0; t <= xmax + 1e-9; t += step) { const x = X(t); c.beginPath(); c.moveTo(x, T); c.lineTo(x, h - B); c.stroke(); c.fillText(t.toFixed(step < 1 ? 1 : 0) + " s", x - 10, h - 8); }
    // envelopes (each master's own variability)
    envs.forEach((e) => {
      const n = e.mean.length, d = e.duration || xmax;
      c.beginPath();
      for (let i = 0; i < n; i++) { const x = X((i / (n - 1)) * d), y = Y(e.hi[i]); i ? c.lineTo(x, y) : c.moveTo(x, y); }
      for (let i = n - 1; i >= 0; i--) { c.lineTo(X((i / (n - 1)) * d), Y(e.lo[i])); }
      c.closePath(); c.fillStyle = e.fill || "rgba(36,66,122,0.13)"; c.fill();
    });
    valid.forEach((s) => {
      c.beginPath(); c.lineWidth = s.width || 2.5; c.strokeStyle = s.color || "#24427A"; c.setLineDash(s.dash || []);
      const fs = s.fs || 100;
      s.y.forEach((v, i) => { const x = X(i / fs), y = Y(v); i ? c.lineTo(x, y) : c.moveTo(x, y); });
      c.stroke(); c.setLineDash([]);
    });
    // legend
    let lx = L + 8;
    valid.concat(opts.envelope ? [{ color: "rgba(36,66,122,0.35)", label: "master's range" }] : []).forEach((s) => {
      if (!s.label) return; c.fillStyle = s.color || "#24427A"; c.fillRect(lx, T + 2, 14, 4); c.fillStyle = "#4A5260"; c.fillText(s.label, lx + 18, T + 8); lx += c.measureText(s.label).width + 40;
    });
    if (opts.unit) { c.fillStyle = "#78808C"; c.fillText(opts.unit, w - R - c.measureText(opts.unit).width, T + 8); }
  };
  function niceStep(x) { if (x <= 1.2) return 0.2; if (x <= 3) return 0.5; if (x <= 8) return 1; return 2; }
  function fmt(v) { if (v >= 100) return Math.round(v).toString(); if (v >= 10) return v.toFixed(0); if (v >= 1) return v.toFixed(1); return v.toFixed(2); }

  // ---------- sound: hear a stroke ----------
  let ac = null;
  K.audio = function () { if (!ac) ac = new (window.AudioContext || window.webkitAudioContext)(); if (ac.state === "suspended") ac.resume(); return ac; };
  K.sonify = function (y, fs, when) {
    if (!y || y.length < 2) return 0;
    const a = K.audio();
    const t0 = a.currentTime + 0.05 + (when || 0);
    const dur = (y.length - 1) / (fs || 100);
    let max = 0; y.forEach((v) => { if (v > max) max = v; }); if (!max) max = 1;
    const o = a.createOscillator(), g = a.createGain();
    o.type = "triangle"; o.connect(g); g.connect(a.destination);
    const step = Math.max(1, Math.floor(y.length / 200));
    g.gain.setValueAtTime(0, t0);
    for (let i = 0; i < y.length; i += step) {
      const n = y[i] / max, t = t0 + i / (fs || 100);
      o.frequency.linearRampToValueAtTime(180 + 620 * n, t);
      g.gain.linearRampToValueAtTime(0.22 * Math.pow(n, 0.7), t);
    }
    g.gain.linearRampToValueAtTime(0, t0 + dur + 0.05);
    o.start(t0); o.stop(t0 + dur + 0.1);
    return dur + 0.15;
  };

  // a short two-note chime when a stroke lands inside the master's range
  K.chime = function (ok) {
    try {
      const a = K.audio(), t0 = a.currentTime + 0.02;
      (ok === false ? [330, 262] : [784, 1175]).forEach((f, i) => {
        const o = a.createOscillator(), g = a.createGain();
        o.type = "sine"; o.frequency.value = f; o.connect(g); g.connect(a.destination);
        const t = t0 + i * 0.12;
        g.gain.setValueAtTime(0, t); g.gain.linearRampToValueAtTime(0.25, t + 0.01); g.gain.exponentialRampToValueAtTime(0.001, t + 0.45);
        o.start(t); o.stop(t + 0.5);
      });
    } catch (e) {}
  };

  K.say = function (text, lang) {
    if (!("speechSynthesis" in window)) return false;
    const u = new SpeechSynthesisUtterance(text);
    u.lang = lang || "ja-JP"; u.rate = 0.9;
    const v = speechSynthesis.getVoices().find((x) => x.lang && x.lang.toLowerCase().startsWith((lang || "ja-JP").slice(0, 2)));
    if (v) u.voice = v;
    speechSynthesis.cancel(); speechSynthesis.speak(u);
    return true;
  };

  K.playClip = function (word) {
    if (word && word.clip) { const a = new Audio("/clips/" + word.clip); a.play().catch(() => K.say(word.word + "と")); return "voice"; }
    if (word) { K.say(word.word + "と"); return "synth"; }
  };

  K.esc = function (s) { return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); };

  K.fmtFeature = function (f, v) {
    if (v == null) return "–";
    if (["duration", "rise", "stop_time", "hold"].includes(f)) return v.toFixed(2) + " s";
    if (["steady_cv", "jitter", "sound_rough"].includes(f)) return v.toFixed(3);
    if (f === "pulses") return Math.round(v).toString();
    return v >= 100 ? Math.round(v).toString() : v.toFixed(2);
  };

  // A stroke pad for laptops: drag with the mouse or trackpad to make a stroke.
  K.dragPad = function (el, src, socket, onStroke) {
    let pts = null;
    el.addEventListener("pointerdown", (e) => { el.setPointerCapture(e.pointerId); pts = { t: [], x: [], y: [], p: [] }; add(e); });
    el.addEventListener("pointermove", (e) => { if (pts) add(e); });
    const end = () => {
      if (!pts) return;
      const s = pts; pts = null;
      if (s.t.length >= 4 && s.t[s.t.length - 1] - s.t[0] > 120) {
        const msg = { type: "stroke", src, stype: "drag", t: s.t, x: s.x, y: s.y, p: s.p };
        if (!socket.send(msg)) K.api("/api/stroke", msg).catch((e) => K.toast(e.message));
        onStroke && onStroke(s);
      }
    };
    el.addEventListener("pointerup", end); el.addEventListener("pointercancel", end);
    function add(e) {
      const evs = e.getCoalescedEvents ? e.getCoalescedEvents() : [e];
      (evs.length ? evs : [e]).forEach((ev) => { pts.t.push(ev.timeStamp); pts.x.push(ev.clientX); pts.y.push(ev.clientY); pts.p.push(ev.pressure || 0); });
    }
  };

  window.K = K;
})();
