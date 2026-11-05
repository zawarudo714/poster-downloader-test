<!-- Moved out of CLAUDE.md on 2026-11-02 (TOKEN-SAVER tidy), text unchanged. -->

## FineArtAmerica behaviour — `MEASURED` (do not re-derive these)

**There are THREE upload forms live at the same time** — `updateartwork
.html`, `2025` and `2026` — chosen per request, apparently at random.
`MEASURED 2026-09-06` from saved copies of 2025 and 2026, and the pattern
across redesigns is now clear: **the FIELDS keep their names; the
CONTAINERS around the buttons churn.** `artworkname`, `artworkkeywords`,
`artworkdescription` are identical on all three. The submit ANCHOR is the
same `a.buttonSubmit` on 2025 and 2026 — what 2026 removed was the
`submittopdiv` wrapper our old selector reached the button THROUGH. Point
selectors at the control's own name or class, never at the box it sits in.
Since v158 a selector holds `||`-separated alternatives, and the shipped
`submit_button` default covers all three forms. Observed 2026-08-17 (the
original two-form incident): the movie project failed three times in a row
while MUSIK succeeded on the same account minutes later, which looked
exactly like a per-project bug and was not.

Consequences:

  * A missing form field is NOT proof the form changed. It is usually "this
    request got the other page". That is why `upload_pause_after_failures`
    exists — an account is only parked after a RUN of them.
  * When a selector times out, the error lists every field name on the page,
    so the fix is copy-and-paste rather than reading saved HTML.
  * The real fix, not yet built: let a selector hold alternatives, so one
    account can straddle both form versions unattended.

**FAA challenges the Linux server as a bot, but not the Windows node.**
Reading the control panel with a plain HTTP client returns "Verify Visitor —
Are you human? Please check the box in order to continue", regardless of
headers or which URL is used. The node does not see this, because it arrives
in a real Chrome carrying the account's own profile — the one that cleared
the challenge once and holds the cookie. This is why earnings reading is a
node job. Do not try to solve it with headers, a different HTTP client, or a
guessed endpoint; all three were tried and cost three deploy cycles.

**The Balance page is a running ledger and its own checksum.** 78 rows for
this account = 70 sales + 8 payouts, and gross sales $1,477.21 − payouts
$1,178.93 = $298.28, which is exactly the Current Balance FAA prints at the
top. If our arithmetic does not land on their figure, we have missed rows.

**Payouts are on the 15th, for orders SHIPPED before the 15th of the previous
month** — stated on the Balance page. Ship dates are not published, so this
file used to say the next payout could not be computed. **Measured, that was
too pessimistic.** Across his eight payouts, each one equals the balance left
standing after the PREVIOUS one, to the cent — 7 transitions out of 7. Nothing
is held back for late shipping; the whole month moves as one block, a month
late. So `due_next = their balance − everything credited since the last
payout` ($183.78 of the $298.28 balance, the rest in October).

That is a pattern in one account's history, not a rule they state, so
`_payout_rule_holds()` re-tests it on every render and the screen only makes
the claim while it still passes. `Current Balance` remains the authoritative
"what you are owed" — never replace it with arithmetic, which is how the page
once read "probably $1,477.21" against a real $298.28.


**An upload that is not published leaves nothing behind.** The image is
saved to the shop only when PUBLISH is pressed; leaving the submit page
saves nothing, even though the page already has an artwork number in its
address (`updateartwork2026.html?uniqueid=…`). Owner-reported 2026-11-05,
after 117 uploads failed on that page: no unfinished uploads in the shop.

**The upload limit is not a strict 100 in 24 hours.** `MEASURED 2026-11-05`:
FineArtAmerica accepted 201 uploads on one account within about 14 hours
(100 on 4 Nov 07:57–08:44 UTC, 101 on 4 Nov 21:01–21:48 UTC), then logged
the account out ("logged out due to activity"); every later submit stayed
on the form. Which clock or window it uses is still unmeasured.
