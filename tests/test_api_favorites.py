from kptncook.api import _extract_favorites_payload


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
