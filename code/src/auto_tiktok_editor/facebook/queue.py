"""Prepare TikTok videos for the independent Facebook phone-send queue."""

from __future__ import annotations

from auto_tiktok_editor.app.gemini import DEFAULT_GEMINI_MODEL, rewrite_facebook_product_name
from auto_tiktok_editor.app.tiktok_shop import resolve_tiktok_shop_product_title
from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.gemini_settings import get_gemini_api_key, get_gemini_model
from auto_tiktok_editor.phone_control import PhoneController
from auto_tiktok_editor.tiktok_profiles.models import FacebookVideo
from auto_tiktok_editor.tiktok_profiles.profile_manager import TikTokProfileManager


def prepare_facebook_video(
    manager: TikTokProfileManager,
    facebook_video_id: int,
) -> FacebookVideo:
    """Resolve the product link and persist a Gemini-rewritten display name."""
    video = manager.get_facebook_video(facebook_video_id)
    if video is None:
        raise ValueError("Không tìm thấy video Facebook: %s" % facebook_video_id)
    manager.update_facebook_video_status(video.id, "processing", note="")
    if not video.product_url:
        raise ValueError("Video không có link sản phẩm để lấy tên.")

    source_product_name = resolve_tiktok_shop_product_title(video.product_url)
    display_name = rewrite_facebook_product_name(
        source_product_name,
        api_key=get_gemini_api_key(),
        model=get_gemini_model(DEFAULT_GEMINI_MODEL),
    )
    return manager.update_facebook_video_preparation(
        video.id,
        source_product_name=source_product_name,
        display_product_name=display_name,
        status="ready",
        note="",
    )


def send_facebook_video_to_phone(
    manager: TikTokProfileManager,
    config: PipelineConfig,
    facebook_video_id: int,
    *,
    address: str,
    connection_mode: str,
    controller: PhoneController | None = None,
) -> dict[str, object]:
    """Send the Facebook snapshot and finish with its display name in the phone clipboard."""
    video = manager.get_facebook_video(facebook_video_id)
    if video is None:
        raise ValueError("Không tìm thấy video Facebook: %s" % facebook_video_id)
    if not video.display_product_name or len(video.display_product_name) > 50:
        raise ValueError("Tên hiển thị phải có nội dung và không quá 50 ký tự.")

    path = manager.resolve_facebook_video_path(video)
    phone_controller = controller or PhoneController(config)
    phone_result = phone_controller.send_file_to_gallery(
        address,
        path,
        connection_mode=connection_mode,
    )
    target = str(phone_result.get("address") or address)
    caption_text = " ".join(
        part for part in (video.caption.strip(), video.hashtags.strip()) if part
    )
    if caption_text:
        phone_controller.copy_text_to_clipboard(
            caption_text,
            label="Facebook description and hashtags",
            address=target,
            sync_to_phone=True,
            require_phone_clipboard=False,
        )
    phone_controller.copy_text_to_clipboard(
        video.display_product_name,
        label="Facebook product display name",
        address=target,
        sync_to_phone=True,
        require_phone_clipboard=False,
    )
    manager.update_facebook_video_status(video.id, "sent", note="")
    manager.add_log(
        "info",
        "facebook_phone_send",
        "Đã gửi video Facebook và tên sản phẩm '%s' sang điện thoại."
        % video.display_product_name,
        account_id=video.source_account_id,
        video_id=video.source_video_id,
    )
    return phone_result
