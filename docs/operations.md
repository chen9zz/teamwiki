# 运行与维护

## Compose

```bash
cp .env.example .env
# 修改 POSTGRES_PASSWORD、DATABASE_URL 中的相同密码与 JWT_SECRET。
docker compose up --build -d
docker compose ps
docker compose logs -f api worker beat
```

打开 <http://localhost:8080>。`migrate` 成功后 API、Worker、Beat 才会启动；只应保留一个 Beat 实例。Nginx 代理同源 API，浏览器无需跨域 Cookie。开发默认 HTTP；公开部署应在入口配置 TLS，设 `ENVIRONMENT=production`（强制 Secure Cookie），并将 `ALLOWED_ORIGINS` 精确设为实际 HTTPS 域名。

创建演示数据：

```bash
python3 scripts/seed.py --url http://localhost:8080
```

脚本通过公开 API 创建四个演示账户和演示空间，重复执行不会重复建样例文档。默认密码 `DemoPassword123!`，可用 `SEED_PASSWORD` 覆盖。请只在本地演示环境使用。

停止服务保留数据库：`docker compose down`。`docker compose down -v` 会删除 PostgreSQL 和 Redis 数据卷。

## 原生开发

安装 Python 3.12+、Node 22.12+、PostgreSQL 17 和 Redis 7，然后在项目根目录：

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.lock
.venv/bin/pip install --no-deps -e ./backend
export DATABASE_URL='postgresql+asyncpg://teamwiki:your-password@127.0.0.1:5432/teamwiki'
export REDIS_URL='redis://127.0.0.1:6379/0'
export CELERY_BROKER_URL='redis://127.0.0.1:6379/1'
export CELERY_RESULT_BACKEND='redis://127.0.0.1:6379/2'
export JWT_SECRET='local-development-secret-at-least-32-characters'
export ALLOWED_ORIGINS='["http://localhost:5173","http://127.0.0.1:5173"]'
cd backend
../.venv/bin/alembic upgrade head
../.venv/bin/uvicorn app.main:app --reload --port 8000
```

另开终端，在相同环境变量下分别启动 Worker 和 Beat：

```bash
cd backend
../.venv/bin/celery -A app.tasks.worker:celery_app worker --loglevel=INFO --concurrency=2
../.venv/bin/celery -A app.tasks.worker:celery_app beat --loglevel=INFO --schedule=/tmp/teamwiki-beat
```

前端在 `frontend/` 运行 `npm ci && npm run dev`，打开 <http://localhost:5173>。Vite 将 `/api` 转到本机 8000。环境变量属于各自终端，不会自动继承到新终端。

默认连接池、超时、过载响应和私有指标见 [Docker 运维配置](docker.md)；本机已配置的便携 Docker 启动方法也见该文档。

## v0.3 浏览器验收与升级

v0.3 的 `0002_page_tree_order` 仅添加页面树复合索引，不改变已有业务字段或已完成导入任务；从 0002 回退到 0001 只移除该索引。ZIP 新任务开始生成目录页，已完成的旧平铺导入不会被自动搬动。当前 `head` 已包含 v0.4 的 0003 回填，升级应按下一节停写操作。

真实浏览器测试先启动完整的已编译Compose栈，再在frontend目录运行`npm ci`、`npx playwright install --with-deps chromium`与`E2E_BASE_URL=http://127.0.0.1:8080 npm run test:e2e`。请使用独立Compose项目/数据库；一个worker，无response mock。失败trace/截图/HTML产物由Git忽略，CI自动上传诊断。更多见[E2E说明](../frontend/e2e/README.md)。

## v0.3 → v0.4 升级

`0003_chinese_search` 会在事务中增加分词字段、回填既有页面并建立 GIN 索引。它不是在线无锁迁移：耗时随页面数变化，旧 API/Worker 也不会维护新字段，因此升级期间必须停写。先完成镜像构建并备份，再停止 API、Worker 和 Beat；不要在迁移期间启动旧版本写入者。以下命令沿用现有 `.env` 与数据卷，数据库名/账户如有自定义需同步替换：

