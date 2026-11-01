// ═══════════════════════════════════════════════════════════════════════════
//  DELETE EVERY LISTING IN THE SHOP — paste into the browser console (v289)
// ═══════════════════════════════════════════════════════════════════════════
//
// For the owner to run BY HAND, signed in to FineArtAmerica, on any
// fineartamerica.com page. Nothing on the site or the worker machine runs
// this; it deletes real listings and cannot be undone.
//
// HOW TO USE
//   1. Leave MODE = 'PREVIEW' and paste the whole file into the console
//      (F12 → Console). It deletes NOTHING. It lists every listing it would
//      delete and the total — expect 417.
//   2. If the list and the total are right, change MODE to 'DELETE' and
//      paste it again.
//   3. Afterwards run the Listing check on the site. Every one should read
//      Page Removed (410). Do this BEFORE renaming the shop.
//
// WHAT IT WILL DELETE
//   Only tiles whose listing address ends in ADDRESS_ENDS (this shop's own
//   listings) and whose delete number sits in the SAME tile as that
//   address. A tile where the two cannot be paired is listed and left.
//
// HOW IT STOPS
//   * after MAX_DELETES deletions in all;
//   * after 3 failed deletions in a row;
//   * when a round leaves the same listings on the page (the deletes are
//     not taking — something changed on FineArtAmerica's side);
//   * when no page holds a listing to delete.
//
// Based on the owner's own page-by-page script (2026-11-01): the delete is
// `?tab=artwork&deleteartworkid=<id>&page=<page>` on the profile page, and
// the number comes from the tile's `editimage(<id>, <n>)` call.

