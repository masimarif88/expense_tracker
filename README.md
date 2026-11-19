# Expense Tracker API

An async REST API built with Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2,
Alembic, PostgreSQL, and Redis. All application routes are under `/api/v1`.

## Run with Docker Compose

Docker Compose starts PostgreSQL, Redis, runs migrations, seeds example exchange
rates, and starts the API:

```bash
docker compose up --build
```

Open the interactive API docs at <http://localhost:8000/docs>. The health check
is at <http://localhost:8000/health>. Stop the services with `Ctrl+C`; data
persists in the `postgres_data` volume.

For anything other than local development, set a strong `JWT_SECRET` in the
environment before starting Compose. The credentials in the Compose file are
development-only.

## Run the API locally

You can run PostgreSQL and Redis in Compose while running Python on your host:

```bash
docker compose up -d db redis
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Set a private `JWT_SECRET` in `.env`, then apply the schema, seed local rates,
and launch the development server:

```bash
alembic upgrade head
python -m app.seed_rates
uvicorn app.main:app --reload
```

## Tests

The test suite uses a real PostgreSQL database named `expense_tracker_test`;
it does not use SQLite. On a fresh Compose database, the initialization script
creates the test database automatically. Start PostgreSQL and install the
dependencies:

```bash
docker compose up -d db
source .venv/bin/activate
pip install -r requirements.txt
```

Run the complete async HTTPX suite, including migrations and coverage, with one
command:

```bash
pytest
```

`pytest` applies the Alembic migrations to the test database, clears all
application tables and reseeds test exchange rates before each test, and
enforces an 85% coverage minimum. The most recent full run passed **62 tests
with 92% coverage** (92.05% measured). Redis behavior in the tests uses
`fakeredis`; receipt storage is mocked and no S3 calls are made.

The Compose PostgreSQL container listens on port 5432 internally and is
published to host port 15439 by default, avoiding conflicts with a PostgreSQL
server already installed on your machine. The test database URL defaults to
`postgresql+asyncpg://expense:expense@localhost:15439/expense_tracker_test`.
If PostgreSQL is mapped to a different host port, pass the test URL when
running pytest, for example:

```bash
TEST_DATABASE_URL=postgresql+asyncpg://expense:expense@localhost:15439/expense_tracker_test pytest
```

The test harness refuses a database whose name does not contain `test` to
reduce the risk of clearing development or production data.

PostgreSQL connection details for DBeaver:

| Setting | Value |
|---|---|
| Host | `localhost` |
| Port | `15439` |
| Database | `expense_tracker` |
| Username | `expense` |
| Password | `expense` |

These are the local development defaults; use stronger credentials outside a
local machine. Compose publishes Redis at `localhost:16379` by default (Redis
still listens on port 6379 inside its container). For a local Python process,
set `REDIS_URL=redis://localhost:16379/0` in `.env`. To choose different host
ports, set `POSTGRES_HOST_PORT` and/or `REDIS_HOST_PORT` before running
Compose; the container ports remain 5432 for PostgreSQL and 6379 for Redis.

## API outline

The importable Postman collection at
[`postman/Expense-Tracker-API.postman_collection.json`](./postman/Expense-Tracker-API.postman_collection.json)
contains requests for the health check and every `/api/v1` endpoint. Import it
into Postman, set the `baseUrl`, and run Login to populate bearer-token
variables. Set a unique `idempotencyKey` when testing expense creation and
select local files for the CSV and receipt requests.

- `POST /api/v1/auth/register` — create an account.
- `POST /api/v1/auth/login` — return a 15-minute access token and a 7-day
  refresh token.
- `POST /api/v1/auth/refresh` — submit `{"refresh_token":"..."}` to rotate the
  refresh token. Reuse of a revoked refresh token revokes all active sessions
  for that user.
- `POST /api/v1/auth/logout` — submit the current refresh token in the same JSON
  shape to revoke it.
- `POST /api/v1/categories` and `GET /api/v1/categories` — create categories
  and list this-month spend/budget in base currency.
- `POST /api/v1/expenses`, `GET /api/v1/expenses`, and
  `GET /api/v1/expenses/{id}` — create and query expenses.
- `PATCH /api/v1/expenses/{id}` — send the version from the response in
  `If-Match`, for example `If-Match: "1"`. The endpoint returns the updated
  version in both the response and `ETag`. Missing `If-Match` returns 428 and
  an outdated version returns 412.
- `DELETE /api/v1/expenses/{id}` — delete an expense.
- `GET /api/v1/reports/monthly?year=2026&month=10` — report totals in the
  account's base currency, compared with the prior month.
