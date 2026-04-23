# Multi-Account Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the Trading 212 MCP server hold credentials for multiple accounts simultaneously, route each tool call to a chosen account, and prevent silent cross-account leaks.

**Architecture:** A new `AccountRegistry` loads `accounts.json` at startup (with env-var fallback) and lazily builds one `Trading212Client` per account. A `resolve_client(account, *, require_explicit)` helper gates tool access: read-only tools default to the configured default account; write tools require an explicit `account=`. Each client gets its own hishel cache directory to prevent response leakage between accounts.

**Tech Stack:** Python 3.11, FastMCP, pydantic v2, hishel (HTTP cache), pytest, httpx mocking via `pytest-httpx`.

Spec: `docs/superpowers/specs/2026-04-23-multi-account-design.md`

---

## File Structure

**New files:**
- `src/utils/accounts.py` — `AccountConfig`, `AccountsFile`, `AccountRegistry`
- `tests/conftest.py` — pytest fixtures, path setup
- `tests/test_accounts.py` — AccountRegistry unit tests
- `tests/test_resolve_client.py` — resolve_client behaviour tests
- `tests/test_tools_multi_account.py` — tool routing + envelope shape
- `tests/test_cache_isolation.py` — cross-account cache isolation

**Modified files:**
- `src/utils/client.py` — accept `cache_dir` kwarg; use per-client `FileStorage`
- `src/mcp_server.py` — replace global `client` with `registry` + `resolve_client`
- `src/tools.py` — add `account` param on every tool; add `list_accounts`; envelope read responses
- `src/resources.py` — add account-prefixed URI variants
- `.gitignore` — add `accounts.json`
- `README.md` — add "Multi-account setup" section
- `pyproject.toml` — add pytest + pytest-httpx to dev deps

---

## Task 1: Set up pytest

**Files:**
- Modify: `pyproject.toml`
- Create: `tests/conftest.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: Add pytest dev dependencies**

Edit `pyproject.toml` — add a `[dependency-groups]` section after the existing `[project]` block:

```toml
[dependency-groups]
dev = [
    "pytest>=8.0",
    "pytest-httpx>=0.30",
]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

- [ ] **Step 2: Install dev deps**

Run: `uv sync --group dev`
Expected: pytest and pytest-httpx installed.

- [ ] **Step 3: Create `tests/__init__.py`** (empty file)

```python
```

- [ ] **Step 4: Create `tests/conftest.py`**

```python
import os
import sys
from pathlib import Path

# Ensure src/ is importable (belt-and-braces with pyproject pythonpath)
SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


import pytest


@pytest.fixture(autouse=True)
def _clear_trading212_env(monkeypatch):
    """Most tests should not be affected by the developer's real env vars."""
    for var in (
        "TRADING212_API_KEY",
        "TRADING212_API_SECRET",
        "ENVIRONMENT",
        "TRADING212_ACCOUNTS_FILE",
    ):
        monkeypatch.delenv(var, raising=False)
```

- [ ] **Step 5: Confirm pytest discovers the empty test dir**

Run: `uv run pytest tests/ -v`
Expected: "collected 0 items" (exit 5), no import errors.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock tests/__init__.py tests/conftest.py
git commit -m "chore: add pytest dev dependency and conftest"
```

---

## Task 2: `AccountConfig` and `AccountsFile` pydantic models

**Files:**
- Create: `src/utils/accounts.py`
- Create: `tests/test_accounts.py`

- [ ] **Step 1: Write failing tests for the config models**

Create `tests/test_accounts.py`:

```python
import pytest
from pydantic import ValidationError

from utils.accounts import AccountConfig, AccountsFile


def test_account_config_accepts_valid_entry():
    cfg = AccountConfig(
        name="Sumeet",
        api_key="key",
        api_secret="secret",
        environment="live",
    )
    assert cfg.name == "Sumeet"
    assert cfg.environment == "live"


def test_account_config_rejects_unknown_environment():
    with pytest.raises(ValidationError):
        AccountConfig(
            name="x",
            api_key="k",
            api_secret="s",
            environment="staging",
        )


def test_accounts_file_rejects_duplicate_names():
    with pytest.raises(ValidationError) as exc:
        AccountsFile(
            default="A",
            accounts=[
                AccountConfig(name="A", api_key="k1", api_secret="s1", environment="live"),
                AccountConfig(name="A", api_key="k2", api_secret="s2", environment="live"),
            ],
        )
    assert "duplicate" in str(exc.value).lower()


def test_accounts_file_rejects_default_not_in_accounts():
    with pytest.raises(ValidationError) as exc:
        AccountsFile(
            default="Missing",
            accounts=[
                AccountConfig(name="A", api_key="k", api_secret="s", environment="live"),
            ],
        )
    assert "default" in str(exc.value).lower()


def test_accounts_file_rejects_empty_accounts():
    with pytest.raises(ValidationError):
        AccountsFile(default="A", accounts=[])


def test_accounts_file_accepts_valid_config():
    f = AccountsFile(
        default="A",
        accounts=[
            AccountConfig(name="A", api_key="k1", api_secret="s1", environment="live"),
            AccountConfig(name="B", api_key="k2", api_secret="s2", environment="demo"),
        ],
    )
    assert f.default == "A"
    assert len(f.accounts) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_accounts.py -v`
Expected: `ModuleNotFoundError: No module named 'utils.accounts'`

- [ ] **Step 3: Write minimal `src/utils/accounts.py`**

```python
from __future__ import annotations

from typing import List, Literal
from pydantic import BaseModel, Field, field_validator, model_validator


