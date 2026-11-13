"""Share resolution only needs headers, never the untrusted response body."""

from types import SimpleNamespace

import httpx
import pytest

from kptncook.services import workflows


class UnneededBody(httpx.SyncByteStream):
    closed = False

    def __iter__(self):
        pytest.fail("Share response body must never be consumed")
        yield b""  # pragma: no cover

    def close(self):
        self.closed = True


@pytest.mark.parametrize("status", [200, 301, 302, 307])
def test_share_resolution_closes_body_without_reading(monkeypatch, status):
    body = UnneededBody()
    calls = []

    def respond(request):
        assert "token" not in request.headers
        assert "kptnkey" not in request.url.params
        return httpx.Response(
            status,
            headers={"Location": "https://mobile.kptncook.com/Abc1234"},
            stream=body,
        )

    recipe = SimpleNamespace(data={})
    client = SimpleNamespace(
        get_by_ids=lambda ids: calls.append(ids) or [recipe], close=lambda: None
    )
    monkeypatch.setattr(workflows, "KptnCookClient", lambda: client)
    monkeypatch.setattr(
        workflows, "_save_repository_entries", lambda rows: calls.append(rows)
    )
    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        monkeypatch.setattr(workflows.httpx, "get", http.get)
        monkeypatch.setattr(workflows.httpx, "stream", http.stream)
        if status in (301, 302):
            result = workflows.search_recipe_by_id("https://share.kptncook.com/Abc1234")
            assert result.id_value == "Abc1234"
            assert len(calls) == 2
        else:
            with pytest.raises(workflows.UserFacingError, match=f"HTTP {status}"):
                workflows.search_recipe_by_id("https://share.kptncook.com/Abc1234")
            assert not calls
    assert body.closed


def test_share_resolution_discards_headers_after_deadline(monkeypatch):
    clock = [0.0]
    body = UnneededBody()

    def respond(request):
        clock[0] = 16.0
        return httpx.Response(302, headers={"location": "/Abc1234"}, stream=body)

    monkeypatch.setattr(workflows, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        workflows, "KptnCookClient", lambda: pytest.fail("No recipe request")
    )
    monkeypatch.setattr(
        workflows, "_save_repository_entries", lambda rows: pytest.fail("No save")
    )
    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        monkeypatch.setattr(workflows.httpx, "get", http.get)
        monkeypatch.setattr(workflows.httpx, "stream", http.stream)
        with pytest.raises(workflows.UserFacingError, match="timeout"):
            workflows.search_recipe_by_id("https://share.kptncook.com/Abc1234")
    assert body.closed
