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
The ZIP case waits for real worker completion rather than sleeping or executing a
task inline. Large tree/history fixtures use real API writes; their pagination is
verified through the rendered UI and the actual bounded requests.

Failure traces and screenshots are written to `test-results/`; the HTML report is
in `playwright-report/`. Both directories are ignored by Git. CI uploads them and
container diagnostics on failure. They contain only synthetic test account data.
Use `npm run test:e2e:report` to inspect a local report.