class AccountConfig(BaseModel):
    name: str = Field(min_length=1)
    api_key: str = Field(min_length=1)
    api_secret: str = Field(min_length=1)
    environment: Literal["live", "demo"]


class AccountsFile(BaseModel):
    default: str = Field(min_length=1)
    accounts: List[AccountConfig] = Field(min_length=1)

    @field_validator("accounts")
    @classmethod
    def _no_duplicate_names(cls, v: List[AccountConfig]) -> List[AccountConfig]:
        seen = set()
        for a in v:
            if a.name in seen:
                raise ValueError(f"duplicate account name: {a.name!r}")
            seen.add(a.name)
        return v

    @model_validator(mode="after")
    def _default_must_exist(self) -> "AccountsFile":
        names = {a.name for a in self.accounts}
        if self.default not in names:
            raise ValueError(
                f"default account {self.default!r} not found in accounts (known: {sorted(names)})"
            )
        return self
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_accounts.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/utils/accounts.py tests/test_accounts.py
git commit -m "feat: add AccountConfig and AccountsFile pydantic models"
```

---

## Task 3: `AccountRegistry.load()` — file path + env-var resolution

**Files:**
- Modify: `src/utils/accounts.py`
- Modify: `tests/test_accounts.py`

- [ ] **Step 1: Append failing tests for `AccountRegistry.load`**

Append to `tests/test_accounts.py`:

```python
import json
from pathlib import Path

from utils.accounts import AccountRegistry