- `POST /api/v1/expenses/import.csv` — import CSV rows, returning imported and
  rejected counts with row-level errors. Required columns are `category_id`,
  `amount`, `currency`, and `spent_on`; `description` is optional.
- `POST /api/v1/expenses/{id}/receipt` — upload a PNG, JPEG, or PDF receipt.
  The API checks file contents and extension instead of trusting the submitted
  MIME type or filename.
- `POST /api/v1/notifications/budget-alerts` — idempotently process a
  budget-alert event and create at most one notification for each user/event.

Protected category and expense endpoints require
`Authorization: Bearer <access_token>`. Expense list filters are
`category_id`, `date_from`, `date_to`, `min_amount`, `max_amount`, `currency`,
and `q`. Pagination uses `limit` (1–100) and the returned `next_cursor`; keep
the same filters when requesting the next page. The signed cursor contains a
creation-time cutoff as well as the keyset position so later inserts don't
change the result set being paged.

Errors use this shape:

```json
{
  "error": {
    "code": "invalid_credentials",
    "message": "Email or password is incorrect",
    "details": []
  }
}
```

The login-failure counter is stored in Redis and shared across API instances.
Five failed attempts are allowed per email in the 15-minute window; blocked
requests include `Retry-After`.

## Retry and concurrent-update guarantees

`POST /api/v1/expenses` accepts an optional `Idempotency-Key`. The database
enforces uniqueness on `(user_id, key)`. The request claims the key with a
PostgreSQL `INSERT ... ON CONFLICT DO UPDATE ... RETURNING` statement, then
inserts the expense and stores the original response in the same transaction.
Concurrent requests with a new key serialize on that unique constraint: one
transaction creates and commits the expense, and the other returns the stored
response. A repeated key with a different validated request body returns 422.
Keys become reusable 24 hours after their first accepted request; an expired
record can be atomically claimed again. This avoids a separate Redis lock and
keeps the key claim, expense, and replay response transactionally consistent.

Expenses use a version-based ETag such as `"1"`. PATCH sends that value in
`If-Match`; the API performs the ownership and version predicates together in
the SQL `UPDATE ... WHERE id = ... AND user_id = ... AND version = ...
RETURNING` statement, while incrementing the version in the same statement.
Consequently, two concurrent PATCH requests with the same ETag can have only
one successful update; the other receives 412 instead of overwriting it.

## Exchange rates and reports

`exchange_rates.rate_to_usd` means currency units per one USD. For instance,
`278` PKR and `3.6725` AED mean one USD is worth those amounts. Conversion from
an expense currency to the account currency uses:

`expense_amount / expense_currency_rate_to_usd * base_currency_rate_to_usd`

The seed command inserts PKR, AED, USD, and EUR rates for the latest 90 days.
The values are fixed demonstration rates, not financial advice or a live feed.
Seeding does not overwrite existing rates; insert/import real daily data into
`exchange_rates` when using this beyond a demo. For an expense date, reports
choose the latest rate on or before that date. If either currency has no
eligible rate, the report returns `exchange_rate_missing` rather than silently
understating totals. Monthly report aggregation, conversion, budget percentage,
and prior-month comparison execute in one SQL statement with `GROUP BY` and
`LAG`.

## Important indexes

- `ix_expenses_user_spent_on_id` supports a user's descending date/id keyset
  listing and ownership filter without offset scans.
- `ix_expenses_user_category_spent_on` supports category-filtered listings and
  date-range lookups such as monthly aggregation.
- `ix_expenses_user_currency` supports filtering expenses by currency.
- `ix_expenses_description_trgm` uses PostgreSQL `pg_trgm` to accelerate
  substring `ILIKE` searches for `q`.
- `uq_categories_user_name` enforces per-user category-name uniqueness and
  supports category lookups by user.
- The primary key on exchange rates is `(currency, valid_on)`, supporting the
  latest-rate-before-date lookup.
- `ix_refresh_tokens_user_active` supports revoking all of a user's live
  refresh tokens after token reuse.

## AWS deployment notes

These are deployment recommendations, not resources created by this repository.
The current app has no background outbox worker and uses an in-memory receipt
adapter; those parts need production implementations before deployment.

### 1. API and worker

- Package the API as an immutable container and run it as an ECS service across
  at least two Availability Zones, behind an Application Load Balancer (ALB).
- Start with two API tasks for availability; use CPU, memory, and request
  latency metrics for target-tracking autoscaling, with sensible minimum and
  maximum task counts.
- Run background consumers as a separate ECS service with its own task count
  and scaling signals (queue depth and oldest-event age); do not run a worker
  in every API process.
- ECS restarts unhealthy or exited tasks. ALB health checks should use a
  readiness endpoint that verifies required dependencies, while liveness
  checks should avoid restarting tasks for transient downstream issues.
