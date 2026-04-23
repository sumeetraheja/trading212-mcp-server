# Multi-Account Support

**Status:** Design approved, pending implementation plan
**Date:** 2026-04-23

## Problem

The Trading 212 MCP server currently binds to a single API key at startup via
`TRADING212_API_KEY` / `TRADING212_API_SECRET` environment variables. Users who
hold more than one Trading 212 account (e.g. personal + family) must restart the
server with different credentials to query each account. Cross-account questions
("summarise both accounts") are impossible in a single Claude conversation.

A local `accounts.json` file already sketches the intended multi-account
configuration, but nothing in the server reads it.

## Goals

- Allow the server to hold credentials for multiple Trading 212 accounts
  simultaneously.
- Let callers target a specific account per tool invocation.
- Keep the single-account env-var path working for existing users.
- Prevent write operations (order placement, pie mutations, CSV exports) from
  silently hitting the wrong account.

## Non-Goals

- Any UI for adding/removing accounts. `accounts.json` is hand-edited.
- Secret storage beyond a local JSON file. No keychain, no vault.
- Per-account rate limiting or quota management.
- Concurrent request fan-out across accounts (each tool call hits one account).

## Design

### Account selection

Every tool gains an optional `account: str | None = None` parameter.

- **Read-only tools** (`fetch_account_cash`, `fetch_pies`, `fetch_all_orders`,
  etc.) default to the `default` account named in `accounts.json` when
  `account` is omitted.
- **Write tools** (`place_*_order`, `cancel_order`, `create_pie`, `update_pie`,
  `delete_pie`, `duplicate_pie`, `request_csv_export`) raise
  `ValueError("account is required for write operations")` when `account` is
  omitted. No silent default for operations that move money or mutate state.

A new read-only tool `list_accounts()` returns the configured account names
and the default, so Claude can discover what's available.

### Config file

Path resolution, in order:

1. `TRADING212_ACCOUNTS_FILE` env var if set.
2. `<repo-root>/accounts.json` if present.
3. Env-var fallback: if neither file path resolves, synthesise a one-entry
   registry named `"default"` from `TRADING212_API_KEY`,
   `TRADING212_API_SECRET`, and `ENVIRONMENT`. This preserves the existing
   single-account workflow documented in the README.
4. If env vars are also missing, fail loud at startup.

Schema (validated with pydantic):

```json
{
  "default": "Sumeet",
  "accounts": [
    {
      "name": "Sumeet",
      "api_key": "...",
      "api_secret": "...",
      "environment": "live"
    },
    {
      "name": "Sheenu",
      "api_key": "...",
      "api_secret": "...",
      "environment": "live"
    }
  ]
}
```

Validation rules:

- `accounts` is non-empty.
- Names are unique.
- `default` references a name that exists in `accounts`.
- `environment` is `"live"` or `"demo"`.

### Response enrichment

Read-only tools wrap their current response in an envelope that names the
account, so cross-account output is unambiguous:

```python
{"account": "Sheenu", "cash": Cash(...)}
```

This change lives at the tool layer only. The `Trading212Client` return types
remain unchanged. Write tools return the same shape they do today — the account
name is already implicit because the caller supplied it.

### MCP resources

For resources (URI-based), add account-prefixed variants alongside the existing
ones:

- Existing: `trading212://account/cash` → default account, unchanged.
- New: `trading212://account/{account}/cash`, plus equivalents for
  `portfolio`, `portfolio/{ticker}`, `orders`, `orders/{order_id}`, `pies`,
  `pies/{pie_id}`, `history/exports`.

Market-data resources (`instruments`, `exchanges`) stay single-form — they
don't depend on the account.

### Architecture

```
accounts.json ──► AccountRegistry ──► { "Sumeet": Client, "Sheenu": Client }
                       │                       (lazy, cached)
                       └── default name

tools.py     ──► resolve_client(account, *, require_explicit) ──► Client
resources.py ──► resolve_client(account, *, require_explicit) ──► Client
```

Components:

- **`src/utils/accounts.py`** (new)
  - `AccountConfig` — pydantic model for one entry.
  - `AccountsFile` — pydantic model for the whole file.
  - `AccountRegistry` — holds validated config; `get(name) -> Trading212Client`
    constructs-on-first-use and caches; `default_name`; `names()`.
  - `AccountRegistry.load(path: str | None = None)` — classmethod implementing
    the path-resolution and env-var-fallback rules above.
- **`src/mcp_server.py`**
  - Replace global `client = Trading212Client()` with
    `registry = AccountRegistry.load()`.
  - Add `resolve_client(account: str | None, *, require_explicit: bool = False)`
    that delegates to the registry and encodes the default-vs-required rule.
- **`src/utils/client.py`** — `Trading212Client.__init__` already accepts
  explicit `api_key`/`api_secret`/`environment` kwargs. **One change required:**
  today all clients share a single global `hishel.FileStorage`, and hishel
  keys cache entries on URL + method (not on the `Authorization` header), so
  two accounts calling the same endpoint would collide on cache and leak each
  other's responses. Fix: accept an optional `cache_dir: str | None` kwarg
  and construct a per-account `hishel.FileStorage(base_path=cache_dir)` when
  provided. The registry passes a per-account path
  (e.g. `<cache_root>/<account-name>/`) when building clients.
