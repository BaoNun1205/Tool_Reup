import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import json
from types import SimpleNamespace
import tempfile
import unittest

from auto_tiktok_editor.app.telegram_multi_bot import _config_for_bot, load_telegram_bot_specs
from auto_tiktok_editor.config import PipelineConfig

TEST_TEMP_ROOT = ROOT / "_tmp_tests"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)


def _temporary_directory():
    return tempfile.TemporaryDirectory(dir=str(TEST_TEMP_ROOT))


class TelegramMultiBotTests(unittest.TestCase):
    def test_load_telegram_bot_specs_reads_token_chat_pairs(self):
        temp_dir = _temporary_directory()
        try:
            config_path = Path(temp_dir.name) / "telegram_bots.json"
            config_path.write_text(
                json.dumps(
                    {
                        "bots": [
                            {"name": "channel_1", "bot_token": "token-a", "chat_id": "111"},
                            {"name": "channel_2", "bot_token": "token-b", "chat_id": 222},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            specs = load_telegram_bot_specs(config_path)

            self.assertEqual([spec.bot_token for spec in specs], ["token-a", "token-b"])
            self.assertEqual([spec.chat_id for spec in specs], [111, 222])
        finally:
            temp_dir.cleanup()

    def test_config_for_bot_uses_dedicated_input_root_and_allowlist(self):
        base = PipelineConfig(
            telegram_input_root=Path("d:/telegram_inputs"),
            default_output_root=Path("d:/output"),
        )
        spec = load_telegram_bot_specs_from_payload(
            {"bots": [{"name": "channel 1", "bot_token": "token-a", "chat_id": "111"}]}
        )[0]

        config = _config_for_bot(base, spec, 1)

        self.assertEqual(config.telegram_bot_token, "token-a")
        self.assertEqual(config.telegram_allowed_chat_ids, (111,))
        self.assertEqual(config.telegram_input_root, Path("d:/telegram_inputs/channel_1"))
        self.assertEqual(config.tiktok_profile_slug, "channel_1")

    def test_config_for_bot_uses_profile_cut_mode_for_bot_name(self):
        base = PipelineConfig(
            telegram_input_root=Path("d:/telegram_inputs"),
            default_output_root=Path("d:/output"),
            video_cut_mode="original",
        )
        spec = load_telegram_bot_specs_from_payload(
            {"bots": [{"name": "tep_riu", "bot_token": "token-a", "chat_id": "111"}]}
        )[0]
        manager = FakeProfileManager({"tep_riu": SimpleNamespace(cut_mode="scene")})

        config = _config_for_bot(base, spec, 1, profile_manager=manager)

        self.assertEqual(manager.calls, ["tep_riu"])
        self.assertEqual(config.tiktok_profile_slug, "tep_riu")
        self.assertEqual(config.video_cut_mode, "scene")

    def test_load_telegram_bot_specs_merges_duplicate_tokens(self):
        specs = load_telegram_bot_specs_from_payload(
            {
                "bots": [
                    {"name": "channel_1", "bot_token": "token-a", "chat_id": "111"},
                    {"name": "channel_1_extra", "bot_token": "token-a", "chat_id": "222"},
                ]
            }
        )

        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0].bot_token, "token-a")
        self.assertEqual(specs[0].chat_ids, (111, 222))


class FakeProfileManager:
    def __init__(self, accounts):
        self.accounts = accounts
        self.calls = []

    def find_account_for_profile_slug(self, profile_slug):
        self.calls.append(profile_slug)
        return self.accounts.get(profile_slug)


def load_telegram_bot_specs_from_payload(payload):
    temp_dir = _temporary_directory()
    path = Path(temp_dir.name) / "telegram_bots.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    try:
        return load_telegram_bot_specs(path)
    finally:
        temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