def _write_config(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "accounts.json"
    p.write_text(json.dumps(data))
    return p


def test_load_from_explicit_file(tmp_path, monkeypatch):
    path = _write_config(tmp_path, {
        "default": "A",
        "accounts": [
            {"name": "A", "api_key": "k1", "api_secret": "s1", "environment": "live"},
            {"name": "B", "api_key": "k2", "api_secret": "s2", "environment": "demo"},
        ],
    })
    monkeypatch.setenv("TRADING212_ACCOUNTS_FILE", str(path))

    reg = AccountRegistry.load()

    assert reg.default_name == "A"
    assert reg.names() == ["A", "B"]


def test_load_env_var_fallback_when_no_file(monkeypatch):
    monkeypatch.setenv("TRADING212_API_KEY", "envkey")
    monkeypatch.setenv("TRADING212_API_SECRET", "envsecret")
    monkeypatch.setenv("ENVIRONMENT", "demo")

    reg = AccountRegistry.load(explicit_path=None, repo_root_path=None)

    assert reg.default_name == "default"
    assert reg.names() == ["default"]


def test_load_fails_when_no_file_and_no_env(monkeypatch):
    with pytest.raises(RuntimeError) as exc:
        AccountRegistry.load(explicit_path=None, repo_root_path=None)
    assert "accounts.json" in str(exc.value)


def test_load_fails_on_invalid_json(tmp_path, monkeypatch):
    path = tmp_path / "accounts.json"
    path.write_text("{not json")
    monkeypatch.setenv("TRADING212_ACCOUNTS_FILE", str(path))

    with pytest.raises(RuntimeError) as exc:
        AccountRegistry.load()
    assert "parse" in str(exc.value).lower() or "json" in str(exc.value).lower()


def test_load_fails_on_schema_error(tmp_path, monkeypatch):
    path = _write_config(tmp_path, {
        "default": "X",
        "accounts": [
            {"name": "A", "api_key": "k", "api_secret": "s", "environment": "live"},
        ],
    })
    monkeypatch.setenv("TRADING212_ACCOUNTS_FILE", str(path))

    with pytest.raises(RuntimeError):
        AccountRegistry.load()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_accounts.py -v`
Expected: 5 new failures — `AttributeError: module has no attribute 'AccountRegistry'`.

- [ ] **Step 3: Add `AccountRegistry.load` to `src/utils/accounts.py`**

Append to `src/utils/accounts.py`:

```python
import json
import os
from pathlib import Path
from typing import Dict, List, Optional


class AccountRegistry:
    """Holds validated account configs and lazily caches constructed clients.

    Client construction is deferred until first `get()` call; client caching
    is added in a later task.
    """

    def __init__(self, config: AccountsFile):
        self._config = config
        self._by_name: Dict[str, AccountConfig] = {a.name: a for a in config.accounts}

    @property
    def default_name(self) -> str:
        return self._config.default

    def names(self) -> List[str]:
        return [a.name for a in self._config.accounts]

    def config_for(self, name: str) -> AccountConfig:
        if name not in self._by_name:
            raise ValueError(
                f"Unknown account {name!r}. Known: {self.names()}"
            )
        return self._by_name[name]

    @classmethod
    def load(
        cls,
        *,
        explicit_path: Optional[str] = None,
        repo_root_path: Optional[Path] = None,
    ) -> "AccountRegistry":
        """Resolve config source and build a registry.

        Resolution order:
        1. `explicit_path` argument (test hook).
        2. `TRADING212_ACCOUNTS_FILE` env var.
        3. `<repo_root_path>/accounts.json` if the path exists.
        4. Env-var fallback → synthetic single "default" account.
        5. Raise RuntimeError.
        """
        path = explicit_path or os.getenv("TRADING212_ACCOUNTS_FILE")
        if not path and repo_root_path is not None:
            candidate = repo_root_path / "accounts.json"
            if candidate.exists():
                path = str(candidate)

        if path:
            try:
                raw = Path(path).read_text()
            except OSError as e:
                raise RuntimeError(f"Cannot read accounts file {path}: {e}") from e
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as e:
                raise RuntimeError(
                    f"Failed to parse JSON in accounts file {path}: {e}"
                ) from e
            try:
                return cls(AccountsFile.model_validate(data))
            except Exception as e:
                raise RuntimeError(
                    f"Invalid accounts file {path}: {e}"
                ) from e

        # Env-var fallback
        api_key = os.getenv("TRADING212_API_KEY")
        api_secret = os.getenv("TRADING212_API_SECRET")
        environment = os.getenv("ENVIRONMENT", "demo")
        if api_key and api_secret:
            synthetic = AccountsFile(
                default="default",
                accounts=[
                    AccountConfig(
                        name="default",
                        api_key=api_key,
                        api_secret=api_secret,
                        environment=environment if environment in ("live", "demo") else "demo",
                    )
                ],
            )
            return cls(synthetic)

        raise RuntimeError(
            "No accounts configuration found. Provide an accounts.json file "
            "(set TRADING212_ACCOUNTS_FILE or place at repo root) or set "
            "TRADING212_API_KEY and TRADING212_API_SECRET. "
            "See README.md 'Multi-account setup'."
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_accounts.py -v`
Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add src/utils/accounts.py tests/test_accounts.py
git commit -m "feat: add AccountRegistry.load with file and env fallback"
```

---

## Task 4: Per-account client caching in `AccountRegistry.get`

**Files:**
- Modify: `src/utils/accounts.py`
- Modify: `tests/test_accounts.py`

- [ ] **Step 1: Append failing test for caching**

Append to `tests/test_accounts.py`:

```python
from unittest.mock import patch


def test_registry_caches_clients_per_name(tmp_path, monkeypatch):
    path = _write_config(tmp_path, {
        "default": "A",
        "accounts": [
            {"name": "A", "api_key": "k1", "api_secret": "s1", "environment": "live"},
        ],
    })
    monkeypatch.setenv("TRADING212_ACCOUNTS_FILE", str(path))
    reg = AccountRegistry.load()

    call_count = {"n": 0}

    def fake_build(config, cache_dir):
        call_count["n"] += 1
        return object()

    with patch("utils.accounts._build_client", side_effect=fake_build):
        c1 = reg.get("A")
        c2 = reg.get("A")

    assert c1 is c2
    assert call_count["n"] == 1


def test_registry_get_unknown_raises(tmp_path, monkeypatch):
    path = _write_config(tmp_path, {
        "default": "A",
        "accounts": [
            {"name": "A", "api_key": "k1", "api_secret": "s1", "environment": "live"},
        ],
    })
    monkeypatch.setenv("TRADING212_ACCOUNTS_FILE", str(path))
    reg = AccountRegistry.load()

    with pytest.raises(ValueError) as exc:
        reg.get("Missing")
    assert "Missing" in str(exc.value)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_accounts.py -v`
Expected: 2 new failures — no `get` method / no `_build_client`.

- [ ] **Step 3: Extend `src/utils/accounts.py`**

Add import near the top (after existing pydantic imports):

```python
from pathlib import Path as _Path
```

Add module-level helper and a cache-root default:

```python
_DEFAULT_CACHE_ROOT = _Path.home() / ".trading212" / "cache"


def _build_client(config: AccountConfig, cache_dir: _Path):
    """Construct a Trading212Client from an AccountConfig.

    Imported lazily to avoid a circular import at module load and to make the
    registry testable without the HTTP stack.
    """
    from utils.client import Trading212Client  # local import

    cache_dir.mkdir(parents=True, exist_ok=True)
    return Trading212Client(
        api_key=config.api_key,
        api_secret=config.api_secret,
        environment=config.environment,
        cache_dir=str(cache_dir),
    )
```

Extend `AccountRegistry`:

```python
    def __init__(
        self,
        config: AccountsFile,
        cache_root: Optional[_Path] = None,
    ):
        self._config = config
        self._by_name: Dict[str, AccountConfig] = {a.name: a for a in config.accounts}
        self._cache_root = cache_root or _DEFAULT_CACHE_ROOT
        self._clients: Dict[str, object] = {}

    def get(self, name: str):
        if name not in self._by_name:
            raise ValueError(
                f"Unknown account {name!r}. Known: {self.names()}"
            )
        if name not in self._clients:
            cache_dir = self._cache_root / name
            self._clients[name] = _build_client(self._by_name[name], cache_dir)
        return self._clients[name]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_accounts.py -v`
Expected: 13 passed.

- [ ] **Step 5: Commit**

```bash
git add src/utils/accounts.py tests/test_accounts.py
git commit -m "feat: lazy per-account client caching in AccountRegistry"
```

---

## Task 5: `Trading212Client` accepts `cache_dir` for per-account hishel storage

**Files:**
- Modify: `src/utils/client.py`
- Create: `tests/test_cache_isolation.py`

- [ ] **Step 1: Write failing cache-isolation test**

Create `tests/test_cache_isolation.py`:

```python
from pathlib import Path

from utils.client import Trading212Client


def test_clients_with_distinct_cache_dirs_do_not_share_cache(tmp_path, httpx_mock):
    # Two clients, two different cache dirs, same URL — they must NOT cross-read
    # each other's cached responses.
    client_a = Trading212Client(
        api_key="keyA",
        api_secret="secretA",
        environment="demo",
        cache_dir=str(tmp_path / "A"),
    )
    client_b = Trading212Client(
        api_key="keyB",
        api_secret="secretB",
        environment="demo",
        cache_dir=str(tmp_path / "B"),
    )

    url = "https://demo.trading212.com/api/v0/equity/account/info"
    httpx_mock.add_response(url=url, json={"id": 1, "currencyCode": "GBP"})
    httpx_mock.add_response(url=url, json={"id": 2, "currencyCode": "USD"})

    a1 = client_a.get_account_info()
    b1 = client_b.get_account_info()

    assert a1.id == 1
    assert b1.id == 2
    # Two distinct caches → two real HTTP requests made.
    assert len(httpx_mock.get_requests()) == 2


def test_default_cache_dir_used_when_kwarg_absent(tmp_path, monkeypatch):
    # Smoke test: constructing without cache_dir uses the module-level default
    # storage and does not raise.
    monkeypatch.setenv("TRADING212_API_KEY", "k")
    monkeypatch.setenv("TRADING212_API_SECRET", "s")
    client = Trading212Client()
    assert client.client is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cache_isolation.py -v`
Expected: fail on unexpected kwarg `cache_dir` or cross-cache contamination.

- [ ] **Step 3: Modify `src/utils/client.py`**

Change the `__init__` signature and storage construction.

Update the existing import block at the top of the file:

```python
import base64
import httpx
import os
import hishel
from pathlib import Path
from typing import Optional, List, Any

from models import *

from utils.hishel_config import storage as default_storage, controller
```

Replace the `__init__` method:

```python
    def __init__(
        self,
        api_key: str = None,
        api_secret: str = None,
        environment: str = None,
        version: str = "v0",
        cache_dir: Optional[str] = None,
    ):
        api_key = api_key or os.getenv("TRADING212_API_KEY")
        api_secret = api_secret or os.getenv("TRADING212_API_SECRET")
        environment = environment or os.getenv("ENVIRONMENT") or Environment.DEMO.value

        if not api_key:
            raise ValueError("Missing TRADING212_API_KEY")
        if not api_secret:
            raise ValueError("Missing TRADING212_API_SECRET")

        base_url = f"https://{environment}.trading212.com/api/{version}"

        credentials = f"{api_key}:{api_secret}".encode("utf-8")
        encoded_credentials = base64.b64encode(credentials).decode("ascii")

        headers = {
            "Authorization": f"Basic {encoded_credentials}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        if cache_dir is not None:
            Path(cache_dir).mkdir(parents=True, exist_ok=True)
            storage = hishel.FileStorage(base_path=Path(cache_dir), ttl=300)
        else:
            storage = default_storage

        self.client = hishel.CacheClient(
            base_url=base_url,
            storage=storage,
            controller=controller,
            headers=headers,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cache_isolation.py -v`
Expected: 2 passed.

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/utils/client.py tests/test_cache_isolation.py
git commit -m "feat: Trading212Client accepts cache_dir for per-account storage"
```

---

## Task 6: `resolve_client` helper in `mcp_server`

**Files:**
- Modify: `src/mcp_server.py`
- Create: `tests/test_resolve_client.py`

- [ ] **Step 1: Write failing tests for `resolve_client`**

Create `tests/test_resolve_client.py`:

```python
import json
from pathlib import Path

import pytest


@pytest.fixture
def configured_registry(tmp_path, monkeypatch):
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps({
        "default": "A",
        "accounts": [
            {"name": "A", "api_key": "kA", "api_secret": "sA", "environment": "demo"},
            {"name": "B", "api_key": "kB", "api_secret": "sB", "environment": "demo"},
        ],
    }))
    monkeypatch.setenv("TRADING212_ACCOUNTS_FILE", str(path))
    monkeypatch.setenv("TRADING212_CACHE_ROOT", str(tmp_path / "cache"))

    # Force a fresh mcp_server module so it re-reads env vars.
    import importlib, sys
    for mod in ("mcp_server",):
        sys.modules.pop(mod, None)
    import mcp_server as m  # noqa: F401
    return m


def test_resolve_client_defaults_to_registry_default(configured_registry):
    m = configured_registry
    c = m.resolve_client(None)
    assert c is m.registry.get("A")


def test_resolve_client_routes_by_name(configured_registry):
    m = configured_registry
    c = m.resolve_client("B")
    assert c is m.registry.get("B")


def test_resolve_client_unknown_raises(configured_registry):
    m = configured_registry
    with pytest.raises(ValueError) as exc:
        m.resolve_client("Nope")
    assert "Nope" in str(exc.value)


def test_resolve_client_require_explicit_raises_when_none(configured_registry):
    m = configured_registry
    with pytest.raises(ValueError) as exc:
        m.resolve_client(None, require_explicit=True)
    assert "required" in str(exc.value).lower()


def test_resolve_client_require_explicit_routes_when_given(configured_registry):
    m = configured_registry
    c = m.resolve_client("A", require_explicit=True)
    assert c is m.registry.get("A")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_resolve_client.py -v`
Expected: failures — `resolve_client` attribute missing.

- [ ] **Step 3: Rewrite `src/mcp_server.py`**

Replace the whole file:

```python
import os
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP
from dotenv import find_dotenv, load_dotenv

from utils.accounts import AccountRegistry

load_dotenv(find_dotenv())

mcp = FastMCP(
    name="Trading212",
    dependencies=["hishel", "pydantic"],
    stateless_http=True,
    host="127.0.0.1",
    port=8000,
)


def _build_registry() -> AccountRegistry:
    cache_root_env = os.getenv("TRADING212_CACHE_ROOT")
    cache_root = Path(cache_root_env) if cache_root_env else None
    repo_root = Path(__file__).resolve().parent.parent
    reg = AccountRegistry.load(repo_root_path=repo_root)
    if cache_root is not None:
        reg._cache_root = cache_root  # test/override hook
    return reg


registry: AccountRegistry = _build_registry()


def resolve_client(
    account: Optional[str] = None,
    *,
    require_explicit: bool = False,
):
    """Return the Trading212Client for the given account.

    - `account=None` + `require_explicit=False` → default account.
    - `account=None` + `require_explicit=True`  → raise ValueError.
    - Unknown name → raise ValueError.
    """
    if account is None:
        if require_explicit:
            raise ValueError(
                f"account is required for write operations. Known: {registry.names()}"
            )
        account = registry.default_name
    return registry.get(account)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_resolve_client.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/mcp_server.py tests/test_resolve_client.py
git commit -m "feat: resolve_client helper with require_explicit gate"
```

---

## Task 7: Read-only tools gain `account=` + response envelope; write tools require `account=`; add `list_accounts`

**Files:**
- Modify: `src/tools.py`
- Create: `tests/test_tools_multi_account.py`

> Note: this is a larger task because every tool changes in the same mechanical way. Steps are granular: tests first for the new behaviour, then a single file rewrite.

- [ ] **Step 1: Write failing behaviour tests**

Create `tests/test_tools_multi_account.py`:

```python
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def tools_module(tmp_path, monkeypatch):
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps({
        "default": "A",
        "accounts": [
            {"name": "A", "api_key": "kA", "api_secret": "sA", "environment": "demo"},
            {"name": "B", "api_key": "kB", "api_secret": "sB", "environment": "demo"},
        ],
    }))
    monkeypatch.setenv("TRADING212_ACCOUNTS_FILE", str(path))
    monkeypatch.setenv("TRADING212_CACHE_ROOT", str(tmp_path / "cache"))

    import importlib, sys
    for mod in ("tools", "resources", "mcp_server"):
        sys.modules.pop(mod, None)

    import mcp_server as m
    import tools as t  # noqa: F401

    # Replace per-account clients with mocks so tests don't hit HTTP.
    mock_a = MagicMock(name="client_A")
    mock_b = MagicMock(name="client_B")
    m.registry._clients["A"] = mock_a
    m.registry._clients["B"] = mock_b

    return m, t, mock_a, mock_b


