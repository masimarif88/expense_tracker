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


## AWS Deployment Notes

These are proposed AWS deployment settings, not resources currently configured in the repository. The outbox worker and persistent receipt storage still need to be implemented before production deployment.

### 1. API and Worker

- Deploy the API on ECS Fargate behind an ALB, across at least two Availability Zones.
- Start with two API tasks and enable autoscaling based on CPU, memory, and request latency.
- Run background workers as a separate ECS service, scaling based on queue depth and event age.
- Configure health checks, graceful shutdown, and automatic recovery for unhealthy tasks.

### 2. Database and Secrets

- Use private, encrypted RDS PostgreSQL with Multi-AZ and automated backups.
- Allow port `5432` only from the API and worker security groups.
- Never keep credentials in source code, Docker images, or logs. Support safe credential rotation.

### 3. Redis

- Allow port `6379` only from API tasks.
- Monitor Redis health and configure alarms for failover and memory issues.

### 4. S3 Receipts and IAM

- Use a private S3 bucket with Block Public Access, encryption, and lifecycle cleanup.
- Give ECS tasks IAM roles instead of storing AWS access keys in application configuration.


### 5. Alembic Migrations

- Run migrations once through an ECS release task, not from every API instance.
- Use backward-compatible migrations so old and new application versions can run during deployment.
- Add new columns or tables first, deploy the updated code, and remove old schema elements in a later release.
- Take backups, monitor migration failures, and stop deployment if migrations fail. Prefer forward fixes over risky automatic rollbacks.

## Short interview answers

### 1. Pre-signed Upload URLs

1. Pre-signed URLs let clients upload files directly to S3, avoiding unnecessary load on the API.
2. The API authenticates the user and generates a short-lived URL with a unique, user-specific object key. The bucket remains private.
3. Since anyone with the URL can use it until expiry, keep its lifetime short and avoid logging it.
4. Restrict uploads by HTTP method, object key, file type, and size where possible.
5. Never trust the client filename or MIME type. Validate the uploaded file before attaching it to an expense.
6. Use S3 lifecycle rules to remove abandoned uploads and handle multipart uploads properly.

### 2. Slow Expense Listing at 20 Million Rows

1. Start with `EXPLAIN (ANALYZE, BUFFERS)` to identify where the query is spending time.
2. Check the existing indexes and add a composite index where needed, such as `(user_id, spent_on DESC, id DESC)`.
3. For category-based filtering, consider `(user_id, category_id, spent_on DESC, id DESC)`.
4. Avoid deep offset pagination. Keyset pagination with the existing signed cursor is a better fit for large datasets.
5. For text searches, check whether the existing `pg_trgm` GIN index is being used for `ILIKE`.
6. Review ORM queries for N+1 issues and use joins or eager loading where appropriate.
7. After changes, compare query execution time and database load. Additional indexes also come with storage and write costs.

### 3. Three API Servers Behind a Load Balancer

1. All API servers use the same managed JWT signing secret, while refresh token state is stored in PostgreSQL.
2. Login rate limits use shared Redis counters instead of local memory. If Redis is unavailable, login requests return 503 rather than bypassing the limits.
3. Idempotency is handled through PostgreSQL transactions and unique constraints, so requests reaching different servers won't create duplicate expenses.
4. Run background workers as a separate service rather than starting an independent worker inside every API instance.
5. Use IAM roles and pre-signed S3 URLs for file uploads. The current in-memory development storage isn't suitable for multiple instances.
6. Run Alembic migrations through the release pipeline and keep them backward-compatible during deployments.
7. Use health checks, graceful shutdowns, centralized logging, and multiple availability zones. The application should not depend on sticky sessions.

### 4. Outbox Backlog After a Two-Hour Worker Outage

1. The current repository doesn't have an outbox worker yet. For production, I'd store events in an outbox table within the same transaction as the business operation.
2. Workers can claim batches using `FOR UPDATE SKIP LOCKED` to avoid processing the same events simultaneously.
3. Start with limited batch sizes and concurrency, then increase gradually while monitoring database load.
4. Make event handlers idempotent and retry temporary failures with backoff. Move repeatedly failing events to a dead-letter state.
5. Monitor queue depth and the age of the oldest pending event to track recovery.
6. If the database becomes overloaded, slow down low-priority processing rather than adding more workers blindly.

### 5. A Deliberate Simplicity Trade-off

1. I chose PostgreSQL for expense idempotency instead of introducing a separate Redis lock.
2. A unique `(user_id, key)` constraint prevents duplicate requests, even when multiple API servers handle them.
3. The expense and its replay response are saved in the same transaction, keeping everything consistent.
4. This adds a small database write and an indexed record for each idempotent request.
5. I prefer this approach because it's simpler to maintain and avoids distributed locking. The 24-hour expiry keeps the idempotency window manageable.

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
