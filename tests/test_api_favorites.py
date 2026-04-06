import json

import httpx

from kptncook.api import KptnCookClient, _extract_favorites_payload


def test_extract_favorites_payload_from_list():
    payload = [{"identifier": "abc"}]
    favorites, found, invalid = _extract_favorites_payload(payload)
    assert favorites == payload
    assert found is True
    assert invalid is False


def test_extract_favorites_payload_empty_list():
    favorites, found, invalid = _extract_favorites_payload([])
    assert favorites == []
    assert found is True
    assert invalid is False


def test_extract_favorites_payload_none():
    favorites, found, invalid = _extract_favorites_payload(None)
    assert favorites == []
    assert found is False
    assert invalid is False


def test_extract_favorites_payload_from_dict():
    payload = {"favorites": [{"identifier": "abc"}]}
    favorites, found, invalid = _extract_favorites_payload(payload)
    assert favorites == payload["favorites"]
    assert found is True
    assert invalid is False


def test_extract_favorites_payload_from_nested_dict():
    payload = {"data": {"items": [{"identifier": "abc"}]}}
    favorites, found, invalid = _extract_favorites_payload(payload)
    assert favorites == payload["data"]["items"]
    assert found is True
    assert invalid is False


def test_extract_favorites_payload_invalid_type():
    payload = {"favorites": {"identifier": "abc"}}
    favorites, found, invalid = _extract_favorites_payload(payload)
    assert favorites == []
    assert found is True
    assert invalid is True


def test_extract_favorites_payload_missing():
    payload = {"status": "ok"}
    favorites, found, invalid = _extract_favorites_payload(payload)
    assert favorites == []
    assert found is False
    assert invalid is False


def test_list_favorites_uses_accounts_me_endpoint(monkeypatch):
    captured = {}
    client = KptnCookClient(base_url="https://mobile.kptncook.com", api_key="test-key")

    def fake_get(path, **kwargs):
        url = client.to_url(path)
        captured["url"] = url
        captured["params"] = kwargs.get("params", {})
        request = httpx.Request("GET", url)
        return httpx.Response(
            200,
            request=request,
            content=json.dumps({"favorites": []}).encode("utf-8"),
            headers={"content-type": "application/json"},
        )

    monkeypatch.setattr(client, "get", fake_get)

    favorites = client.list_favorites()

    assert favorites == []
    assert captured["url"] == "https://mobile.kptncook.com/accounts/me/favorites"
    assert captured["params"]["kptnkey"] == "test-key"
