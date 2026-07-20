import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import tempfile
import unittest
from unittest import mock

from auto_tiktok_editor.tiktok_profiles import profile_browser
from auto_tiktok_editor.tiktok_profiles.profile_manager import TikTokProfileManager, slugify, split_caption_and_hashtags
from auto_tiktok_editor.tiktok_profiles.ui import (
    _compose_video_caption_with_hashtags,
    _default_hashtag_for_account_name,
    _extract_product_url_from_note,
    _format_vietnam_datetime,
    _hashtag_tokens_for_ui,
    _telegram_bot_config_for_account,
    _telegram_product_messages_for_video,
)
from auto_tiktok_editor.tiktok_profiles.telegram_queue import enqueue_telegram_video, enqueue_telegram_video_draft
from auto_tiktok_editor.config import PipelineConfig

TEST_TEMP_ROOT = ROOT / "_tmp_tests"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)


def _temporary_directory():
    return tempfile.TemporaryDirectory(dir=str(TEST_TEMP_ROOT))


class TikTokProfileManagerTests(unittest.TestCase):
    def test_hashtag_append_only_prefixes_first_hashtag_with_space(self):
        events = []

        class FakeKeyboard(object):
            def insert_text(self, text):
                events.append(("text", text))

            def press(self, key):
                events.append(("press", key))

        class FakePage(object):
            def __init__(self):
                self.keyboard = FakeKeyboard()

            def wait_for_timeout(self, _milliseconds):
                events.append(("wait", _milliseconds))

        class FakeLocator(object):
            def click(self, timeout=None):
                events.append(("click", timeout))

            def evaluate(self, script):
                if "selectionStart" in script or "selectNodeContents" in script:
                    events.append(("caret_end", None))
                    return True
                events.append(("space_check", None))
                return False

        locator = FakeLocator()
        with mock.patch.object(profile_browser, "_find_caption_input", return_value=locator), mock.patch.object(
            profile_browser, "_select_first_hashtag_suggestion", return_value=True
        ):
            profile_browser._append_hashtags_with_suggestions(FakePage(), ["#demo", "#next"], prefix_space=True)

        self.assertLess(events.index(("caret_end", None)), events.index(("text", " ")))
        self.assertLess(events.index(("text", " ")), events.index(("text", "#demo")))
        self.assertEqual([event for event in events if event == ("text", " ")], [("text", " ")])
        self.assertLess(events.index(("text", "#demo")), events.index(("text", "#next")))

    def test_hashtag_append_does_not_prefix_space_without_caption(self):
        events = []

        class FakeKeyboard(object):
            def insert_text(self, text):
                events.append(("text", text))

            def press(self, key):
                events.append(("press", key))

        class FakePage(object):
            def __init__(self):
                self.keyboard = FakeKeyboard()

            def wait_for_timeout(self, _milliseconds):
                events.append(("wait", _milliseconds))

        class FakeLocator(object):
            def click(self, timeout=None):
                events.append(("click", timeout))

            def evaluate(self, script):
                if "selectionStart" in script or "selectNodeContents" in script:
                    events.append(("caret_end", None))
                    return True
                events.append(("space_check", None))
                return False

        locator = FakeLocator()
        with mock.patch.object(profile_browser, "_find_caption_input", return_value=locator), mock.patch.object(
            profile_browser, "_select_first_hashtag_suggestion", return_value=True
        ):
            profile_browser._append_hashtags_with_suggestions(FakePage(), ["#demo", "#next"], prefix_space=False)

        self.assertNotIn(("text", " "), events)
        self.assertEqual([event for event in events if event[0] == "text"], [("text", "#demo"), ("text", "#next")])

    def test_product_modal_add_does_not_rewrite_name_when_first_add_is_valid(self):
        page = mock.Mock()
        with mock.patch.object(profile_browser, "_wait_until") as wait_until, mock.patch.object(
            profile_browser, "_click_product_confirmation_add_button", return_value=True
        ) as click_add, mock.patch.object(
            profile_browser, "_wait_for_product_confirmation_invalid_name_error", return_value=False
        ), mock.patch.object(profile_browser, "_set_product_confirmation_name") as set_name:
            profile_browser._click_product_modal_add(page, "1730667245645826792")

        wait_until.assert_called_once()
        click_add.assert_called_once()
        set_name.assert_not_called()

    def test_product_modal_add_rewrites_name_only_after_invalid_character_error(self):
        page = mock.Mock()
        with mock.patch.object(profile_browser, "_wait_until"), mock.patch.object(
            profile_browser, "_click_product_confirmation_add_button", return_value=True
        ) as click_add, mock.patch.object(
            profile_browser, "_wait_for_product_confirmation_invalid_name_error", return_value=True
        ), mock.patch.object(profile_browser, "_set_product_confirmation_name") as set_name:
            profile_browser._click_product_modal_add(page, "1730667245645826792")

        self.assertEqual(click_add.call_count, 2)
        set_name.assert_called_once_with(page, "Mua ở đây")

    def test_extract_product_url_from_video_note(self):
        self.assertEqual(
            _extract_product_url_from_note("Telegram source: https://www.tiktok.com/@store/video/123\nProduct link: https://vt.tiktok.com/demo/"),
            "https://vt.tiktok.com/demo/",
        )

    def test_format_vietnam_datetime_for_ui_columns(self):
        self.assertEqual(_format_vietnam_datetime("2026-05-27T08:30:15+00:00"), "2026-05-27 15:30")
        self.assertEqual(_format_vietnam_datetime("2026-05-27 18:20:00", assume_utc=False), "2026-05-27 18:20")

    def test_video_caption_copy_text_combines_description_and_hashtag_tags(self):
        self.assertEqual(
            _compose_video_caption_with_hashtags("Mo ta mon an", "#linhanngon anvatcungtien #linhanngon"),
            "Mo ta mon an\n#linhanngon #anvatcungtien",
        )
        self.assertEqual(_compose_video_caption_with_hashtags("", "mymeanvat"), "#mymeanvat")

    def test_split_caption_separates_hashtags_attached_to_words(self):
        caption, hashtags = split_caption_and_hashtags(
            "Ăn trung thu sớmmmm#review#vir#tiktokshop#banhtrungthuxuantung"
        )

        self.assertEqual(caption, "Ăn trung thu sớmmmm")
        self.assertEqual(hashtags, "#review #vir #tiktokshop #banhtrungthuxuantung")

    def test_telegram_product_messages_are_caption_hashtags_then_product_id(self):
        video = mock.Mock(
            id=12,
            caption="Mo ta mon an",
            hashtags="#linhanngon anvatcungtien",
            product_id="1730667245645826792",
        )

        self.assertEqual(
            _telegram_product_messages_for_video(video),
            ("Mo ta mon an\n#linhanngon #anvatcungtien", "1730667245645826792"),
        )

    def test_telegram_product_messages_allow_missing_product_id(self):
        video = mock.Mock(id=12, caption="Mo ta mon an", hashtags="#linhanngon", product_id="")

        self.assertEqual(
            _telegram_product_messages_for_video(video),
            ("Mo ta mon an\n#linhanngon", ""),
        )

    def test_telegram_product_messages_reject_invalid_product_id_text(self):
        video = mock.Mock(
            id=12,
            caption="Mo ta mon an",
            hashtags="#linhanngon",
            product_id="Mo ta mon an\n#linhanngon",
        )

        with self.assertRaises(ValueError):
            _telegram_product_messages_for_video(video)

    def test_youtube_tag_input_helpers_normalize_and_map_account_tags(self):
        self.assertEqual(
            _hashtag_tokens_for_ui("linhanngon, #anvatcungtien #LINHANNGON"),
            ["#linhanngon", "#anvatcungtien"],
        )
        self.assertEqual(_default_hashtag_for_account_name("Linh An Ngon"), "#linhanngon")
        self.assertEqual(_default_hashtag_for_account_name("an_vat_cung_tien"), "#anvatcungtien")
        self.assertEqual(_default_hashtag_for_account_name("My Me An Vat"), "#mymeanvat")

    def test_telegram_bot_config_matches_video_profile(self):
        with _temporary_directory() as temp_dir:
            root = Path(temp_dir)
            manager = TikTokProfileManager(
                db_path=root / "accounts.sqlite3",
                profiles_root=root / "profiles",
                project_root=root,
            )
            account = manager.add_account("Linh An Ngon", "google")
            payload = {
                "bots": [
                    {"name": "other_profile", "bot_token": "token-x", "chat_id": 111},
                    {"name": "linh_an_ngon_bot", "bot_token": "token-a", "chat_id": "6547959450"},
                ]
            }

            bot_token, chat_id = _telegram_bot_config_for_account(payload, account)

            self.assertEqual(bot_token, "token-a")
            self.assertEqual(chat_id, 6547959450)

    def test_slugify_keeps_profile_folder_stable(self):
        self.assertEqual(slugify("Nick 1"), "nick_1")
        self.assertEqual(slugify("  Shop Demo!!!  "), "shop_demo")
        self.assertEqual(slugify(""), "account")

    def test_add_account_creates_profile_folder_and_database_row(self):
        with _temporary_directory() as temp_dir:
            root = Path(temp_dir)
            manager = TikTokProfileManager(
                db_path=root / "accounts.sqlite3",
                profiles_root=root / "profiles",
                project_root=root,
            )

            account = manager.add_account("Nick 1", "google", note="main")

            self.assertEqual(account.name, "Nick 1")
            self.assertEqual(account.login_type, "google")
            self.assertEqual(account.profile_path, "profiles/nick_1")
            self.assertEqual(account.status, "paused")
            self.assertEqual(account.note, "main")
            self.assertEqual(account.cut_mode, "original")
            self.assertEqual(account.hashtags, "")
            self.assertTrue((root / "profiles" / "nick_1").is_dir())

    def test_duplicate_names_get_unique_profile_paths(self):
        with _temporary_directory() as temp_dir:
            root = Path(temp_dir)
            manager = TikTokProfileManager(
                db_path=root / "accounts.sqlite3",
                profiles_root=root / "profiles",
                project_root=root,
            )

            first = manager.add_account("Nick 1", "google")
            second = manager.add_account("Nick 1", "facebook")

            self.assertEqual(first.profile_path, "profiles/nick_1")
            self.assertEqual(second.profile_path, "profiles/nick_1_2")
            self.assertTrue((root / "profiles" / "nick_1_2").is_dir())

    def test_update_status(self):
        with _temporary_directory() as temp_dir:
            root = Path(temp_dir)
            manager = TikTokProfileManager(
                db_path=root / "accounts.sqlite3",
                profiles_root=root / "profiles",
                project_root=root,
            )
            account = manager.add_account("Nick 1", "phone")

            updated = manager.update_status(account.id, "need_login", note="manual check")

            self.assertEqual(updated.status, "need_login")
            self.assertEqual(updated.note, "manual check")

    def test_account_cut_mode_is_default_for_new_videos(self):
        with _temporary_directory() as temp_dir:
            root = Path(temp_dir)
            video_file = root / "demo.mp4"
            video_file.write_bytes(b"fake video")
            product_image = root / "product.jpg"
            product_image.write_bytes(b"image")
            manager = TikTokProfileManager(
                db_path=root / "accounts.sqlite3",
                profiles_root=root / "profiles",
                project_root=root,
            )
            account = manager.add_account("Nick 1", "google", cut_mode="fixed")

            updated_account = manager.update_account_cut_mode(account.id, "scene")
            video = manager.add_video(video_file, account_id=account.id)
            draft = manager.add_video_draft(
                root / "profile_video_queue" / "nick_1" / "draft.pending",
                source_video_url="https://www.tiktok.com/@store/video/123",
                product_image_path=product_image,
                account_id=account.id,
            )

            self.assertEqual(updated_account.cut_mode, "scene")
            self.assertEqual(video.cut_mode, "scene")
            self.assertEqual(draft.cut_mode, "scene")

    def test_account_hashtags_are_editable_and_apply_to_new_videos(self):
        with _temporary_directory() as temp_dir:
            root = Path(temp_dir)
            video_file = root / "demo.mp4"
            video_file.write_bytes(b"fake video")
            product_image = root / "product.jpg"
            product_image.write_bytes(b"image")
            final_video = root / "final.mp4"
            final_video.write_bytes(b"rendered")
            manager = TikTokProfileManager(
                db_path=root / "accounts.sqlite3",
                profiles_root=root / "profiles",
                project_root=root,
            )
            account = manager.add_account("Nick 1", "google", hashtags="brand, #food")

            updated_account = manager.update_account_hashtags(account.id, "#brand #daily")
            video = manager.add_video(video_file, hashtags="#source #brand", account_id=account.id)
            draft = manager.add_video_draft(
                root / "profile_video_queue" / "nick_1" / "draft.pending",
                source_video_url="https://www.tiktok.com/@store/video/123",
                product_image_path=product_image,
                hashtags="#draft",
                account_id=account.id,
            )
            rendered = manager.mark_video_rendered(draft.id, final_video, source_title="Demo title #source")

            self.assertEqual(updated_account.hashtags, "#brand #daily")
            self.assertEqual(video.hashtags, "#source #brand #daily")
            self.assertEqual(draft.hashtags, "#draft #brand #daily")
            self.assertEqual(rendered.hashtags, "#draft #brand #daily #source")

    def test_source_channel_crud_per_account(self):
        with _temporary_directory() as temp_dir:
            root = Path(temp_dir)
            manager = TikTokProfileManager(
                db_path=root / "accounts.sqlite3",
                profiles_root=root / "profiles",
                project_root=root,
            )
            account = manager.add_account("Nick 1", "google")

            channel = manager.add_source_channel(account.id, "", "@demo_channel", note="good food")

            self.assertEqual(channel.account_id, account.id)
            self.assertEqual(channel.name, "@demo_channel")
            self.assertEqual(channel.url, "https://www.tiktok.com/@demo_channel")
            self.assertEqual(channel.note, "good food")
            self.assertFalse(channel.featured)
            self.assertTrue(channel.enabled)
            self.assertEqual(manager.list_source_channels(account.id), [channel])

            featured = manager.add_source_channel(account.id, "Top Channel", "@top_channel", featured=True)
            self.assertTrue(featured.featured)
            self.assertEqual([item.id for item in manager.list_source_channels(account.id)], [featured.id, channel.id])

            updated = manager.update_source_channel(
                channel.id,
                account.id,
                "Demo Channel",
                "tiktok.com/@demo_channel",
                note="updated",
                enabled=False,
            )

            self.assertEqual(updated.name, "Demo Channel")
            self.assertEqual(updated.url, "https://tiktok.com/@demo_channel")
            self.assertEqual(updated.note, "updated")
            self.assertFalse(updated.featured)
            self.assertFalse(updated.enabled)
            promoted = manager.set_source_channel_featured(updated.id, True)
            self.assertTrue(promoted.featured)
            self.assertTrue(manager.delete_source_channel(channel.id))
            self.assertTrue(manager.delete_source_channel(featured.id))
            self.assertEqual(manager.list_source_channels(account.id), [])

    def test_add_video_and_log_rows(self):
        with _temporary_directory() as temp_dir:
            root = Path(temp_dir)
            video_file = root / "demo.mp4"
            video_file.write_bytes(b"fake video")
            manager = TikTokProfileManager(
                db_path=root / "accounts.sqlite3",
                profiles_root=root / "profiles",
                project_root=root,
            )
            account = manager.add_account("Nick 1", "google")

            video = manager.add_video(video_file, caption="Caption", hashtags="demo, #food", note="draft")
            log = manager.add_log(
                "info",
                "queue_file_selected",
                "Selected file only.",
                account_id=account.id,
                video_id=video.id,
            )

            self.assertEqual(video.file_path, "demo.mp4")
            self.assertEqual(video.account_id, None)
            self.assertEqual(video.hashtags, "#demo #food")
            self.assertEqual(video.product_id, "")
            self.assertEqual(video.publish_mode, "now")
            self.assertEqual(video.scheduled_at, "")
            self.assertEqual(video.source, "manual")
            self.assertEqual(video.status, "ready")
            self.assertEqual(log.account_id, account.id)
            self.assertEqual(log.video_id, video.id)
            self.assertEqual(manager.resolve_video_path(video), video_file)

            self.assertEqual(manager.clear_logs(), 1)
            self.assertEqual(manager.list_logs(), [])

            scheduled = manager.update_video_details(
                video.id,
                caption="Updated caption",
                hashtags="newtag #food",
                product_id="1730667245645826792",
                publish_mode="scheduled",
                scheduled_at="2099-01-01 10:30",
                note="ready to post",
                account_id=account.id,
            )

            self.assertEqual(scheduled.account_id, account.id)
            self.assertEqual(scheduled.caption, "Updated caption")
            self.assertEqual(scheduled.hashtags, "#newtag #food")
            self.assertEqual(scheduled.product_id, "1730667245645826792")
            self.assertEqual(scheduled.publish_mode, "scheduled")
            self.assertEqual(scheduled.scheduled_at, "2099-01-01 10:30:00")
            self.assertEqual(scheduled.note, "ready to post")
            with manager._connect() as conn:
                video_columns = {row["name"] for row in conn.execute("PRAGMA table_info(videos)").fetchall()}
                table_names = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
            self.assertFalse(
                {
                    "visibility",
                    "high_quality_upload",
                    "allow_comments",
                    "allow_reuse",
                    "content_disclosure",
                    "ai_generated",
                    "copyright_check",
                    "quick_content_check",
                }.intersection(video_columns)
            )
            self.assertIn("hashtags", video_columns)
            self.assertNotIn("products", table_names)

    def test_delete_videos_removes_files_and_database_rows(self):
        with _temporary_directory() as temp_dir:
            root = Path(temp_dir)
            video_one_file = root / "one.mp4"
            video_two_file = root / "two.mp4"
            video_one_file.write_bytes(b"video one")
            video_two_file.write_bytes(b"video two")
            manager = TikTokProfileManager(
                db_path=root / "accounts.sqlite3",
                profiles_root=root / "profiles",
                project_root=root,
            )
            video_one = manager.add_video(video_one_file)
            video_two = manager.add_video(video_two_file)

            report = manager.delete_videos([video_one.id, video_two.id])

            self.assertEqual(report["deleted"], 2)
            self.assertEqual(report["deleted_ids"], [video_one.id, video_two.id])
            self.assertEqual(report["missing_files"], 0)
            self.assertEqual(report["errors"], [])
            self.assertFalse(video_one_file.exists())
            self.assertFalse(video_two_file.exists())
            self.assertEqual(manager.list_videos(), [])

    def test_delete_video_removes_stale_row_when_file_is_already_missing(self):
        with _temporary_directory() as temp_dir:
            root = Path(temp_dir)
            video_file = root / "missing.mp4"
            video_file.write_bytes(b"video")
            manager = TikTokProfileManager(
                db_path=root / "accounts.sqlite3",
                profiles_root=root / "profiles",
                project_root=root,
            )
            video = manager.add_video(video_file)
            video_file.unlink()

            report = manager.delete_videos([video.id])

            self.assertEqual(report["deleted"], 1)
            self.assertEqual(report["deleted_ids"], [video.id])
            self.assertEqual(report["missing_files"], 1)
            self.assertEqual(report["errors"], [])
            self.assertEqual(manager.list_videos(), [])

    def test_update_video_status(self):
        with _temporary_directory() as temp_dir:
            root = Path(temp_dir)
            video_file = root / "demo.mp4"
            video_file.write_bytes(b"fake video")
            manager = TikTokProfileManager(
                db_path=root / "accounts.sqlite3",
                profiles_root=root / "profiles",
                project_root=root,
            )
            video = manager.add_video(video_file)

            updated_video = manager.update_video_status(video.id, "file_selected", note="selected")

            self.assertEqual(updated_video.status, "file_selected")
            self.assertEqual(updated_video.note, "selected")

    def test_add_video_draft_cut_mode_and_mark_rendered(self):
        with _temporary_directory() as temp_dir:
            root = Path(temp_dir)
            product_image = root / "product.jpg"
            product_image.write_bytes(b"image")
            marker_file = root / "profile_video_queue" / "profile" / "draft.pending"
            final_video = root / "profile_video_queue" / "profile" / "final.mp4"
            final_video.parent.mkdir(parents=True, exist_ok=True)
            final_video.write_bytes(b"video")
            manager = TikTokProfileManager(
                db_path=root / "accounts.sqlite3",
                profiles_root=root / "profiles",
                project_root=root,
            )
            account = manager.add_account("Profile", "google")

            draft = manager.add_video_draft(
                marker_file,
                source_video_url="https://www.tiktok.com/@store/video/123",
                product_image_path=product_image,
                account_id=account.id,
                product_id="1730667245645826792",
            )

            self.assertEqual(draft.status, "draft")
            self.assertEqual(draft.cut_mode, "original")
            self.assertEqual(draft.source_video_url, "https://www.tiktok.com/@store/video/123")
            self.assertTrue(manager.resolve_video_path(draft).exists())
            self.assertTrue((root / draft.product_image_path).exists())

            updated_cut = manager.update_video_cut_mode(draft.id, "scene")
            self.assertEqual(updated_cut.cut_mode, "scene")

            rendered = manager.mark_video_rendered(draft.id, final_video, source_title="Demo title #food")
            self.assertEqual(rendered.status, "ready")
            self.assertEqual(rendered.caption, "Demo title")
            self.assertEqual(rendered.hashtags, "#food")
            self.assertEqual(manager.resolve_video_path(rendered), final_video)

    def test_find_account_for_profile_slug_supports_bot_suffix(self):
        with _temporary_directory() as temp_dir:
            root = Path(temp_dir)
            manager = TikTokProfileManager(
                db_path=root / "accounts.sqlite3",
                profiles_root=root / "profiles",
                project_root=root,
            )
            account = manager.add_account("Linh An Ngon", "google")

            self.assertEqual(manager.find_account_for_profile_slug("linh_an_ngon"), account)
            self.assertEqual(manager.find_account_for_profile_slug("linh_an_ngon_bot"), account)

    def test_enqueue_telegram_video_copies_file_into_profile_queue(self):
        with _temporary_directory() as temp_dir:
            root = Path(temp_dir)
            source_video = root / "final_video.mp4"
            source_video.write_bytes(b"video")
            manager = TikTokProfileManager(
                db_path=root / "accounts.sqlite3",
                profiles_root=root / "profiles",
                project_root=root,
            )
            account = manager.add_account("Linh An Ngon", "google")
            config = PipelineConfig(tiktok_profile_slug="linh_an_ngon_bot")

            queued = enqueue_telegram_video(
                config,
                source_video,
                "Demo title #topmo #anvat",
                "https://www.tiktok.com/@store/video/123",
                "1730667245645826792",
                manager=manager,
                queue_root=root / "profile_video_queue",
            )

            self.assertTrue(queued)
            videos = manager.list_videos()
            self.assertEqual(len(videos), 1)
            self.assertEqual(videos[0].account_id, account.id)
            self.assertEqual(videos[0].caption, "Demo title")
            self.assertEqual(videos[0].hashtags, "#topmo #anvat #linhanngon")
            self.assertEqual(videos[0].source, "telegram")
            self.assertEqual(videos[0].product_id, "1730667245645826792")
            self.assertTrue(manager.resolve_video_path(videos[0]).exists())

    def test_enqueue_telegram_video_draft_copies_inputs_into_profile_queue(self):
        with _temporary_directory() as temp_dir:
            root = Path(temp_dir)
            product_image = root / "source_product.png"
            product_image.write_bytes(b"image")
            manager = TikTokProfileManager(
                db_path=root / "accounts.sqlite3",
                profiles_root=root / "profiles",
                project_root=root,
            )
            account = manager.add_account("Linh An Ngon", "google")
            config = PipelineConfig(tiktok_profile_slug="linh_an_ngon_bot")

            queued = enqueue_telegram_video_draft(
                config,
                source_video_url="https://www.tiktok.com/@store/video/123",
                product_image_path=product_image,
                product_id="1730667245645826792",
                product_url="https://www.tiktok.com/view/product/1730667245645826792",
                cut_mode="scene",
                manager=manager,
                queue_root=root / "profile_video_queue",
            )

            self.assertTrue(queued)
            videos = manager.list_videos()
            self.assertEqual(len(videos), 1)
            self.assertEqual(videos[0].account_id, account.id)
            self.assertEqual(videos[0].status, "draft")
            self.assertEqual(videos[0].cut_mode, "scene")
            self.assertEqual(videos[0].source, "telegram")
            self.assertEqual(videos[0].source_video_url, "https://www.tiktok.com/@store/video/123")
            self.assertEqual(videos[0].product_id, "1730667245645826792")
            self.assertEqual(videos[0].hashtags, "#linhanngon")
            self.assertTrue(manager.resolve_video_path(videos[0]).exists())
            self.assertTrue((root / videos[0].product_image_path).exists())


if __name__ == "__main__":
    unittest.main()
