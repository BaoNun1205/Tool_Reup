import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from datetime import datetime, timedelta, timezone
import tempfile
import unittest
from unittest import mock

from auto_tiktok_editor.cli import _require_cached_license_session
from auto_tiktok_editor.commercial_entry import COMMERCIAL_LICENSE_SERVER_URL
from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.license.config import LicenseClientConfig
from auto_tiktok_editor.license.exceptions import LicenseAuthenticationRequired
from auto_tiktok_editor.license.guard import LicenseGuard
from auto_tiktok_editor.license.models import LicenseTokenBundle, VerifiedLicenseSession
from auto_tiktok_editor.license.storage import LicenseCacheStore
from auto_tiktok_editor.ui.app import launch_ui


def _sample_bundle(device_fingerprint: str = "fingerprint-1234567890") -> LicenseTokenBundle:
    now = datetime.now(timezone.utc)
    return LicenseTokenBundle(
        access_token="header.payload.signature",
        access_token_expires_at=now + timedelta(minutes=30),
        refresh_token="refresh-token-demo",
        refresh_token_expires_at=now + timedelta(hours=12),
        session_id="11111111-1111-1111-1111-111111111111",
        account_id="account-demo",
        username="demo_user",
        license_id="license-demo",
        license_code="ABCD1234",
        plan_name="standard",
        license_expires_at=now + timedelta(days=30),
        device_id="device-demo",
        device_fingerprint=device_fingerprint,
        public_key_b64="public-key",
        server_base_url="http://127.0.0.1:8787",
        cached_at=now,
        server_time=now,
        last_verified_at=now,
        offline_grace_expires_at=now + timedelta(hours=48),
    )


class LicenseCacheStoreTests(unittest.TestCase):
    def test_roundtrip_save_and_load(self):
        temp_dir = tempfile.TemporaryDirectory()
        try:
            cache_path = Path(temp_dir.name) / "license_cache.json"
            store = LicenseCacheStore(cache_path)
            bundle = _sample_bundle()
            store.save(bundle)

            loaded = store.load()

            self.assertEqual(loaded.username, bundle.username)
            self.assertEqual(loaded.device_fingerprint, bundle.device_fingerprint)
            self.assertEqual(loaded.license_code, bundle.license_code)
        finally:
            temp_dir.cleanup()


class LicenseGuardTests(unittest.TestCase):
    def test_ensure_valid_session_rejects_cache_for_other_device(self):
        temp_dir = tempfile.TemporaryDirectory()
        try:
            cache_path = Path(temp_dir.name) / "license_cache.json"
            config = LicenseClientConfig(server_base_url="http://127.0.0.1:8787", cache_path=cache_path)
            guard = LicenseGuard(config=config)
            guard.store.save(_sample_bundle(device_fingerprint="bound-device"))

            with mock.patch("auto_tiktok_editor.license.guard.build_device_fingerprint", return_value="other-device"):
                with self.assertRaises(LicenseAuthenticationRequired):
                    guard.ensure_valid_session()

            self.assertIsNone(guard.store.load())
        finally:
            temp_dir.cleanup()

    def test_ensure_online_session_calls_server_validation(self):
        temp_dir = tempfile.TemporaryDirectory()
        try:
            cache_path = Path(temp_dir.name) / "license_cache.json"
            config = LicenseClientConfig(server_base_url="http://127.0.0.1:8787", cache_path=cache_path)
            guard = LicenseGuard(config=config)
            bundle = _sample_bundle(device_fingerprint="bound-device")
            guard.store.save(bundle)

            verified = VerifiedLicenseSession(
                account_id=bundle.account_id,
                username=bundle.username,
                license_id=bundle.license_id,
                session_id=bundle.session_id,
                device_id=bundle.device_id,
                plan_name=bundle.plan_name,
                license_expires_at=bundle.license_expires_at,
                access_token_expires_at=bundle.access_token_expires_at,
                raw_payload={"plan": bundle.plan_name},
            )

            with mock.patch("auto_tiktok_editor.license.guard.build_device_fingerprint", return_value="bound-device"):
                with mock.patch("auto_tiktok_editor.license.guard.verify_access_token", return_value=verified):
                    guard.client.me = mock.Mock(return_value={"status": "ok"})
                    guard.client.heartbeat = mock.Mock()

                    session = guard.ensure_online_session()

            self.assertEqual(session.session_id, bundle.session_id)
            guard.client.me.assert_called_once_with(access_token=bundle.access_token)
            guard.client.heartbeat.assert_called_once_with(access_token=bundle.access_token, session_id=bundle.session_id)
        finally:
            temp_dir.cleanup()


class CliLicenseTests(unittest.TestCase):
    def test_require_cached_license_session_returns_none_when_login_is_needed(self):
        guard = mock.Mock()
        guard.ensure_valid_session.side_effect = LicenseAuthenticationRequired("login required")

        result = _require_cached_license_session(guard, "run-session")

        self.assertIsNone(result)


