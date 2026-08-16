"""Qt6 Fluent Design UI Package for TikTok Profile Manager."""

from __future__ import annotations

__all__ = ["launch_qt_profile_manager"]


def launch_qt_profile_manager(manager=None, config=None) -> int:
    from auto_tiktok_editor.tiktok_profiles.qt_ui.app import launch_app
    return launch_app(manager=manager, config=config)