def test_fetch_account_cash_defaults_to_default_account(tools_module):
    m, t, mock_a, mock_b = tools_module
    mock_a.get_account_cash.return_value = {"free": 100}

    result = t.fetch_account_cash()

    mock_a.get_account_cash.assert_called_once()
    mock_b.get_account_cash.assert_not_called()
    assert result == {"account": "A", "cash": {"free": 100}}


def test_fetch_account_cash_routes_by_account_param(tools_module):
    m, t, mock_a, mock_b = tools_module
    mock_b.get_account_cash.return_value = {"free": 999}

    result = t.fetch_account_cash(account="B")

    mock_b.get_account_cash.assert_called_once()
    mock_a.get_account_cash.assert_not_called()
    assert result == {"account": "B", "cash": {"free": 999}}


def test_place_market_order_without_account_raises(tools_module):
    _, t, _, _ = tools_module
    with pytest.raises(ValueError) as exc:
        t.place_market_order(ticker="AAPL_US_EQ", quantity=1)
    assert "required" in str(exc.value).lower()


def test_place_market_order_routes_with_account(tools_module):
    _, t, mock_a, mock_b = tools_module
    mock_a.place_market_order.return_value = {"id": 42}

    result = t.place_market_order(ticker="AAPL_US_EQ", quantity=1, account="A")

    mock_a.place_market_order.assert_called_once()
    assert result == {"id": 42}


