# FineArtAmerica Bulk Delete — Console Script

## Context

FineArtAmerica's artwork manager has no native bulk-delete option. Each item is
deleted one at a time via a modal (pencil edit icon → Delete → confirm), which
triggers a plain browser navigation to:

```
https://fineartamerica.com/profiles/<your-username>?tab=artwork&deleteartworkid=<ID>&page=1
```

There is no AJAX/XHR involved — the "Delete" link's `href` is literally
`javascript: confirmdelete(<ID>);`, which shows a `confirm()` popup and then
navigates to the URL above. Visiting that URL directly (e.g. via `fetch`)
deletes the artwork and returns the refreshed profile page HTML, without
needing to interact with any button or popup.

Each artwork in the grid always uses the newest upload in position 0, so the
approach is: read the ID currently in position 0, delete it, re-read the new
position 0 from the response, repeat.

## How the ID is exposed in the DOM

Each thumbnail's edit control looks like this:

```html
<div class="imageeditdiv" onclick="javascript: editimage(70544168,0);" ...>
```

- First number (`70544168`) = artwork ID
- Second number (`0`) = position in the grid

The script below extracts the ID from the **first** `.imageeditdiv` element on
the page (i.e. whatever is currently in position 0).

## The script

Run this in Chrome DevTools → **Console** tab, while the artwork list page is
loaded (`fineartamerica.com/profiles/<username>?tab=artwork`).

```javascript
(async function bulkDelete() {
  const DELAY_MS = 1500;       // pause between deletes — keep this gentle
  const MAX_DELETES = 1;       // safety cap; raise once verified working
  let deletedCount = 0;
  let doc = document;

  function getNextArtworkId(doc) {
    const el = doc.querySelector('.imageeditdiv[onclick*="editimage("]');
    if (!el) return null;
    const match = el.getAttribute('onclick').match(/editimage\((\d+),/);
    return match ? match[1] : null;
  }

  let id = getNextArtworkId(doc);

  while (id && deletedCount < MAX_DELETES) {
    console.log(`Deleting artwork ID ${id}  (deleted so far: ${deletedCount})`);
    const url = `https://fineartamerica.com/profiles/elton-odhiambo?tab=artwork&deleteartworkid=${id}&page=1`;
    const res = await fetch(url);
    const html = await res.text();
    doc = new DOMParser().parseFromString(html, 'text/html');
    deletedCount++;
    id = getNextArtworkId(doc);
    await new Promise(r => setTimeout(r, DELAY_MS));
  }

  console.log(`Done. Deleted ${deletedCount} artwork(s).`);
})();
```

**Note:** the profile URL (`elton-odhiambo`) is hardcoded in the script — swap
that if the username ever changes.

## How to run it

1. Open DevTools → **Console** tab (not Elements/Network) on the artwork list page.
2. Paste the script. If Chrome blocks pasting with a security warning, type
   `allow pasting` into the console, press Enter, then paste again.
3. Set `MAX_DELETES = 1` first and run it, to confirm one item deletes correctly.
4. Manually refresh the page (F5) and confirm that item is actually gone —
   don't just trust that the script logged success.
5. Once confirmed, bump `MAX_DELETES` to however many you want removed
   (e.g. `100`) and re-run.
6. To stop mid-run, refresh the page — this kills the running script.

## If the site's HTML changes later

If this script stops working, the most likely cause is FineArtAmerica changing
their page structure. To adapt it:

1. Open DevTools → **Elements** tab, hover the pencil/edit icon on the first
   (newest) artwork thumbnail, and find the element with an `onclick` or
   similar attribute referencing the artwork ID (previously
   `editimage(<ID>, <position>)`).
2. Update the `querySelector` and regex in `getNextArtworkId()` to match the
   new attribute/format.
3. Re-verify the delete URL still works the same way: open DevTools →
   **Network** tab, check **Preserve log**, manually click through
   Edit → Delete → confirm on one item, and see what URL/request actually
   fires. Update the `url` template in the script if it has changed
   (e.g. a different query param name, or a POST instead of GET).
4. Re-test with `MAX_DELETES = 1` before running at scale again.
