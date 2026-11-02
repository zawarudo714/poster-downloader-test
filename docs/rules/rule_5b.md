<!-- Moved out of CLAUDE.md on 2026-11-02 (TOKEN-SAVER tidy), text unchanged. -->

### 5b. A lesson from one marketplace is not a rule about all of them

Rule 6 asks whether a change is right for a third PROJECT. This asks the same
about a third MARKETPLACE, and it is easier to get wrong because the lesson
usually arrives as a genuine discovery.

The incident: his old TeePublic tool proved that never opening the sign-in
page is what avoids a security check. Correct — and applied to every earnings
read, including FineArtAmerica, which drops its session almost immediately.
FAA would have failed as "signed out" on the very first read and parked
itself for twelve hours. The owner caught it in one line, before deploy:
*"this is for teepublic only yes? faa almost always requires log in."*

The untested assumption was that a behaviour measured on one site describes
"how marketplaces work". Two sites here behave OPPOSITELY: one keeps you
signed in for weeks and punishes knocking, the other forgets you in minutes
and does not mind. Either single answer is wrong somewhere.

So when a discovery changes how we talk to a marketplace:

1. **Say which site it was measured on.** One site's behaviour is one site's.
2. **Put it in that site's CAPABILITIES row**, not in the node and not in an
   if-statement. `signin_on_read` sits beside `shape` and `sales` for the
   same reason those do — the next marketplace answers by adding a line.
3. **A capability that is gone should be DELETED, not disabled.** When
   TeePublic dropped Cloudflare the whole challenge mechanism went with it,
   because nothing else was using it — FAA challenges the Linux server, never
   the node. A defence left lying around is worse than none: the next session
   reasons about protection that was never firing. (I argued to keep it "for
   FAA" and was wrong; the measurement was already written down two sections
   up. Read the file before defending code with it.)
4. **Pick the default that fails cheaply.** An unmeasured site signs in:
   signing in needlessly costs a slow page, while skipping it when it was
   needed stops the money being read. Wrong in the cheap direction.

The general form: **a fact discovered about one external system is DATA about
that system, never policy for all of them.** Same shape as rule 6, one level
out — and the same shape as `processor` and `has_year`, which exist so a
project's differences are data rather than a template edit.