def test_list_accounts_returns_names_and_default(tools_module):
    _, t, _, _ = tools_module
    result = t.list_accounts()
    assert result["default"] == "A"
    assert set(result["accounts"]) == {"A", "B"}


def test_fetch_all_orders_envelopes_list(tools_module):
    _, t, mock_a, _ = tools_module
    mock_a.get_orders.return_value = [{"id": 1}, {"id": 2}]
    result = t.fetch_all_orders()
    assert result == {"account": "A", "orders": [{"id": 1}, {"id": 2}]}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tools_multi_account.py -v`
Expected: failures — current tool signatures don't accept `account`, no `list_accounts`, envelopes not applied.

- [ ] **Step 3: Replace `src/tools.py`**

Write the full updated `src/tools.py`:

```python
from typing import Optional, Any, Dict

from mcp_server import mcp, resolve_client, registry
from models import *


def _envelope(account: Optional[str], key: str, value: Any) -> Dict[str, Any]:
    """Wrap a read-only response with the account name it came from."""
    name = account if account is not None else registry.default_name
    return {"account": name, key: value}


# Accounts

@mcp.tool("list_accounts")
def list_accounts() -> dict:
    """List configured Trading 212 accounts and the default.

    Returns:
        {"default": "<name>", "accounts": ["<name>", ...]}
    """
    return {"default": registry.default_name, "accounts": registry.names()}


# Instruments Metadata
@mcp.tool("search_instrument")
def search_instrument(search_term: str = None, account: Optional[str] = None) -> dict:
    """Fetch instruments, optionally filtered by ticker or name.

    Args:
        search_term: case-insensitive filter on ticker or name.
        account: optional account name; defaults to the configured default.
    """
    client = resolve_client(account)
    instruments = client.get_instruments()
    if search_term:
        s = search_term.lower()
        instruments = [
            i for i in instruments
            if (i.ticker and s in i.ticker.lower())
            or (i.name and s in i.name.lower())
        ]
    return _envelope(account, "instruments", instruments)


@mcp.tool("search_exchange")
def search_exchange(search_term: str = None, account: Optional[str] = None) -> dict:
    """Fetch exchanges, optionally filtered by name or ID."""
    client = resolve_client(account)
    exchanges = client.get_exchanges()
    if search_term:
        s = search_term.lower()
        exchanges = [
            e for e in exchanges
            if (e.name and s in e.name.lower()) or (str(e.id) == search_term)
        ]
    return _envelope(account, "exchanges", exchanges)


