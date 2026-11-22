/* GOFLIX player v4.
   Loaded after the main app script (plugins/route.py injects it into /app).
   It replaces the built-in player and the "watch" sheet flow:
   movie -> language (only if there is more than one) -> plays at once - inside the
   banner of the detail page (Hotstar-style, pinned while you scroll) with a fullscreen
   button, or fullscreen when started from a home row - with
   language / quality / speed / volume switching and VLC / MX Player inside
   the player. Downloads still use the original quality picker. */
(() => {
"use strict";
if (typeof openWatchSheet !== "function" || typeof buildStreamUrls !== "function" || typeof fetchQualities !== "function") {
  console.warn("[goflix-player] main app API not found - keeping the built-in player");
  return;
}
const legacyWatch = window.openWatchSheet;

/* ------------------------------------------------------------------ helpers */
const RANK = { SD: 0, "480p": 1, "720p": 2, "1080p": 3, "2K": 4, "4K": 5 };
const SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 2];
const POS_KEY = "goflix_resume_v2";
const RISKY_V = /(^|[^a-z0-9])(x265|hevc|h[ .]?265|10[ -]?bit|hdr10?|dovi|dolby[ .-]?vision)([^a-z0-9]|$)/i;
const RISKY_A = /(^|[^a-z0-9])(dts|truehd|atmos|e-?ac-?3|ac-?3|ddp|dd\+|dd[ .]?[257][ .]?[01])/i;
const $id = id => document.getElementById(id);
const clamp = (n, a, b) => Math.min(b, Math.max(a, n));
const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = s => {
  if (!isFinite(s) || s < 0) s = 0;
  s = Math.floor(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
  return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(x).padStart(2, "0");
};
const store = {
  get(k) { try { return JSON.parse(localStorage.getItem(k) || "null"); } catch (e) { return null; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} },
};
const risky = f => RISKY_V.test(f.name || "") || RISKY_A.test(f.name || "");
const inLang = (f, lang) => lang === "und" ? !(f.languages && f.languages.length) : (f.languages || []).includes(lang);
const say = m => { try { if (tg && tg.showAlert) return tg.showAlert(m); } catch (e) {} alert(m); };
const IOS_VOL = (() => { try { const a = document.createElement("video"); a.volume = 0.5; return a.volume !== 0.5; } catch (e) { return false; } })();

function groupsOf(files) {
  const m = new Map();
  files.forEach(f => {
    const cs = f.languages && f.languages.length ? f.languages : ["und"];
    cs.forEach(c => { if (!m.has(c)) m.set(c, []); m.get(c).push(f); });
  });
  return new Map([...m.entries()].sort((a, b) => b[1].length - a[1].length));
}
function langName(c) {
  if (c === "und") return "Default audio";
  try { return langLabel(c); } catch (e) { return String(c).toUpperCase(); }
}
function langFlag(c) {
  if (c === "und") return "🎧";
  try { return LANG_FLAGS[c] || "🌐"; } catch (e) { return "🌐"; }
}
function autoRank() {
  const c = navigator.connection;
  if (c) {
    if (c.saveData || /(^|-)2g/.test(c.effectiveType || "")) return RANK["480p"];
    if (/3g/.test(c.effectiveType || "") || (c.downlink && c.downlink < 3)) return RANK["720p"];
  }
  let p = null;
  try { p = effectivePreferredQuality(); } catch (e) {}
  return p && p in RANK ? RANK[p] : RANK["720p"];
}
/* Best file for a language + quality. A file that may not play in a browser
   (HEVC / 10-bit / AC3 / DTS ...) loses to a playable one that is close in
   quality, and a file whose FIRST listed language matches wins for dual-audio
   releases (that is the audio track the browser plays). */
function pick(files, opt) {
  const o = opt || {};
  let pool = o.lang ? files.filter(f => inLang(f, o.lang)) : files;
  if (!pool.length) pool = files;
  const target = o.quality != null ? (RANK[o.quality] != null ? RANK[o.quality] : 3) : autoRank();
  const penalty = o.penalty != null ? o.penalty : 25;
  let best = null, bestScore = -Infinity;
  pool.forEach((f, i) => {
    if (f._bad) return;
    let s = -Math.abs((RANK[f.quality] || 0) - target) * 10 - (risky(f) ? penalty : 0) - i * 0.01;
    if (o.lang && o.lang !== "und" && f.languages && f.languages[0] === o.lang) s += 12;
    if (s > bestScore) { bestScore = s; best = f; }
  });
  return best || pool[0];
}
const streamUrl = r => buildStreamUrls(r).directStreamUrl;

/* ------------------------------------------------------------------ icons */
const I = {
  back: '<svg viewBox="0 0 24 24"><path d="M15 18l-6-6 6-6"/></svg>',
  close: '<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg>',
  play: '<svg viewBox="0 0 24 24"><path class="f" d="M8 5v14l11-7z"/></svg>',
  pause: '<svg viewBox="0 0 24 24"><path class="f" d="M7 5h4v14H7zM13 5h4v14h-4z"/></svg>',
  replay: '<svg viewBox="0 0 24 24"><path d="M3 12a9 9 0 1 0 3-6.7M3 4v5h5"/></svg>',
  sb: '<svg viewBox="0 0 24 24"><path d="M3 12a9 9 0 1 0 3-6.7M3 4v5h5"/><text x="12" y="15.4" font-size="7.5" font-weight="800" text-anchor="middle" fill="currentColor" stroke="none">10</text></svg>',
  sf: '<svg viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-3-6.7M21 4v5h-5"/><text x="12" y="15.4" font-size="7.5" font-weight="800" text-anchor="middle" fill="currentColor" stroke="none">10</text></svg>',
  vol: '<svg viewBox="0 0 24 24"><path d="M11 5L6 9H3v6h3l5 4z"/><path d="M15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13"/></svg>',
  volx: '<svg viewBox="0 0 24 24"><path d="M11 5L6 9H3v6h3l5 4z"/><path d="M16 9l5 6M21 9l-5 6"/></svg>',
  sun: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
  globe: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3.2 3 14.8 0 18M12 3c-3 3.2-3 14.8 0 18"/></svg>',
  fit: '<svg viewBox="0 0 24 24"><rect x="3" y="7" width="18" height="10" rx="1.5"/></svg>',
  fs: '<svg viewBox="0 0 24 24"><path d="M8 3H5a2 2 0 0 0-2 2v3M16 3h3a2 2 0 0 1 2 2v3M8 21H5a2 2 0 0 1-2-2v-3M16 21h3a2 2 0 0 0 2-2v-3"/></svg>',
  lock: '<svg viewBox="0 0 24 24"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>',
  unlock: '<svg viewBox="0 0 24 24"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 7.5-2"/></svg>',
  cone: '<svg viewBox="0 0 24 24"><path class="f" d="M12 2.5l6.4 17.5H5.6z"/><path d="M9.3 10.5h5.4M7.9 14.8h8.2" stroke="#111" stroke-width="2.2"/><path class="f" d="M4 21h16v1.5H4z"/></svg>',
  check: '<svg viewBox="0 0 24 24"><path d="M5 12l5 5 9-10"/></svg>',
  retry: '<svg viewBox="0 0 24 24"><path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/></svg>',
};

/* ------------------------------------------------------------------ DOM */
const root = document.createElement("div");
root.className = "gx paused";
root.id = "gx";
root.innerHTML = `
<video class="gx-video" id="gxVideo" playsinline webkit-playsinline preload="auto"></video>
<canvas class="gx-freeze" id="gxFreeze"></canvas>
<div class="gx-bg" id="gxBg"></div>
<div class="gx-loader" id="gxLoader"><div class="gx-ring"></div><div class="gx-lt" id="gxLt"></div><div class="gx-ls" id="gxLs"></div></div>
<div class="gx-gest" id="gxGest"></div>
<div class="gx-rip l" id="gxRipL"></div><div class="gx-rip r" id="gxRipR"></div>
<div class="gx-hud" id="gxHud"><span id="gxHudIcon"></span><div class="gx-hud-bar"><b id="gxHudFill"></b></div><span id="gxHudVal"></span></div>
<div class="gx-hint" id="gxHint"></div>
<div class="gx-ui" id="gxUi">
  <div class="gx-top">
    <button class="gx-ib" id="gxBack" aria-label="Close">${I.back}</button>
    <div class="gx-tb"><div class="gx-title" id="gxTitle"></div><div class="gx-chips" id="gxChips"></div></div>
    <button class="gx-vlc" id="gxVlc" aria-label="Play in VLC or another player">${I.cone}<span>VLC</span></button>
  </div>
  <div class="gx-mid">
    <button class="gx-skip" id="gxSkipB" aria-label="Back 10 seconds">${I.sb}</button>
    <button class="gx-play" id="gxPlay" aria-label="Play / pause"></button>
    <button class="gx-skip" id="gxSkipF" aria-label="Forward 10 seconds">${I.sf}</button>
  </div>
  <div class="gx-bot">
    <div class="gx-seek" id="gxSeek"><div class="gx-track"><div class="gx-buf" id="gxBuf"></div><div class="gx-fill" id="gxFill"></div></div><div class="gx-thumb" id="gxThumb"></div><div class="gx-tip" id="gxTip"></div></div>
    <div class="gx-times"><span id="gxCur">0:00</span><span id="gxDur">0:00</span></div>
    <div class="gx-row">
      <button class="gx-pill" id="gxVolB" aria-label="Volume"></button>
      <button class="gx-pill" id="gxSpeed" aria-label="Speed">1x</button>
      <span class="gx-sp"></span>
      <button class="gx-pill" id="gxLang" aria-label="Language">${I.globe}<span id="gxLangT"></span></button>
      <button class="gx-pill acc" id="gxQual" aria-label="Quality"><span id="gxQualT"></span></button>
      <button class="gx-pill" id="gxFit" aria-label="Fit / fill">${I.fit}</button>
      <button class="gx-pill" id="gxFs" aria-label="Fullscreen">${I.fs}</button>
    </div>
  </div>
</div>
<button class="gx-ib gx-lock" id="gxLock" aria-label="Lock controls">${I.lock}</button>
<button class="gx-suggest" id="gxSuggest" hidden></button>
<div class="gx-toast" id="gxToast"></div>
<div class="gx-mw" id="gxMw"><div class="gx-menu" id="gxMenu"></div></div>
<div class="gx-err" id="gxErr">
  <div class="gx-ei" id="gxEi">⚠️</div><div class="gx-et" id="gxEt"></div><div class="gx-es" id="gxEs"></div>
  <button class="gx-eb pri" id="gxEbPri"></button>
  <button class="gx-eb" id="gxEbSec"></button>
  <button class="gx-eb" id="gxEbAlt" hidden></button>
  <button class="gx-eb" id="gxEbDet">Details</button>
  <button class="gx-eb" id="gxEbClose">Close</button>
</div>`;
document.body.appendChild(root);
const E = {};
root.querySelectorAll("[id]").forEach(n => { E[n.id.charAt(2).toLowerCase() + n.id.slice(3)] = n; });
const v = E.video;

/* ------------------------------------------------------------------ state */
const S = {
  open: false, item: null, files: [], lang: null, file: null, resolved: null, url: "",
  token: 0, pending: null, started: false, errored: false,
  retries: 0, retryT: 0, okT: 0, loadT: 0, stallT: 0, bufT: 0, uiT: 0, tapT: 0, hudT: 0, hintT: 0, toastT: 0,
  lastTap: 0, lastSide: null, acc: 0, accT: 0, accSide: null,
  seeking: false, dragT: 0, locked: false, brightness: 1, fill: false, speed: 1,
  histDone: false, lastSave: 0, wake: null, menu: null, slowToast: false, volWarned: false, diagBusy: false,
  inline: false, full: false, hero: null, nfs: false, sysFs: false,
  lastData: 0, loadStart: 0, waitAt: 0, netT: 0, sugT: 0, sugAt: -1e9, stalls: [], loadSub: "",
};

/* ------------------------------------------------------------------ small UI helpers */
function toast(msg, ms) {
  E.toast.textContent = msg;
  E.toast.classList.add("on");
  clearTimeout(S.toastT);
  S.toastT = setTimeout(() => E.toast.classList.remove("on"), ms || 2200);
}
function loader(on, title, sub, light) {
  E.loader.classList.toggle("on", !!on);
  E.loader.classList.toggle("light", !!light);
  if (title != null) E.lt.textContent = title;
  if (sub != null) E.ls.textContent = sub;
}
function showUI(stick) {
  root.classList.add("ui");
  clearTimeout(S.uiT);
  if (!stick) armHide();
}
function armHide() {
  clearTimeout(S.uiT);
  S.uiT = setTimeout(() => {
    if (!v.paused && !S.menu && !S.seeking && !S.errored) root.classList.remove("ui");
  }, 3500);
}
function toggleUI() {
  if (root.classList.contains("ui")) { if (!v.paused && !S.menu) root.classList.remove("ui"); }
  else showUI();
}
function setPlayIcon() {
  E.play.innerHTML = v.ended ? I.replay : (v.paused ? I.play : I.pause);
  root.classList.toggle("paused", v.paused);
}
function setVolIcon() {
  E.volB.innerHTML = (v.muted || v.volume === 0) ? I.volx : I.vol;
}
function hud(kind, val, label) {
  E.hud.className = "gx-hud on " + (kind === "bri" ? "l" : "r");
  E.hudIcon.innerHTML = kind === "bri" ? I.sun : ((v.muted || v.volume === 0) ? I.volx : I.vol);
  E.hudFill.style.height = Math.round(clamp(val, 0, 1) * 100) + "%";
  E.hudVal.textContent = label;
  clearTimeout(S.hudT);
  S.hudT = setTimeout(hideHud, 900);
}
function hideHud() { E.hud.classList.remove("on"); }
function hint(text) {
  E.hint.textContent = text;
  E.hint.classList.add("on");
  clearTimeout(S.hintT);
  S.hintT = setTimeout(hideHint, 1200);
}
function hideHint() { E.hint.classList.remove("on"); }
function ripple(left) {
  const el = left ? E.ripL : E.ripR;
  const now = Date.now();
  S.acc = (S.accSide === left && now - S.accT < 900 ? S.acc : 0) + 10;
  S.accSide = left; S.accT = now;
  el.textContent = left ? "«  " + S.acc + "s" : S.acc + "s  »";
  el.classList.remove("on");
  void el.offsetWidth;
  el.classList.add("on");
}
function setBrightness(b) {
  S.brightness = clamp(b, 0.3, 1.6);
  root.style.setProperty("--gx-bri", S.brightness);
  hud("bri", (S.brightness - 0.3) / 1.3, Math.round(S.brightness * 100) + "%");
}
function setVolume(x, quiet) {
  if (IOS_VOL) { if (!S.volWarned) { S.volWarned = true; toast("Use your phone's volume buttons"); } return; }
  v.volume = clamp(x, 0, 1);
  v.muted = v.volume === 0;
  if (!quiet) hud("vol", v.volume, Math.round(v.volume * 100) + "%");
}
function freeze() {
  try {
    if (v.readyState < 2 || !v.videoWidth) return;
    const k = Math.min(1, 960 / v.videoWidth), c = E.freeze;
    c.width = Math.round(v.videoWidth * k);
    c.height = Math.round(v.videoHeight * k);
    c.getContext("2d").drawImage(v, 0, 0, c.width, c.height);
    c.classList.add("on");
  } catch (e) {}
}
function unfreeze() { E.freeze.classList.remove("on"); }

/* ------------------------------------------------------------------ title / chips */
function cleanName(n) { return String(n || "").replace(/\.[a-z0-9]{2,4}$/i, "").replace(/[._]+/g, " ").trim(); }
function setMeta() {
  const it = S.item || {}, f = S.file || {};
  E.title.textContent = it._episodeLabel || it.title || it.name || cleanName(f.name) || "GOFLIX";
  const multi = groupsOf(S.files).size > 1;
  let chips = `<span class="gx-chip q">${esc(f.quality || "")}</span>`;
  if (S.lang && (S.lang !== "und" || multi)) chips += `<span class="gx-chip">${esc(langName(S.lang))}</span>`;
  if (f.size) chips += `<span class="gx-chip">${esc(f.size)}</span>`;
  if (risky(f)) chips += `<span class="gx-chip w">VLC recommended</span>`;
  E.chips.innerHTML = chips;
  E.qualT.textContent = f.quality || "";
  E.langT.textContent = S.lang === "und" ? "AUDIO" : String(S.lang || "").toUpperCase();
  const tracks = v.audioTracks ? v.audioTracks.length : 0;
  E.lang.style.display = (multi || tracks > 1) ? "" : "none";
}

/* ------------------------------------------------------------------ resolve */
async function resolveFile(item, fileId, fresh) {
  const res = await fetch(CONFIG.RESOLVE_ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ init_data: (tg && tg.initData) || "", file_id: fileId, title: "", fresh: !!fresh }),
  });
  if (res.status === 402) {
    const d = await res.json().catch(() => ({}));
    const e = new Error("premium_required"); e.premiumMessage = d.message; throw e;
  }
  if (res.status === 401) throw new Error("auth_failed");
  if (res.status === 404) throw new Error("not_found");
  if (res.status === 502) {
    const d = await res.json().catch(() => ({}));
    const e = new Error("file_corrupted"); e.serverMessage = d.message; throw e;
  }
  if (!res.ok) throw new Error("server_error");
  return res.json();
}
/* Resolve the chosen file; if Telegram says that copy is broken, quietly try
   the next best one instead of failing. */
