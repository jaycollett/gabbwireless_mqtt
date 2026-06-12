"""Minimal client for the Gabb Wireless (Smartcom FiLIP) API.

Trimmed from the original ``gabb`` library by Benjamin Woods
(https://github.com/woodsbw/gabb), Apache License 2.0 -- see LICENSE.txt in
this directory. Only what gabb_mqtt_publisher actually uses is kept: the
username/password login, token refresh with expiry checking, and the ``map``
endpoint that returns device geolocation/status data.
"""

import datetime

import requests
from dateutil import parser as date_parser

__all__ = ["GabbClient"]
__version__ = "0.2.0"

BASE_URL = "https://api.myfilip.com/v2/"
AUTH_URL = "https://api.myfilip.com/v2/sso/gabb"
REFRESH_URL = "https://api.myfilip.com/v2/token/refresh"

# Build version of the Gabb app we emulate. The API rejects requests without
# a plausible value; best left alone unless you know why you're changing it.
APP_BUILD = "1.28 (966)"

# Applied to every HTTP request (auth, refresh, and API calls) so a hung
# server can't stall the publisher's poll loop indefinitely.
REQUEST_TIMEOUT = 15

# Static headers the FiLIP API requires in order to accept requests.
REQUIRED_HEADERS = {
    "X-Accept-Language": "en-US",
    "X-Accept-Offset": "-5.000000",
    "Accept-Version": "1.0",
    "User-Agent": "FiLIP-iOS",
    "X-Accept-Version": "1.0",
    "Content-Type": "application/json",
}


class GabbAuth(requests.auth.AuthBase):
    """requests auth handler for the Gabb API.

    Logs in with username/password on construction, refreshes the access
    token when it expires, and injects the Bearer token into every request.
    """

    def __init__(self, username: str, password: str) -> None:
        self.username = username
        self.password = password
        self._access_token: str = ""
        self._refresh_token: str = ""
        self._exp_date: datetime.datetime | None = None
        self._login()

    def __call__(self, request: requests.PreparedRequest) -> requests.PreparedRequest:
        if self._token_expired:
            self._refresh()
        request.headers["Authorization"] = f"Bearer {self._access_token}"
        return request

    def _login(self) -> None:
        """Fresh authentication with the account username/password."""
        resp = requests.post(
            AUTH_URL,
            headers=REQUIRED_HEADERS,
            json={
                "appBuild": APP_BUILD,
                "username": self.username,
                "password": self.password,
            },
            timeout=REQUEST_TIMEOUT,
        )
        self._update_tokens_from_response(resp)

    def _refresh(self) -> None:
        """Exchange the refresh token for a new access token."""
        resp = requests.post(
            REFRESH_URL,
            headers=REQUIRED_HEADERS,
            json={"refreshToken": self._refresh_token},
            timeout=REQUEST_TIMEOUT,
        )
        self._update_tokens_from_response(resp)

    def _update_tokens_from_response(self, response: requests.Response) -> None:
        """Parse tokens/expiry out of an auth or refresh response.

        raise_for_status() first so bad credentials surface as an HTTPError
        (which the publisher recognizes via its 401/403 auth-failure check)
        rather than a KeyError from indexing an error body.
        """
        response.raise_for_status()
        data = response.json()["data"]
        self._access_token = data["accessToken"]
        self._refresh_token = data["refreshToken"]
        self._exp_date = date_parser.parse(data["expDate"])

    @property
    def _token_expired(self) -> bool:
        if self._exp_date is None:
            return True
        return self._exp_date < datetime.datetime.now(datetime.timezone.utc)


class GabbClient:
    """Minimal Gabb REST API client: authenticate and fetch map data."""

    def __init__(self, username: str, password: str) -> None:
        self._session = requests.Session()
        self._session.headers.update(REQUIRED_HEADERS)
        self._session.auth = GabbAuth(username, password)

    def get_map(self) -> requests.Response:
        """Get device geolocation data plus general device info.

        Returns the raw requests.Response; the caller is responsible for
        raise_for_status() and JSON parsing.
        """
        return self._session.get(BASE_URL + "map", timeout=REQUEST_TIMEOUT)
