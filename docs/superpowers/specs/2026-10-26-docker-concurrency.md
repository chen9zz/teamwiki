# Docker and concurrency hardening

User requested actual Docker integration, technical-stack completion and implementation of a recommended high-concurrency approach, with autonomous reasonable defaults. Baseline clean commit0abea29. Existing actual PostgreSQL/Redis/API remain available while upgrade is built in an isolated clone.

## Recommendation and alternatives
Keep the modular monolith. Use2 Uvicorn workers; perprocess bounded PostgreSQL pools (5+5) and event-loop scoped Redis pools (20); Redis cache separated from durable broker; measured overload responses rather than unbounded waiting. Use shared workspace locks for ordinary page edits and exclusive locks for structural/member changes. Reindex only the edited source on common saves. Continue synchronous transactionally consistent links, revisions, audit and CAS.

An asynchronous link Outbox could reduce save latency further but requires version-aware consumers and an explicit stale-read contract; defer until measured need. Microservices, Kubernetes, a search cluster, Redis clustering and PgBouncer would introduce operational cost before this application's measured capacity needs them.

## Concurrency invariants
- Always membership check before data exposure; recheck after acquired lock to cover revocation.
- A normal edit holds Workspace FOR SHARE until commit; multiple unrelated normal edits overlap. Structural page metadata/create/delete/restore and membership changes use Workspace FOR UPDATE. Lock order workspace before job/page.
- Determine ordinary versus structural using requested values and observed page metadata. Reject stale observed version rather than upgrading shared to exclusive in the same transaction. CAS remains UPDATE WHERE workspace_id+id+version, so one same-version winner persists.
- Normal edits replace outgoing links only for their source; preserve bindings for unchanged raw targets. Structural changes retain exclusive workspace reconciliation for target rename/delete/restore/title ambiguity, but load only page metadata plus source content when needed.
- Keep deleted source bindings for undelete; backlinks/graph exclude deleted sources.
- Unrelated PageLink IDs must survive normal save. Parser must not reparse unrelated bodies. Cache hits should not queue on miss singleflight lock.

## Docker and operational contract
Compose includes migrated PostgreSQL, eviction-bounded cache Redis, persistent noeviction broker Redis, API,worker,beat,Nginx/frontend; onlyfrontend publisheslocalhost8080.2APIworkers cap20 database connections +worker/beat/migrations margin, withinpostgres100. /internal/metrics backend-only with bounded route labels; ready checks DBandcache. Production HTTPS and randomsecret remain required. CI builds and exercises actualcompose. Local engine provision officialDocker-compatibleruntime only, no system security bypasses.

## Evidence
Test-first shared/exclusive contention, samepageCAS and revision atomicity, title/body rename races, permission revocation and source index scope; existing tests remain green. Benchmark baseline/upgraded identical dataset and APIworker counts, separately report independent writes, reads and hotpage expected409s. Preserve rawresults and avoid productioncapacity claims. Record actualcontainer/CI environmental failures explicitly.
