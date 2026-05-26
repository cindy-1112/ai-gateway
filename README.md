# AI Gateway

AI Gateway is a FastAPI-based model gateway for managing provider keys, model aliases, user access, usage statistics, and admin operations from a web console.

It is currently designed for small-team or personal gateway management. The admin console includes login/register, provider configuration, key pool status, model application approval, user-model binding, usage statistics, and request logs.

## Features

- OpenAI-compatible `/v1/chat/completions` gateway endpoint.
- Provider routing by model pattern and model alias.
- Key pool management with round-robin, random, and least-used strategies.
- Admin login, user registration, roles, and module permissions.
- Model application workflow: user applies, admin approves, admin can stop or resume access.
- Usage statistics by tenant and model.
- Request logs for gateway call troubleshooting.
- Online configuration editor with provider/model wizards, delete confirmation, and save summary.
- Provider keys are masked by default. Users with configuration permission can explicitly reveal plaintext keys.
- SQLite persistence for local development and small deployments.

## Requirements

- Python 3.12 or later
- Windows PowerShell, Command Prompt, or any shell that can run Python
- Network access to the configured upstream model providers

## Install

From the project root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

If you do not need test tools:

```powershell
pip install -e .
```

## Configure

The application reads its main configuration from:

```text
config/gateway.yaml
```

Important sections:

- `aliases`: public model aliases shown to users, for example `volcengine-code: ark-code-latest`.
- `routes`: maps real model names or patterns to providers.
- `keypools`: provider API keys and selection strategy.
- `provider_base_urls`: upstream provider base URLs.
- `tenants`: tenant API keys, rate limits, and quotas.
- `pricing`: optional pricing data used for cost calculation.
- `logging`: access/error log paths and retention settings.

Example key pool:

```yaml
keypools:
  volcengine:
    keys:
      - REPLACE_WITH_PROVIDER_KEY
    rate_limit: 60
    strategy: round-robin
```

Do not commit real API keys to GitHub. Keep production secrets in a private local config, environment variables, or a secret manager.

Model availability depends on the full configuration chain:

```text
model alias -> real model -> route rule -> provider -> base URL -> key pool
```

For example, `volcengine-code` can resolve to `ark-code-latest`, route to `volcengine`, then use the VolcEngine Base URL and key pool.

## Start

Run:

```powershell
python run.py
```

Default address:

```text
http://127.0.0.1:8000/admin/
```

The API endpoint is:

```text
POST http://127.0.0.1:8000/v1/chat/completions
```

Windows background start:

```powershell
Start-Process -FilePath python -ArgumentList 'run.py' -WorkingDirectory 'D:\claude\ai-gateway' -WindowStyle Hidden
```

Stop a local service on port `8000`:

```powershell
$pid = Get-NetTCPConnection -LocalPort 8000 -State Listen | Select-Object -ExpandProperty OwningProcess
Stop-Process -Id $pid
```

## Default Admin Account

On first startup, the application creates a default admin account if it does not already exist.

Default values:

```text
username: admin
password: <set ADMIN_PASSWORD>
```

Override them before first startup:

```powershell
$env:ADMIN_USERNAME = "admin@example.com"
$env:ADMIN_PASSWORD = "<your-admin-password>"
python run.py
```

Change the default password immediately in any shared or production environment.

## Environment Variables

| Variable | Purpose | Default |
| --- | --- | --- |
| `ADMIN_USERNAME` | Default admin username created on first startup | `admin` |
| `ADMIN_PASSWORD` | Default admin password created on first startup | `<set ADMIN_PASSWORD>` |
| `GATEWAY_PORT` | Overrides `server.port` from `config/gateway.yaml` | `8000` |
| `TEST_DB_URL` | Overrides the database URL | `sqlite+aiosqlite:///data/gateway.db` |

`TEST_DB_URL` is currently the runtime database override despite its test-oriented name.

## Database

The default database is SQLite:

```text
data/gateway.db
```

