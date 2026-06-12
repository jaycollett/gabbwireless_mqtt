"""Unit tests for the trimmed vendored gabb client (auth + map fetch)."""

import datetime
import json

import pytest
import requests

import gabb


def _fake_response(status_code: int = 200, body: dict | None = None) -> requests.Response:
    resp = requests.Response()
    resp.status_code = status_code
    resp._content = json.dumps(body if body is not None else {}).encode()
    return resp


def _token_body(exp_minutes: int = 60, access: str = "access-1") -> dict:
    exp = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=exp_minutes)
    return {
        "data": {
            "accessToken": access,
            "refreshToken": "refresh-1",
            "expDate": exp.isoformat(),
        }
    }


def test_login_failure_raises_http_error(monkeypatch):
    """Bad credentials surface as HTTPError (401), not KeyError."""
    monkeypatch.setattr(
        gabb.requests,
        "post",
        lambda *a, **kw: _fake_response(401, {"error": "bad credentials"}),
    )
    with pytest.raises(requests.exceptions.HTTPError) as excinfo:
        gabb.GabbAuth("user", "wrong-password")
    assert excinfo.value.response.status_code == 401


def test_login_success_injects_bearer_token(monkeypatch):
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "json": json, "timeout": timeout, "headers": headers})
        return _fake_response(200, _token_body())

    monkeypatch.setattr(gabb.requests, "post", fake_post)

    auth = gabb.GabbAuth("user", "pass")

    # Login hit the right endpoint with appBuild and a timeout.
    assert calls[0]["url"] == gabb.AUTH_URL
    assert calls[0]["json"]["appBuild"] == gabb.APP_BUILD
    assert calls[0]["json"]["username"] == "user"
    assert calls[0]["timeout"] == gabb.REQUEST_TIMEOUT
    assert calls[0]["headers"]["User-Agent"] == "FiLIP-iOS"

    # Token is injected into outgoing requests.
    request = requests.Request("GET", "https://example.invalid").prepare()
    auth(request)
    assert request.headers["Authorization"] == "Bearer access-1"


def test_expired_token_triggers_refresh(monkeypatch):
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(url)
        if url == gabb.AUTH_URL:
            # Already-expired token forces a refresh on first use.
            return _fake_response(200, _token_body(exp_minutes=-5, access="stale"))
        return _fake_response(200, _token_body(exp_minutes=60, access="fresh"))

    monkeypatch.setattr(gabb.requests, "post", fake_post)

    auth = gabb.GabbAuth("user", "pass")
    request = requests.Request("GET", "https://example.invalid").prepare()
    auth(request)

    assert calls == [gabb.AUTH_URL, gabb.REFRESH_URL]
    assert request.headers["Authorization"] == "Bearer fresh"


def test_refresh_failure_raises_http_error(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        if url == gabb.AUTH_URL:
            return _fake_response(200, _token_body(exp_minutes=-5))
        return _fake_response(403, {"error": "refresh token revoked"})

    monkeypatch.setattr(gabb.requests, "post", fake_post)

    auth = gabb.GabbAuth("user", "pass")
    request = requests.Request("GET", "https://example.invalid").prepare()
    with pytest.raises(requests.exceptions.HTTPError) as excinfo:
        auth(request)
    assert excinfo.value.response.status_code == 403


def test_get_map_uses_v2_endpoint_and_timeout(monkeypatch):
    monkeypatch.setattr(
        gabb.requests, "post", lambda *a, **kw: _fake_response(200, _token_body())
    )
    client = gabb.GabbClient("user", "pass")

    captured = {}

    def fake_get(url, timeout=None, **kwargs):
        captured["url"] = url
        captured["timeout"] = timeout
        return _fake_response(200, {"data": {"Devices": []}})

    monkeypatch.setattr(client._session, "get", fake_get)

    resp = client.get_map()
    assert captured["url"] == "https://api.myfilip.com/v2/map"
    assert captured["timeout"] == gabb.REQUEST_TIMEOUT
    assert resp.json() == {"data": {"Devices": []}}


def test_public_import_surface():
    from gabb import GabbClient  # noqa: F401

    assert gabb.__all__ == ["GabbClient"]
