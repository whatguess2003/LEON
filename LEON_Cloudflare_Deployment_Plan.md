# LEON backend requirements for Cloudflare Containers + Hyperdrive

Scope: backend code only, so the FastAPI server stays deployable later behind a Worker that starts a Container and reaches Postgres through Hyperdrive. Wrangler config and deployment steps are out of scope (the user will do binding and deployment).

## Checklist

### 1. Connection from env, one place
- [ ] All Postgres connections come from one helper that reads `DATABASE_URL` from the environment (today ~20 modules call `psycopg2.connect(os.getenv("DATABASE_URL"))` directly, e.g. `booking_manager.py`, `inventory_manager.py`, `payment_manager.py`, `aiosell_reservation_webhook.py`, `front_desk_manager.py`).
- [ ] The helper handles the `sslrootcert=system` rewrite (currently duplicated in `main.py` and `content_store.py`) and a connect timeout (`PGCONNECT_TIMEOUT` is already set in `Dockerfile.pms`).
- [ ] Swapping the Railway URL for a Hyperdrive or pooler URL is an env change only. No host, port, or credentials in code.

### 2. Settings scoped to the transaction
- [ ] Use `set_config(name, value, true)` / `SET LOCAL` inside an explicit transaction. `semantic/query.py` already does this for `TimeZone`. Keep it that way.
- [ ] Replace session-level settings:
  - `content_store._connection()`: `SET statement_timeout = 8000` on a reused autocommit connection.
  - `semantic/sql_agent._pg` and `semantic/cache.py`: `SET statement_timeout = 8000` then `= 0` on the shared guest connection.
  - `content_store.add_column`: `SET lock_timeout` then `set_config('lock_timeout', previous, false)`.
- [ ] `seed_general.py` reads `current_setting('TimeZone')`, so every query built from it has to run inside a transaction that set `TimeZone` locally first.

### 3. No session state across statements
- [ ] Don't use temp tables, `LISTEN/NOTIFY`, session advisory locks, `WITH HOLD` cursors, or named `PREPARE`. A grep found none today, so keep it that way.
- [ ] Don't keep long-lived per-object connections that depend on earlier session state (`self.postgres_conn` in the booking, inventory, and payment managers and `ota_agent`; `guest_manager._pg_conn`). Either use short-lived connections from the helper or a small pool, with a reconnect when a connection turns out to be stale.
- [ ] Keep the pool small (a few connections per process). A pooler in front of Postgres does the fan-in.
- [ ] Stop mutating process-wide `os.environ['HOTEL_TIMEZONE']` per request (`main.py`, about 10 call sites). It isn't thread-safe. Pass the hotel zone explicitly instead.

### 4. Background jobs when the container sleeps
- [ ] Assume the process can stop when it goes idle or is redeployed. `workers/pms.js` sets a 1-hour inactivity timeout, and in-flight threads are lost when the process stops.
- [ ] Catalog agent and reviewer investigations (`semantic/trace.py`: `enqueue_catalog_agent`, `enqueue_resume`, both `threading.Thread`) must be resumable from Postgres:
  - Persist job state (queued, running, done, blocked) before starting the thread.
  - Make each step idempotent.
  - Re-enqueue unfinished jobs at startup or from a `/jobs/resume`-style endpoint, so a cron or alarm can trigger it later.
- [ ] FastAPI `BackgroundTasks` (WhatsApp guest needs, the `push_dates` channel-manager push) are fire-and-forget. They should either be safe to lose or be written to a DB outbox first and then retried.
- [ ] Keep `CATALOG_AGENT_SYNC=1` working as the synchronous fallback.

### 5. Secrets and config only from env
- [ ] No defaults for secrets in production. `JWT_SECRET` currently falls back to `'dev-secret'` in about 8 places, so the app should fail at startup when it's missing.
- [ ] Env names the code reads: `DATABASE_URL`, `JWT_SECRET`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `AIOSSELL_USERNAME`, `AIOSSELL_PASSWORD`, `AIOSSELL_PARTNER_ID`, `AIOSSELL_CARD_KEY`, `CHANNEL_MANAGER_URL`, `FRONTEND_ORIGINS`, `HOTEL_TIMEZONE`, `LEON_HOTEL_TZ`, `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_PHONE_NUMBER`, `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET`, `WHATSAPP_GRAPH_VERSION`, `WHATSAPP_TEMPLATE_NAME`, `WHATSAPP_TEMPLATE_LANGUAGE`, `S3_API`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `SERPER_API_KEY`, `TAVILY_API_KEY`, `LEON_SUGGESTIONS_ENABLED`, `LEON_CATALOG_MANAGER_DAILY`, `LEON_CATALOG_HOTEL_DAILY`, `LEON_CATALOG_DAILY_TOKENS`, `LEON_CATALOG_INVESTIGATION_TOKENS`, `CATALOG_AGENT_SYNC`, `PORT`.
- [ ] Gap for later: `workers/pms.js` doesn't forward `ANTHROPIC_API_KEY`, the `S3_*` vars, `WHATSAPP_GRAPH_VERSION`, or the `LEON_*` vars to the container. Note this for whoever does the binding.
- [ ] Don't read `.env` files at runtime in the container (`.dockerignore` already excludes `.env*`).

### 6. Health and startup
- [ ] `GET /health` exists (`main.py`). Keep it cheap: no LLM calls and no heavy database work. Optionally add a separate readiness check that runs `SELECT 1`.
- [ ] Startup must not block on schema DDL. Keep `ensure_tables`/`add_column` read-first with a short `lock_timeout`, or move DDL into migrations.
- [ ] Listen on `PORT` (8080 in the container, 8002 locally). `container_entry.py` already answers 503 while `main` imports.
- [ ] Bound request bodies and use timeouts on outbound HTTP calls (OpenAI, Anthropic, Aiosell, WhatsApp).

## Verified vs not verified

Verified from this repo's code (this session):
- `workers/pms.js` and `workers/cm.js` already use the Durable Object container API (`ctx.container.start({ env, enableInternet })`, `getTcpPort(8080).fetch`, `setInactivityTimeout(1h)`). Env reaches the container only through `start({ env })`.
- `wrangler.pms.jsonc` uses `instance_type: standard-1` and `max_instances: 2`, with no Hyperdrive binding yet.

Not verified against the Cloudflare docs (research stopped before the docs lookup finished). Confirm these before deploying:
- Whether a Container can use a Hyperdrive binding directly, or must get the connection string from the Worker, or connect to the origin database itself.
- Hyperdrive pooling mode (transaction mode is assumed, and it is why items 2 and 3 above matter) and which session features it supports.
- How a Container behaves when it sleeps, how long a cold start takes, and the instance size limits.
- References to check: https://developers.cloudflare.com/containers/ , https://developers.cloudflare.com/hyperdrive/ , https://developers.cloudflare.com/hyperdrive/reference/supported-databases-and-features/
