// The subject drawings — City, Mountain, Island and the rest.
//
// These lived inside user.js until 2026-09-10, when the owner asked to see
// the same drawing while REVIEWING a worker's images, not only while finding
// them. Two admin screens now show it too, and three copies of seventeen
// drawings is three chances for one of them to drift — so the set moved into
// this one file and everything reads it from here.
//
// Loaded as a plain global (window.SubjectKind) because this project has no
// build step. Every template that shows a subject loads this file BEFORE its
// own page script.
//
// All seventeen are the same weight of line and all `currentColor`, so the
// page's own colour decides how they look and they read as one family.
// Unknown words fall back to a map pin rather than to nothing: a project
// whose sheet uses different words still gets a sensible picture, and the
// WORD is always shown beside it anyway.
window.SubjectKind = (function () {
  const ICONS = {
    'city':        '<path d="M3 21h18M5 21V9l5-3v15M14 21V4l5 3v14M7.5 12h.01M7.5 15h.01M7.5 18h.01M16.5 10h.01M16.5 13h.01M16.5 16h.01"/>',
    'town':        '<path d="M2 21h20M4 21v-7l4-3 4 3v7M13 21v-9l4-3 4 3v9M7 21v-3h2v3M17 21v-3h2v3"/>',
    'mountain':    '<path d="M2 20h20L14.5 6l-3.5 6-2-3L2 20zM13 9.5l1.5-2.5"/>',
    'island':      '<path d="M12 12v7M12 12c0-2 2-3.5 4-3M12 12c0-2-2-3.5-4-3M12 12c1.5-1.5 3.5-1.5 5 0M12 12c-1.5-1.5-3.5-1.5-5 0M2 20c2 0 2 1.5 4 1.5S8 20 10 20s2 1.5 4 1.5S16 20 18 20s2 1.5 4 1.5"/>',
    'lake':        '<path d="M3 9c1.6-2.6 4.7-4 9-4s7.4 1.4 9 4c-1.6 6-4.7 9-9 9s-7.4-3-9-9zM7 11c1.2 0 1.2 1.2 2.5 1.2S11 11 12.2 11s1.3 1.2 2.5 1.2S16 11 17 11"/>',
    'castle':      '<path d="M3 21h18M4 21V8h2V5h2v3h2V5h4v3h2V5h2v3h2v13M9 21v-5h6v5"/>',
    'protected area': '<path d="M12 3l7 3v6c0 5-3 7.5-7 9-4-1.5-7-4-7-9V6zM12 8v6M12 10c1.5-1.5 3-1 3-1s0 2.5-3 2.5M12 10C10.5 8.5 9 9 9 9s0 2.5 3 2.5"/>',
    'monument':    '<path d="M4 21h16M7 21v-3h10v3M10 18V6l2-3 2 3v12M10 8h4"/>',
    'national park': '<path d="M12 3l4 6h-2.5l3 5H13v7h-2v-7H7.5l3-5H8zM3 21h18"/>',
    'cathedral':    '<path d="M12 2v4M10 4h4M4 21V11l8-5 8 5v10M4 21h16M10 21v-6h4v6M8 12v3M16 12v3"/>',
    'bridge':      '<path d="M2 16h20M2 16c0-6 4.5-9 10-9s10 3 10 9M6 16v-4M12 16V8M18 16v-4M2 20h20"/>',
    'World Heritage Site': '<path d="M12 3a9 9 0 100 18 9 9 0 000-18zM3.5 12h17M12 3c2.5 3 2.5 15 0 18M12 3c-2.5 3-2.5 15 0 18"/>',
    'waterfall':   '<path d="M4 4h16M7 4v10M11 4v12M15 4v10M19 4v12M3 20c2 0 2-1.5 4-1.5S9 20 11 20s2-1.5 4-1.5S17 20 19 20"/>',
    'lighthouse':  '<path d="M9 21h6M9 21l1-11h4l1 11M10 10V7l2-4 2 4v3M9.5 14h5M2 7l4 1M22 7l-4 1M2 13l4-1M22 13l-4-1"/>',
    'cave':        '<path d="M2 21c0-8 4.5-14 10-14s10 6 10 14M9 21c0-4 1.3-6.5 3-6.5s3 2.5 3 6.5M2 21h20"/>',
    'beach':       '<path d="M17 5.5a3 3 0 100 6 3 3 0 000-6zM17 2v1.5M17 13v1.5M21.5 8H20M14 8h-1.5M3 15v6M3 15c-2 0-3 2-3 2M3 15c2 0 3 2 3 2M2 21c2 0 2 1.2 4 1.2M2 21h20"/>',
    'desert':      '<path d="M18 4.5a2.5 2.5 0 100 5 2.5 2.5 0 000-5zM2 20c2.5-5 5-7 7.5-4S14 20 14 20M12 20c1.5-3 3.5-4.5 5-3s3 3 3 3M2 20h20"/>',
  };
  const FALLBACK =
    '<path d="M12 21s7-6.2 7-11a7 7 0 10-14 0c0 4.8 7 11 7 11zM12 13a3 3 0 100-6 3 3 0 000 6z"/>';

  function icon(kind) {
    const paths = ICONS[String(kind || '').trim().toLowerCase()]
               || ICONS[String(kind || '').trim()]
               || FALLBACK;
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
         + 'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
         + paths + '</svg>';
  }

  // Title Case for display. The sheet writes "city" and "protected area" in
  // lower case and "World Heritage Site" already capitalised, so the words
  // are tidied for the screen rather than in the data.
  function words(kind) {
    const raw = String(kind || '').trim();
    if (!raw) return '';
    if (raw === raw.toUpperCase() || /[A-Z]/.test(raw.slice(1))) return raw;
    return raw.replace(/\b[a-z]/g, (c) => c.toUpperCase());
  }

  // The small inline version the admin screens use: the drawing plus the
  // word, sized to sit beside a title. Returns '' for an empty kind so the
  // caller can simply assign it and get nothing rather than a stray pin.
  function chip(kind) {
    const w = words(kind);
    if (!w) return '';
    // The word comes from the owner's own sheet, but it still goes through
    // innerHTML, so angle brackets are neutralised rather than trusted.
    const safe = w.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return '<span class="subject-chip" title="What the worker was asked to '
         + 'photograph — the kind word from your sheet">'
         + icon(kind) + '<span>' + safe + '</span></span>';
  }

  return { icon: icon, words: words, chip: chip };
})();