(async () => {
  const MODE = 'PREVIEW';            // 'PREVIEW' or 'DELETE'
  const PROFILE = '/profiles/illusinationtravelposters';
  const TAB = 'artwork';
  const ADDRESS_ENDS = '-illusination-travel-posterz.html';
  const MAX_DELETES = 420;
  const MAX_PAGES = 15;              // 48 a page: 417 is 9 pages
  const DELAY_MS = 1200;
  const EDIT_ID = /editimage\((\d+)\s*,\s*\d+\)/;

  const base = location.origin + PROFILE;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const pageUrl = (page) => `${base}?tab=${TAB}&page=${page}`;

  // ── Reading one page ────────────────────────────────────────────────
  // Each listing is one `.artworkFlowDiv` tile holding its address and,
  // when signed in, its edit call. Read the raw page first; if the edit
  // calls are not in it (put there by the page's own script), load the
  // page in a hidden frame and read what the browser built.
  function tilesFrom(doc) {
    return [...doc.querySelectorAll('.artworkFlowDiv')].map((tile) => {
      const link = tile.querySelector('a[href*="/featured/"]');
      const href = link ? link.href || link.getAttribute('href') : '';
      const m = tile.outerHTML.match(EDIT_ID);
      const name = (tile.querySelector('.flowdivtitle3 a')?.textContent
                    || link?.querySelector('img')?.getAttribute('alt') || '').trim();
      return { id: m ? m[1] : null, href, name };
    });
  }

  async function readByFetch(page) {
    const r = await fetch(pageUrl(page), { credentials: 'include' });
    if (!r.ok) throw new Error(`page ${page}: HTTP ${r.status}`);
    const doc = new DOMParser().parseFromString(await r.text(), 'text/html');
    return tilesFrom(doc);
  }

  async function readByFrame(page) {
    const frame = document.createElement('iframe');
    frame.style.cssText = 'position:absolute;width:1px;height:1px;left:-9999px;';
    frame.src = pageUrl(page);
    document.body.appendChild(frame);
    try {
      await new Promise((ok, bad) => {
        frame.onload = ok;
        setTimeout(() => bad(new Error(`page ${page}: the frame did not load`)), 30000);
      });
      await sleep(2000);                 // let the page's own script finish
      return tilesFrom(frame.contentDocument);
    } finally {
      frame.remove();
    }
  }

  let useFrame = false;
  async function readPage(page) {
    if (!useFrame) {
      const tiles = await readByFetch(page);
      if (!tiles.length || tiles.some((t) => t.id)) return tiles;
      console.log('The delete numbers are not in the raw page — reading it in a hidden frame instead.');
      useFrame = true;
    }
    return readByFrame(page);
  }

  const ours = (t) => t.href.endsWith(ADDRESS_ENDS);
  const deletable = (tiles) => tiles.filter((t) => ours(t) && t.id);

  // ── PREVIEW: walk every page, delete nothing ────────────────────────
  if (MODE !== 'DELETE') {
    const all = [];
    const unpaired = [];
    const others = [];
    for (let page = 1; page <= MAX_PAGES; page++) {
      const tiles = await readPage(page);
      if (!tiles.length) break;
      tiles.forEach((t) => {
        if (!ours(t)) others.push(t);
        else if (!t.id) unpaired.push(t);
        else if (!all.some((x) => x.id === t.id)) all.push({ ...t, page });
      });
      await sleep(400);
    }
    console.table(all.map((t) => ({ page: t.page, id: t.id, title: t.name })));
    console.log(`PREVIEW — would delete ${all.length} listing(s). Nothing was deleted.`);
    if (unpaired.length) {
      console.warn(`${unpaired.length} of this shop's tile(s) have no delete number in the tile and would be LEFT:`,
                   unpaired.map((t) => t.name || t.href));
    }
    if (others.length) {
      console.warn(`${others.length} tile(s) do not end in "${ADDRESS_ENDS}" and would be LEFT:`,
                   others.map((t) => t.href));
    }
    if (!all.length) {
      console.warn('Found nothing to delete. Are you signed in to this shop? '
                   + 'If the tiles show "Edit" but this still reads 0, stop and tell Claude.');
    }
    console.log("If this is right, change MODE to 'DELETE' and paste the script again.");
    return;
  }

  // ── DELETE: page by page until nothing is left ──────────────────────
  if (!confirm('DELETE every listing in this shop that ends in\n'
               + ADDRESS_ENDS + '?\n\nThis cannot be undone.')) {
    console.log('Cancelled. Nothing was deleted.');
    return;
  }
  let deleted = 0;
  let failedInARow = 0;
  let lastRound = '';
  for (;;) {
    // The first page that still holds one of ours. Page 1 normally; a
    // later page only if something else is sitting in front of them.
    let page = 0;
    let todo = [];
    for (let p = 1; p <= MAX_PAGES; p++) {
      const tiles = await readPage(p);
      if (!tiles.length) break;
      todo = deletable(tiles);
      if (todo.length) { page = p; break; }
    }
    if (!todo.length) {
      console.log(`DONE — ${deleted} deleted. No listing of this shop is left to delete.`);
      break;
    }
    const round = todo.map((t) => t.id).sort().join(',');
    if (round === lastRound) {
      console.error('STOPPED — the same listings are still on the page after a round of deletes. '
                    + `Nothing more was tried. ${deleted} deleted so far.`);
      break;
    }
    lastRound = round;

    for (const t of todo) {
      if (deleted >= MAX_DELETES) {
        console.error(`STOPPED — reached the cap of ${MAX_DELETES}. ${deleted} deleted.`);
        return;
      }
      try {
        const r = await fetch(`${base}?tab=${TAB}&deleteartworkid=${t.id}&page=${page}`,
                              { credentials: 'include' });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        deleted += 1;
        failedInARow = 0;
        console.log(`${deleted}. deleted ${t.id} — ${t.name}`);
      } catch (e) {
        failedInARow += 1;
        console.warn(`could not delete ${t.id} — ${t.name}: ${e.message}`);
        if (failedInARow >= 3) {
          console.error(`STOPPED — 3 failures in a row. ${deleted} deleted so far.`);
          return;
        }
      }
      await sleep(DELAY_MS);
    }
  }
  console.log('Next: run the Listing check on the site. Every one should read Page Removed (410).');
})();
