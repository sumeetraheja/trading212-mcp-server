import json
import os
from pathlib import Path
from typing import List, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from utils.client import Trading212Client


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


class AccountRegistry:
    def __init__(self, config_path: str | None = None):
        config_path = config_path or os.getenv("ACCOUNTS_CONFIG", "accounts.json")
        self._clients: dict[str, Trading212Client] = {}
        self._default: str | None = None

        if os.path.exists(config_path):
            self._load_from_file(config_path)
        else:
            self._load_from_env()

    def _load_from_file(self, config_path: str) -> None:
        with open(config_path) as f:
            raw = f.read()

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"accounts file at {config_path!r} is not valid JSON: {e}"
            ) from e

        try:
            validated = AccountsFile.model_validate(data)
        except ValidationError as e:
            raise ValueError(
                f"invalid accounts file {config_path!r}: {e}"
            ) from e

        cache_root = Path(
            os.getenv("TRADING212_CACHE_ROOT")
            or (Path.home() / ".trading212" / "cache")
        )

        self._default = validated.default
        for account in validated.accounts:
            account_cache_dir = cache_root / account.name
            self._clients[account.name] = Trading212Client(
                api_key=account.api_key,
                api_secret=account.api_secret,
                environment=account.environment,
                cache_dir=str(account_cache_dir),
            )

    def _load_from_env(self) -> None:
        api_key = os.getenv("TRADING212_API_KEY")
        api_secret = os.getenv("TRADING212_API_SECRET")
        environment = os.getenv("ENVIRONMENT", "demo")

        if not api_key or not api_secret:
            raise ValueError(
                "No accounts configured. Create accounts.json (or set ACCOUNTS_CONFIG to "
                "point to one), or set TRADING212_API_KEY and TRADING212_API_SECRET."
            )

        self._clients = {
            "default": Trading212Client(
                api_key=api_key, api_secret=api_secret, environment=environment
            )
        }
        self._default = "default"

    def get_client(self, name: str) -> Trading212Client:
        if name not in self._clients:
            available = ", ".join(self._clients.keys())
            raise ValueError(
                f"Account '{name}' not found. Available accounts: {available}"
            )
        return self._clients[name]

    def get_clients(self, names: list[str]) -> dict[str, Trading212Client]:
        return {name: self.get_client(name) for name in names}

    def all_clients(self) -> dict[str, Trading212Client]:
        return dict(self._clients)

    def account_names(self) -> list[str]:
        return list(self._clients.keys())

    def default_name(self) -> str:
        return self._default

    def resolve(self, account: str | list[str] | None) -> dict[str, Trading212Client]:
        if account is None:
            return {self._default: self.get_client(self._default)}
        if account == "all":
            return self.all_clients()
        if isinstance(account, list):
            return self.get_clients(account)
        return {account: self.get_client(account)}