async function resolveBest(file, lang) {
  const tried = new Set();
  let f = file;
  for (let i = 0; i < 4 && f; i++) {
    tried.add(f);
    try { return { file: f, resolved: await resolveFile(S.item, f.file_id, false) }; }
    catch (e) {
      if (e.message !== "file_corrupted" && e.message !== "not_found") throw e;
      f._bad = true;
      const rest = S.files.filter(x => !tried.has(x) && !x._bad);
      f = rest.length ? pick(rest, { lang, quality: file.quality, penalty: 8 }) : null;
      if (f) toast("That copy is broken - trying another");
    }
  }
  throw new Error("file_corrupted");
}

/* ------------------------------------------------------------------ source loading + recovery */
function bufEnd() {
  for (let i = 0; i < v.buffered.length; i++) {
    if (v.buffered.start(i) <= v.currentTime + 0.3 && v.buffered.end(i) >= v.currentTime) return v.buffered.end(i);
  }
  return v.currentTime;
}
function load(url, o) {
  const opt = o || {};
  S.url = url;
  S.pending = { resumeAt: opt.resumeAt || 0, autoplay: opt.autoplay !== false };
  S.errored = false;
  E.err.classList.remove("on");
  clearTimeout(S.stallT); clearTimeout(S.bufT); clearTimeout(S.loadT);
  S.loadStart = S.lastData = performance.now();
  netReset();
  /* Only when NO data at all arrives for ~18s is the link treated as stuck. A slow but moving
     download is left alone - restarting it every 20s is what kept big files from ever loading. */
  S.loadT = setTimeout(loadTick, 3000);
  v.pause();
  v.defaultPlaybackRate = S.speed;
  v.src = url;
  v.load();
  v.playbackRate = S.speed;
}
function play() {
  const pr = v.play();
  if (pr && pr.catch) pr.catch(err => {
    if (err && err.name === "NotAllowedError") { loader(false); unfreeze(); showUI(true); toast("Tap play to start"); }
  });
}
const NET = { t: 0, b: 0, ema: 0 };
function netReset() { NET.t = 0; NET.b = 0; NET.ema = 0; }
/* Download speed estimated from how fast the buffered range grows x the file's bitrate. */
function netSample() {
  if (!v.duration || !S.file || !S.file.bytes) return;
  const now = performance.now(), b = bufEnd();
  if (!NET.t) { NET.t = now; NET.b = b; return; }
  const dt = (now - NET.t) / 1000;
  if (dt < 0.9) return;
  const db = b - NET.b;
  NET.t = now; NET.b = b;
  if (db < 0 || db > 120) return;          // a seek / new range, not a download sample
  const bps = db * (S.file.bytes / v.duration) / dt;
  NET.ema = NET.ema ? NET.ema * 0.6 + bps * 0.4 : bps;
}
function netText() {
  if (NET.ema > 0 && v.duration) {
    return Math.round(Math.max(0, bufEnd() - v.currentTime)) + "s buffered · " + (NET.ema / 1048576).toFixed(1) + " MB/s";
  }
  return S.loadSub || "";
}
function netLoop() {
  if (!S.open) return;
  netSample();
  if (E.loader.classList.contains("on")) E.ls.textContent = netText();
  S.netT = setTimeout(netLoop, 1000);
}
function bumpData() { S.lastData = performance.now(); }
function loadTick() {
  if (!S.open || !S.pending) return;
  const now = performance.now();
  if (now - S.lastData > 18000) return recover(false);
  if (now - S.loadStart > 10000) suggestLower();
  S.loadT = setTimeout(loadTick, 3000);
}
/* "Slow network - switch to 480p" - one tap, never forced. */
function suggestLower() {
  const now = performance.now();
  if (!S.file || !S.open || S.locked || now - S.sugAt < 90000) return;
  const cur = RANK[S.file.quality] || 0;
  const lower = S.files
    .filter(f => !f._bad && inLang(f, S.lang) && (RANK[f.quality] || 0) < cur)
    .sort((a, b) => (RANK[b.quality] || 0) - (RANK[a.quality] || 0) || (a.bytes || 0) - (b.bytes || 0))[0];
  if (!lower) return;
  S.sugAt = now;
  E.suggest.textContent = "Slow network - switch to " + lower.quality + (lower.size ? " (" + lower.size + ")" : "");
  E.suggest.hidden = false;
  E.suggest.onclick = () => { E.suggest.hidden = true; switchTo(lower, S.lang); };
  clearTimeout(S.sugT);
  S.sugT = setTimeout(() => { E.suggest.hidden = true; }, 10000);
}
function bufStart(isStall) {
  clearTimeout(S.bufT);
  const now = performance.now();
  S.waitAt = now;
  if (isStall) {
    S.stalls = S.stalls.filter(t => now - t < 60000);
    S.stalls.push(now);
    if (S.stalls.length >= 3) suggestLower();
  }
  S.bufT = setTimeout(() => { if (S.open && !v.paused && !S.errored) loader(true, "Buffering…", netText(), true); }, 350);
  armStall();
}
function bufDone() {
  clearTimeout(S.bufT); clearTimeout(S.stallT);
  if (!S.pending) loader(false);
}
/* A stalled video is only restarted when NO new data has arrived for 12s (before it was "no
   timeupdate for 10s", which fired during every slow seek and restarted the video). */
