<!-- Moved out of CLAUDE.md on 2026-11-02 (TOKEN-SAVER tidy), text unchanged. -->

## FineArtAmerica listing pages — `MEASURED 2026-08-24` (do not re-derive)

Measured with `tools/faa_url_check.py` against two real shops. This is what
the reconciliation scanner rests on, so it is written down rather than
rediscovered.

**A listing's public address is derivable — no lookup, no stored ID.**

    https://fineartamerica.com/featured/{title-slug}-{artist-slug}.html

where a slug is the text lower-cased with every non-alphanumeric run turned
into a single hyphen. `"The Killing - 2011 C"` + `"Golden Reel"` gives
`the-killing-2011-c-golden-reel.html`. The `" - "` separator and the `"#"`
in the MUSIK titles both simply dissolve into that; neither needs a case.

**Build the slug from the STORED title, never the one we typed.** FAA
rewrites titles on save, so `clean_for_marketplace()` must run first —
`E.T. - 1982 A` lists as `ET - 1982 A` and therefore lives at `et-1982-a-…`.
Skip that and every accented or punctuated title reads MISSING while sitting
there live. This is the payoff for normalising in `render_remote_title`.

**FAA RETURNS TWO DIFFERENT CODES AND THE DIFFERENCE IS THE WHOLE FEATURE.**
Measured 25 Aug against a listing the owner deleted by hand and the same
address with two extra letters:

    410  "Page Removed"    — it existed and is now deleted
    404  "Page Not Found"  — no page has ever lived at this address

Neither is a 200 carrying an error page, so the check is a status code —
nothing to parse and nothing to break when FAA redesigns.

**410 is a takedown. 404 means OUR ADDRESS IS WRONG.** For something we
believe we uploaded, "no page ever existed here" is a mistyped artist name
or a title that FAA stored differently — not a copyright claim. That makes
the wrong-name guard exact instead of a guess from proportions.

The first version read 404 as "gone" and let 410 fall through to "we could
not look", so REAL removals — the entire point of the feature — were filed
as not-evidence and ignored, while a typo would have been reported as
thousands of takedowns. Both wrong, and the expensive one was wrong in the
direction of silence. **When a site offers two codes, find out what each one
means before collapsing them into one answer.**

**HEAD is honoured**, same statuses as GET. A live page is ~190KB and the
404 page ~128KB, so across 4,811 images that is the difference between
~900MB and almost nothing, and about three and a quarter hours versus one.
Sweep with HEAD; GET only to confirm something surprising.

**THERE IS NO HIDDEN STATE. A listing is live or it is deleted** — the
owner's own words, and it is why this is nothing like the TeePublic tool.
No search-visibility question, no paging, no vague tags, no deactivate/
reactivate cure, nothing that can be left switched off. (Caveat not yet
tested: the Edit page has a Display Options panel. If anything there can
pull an image out of search while its page still loads, it would read as
healthy here. Revisit only if a 200 listing stops earning.)

**The buy panel is on the 404 page too** — FRAMED PRINTS / CANVAS PRINTS /
ART PRINTS, under "click one of the products below to start shopping". It
looks like the obvious positive marker and proves nothing. Use `<title>`,
which reads `{stored title} {medium} by {artist} - Fine Art America`.

**"Not there" and "we could not look" are different answers.** Only 404 is
evidence. 403, 429, 5xx and timeouts mean we were blocked or the site had a
moment; treating those as GONE would report healthy listings as copyright
takedowns. Same shape as the TeePublic header-logo test.

**The LINUX SERVER gets 403 on these PUBLIC pages, and headers do not help.**
Measured 2026-08-24 from 178.105.232.196: plain client, browser User-Agent,
and a full browser header set all returned 403 — for the live URL AND the
deliberately broken one, so from there we cannot even tell live from gone.
The earlier note said FAA challenges the server "for signed-in pages"; that
was too narrow. It challenges it for everything.

Do not spend another cycle on headers, a different HTTP client, or a guessed
endpoint. The same plain client works fine from the laptop, so it is the
ADDRESS being refused, not the request.

**The WINDOWS NODE gets through with a PLAIN HTTP client — no browser
needed.** Measured 2026-08-24: `requests.head()` returned 200 for a live
listing and 404 for a missing one. So the sweep is ~4,811 HEAD requests,
about an hour, no Chrome, no login, no bandwidth worth counting. It is
therefore nothing like the TeePublic scan and must not inherit its shape:
it changes nothing on the marketplace and has no reason to hold the
pipeline. It should still go one CHUNK at a time, because the node runs one
job at a time and a single hour-long job would block Photoshop anyway.

**The node's plain Python cannot do HTTPS at all — use `requests`.**
`urllib` there fails with `CERTIFICATE_VERIFY_FAILED: unable to get local
issuer certificate`, because a stock Windows Python has no CA store;
`requests` carries its own via `certifi`, which is why the agent has never
hit it. The symptom looks like a network fault or a block and is neither.
Never "fix" it by disabling verification — that hides this exact failure and
the node sends real passwords to marketplaces. Same family as rule 9: the
node's environment is whatever the copied folder happened to bring.

**WE DO NOT STORE THE ARTIST NAME, AND THE ADDRESS NEEDS IT.** FAA fills
that field from the account, so the uploader never sets it. It cannot be
derived either — the profile URL is `/profiles/elton-odhiambo` while the
listing slug is `golden-reel`. Each FAA account needs the artist name typed
in once, exactly as FAA holds it.

**INVARIANT: a sweep where nearly everything reads GONE is a broken sweep,
not a catastrophe.** One wrong character in the artist name makes every
listing 404, and the screen would report thousands of copyright takedowns.
Above a small fraction per account, stop and say "the artist name is
probably wrong" instead of presenting findings.

Option deliberately kept open: these are PUBLIC pages and the OWNER'S LAPTOP
fetches them perfectly with no wall at all. A sweep that runs there and
posts results back would never compete with the pipeline. The only cost is
that it runs when the laptop is on.

