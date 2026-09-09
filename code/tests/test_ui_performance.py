from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class UiPerformanceBehaviorTests(unittest.TestCase):
    def test_reapplying_same_global_theme_does_not_repolish_widget_tree(self):
        from auto_tiktok_editor.tiktok_profiles.qt_ui import app

        window = SimpleNamespace(
            _applied_theme_mode=None,
            setStyleSheet=mock.Mock(),
        )
        with mock.patch.object(app, "set_current_theme_mode"), mock.patch.object(
            app, "setTheme"
        ) as set_theme, mock.patch.object(app, "setThemeColor") as set_color:
            app.TikTokProfileManagerApp.apply_theme_mode(window, "light", initial=True)
            app.TikTokProfileManagerApp.apply_theme_mode(window, "light", initial=True)

        set_theme.assert_called_once()
        set_color.assert_called_once()
        window.setStyleSheet.assert_called_once()

    def test_unchanged_sources_do_not_rebuild_table(self):
        from auto_tiktok_editor.tiktok_profiles.qt_ui.views.sources_view import SourcesView

        source = SimpleNamespace(
            id=1,
            account_id=2,
            name="source",
            url="https://example.test",
            note="",
            featured=False,
            enabled=True,
            updated_at="2026-01-01T00:00:00",
        )
        view = SimpleNamespace(
            manager=SimpleNamespace(list_source_channels=mock.Mock(return_value=[source])),
            profile_combo=SimpleNamespace(currentData=lambda: None),
            _sources_signature=None,
            _sources_cache=[],
            _populate_table=mock.Mock(),
        )

        SourcesView.refresh_sources(view)
        SourcesView.refresh_sources(view)

        view._populate_table.assert_called_once_with([source])

    def test_video_refresh_updates_existing_rows_in_place(self):
        from auto_tiktok_editor.tiktok_profiles.qt_ui.views.videos_view import VideosView

        video = SimpleNamespace(
            id=1,
            account_id=None,
            file_path="video.mp4",
            caption="caption",
            hashtags="#tag",
            product_id="",
            publish_mode="manual",
            scheduled_at=None,
            status="ready",
            cut_mode="fixed",
            updated_at="2026-01-01T00:00:00",
        )
        view = SimpleNamespace(
            manager=SimpleNamespace(
                list_videos=mock.Mock(return_value=[video]),
                list_facebook_videos=mock.Mock(return_value=[]),
            ),
            _current_account_name=None,
            _videos_cache=[video],
            _last_videos_signature=None,
            table=SimpleNamespace(rowCount=lambda: 1),
            _update_table_in_place=mock.Mock(),
            _populate_table_preserving_state=mock.Mock(),
            window=lambda: None,
        )

        VideosView.refresh_videos(view)

        view._update_table_in_place.assert_called_once_with([video])
        view._populate_table_preserving_state.assert_not_called()


if __name__ == "__main__":
    unittest.main()
