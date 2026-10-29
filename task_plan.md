# TeamWiki implementation

User authorized immediate implementation with sensible defaults; no design approval gate required.

## Phase 1: runnable tenant-aware MVP
Status: complete
- [x] Python/FastAPI, PostgreSQL/Alembic, cookie/JWT sessions, Argon2
- [x] Tenant memberships, invitations, RBAC, protected workspace APIs
- [x] Page tree, CRUD, tags, React workspace/editor/search/members
- [x] Docker Compose startup

## Phase 2: correctness and reliability
Status: complete
- [x] Atomic conditional version updates, immutable revisions and restore
- [x] Stable wiki links/backlinks, ambiguous and unresolved states
- [x] Version-addressed tenant Redis cache, PostgreSQL FTS
- [x] Transactional Outbox, idempotent Celery imports, audit
- [x] Adversarial API integration tests including concurrency

## Phase 3: deployment and demonstration
Status: complete
- [x] Graph, safe Markdown/ZIP import and export, job UI
- [x] CI, Locust, seed, OpenAPI, ER and architecture diagrams
- [x] Real database/runtime/browser checks; record observed results only
- [x] README and verified source copied to /path/to/teamwiki

## Errors / constraints
- System Python is 3.9.6; use bundled Python 3.13 or downloaded runtime.
- Docker is absent; prepare Compose and attempt native PostgreSQL/Redis validation.
- Requested /path/to/teamwiki lies outside default writable roots: stage in task workspace, then copy using authorized escalation.


## Phase4: Docker and concurrency hardening
Status: complete
- [x] Actual persistent Docker engine, image builds, full Compose startup and private network.
- [x] Separate cache/broker Redis, bounded pools/deadlines/admission, multiprocess operational metrics.
- [x] Shared ordinary-save locks, source-only link indexing, permission rechecks and preserved CAS.
- [x]77 real PostgreSQL/Redis tests,13frontend tests, compiled production DOM regression, actual Docker browser acceptance.
- [x] Matched baseline/after concurrency evidence with limitations; Docker fault/recovery and VM persistence verified.
- [x] Source and documentation ready for canonical repository synchronization.

Earlier Docker-absent constraint resolved in0.2.0: persistent task-local Docker-compatible engine and Compose now installed under ignored work/docker-runtime; no systemwide installation or security bypass.


## v0.3 (2026-10-28)
Status: complete
- [x] Hierarchical ZIP import with atomic idempotent folder creation and bounded resources.
- [x] Metadata tree/sibling pagination and history load-more, safe parent selection.
- [x] Playwright real-browser full-stack workflows and CI.
- [x] Full regression, migration and container validation; independent review.
- [x] Release docs, GitHub synchronization and canonical source update.

Environment: native worktree tool rejected sourceRoot because canonical repo is outside the chat workspace list; use an isolated clone under task work/. Existing demo runtime remains untouched.


## v0.4 (2026-10-29)
Status: complete
- [x] Chinese tokenization, transactional index and existing-page backfill.
- [x] Metadata-preserving bounded portable ZIP export/import.
- [x] UI and real browser workflows.
- [x] Full regression, security review and documented concurrency boundaries.
- [x] GitHub CI and canonical/demo synchronization.
