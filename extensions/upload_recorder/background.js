// Upload Recorder — the part that keeps the recording.
//
// Every event is written to chrome.storage.local the moment it arrives, one
// key per event ("ev_000001", ...), so a page refresh, a closed tab, a
// browser restart or this service worker being put to sleep loses nothing.
// The recording state lives in storage too ("rec"), never only in memory.

const SECRET = /pass|pwd|token|secret|auth|card|cvc|cvv|iban|ssn|csrf|session|cookie|otp/i;
const SKIP_TYPES = new Set(["image", "font", "stylesheet", "media", "script", "ping", "csp_report"]);

function siteOf(host) {
  return (host || "").split(".").slice(-2).join(".");
}

function hostOf(url) {
  try { return new URL(url).hostname; } catch (e) { return ""; }
}

function onSite(url, rec) {
  return !!(rec && rec.on && url && siteOf(hostOf(url)) === rec.site);
}

// Query values named like a secret are hidden; the rest of the URL is kept.
function cleanUrl(url) {
  try {
    const u = new URL(url);
    for (const k of [...u.searchParams.keys()]) {
      if (SECRET.test(k)) u.searchParams.set(k, "[hidden]");
    }
    return u.toString();
  } catch (e) { return url; }
}

function maskJson(v) {
  if (Array.isArray(v)) return v.slice(0, 50).map(maskJson);
  if (v && typeof v === "object") {
    const out = {};
    for (const [k, x] of Object.entries(v)) out[k] = SECRET.test(k) ? "[hidden]" : maskJson(x);
    return out;
  }
  return typeof v === "string" ? v.slice(0, 500) : v;
}

// What was sent: form field names and short values, or small JSON bodies.
// Big bodies (the picture itself) are recorded only by size.
function bodySummary(rb) {
  if (!rb) return undefined;
  if (rb.formData) {
    const out = {};
    for (const [k, vals] of Object.entries(rb.formData)) {
      out[k] = SECRET.test(k) ? "[hidden]" : vals.map(v => String(v).slice(0, 300));
    }
    return { form: out };
  }
  if (rb.raw) {
    let bytes = 0;
    for (const part of rb.raw) {
      if (part.bytes) bytes += part.bytes.byteLength;
      if (part.file) return { file: part.file };
    }
    if (bytes > 8000 || !rb.raw[0] || !rb.raw[0].bytes) return { bytes };
    const text = new TextDecoder().decode(rb.raw[0].bytes);
    try { return { json: maskJson(JSON.parse(text)) }; }
    catch (e) { return { bytes, text: SECRET.test(text) ? "[hidden]" : text.slice(0, 2000) }; }
  }
  return undefined;
}

// One write at a time, so two events never take the same number.
let chain = Promise.resolve();
function add(ev) {
  chain = chain.then(async () => {
    const { rec, seq = 0 } = await chrome.storage.local.get(["rec", "seq"]);
    if (!rec || !rec.on) return;
    const n = seq + 1;
    ev.n = n;
    ev.design = rec.design;
    ev.t = ev.t || Date.now();
    await chrome.storage.local.set({ seq: n, ["ev_" + String(n).padStart(6, "0")]: ev });
  }).catch(e => console.error("Upload Recorder could not save an event", e));
  return chain;
}

async function start(tabUrl) {
  const { rec = {} } = await chrome.storage.local.get("rec");
  const site = /^https?:/.test(tabUrl || "") ? siteOf(hostOf(tabUrl)) : "";
  if (!site) return { error: "Open the website first, then press Start." };
  // Starting again on the same site carries on the same recording.
  const design = rec.site === site ? (rec.design || 1) : 1;
  await chrome.storage.local.set({ rec: { on: true, site, design, startedAt: rec.startedAt || Date.now() } });
  await add({ k: "start", url: cleanUrl(tabUrl) });
  return { ok: true };
}

async function stop() {
  await add({ k: "stop" });
  const { rec = {} } = await chrome.storage.local.get("rec");
  await chrome.storage.local.set({ rec: { ...rec, on: false } });
  return { ok: true };
}

async function nextDesign() {
  const { rec = {} } = await chrome.storage.local.get("rec");
  if (!rec.on) return { error: "Not recording." };
  await chrome.storage.local.set({ rec: { ...rec, design: (rec.design || 1) + 1 } });
  await add({ k: "next_design" });
  return { ok: true };
}

async function clearAll() {
  const all = await chrome.storage.local.get(null);
  await chrome.storage.local.remove(Object.keys(all).filter(k => k.startsWith("ev_")));
  await chrome.storage.local.set({ seq: 0, rec: { on: false } });
  return { ok: true };
}

function badge(rec) {
  chrome.action.setBadgeText({ text: rec && rec.on ? "REC" : "" });
  chrome.action.setBadgeBackgroundColor({ color: "#c62828" });
}

chrome.storage.local.get("rec").then(({ rec }) => badge(rec));
chrome.storage.onChanged.addListener(ch => { if (ch.rec) badge(ch.rec.newValue); });

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (msg.add) {
    add(msg.add);
    return false;
  }
  const jobs = {
    start: () => start(msg.url),
    stop,
    next: nextDesign,
    note: () => add({ k: "note", text: String(msg.text || "").slice(0, 1000) }).then(() => ({ ok: true })),
    clear: clearAll,
  };
  if (jobs[msg.do]) {
    jobs[msg.do]().then(reply, e => reply({ error: String(e) }));
    return true;
  }
  return false;
});

chrome.commands.onCommand.addListener(async cmd => {
  if (cmd !== "toggle-recording") return;
  const { rec } = await chrome.storage.local.get("rec");
  if (rec && rec.on) return stop();
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (tab && tab.url) return start(tab.url);
});

// Page changes, including the ones a single-page app makes without reloading.
function nav(how) {
  return async d => {
    if (d.frameId !== 0) return;
    const { rec } = await chrome.storage.local.get("rec");
    if (onSite(d.url, rec)) add({ k: "nav", how, url: cleanUrl(d.url), tab: d.tabId });
  };
}
chrome.webNavigation.onCommitted.addListener(nav("load"));
chrome.webNavigation.onHistoryStateUpdated.addListener(nav("in_page"));

// What the page sends to the website. Requests to other hosts count when the
// site's own page started them (for example an upload straight to cloud storage).
const pending = new Map();

async function wanted(d) {
  if (d.tabId < 0 || SKIP_TYPES.has(d.type)) return false;
  const { rec } = await chrome.storage.local.get("rec");
  return onSite(d.url, rec) || onSite(d.initiator, rec);
}

chrome.webRequest.onBeforeRequest.addListener(d => {
  pending.set(d.requestId, { at: d.timeStamp, body: bodySummary(d.requestBody) });
  if (pending.size > 500) pending.delete(pending.keys().next().value);
}, { urls: ["<all_urls>"] }, ["requestBody"]);

async function finished(d, error) {
  const p = pending.get(d.requestId) || {};
  pending.delete(d.requestId);
  if (!(await wanted(d))) return;
  const ev = {
    k: "net", method: d.method, url: cleanUrl(d.url), type: d.type,
    status: d.statusCode, ms: p.at ? Math.round(d.timeStamp - p.at) : undefined,
  };
  if (error) ev.error = error;
  if (p.body) ev.body = p.body;
  add(ev);
}
chrome.webRequest.onCompleted.addListener(d => finished(d), { urls: ["<all_urls>"] });
chrome.webRequest.onErrorOccurred.addListener(d => finished(d, d.error), { urls: ["<all_urls>"] });
