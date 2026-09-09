"""Safe, user-facing exceptions for the Facebook Graph API."""

from __future__ import annotations


class FacebookGraphError(Exception):
    """Base error whose message never includes API payloads or access tokens."""

    default_message = "Meta Graph API trả về lỗi không xác định."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: int | None = None,
        error_type: str = "",
    ) -> None:
        super().__init__(message or self.default_message)
        self.code = code
        self.error_type = error_type


class FacebookInvalidTokenError(FacebookGraphError):
    default_message = "Access Token không hợp lệ hoặc đã hết hạn."


class FacebookPermissionError(FacebookGraphError):
    default_message = "Tài khoản chưa cấp đủ quyền để đọc danh sách Page."


class FacebookRateLimitError(FacebookGraphError):
    default_message = "Meta Graph API đang giới hạn request. Hãy thử lại sau."


class FacebookNetworkError(FacebookGraphError):
    default_message = "Không thể kết nối Meta Graph API."


class FacebookResponseError(FacebookGraphError):
    default_message = "Dữ liệu Meta Graph API trả về không hợp lệ."
