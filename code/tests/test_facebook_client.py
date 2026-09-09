import io
import json
import logging
import sys
import unittest
from pathlib import Path
from urllib.error import HTTPError, URLError
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from auto_tiktok_editor.facebook.client import FacebookGraphClient
from auto_tiktok_editor.facebook.exceptions import (
    FacebookInvalidTokenError,
    FacebookNetworkError,
    FacebookPermissionError,
    FacebookRateLimitError,
)


class FakeResponse:
    def __init__(self, payload):
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self.body


class FacebookGraphClientTests(unittest.TestCase):
    def make_client(self, *responses):
        opener = mock.Mock(side_effect=list(responses))
        client = FacebookGraphClient(
            "https://graph.facebook.com/v26.0",
            timeout=9,
            opener=opener,
        )
        return client, opener

    def test_parses_me_accounts_response(self):
        client, opener = self.make_client(
            FakeResponse(
                {
                    "data": [
                        {
                            "id": "123",
                            "name": "Tép Ăn Ngon",
                            "access_token": "page-token",
                            "tasks": ["CREATE_CONTENT", "MODERATE"],
                            "category": "Food & beverage",
                            "picture": {"data": {"url": "https://cdn.example/avatar.jpg"}},
                        }
                    ]
                }
            )
        )

        pages = client.get_managed_pages("user-token")

        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0].page_id, "123")
        self.assertEqual(pages[0].name, "Tép Ăn Ngon")
        self.assertEqual(pages[0].category, "Food & beverage")
        self.assertEqual(pages[0].access_token, "page-token")
        self.assertEqual(pages[0].tasks, ("CREATE_CONTENT", "MODERATE"))
        self.assertEqual(pages[0].picture_url, "https://cdn.example/avatar.jpg")
        request = opener.call_args.args[0]
        self.assertIn("/v26.0/me/accounts?", request.full_url)
        self.assertIn("fields=id%2Cname%2Caccess_token%2Ctasks%2Ccategory%2Cpicture", request.full_url)
        self.assertEqual(opener.call_args.kwargs["timeout"], 9)

    def test_parses_multiple_pages(self):
        client, _opener = self.make_client(
            FakeResponse(
                {
                    "data": [
                        {"id": "1", "name": "Page One"},
                        {"id": "2", "name": "Page Two"},
                        {"id": "3", "name": "Page Three"},
                    ]
                }
            )
        )

        pages = client.get_managed_pages("user-token")

        self.assertEqual([page.page_id for page in pages], ["1", "2", "3"])

    def test_follows_pagination_until_next_is_absent(self):
        next_url = "https://graph.facebook.com/v26.0/me/accounts?after=cursor"
        client, opener = self.make_client(
            FakeResponse(
                {
                    "data": [{"id": "1", "name": "First"}],
                    "paging": {"next": next_url},
                }
            ),
            FakeResponse({"data": [{"id": "2", "name": "Second"}]}),
        )

        pages = client.get_managed_pages("user-token")

        self.assertEqual([page.name for page in pages], ["First", "Second"])
        self.assertEqual(opener.call_count, 2)
        self.assertEqual(opener.call_args_list[1].args[0].full_url, next_url)

    def test_empty_page_list_is_valid(self):
        client, _opener = self.make_client(FakeResponse({"data": []}))

        self.assertEqual(client.get_managed_pages("user-token"), [])

    def test_invalid_token_error_from_http_response(self):
        body = io.BytesIO(
            json.dumps(
                {
                    "error": {
                        "message": "Invalid OAuth access token.",
                        "type": "OAuthException",
                        "code": 190,
                    }
                }
            ).encode("utf-8")
        )
        http_error = HTTPError(
            "https://graph.facebook.com/v26.0/me/accounts",
            400,
            "Bad Request",
            {},
            body,
        )
        client, _opener = self.make_client(http_error)

        with self.assertRaisesRegex(
            FacebookInvalidTokenError,
            "Access Token không hợp lệ hoặc đã hết hạn",
        ):
            client.get_managed_pages("bad-user-token")

    def test_permission_error_is_mapped(self):
        client, _opener = self.make_client(
            FakeResponse(
                {
                    "error": {
                        "message": "Permissions error",
                        "type": "OAuthException",
                        "code": 200,
                    }
                }
            )
        )

        with self.assertRaises(FacebookPermissionError):
            client.get_managed_pages("user-token")

    def test_network_error_is_mapped(self):
        client, _opener = self.make_client(URLError("connection reset"))

        with self.assertRaisesRegex(
            FacebookNetworkError,
            "Không thể kết nối Meta Graph API",
        ):
            client.get_managed_pages("user-token")

    def test_rate_limit_error_is_mapped(self):
        client, _opener = self.make_client(
            FakeResponse({"error": {"message": "Too many calls", "code": 4}})
        )

        with self.assertRaises(FacebookRateLimitError):
            client.get_managed_pages("user-token")

    def test_token_never_appears_in_exception_or_log(self):
        token = "EAABabc123456789"
        client, _opener = self.make_client(URLError(f"failed for {token}"))
        logger = logging.getLogger("auto_tiktok_editor.facebook")
        records = []

        class CaptureHandler(logging.Handler):
            def emit(self, record):
                records.append(self.format(record))

        handler = CaptureHandler()
        logger.addHandler(handler)
        try:
            with self.assertRaises(FacebookNetworkError) as caught:
                client.get_managed_pages(token)
        finally:
            logger.removeHandler(handler)

        self.assertNotIn(token, str(caught.exception))
        self.assertNotIn(token, "\n".join(records))


if __name__ == "__main__":
    unittest.main()
