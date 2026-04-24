import json
import sys
import pytest
from unittest.mock import patch, MagicMock


def _make_config(tmp_path):
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps({
        "default": "a",
        "accounts": [
            {"name": "a", "api_key": "ka", "api_secret": "sa", "environment": "demo"},
            {"name": "b", "api_key": "kb", "api_secret": "sb", "environment": "demo"},
        ],
    }))
    return str(path)


def _reload_resources():
    """Force a fresh import of mcp_server and resources so they re-read env."""
    for mod in ("resources", "mcp_server"):
        sys.modules.pop(mod, None)
    import resources
    return resources


@pytest.mark.parametrize(
    "resource_func_name, client_method, kwargs",
    [
        ("get_account_info_for", "get_account_info", {}),
        ("get_account_cash_for", "get_account_cash", {}),
        ("get_account_positions_for", "get_account_positions", {}),
        ("get_account_position_by_ticker_for", "get_account_position_by_ticker", {"ticker": "AAPL"}),
        ("get_orders_for", "get_orders", {}),
        ("get_order_by_id_for", "get_order_by_id", {"order_id": 42}),
        ("get_pies_for", "get_pies", {}),
        ("get_pie_by_id_for", "get_pie_by_id", {"pie_id": 7}),
        ("get_reports_for", "get_reports", {}),
    ],
)
@patch("accounts.Trading212Client")
def test_prefixed_resource_routes_to_named_account(
    MockClient, resource_func_name, client_method, kwargs, tmp_path, monkeypatch
):
    mock_a, mock_b = MagicMock(), MagicMock()
    MockClient.side_effect = [mock_a, mock_b]

    monkeypatch.setenv("ACCOUNTS_CONFIG", _make_config(tmp_path))

    r = _reload_resources()

    sentinel = f"result-from-b-{client_method}"
    getattr(mock_b, client_method).return_value = sentinel

    func = getattr(r, resource_func_name)
    result = func(account="b", **kwargs)

    assert result == sentinel
    # The correct method on mock_b was called once with the forwarded args
    getattr(mock_b, client_method).assert_called_once()
    call_args = getattr(mock_b, client_method).call_args
    # Positional args should match kwargs values (in dict insertion order)
    assert list(call_args.args) == list(kwargs.values())
    # Account "a" was not touched for this call
    getattr(mock_a, client_method).assert_not_called()


@patch("accounts.Trading212Client")
def test_prefixed_resource_unknown_account_raises(MockClient, tmp_path, monkeypatch):
    MockClient.side_effect = [MagicMock(), MagicMock()]
    monkeypatch.setenv("ACCOUNTS_CONFIG", _make_config(tmp_path))

    r = _reload_resources()

    with pytest.raises(ValueError, match="not found"):
        r.get_account_cash_for(account="unknown")
