"""Fashion-product ingestion shared by the dedicated bot and the Qt workspace."""

from __future__ import annotations

from pathlib import Path

from auto_tiktok_editor.app.gemini import write_fashion_product_copy
from auto_tiktok_editor.app.tiktok_shop import (
    TikTokShopProduct,
    download_tiktok_shop_product_image,
    resolve_tiktok_shop_product,
)
from auto_tiktok_editor.gemini_settings import get_gemini_api_key, get_gemini_model
from auto_tiktok_editor.app.gemini import DEFAULT_GEMINI_MODEL
from auto_tiktok_editor.tiktok_profiles.models import FashionProduct
from auto_tiktok_editor.tiktok_profiles.profile_manager import TikTokProfileManager


def receive_fashion_product(manager: TikTokProfileManager, product_url: str) -> FashionProduct:
    """Resolve a Shop link, download its product image and persist a processing record."""

    product = resolve_tiktok_shop_product(product_url)
    image_dir = Path(manager.project_root) / "fashion_products" / "images"
    image_path = download_tiktok_shop_product_image(product, image_dir)
    return manager.add_fashion_product(
        product_url=product.resolved_url,
        product_id=product.product_id,
        product_name=product.title,
        image_path=image_path,
        status="processing",
    )


def generate_fashion_product_description(
    manager: TikTokProfileManager,
    product: FashionProduct,
) -> FashionProduct:
    """Use the saved Gemini selection to create the product description silently."""

    image_path = manager.resolve_fashion_product_image_path(product)
    if image_path is None or not image_path.is_file():
        raise ValueError("Không tìm thấy ảnh sản phẩm đã lưu.")
    manager.update_fashion_product_status(product.id, "processing", note="")
    generated = write_fashion_product_copy(
        image_path=image_path,
        product_name=product.product_name,
        api_key=get_gemini_api_key(),
        model=get_gemini_model(DEFAULT_GEMINI_MODEL),
    )
    return manager.update_fashion_product_copy(
        product.id,
        caption=generated.caption,
        hashtags=" ".join(generated.hashtags),
        description=generated.description,
        status="ready",
        note="",
    )


def receive_and_generate_fashion_product(manager: TikTokProfileManager, product_url: str) -> FashionProduct:
    product = receive_fashion_product(manager, product_url)
    try:
        return generate_fashion_product_description(manager, product)
    except Exception as exc:
        manager.update_fashion_product_status(product.id, "error", note=str(exc))
        raise
