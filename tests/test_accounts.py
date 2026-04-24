import json
import os
import pytest
from unittest.mock import patch, MagicMock


def make_config(tmp_path, accounts, default):
    config = {"default": default, "accounts": accounts}
    p = tmp_path / "accounts.json"
    p.write_text(json.dumps(config))
    return str(p)


@patch("accounts.Trading212Client")
def test_loads_accounts_from_file(MockClient, tmp_path):
    path = make_config(tmp_path, [
        {"name": "sumeet", "api_key": "k1", "api_secret": "s1", "environment": "live"},
        {"name": "wife",   "api_key": "k2", "api_secret": "s2", "environment": "live"},
    ], default="sumeet")

    from accounts import AccountRegistry
    registry = AccountRegistry(config_path=path)

    assert registry.account_names() == ["sumeet", "wife"]
    assert registry.default_name() == "sumeet"
    assert MockClient.call_count == 2


@patch("accounts.Trading212Client")
def test_get_client_returns_correct_instance(MockClient, tmp_path):
    mock_sumeet = MagicMock()
    mock_wife = MagicMock()
    MockClient.side_effect = [mock_sumeet, mock_wife]

    path = make_config(tmp_path, [
        {"name": "sumeet", "api_key": "k1", "api_secret": "s1", "environment": "live"},
        {"name": "wife",   "api_key": "k2", "api_secret": "s2", "environment": "live"},
    ], default="sumeet")

    from accounts import AccountRegistry
    registry = AccountRegistry(config_path=path)

    assert registry.get_client("sumeet") is mock_sumeet
    assert registry.get_client("wife") is mock_wife


@patch("accounts.Trading212Client")
def test_get_client_raises_on_unknown_account(MockClient, tmp_path):
    path = make_config(tmp_path, [
        {"name": "sumeet", "api_key": "k1", "api_secret": "s1", "environment": "live"},
    ], default="sumeet")

    from accounts import AccountRegistry
    registry = AccountRegistry(config_path=path)

    with pytest.raises(ValueError, match="Account 'xyz' not found"):
        registry.get_client("xyz")


@patch("accounts.Trading212Client")
def test_all_clients_returns_all(MockClient, tmp_path):
    path = make_config(tmp_path, [
        {"name": "sumeet", "api_key": "k1", "api_secret": "s1", "environment": "live"},
        {"name": "wife",   "api_key": "k2", "api_secret": "s2", "environment": "live"},
    ], default="sumeet")

    from accounts import AccountRegistry
    registry = AccountRegistry(config_path=path)

    all_clients = registry.all_clients()
    assert set(all_clients.keys()) == {"sumeet", "wife"}


@patch("accounts.Trading212Client")
def test_resolve_none_returns_default(MockClient, tmp_path):
    path = make_config(tmp_path, [
        {"name": "sumeet", "api_key": "k1", "api_secret": "s1", "environment": "live"},
        {"name": "wife",   "api_key": "k2", "api_secret": "s2", "environment": "live"},
    ], default="sumeet")

    from accounts import AccountRegistry
    registry = AccountRegistry(config_path=path)

    result = registry.resolve(None)
    assert list(result.keys()) == ["sumeet"]


@patch("accounts.Trading212Client")
def test_resolve_all_returns_all_accounts(MockClient, tmp_path):
    path = make_config(tmp_path, [
        {"name": "sumeet", "api_key": "k1", "api_secret": "s1", "environment": "live"},
        {"name": "wife",   "api_key": "k2", "api_secret": "s2", "environment": "live"},
    ], default="sumeet")

    from accounts import AccountRegistry
    registry = AccountRegistry(config_path=path)

    result = registry.resolve("all")
    assert set(result.keys()) == {"sumeet", "wife"}


@patch("accounts.Trading212Client")
def test_resolve_string_returns_named_account(MockClient, tmp_path):
    path = make_config(tmp_path, [
        {"name": "sumeet", "api_key": "k1", "api_secret": "s1", "environment": "live"},
        {"name": "wife",   "api_key": "k2", "api_secret": "s2", "environment": "live"},
    ], default="sumeet")

    from accounts import AccountRegistry
    registry = AccountRegistry(config_path=path)

    result = registry.resolve("wife")
    assert list(result.keys()) == ["wife"]


@patch("accounts.Trading212Client")
def test_resolve_list_returns_subset(MockClient, tmp_path):
    path = make_config(tmp_path, [
        {"name": "sumeet", "api_key": "k1", "api_secret": "s1", "environment": "live"},
        {"name": "wife",   "api_key": "k2", "api_secret": "s2", "environment": "live"},
        {"name": "son",    "api_key": "k3", "api_secret": "s3", "environment": "demo"},
    ], default="sumeet")

    from accounts import AccountRegistry
    registry = AccountRegistry(config_path=path)

    result = registry.resolve(["wife", "son"])
    assert set(result.keys()) == {"wife", "son"}


