# Bellhaven CRM Reconciliation

## Purpose

Reconcile Bellhaven's published community inventory with the CRM.
A reviewer inspects evidence and approves changes before API writes.
The daily pipeline only reads sources and generates findings.

## Setup

Requires Python 3.13 and the packages in requirements.txt.

Create and activate an environment:

    python3 -m venv .venv
    source .venv/bin/activate
    python -m pip install -r requirements.txt

Create .env:

    CRM_BASE_URL=https://analyst-assessment-production.up.railway.app
    CRM_API_TOKEN=YOUR_PERSONAL_TOKEN

The token is excluded from version control.

## Run the daily pipeline

    python daily_pipeline.py

The pipeline scrapes the website, downloads all CRM pages, runs matching,
and summarizes pending reviews and unfinished operations.

schedule.cron contains an example daily 07:15 schedule.
It is configuration only; no live schedule is required.
The configured path must be updated if the project moves.

The pipeline uses a local file lock to prevent overlapping pipeline runs.
It stops if a stage fails and does not perform CRM writes.

## Review and approval

Run one app at a time:

    python -m streamlit run review_app.py --server.address 127.0.0.1

Prepare proposals or record rejection/no-change decisions there.

Use the relevant approval screen:

    python -m streamlit run approve_updates.py --server.address 127.0.0.1
    python -m streamlit run approve_creations.py --server.address 127.0.0.1
    python -m streamlit run approve_chow.py --server.address 127.0.0.1
    python -m streamlit run approve_duplicates.py --server.address 127.0.0.1

Saving a proposal is not approval. The approval screen displays the
operation and requires an explicit approval click before writing.

## Scraping and evidence

The scraper follows all three community-directory pages and community
links on the homepage and other visited pages.

The directory lists 34 communities. A homepage announcement links to
Findlay, producing a complete inventory of 35 communities.

Each location includes its name, street, city, state, ZIP, care offerings,
source URL, and captured page text. Original HTML and hashes are archived.

Count and parsing checks prevent an incomplete scrape from replacing
the last successful location snapshot.

## Matching

The matcher searches the full CRM, including accounts with former names
and accounts assigned to other parents.

It normalizes street suffixes and direction abbreviations. Street, city,
and state agreement supports a strong match. ZIP differences are surfaced
for review. Same-name accounts in different locations are not treated
as matches.

Multiple accounts at one address require duplicate review.
Name/city agreement with a conflicting street requires identity review.
Website absence alone does not establish closure or a new owner.

The exercise's website is treated as the source for the operator
relationship. This does not independently establish legal ownership.

## Corrections performed

- Eleven straightforward account corrections.
- Four missing facility accounts created.
- Two billing-protected CHOW successor accounts created.
- Five duplicate groups resolved, with seven losing copies marked
  Inactive and linked to their survivors.
- Three website-absent accounts marked Needs Review with notes.

The resulting inventory has 34 strong matches and one reviewed
address discrepancy.

## Billing SOP

For a parent change, revenue history and outstanding AR are checked
again against the live account.

If both are positive, the old account retains its original parent,
billing amounts, status, and other business fields. A new Bellhaven
account is created, and only chow_current_account is updated on the old
account. The API may also change updated_at.

Marietta and Tiffin were handled using this workflow.
Revenue and AR were not copied to the new accounts.

## Reviewer judgments

Ashtabula's CRM billing address is a PO Box, while its website lists
a physical address. Its name, city, state, ZIP, and existing Bellhaven
parent support the identity relationship. The billing address was
preserved and a no-change decision recorded.

Alliance, Coldwater, and Sandusky remain ownership-investigation items.
They were flagged Needs Review rather than assumed closed or transferred.

Where duplicate accounts had equivalent billing histories, the survivor
choice was explicitly documented. Existing current Bellhaven records
were preferred when available.

Website care offerings are preserved as evidence. New nursing accounts
use Skilled Nursing for the website's Short-Term Rehabilitation & Nursing
offering. Existing phone numbers and care-type fields were not broadly
synchronized; the work focused on facility identity and parent relationships.

## Persistence and recovery

SQLite stores reviewed evidence, proposed operations, reviewer reasons,
and decision status. Identical findings retain their decisions.
Meaningful evidence changes can return an item for review.

Applied absence flags are recognized by their exact approved result.
Confident matches do not require repeated manual review.

Creation references are included in new-account notes. Uncertain
standalone creations stop for verification rather than being resent.
CHOW records its execution before creation and locates the referenced
new account during recovery. Duplicate recovery checks approved
before/after states before continuing.

Do not delete the SQLite database or reset interrupted operations
to bypass recovery checks.

The API does not provide a transaction spanning multiple account writes.
Live checks reduce conflicts but cannot eliminate concurrent changes
between a read and a write. Use one approval app at a time.

## Validation

Run:

    python -m unittest test_review_queue -v
    python verify_submission.py

Run the daily pipeline twice to check repeatability:

    python daily_pipeline.py
    python daily_pipeline.py

Inspect data/pipeline-summary.json and data/pipeline-runs/.
Repeated unchanged runs should show zero pending reviews,
no unfinished decisions, and zero CRM writes.

The final verifier checks original-account preservation, approved updates,
six creations, seven duplicate links, two CHOW cases, and review flags.

## Submission files and local state

Source code and schedule configuration belong in the submission.
.env must remain private.

data/review.sqlite3 and the evidence snapshots are needed to reproduce
the existing review history. They are excluded from Git by default;
retain them locally for the demonstration. Include sandbox evidence
separately if the submission instructions require it.

## Time and AI assistance

Actual active working time: 1.5hr

AI assistance was used to develop the implementation and review approach.
I ran the tools, inspected the supporting evidence, selected survivors,
and explicitly approved the CRM changes.