# Pies (read)

@mcp.tool("fetch_pies")
def fetch_pies(account: Optional[str] = None) -> dict:
    """Fetch all pies."""
    client = resolve_client(account)
    return _envelope(account, "pies", client.get_pies())


@mcp.tool("fetch_a_pie")
def fetch_a_pie(pie_id: int, account: Optional[str] = None) -> dict:
    """Fetch a specific pie by ID."""
    client = resolve_client(account)
    return _envelope(account, "pie", client.get_pie_by_id(pie_id))


# Pies (write)

@mcp.tool("create_pie")
def create_pie(
    name: str,
    instrument_shares: dict,
    account: str,
    dividend_cash_action: Optional[DividendCashActionEnum] = None,
    end_date: Optional[datetime] = None,
    goal: Optional[float] = None,
    icon: Optional[str] = None,
):
    """Create a new pie. `account` is required."""
    client = resolve_client(account, require_explicit=True)
    pie_data = PieRequest(
        name=name,
        instrumentShares=instrument_shares,
        dividendCashAction=dividend_cash_action,
        endDate=end_date,
        goal=goal,
        icon=icon,
    )
    return client.create_pie(pie_data)


@mcp.tool("delete_pie")
def delete_pie(pie_id: int, account: str):
    """Delete a pie. `account` is required."""
    client = resolve_client(account, require_explicit=True)
    return client.delete_pie(pie_id)


@mcp.tool("update_pie")
def update_pie(
    pie_id: int,
    account: str,
    name: str = None,
    instrument_shares: dict = None,
    dividend_cash_action: Optional[DividendCashActionEnum] = None,
    end_date: Optional[datetime] = None,
    goal: Optional[float] = None,
    icon: Optional[str] = None,
):
    """Update a pie. `account` is required."""
    client = resolve_client(account, require_explicit=True)
    pie_data = PieRequest(
        name=name,
        instrumentShares=instrument_shares,
        dividendCashAction=dividend_cash_action,
        endDate=end_date,
        goal=goal,
        icon=icon,
    )
    return client.update_pie(pie_id, pie_data)


@mcp.tool("duplicate_pie")
def duplicate_pie(
    pie_id: int,
    account: str,
    name: Optional[str] = None,
    icon: Optional[str] = None,
):
    """Duplicate a pie. `account` is required."""
    client = resolve_client(account, require_explicit=True)
    return client.duplicate_pie(pie_id, DuplicateBucketRequest(name=name, icon=icon))


# Orders (read)

@mcp.tool("fetch_all_orders")
def fetch_all_orders(account: Optional[str] = None) -> dict:
    """Fetch all equity orders."""
    client = resolve_client(account)
    return _envelope(account, "orders", client.get_orders())


@mcp.tool("fetch_order")
def fetch_order(order_id: int, account: Optional[str] = None) -> dict:
    """Fetch a specific order by ID."""
    client = resolve_client(account)
    return _envelope(account, "order", client.get_order_by_id(order_id))


# Orders (write)

@mcp.tool("place_limit_order")
def place_limit_order(
    ticker: str,
    quantity: float,
    limit_price: float,
    account: str,
    time_validity: LimitRequestTimeValidityEnum = LimitRequestTimeValidityEnum.DAY,
):
    """Place a limit order. `account` is required."""
    client = resolve_client(account, require_explicit=True)
    return client.place_limit_order(LimitRequest(
        ticker=ticker, quantity=quantity,
        limitPrice=limit_price, timeValidity=time_validity,
    ))


@mcp.tool("place_market_order")
def place_market_order(ticker: str, quantity: float, account: str):
    """Place a market order. `account` is required."""
    client = resolve_client(account, require_explicit=True)
    return client.place_market_order(MarketRequest(ticker=ticker, quantity=quantity))


@mcp.tool("place_stop_order")
def place_stop_order(
    ticker: str,
    quantity: float,
    stop_price: float,
    account: str,
    time_validity: StopRequestTimeValidityEnum = StopRequestTimeValidityEnum.DAY,
):
    """Place a stop order. `account` is required."""
    client = resolve_client(account, require_explicit=True)
    return client.place_stop_order(StopRequest(
        ticker=ticker, quantity=quantity,
        stopPrice=stop_price, timeValidity=time_validity,
    ))


@mcp.tool("place_stop_limit_order")
def place_stop_limit_order(
    ticker: str,
    quantity: float,
    stop_price: float,
    limit_price: float,
    account: str,
    time_validity: StopLimitRequestTimeValidityEnum = StopLimitRequestTimeValidityEnum.DAY,
):
    """Place a stop-limit order. `account` is required."""
    client = resolve_client(account, require_explicit=True)
    return client.place_stop_limit_order(StopLimitRequest(
        ticker=ticker, quantity=quantity,
        stopPrice=stop_price, limitPrice=limit_price,
        timeValidity=time_validity,
    ))


@mcp.tool("cancel_order")
def cancel_order(order_id: int, account: str) -> None:
    """Cancel an existing order. `account` is required."""
    client = resolve_client(account, require_explicit=True)
    return client.cancel_order(order_id)


# Account data (read)

@mcp.tool("fetch_account_info")
def fetch_account_info(account: Optional[str] = None) -> dict:
    """Fetch account metadata."""
    client = resolve_client(account)
    return _envelope(account, "info", client.get_account_info())


@mcp.tool("fetch_account_cash")
def fetch_account_cash(account: Optional[str] = None) -> dict:
    """Fetch account cash balance."""
    client = resolve_client(account)
    return _envelope(account, "cash", client.get_account_cash())


# Portfolio (read)

