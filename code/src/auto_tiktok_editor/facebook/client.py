"""Minimal official Meta Graph API client for managed Facebook Pages."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from auto_tiktok_editor.facebook.exceptions import (
    FacebookGraphError,
    FacebookInvalidTokenError,
    FacebookNetworkError,
    FacebookPermissionError,
    FacebookRateLimitError,
    FacebookResponseError,
)
from auto_tiktok_editor.facebook.models import FacebookPage

DEFAULT_GRAPH_BASE_URL = "https://graph.facebook.com/v26.0"
MANAGED_PAGE_FIELDS = "id,name,access_token,tasks,category,picture"
_INVALID_TOKEN_CODES = {190}
_PERMISSION_CODES = {10, 200}
_RATE_LIMIT_CODES = {4, 17, 32, 613, 80004}


class FacebookGraphClient:
    """Read Facebook Pages available to a user access token."""

    def __init__(
        self,
        base_url: str = DEFAULT_GRAPH_BASE_URL,
        *,
        timeout: float = 15.0,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self.base_url = str(base_url).strip().rstrip("/")
        if not self.base_url:
            raise ValueError("Facebook Graph API base URL must not be empty.")
        self.timeout = max(1.0, float(timeout))
        self._opener = opener or urlopen
        parsed_base = urlparse(self.base_url)
        if parsed_base.scheme not in {"http", "https"} or not parsed_base.netloc:
            raise ValueError("Facebook Graph API base URL is invalid.")
        self._base_origin = (parsed_base.scheme.lower(), parsed_base.netloc.lower())

    def get_managed_pages(self, user_access_token: str) -> list[FacebookPage]:
        """Return every Page from ``/me/accounts``, following ``paging.next``."""
        token = str(user_access_token or "").strip()
        if not token:
            raise FacebookInvalidTokenError()

        query = urlencode(
            {
                "fields": MANAGED_PAGE_FIELDS,
                "access_token": token,
            }
        )
        next_url: str | None = f"{self.base_url}/me/accounts?{query}"
        pages: list[FacebookPage] = []

        while next_url:
            if not self._is_allowed_page_url(next_url):
                raise FacebookResponseError()
            payload = self._get_json(next_url)
            self._raise_for_api_error(payload)

            raw_pages = payload.get("data")
            if not isinstance(raw_pages, list):
                raise FacebookResponseError()
            for item in raw_pages:
                if not isinstance(item, Mapping):
                    raise FacebookResponseError()
                page = FacebookPage.from_graph_data(item)
                if not page.page_id or not page.name:
                    raise FacebookResponseError()
                pages.append(page)

            paging = payload.get("paging")
            if paging is None:
                next_url = None
            elif isinstance(paging, Mapping):
                raw_next = paging.get("next")
                next_url = str(raw_next).strip() if raw_next else None
            else:
                raise FacebookResponseError()

        return pages

    def _is_allowed_page_url(self, url: str) -> bool:
        parsed = urlparse(url)
        return (parsed.scheme.lower(), parsed.netloc.lower()) == self._base_origin

    def _get_json(self, url: str) -> Mapping[str, Any]:
        request = Request(url, headers={"Accept": "application/json"}, method="GET")
        try:
            with self._opener(request, timeout=self.timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            try:
                raw = exc.read()
                payload = self._decode_payload(raw)
                self._raise_for_api_error(payload)
            except FacebookGraphError:
                raise
            except Exception:
                pass
            raise FacebookResponseError() from None
        except (URLError, TimeoutError, OSError):
            raise FacebookNetworkError() from None

        return self._decode_payload(raw)

    @staticmethod
    def _decode_payload(raw: bytes | str) -> Mapping[str, Any]:
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
            raise FacebookResponseError() from None
        if not isinstance(payload, Mapping):
            raise FacebookResponseError()
        return payload

    @staticmethod
    def _raise_for_api_error(payload: Mapping[str, Any]) -> None:
        error = payload.get("error")
        if error is None:
            return
        if not isinstance(error, Mapping):
            raise FacebookResponseError()

        raw_code = error.get("code")
        try:
            code = int(raw_code) if raw_code is not None else None
        except (TypeError, ValueError):
            code = None
        error_type = str(error.get("type") or "")

        if code in _INVALID_TOKEN_CODES:
            raise FacebookInvalidTokenError(code=code, error_type=error_type)
        if code in _PERMISSION_CODES:
            raise FacebookPermissionError(code=code, error_type=error_type)
        if code in _RATE_LIMIT_CODES:
            raise FacebookRateLimitError(code=code, error_type=error_type)
        raise FacebookGraphError(code=code, error_type=error_type)
