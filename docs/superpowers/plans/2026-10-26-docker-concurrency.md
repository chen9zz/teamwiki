# TeamWiki Docker and concurrency implementation plan

**Goal:** verify Docker delivery and remove measured concurrency bottlenecks without sacrificing isolation/history/link correctness.
**Spec:** docs/superpowers/specs/2026-10-26-docker-concurrency.md
**Global constraints:** modularmonolith, Python>=3.12, PostgreSQL authoritative; immutable revision snapshots, tenant authorization on every API; no unverified metrics; migrations for anyschema change.

- [x] Docker/runtime task: portable engine attempt, Composecache/broker split,2APIworkers, health/resourceconfig,CI andactualcontainer smoke.
- [x] Runtime task: test boundedpool pressure503 andRedis reuse/cleanup; implementconfig,operational metrics andlifespan; runownedtests.
- [x] Page/index task: failingtests sharedlock ordinarysave, exclusive structuralwrites, independent sourceindex; implementlockclassification andincrementalsourceindex; rerunPGsuite.
- [x] Evidence task: baseline workload, identicalupgradedworkload, latency/status/throughput reports; recommendation and sizingdoc.
- [x] Final integration: freshreview,fullbackend+frontend+migration+container checks,README/OpenAPIupdate; synchronizecanonicalrepo only ifbaselineunchanged; sourcearchive.