function armStall() {
  clearTimeout(S.stallT);
  S.stallT = setTimeout(() => {
    if (!S.open || S.pending || S.errored || v.paused || v.readyState >= 3) return;
    const now = performance.now();
    if (now - S.lastData > 12000) return recover(false);
    if (now - S.waitAt > 8000 && !S.slowToast) { S.slowToast = true; toast("Slow connection…"); }
    armStall();
  }, 3000);
}
async function recover(forceFresh) {
  if (!S.open || !S.file) return;
  const now = Date.now();
  if (now - S.retryT > 120000) { S.retries = 0; S.retryT = now; }
  if (++S.retries > 3) return fail("net");
  const t = v.currentTime || (S.pending && S.pending.resumeAt) || 0;
  const my = ++S.token;
  toast(S.retries > 1 ? "Reconnecting…" : "Connection hiccup - retrying…");
  freeze();
  loader(true, "Reconnecting…", "", true);
  try {
    let url = S.url;
    if (forceFresh || S.retries >= 2) {
      const r = await resolveFile(S.item, S.file.file_id, true);
      if (my !== S.token) return;
      S.resolved = r;
      url = streamUrl(r);
    }
    load(url, { resumeAt: t, autoplay: true });
  } catch (e) {
    if (my === S.token) fail(e.message === "file_corrupted" ? "codec" : "net");
  }
}
function altFile() {
  if (!S.file) return null;
  const pool = S.files.filter(f => f !== S.file && !f._bad && !risky(f) && inLang(f, S.lang));
  return pool.length ? pick(pool, { lang: S.lang, quality: S.file.quality, penalty: 0 }) : null;
}
function fail(kind) {
  S.errored = true;
  v.pause();
  loader(false); unfreeze();
  root.classList.remove("ui");
  const codec = kind === "codec";
  E.ei.textContent = codec ? "🎞️" : "📡";
  E.et.textContent = codec ? "This file can't play in the browser" : "Connection lost";
  E.es.textContent = codec
    ? "Your phone's browser doesn't support this video or audio format. VLC plays everything."
    : "The stream stopped and couldn't be restored. Tap Details to see why, or retry.";
  const vlcFirst = codec;
  const retry = { html: I.retry + " Retry", fn: () => { E.err.classList.remove("on"); S.retries = 0; recover(true); } };
  const vlc = { html: I.cone + " Open in VLC", fn: () => openExternal("vlc") };
  const a = vlcFirst ? vlc : retry, b = vlcFirst ? retry : vlc;
  E.ebPri.innerHTML = a.html; E.ebPri.onclick = a.fn;
  E.ebSec.innerHTML = b.html; E.ebSec.onclick = b.fn;
  const alt = codec ? altFile() : null;
  E.ebAlt.hidden = !alt;
  if (alt) {
    E.ebAlt.textContent = "Try " + alt.quality + (alt.size ? " · " + alt.size : "") + " (browser-friendly)";
    E.ebAlt.onclick = () => { E.err.classList.remove("on"); S.errored = false; switchTo(alt, S.lang); };
  }
  E.err.classList.add("on");
}

