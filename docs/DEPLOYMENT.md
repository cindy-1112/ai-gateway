# Deployment Guide

This guide explains how to run AI Gateway outside a short local test session. It covers startup, configuration, database storage, API keys, and production safety checks.

## 1. Prepare The Server

Install Python 3.12 or later.

Create an application directory and install the project:

```powershell
cd D:\claude\ai-gateway
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

For development and tests:

```powershell
pip install -e ".[dev]"
```

## 2. Configure The Gateway

Edit:

```text
config/gateway.yaml
```

Minimum production configuration usually needs:

- at least one configured provider in `keypools`
- routes that map models to providers
- model aliases that users can request
- at least one tenant in `tenants`
- logging paths under `logging`

Example:

```yaml
aliases:
  volcengine-code: ark-code-latest

routes:
  - pattern: ark-code-latest
    provider: volcengine
  - pattern: "*"
    provider: openai

provider_base_urls:
  volcengine: https://ark.cn-beijing.volces.com/api/coding/v3

keypools:
  volcengine:
    keys:
      - REPLACE_WITH_PROVIDER_KEY
    rate_limit: 60
    strategy: round-robin

tenants:
  - name: default
    api_key: REPLACE_WITH_TENANT_KEY
    rate_limit:
      rpm: 60
      tpm: 100000
    quota:
      daily_tokens: 500000
      monthly_tokens: 10000000
```

## 3. Set Environment Variables

Use environment variables for deployment-specific values.

```powershell
$env:ADMIN_USERNAME = "admin@example.com"
$env:ADMIN_PASSWORD = "<your-admin-password>"
$env:GATEWAY_PORT = "8000"
```

Optional database override:

```powershell
$env:TEST_DB_URL = "sqlite+aiosqlite:///data/gateway.db"
```

Despite the name, `TEST_DB_URL` is currently the runtime override used by the app. If it is not set, SQLite at `data/gateway.db` is used.

## 4. Start The Service

Run:

```powershell
python run.py
```

Admin console:

```text
http://127.0.0.1:8000/admin/
```

Health endpoints:

```text
GET /health
GET /ready
```

Chat endpoint:

```text
POST /v1/chat/completions
```

Windows background start:

```powershell
Start-Process -FilePath python -ArgumentList 'run.py' -WorkingDirectory 'D:\claude\ai-gateway' -WindowStyle Hidden
```

Stop the local service:

```powershell
$pid = Get-NetTCPConnection -LocalPort 8000 -State Listen | Select-Object -ExpandProperty OwningProcess
Stop-Process -Id $pid
```

## 5. First Login

If no admin user exists, startup creates one from:

```text
ADMIN_USERNAME
ADMIN_PASSWORD
```

If these variables are not set, the default is:

```text
admin / <set ADMIN_PASSWORD>
```

Change this before first production startup.

## 6. Configure Providers And Models

In the admin console:

1. Open `配置管理`.
2. Add or edit provider Base URLs.
3. Add provider keys in the key pool.
4. Add model aliases.
5. Add route rules so each real model maps to a provider.
6. Save configuration.

Some runtime objects are rebuilt after saving configuration, but provider clients and some auth/rate-limit objects may still require a restart after deeper config changes. Restart the service after changing provider Base URLs, tenants, or production key material.

Model availability depends on the full path:

```text
alias -> real model -> route -> provider -> Base URL -> key pool
```

If any part is missing, the model can appear unavailable or fail at request time.

The admin UI can add provider configuration, but the backend provider registry is still fixed in `app/main.py`. A newly named provider will not be callable until the backend supports it.

## 7. User Model Access Flow

Normal user flow:

1. User registers and logs in.
2. User opens `申请模型`.
3. User submits a model request.
4. Admin opens `模型申请`.
5. Admin approves or rejects.
6. If approved, the user receives one active model binding.
7. Admin can later stop or resume that binding.

Current rule:

- one user has one current model binding
- active binding means usable
- stopped binding means the user cannot use the model until resumed

## 8. API Authentication

The current API call path supports tenant Bearer keys from `config/gateway.yaml`.

Example:

```powershell
$body = @{
  model = "volcengine-code"
  messages = @(
    @{ role = "user"; content = "hello" }
  )
} | ConvertTo-Json -Depth 5

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/v1/chat/completions" `
  -Method POST `
  -Headers @{ Authorization = "Bearer REPLACE_WITH_TENANT_KEY" } `
  -ContentType "application/json" `
  -Body $body
```

Planned improvement:

- user-level API keys for precise user identity
- user-level model binding enforcement on API calls
- user-level usage records

Current model binding enforcement is complete for admin-session user flows and user pages. For external API calls that only use tenant keys, the gateway cannot yet identify the registered user. Add user-level API keys before relying on strict per-user API enforcement.

## 9. Database Notes

Default SQLite file:

```text
data/gateway.db
```

Do not commit database files.

Back up before upgrades:

```powershell
Copy-Item data\gateway.db data\gateway.backup.db
```

Current table creation uses SQLAlchemy `create_all`. This creates missing tables but does not safely migrate existing columns. Add Alembic before making production schema migrations.

## 10. Logging

Access logs are written to the path configured in:

```yaml
logging:
  access_log: data/access.log
  error_log: data/error.log
  retention_days: 10
```

The admin `请求日志` page reads the access log file and displays recent model calls.

The request log is for call troubleshooting. It is not an audit log for admin actions such as login, approval, provider edits, stop, or resume.

Displayed log time is formatted by the browser in the local timezone. The stored access log timestamp remains UTC.

## 11. Troubleshooting

| Symptom | Likely Cause | Action |
| --- | --- | --- |
| `/ready` returns `503` | Database unavailable or no key pool is available | Check `data/gateway.db` and provider keys |
| `401` from API | Missing or invalid tenant key | Check tenant `api_key` and Authorization header |
| `403 Module access denied` | User lacks module permission | Adjust role or module override in `用户与权限` |
| `429` | Rate limit or quota exceeded | Check tenant `rate_limit` and `quota` |
| `502` | Upstream provider error | Check request log, Base URL, key status |
| `503 No key pool` | Route points to provider without key pool | Fix `routes` or `keypools` |
| `503 Unknown provider` | Provider name exists in config but not backend registry | Add provider support or use an existing provider |
| Config saved but calls still use old URL | Provider instance was not rebuilt | Restart the service |

## 12. Production Safety Checklist

Before production use:

- Change the default admin password.
- Remove all real secrets from Git.
- Keep `data/`, `.env`, local SQLite files, and screenshots out of Git.
- Restrict access to the admin console behind VPN, firewall, or a trusted reverse proxy.
- Use HTTPS when exposing the service outside localhost.
- Restart after changing provider URLs, tenants, or key material.
- Back up `data/gateway.db`.
- Review logs for failed upstream calls, 401/403, 429, and 5xx.
- Add user-level API keys before relying on per-user API enforcement.
- Rotate any provider key that has ever been committed to Git history.

## 13. GitHub Publishing Notes

When publishing:

1. Create a sanitized copy of the project.
2. Remove `.git`, `data/`, caches, local databases, screenshots, and real configs.
3. Replace provider keys with placeholders.
4. Scan for secrets.
5. Push only the sanitized copy.

Example checks:

```powershell
rg -n "sk-|AIza|api_key:|gho_|AKIA|SECRET" .
git status --short
git diff --cached
```
