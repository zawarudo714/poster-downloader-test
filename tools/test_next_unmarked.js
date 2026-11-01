// Run: node tools/test_next_unmarked.js app/static/js/admin_review_images.js  (v290)
// Lifts imageFate, nextUnmarkedIndex and moveToNextUnmarked out of the
// SHIPPED file (never a copy) and drives them with a fake batch.
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
function lift(name) {
  const at = src.indexOf('function ' + name + '(');
  if (at < 0) throw new Error('missing ' + name);
  let i = src.indexOf('{', at), depth = 0;
  for (; i < src.length; i++) {
    if (src[i] === '{') depth++;
    if (src[i] === '}' && --depth === 0) break;
  }
  return src.slice(at, i + 1);
}
let fails = 0;
const ok = (c, m) => { console.log((c ? 'PASS ' : 'FAIL ') + m); if (!c) fails++; };
const toasts = [];
const env = `
  let titles = [], decisions = new Map(), mode = 'all', index = 0,
      advanceLock = false, zoomOpen = false, renders = 0;
  const window = { toast: (m) => toasts.push(m) };
  function render() { renders += 1; }
  function syncZoom() {}
  ${lift('imageFate')}
  ${lift('nextUnmarkedIndex')}
  ${lift('moveToNextUnmarked')}
  return { set: (o) => { titles = o.titles; decisions = o.decisions; index = o.index;
                         advanceLock = !!o.lock; mode = o.mode || 'all'; },
           go: moveToNextUnmarked, at: () => index, renders: () => renders };`;
const S = new Function('toasts', env)(toasts);
const T = (...ids) => ({ images: ids.map((id) => ({ poster_id: id })) });
// Designs 0..5; 0,1,2 marked, 3 marked, 4 bare, 5 bare.
const marked = (...ids) => new Map(ids.map((id) => [id, { action: 'approve' }]));
const titles = [T(1), T(2), T(3), T(4), T(5), T(6)];
S.set({ titles, decisions: marked(1, 2, 3, 4), index: 0 });
S.go(); ok(S.at() === 4, 'from design 1 it skips three marked designs to design 5 (index 4)');
S.go(); ok(S.at() === 5, 'next press goes to the next bare one');
toasts.length = 0;
S.go(); ok(S.at() === 4 && toasts.some((m) => /start/.test(m)), 'past the end it goes round to the start and says so');
// A design with one of two pictures marked still counts as not marked.
S.set({ titles: [T(1), T(2, 3)], decisions: marked(1, 2), index: 0 });
S.go(); ok(S.at() === 1, 'a design with one picture still bare counts');
// LEAVE FOR PHOTOSHOP is a mark.
S.set({ titles: [T(1), T(2), T(3)], decisions: new Map([[1, {action:'approve'}], [2, {action:'photoshop'}]]), index: 0 });
S.go(); ok(S.at() === 2, 'LEAVE FOR PHOTOSHOP counts as a mark and is skipped');
// Nothing else bare: stays put, says so, does not redraw.
toasts.length = 0;
S.set({ titles: [T(1), T(2), T(3)], decisions: marked(1, 3), index: 1 });
const r0 = S.renders(); S.go();
ok(S.at() === 1 && S.renders() === r0 && toasts.some((m) => /No other design/.test(m)),
   'only the design on screen is bare: it stays and says no other design is waiting');
// Photoshop door: silence is "left", so nothing is "not marked".
toasts.length = 0;
S.set({ titles: [T(1), T(2)], decisions: new Map(), index: 0, mode: 'held' });
S.go(); ok(S.at() === 0 && toasts.length === 1, 'in the Photoshop door nothing counts as unmarked');
// The poster-loading lock holds it, like NEXT.
S.set({ titles: [T(1), T(2)], decisions: marked(1), index: 0, lock: true });
S.go(); ok(S.at() === 0, 'while the poster is loading the press is held, as NEXT is');
S.set({ titles: [T(1), T(2)], decisions: marked(1), index: 0 });
S.go(); ok(S.at() === 1, 'control: the same batch unlocked does move');
console.log(fails ? `\n${fails} FAILED` : '\nALL NEXT-NOT-MARKED CHECKS PASSED');
process.exit(fails ? 1 : 0);
