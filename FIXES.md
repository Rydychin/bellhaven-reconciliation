# Submission fixes

This patch changes source code only. The installer checks the original source
hashes, backs up overwritten files, and preserves .env and the data directory.

## P1: Regressions reopen

Applied decisions no longer resolve the old erroneous state. A recurrent finding
gets a new review version, allowing another decision without overwriting history
or colliding with the SQLite primary key. Exact rejections/no-change decisions
remain recognized. Applied absence flags are checked against their approved result.

## P1: Fresh evidence before approval

Before the first write of an operation, the application runs the complete read-only
pipeline. A failed scrape/download stops approval. The current finding must match
the prepared evidence and the proposal must still be the latest prepared decision.
New decisions supersede prior prepared decisions for that item. Interrupted CHOW
and duplicate work continues through its original recovery plan instead.

## P2: Uncertain-write recovery

Run: python -m streamlit run recovery_app.py --server.address 127.0.0.1

This screen sends GET requests only and records a local recovery event. An exact
approved update or uniquely referenced creation can be marked applied. An unchanged
update can return to prepared for explicit approval with fresh checks. Conflicting
results stay blocked. The existing CHOW and duplicate apps resume their own work.

If a timed-out POST has no visible account, absence does not prove it failed.
The API has no documented idempotency key or operation-status endpoint, so the
screen intentionally does not resend an ambiguous creation. Investigate the
server outcome; never reset the SQLite row to force a second POST.

## P2: Care offerings

Creation and CHOW share care mapping. Assisted living and memory care are supported
as well as nursing. For multiple or unrecognized offerings, the reviewer explicitly
selects one supported primary CRM label. All original offerings remain in the new
account's note. No invented multi-value care_type encoding is sent to the API.

## Validation

Run: python -m unittest discover -v
Run: python verify_submission.py
Run: python daily_pipeline.py

The verification script is a checkpoint for the completed exercise, not a generic
validator of every future account count. The patch preserves your existing audit
history. Fill in actual working time in README.md yourself.

Tests use synthetic data; the existing CRM snapshot verifier can run offline.
There are 22 unit tests including the five existing queue tests. Keep FIXES.md
with README.md so reviewers can see the added recovery workflow and limitations.
