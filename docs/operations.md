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
cd backend
DATABASE_URL="$TEST_DATABASE_URL" ../.venv/bin/alembic upgrade head
../.venv/bin/pytest -q
```

前端：`cd frontend && npm test && npm run build`。CI 使用 PostgreSQL、Redis 服务，执行迁移、测试以及迁移回退再升级。

压测先运行 seed，随后：

```bash
.venv/bin/locust -f scripts/locustfile.py --headless -u 10 -r 2 -t 30s \
  --host http://localhost:8000 --csv work/locust
DATABASE_URL='postgresql+asyncpg://...' .venv/bin/python scripts/explain_search.py \
  --workspace WORKSPACE_UUID --query TeamWiki
```

Locust 会创建带 `load-test` 标签的测试页，并验证旧版本保存确实返回 409；期望的 409 计为成功。输出是这次运行的结果，不能作为通用容量承诺。EXPLAIN 的 `--force-index` 只用于观察小数据集的索引路径，正常生产查询应让优化器选择计划。

