# Findings
- Empty requested directory exists at /path/to/teamwiki; no existing repository found.
- Node v26 available; default Python 3.9; bundled Python runtime available.
- SQLAlchemy 2.0 version-counter mechanism does not apply to bulk updates. Use explicit UPDATE ... WHERE id AND workspace_id AND version RETURNING, within the same transaction as revision/link/audit writes.
- PostgreSQL FTS expression must specify text configuration consistently with GIN index. Use stored weighted simple-config vector, with documented CJK limitations.
- Celery late acknowledgement requires idempotent consumers; Outbox dispatch is at least once and duplicates must be harmless.
- Official references: https://docs.sqlalchemy.org/en/20/orm/versioning.html ; https://www.postgresql.org/docs/current/textsearch-tables.html ; https://docs.celeryq.dev/en/stable/userguide/tasks.html
- Runtime established: PostgreSQL17.11 compiled in work/runtime listens127.0.0.1:55432; Redis7.4.7 compiled listens127.0.0.1:56379. Local trust is only ephemeral development, Compose password config remains default deliverable.
- FastAPI>=0.121 dependency scope='function' ensures transaction commit completes before sending success. A regression test verifies IntegrityError at commit becomes409.
- All writes affecting pages, membership and job consumption acquire same workspace lock. This intentionally serializes workspace writes for deterministic tree/link consistency; reads remain concurrent. Perpage CAS still uses atomic WHERE version conditional UPDATE.
- Tags stored as validated per-page JSON list rather than separate taxonomy tables (no independent tag-management requirements).
- Imports bounded atomic transaction gives queued(0/N)->completed(N/N) visibility; intermediate per-file progress remains uncommitted.

2026-10-28 v0.3: current ZIP import is flat despite UI wording; full import uses one atomic transaction. Frontend currently fetches at most 5000 pages and 200 revisions once. Existing CI is green on c2831b8.

V0.4 decisions: Chinese/mixed queries use frozenjieba0.42.1 document/query normalization and GIN; new index bounded to32768bodychars,8192terms/96KiBperfield,128UTF8bytes/term, with visibletruncatedflag. Englishlegacyvector remains unchanged; exactPG54000tsvector overflow is nowAPI413 with transactionrollback, not unclassified500. Portable ZIP preservesmetadata and internalbindings with newlyallocatedIDs; explicit targets outsidearchive become newunresolvedidentities, while undeclared titlelinks use existingdestination-workspace rules. Offsets share the exactparser to protectcode/multiline edge cases. No newS3/Celeryexportservice; synchronous boundeddownload takesRRsnapshot then compressesoffloop with2-per-process admission.
