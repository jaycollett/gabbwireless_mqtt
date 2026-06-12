"""Async API client for the Gabb Wireless (Smartcom FiLIP) cloud service.

This is a self-contained aiohttp client with no dependencies outside of
Home Assistant's bundled libraries. It mirrors the protocol implemented by
the ``gabb`` package shipped alongside this repository's MQTT publisher.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

AUTH_URL = "https://api.myfilip.com/v2/sso/gabb"
REFRESH_URL = "https://api.myfilip.com/v2/token/refresh"
MAP_URL = "https://api.myfilip.com/v2/map"

APP_BUILD = "1.28 (966)"

REQUIRED_HEADERS = {
    "X-Accept-Language": "en-US",
    "X-Accept-Offset": "-5.000000",
    "Accept-Version": "1.0",
    "User-Agent": "FiLIP-iOS",
    "X-Accept-Version": "1.0",
    "Content-Type": "application/json",
}

REQUEST_TIMEOUT = 15
TOKEN_EXPIRY_MARGIN = datetime.timedelta(seconds=60)
FALLBACK_TOKEN_LIFETIME = datetime.timedelta(minutes=10)


class GabbApiError(Exception):
    """Base exception for Gabb API errors."""


class GabbAuthError(GabbApiError):
    """Raised when authentication fails (bad credentials or rejected token)."""


class GabbConnectionError(GabbApiError):
    """Raised when the Gabb API cannot be reached or returns garbage."""


def _parse_exp_date(value: Any) -> datetime.datetime:
    """Parse the token expiry from the API into an aware UTC datetime."""
    now = datetime.datetime.now(datetime.timezone.utc)
    if value is None:
        return now + FALLBACK_TOKEN_LIFETIME
    if isinstance(value, (int, float)):
        # Heuristic: epoch milliseconds vs seconds.
        timestamp = value / 1000 if value > 1e11 else value
        try:
            return datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return now + FALLBACK_TOKEN_LIFETIME
    try:
        parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        _LOGGER.debug("Could not parse token expiry %r; using fallback lifetime", value)
        return now + FALLBACK_TOKEN_LIFETIME
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


class GabbApiClient:
    """Minimal async client for the Gabb / FiLIP API."""

    def __init__(
        self,
        username: str,
        password: str,
        session: aiohttp.ClientSession,
    ) -> None:
        """Initialize the client. No I/O happens here."""
        self._username = username
        self._password = password
        self._session = session
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._exp_date: datetime.datetime | None = None
        self._token_lock = asyncio.Lock()

    @property
    def _token_expired(self) -> bool:
        if self._access_token is None or self._exp_date is None:
            return True
        now = datetime.datetime.now(datetime.timezone.utc)
        return self._exp_date - TOKEN_EXPIRY_MARGIN <= now

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        authorized: bool = False,
    ) -> dict[str, Any]:
        """Perform a request and return the decoded JSON body."""
        headers = dict(REQUIRED_HEADERS)
        if authorized:
            headers["Authorization"] = f"Bearer {self._access_token}"
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                response = await self._session.request(
                    method, url, json=json, headers=headers
                )
                if response.status in (401, 403):
                    raise GabbAuthError(
                        f"Gabb API rejected the request ({response.status})"
                    )
                if response.status >= 400:
                    raise GabbConnectionError(
                        f"Gabb API returned HTTP {response.status} for {url}"
                    )
                try:
                    body: dict[str, Any] = await response.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError) as err:
                    raise GabbConnectionError(
                        "Gabb API returned a non-JSON response"
                    ) from err
        except TimeoutError as err:
            raise GabbConnectionError(f"Timeout talking to {url}") from err
        except aiohttp.ClientError as err:
            raise GabbConnectionError(f"Error talking to {url}: {err}") from err
        if not isinstance(body, dict):
            raise GabbConnectionError("Gabb API returned an unexpected payload")
        return body

    def _store_tokens(self, body: dict[str, Any]) -> None:
        data = body.get("data")
        if not isinstance(data, dict) or not data.get("accessToken"):
            raise GabbAuthError("Gabb API response did not contain an access token")
        self._access_token = data["accessToken"]
        self._refresh_token = data.get("refreshToken")
        self._exp_date = _parse_exp_date(data.get("expDate"))

    async def async_login(self) -> None:
        """Authenticate with username/password and store tokens."""
        body = await self._request_json(
            "POST",
            AUTH_URL,
            json={
                "appBuild": APP_BUILD,
                "username": self._username,
                "password": self._password,
            },
        )
        self._store_tokens(body)

    async def _async_refresh(self) -> None:
        """Refresh the access token, falling back to a full login."""
        if self._refresh_token:
            try:
                body = await self._request_json(
                    "POST",
                    REFRESH_URL,
                    json={"refreshToken": self._refresh_token},
                )
                self._store_tokens(body)
            except GabbAuthError:
                _LOGGER.debug("Token refresh rejected; retrying with full login")
            else:
                return
        await self.async_login()

    async def _async_ensure_token(self) -> None:
        async with self._token_lock:
            if self._token_expired:
                await self._async_refresh()

    async def async_get_devices(self) -> dict[str, dict[str, Any]]:
        """Fetch device map data, returned as a dict keyed by str(device id)."""
        await self._async_ensure_token()
        try:
            body = await self._request_json("GET", MAP_URL, authorized=True)
        except GabbAuthError:
            # Token may have been revoked server-side; re-login once and retry.
            async with self._token_lock:
                await self.async_login()
            body = await self._request_json("GET", MAP_URL, authorized=True)

        data = body.get("data")
        devices_raw = data.get("Devices") if isinstance(data, dict) else None
        if not isinstance(devices_raw, list):
            raise GabbConnectionError("Gabb map response did not contain device data")

        devices: dict[str, dict[str, Any]] = {}
        for device in devices_raw:
            if not isinstance(device, dict) or device.get("id") is None:
                continue
            devices[str(device["id"])] = device
        return devices
