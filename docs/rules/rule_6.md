<!-- Moved out of CLAUDE.md on 2026-11-02 (TOKEN-SAVER tidy), text unchanged. -->

### 6. The third-project test

For every change: **would this still be right for a third project?** If the
answer needs "well, for MUSIK…", it is not finished. See `MULTIPROJECT.md`.

**And the same question about FIELDS, because that is where it keeps
costing money: can the thing this column names ever be plural, and can the
thing this status describes fail in more than one way?** `project_id` on an
account, one `paused_until` for uploading and for reading money, one
`action_error` for switching off and switching back on — three columns that
were each correct for their first user and wrong for their second, and each
one hid a real fault while looking fine. A minute spent on this at the time
is a session saved later. This is planned item 10.

**Also, and this only became true recently: would this be right on 201,133
rows?** The database is no longer a toy, and a query with no filter is now a
full scan on every page load. See planned item 9.