/* ------------------------------------------------------------------ video events */
v.addEventListener("loadedmetadata", () => {
  E.dur.textContent = fmt(v.duration);
  const p = S.pending;
  if (p && p.resumeAt > 1 && isFinite(v.duration) && p.resumeAt < v.duration - 3) { try { v.currentTime = p.resumeAt; } catch (e) {} }
  setMeta();
});
v.addEventListener("canplay", () => {
  const p = S.pending;
  clearTimeout(S.loadT);
  if (p) {
    S.pending = null;
    if (p.autoplay) play(); else { loader(false); unfreeze(); }
  } else bufDone();
});
v.addEventListener("playing", () => {
  S.started = true; S.errored = false;
  root.classList.add("started");
  setPlayIcon();
  clearTimeout(S.bufT); clearTimeout(S.stallT);
  loader(false); unfreeze();
  armHide(); wake(); mediaSession();
  clearTimeout(S.okT);
  S.okT = setTimeout(() => { S.retries = 0; S.slowToast = false; }, 25000);
});
v.addEventListener("waiting", () => { if (S.started && !S.pending) bufStart(true); });
v.addEventListener("seeking", () => { if (S.started && !S.pending) bufStart(false); });
/* first frame is ready: start right away instead of waiting for "canplay" (instant play) */
v.addEventListener("loadeddata", () => {
  const p = S.pending;
  if (p && p.autoplay && v.readyState >= 2) {
    S.pending = null;
    clearTimeout(S.loadT);
    loader(true, "Starting…", netText(), false);
    play();
  }
});
["progress", "loadedmetadata", "loadeddata", "canplay", "playing", "timeupdate", "seeked", "durationchange"]
  .forEach(t => v.addEventListener(t, bumpData));
v.addEventListener("seeked", () => { if (!S.pending) bufDone(); });
v.addEventListener("pause", () => { setPlayIcon(); if (S.open && !S.errored) showUI(true); unwake(); });
v.addEventListener("play", () => { setPlayIcon(); armHide(); });
v.addEventListener("ended", () => { setPlayIcon(); showUI(true); persist(true); });
v.addEventListener("ratechange", () => { E.speed.textContent = (v.playbackRate === 1 ? "1" : v.playbackRate) + "x"; });
v.addEventListener("volumechange", () => { setVolIcon(); if (S.menu === "volume") paintVolMenu(); });
v.addEventListener("progress", paintBuf);
v.addEventListener("durationchange", () => { E.dur.textContent = fmt(v.duration); });
v.addEventListener("timeupdate", () => {
  if (!isFinite(v.duration) || !v.duration) return;
  if (!S.seeking) paintTime(v.currentTime);
  paintBuf();
  persist(false);
  if (!S.histDone && S.item && v.currentTime >= Math.min(300, v.duration * 0.5)) {
    S.histDone = true;
    try { recordHistory(S.item); } catch (e) {}
  }
});
v.addEventListener("error", async () => {
  if (!S.open || !v.getAttribute("src")) return;
  const code = v.error && v.error.code;
  if (code === 1) return;
  if (code === 2) return recover(false);
  /* 3 = decode error, 4 = not supported. Either a dead link (404) or a real
     codec problem - ask the server which. */
  const my = S.token;
  try {
    const r = await fetch(S.url, { method: "HEAD" });
    if (my !== S.token) return;
    if (r.status === 404 || r.status === 403 || r.status >= 500) return recover(true);
  } catch (e) {
    if (my !== S.token) return;
    if (navigator.onLine === false) return recover(false);
  }
  fail("codec");
});
function paintTime(t) {
  const p = clamp(t / v.duration, 0, 1) * 100;
  E.fill.style.width = p + "%";
  E.thumb.style.left = p + "%";
  E.cur.textContent = fmt(t);
}
function paintBuf() {
  if (!v.duration) return;
  E.buf.style.width = clamp(bufEnd() / v.duration, 0, 1) * 100 + "%";
}