@mcp.tool("fetch_all_open_positions")
def fetch_all_open_positions(account: Optional[str] = None) -> dict:
    """Fetch all open positions."""
    client = resolve_client(account)
    return _envelope(account, "positions", client.get_account_positions())


@mcp.tool("fetch_open_position_by_ticker")
def fetch_open_position_by_ticker(ticker: str, account: Optional[str] = None) -> dict:
    """Fetch a position by ticker (deprecated)."""
    client = resolve_client(account)
    return _envelope(account, "position", client.get_account_position_by_ticker(ticker))


@mcp.tool("search_specific_position_by_ticker")
def search_specific_position_by_ticker(ticker: str, account: Optional[str] = None) -> dict:
    """Search for a position by ticker (POST)."""
    client = resolve_client(account)
    return _envelope(account, "position", client.search_position_by_ticker(ticker))


# History (read)

@mcp.tool("fetch_historical_order_data")
def fetch_historical_order_data(
    cursor: int = None, ticker: str = None, limit: int = 20,
    account: Optional[str] = None,
) -> dict:
    """Fetch historical order data with pagination."""
    client = resolve_client(account)
    return _envelope(
        account, "orders",
        client.get_historical_order_data(cursor=cursor, ticker=ticker, limit=limit),
    )


@mcp.tool("fetch_paid_out_dividends")
def fetch_paid_out_dividends(
    cursor: int = None, ticker: str = None, limit: int = 20,
    account: Optional[str] = None,
) -> dict:
    """Fetch historical dividend data with pagination."""
    client = resolve_client(account)
    return _envelope(
        account, "dividends",
        client.get_dividends(cursor=cursor, ticker=ticker, limit=limit),
    )


@mcp.tool("fetch_exports_list")
def fetch_exports_list(account: Optional[str] = None) -> dict:
    """List CSV account exports."""
    client = resolve_client(account)
    return _envelope(account, "exports", client.get_reports())


@mcp.tool("fetch_transaction_list")
def fetch_transaction_list(
    cursor: str | None = None, time: str | None = None, limit: int = 20,
    account: Optional[str] = None,
) -> dict:
    """Fetch movements to and from the account."""
    client = resolve_client(account)
    return _envelope(
        account, "transactions",
        client.get_history_transactions(cursor=cursor, time_from=time, limit=limit),
    )


# History (write — CSV export requests state)

@mcp.tool("request_csv_export")
def request_csv_export(
    account: str,
    include_dividends: bool = True,
    include_interest: bool = True,
    include_orders: bool = True,
    include_transactions: bool = True,
    time_from: str = None,
    time_to: str = None,
):
    """Request a CSV export. `account` is required."""
    client = resolve_client(account, require_explicit=True)
    data_included = ReportDataIncluded(
        includeDividends=include_dividends,
        includeInterest=include_interest,
        includeOrders=include_orders,
        includeTransactions=include_transactions,
    )
    return client.request_export(
        data_included=data_included, time_from=time_from, time_to=time_to,
    )
```

- [ ] **Step 4: Run tool tests to verify they pass**

Run: `uv run pytest tests/test_tools_multi_account.py -v`
Expected: 6 passed.

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/tools.py tests/test_tools_multi_account.py
git commit -m "feat: multi-account tools with write-guard and response envelope"
```

---

## Task 8: Account-prefixed MCP resources

**Files:**
- Modify: `src/resources.py`

- [ ] **Step 1: Replace `src/resources.py`**

```python
from mcp_server import mcp, resolve_client
from models import (
    Account, Cash, Position, Order,
    AccountBucketResultResponse,
    Exchange, TradeableInstrument, ReportResponse,
)


# Default-account resources (back-compat)

@mcp.resource("trading212://account/info")
def get_account_info() -> Account:
    return resolve_client(None).get_account_info()


@mcp.resource("trading212://account/cash")
def get_account_cash() -> Cash:
    return resolve_client(None).get_account_cash()


@mcp.resource("trading212://account/portfolio")
def get_account_positions() -> list[Position]:
    return resolve_client(None).get_account_positions()


@mcp.resource("trading212://account/portfolio/{ticker}")
def get_account_position_by_ticker(ticker: str) -> Position:
    return resolve_client(None).get_account_position_by_ticker(ticker)


@mcp.resource("trading212://orders")
def get_orders() -> list[Order]:
    return resolve_client(None).get_orders()


@mcp.resource("trading212://orders/{order_id}")
def get_order_by_id(order_id: int) -> Order:
    return resolve_client(None).get_order_by_id(order_id)


@mcp.resource("trading212://pies")
def get_pies() -> list[AccountBucketResultResponse]:
    return resolve_client(None).get_pies()


@mcp.resource("trading212://pies/{pie_id}")
def get_pie_by_id(pie_id: int) -> AccountBucketResultResponse:
    return resolve_client(None).get_pie_by_id(pie_id)


@mcp.resource("trading212://history/exports")
def get_reports() -> list[ReportResponse]:
    return resolve_client(None).get_reports()


# Account-prefixed resources

@mcp.resource("trading212://account/{account}/info")
def get_account_info_for(account: str) -> Account:
    return resolve_client(account).get_account_info()


@mcp.resource("trading212://account/{account}/cash")
def get_account_cash_for(account: str) -> Cash:
    return resolve_client(account).get_account_cash()


@mcp.resource("trading212://account/{account}/portfolio")
def get_account_positions_for(account: str) -> list[Position]:
    return resolve_client(account).get_account_positions()


@mcp.resource("trading212://account/{account}/portfolio/{ticker}")
def get_account_position_by_ticker_for(account: str, ticker: str) -> Position:
    return resolve_client(account).get_account_position_by_ticker(ticker)


@mcp.resource("trading212://account/{account}/orders")
def get_orders_for(account: str) -> list[Order]:
    return resolve_client(account).get_orders()


@mcp.resource("trading212://account/{account}/orders/{order_id}")
def get_order_by_id_for(account: str, order_id: int) -> Order:
    return resolve_client(account).get_order_by_id(order_id)


@mcp.resource("trading212://account/{account}/pies")
def get_pies_for(account: str) -> list[AccountBucketResultResponse]:
    return resolve_client(account).get_pies()


@mcp.resource("trading212://account/{account}/pies/{pie_id}")
def get_pie_by_id_for(account: str, pie_id: int) -> AccountBucketResultResponse:
    return resolve_client(account).get_pie_by_id(pie_id)


@mcp.resource("trading212://account/{account}/history/exports")
def get_reports_for(account: str) -> list[ReportResponse]:
    return resolve_client(account).get_reports()


# Market data — not account-scoped

@mcp.resource("trading212://instruments")
def get_instruments() -> list[TradeableInstrument]:
    return resolve_client(None).get_instruments()


@mcp.resource("trading212://exchanges")
def get_exchanges() -> list[Exchange]:
    return resolve_client(None).get_exchanges()
```

