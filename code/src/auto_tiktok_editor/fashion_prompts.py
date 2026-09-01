"""Built-in fashion video prompt presets."""

from __future__ import annotations

from dataclasses import dataclass
import unicodedata


@dataclass(frozen=True)
class FashionPromptPreset:
    key: str
    label: str
    first_scene: str
    second_scene: str


FASHION_PROMPT_PRESETS = (
    FashionPromptPreset(
        key="t_shirt",
        label="Áo thun",
        first_scene=(
            "Người mẫu đứng trước gương, giữ nguyên tư thế và cầm điện thoại quay selfie. "
            "Dùng **tay trái** vuốt nhẹ từ phần ngực áo xuống tới bụng, sau đó dùng tay trái "
            "cầm nhẹ vạt áo và kéo lên một chút để thể hiện form và chất liệu áo. Cuối cùng thả "
            "áo xuống tự nhiên rồi đưa tay trái trở lại túi quần. Chuyển động chậm, mượt, tự nhiên. "
            "Giữ nguyên khuôn mặt, cơ thể, quần áo, điện thoại, phông nền và bố cục."
        ),
        second_scene=(
            "Người mẫu đứng trước gương cầm điện thoại quay selfie. Từ từ **xoay người nhẹ khoảng "
            "30 độ sang phải**, đồng thời **dang nhẹ tay trái ra khỏi cơ thể** để thấy rõ form áo "
            "đang mặc. Sau đó dùng **tay trái vuốt nhẹ vạt áo 2 lần** một cách tự nhiên. Cuối cùng "
            "từ từ **xoay người trở lại chính diện**, đứng thẳng để thấy rõ toàn bộ phần thân áo. "
            "Chuyển động chậm, mượt và tự nhiên. Giữ nguyên khuôn mặt, cơ thể, quần áo, điện thoại, "
            "phông nền và bố cục."
        ),
    ),
)

DEFAULT_CHANGE_OUTFIT_PROMPT = (
    "Promt thay đồ",
    "Cho nhân vật ảnh 1 thay đồ của ảnh 2 nhưng vẫn giữ bố cục của ảnh 1",
)


def get_fashion_prompt_preset(key: str) -> FashionPromptPreset:
    """Return a known preset, using the first preset as a safe fallback."""
    for preset in FASHION_PROMPT_PRESETS:
        if preset.key == key:
            return preset
    return FASHION_PROMPT_PRESETS[0]


def is_change_outfit_prompt_name(name: str) -> bool:
    """Identify the legacy 'Thay đồ' prompt so it can be moved to the shared area."""
    normalized = unicodedata.normalize("NFD", str(name or ""))
    normalized = "".join(char for char in normalized if not unicodedata.combining(char)).lower()
    normalized = normalized.replace("đ", "d")
    return "thay do" in normalized
