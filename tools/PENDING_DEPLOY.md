# Not yet deployed

**v233 — OPEN buttons on duplicate pictures, a PAID band on Worker Images,
and every Diagnostics number now says what it counts.**
- Diagnostics' "identical pictures" line names both titles and gives each
  picture an OPEN button. The button goes to that worker and day on Worker
  Images, with the zoom already open on the picture.
- Diagnostics no longer prints a bare "#142". Title numbers read "title 57"
  and internal numbers read "picture record 142". A new preflight check
  fails if a bare "#number" comes back.
- Worker Images shows a gold PAID band when the whole day is paid. When part
  of the day is unpaid, it lists each unpaid picture with the reason, and a
  click opens that picture. A day with nothing paid shows a small grey
  "NOT PAID YET" line.
- Server only. The Windows node is NOT affected.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