```bash
# 在已经取到 v0.4 源码的项目目录，先构建，减少停机时间。
docker compose build
mkdir -p work/backups
umask 077
docker compose exec -T postgres pg_dump -U teamwiki -d teamwiki -Fc > work/backups/pre-v0.4.dump
# 等待/检查进行中的导入，再安排维护窗口停止所有写入者。
docker compose stop api worker beat
docker compose run --rm --no-deps migrate
docker compose up -d --wait --wait-timeout 180
curl --fail http://localhost:8080/api/ready
```

备份应验证可读并定期在独立库演练恢复。失败时保留备份和数据卷，不要反复尝试旧服务写入新模式；先检查迁移日志。回退到 `0002_page_tree_order` 仅移除新增分词字段/索引，保留原始正文和英文向量，仍须停写并使用匹配的应用版本。迁移不会自动改造已完成的旧导入任务。

v0.4 的便携 ZIP 仅用于活跃页面迁移，不包含账户、成员、历史或审计，不能代替 `pg_dump`。导出是有限大小的同步快照下载，忙时可按 `Retry-After` 重试；超过格式上限须拆分数据或后续扩展后台导出能力。

## 数据库迁移与备份

应用只使用迁移后的模式。升级前备份数据库，运行 `alembic upgrade head`；不要让多个实例同时执行迁移。迁移回退会删除相应结构，初始迁移 `downgrade base` 会清空全部业务表，仅适用于可丢弃测试库。

```bash
docker compose exec -T postgres pg_dump -U teamwiki -d teamwiki -Fc > teamwiki.dump
# 在停写并准备好恢复目标后：
docker compose exec -T postgres pg_restore -U teamwiki -d teamwiki --clean --if-exists < teamwiki.dump
```

数据库是数据权威。Redis 缓存可过期/丢弃，broker 丢失的排队任务由数据库的 Outbox 和滞留作业恢复逻辑补投。监控失败 ImportJob、长时间未发布 Outbox、Worker 错误和 PostgreSQL 连接池，勿把 `/api/health` 的进程存活检查当作完整依赖检查。

## 验证与压测

后端测试应指向独立 PostgreSQL 测试库，测试会清空业务表：

```bash
export TEST_DATABASE_URL='postgresql+asyncpg://teamwiki:your-password@127.0.0.1:5432/teamwiki_test'
# 预先另建独立的空库；迁移测试会 downgrade base，切勿使用上面的业务测试库。
export MIGRATION_TEST_DATABASE_URL='postgresql+asyncpg://teamwiki:your-password@127.0.0.1:5432/teamwiki_migration_test'
export REDIS_URL='redis://127.0.0.1:6379/13'
cd backend
DATABASE_URL="$TEST_DATABASE_URL" ../.venv/bin/alembic upgrade head
../.venv/bin/pytest -q
```

`MIGRATION_TEST_DATABASE_URL` 必须指向单独、可销毁的 PostgreSQL 库；该测试会删除其全部业务表，建立旧版夹具，再验证升级回填、降级及再升级。未配置时该用例跳过，不能把跳过视作迁移已验证。普通 `TEST_DATABASE_URL` 与 Redis DB 也必须专供测试，不能复用演示/生产数据。

前端：`cd frontend && npm test && npm run typecheck && npm run build`。真实浏览器流程见上文，需完整 PostgreSQL/Redis/Worker/Beat 栈。CI 使用 PostgreSQL、Redis 服务及独立迁移测试库，执行迁移、测试以及迁移回退再升级。

2026-10-29 本地 v0.4 验证为后端 **198 项通过、无跳过**，前端 **30 项通过**，真实 Chromium **7 项通过**；还运行了类型检查、生产构建及编译产物 DOM 回归。详情与边界见 [v0.4 验证记录](v0.4.md)，这些是当前测试结果，不能当作生产容量承诺。

压测先运行 seed，随后：

```bash
.venv/bin/locust -f scripts/locustfile.py --headless -u 10 -r 2 -t 30s \
  --host http://localhost:8000 --csv work/locust
DATABASE_URL='postgresql+asyncpg://...' .venv/bin/python scripts/explain_search.py \
  --workspace WORKSPACE_UUID --query TeamWiki
```

Locust 会创建带 `load-test` 标签的测试页，并验证旧版本保存确实返回 409；期望的 409 计为成功。输出是这次运行的结果，不能作为通用容量承诺。EXPLAIN 的 `--force-index` 只用于观察小数据集的索引路径，正常生产查询应让优化器选择计划。