- Release by registering a new task-definition revision and using an ECS
  rolling deployment with health checks, connection draining, and a deployment
  circuit breaker/automatic rollback. Keep old and new tasks serving together
  during rollout; avoid destructive schema changes until old tasks are gone.

### 2. Database and secrets

- Use RDS for PostgreSQL in private database subnets, with public accessibility
  disabled, encryption enabled, automated backups, and Multi-AZ for production.
- Allow inbound TCP **5432** on the RDS security group only from the API and
  worker task security groups; do not allow public or developer IP ranges in
  the application database rule.
- Put the database username/password in AWS Secrets Manager (or SSM Parameter
  Store SecureString) and grant the ECS task role permission to read only that
  secret; inject it at task start or retrieve it securely at runtime.
- Never bake plaintext credentials into the image, source tree, task
  definition, or logs; a task definition should contain only the secret
  reference. Rotate secrets with a process that permits old and new credentials
  to overlap during connection-pool replacement.

### 3. Redis

- Use a private ElastiCache for Redis deployment in the VPC, with in-transit
  encryption and authentication enabled where supported by the selected
  deployment mode.
- Allow TCP **6379** only from API task security groups; do not expose Redis
  publicly.
- This code stores login failure counters in Redis. If Redis is unavailable,
  login currently fails closed with **503** instead of silently bypassing the
  distributed rate limit.
- Expense idempotency is stored transactionally in PostgreSQL, not Redis, so
  retries remain safe during a Redis outage. JWT access tokens are validated
  locally; refresh-token revocation state is in PostgreSQL.
- Configure ElastiCache monitoring and alarms, and decide explicitly whether
  rate-limit counters may reset during Redis failover/eviction; for stricter
  guarantees use durable Redis settings and an availability plan.

### 4. S3 receipts and IAM

- Use a private S3 bucket with Block Public Access enabled, TLS-only access,
  encryption at rest, and lifecycle rules for incomplete multipart uploads.
- Give each ECS task an IAM task role; do not put AWS access keys in
  environment variables or application configuration.