- [ ] **Step 2: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: all tests pass; no import errors from the new module.

- [ ] **Step 3: Commit**

```bash
git add src/resources.py
git commit -m "feat: add account-prefixed MCP resource URIs"
```

---

## Task 9: `.gitignore` + README

**Files:**
- Modify: `.gitignore`
- Modify: `README.md`

- [ ] **Step 1: Update `.gitignore`**

Append to `.gitignore`:

```
# Account credentials — holds live API keys, never commit
accounts.json

# Multi-account hishel cache (if kept inside repo)
.trading212-cache/
```

- [ ] **Step 2: Add multi-account section to `README.md`**

After the "Environment Configuration" section in `README.md`, insert:

````markdown
### Multi-account setup

The server can hold credentials for multiple Trading 212 accounts at once and
route each tool call to a chosen account.

Create an `accounts.json` at the repo root (or point at it with the
`TRADING212_ACCOUNTS_FILE` env var):

```json
{
  "default": "Personal",
  "accounts": [
    {
      "name": "Personal",
      "api_key": "...",
      "api_secret": "...",
      "environment": "live"
    },
    {
      "name": "Family",
      "api_key": "...",
      "api_secret": "...",
      "environment": "live"
    }
  ]
}
```

- Every tool accepts an optional `account="<name>"` argument.
- Read-only tools default to the `default` account when omitted.
- Write tools (`place_*_order`, `cancel_order`, `create_pie`, `update_pie`,
  `delete_pie`, `duplicate_pie`, `request_csv_export`) **require** `account=`
  — they raise if omitted, to prevent silent routing to the wrong account.
- Use the `list_accounts` tool to see configured names and the default.
- Account-prefixed resource URIs are also available, e.g.
  `trading212://account/Personal/cash`.

`accounts.json` is gitignored. **Do not commit it.** If a key is ever exposed,
rotate it from the Trading 212 dashboard immediately.

If `accounts.json` is absent, the server falls back to the old single-account
env-var path (`TRADING212_API_KEY` / `TRADING212_API_SECRET` /
`ENVIRONMENT`) and exposes the sole account under the name `default`.

Per-account hishel cache directories are written under
`~/.trading212/cache/<account>/` by default. Override the root with the
`TRADING212_CACHE_ROOT` env var if needed.
````

- [ ] **Step 3: Confirm the README renders**

Run: `grep -n "Multi-account setup" README.md`
Expected: one match.

- [ ] **Step 4: Commit**

```bash
git add .gitignore README.md
git commit -m "docs: document multi-account setup and gitignore accounts.json"
```

---

## Task 10: Manual smoke test

**Files:** none (manual verification)

- [ ] **Step 1: Ensure `accounts.json` at repo root has both accounts**

Run: `python -c "import json; print(json.load(open('accounts.json'))['default'])"`
Expected: prints the default account name.

- [ ] **Step 2: Start the MCP server**

Run: `uv run src/server.py`
Expected: starts without errors (Ctrl-C to stop once verified).

- [ ] **Step 3: From Claude, call `list_accounts`**

Expected: returns both configured account names and the default.

- [ ] **Step 4: From Claude, fetch cash for each account**

Call `fetch_account_cash(account="<first>")` then `fetch_account_cash(account="<second>")`.
Expected: two distinct responses, each wrapped with its `account` name.

- [ ] **Step 5: From Claude, attempt `place_market_order` without `account=`**

Expected: error mentioning `account is required for write operations`.

- [ ] **Step 6: Final commit summary**

Run: `git log --oneline -n 12`
Expected: one commit per task, clean history.

---

## Scope check (self-review)

**Spec coverage:**
- Account selection per tool → Tasks 6, 7.
- `list_accounts` tool → Task 7.
- Config file + env-var fallback → Task 3.
- `AccountRegistry` → Tasks 3, 4.
- Response envelope for read tools → Task 7.
- Resource URIs (existing + prefixed) → Task 8.
- `cache_dir` per-account isolation → Task 5.
- Error handling startup / unknown name / write without account → Tasks 3, 6, 7.
- `.gitignore` + README → Task 9.
- Manual smoke test → Task 10.

All spec sections covered.

**Placeholder scan:** no TBDs; every code block is complete; every test names its expected outcome.

**Type consistency:** `resolve_client(account, *, require_explicit=False)` signature is identical across mcp_server, tools, resources, and tests. `registry.get(name)` and `registry.names()` match the shape used in every consumer.
