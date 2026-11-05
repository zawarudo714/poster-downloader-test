// Behaviour test for extensions/upload_recorder/background.js: it runs the real
// file against a fake chrome whose storage outlives each service worker.
// Run from the repo root:  node tools/test_upload_recorder.js
const vm = require("vm"), fs = require("fs"), path = require("path");
const src = fs.readFileSync(path.join(process.argv[2] || path.join(__dirname, "..", "extensions", "upload_recorder"), "background.js"), "utf8");
const store = {};
let fails = 0;
const ok = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); if (!c) fails++; };
function boot() {
  const L = {};
  const on = name => ({ addListener: f => { (L[name] = L[name] || []).push(f); } });
  const chrome = {
    storage: { local: {
      get: async k => { if (k === null) return JSON.parse(JSON.stringify(store));
        const out = {}; for (const x of [].concat(k)) if (x in store) out[x] = JSON.parse(JSON.stringify(store[x])); return out; },
      set: async o => { Object.assign(store, JSON.parse(JSON.stringify(o))); },
      remove: async ks => { for (const k of ks) delete store[k]; } },
      onChanged: on("changed") },
    runtime: { onMessage: on("msg") }, commands: { onCommand: on("cmd") },
    action: { setBadgeText() {}, setBadgeBackgroundColor() {} },
    tabs: { query: async () => [] },
    webNavigation: { onCommitted: on("nav"), onHistoryStateUpdated: on("hist") },
    webRequest: { onBeforeRequest: on("req"), onCompleted: on("done"), onErrorOccurred: on("err") },
  };
  const ctx = { chrome, console, URL, TextDecoder, TextEncoder, Promise, setTimeout };
  vm.createContext(ctx); vm.runInContext(src, ctx);
  const msg = m => new Promise(r => { const async = L.msg[0](m, {}, r); if (!async) setTimeout(r, 20); });
  return { ctx, L, msg };
}
const wait = ms => new Promise(r => setTimeout(r, ms));
const evs = () => Object.keys(store).filter(k => k.startsWith("ev_")).sort().map(k => store[k]);
(async () => {
  let w = boot();
  ok(w.ctx.siteOf("creators.juniqe.com") === "juniqe.com", "a subdomain belongs to the same site");
  await w.msg({ add: { k: "click" } }); await wait(30);
  ok(evs().length === 0, "nothing is saved before Start");
  ok((await w.msg({ do: "start", url: "chrome://newtab/" })).error, "Start on a page with no website is refused");
  await w.msg({ do: "start", url: "https://creators.juniqe.com/upload?x=1&token=abc" });
  ok(store.rec.on && store.rec.site === "juniqe.com", "Start records the site of the open page");
  ok(evs()[0].url.includes("token=%5Bhidden%5D") && evs()[0].url.includes("x=1"), "a token in the address is hidden, other values kept");
  for (let i = 0; i < 20; i++) w.msg({ add: { k: "click", i } });
  await wait(100);
  const n = evs().map(e => e.n);
  ok(n.length === 21 && new Set(n).size === 21 && n.join() === [...Array(21)].map((_, i) => i + 1).join(),
     "20 clicks at once are all saved, numbered 1 to 21 with no clash");
  await w.msg({ do: "next" });
  // The service worker goes to sleep: a new one starts from storage only.
  w = boot();
  await w.msg({ add: { k: "click", after: "restart" } }); await wait(30);
  const last = evs().at(-1);
  ok(last.after === "restart" && last.design === 2 && last.n === 23, "after a restart it carries on: design 2, step 23");
  // Network: the site's own requests and uploads it starts elsewhere; not other sites.
  const req = (id, url, initiator, type, body) => {
    w.L.req[0]({ requestId: id, timeStamp: 1000, requestBody: body });
    w.L.done[0]({ requestId: id, url, initiator, type, method: "POST", statusCode: 200, tabId: 3, timeStamp: 1250 });
  };
  req("a", "https://api.juniqe.com/designs", "https://creators.juniqe.com", "xmlhttprequest",
      { raw: [{ bytes: new TextEncoder().encode(JSON.stringify({ title: "Vatican City", password: "x", tags: ["rome"] })).buffer }] });
  req("b", "https://bucket.s3.amazonaws.com/up", "https://creators.juniqe.com", "xmlhttprequest", { raw: [{ bytes: new ArrayBuffer(500000) }] });
  req("c", "https://www.google-analytics.com/collect", "https://www.othersite.com", "xmlhttprequest");
  req("d", "https://creators.juniqe.com/logo.png", "https://creators.juniqe.com", "image");
  req("e", "https://creators.juniqe.com/login", "https://creators.juniqe.com", "xmlhttprequest", { formData: { email: ["a@b.c"], password: ["hunter2"] } });
  await wait(100);
  const net = evs().filter(e => e.k === "net");
  ok(net.length === 3, `the site's requests and the upload it started are kept, others skipped (${net.length})`);
  const a = net.find(e => e.url.includes("/designs"));
  ok(a && a.body.json.title === "Vatican City" && a.body.json.password === "[hidden]" && a.ms === 250, "a JSON body is kept with the password hidden, and the time taken");
  ok(net.find(e => e.url.includes("s3")).body.bytes === 500000, "a big upload is recorded by size only");
  ok(!JSON.stringify(store).includes("hunter2"), "a password sent in a form is never saved");
  await w.msg({ do: "stop" });
  const before = evs().length;
  await w.msg({ add: { k: "click" } }); await wait(30);
  ok(!store.rec.on && evs().length === before && evs().at(-1).k === "stop", "Stop saves a stop step, then nothing more");
  await w.msg({ do: "start", url: "https://creators.juniqe.com/x" });
  ok(store.rec.design === 2 && evs().length === before + 1, "Start again on the same site carries on the same recording");
  await w.msg({ do: "clear" });
  ok(evs().length === 0 && store.seq === 0 && !store.rec.on, "Delete removes every step and switches off");
  console.log(fails ? `${fails} FAILED` : "ALL BACKGROUND CHECKS PASSED");
  process.exit(fails ? 1 : 0);
})();