- For a server-generated pre-signed **single PUT** URL restricted to the
  `receipts/` prefix, the minimal identity policy is:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:PutObject"],
      "Resource": "arn:aws:s3:::YOUR_PRIVATE_BUCKET/receipts/*"
    }
  ]
}
```

- Replace the bucket placeholder with the deployment bucket. Add narrowly
  scoped `s3:GetObject` only if the application must read objects itself;
  downloading can instead use short-lived pre-signed GET URLs.
- The current receipt endpoint validates file signatures but uses in-memory
  storage. Production should issue short-lived, key-specific upload URLs,
  verify the uploaded object and ownership before persisting its key, and
  never make the bucket public.

### 5. Alembic migrations during releases

- Run migrations as a single ECS one-off release task, not from every API
  replica at startup; make the release pipeline wait for successful completion.
- Use an expand/contract sequence: first add nullable columns/tables/indexes
  that old code tolerates, deploy code that can work with both schemas, then
  backfill and validate data.
- Only in a later release should code start requiring the expanded schema;
  remove old columns or constraints after all old tasks and rollback windows
  have passed.
- Build large indexes with PostgreSQL's concurrent-index strategy in a
  separately managed migration when table locks or write pauses would be risky.
- Make every migration observable and bounded; take backups, alert on failure,
  and stop the rollout if migration health checks fail. Prefer a forward fix
  over an unsafe automatic downgrade after application traffic has changed data.

## Short interview answers

### 1. Pre-signed upload URLs

1. A pre-signed URL lets the client upload directly to S3, avoiding API memory,
   bandwidth, connection, and timeout bottlenecks for large files.
2. The API authenticates first and generates a short-lived URL for one random,
   user-scoped object key; the bucket stays private.
3. A leaked URL is a bearer capability until expiry, so keep expiry short,
   redact it from logs, and never reuse keys.
4. Bind the signature to the expected method, key, content type, and—where
   supported—content length or checksum; enforce upload-size limits.
5. Do not trust the client filename or MIME type: inspect the uploaded bytes
   and confirm the object belongs to the requesting user before attaching it.
6. Clean up abandoned uploads with an S3 lifecycle rule; use multipart
   completion/abort handling for large files.

### 2. Slow expense listing at 20 million rows

1. Reproduce the slow request and inspect `EXPLAIN (ANALYZE, BUFFERS)` with
   representative parameters; check row estimates, actual rows, sort spills,
   heap fetches, and shared reads.
2. Compare the plan with production query frequency and filters, then add or
   adjust a composite index matching the ownership predicate and keyset order:
   `(user_id, spent_on DESC, id DESC)`.
3. For common category/date filters, validate a targeted index such as
   `(user_id, category_id, spent_on DESC, id DESC)` against measured plans.
4. Offset pagination gets slower at deeper pages because the database still
   scans and discards preceding rows; use the existing signed keyset cursor.
5. Check substring search separately; use the existing `pg_trgm` GIN index for
   `ILIKE`, and verify it is selected rather than assuming it helps.
6. Inspect ORM SQL for N+1 queries; use explicit joins/eager loading or
   aggregates in SQL, and avoid per-row follow-up queries.
7. Recheck plans, latency, buffer reads, and write overhead after index
   changes; indexes improve reads but consume storage and slow writes.

### 3. Three API servers behind a load balancer

1. Access JWTs are signed with the same managed secret on every task; refresh
   token state is in PostgreSQL, so rotation/reuse detection is not tied to
   server memory.
2. Login limits use shared Redis counters, not per-process memory; Redis
   failure currently returns 503 for login rather than bypassing the limit.
3. Expense idempotency uses PostgreSQL uniqueness and transactions, so requests
   routed to different servers still converge on one stored response.
4. Workers should be a separate, horizontally scaled service using durable
   database/outbox claims, not an in-process singleton in each API task.
5. For file uploads, use an IAM task role and short-lived pre-signed S3 URLs;
   the current development memory adapter is not multi-instance storage.
6. Run Alembic once in the release pipeline, using backward-compatible
   expand/contract migrations before rolling API tasks.
7. Use ALB health checks, graceful draining, shared logs/metrics, and deploy
   across multiple AZs; never depend on sticky sessions for correctness.

### 4. Outbox backlog after a two-hour worker outage

1. The current repository does not yet implement an outbox worker; in
   production I would persist events in an outbox table in the same transaction
   as the business change.
2. On recovery, workers claim bounded batches using row locks with
   `FOR UPDATE SKIP LOCKED`, then process with a configurable concurrency cap.
3. Start with a conservative batch/rate limit and increase only while database
   CPU, connection pool, and query latency stay healthy; leave API capacity
   reserved.
4. Make handlers idempotent and mark each event delivered only after success;
   retry transient failures with backoff and move poison events to a dead-letter
   state after a limit.
5. Monitor queue depth and oldest-event age, and autoscale gradually from those
   signals rather than launching an unbounded worker fleet.
6. Apply backpressure or pause low-priority event production if lag or database
   saturation crosses an alert threshold; alert operators until the backlog
   drains.

### 5. A deliberate simplicity trade-off

1. Expense idempotency uses PostgreSQL instead of adding a Redis lock.
2. The unique `(user_id, key)` constraint is the serialization point, so it
   remains correct across API instances and process restarts.
3. The expense and replay response are committed with the key record in one
   transaction, avoiding a distributed consistency problem between Redis and
   PostgreSQL.
4. The trade-off is an extra indexed database row and a unique-index write per
   idempotent request.
5. For this workload, correctness and operational simplicity are more valuable
   than avoiding that small database cost; the 24-hour expiry makes keys
   reusable without a separate cleanup dependency.

## Notes

The development receipt adapter stores objects in memory; replace it with a
production object-storage adapter for deployment. Account authorization is
enforced in database lookups, and inaccessible expenses/categories return 404.
Amounts in filters apply to the stored expense-currency amount; monthly report
and category aggregates are in base currency.

## Not finished and next steps

- **Production receipt storage / pre-signed URLs:** receipt bytes currently go
  into a process-local memory adapter, so they disappear on restart and are not
  shared across API instances. Implement an S3 adapter with private-bucket
  access, short-lived pre-signed URLs, checksum/size validation, and an
  ownership-checked completion step.
- **Outbox and background worker:** budget-alert processing is an idempotent
  API operation, but there is no durable outbox table or independent worker
  service. Add transactional outbox writes, bounded `SKIP LOCKED` claims,
  retries/backoff, dead-letter handling, and queue-lag monitoring before
  relying on asynchronous event delivery.
- **Live exchange-rate ingestion:** the included seed rates are fixed
  demonstration values. Integrate a trusted provider/import process, validate
  its data, and monitor gaps before using reports for real financial decisions.
- **Deployment automation:** the AWS section is a deployment design, not
  provisioned infrastructure. Add infrastructure-as-code, CI image publishing,
  security scanning, a one-off migration stage, staged ECS deployment, and
  alarms/rollback tests.
- **Idempotency-record cleanup:** expired keys are safe to reclaim on reuse,
  but untouched expired rows are not periodically deleted. Add a bounded
  scheduled cleanup task and retention/monitoring for the idempotency table as
  request volume grows.
