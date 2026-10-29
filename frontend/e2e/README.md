# Real browser regression

The suite uses Chromium against the **compiled** frontend and the actual API,
PostgreSQL, Redis, Celery worker and Beat. Start the complete Compose stack first;
there is no Vite server, API interception or response mocking in this suite.

```sh
npm ci
npx playwright install --with-deps chromium
E2E_BASE_URL=http://127.0.0.1:8080 npm run test:e2e
```

`E2E_BASE_URL` points to the frontend's same-origin API proxy. Each worker creates
a unique test account, and every test creates a unique workspace through the API.
The browser signs in through the UI. The suite never needs demo credentials or
preexisting pages. Run against a disposable Compose project/database and remove
its volumes afterwards; the tests do not delete or alter demo data.

The production authentication rate limit applies. Keep one worker (configured)
and avoid repeatedly launching fresh runs in a tight loop against the same proxy.
The seven cases make ten authentication requests (the worker registration, eight
owner/viewer browser logins and a viewer registration). The CI seed/smoke steps
use eleven more. Run the complete stack checks once and keep the real production
limit enabled; frequent manual reruns should use a fresh disposable stack or
wait for the configured rate limit to recover.
The ZIP cases wait for real worker completion rather than sleeping or executing a
tasks inline. Large tree/history fixtures use real API writes; their pagination is
verified through the rendered UI and the actual bounded requests.

Failure traces and screenshots are written to `test-results/`; the HTML report is
in `playwright-report/`. Both directories are ignored by Git. CI uploads them and
container diagnostics on failure. They contain only synthetic test account data.
Use `npm run test:e2e:report` to inspect a local report.

The v0.4 case adds two-character Chinese and mixed Chinese/English queries, opens
a result in the reader, downloads the TeamWiki ZIP through the format picker,
and imports that exact file through the UI into another unique workspace. It
checks titles, tags, parent/child placement, new IDs, title/ID links and isolation
from an existing page with the same title. Inline ID examples remain literal.
The original six regression cases remain in the suite.

The archive picker offers **TeamWiki 完整格式** for metadata-preserving transfer
and **纯 Markdown** for legacy flat Markdown files. Both are bounded downloads
in the current request; imports continue through the real asynchronous worker.
Only the production DOM smoke uses API stubs. Browser cases never use request
routing, mocks or fixed sleeps.

For the macOS development environment with its old global proxy disabled, keep
proxy/cache changes local to the command:

```sh
npm ci --proxy=null --https-proxy=null --cache=/private/tmp/teamwiki-v04-npm-cache
PLAYWRIGHT_BROWSERS_PATH=../../.cache/ms-playwright E2E_BASE_URL=http://127.0.0.1:8091 npm run test:e2e
```

Use the existing Playwright 1.64.1 Chromium cache and its configured `chromium`
channel. macOS Chromium requires permission to launch outside the restricted
command environment; no global npm proxy setting needs to change.
