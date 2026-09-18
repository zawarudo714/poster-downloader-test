/* Chat page JS (shared between admin and worker views).
   Reads its config from data-* attributes on #chat-stream:
     data-worker-id   = numeric worker id (the thread)
     data-viewer-role = "admin" | "worker"
   The admin endpoints differ from the worker ones, so we map per-role. */

(function () {
  const stream = document.getElementById('chat-stream');
  if (!stream) return;
  const workerId    = parseInt(stream.getAttribute('data-worker-id') || '0', 10);
  const viewerRole  = stream.getAttribute('data-viewer-role') || 'worker';
  const inputEl = document.getElementById('chat-input');
  const formEl  = document.getElementById('chat-form');
  const nameEl  = document.getElementById('chat-with-name');

  if (!workerId) return;

  // URL builders — admin endpoints are nested under /admin, worker uses /api/chat.
  function urlPoll(after) {
    if (viewerRole === 'admin') {
      return `/admin/api/chat/${workerId}?after=${after || 0}`;
    }
    return `/api/chat?after=${after || 0}`;
  }
  function urlSend() {
    return viewerRole === 'admin'
      ? `/admin/api/chat/${workerId}/send`
      : `/api/chat/send`;
  }
  function urlMarkRead() {
    return viewerRole === 'admin'
      ? `/admin/api/chat/${workerId}/mark_read`
      : `/api/chat/mark_read`;
  }

  let highestId = 0;       // last message id we've shown
  let myRoleMatch = viewerRole;  // for "is this MY message" comparison: "admin" or "worker"

  // Where an uploaded image is fetched from — the role-scoped serve route.
  // A pasted link (m.image_url) is loaded directly instead.
  function imageSrc(m) {
    if (m.image_url) return m.image_url;
    if (m.image_upload) {
      return viewerRole === 'admin'
        ? `/admin/api/chat/image/${m.id}`
        : `/api/chat/image/${m.id}`;
    }
    return null;
  }

  // ── Render ──────────────────────────────────────────────────────────────
  function appendMessage(m) {
    const isMine = (m.sender_role === myRoleMatch);
    const wrap = document.createElement('div');
    wrap.className = 'chat-msg ' + (isMine ? 'chat-msg-mine' : 'chat-msg-theirs');
    wrap.dataset.msgId = m.id;
    // The moment this message was written, in the same UTC shape the
    // server reports read-times in — what the "Seen" line compares against.
    if (m.created_at_iso) wrap.dataset.createdIso = m.created_at_iso;
    const bubble = document.createElement('div');
    bubble.className = 'chat-msg-bubble';
    // Text first, if any — an image-only message has an empty body.
    if (m.body) {
      const t = document.createElement('div');
      t.className = 'chat-msg-text';
      t.textContent = m.body;
      bubble.appendChild(t);
    }
    // Then the image, if any. Click to open it full in a new tab.
    const src = imageSrc(m);
    if (src) {
      const a = document.createElement('a');
      a.href = src; a.target = '_blank'; a.rel = 'noopener';
      const img = document.createElement('img');
      img.className = 'chat-img';
      img.src = src;
      img.loading = 'lazy';
      img.alt = 'shared image';
      a.appendChild(img);
      bubble.appendChild(a);
    }
    const meta = document.createElement('div');
    meta.className = 'chat-msg-meta mono muted';
    meta.textContent = (isMine ? 'you · ' : (m.sender_role + ' · ')) + m.created_at;
    wrap.appendChild(bubble);
    wrap.appendChild(meta);
    stream.appendChild(wrap);
    if (m.id > highestId) highestId = m.id;
  }

  function clearLoadingHint() {
    const hint = stream.querySelector('.empty-hint');
    if (hint) hint.remove();
  }

  // ── The "Seen" line — admin view only ───────────────────────────────────
  // Instagram-style: ONE small line, under the LAST of my messages the
  // worker has read. The server reports the worker's read-time on every
  // poll, so the line appears the moment they open the chat — no new
  // message needed to carry it. Comparing the two ISO strings works
  // because both are UTC in the same shape.
  function renderSeen(seenIso, seenLabel) {
    if (viewerRole !== 'admin' || !seenIso) return;
    const old = stream.querySelector('.chat-seen');
    let target = null;
    stream.querySelectorAll('.chat-msg-mine').forEach((el) => {
      if (el.dataset.createdIso && el.dataset.createdIso <= seenIso) target = el;
    });
    if (!target) { if (old) old.remove(); return; }
    if (old && old.previousElementSibling === target) {
      old.textContent = 'Seen ' + (seenLabel || '');
      return;
    }
    if (old) old.remove();
    const el = document.createElement('div');
    el.className = 'chat-seen mono muted';
    el.textContent = 'Seen ' + (seenLabel || '');
    el.title = 'The worker had the chat open after this message.';
    target.insertAdjacentElement('afterend', el);
  }

  // ── Polling ─────────────────────────────────────────────────────────────
  let polling = false;
  async function poll() {
    if (polling) return;
    polling = true;
    try {
      const r = await fetch(urlPoll(highestId), { cache: 'no-store' });
      if (!r.ok) return;
      const data = await r.json();
      if (data.messages && data.messages.length) {
        clearLoadingHint();
        const wasAtBottom = isAtBottom();
        data.messages.forEach(appendMessage);
        if (wasAtBottom) scrollToBottom();
      } else if (highestId === 0) {
        clearLoadingHint();
        if (!stream.querySelector('.chat-msg')) {
          stream.innerHTML = '<div class="empty-hint">No messages yet — say hello!</div>';
        }
      }
      // Mark read after we've seen everything.
      if (data.messages && data.messages.length) {
        try { await fetch(urlMarkRead(), { method: 'POST' }); } catch (e) {}
      }
      // Place (or move) the worker's "Seen" line — sent on every poll,
      // whether or not any new message came with it.
      renderSeen(data.worker_seen_at_iso, data.worker_seen_at);
    } catch (e) {
      // network blip — try again next tick.
    } finally {
      polling = false;
    }
  }

  function isAtBottom() {
    return (stream.scrollHeight - stream.scrollTop - stream.clientHeight) < 60;
  }
  function scrollToBottom() {
    stream.scrollTop = stream.scrollHeight;
  }

  // ── Attach an image: a file OR a pasted link ─────────────────────────────
  const attachBtn   = document.getElementById('chat-attach-btn');
  const attachRow   = document.getElementById('chat-attach-row');
  const fileInput   = document.getElementById('chat-file');
  const linkInput   = document.getElementById('chat-link');
  const attachName  = document.getElementById('chat-attach-name');
  const attachClear = document.getElementById('chat-attach-clear');

  function clearAttach() {
    if (fileInput) fileInput.value = '';
    if (linkInput) linkInput.value = '';
    if (attachName) attachName.textContent = '';
    if (attachRow) attachRow.hidden = true;
  }
  if (attachBtn && attachRow) {
    attachBtn.addEventListener('click', () => {
      attachRow.hidden = !attachRow.hidden;
      if (!attachRow.hidden && linkInput) linkInput.focus();
    });
  }
  if (fileInput) {
    fileInput.addEventListener('change', () => {
      const f = fileInput.files[0];
      // A file and a link are two answers to one question; picking a file
      // clears any half-typed link so only one is ever sent.
      if (f && linkInput) linkInput.value = '';
      if (attachName) attachName.textContent = f ? f.name : '';
    });
  }
  if (attachClear) attachClear.addEventListener('click', clearAttach);

  // ── Send ────────────────────────────────────────────────────────────────
  if (formEl) {
    formEl.addEventListener('submit', async (e) => {
      e.preventDefault();
      const body = (inputEl.value || '').trim();
      const file = fileInput && fileInput.files[0];
      const link = (linkInput && linkInput.value.trim()) || '';
      // Nothing to send is nothing to do — text, a file or a link is enough.
      if (!body && !file && !link) return;
      inputEl.disabled = true;
      const fd = new FormData();
      fd.append('body', body);
      if (file) fd.append('file', file);
      else if (link) fd.append('image_url', link);
      const r = await fetch(urlSend(), { method: 'POST', body: fd });
      inputEl.disabled = false;
      if (r.ok) {
        inputEl.value = '';
        clearAttach();
        await poll();
        scrollToBottom();
        inputEl.focus();
      } else {
        const data = await r.json().catch(() => ({}));
        alert('Send failed: ' + (data.detail || r.status));
      }
    });
    // Enter to send, Shift+Enter for newline.
    inputEl.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        formEl.requestSubmit();
      }
    });
  }

  // Set the chat title on admin side (look up from sidebar).
  if (nameEl) {
    const active = document.querySelector('.chat-thread-row.active .chat-thread-name');
    if (active) {
      // First text node only (ignore the badge inside).
      const txt = (active.firstChild && active.firstChild.nodeType === 3) ? active.firstChild.textContent.trim() : active.textContent.trim();
      nameEl.textContent = txt;
    }
  }

  // Kick off
  poll().then(scrollToBottom);
  setInterval(poll, 6000);
})();
