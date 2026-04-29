/* Tooltip — event-delegated. Reads `data-tip` from any element on
   mouseenter/focusin and shows a positioned card. Hides on mouseleave/blur/Esc.
   Touch: tap to toggle; tap elsewhere to close. */
(function () {
  const tip = document.getElementById('tooltip');
  if (!tip) return;

  let currentTarget = null;

  function show(el) {
    const text = el.getAttribute('data-tip');
    if (!text) return;
    currentTarget = el;
    tip.textContent = text;
    tip.hidden = false;
    position(el);
  }

  function hide() {
    tip.hidden = true;
    currentTarget = null;
  }

  function position(el) {
    const r = el.getBoundingClientRect();
    // First, render to get tooltip dimensions, then offset.
    tip.style.left = '-9999px';
    tip.style.top  = '-9999px';
    tip.hidden = false;
    const tr = tip.getBoundingClientRect();
    let left = r.left + (r.width / 2) - (tr.width / 2);
    let top  = r.bottom + 6;
    // Flip up if it would overflow bottom of viewport.
    if (top + tr.height > window.innerHeight - 4) {
      top = r.top - tr.height - 6;
    }
    left = Math.max(6, Math.min(left, window.innerWidth - tr.width - 6));
    tip.style.left = left + 'px';
    tip.style.top  = top + 'px';
  }

  // Pointer (mouse) events
  document.addEventListener('mouseover', (e) => {
    const el = e.target.closest && e.target.closest('[data-tip]');
    if (el) show(el);
  });
  document.addEventListener('mouseout', (e) => {
    const el = e.target.closest && e.target.closest('[data-tip]');
    if (el && el === currentTarget) hide();
  });

  // Keyboard focus
  document.addEventListener('focusin', (e) => {
    const el = e.target.closest && e.target.closest('[data-tip]');
    if (el) show(el);
  });
  document.addEventListener('focusout', (e) => {
    const el = e.target.closest && e.target.closest('[data-tip]');
    if (el && el === currentTarget) hide();
  });

  // Escape dismisses
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') hide();
  });

  // Touch — tap to toggle
  document.addEventListener('click', (e) => {
    const el = e.target.closest && e.target.closest('[data-tip]');
    if (el) {
      if (currentTarget === el) hide();
      else show(el);
    } else if (currentTarget) {
      hide();
    }
  });

  // Reposition on scroll/resize while visible
  ['scroll', 'resize'].forEach((evt) =>
    window.addEventListener(evt, () => {
      if (currentTarget) position(currentTarget);
    }, { passive: true })
  );
})();