class LaunchUiTests(unittest.TestCase):
    def test_launch_ui_skips_license_when_commercial_mode_is_disabled(self):
        fake_root = mock.Mock()
        config = PipelineConfig(commercial_mode=False, allow_local_telegram=True, telegram_bot_token="token-demo")

        with mock.patch("auto_tiktok_editor.ui.app.ensure_ui_license_session") as mocked_login:
            with mock.patch("auto_tiktok_editor.ui.app.tk.Tk", return_value=fake_root) as mocked_tk:
                with mock.patch("auto_tiktok_editor.ui.app.EditorApplication") as mocked_app:
                    exit_code = launch_ui(config=config)

        self.assertEqual(exit_code, 0)
        mocked_login.assert_not_called()
        mocked_tk.assert_called_once()
        mocked_app.assert_called_once_with(fake_root, config=config, license_guard=None, license_session=None)
        fake_root.mainloop.assert_called_once()

    def test_launch_ui_returns_non_zero_when_login_dialog_is_cancelled(self):
        with mock.patch.dict(
            "os.environ",
            {"AUTO_EDITOR_LICENSE_SERVER_URL": COMMERCIAL_LICENSE_SERVER_URL, "AUTO_EDITOR_COMMERCIAL_MODE": "1"},
            clear=False,
        ):
            with mock.patch("auto_tiktok_editor.ui.app.ensure_ui_license_session", return_value=None):
                with mock.patch("auto_tiktok_editor.ui.app.tk.Tk") as mocked_tk:
                    exit_code = launch_ui()

        self.assertEqual(exit_code, 1)
        mocked_tk.assert_not_called()

    def test_launch_ui_opens_main_window_after_successful_login(self):
        fake_session = VerifiedLicenseSession(
            account_id="account-demo",
            username="demo_user",
            license_id="license-demo",
            session_id="session-demo",
            device_id="device-demo",
            plan_name="standard",
            license_expires_at=datetime.now(timezone.utc) + timedelta(days=30),
            access_token_expires_at=datetime.now(timezone.utc) + timedelta(minutes=20),
            raw_payload={"plan": "standard"},
        )
        fake_root = mock.Mock()

        with mock.patch.dict(
            "os.environ",
            {"AUTO_EDITOR_LICENSE_SERVER_URL": COMMERCIAL_LICENSE_SERVER_URL, "AUTO_EDITOR_COMMERCIAL_MODE": "1"},
            clear=False,
        ):
            with mock.patch("auto_tiktok_editor.ui.app.ensure_ui_license_session", return_value=fake_session):
                with mock.patch("auto_tiktok_editor.ui.app.tk.Tk", return_value=fake_root) as mocked_tk:
                    with mock.patch("auto_tiktok_editor.ui.app.EditorApplication") as mocked_app:
                        exit_code = launch_ui()

        self.assertEqual(exit_code, 0)
        mocked_tk.assert_called_once()
        mocked_app.assert_called_once()
        fake_root.mainloop.assert_called_once()

    def test_launch_ui_reopens_login_when_reauthentication_is_requested(self):
        session_one = VerifiedLicenseSession(
            account_id="account-demo",
            username="demo_user",
            license_id="license-demo",
            session_id="session-one",
            device_id="device-demo",
            plan_name="standard",
            license_expires_at=datetime.now(timezone.utc) + timedelta(days=30),
            access_token_expires_at=datetime.now(timezone.utc) + timedelta(minutes=20),
            raw_payload={"plan": "standard"},
        )
        session_two = VerifiedLicenseSession(
            account_id="account-demo",
            username="demo_user",
            license_id="license-demo",
            session_id="session-two",
            device_id="device-demo",
            plan_name="standard",
            license_expires_at=datetime.now(timezone.utc) + timedelta(days=30),
            access_token_expires_at=datetime.now(timezone.utc) + timedelta(minutes=20),
            raw_payload={"plan": "standard"},
        )
        fake_root_one = mock.Mock()
        fake_root_two = mock.Mock()
        fake_app_one = mock.Mock()
        fake_app_one.reauthenticate_requested = True
        fake_app_two = mock.Mock()
        fake_app_two.reauthenticate_requested = False

        with mock.patch.dict(
            "os.environ",
            {"AUTO_EDITOR_LICENSE_SERVER_URL": COMMERCIAL_LICENSE_SERVER_URL, "AUTO_EDITOR_COMMERCIAL_MODE": "1"},
            clear=False,
        ):
            with mock.patch(
                "auto_tiktok_editor.ui.app.ensure_ui_license_session",
                side_effect=[session_one, session_two],
            ):
                with mock.patch(
                    "auto_tiktok_editor.ui.app.tk.Tk",
                    side_effect=[fake_root_one, fake_root_two],
                ) as mocked_tk:
                    with mock.patch(
                        "auto_tiktok_editor.ui.app.EditorApplication",
                        side_effect=[fake_app_one, fake_app_two],
                    ) as mocked_app:
                        exit_code = launch_ui()

        self.assertEqual(exit_code, 0)
        self.assertEqual(mocked_tk.call_count, 2)
        self.assertEqual(mocked_app.call_count, 2)
        fake_root_one.mainloop.assert_called_once()
        fake_root_two.mainloop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
