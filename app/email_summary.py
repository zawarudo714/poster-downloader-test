"""
REMOVED — the daily summary email was never used after it was built.

Deleted rather than left switched off, because dead code is worse than no
code: it still has to be read, still shows up in searches for "how does this
app notify anyone", and would have needed reworking for multi-project along
with everything else.

The scheduler tick in backups.py and the /admin/email routes went with it.
Nothing else referenced this module. The `email` column on UploadAccount is
unrelated — that is a marketplace login, not a notification address.

DELETE THIS FILE. It is left here only because the editing tool could not
remove it; `git rm app/email_summary.py` finishes the job.
"""