- **`src/tools.py`** — add `account` param to every tool; call
  `resolve_client(account, require_explicit=<True for writes>)` at the top of
  each. Wrap read-only responses in the `{"account": ..., ...}` envelope. Add
  a new `list_accounts` tool.
- **`src/resources.py`** — add account-prefixed resource variants; keep
  existing URIs as default-account aliases.
- **`.gitignore`** — add `accounts.json`.
- **`README.md`** — add a "Multi-account setup" section documenting the file
  format and env-var fallback.

### Data flow

**Read call** (`fetch_account_cash(account="Sheenu")`):

1. Tool receives `account="Sheenu"`.
2. `resolve_client("Sheenu")` → `registry.get("Sheenu")` → cached client (or
   newly constructed on first use).
3. Client hits Trading 212 with Sheenu's basic-auth header.
4. Tool wraps as `{"account": "Sheenu", "cash": Cash(...)}` and returns.

**Write call** (`place_market_order(..., account="Sumeet")`):

1. `resolve_client("Sumeet", require_explicit=True)`. If `account` is `None`,
   raise `ValueError`.
2. Otherwise same as read.

**Startup:**

1. `AccountRegistry.load()` resolves config path, parses JSON, validates with
   pydantic. Duplicate names, missing `default`, or an unknown `default` →
   startup error with a message pointing at the README.
2. If config file absent, attempt env-var fallback.
3. If env vars absent, fail loud at startup.
4. Clients are **not** constructed at startup — only on first `get(name)`
   call. Avoids wasted HTTP sessions for unused accounts.

### Error handling

| Case | Behaviour |
|---|---|
| `accounts.json` missing, env vars missing | Startup error pointing at README. |
| `accounts.json` invalid JSON | Startup error showing parse location. |
| `accounts.json` fails schema validation | Startup error showing pydantic detail. |
| `accounts.json` missing `default` or `default` names an unknown account | Startup error. |
| Duplicate account names | Startup error. |
| Tool called with unknown `account="..."` | `ValueError("Unknown account 'X'. Known: [...]")` |
| Write tool called without `account` | `ValueError("account is required for write operations (known: [...])")` |
| Trading 212 API returns 401/403 | Existing error path. Error log includes account name for disambiguation. |

No silent fallback anywhere between accounts.

### Testing

- **Unit — `AccountRegistry`:**
  - loads a valid file;
  - rejects duplicate names;
  - rejects missing `default`;
  - rejects `default` that references unknown account;
  - env-var fallback synthesises a single `"default"` entry;
  - `get()` caches clients per name (second call returns same instance);
  - unknown name raises `ValueError`.
- **Unit — `resolve_client`:**
  - `require_explicit=False` + `account=None` → returns default client;
  - `require_explicit=True` + `account=None` → raises;
  - both forms + unknown name → raises.
- **Unit — tool wrappers:**
  - `fetch_account_cash()` (no arg) calls the default client and returns the
    enriched envelope;
  - `fetch_account_cash(account="Sheenu")` routes to Sheenu's client;
  - `place_market_order(...)` without `account` raises `ValueError`;
  - `place_market_order(..., account="Sumeet")` routes correctly.
- **Unit — `list_accounts`:**
  - returns configured names + default.
- **Unit — cache isolation:**
  - two `Trading212Client` instances with different `cache_dir` arguments do
    not share cached responses for the same URL. Mock the HTTP layer; call
    the same endpoint on both clients with different mocked responses;
    confirm each client sees its own response on re-fetch.
- **Manual smoke:**
  - Run server against real `accounts.json`;
  - call `fetch_account_cash(account="Sumeet")` and
    `fetch_account_cash(account="Sheenu")` from Claude;
  - confirm distinct totals;
  - call `place_market_order` without `account` and confirm the error
    surfaces cleanly in Claude.

Existing tests under `tests/` must stay green. If any mock the global `client`
import, they shift to mocking `resolve_client` instead.

## Security

- `accounts.json` holds live Trading 212 API credentials. Added to
  `.gitignore` as part of this change.
- The two keys currently in the working-copy `accounts.json` were visible in
  the conversation that produced this design and should be rotated in the
  Trading 212 dashboard before or shortly after this change ships.
- No change to credential storage mechanism itself — still a plaintext file.
  Users who want stronger storage can point `TRADING212_ACCOUNTS_FILE` at a
  path inside an encrypted volume.

## Open questions

None at design time.

## Out of scope / future work

- Keychain/OS secret-store integration.
- Encryption of the per-account hishel cache directory. The cache can
  contain API responses including positions and balances; users who want
  stronger at-rest protection can point the cache root at an encrypted
  volume.
- A `--account` CLI flag for `scripts/run_server.sh` to override the default.