The project uses SQLAlchemy Async ORM with `aiosqlite`.

Current startup behavior creates missing tables automatically. For production-style schema changes, add a migration tool such as Alembic before making breaking database changes.

## Admin Console

Admin console URL:

```text
http://127.0.0.1:8000/admin/login
```

Main modules:

- `密钥池`: provider key status, Base URL, and model mapping.
- `用量统计`: token, cost, and request count summaries.
- `请求日志`: recent gateway calls, status codes, latency, model, and provider.
- `模型申请`: model request approval and user binding stop/resume.
- `用户与权限`: users, roles, tenants, and module overrides.
- `配置管理`: routes, aliases, providers, key pools, and fallback rules.

Default role modules:

| Role | Modules |
| --- | --- |
| `admin` | keys, usage, logs, users, config |
| `operator` | keys, usage, logs |
| `user` | usage, logs plus the model application entry |

Registration supports email or phone plus verification code. The current implementation is still in development mode: the verification API returns `debug_code` and does not send real email or SMS.

## API Endpoint Summary

| Endpoint | Method | Purpose | Auth |
| --- | --- | --- | --- |
| `/health` | GET | Liveness check | None |
| `/ready` | GET | Database and key-pool readiness | None |
| `/v1/chat/completions` | POST | OpenAI-compatible model gateway | Bearer tenant key |
| `/v1/usage` | GET | Tenant quota usage | Bearer tenant key |
| `/v1/usage/summary` | GET | Admin usage summary | Cookie or Bearer |
| `/v1/usage/events` | GET | Usage SSE events | Cookie or Bearer |
| `/v1/keys/status` | GET | Provider key-pool status | `keys` module |
| `/v1/logs` | GET | Request logs | `logs` module |
| `/v1/config` | GET/PUT | Read or save gateway config | `config` module |
| `/v1/config/keypools/{provider}/keys` | GET | Reveal provider keys | `config` module |
| `/admin/api/model-requests` | GET | Admin model requests and bindings | admin |
| `/admin/api/model-requests/me` | GET | Current user's model request state | Cookie |
| `/admin/api/users` | GET/POST | User management | `users` module |

## Configuration Hot Reload Boundary

Saving configuration from the admin console currently refreshes:

- routes
- aliases
- key pools

Restart the service after changing:

- tenant API keys
- tenant rate limits or quotas
- provider Base URLs
- newly added provider names that require backend registration
- production key material

## Troubleshooting

| Symptom | Likely Cause | What To Check |
| --- | --- | --- |
| `401` | Missing or invalid Bearer token | `Authorization: Bearer ...` |
| `403 Module access denied` | Login user lacks that module | `用户与权限` module overrides |
| `429` | RPM/TPM or quota exceeded | `/v1/usage`, tenant config |
| `502` | Upstream provider error | Request logs, provider Base URL, key status |
| `503 No key pool` | Route points to provider without keys | `routes` and `keypools` |
| `503 Unknown provider` | Config provider is not registered in backend | `app/main.py` providers |
| Config saved but calls use old Base URL | Provider clients are not fully hot-reloaded | Restart service |

## GitHub Safety

Before pushing to GitHub:

- Remove real provider keys from `config/gateway.yaml`.
- Do not commit `data/`, `.env`, local SQLite files, screenshots, or temporary exports.
- Prefer committing an example config and keeping real secrets outside Git.
- Check the staged diff before every push.
- If real provider keys ever entered Git history, rotate those provider keys.

Suggested checks:

```powershell
git status --short
git diff --cached
rg -n "sk-|AIza|api_key:|gho_" .
```

## Run Tests

```powershell
pytest
```

Useful quick checks:

```powershell
python -m py_compile app/main.py app/db/models.py
node --check app/static/app.js
```

## More Documentation

Deployment and production notes:

```text
docs/DEPLOYMENT.md
```

Chinese project guide with module and model diagrams:

```text
docs/ZH_CN.md
```
