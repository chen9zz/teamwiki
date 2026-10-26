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