/* resume position (shared by every language / quality of the same title) */
function posKey() {
  const it = S.item || {};
  return [it.media_type || "m", it.id, it._season || 0, it._episode || 0].join(":");
}
function persist(force) {
  const now = Date.now();
  if (!force && now - S.lastSave < 5000) return;
  S.lastSave = now;
  if (!v.duration || !S.item) return;
  const m = store.get(POS_KEY) || {};
  if (v.currentTime > v.duration - 45) delete m[posKey()];
  else if (v.currentTime >= 20) m[posKey()] = [Math.floor(v.currentTime), now];
  else return;
  const keys = Object.keys(m);
  if (keys.length > 60) keys.sort((a, b) => m[a][1] - m[b][1]).slice(0, keys.length - 60).forEach(k => delete m[k]);
  store.set(POS_KEY, m);
}
function savedPos() {
  const m = store.get(POS_KEY) || {};
  const e = m[posKey()];
  return e ? e[0] : 0;
}

/* ------------------------------------------------------------------ switching language / quality */
async function switchTo(file, lang) {
  if (!file || !S.open) return;
  if (S.file && file.file_id === S.file.file_id && lang === S.lang) return;
  const my = ++S.token;
  toast("Switching to " + (lang !== "und" ? langName(lang) + " · " : "") + file.quality + "…", 4000);
  try {
    const best = await resolveBest(file, lang);
    if (my !== S.token) return;
    const t = v.currentTime, wasPlaying = !v.paused || !!S.pending;
    freeze();
    loader(true, "Switching…", "", true);
    S.file = best.file; S.lang = lang; S.resolved = best.resolved; S.slowToast = false;
    setMeta();
    load(streamUrl(best.resolved), { resumeAt: t, autoplay: wasPlaying });
  } catch (e) {
    if (my !== S.token) return;
    loader(false); unfreeze();
    toast(e.message === "file_corrupted" ? "That file is not available" : "Couldn't switch - check your connection", 3000);
  }
}

/* ------------------------------------------------------------------ VLC / MX / copy */
/* "VLC": open the stream page (/watch/...) - the page you already have, where VLC, MX Player and the
   other players are listed. Its address is built from the URL in info.py (CONFIG.STREAM_BASE_URL). */