@patch("accounts.Trading212Client")
def test_fallback_to_env_vars_when_no_config_file(MockClient):
    with patch.dict(os.environ, {
        "TRADING212_API_KEY": "envkey",
        "TRADING212_API_SECRET": "envsecret",
        "ENVIRONMENT": "live",
    }):
        from accounts import AccountRegistry
        registry = AccountRegistry(config_path="/nonexistent/path/accounts.json")

    assert registry.default_name() == "default"
    assert "default" in registry.account_names()
    MockClient.assert_called_once_with(
        api_key="envkey", api_secret="envsecret", environment="live"
    )


def test_raises_when_no_config_and_no_env_vars():
    with patch.dict(os.environ, {}, clear=True):
        for key in ["TRADING212_API_KEY", "TRADING212_API_SECRET"]:
            os.environ.pop(key, None)

        from accounts import AccountRegistry
        with pytest.raises(ValueError, match="No accounts configured"):
            AccountRegistry(config_path="/nonexistent/path/accounts.json")


@patch("accounts.Trading212Client")
def test_load_with_bad_default_raises_at_load_time(MockClient, tmp_path):
    # default names an account that doesn't exist in the accounts list
    import json as _json
    p = tmp_path / "accounts.json"
    p.write_text(_json.dumps({
        "default": "typo",
        "accounts": [
            {"name": "sumeet", "api_key": "k", "api_secret": "s", "environment": "live"},
        ],
    }))

    from accounts import AccountRegistry
    with pytest.raises(ValueError, match="default"):
        AccountRegistry(config_path=str(p))


@patch("accounts.Trading212Client")
def test_raises_helpful_error_when_default_key_missing(MockClient, tmp_path):
    p = tmp_path / "accounts.json"
    p.write_text(json.dumps({"accounts": []}))

    from accounts import AccountRegistry
    with pytest.raises(ValueError, match="default"):
        AccountRegistry(config_path=str(p))


@patch("accounts.Trading212Client")
def test_raises_helpful_error_when_accounts_key_missing(MockClient, tmp_path):
    p = tmp_path / "accounts.json"
    p.write_text(json.dumps({"default": "sumeet"}))

    from accounts import AccountRegistry
    with pytest.raises(ValueError, match="accounts"):
        AccountRegistry(config_path=str(p))


@patch("accounts.Trading212Client")
def test_registry_passes_distinct_cache_dirs_per_account(MockClient, tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING212_CACHE_ROOT", str(tmp_path / "cache"))
    path = make_config(tmp_path, [
        {"name": "sumeet", "api_key": "k1", "api_secret": "s1", "environment": "live"},
        {"name": "wife",   "api_key": "k2", "api_secret": "s2", "environment": "live"},
    ], default="sumeet")

    from accounts import AccountRegistry
    AccountRegistry(config_path=path)

    cache_dirs = [call.kwargs["cache_dir"] for call in MockClient.call_args_list]
    assert len(cache_dirs) == len(set(cache_dirs))  # all distinct
    assert len(cache_dirs) == 2
    assert all(str(tmp_path / "cache") in d for d in cache_dirs)


@patch("accounts.Trading212Client")
def test_duplicate_account_names_rejected_at_load(MockClient, tmp_path):
    path = make_config(tmp_path, [
        {"name": "sumeet", "api_key": "k1", "api_secret": "s1", "environment": "live"},
        {"name": "sumeet", "api_key": "k2", "api_secret": "s2", "environment": "live"},
    ], default="sumeet")

    from accounts import AccountRegistry
    with pytest.raises(ValueError, match="duplicate"):
        AccountRegistry(config_path=path)


@patch("accounts.Trading212Client")
def test_unknown_environment_rejected_at_load(MockClient, tmp_path):
    path = make_config(tmp_path, [
        {"name": "sumeet", "api_key": "k", "api_secret": "s", "environment": "staging"},
    ], default="sumeet")

    from accounts import AccountRegistry
    with pytest.raises(ValueError):
        AccountRegistry(config_path=path)


@patch("accounts.Trading212Client")
def test_empty_accounts_list_rejected_at_load(MockClient, tmp_path):
    import json as _json
    p = tmp_path / "accounts.json"
    p.write_text(_json.dumps({"default": "sumeet", "accounts": []}))

    from accounts import AccountRegistry
    with pytest.raises(ValueError):
        AccountRegistry(config_path=str(p))


@patch("accounts.Trading212Client")
def test_missing_per_account_field_rejected_at_load(MockClient, tmp_path):
    import json as _json
    p = tmp_path / "accounts.json"
    p.write_text(_json.dumps({
        "default": "sumeet",
        "accounts": [{"name": "sumeet", "api_key": "k", "environment": "live"}],
    }))

    from accounts import AccountRegistry
    with pytest.raises(ValueError):
        AccountRegistry(config_path=str(p))


@patch("accounts.Trading212Client")
def test_invalid_json_rejected_at_load(MockClient, tmp_path):
    p = tmp_path / "accounts.json"
    p.write_text("{not json")

    from accounts import AccountRegistry
    with pytest.raises(ValueError, match="not valid JSON"):
        AccountRegistry(config_path=str(p))
