const $ = id => document.getElementById(id);
const ask = msg => chrome.runtime.sendMessage(msg);

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function show() {
  const { rec = {}, seq = 0 } = await chrome.storage.local.get(["rec", "seq"]);
  const on = !!rec.on;
  $("state").textContent = on ? "Recording on " + rec.site : "Off";
  $("state").className = on ? "on" : "";
  $("info").textContent = seq
    ? seq + " steps saved" + (rec.site ? " · design " + (rec.design || 1) + " · " + rec.site : "")
    : "Nothing recorded yet.";
  $("toggle").textContent = on ? "Stop recording" : (seq ? "Carry on recording" : "Start recording");
  $("toggle").className = on ? "on" : "";
  for (const id of ["next", "addNote", "snap"]) $(id).disabled = !on;
}

function done(r) {
  $("msg").textContent = r && r.error ? r.error : "";
  return show();
}

$("toggle").onclick = async () => {
  const { rec = {} } = await chrome.storage.local.get("rec");
  if (rec.on) return done(await ask({ do: "stop" }));
  const tab = await activeTab();
  done(await ask({ do: "start", url: tab && tab.url }));
};

$("next").onclick = async () => done(await ask({ do: "next" }));

$("addNote").onclick = async () => {
  const text = $("note").value.trim();
  if (!text) return;
  $("note").value = "";
  done(await ask({ do: "note", text }));
};

$("snap").onclick = async () => {
  const tab = await activeTab();
  if (tab) chrome.tabs.sendMessage(tab.id, { snapshot: true }).catch(() => {});
  setTimeout(show, 300);
};

$("save").onclick = async () => {
  const all = await chrome.storage.local.get(null);
  const events = Object.keys(all).filter(k => k.startsWith("ev_")).sort().map(k => all[k]);
  if (!events.length) return done({ error: "Nothing recorded yet." });
  const file = { saved: new Date().toISOString(), recording: all.rec, steps: events.length, events };
  const blob = new Blob([JSON.stringify(file, null, 1)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  const stamp = new Date().toISOString().slice(0, 16).replace(/[T:]/g, "-");
  a.download = "upload_recording_" + ((all.rec && all.rec.site) || "site") + "_" + stamp + ".json";
  a.click();
};

$("clear").onclick = async () => {
  if (!confirm("Delete every recorded step? Save the recording file first if you need it.")) return;
  done(await ask({ do: "clear" }));
};

chrome.storage.onChanged.addListener(show);
show();
