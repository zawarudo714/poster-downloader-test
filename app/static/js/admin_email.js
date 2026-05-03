/* Email config page — handles the SEND TEST button.
   Note: the button posts current saved settings (NOT the unsaved form
   values) so admin should click SAVE SETTINGS first if they've edited
   anything. We surface that as inline help text on hover. */

(function () {
  const btn = document.getElementById('email-test-btn');
  const status = document.getElementById('email-test-status');
  if (!btn) return;

  btn.addEventListener('click', async () => {
    status.textContent = 'Sending…';
    status.className = 'muted mono';
    btn.disabled = true;
    try {
      const r = await fetch('/admin/email/send_test', { method: 'POST' });
      const data = await r.json().catch(() => ({}));
      if (r.ok) {
        status.textContent = `✓ Sent to ${data.sent_to}`;
        status.className = 'mono ok';
      } else {
        status.textContent = `✗ ${data.detail || ('Failed (' + r.status + ')')}`;
        status.className = 'mono error';
      }
    } catch (e) {
      status.textContent = `✗ ${e.message}`;
      status.className = 'mono error';
    } finally {
      btn.disabled = false;
    }
  });
})();
