# Docker delivery and operating limits

## Start and verify

Docker Engine and the Compose plugin are required. This deployment was verified on Linux ARM64. The Dockerfiles do not force an
architecture, but AMD64 still needs its own build verification. On macOS, a
Docker-compatible runtime such as Docker Desktop, Colima or Lima is required.
Run from the repository root:

```sh
cp .env.example .env
# Replace POSTGRES_PASSWORD in both places and replace JWT_SECRET with random values.
docker compose config --quiet
docker compose up --build -d --wait --wait-timeout 180
python3 scripts/seed.py --url http://127.0.0.1:8080
python3 scripts/docker_smoke.py --url http://127.0.0.1:8080
```

Open <http://127.0.0.1:8080>. Demo accounts are `owner@teamwiki.dev`,
`admin@teamwiki.dev`, `editor@teamwiki.dev` and `viewer@teamwiki.dev`, with password
`DemoPassword123!`; override `SEED_PASSWORD` for both commands to use a different
password. Seeding is explicit and is never run automatically on startup.

The smoke checks the built frontend, readiness, demo workspace, revision history,
stale-save rejection, cross-tenant denial and a real asynchronous Markdown import.
The import is queued transactionally by the API, dispatched by the single beat
scheduler and consumed by a Celery worker. It therefore checks all runtime roles.
The CI `containers` job runs the same commands on a fresh stack and always removes
its test volumes. It supplements the PostgreSQL-backed backend tests and frontend
checks. Both frontend Dockerfiles and the frontend CI job also run
`npm run test:production`: this builds the actual production JavaScript and
executes the compiled document → CodeMirror editor → Markdown preview flow in a
DOM with API responses stubbed. This checks lazy module initialization that a
successful build or an HTTP request for `index.html` alone cannot prove.

## Portable runtime available on this machine

The delivered project at `/path/to/teamwiki` also has an ignored
`work/docker-runtime/` directory containing a project-local Docker CLI, Compose,
Colima and Lima. It does not require a global Docker installation or a shell
profile change. From that project directory:

```sh
./work/docker-runtime/with-colima.sh start --activate=false
./work/docker-runtime/with-docker.sh compose up -d --wait
./work/docker-runtime/with-docker.sh compose ps
# Stop only this project's VM when finished:
./work/docker-runtime/with-colima.sh stop
```

The actual VM disks and Docker named volumes live in
`/path/to/teamwiki/work/docker-runtime/lima`, outside temporary storage. The
runtime helpers use that persistent directory and a private project-local Docker
configuration. Existing demo data was verified after stopping the VM, moving its
sparse disks to this directory and restarting it. Local `.env` permissions are
0600; it is excluded from Git and source packages. These machine-local tools and
VM disks are also excluded from the downloadable source package. Other machines
can use their own Docker Engine/Compose installation with the standard commands
above.

## Process and connection budget

The default is two Uvicorn API workers and two Celery prefork children. Each API
process has a SQLAlchemy pool of five connections and at most five overflow
connections, so the API ceiling is `API_WORKERS × (DB_POOL_SIZE + DB_MAX_OVERFLOW)`
= 20 PostgreSQL connections. API overload stops waiting after two seconds.
Celery runs each task in a fresh event loop and disposes its engine after the task;
concurrency two plus the beat dispatch/recovery tasks run in those same children and must be included in
the worker connection budget. PostgreSQL is capped at 80 connections, leaving operating room
for migrations and administration. Increase process counts only after load tests
and a corresponding review of this budget.

`DB_STATEMENT_TIMEOUT_MS=5000` and `DB_LOCK_TIMEOUT_MS=2000` bound slow database
work. `REQUEST_MAX_INFLIGHT=64` is per API process, so two workers admit at most 128
concurrent application requests; overload returns 503 with `Retry-After`.
Nginx connects within three seconds, sends within 15 and waits at most 30 seconds
for a response. Imports return a job immediately, so they do not require a long
HTTP timeout. Uploaded request bodies are limited to 10 MiB at the gateway.

## Private services and storage

Only Nginx publishes a port, bound to `127.0.0.1:8080`. PostgreSQL, both Redis
instances and all Python roles share an internal Docker network and publish no
host ports. Put a TLS reverse proxy in front of this loopback port before external
access, use `ENVIRONMENT=production`, configure real allowed origins and HTTPS
cookies, and create non-demo accounts. `/internal/metrics` is explicitly blocked
by Nginx; inspect metrics from the private API network instead. API worker counters
are aggregated with Prometheus multiprocess storage under `/tmp/teamwiki-metrics`;
the API parent clears that directory once before starting workers. This setting
is intentionally absent from worker/beat services.

