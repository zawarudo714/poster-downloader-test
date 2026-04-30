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

  // ── Render ──────────────────────────────────────────────────────────────
  function appendMessage(m) {
    const isMine = (m.sender_role === myRoleMatch);
    const wrap = document.createElement('div');
    wrap.className = 'chat-msg ' + (isMine ? 'chat-msg-mine' : 'chat-msg-theirs');
    wrap.dataset.msgId = m.id;
    wrap.innerHTML = `
      <div class="chat-msg-bubble"></div>
      <div class="chat-msg-meta mono muted"></div>
    `;
    wrap.querySelector('.chat-msg-bubble').textContent = m.body;
    wrap.querySelector('.chat-msg-meta').textContent =
      (isMine ? 'you · ' : (m.sender_role + ' · ')) + m.created_at;
    stream.appendChild(wrap);
    if (m.id > highestId) highestId = m.id;
  }

  function clearLoadingHint() {
    const hint = stream.querySelector('.empty-hint');
    if (hint) hint.remove();
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

  // ── Send ────────────────────────────────────────────────────────────────
  if (formEl) {
    formEl.addEventListener('submit', async (e) => {
      e.preventDefault();
      const body = (inputEl.value || '').trim();
      if (!body) return;
      inputEl.disabled = true;
      const fd = new FormData();
      fd.append('body', body);
      const r = await fetch(urlSend(), { method: 'POST', body: fd });
      inputEl.disabled = false;
      if (r.ok) {
        inputEl.value = '';
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
