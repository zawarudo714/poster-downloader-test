// Upload Recorder — runs in every page, but records only while recording is
// on AND the page belongs to the site where Start was pressed. It reads that
// state from storage on every page load, so a refresh carries straight on.

(() => {
  if (window.__uploadRecorder) return;
  window.__uploadRecorder = true;

  const SECRET = /pass|pwd|token|secret|auth|card|cvc|cvv|iban|ssn|csrf|session|cookie|otp/i;
  const CLICKABLE = "button, a, input, select, textarea, label, summary, [role=button], [role=option], " +
    "[role=tab], [role=menuitem], [role=checkbox], [role=radio], [role=switch], [role=combobox], [onclick]";
  let rec = null;

  const siteOf = h => (h || "").split(".").slice(-2).join(".");
  const live = () => !!(rec && rec.on && siteOf(location.hostname) === rec.site);
  const clean = (s, n = 80) => (s || "").replace(/\s+/g, " ").trim().slice(0, n);

  function send(ev) {
    if (!live()) return;
    ev.url = location.href;
    ev.frame = window === window.top ? "top" : "frame";
    ev.t = Date.now();
    try { chrome.runtime.sendMessage({ add: ev }); } catch (e) { /* extension was reloaded */ }
  }

  function labelOf(el) {
    if (el.labels && el.labels[0]) return clean(el.labels[0].innerText);
    const by = el.getAttribute && el.getAttribute("aria-labelledby");
    if (by) {
      const l = document.getElementById(by.split(" ")[0]);
      if (l) return clean(l.innerText);
    }
    const wrap = el.closest && el.closest("label");
    return wrap ? clean(wrap.innerText) : "";
  }

  function cssPath(el) {
    const parts = [];
    for (let e = el; e && e.nodeType === 1 && parts.length < 5; e = e.parentElement) {
      if (e.id) { parts.unshift("#" + CSS.escape(e.id)); break; }
      let i = 1;
      for (let s = e.previousElementSibling; s; s = s.previousElementSibling) if (s.tagName === e.tagName) i++;
      parts.unshift(e.tagName.toLowerCase() + ":nth-of-type(" + i + ")");
    }
    return parts.join(" > ");
  }

  function isSecret(el) {
    return el.type === "password" || SECRET.test((el.name || "") + " " + (el.id || "") + " " + (el.autocomplete || ""));
  }

  function describe(el) {
    const d = { tag: el.tagName.toLowerCase() };
    for (const a of ["id", "name", "type", "role", "aria-label", "placeholder", "title", "href",
                     "data-testid", "data-test", "data-cy", "aria-checked", "aria-selected", "aria-expanded"]) {
      const v = el.getAttribute(a);
      if (v) d[a] = clean(v, 150);
    }
    const label = labelOf(el);
    if (label) d.label = label;
    if (!/^(input|select|textarea)$/.test(d.tag)) {
      const text = clean(el.innerText);
      if (text) d.text = text;
    }
    d.path = cssPath(el);
    return d;
  }

  function valueOf(el) {
    if (isSecret(el)) return "[hidden]";
    if (el.type === "file") return [...(el.files || [])].map(f => ({ name: f.name, size: f.size, type: f.type }));
    if (el.type === "checkbox" || el.type === "radio") return el.checked;
    if (el.tagName === "SELECT") return [...el.selectedOptions].map(o => clean(o.text));
    if (el.isContentEditable) return clean(el.innerText, 2000);
    return String(el.value || "").slice(0, 2000);
  }

  const target = e => (e.composedPath && e.composedPath()[0]) || e.target;

  document.addEventListener("click", e => {
    const t = target(e);
    if (!t || !t.closest) return;
    send({ k: "click", el: describe(t.closest(CLICKABLE) || t) });
  }, true);

  // Typing is recorded once it settles, not one key at a time.
  const typing = new Map();
  function flush(el) {
    clearTimeout(typing.get(el));
    typing.delete(el);
    send({ k: "type", el: describe(el), value: valueOf(el) });
  }
  document.addEventListener("input", e => {
    const el = target(e);
    if (!el || !el.tagName) return;
    if (/^(checkbox|radio|file)$/.test(el.type) || el.tagName === "SELECT") return;
    clearTimeout(typing.get(el));
    typing.set(el, setTimeout(() => flush(el), 800));
  }, true);
  document.addEventListener("focusout", e => { if (typing.has(target(e))) flush(target(e)); }, true);
  window.addEventListener("pagehide", () => { for (const el of [...typing.keys()]) flush(el); });

  document.addEventListener("change", e => {
    const el = target(e);
    if (!el || !el.tagName) return;
    if (/^(checkbox|radio|file)$/.test(el.type) || el.tagName === "SELECT") {
      send({ k: "choose", el: describe(el), value: valueOf(el) });
    }
  }, true);

  document.addEventListener("drop", e => {
    const t = target(e);
    const files = [...((e.dataTransfer && e.dataTransfer.files) || [])].map(f => ({ name: f.name, size: f.size, type: f.type }));
    send({ k: "drop", el: t && t.tagName ? describe(t) : undefined, files });
  }, true);

  document.addEventListener("keydown", e => {
    if (!["Enter", "Escape", "Tab"].includes(e.key)) return;
    const t = target(e);
    send({ k: "key", key: e.key, el: t && t.tagName ? describe(t) : undefined });
  }, true);

  document.addEventListener("submit", e => send({ k: "submit", el: describe(e.target) }), true);

  // Messages the site shows: errors, "saved", "uploading 40%" and so on.
  let lastMsg = "";
  const MSG = "[role=alert], [role=status], [aria-live], [class*=error], [class*=toast], [class*=snackbar], [class*=notif]";
  new MutationObserver(muts => {
    if (!live()) return;
    for (const m of muts) {
      const el = m.target.nodeType === 1 ? m.target : m.target.parentElement;
      const box = el && el.closest && el.closest(MSG);
      if (!box) continue;
      const text = clean(box.innerText, 300);
      if (text && text !== lastMsg) {
        lastMsg = text;
        send({ k: "message", text, el: describe(box) });
      }
    }
  }).observe(document.documentElement, { childList: true, subtree: true, characterData: true });

  // What is on the page: every visible field and button, with its options and
  // default value. Taken on load, on every in-page change and on request.
  function snapshot(why) {
    if (!live()) return;
    const visible = el => el.getClientRects().length > 0;
    const fields = [...document.querySelectorAll("input, select, textarea, [contenteditable=true], [role=combobox], [role=checkbox], [role=switch]")]
      .filter(el => visible(el) || el.type === "file").slice(0, 200).map(el => {
        const d = describe(el);
        if (el.required || el.getAttribute("aria-required") === "true") d.required = true;
        if (el.tagName === "SELECT") d.options = [...el.options].slice(0, 60).map(o => clean(o.text));
        if (el.type !== "hidden") d.value = valueOf(el);
        return d;
      });
    const buttons = [...document.querySelectorAll("button, [role=button], input[type=submit]")]
      .filter(visible).slice(0, 120).map(el => clean(el.innerText || el.value || el.getAttribute("aria-label")))
      .filter(Boolean);
    const headings = [...document.querySelectorAll("h1, h2, h3")].filter(visible).slice(0, 30)
      .map(h => clean(h.innerText)).filter(Boolean);
    send({ k: "page", why, title: document.title, headings, fields, buttons });
  }

  let lastHref = location.href;
  setInterval(() => {
    if (location.href !== lastHref) {
      lastHref = location.href;
      setTimeout(() => snapshot("page changed"), 1500);
    }
  }, 1000);

  chrome.runtime.onMessage.addListener(msg => { if (msg.snapshot) snapshot("asked"); });

  chrome.storage.local.get("rec", r => {
    rec = r.rec;
    if (window === window.top) setTimeout(() => snapshot("page loaded"), 1500);
  });
  chrome.storage.onChanged.addListener(ch => {
    if (!ch.rec) return;
    const was = live();
    rec = ch.rec.newValue;
    if (!was && live() && window === window.top) snapshot("recording started");
  });
})();