The page cache is a separate `redis-cache` instance, capped at 128 MiB with
`allkeys-lru` and no persistence. Cache loss degrades performance but does not
change the source of truth. Readiness remains successful when only the optional
cache is unavailable. The broker/result instance `redis-broker` uses AOF with
`appendfsync everysec`, a 192 MiB dataset cap and `noeviction`, so cache churn
cannot evict queued tasks. Its container has additional memory headroom for AOF
rewrites and allocator overhead. A full broker refuses new writes; monitor this
condition and size storage/memory for queue depth. AOF every second can lose up to
roughly one second of Redis writes after abrupt host failure; the PostgreSQL
outbox and stale-job recovery supply import delivery retries and idempotency.

Named volumes `postgres_data` and `redis_broker_data` survive ordinary restarts.
Back up PostgreSQL and retain an off-host restore test. `docker compose down`
keeps data; `docker compose down --volumes` intentionally deletes the local
stack's data. Run exactly one beat scheduler. Horizontal API scaling requires
shared PostgreSQL and both shared Redis roles; tenant locks and version checks
are database-backed and work across processes.

The backend runs as UID 10001 with dropped capabilities and no new privileges.
Container memory/CPU limits bound this small-machine starting configuration;
they are a starting budget, not a throughput promise. Images use supported major
release tags; rebuild with `--pull` for updates and pin approved digests for a
release process that requires bit-for-bit image identity.

## Troubleshooting

```sh
docker compose ps -a
docker compose logs --tail=150 api worker beat migrate
docker compose exec -T redis-cache redis-cli INFO memory
docker compose exec -T redis-broker redis-cli INFO persistence
docker compose exec -T postgres psql -U teamwiki -d teamwiki -c \
  'select application_name, state, count(*) from pg_stat_activity group by 1, 2;'
```

Migration failure prevents API, worker and beat startup. Database failure changes
`/api/ready` to 503. Optional cache failure is reported as degraded readiness.
Nginx limits login/registration to ten requests per minute per address with a
burst allowance for normal demo setup. A 429 response should be retried later.

## Validation observed on 26 October 2026

Both images were built and the complete stack ran on an ARM64 Linux VM on macOS,
using a task-local Colima 0.10.3 / Lima 2.2.1 runtime. Docker Engine 29.5.2 and
Compose 5.6.1 executed the deployment. The host's existing native PostgreSQL,
Redis and API services were left running separately.

Observed checks passed: migration startup, two API worker processes, two Celery
children, built frontend, demo seed, revision/stale-save checks, tenant denial,
and beat-dispatched worker imports. Stopping the cache produced HTTP 200 readiness
with `cache: degraded` while page reads still succeeded. An import queued with
worker/beat stopped completed after restarting the broker and resuming them.
Nginx syntax, authentication rate limiting (429), backend UID 10001, private
network and Redis eviction/persistence policies were checked directly. The CI
container job is configured with the same smoke, but a remote GitHub Actions run
is separate from this local evidence.

The final 0.2.0 image was rebuilt after connection-refusal and database DNS-failure
handling were corrected. With the container PostgreSQL stopped, readiness and
three authenticated page-list requests returned 503 with `Retry-After: 1`;
starting PostgreSQL restored both readiness and authenticated reads. A concurrent
read probe exercised both API workers, whose page-read counters (21 and 23)
aggregated to 44, with no requests left inflight. The running OpenAPI document
reported version 0.2.0 and 25 paths. The sanitized record is
[`verification-artifacts/docker-v0.2.json`](verification-artifacts/docker-v0.2.json),
with accompanying image-build and startup transcripts in the same directory.


## 本次机器上的持久 Docker 运行时

为了不修改系统级安装，Docker CLI、Compose、Colima、Lima 及VM磁盘实际保存在 `/path/to/teamwiki/work/docker-runtime`；这是Git忽略的本机运行目录，不随源码包分发。Lima状态已从临时目录迁移到此处，迁移前的PostgreSQL/Redis卷数据及已完成导入均验证保留，不再依赖系统临时目录。`.env`为仅本人可读写的0600文件，源码包只包含`.env.example`。

在这台机器使用：

```sh
cd /path/to/teamwiki
./work/docker-runtime/with-colima.sh status
# VM停止时先启动：
./work/docker-runtime/with-colima.sh start
./work/docker-runtime/with-docker.sh compose ps
./work/docker-runtime/with-docker.sh compose up -d --wait --wait-timeout 180
```

原生开发服务及数据库未被覆盖；8080容器应用使用独立的数据库卷。删除本机`work/docker-runtime`会删除VM及其中的容器卷，清理前先导出数据库备份。在其他机器安装标准Docker环境后，直接使用本文开头的`docker compose`命令。