function pageUrl() { return buildStreamUrls(S.resolved).webPlayerUrl; }
function openLinkExt(u) {
  try {
    if (tg && tg.openLink) {
      /* Telegram's built-in browser cannot hand a link over to another app (that is the
         "Webpage not available" error), so ask for the phone's real browser instead
         (Bot API 7.6+; older clients ignore the option). */
      const p = String(tg.platform || "").toLowerCase();
      const opts = p.indexOf("android") === 0 ? { try_browser: "chrome" } : (p === "ios" ? { try_browser: "safari" } : {});
      tg.openLink(u, opts);
      return;
    }
  } catch (e) {}
  window.open(u, "_blank");
}
function openExternal() {
  if (!S.resolved) return toast("Stream isn't ready yet");
  try { v.pause(); } catch (e) {}
  toast("Opening the stream page…", 2000);
  openLinkExt(pageUrl());
}
/* "Details" on the error screen: finds out WHY the stream fails (browser -> server, and Telegram -> server). */
async function showDiag() {
  if (S.diagBusy) return;
  S.diagBusy = true;
  toast("Running tests…", 15000);
  const base = CONFIG.STREAM_BASE_URL.replace(/\/$/, "");
  const r = S.resolved || {};
  const lines = ["Stream host: " + base.replace(/^https?:\/\//, "")];
  let same = true;
  try { same = new URL(S.url, location.href).origin === location.origin; } catch (e) {}
  try {
    const t0 = performance.now();
    const res = await fetch(S.url, { headers: { Range: "bytes=0-65535" }, cache: "no-store", mode: same ? "cors" : "no-cors" });
    if (res.type === "opaque") lines.push("Browser test: reached the server (details hidden cross-site)");
    else {
      const buf = await res.arrayBuffer();
      lines.push("Browser test: HTTP " + res.status + ", " + Math.round(buf.byteLength / 1024) + " KB in " + ((performance.now() - t0) / 1000).toFixed(1) + "s");
    }
  } catch (e) { lines.push("Browser test FAILED: " + (e && e.message ? e.message : e)); }
  if (r.id) {
    try {
      const res = await fetch(base + "/api/diag?id=" + encodeURIComponent(r.id) + "&hash=" + encodeURIComponent(r.hash) + "&mb=4");
      const d = await res.json();
      lines.push("Server: TgCrypto " + (d.tgcrypto ? "ok" : "MISSING (very slow)") + ", Python " + d.python + ", " + d.clients + " client(s)");
      if (d.test) lines.push(d.test.error ? "Telegram test FAILED: " + d.test.error : "Telegram to server: " + d.test.mb_per_s + " MB/s (first byte " + d.test.first_byte_s + "s)");
    } catch (e) { lines.push("Server test FAILED: " + (e && e.message ? e.message : e)); }
  }
  S.diagBusy = false;
  E.toast.classList.remove("on");
  say(lines.join("\n"));
}

/* ------------------------------------------------------------------ menus */
function row(o) {
  return `<button class="gx-mr${o.cur ? " cur" : ""}" ${o.data || ""}>
    <span class="gx-mi${o.big ? " big" : ""}">${o.icon}</span>
    <span class="gx-mx"><div class="gx-ma">${esc(o.a)}</div>${o.b ? `<div class="gx-mb">${esc(o.b)}</div>` : ""}</span>
    ${o.chip ? `<span class="gx-mc">${esc(o.chip)}</span>` : ""}${o.cur ? `<span class="gx-ck">${I.check}</span>` : ""}</button>`;
}
function audioTrackList() {
  const t = v.audioTracks;
  return t && t.length > 1 ? Array.from({ length: t.length }, (_, i) => t[i]) : [];
}
function menuHtml(kind) {
  if (kind === "lang") {
    let h = '<div class="gx-mt">Language</div>';
    groupsOf(S.files).forEach((fl, code) => {
      const best = fl.reduce((m, f) => Math.max(m, RANK[f.quality] || 0), 0);
      const bestQ = Object.keys(RANK).find(k => RANK[k] === best) || "";
      h += row({ icon: langFlag(code), big: true, a: langName(code), b: fl.length + " file" + (fl.length > 1 ? "s" : "") + " · up to " + bestQ, cur: code === S.lang, data: `data-lang="${esc(code)}"` });
    });
    const tr = audioTrackList();
    if (tr.length) {
      h += '<div class="gx-mt" style="margin-top:14px">Audio tracks in this file</div>';
      tr.forEach((t, i) => { h += row({ icon: "🎧", big: true, a: t.label || t.language || "Track " + (i + 1), b: t.language || "", cur: t.enabled, data: `data-track="${i}"` }); });
    }
    return h;
  }
  if (kind === "quality") {
    let list = S.files.filter(f => !f._bad && inLang(f, S.lang));
    if (!list.length) list = S.files.filter(f => !f._bad);
    list = list.slice().sort((a, b) => (RANK[b.quality] || 0) - (RANK[a.quality] || 0) || (b.bytes || 0) - (a.bytes || 0));
    let h = '<div class="gx-mt">Quality</div>';
    list.forEach(f => {
      h += row({ icon: f.quality, a: f.quality + (f.size ? " · " + f.size : ""), b: cleanName(f.name).slice(0, 60), chip: risky(f) ? "VLC rec." : "", cur: !!S.file && f.file_id === S.file.file_id, data: `data-idx="${S.files.indexOf(f)}"` });
    });
    if (list.length < 2) h += '<div class="gx-note">Only one file is available for this language.</div>';
    return h;
  }
  if (kind === "speed") {
    return '<div class="gx-mt">Playback speed</div><div class="gx-sg">' +
      SPEEDS.map(s => `<button class="gx-sc${s === S.speed ? " cur" : ""}" data-speed="${s}">${s === 1 ? "Normal" : s + "x"}</button>`).join("") + "</div>";
  }
  if (kind === "volume") {
    return '<div class="gx-mt">Volume</div><div class="gx-vol"><button class="gx-pill" id="gxMute"></button><input class="gx-range" id="gxRange" type="range" min="0" max="100" step="1" aria-label="Volume"></div>' +
      (IOS_VOL ? '<div class="gx-note">On iPhone use the side volume buttons.</div>' : "");
  }
  return "";
}
function paintVolMenu() {
  const r = $id("gxRange"), m = $id("gxMute");
  if (!r) return;
  const val = v.muted ? 0 : Math.round(v.volume * 100);
  r.value = val;
  r.style.setProperty("--p", val + "%");
  m.innerHTML = (v.muted || v.volume === 0) ? I.volx : I.vol;
}
function openMenu(kind) {
  S.menu = kind;
  E.menu.innerHTML = '<div class="gx-mh"></div>' + menuHtml(kind);
  E.mw.classList.add("on");
  setLift();
  showUI(true);
  if (kind === "volume") {
    const r = $id("gxRange"), m = $id("gxMute");
    r.addEventListener("input", () => { v.muted = false; setVolume(r.value / 100, true); r.style.setProperty("--p", r.value + "%"); });
    m.addEventListener("click", () => { v.muted = !v.muted; if (!v.muted && v.volume === 0) v.volume = 0.6; paintVolMenu(); });
    paintVolMenu();
  }
}
function closeMenu(silent) {
  S.menu = null;
  E.mw.classList.remove("on");
  setLift();
  if (!silent) armHide();
}
E.mw.addEventListener("click", e => {
  if (e.target === E.mw) return closeMenu();
  const b = e.target.closest("button");
  if (!b) return;
  if (b.dataset.lang != null) {
    const code = b.dataset.lang;
    closeMenu(true);
    if (code === S.lang) return;
    const f = pick(S.files, { lang: code, quality: S.file.quality, penalty: 8 });
    if (f.file_id === S.file.file_id) return toast("Both languages are inside this one file - choose the audio track in VLC.", 3600);
    return switchTo(f, code);
  }
  if (b.dataset.track != null) {
    const tr = audioTrackList(), i = +b.dataset.track;
    tr.forEach((t, j) => { t.enabled = j === i; });
    closeMenu(true);
    return toast("Audio: " + (tr[i].label || tr[i].language || "track " + (i + 1)));
  }
  if (b.dataset.idx != null) {
    closeMenu(true);
    return switchTo(S.files[+b.dataset.idx], S.lang);
  }
  if (b.dataset.speed != null) {
    S.speed = +b.dataset.speed;
    v.defaultPlaybackRate = S.speed; v.playbackRate = S.speed;
    E.speed.textContent = S.speed + "x";
    return closeMenu();
  }
});

/* ------------------------------------------------------------------ controls wiring */
const stop = e => e.stopPropagation();
function bindBtn(el, fn) {
  el.addEventListener("pointerdown", stop);
  el.addEventListener("click", e => { e.stopPropagation(); fn(e); if (S.open && !S.menu) showUI(); });
}
function togglePlay() {
  if (v.ended) v.currentTime = 0;
  if (v.paused) play(); else v.pause();
}
function skip(d) {
  if (!v.duration) return;
  v.currentTime = clamp(v.currentTime + d, 0, v.duration - 0.1);
  paintTime(v.currentTime);
}
bindBtn(E.back, () => { if (S.inline && S.full) setFull(false); else closePlayer(); });
bindBtn(E.play, togglePlay);
bindBtn(E.skipB, () => skip(-10));
bindBtn(E.skipF, () => skip(10));
bindBtn(E.vlc, () => openExternal());
bindBtn(E.ebDet, () => showDiag());
bindBtn(E.lang, () => openMenu("lang"));
bindBtn(E.qual, () => openMenu("quality"));
bindBtn(E.speed, () => openMenu("speed"));
bindBtn(E.volB, () => openMenu("volume"));
bindBtn(E.fit, () => { S.fill = !S.fill; root.classList.toggle("fill", S.fill); toast(S.fill ? "Fill screen" : "Fit to screen", 1200); });
bindBtn(E.fs, () => toggleFs());
bindBtn(E.lock, () => {
  S.locked = !S.locked;
  root.classList.toggle("locked", S.locked);
  E.lock.innerHTML = S.locked ? I.lock : I.unlock;
  if (S.locked) toast("Controls locked - tap the lock to unlock", 1800);
});
bindBtn(E.ebClose, () => closePlayer());
E.lock.innerHTML = I.unlock;

/* seek bar - the video is only seeked when you LET GO (before, every pixel of
   the drag fired a new request to the server) */
function previewSeek(e) {
  const r = E.seek.getBoundingClientRect();
  const p = clamp((e.clientX - r.left) / r.width, 0, 1);
  S.dragT = p * v.duration;
  paintTime(S.dragT);
  E.tip.style.left = p * 100 + "%";
  E.tip.textContent = fmt(S.dragT);
}
E.seek.addEventListener("pointerdown", e => {
  e.stopPropagation();
  if (!v.duration) return;
  S.seeking = true;
  E.seek.classList.add("drag");
  try { E.seek.setPointerCapture(e.pointerId); } catch (err) {}
  previewSeek(e);
  showUI(true);
});
E.seek.addEventListener("pointermove", e => { if (S.seeking) previewSeek(e); });
E.seek.addEventListener("pointerup", () => {
  if (!S.seeking) return;
  S.seeking = false;
  E.seek.classList.remove("drag");
  v.currentTime = S.dragT;
  showUI();
});
E.seek.addEventListener("pointercancel", () => { S.seeking = false; E.seek.classList.remove("drag"); });

/* gestures on the picture: tap = show/hide, double-tap = +-10s, drag up/down on the
   left = brightness / right = volume, drag sideways = scrub */
let G = null;
E.gest.addEventListener("pointerdown", e => {
  if (e.pointerType === "mouse" && e.button !== 0) return;
  G = { id: e.pointerId, x: e.clientX, y: e.clientY, t: performance.now(), mode: null, left: e.clientX < innerWidth / 2, vol: v.volume, bri: S.brightness, time: v.currentTime, target: null };
  try { E.gest.setPointerCapture(e.pointerId); } catch (err) {}
});
E.gest.addEventListener("pointermove", e => {
  if (!G || e.pointerId !== G.id || S.locked) return;
  const dx = e.clientX - G.x, dy = e.clientY - G.y;
  if (!G.mode) {
    if (Math.hypot(dx, dy) < 12) return;
    G.mode = Math.abs(dy) > Math.abs(dx) ? (G.left ? "bri" : "vol") : (v.duration ? "seek" : "none");
  }
  if (G.mode === "bri") setBrightness(G.bri - dy / 260);
  else if (G.mode === "vol") setVolume(G.vol - dy / 260);
  else if (G.mode === "seek") {
    G.target = clamp(G.time + dx / innerWidth * 120, 0, v.duration);
    const d = Math.round(G.target - G.time);
    hint(fmt(G.target) + "  (" + (d >= 0 ? "+" : "") + d + "s)");
  }
});
E.gest.addEventListener("pointerup", e => {
  if (!G || e.pointerId !== G.id) return;
  const g = G; G = null;
  if (g.mode === "seek" && g.target != null) { v.currentTime = g.target; paintTime(g.target); return; }
  if (g.mode) return;
  if (performance.now() - g.t > 380) return;
  tap(g.left);
});
E.gest.addEventListener("pointercancel", () => { G = null; hideHint(); hideHud(); });
function tap(left) {
  const now = performance.now();
  if (S.locked) { toggleUI(); return; }
  if (now - S.lastTap < 300 && S.lastSide === left) {
    clearTimeout(S.tapT);
    skip(left ? -10 : 10);
    ripple(left);
    S.lastTap = now;
    return;
  }
  S.lastTap = now; S.lastSide = left;
  clearTimeout(S.tapT);
  S.tapT = setTimeout(toggleUI, 260);
}

/* keyboard (desktop) */
function onKey(e) {
  if (!S.open) return;
  const k = e.key;
  if (k === " " || k === "k") { e.preventDefault(); togglePlay(); }
  else if (k === "ArrowLeft") skip(-10);
  else if (k === "ArrowRight") skip(10);
  else if (k === "ArrowUp") { e.preventDefault(); setVolume(v.volume + 0.1); }
  else if (k === "ArrowDown") { e.preventDefault(); setVolume(v.volume - 0.1); }
  else if (k === "m") v.muted = !v.muted;
  else if (k === "f") toggleFs();
  else if (k === "Escape") { if (S.menu) closeMenu(); else if (S.inline && S.full) setFull(false); else if (!document.fullscreenElement) closePlayer(); }
  showUI();
}

/* ------------------------------------------------------------------ fullscreen / wake lock / media session */
function setLift() { if (S.hero) S.hero.classList.toggle("gx-lift", S.full || !!S.menu); }
function setBackIcon() { E.back.innerHTML = (S.inline && !S.full) ? I.close : I.back; }
function onTgBack() { if (S.inline && S.full) setFull(false); else closePlayer(); }
function syncTgBack() {
  try {
    if (!tg || !tg.BackButton) return;
    if (S.open && S.full) { tg.BackButton.show(); tg.BackButton.onClick(onTgBack); }
    else { tg.BackButton.offClick(onTgBack); tg.BackButton.hide(); }
  } catch (e) {}
}
/* the phone's own fullscreen / landscape (best effort - inside Telegram only some of these exist) */
function nativeFs(on) {
  S.nfs = !!on;
  if (on) {
    try { if (tg && tg.requestFullscreen) tg.requestFullscreen(); } catch (e) {}
    try { const req = root.requestFullscreen || root.webkitRequestFullscreen; if (req) Promise.resolve(req.call(root)).catch(() => {}); } catch (e) {}
    try { Promise.resolve(screen.orientation.lock("landscape")).catch(() => {}); } catch (e) {}
  } else {
    try { if (tg && tg.exitFullscreen) tg.exitFullscreen(); } catch (e) {}
    try { if (document.fullscreenElement || document.webkitFullscreenElement) Promise.resolve((document.exitFullscreen || document.webkitExitFullscreen).call(document)).catch(() => {}); } catch (e) {}
    try { screen.orientation.unlock(); } catch (e) {}
  }
}
/* inline player: "full" = the same player stretched over the whole screen */
function setFull(on) {
  S.full = !!on || !S.inline;
  root.classList.toggle("full", S.full);
  document.body.style.overflow = S.full ? "hidden" : "";
  setLift(); setBackIcon(); syncTgBack();
  if (S.inline) nativeFs(!!on);
}
function toggleFs() {
  if (S.inline) setFull(!S.full);
  else nativeFs(!S.nfs);
}
document.addEventListener("fullscreenchange", () => {
  const el = document.fullscreenElement || document.webkitFullscreenElement;
  if (el === root) S.sysFs = true;
  else if (S.sysFs) { S.sysFs = false; S.nfs = false; if (S.open && S.inline && S.full) setFull(false); }
});
try {
  if (tg && tg.onEvent) tg.onEvent("fullscreenChanged", () => {
    if (tg.isFullscreen === false && S.nfs && S.open) { S.nfs = false; if (S.inline && S.full) setFull(false); }
  });
} catch (e) {}
async function wake() {
  try {
    if (navigator.wakeLock && !S.wake) {
      S.wake = await navigator.wakeLock.request("screen");
      S.wake.addEventListener("release", () => { S.wake = null; });
    }
  } catch (e) {}
}
function unwake() { try { if (S.wake) S.wake.release(); } catch (e) {} S.wake = null; }
function mediaSession() {
  if (!("mediaSession" in navigator) || typeof MediaMetadata === "undefined") return;
  try {
    const it = S.item || {};
    navigator.mediaSession.metadata = new MediaMetadata({
      title: E.title.textContent, artist: "GOFLIX",
      artwork: it.poster_path ? [{ src: IMG_BASE + "/w342" + it.poster_path, sizes: "342x513", type: "image/jpeg" }] : [],
    });
    navigator.mediaSession.setActionHandler("play", () => play());
    navigator.mediaSession.setActionHandler("pause", () => v.pause());
    navigator.mediaSession.setActionHandler("seekbackward", () => skip(-10));
    navigator.mediaSession.setActionHandler("seekforward", () => skip(10));
    navigator.mediaSession.setActionHandler("seekto", d => { if (d && d.seekTime != null) v.currentTime = d.seekTime; });
  } catch (e) {}
}

/* ------------------------------------------------------------------ open / close */
/* Opened from the detail page: the player lives inside the banner (.detail-hero), which becomes a pinned
   16:9 block while it plays. Anywhere else (e.g. a home row) it is a fullscreen overlay. */
function placeRoot() {
  const view = $id("view-detail");
  const hero = view && view.querySelector(".detail-hero");
  const inline = !!(view && view.classList.contains("active") && hero);
  if (inline) {
    hero.appendChild(root);
    S.hero = hero;
    hero.classList.add("gx-active");
    const tb = document.querySelector(".topbar");
    const th = tb && getComputedStyle(tb).display !== "none" ? Math.round(tb.getBoundingClientRect().height) : 0;
    hero.style.top = th + "px";
    requestAnimationFrame(() => {
      const r = hero.getBoundingClientRect();
      if (r.top < th - 2 || r.top > innerHeight * 0.6) window.scrollTo({ top: Math.max(0, window.scrollY + r.top - th), behavior: "smooth" });
    });
  } else {
    document.body.appendChild(root);
    S.hero = null;
  }
  S.inline = inline;
  root.classList.toggle("inline", inline);
}
function showPlayer() {
  if (S.open) return;
  S.open = true;
  placeRoot();
  root.classList.add("open");
  root.classList.remove("started", "ui", "locked", "fill");
  S.locked = false; S.fill = false; S.brightness = 1; S.speed = 1; S.stalls = []; S.sugAt = -1e9;
  S.full = !S.inline;
  root.classList.toggle("full", S.full);
  root.style.setProperty("--gx-bri", 1);
  E.lock.innerHTML = I.unlock;
  E.suggest.hidden = true;
  document.body.style.overflow = S.full ? "hidden" : "";
  setLift(); setBackIcon();
  try { if (tg && tg.disableVerticalSwipes) tg.disableVerticalSwipes(); } catch (e) {}
  syncTgBack();
  document.addEventListener("keydown", onKey);
  setPlayIcon(); setVolIcon();
  E.cur.textContent = "0:00"; E.dur.textContent = "0:00";
  E.fill.style.width = "0"; E.buf.style.width = "0"; E.thumb.style.left = "0";
  clearTimeout(S.netT);
  S.netT = setTimeout(netLoop, 1000);
}
function closePlayer() {
  if (!S.open) return;
  try { persist(true); } catch (e) {}
  S.open = false;
  S.token++;
  S.pending = null;
  ["stallT", "bufT", "uiT", "tapT", "hudT", "hintT", "okT", "loadT", "toastT", "netT", "sugT"].forEach(k => clearTimeout(S[k]));
  E.toast.classList.remove("on");
  E.suggest.hidden = true;
  try { v.pause(); } catch (e) {}
  v.removeAttribute("src");
  try { v.load(); } catch (e) {}
  closeMenu(true);
  loader(false); unfreeze(); hideHud(); hideHint();
  E.err.classList.remove("on");
  root.classList.remove("open", "started", "ui", "locked", "fill", "full", "inline");
  document.body.style.overflow = "";
  document.removeEventListener("keydown", onKey);
  unwake();
  if (S.nfs || S.sysFs) nativeFs(false);
  S.nfs = false; S.sysFs = false;
  try { if (tg && tg.enableVerticalSwipes) tg.enableVerticalSwipes(); } catch (e) {}
  if (S.hero) { S.hero.classList.remove("gx-active", "gx-lift"); S.hero.style.top = ""; S.hero = null; }
  document.body.appendChild(root);
  S.inline = false; S.full = false;
  syncTgBack();
}
/* leaving the detail page (back button, tab bar) must stop the inline player */
try {
  const dv = $id("view-detail");
  if (dv && window.MutationObserver) {
    new MutationObserver(() => { if (S.open && S.inline && !dv.classList.contains("active")) closePlayer(); })
      .observe(dv, { attributes: true, attributeFilter: ["class"] });
  }
} catch (e) {}

async function start(item, files, lang, file, resolved) {
  if (S.open) closePlayer();
  files.forEach(f => { delete f._bad; });
  S.item = item; S.files = files; S.lang = lang; S.file = file; S.resolved = resolved || null;
  S.histDone = false; S.retries = 0; S.slowToast = false; S.started = false; S.errored = false; S.pending = null;
  showPlayer();
  setMeta();
  const art = item.backdrop_path || item.poster_path;
  E.bg.style.backgroundImage = art ? `url(${IMG_BASE}/w780${art})` : "none";
  const my = ++S.token;
  S.loadSub = (lang !== "und" ? langName(lang) + " · " : "") + file.quality;
  loader(true, "Preparing your stream…", S.loadSub, false);
  try {
    const best = resolved ? { file, resolved } : await resolveBest(file, lang);
    if (my !== S.token) return;
    S.file = best.file; S.resolved = best.resolved;
    setMeta();
    const at = savedPos();
    load(streamUrl(best.resolved), { resumeAt: at, autoplay: true });
    if (at > 20) toast("Resuming from " + fmt(at), 2600);
  } catch (e) {
    if (my !== S.token) return;
    closePlayer();
    try { goHaptic("notification", "error"); } catch (e2) {}
    say(e.message === "file_corrupted" && e.serverMessage ? e.serverMessage : errorMessageFor(e));
  }
}

/* ------------------------------------------------------------------ the "watch" sheet: movie -> language -> play */
function renderLangSheet(item, files, groups) {
  sheetTitleEl.textContent = "Choose language";
  qualityStep.innerHTML = "";
  groups.forEach((fl, code) => {
    const best = fl.reduce((m, f) => Math.max(m, RANK[f.quality] || 0), 0);
    const bestQ = Object.keys(RANK).find(k => RANK[k] === best) || "";
    const btn = document.createElement("button");
    btn.className = "play-opt quality";
    btn.innerHTML = `<div class="oicon">${langFlag(code)}</div><div class="otext"><div class="otitle">${esc(langName(code))}</div><div class="osub">${fl.length} file${fl.length > 1 ? "s" : ""} · up to ${esc(bestQ)}</div></div>`;
    btn.addEventListener("click", () => {
      try { goHaptic("impact", "medium"); } catch (e) {}
      closeSheet();
      start(item, files, code, pick(files, { lang: code }));
    });
    qualityStep.appendChild(btn);
  });
}

window.openWatchSheet = async function (item, action) {
  if (action === "download") return legacyWatch.apply(this, arguments);
  if (S.open) closePlayer();
  activeItem = item;
  pendingAction = "watch";
  try { if (serverConfigPromise) await serverConfigPromise; } catch (e) {}
  sheetTitleEl.textContent = "Finding your movie…";
  qualityStep.innerHTML = '<div class="play-opt skeleton" style="height:58px"></div>'.repeat(3);
  sheet.classList.add("open");
  sheetBackdrop.classList.add("open");
  try { goHaptic("impact", "light"); } catch (e) {}
  try {
    const data = await fetchQualities(item);
    const files = (data && data.files) || [];
    sheetFiles = files;
    if (!files.length) {
      qualityStep.innerHTML = '<div class="empty-state" style="padding:24px 8px;text-align:center;color:var(--text-dim)">No files for this title yet.</div>';
      return;
    }
    const groups = groupsOf(files);
    if (groups.size > 1) return renderLangSheet(item, files, groups);
    closeSheet();
    const code = groups.keys().next().value;
    start(item, files, code, pick(files, { lang: code }));
  } catch (err) {
    try { goHaptic("notification", "error"); } catch (e) {}
    closeSheet();
    say(errorMessageFor(err));
  }
};

/* playRawFile() (language browsing) and the old quality picker call this. */
window.openGoflixPlayer = function (item, resolved, file) {
  const files = sheetFiles && sheetFiles.length ? sheetFiles : [file];
  const lang = (file.languages && file.languages[0]) || "und";
  start(item, files, lang, file, resolved);
};
window.closeGoflixPlayer = closePlayer;
})();
