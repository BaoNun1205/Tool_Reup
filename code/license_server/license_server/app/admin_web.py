from __future__ import annotations

from datetime import timedelta, timezone
from html import escape
from math import ceil
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from license_server.app.config import LicenseServerConfig
from license_server.app.database import get_config, get_db
from license_server.app.security import sign_admin_session, utcnow, verify_admin_session
from license_server.app.services import AdminService, AuthError, LicenseError, LicenseService


router = APIRouter(include_in_schema=False)


def _latest_license(user):
    licenses = sorted(
        user.licenses,
        key=lambda record: (_as_utc(record.expires_at), _as_utc(record.created_at)),
        reverse=True,
    )
    return licenses[0] if licenses else None


def _as_utc(value):
    if value is None:
        return None
    if getattr(value, "tzinfo", None) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _format_dt(value) -> str:
    if value is None:
        return "-"
    normalized = _as_utc(value)
    return str(normalized).replace("T", " ")[:19]


def _badge(text: str, tone: str) -> str:
    return f'<span class="badge {escape(tone)}">{escape(text)}</span>'


def _safe_redirect_target(value: str | None, fallback: str = "/admin") -> str:
    candidate = str(value or "").strip()
    if candidate.startswith("/admin"):
        return candidate
    return fallback


def _flash_redirect(path: str, *, message: str | None = None, error: str | None = None) -> RedirectResponse:
    params = {}
    if message:
        params["message"] = message
    if error:
        params["error"] = error
    url = path if not params else f"{path}?{urlencode(params)}"
    return RedirectResponse(url=url, status_code=303)


def _redirect_with_flash(target: str, *, message: str | None = None, error: str | None = None) -> RedirectResponse:
    target = _safe_redirect_target(target)
    separator = "&" if "?" in target else "?"
    params = {}
    if message:
        params["message"] = message
    if error:
        params["error"] = error
    if not params:
        return RedirectResponse(url=target, status_code=303)
    return RedirectResponse(url=f"{target}{separator}{urlencode(params)}", status_code=303)


def _is_secure_request(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto", "").strip().lower()
    return request.url.scheme == "https" or proto == "https"


def _load_admin_user(request: Request, db: Session, config: LicenseServerConfig):
    token = request.cookies.get(config.admin_session_cookie_name)
    if not token:
        return None
    try:
        payload = verify_admin_session(config, token)
        service = LicenseService(db, config)
        user = service.get_user(payload["sub"])
        if not user.is_active or not user.is_admin:
            return None
        return user
    except Exception:
        return None


def _require_admin_user(request: Request, db: Session, config: LicenseServerConfig):
    user = _load_admin_user(request, db, config)
    if user is None:
        return None, _flash_redirect("/admin/login", error="Phiên quản trị không hợp lệ hoặc đã hết hạn.")
    return user, None


def _build_dashboard_query(filters: dict, *, users_page: int | None = None, licenses_page: int | None = None) -> str:
    params = {
        "search": filters["search"],
        "account_status": filters["account_status"],
        "role": filters["role"],
        "license_status": filters["license_status"],
        "expires_in_days": filters["expires_in_days"],
        "sort": filters["sort"],
        "direction": filters["direction"],
        "users_page": str(users_page if users_page is not None else filters["users_page"]),
        "licenses_page": str(licenses_page if licenses_page is not None else filters["licenses_page"]),
        "page_size": str(filters["page_size"]),
    }
    compact = {key: value for key, value in params.items() if str(value).strip() and str(value) != "all"}
    return f"/admin?{urlencode(compact)}" if compact else "/admin"


def _pagination(total_items: int, page: int, page_size: int) -> dict:
    total_pages = max(1, ceil(total_items / page_size)) if page_size > 0 else 1
    current_page = min(max(1, page), total_pages)
    start = (current_page - 1) * page_size
    end = start + page_size
    return {
        "total_items": total_items,
        "page": current_page,
        "page_size": page_size,
        "total_pages": total_pages,
        "start": start,
        "end": end,
    }


def _page(title: str, body: str, *, current_admin=None, message: str = "", error: str = "") -> HTMLResponse:
    topbar = ""
    if current_admin is not None:
        topbar = f"""
        <div class="topbar">
          <div>
            <div class="eyebrow">Auto TikTok Editor</div>
            <h1>Bảng điều khiển quản trị</h1>
          </div>
          <div class="topbar-actions">
            <div class="admin-chip">Đăng nhập: {escape(current_admin.username)}</div>
            <form method="post" action="/admin/logout">
              <button type="submit" class="ghost-button">Đăng xuất</button>
            </form>
          </div>
        </div>
        """
    flashes = ""
    if message:
        flashes += f'<div class="flash success">{escape(message)}</div>'
    if error:
        flashes += f'<div class="flash error">{escape(error)}</div>'
    html = f"""
    <!doctype html>
    <html lang="vi">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>{escape(title)}</title>
      <style>
        :root {{
          --bg: #08111f;
          --hero: #0f1b2f;
          --panel: #101c30;
          --panel-alt: #14233c;
          --border: #253a5f;
          --text: #f5f7fb;
          --muted: #90a4c5;
          --green: #1ed3a5;
          --blue: #63a9ff;
          --amber: #f3c969;
          --red: #ff7d8d;
        }}
        * {{ box-sizing: border-box; }}
        body {{
          margin: 0;
          font-family: "Segoe UI", "Noto Sans", sans-serif;
          background: radial-gradient(circle at top left, #173058, var(--bg) 42%);
          color: var(--text);
        }}
        a {{ color: var(--blue); text-decoration: none; }}
        code {{ font-family: "Consolas", monospace; }}
        .shell {{ max-width: 1500px; margin: 0 auto; padding: 28px; }}
        .topbar {{
          display: flex;
          justify-content: space-between;
          align-items: flex-start;
          gap: 16px;
          margin-bottom: 22px;
        }}
        .topbar h1 {{ margin: 6px 0 0; font-size: 34px; }}
        .eyebrow {{
          color: var(--muted);
          text-transform: uppercase;
          letter-spacing: .12em;
          font-size: 12px;
        }}
        .topbar-actions {{ display: flex; align-items: center; gap: 12px; }}
        .admin-chip {{
          padding: 10px 14px;
          background: rgba(99,169,255,.12);
          border: 1px solid rgba(99,169,255,.35);
          border-radius: 999px;
          color: #d8e8ff;
        }}
        .flash {{
          padding: 14px 16px;
          border-radius: 14px;
          margin-bottom: 16px;
          border: 1px solid transparent;
        }}
        .flash.success {{ background: rgba(30,211,165,.12); border-color: rgba(30,211,165,.3); color: #c9f9eb; }}
        .flash.error {{ background: rgba(255,125,141,.12); border-color: rgba(255,125,141,.3); color: #ffd5dc; }}
        .hero-card {{
          background: linear-gradient(135deg, rgba(99,169,255,.16), rgba(30,211,165,.08));
          border: 1px solid rgba(99,169,255,.28);
          border-radius: 22px;
          padding: 24px;
          margin-bottom: 18px;
        }}
        .hero-card h2 {{ margin: 0 0 10px; font-size: 30px; }}
        .hero-card p {{ margin: 0; color: #d4def0; max-width: 840px; }}
        .stats {{
          display: grid;
          grid-template-columns: repeat(4, minmax(0, 1fr));
          gap: 14px;
          margin-bottom: 18px;
        }}
        .stat {{
          background: var(--panel-alt);
          border: 1px solid var(--border);
          border-radius: 16px;
          padding: 16px;
        }}
        .stat .label {{ color: var(--muted); font-size: 13px; margin-bottom: 8px; }}
        .stat .value {{ font-size: 28px; font-weight: 700; }}
        .layout {{
          display: grid;
          grid-template-columns: 360px minmax(0, 1fr);
          gap: 20px;
          align-items: start;
        }}
        .stack {{ display: grid; gap: 18px; }}
        .panel {{
          background: linear-gradient(180deg, rgba(255,255,255,.02), rgba(255,255,255,.01));
          border: 1px solid var(--border);
          border-radius: 20px;
          padding: 18px;
          box-shadow: 0 18px 50px rgba(0,0,0,.25);
        }}
        .panel h2, .panel h3, .panel h4 {{ margin: 0 0 14px; }}
        .muted {{ color: var(--muted); }}
        label {{ display: block; font-size: 13px; color: var(--muted); margin-bottom: 6px; }}
        input, select, textarea, button {{
          width: 100%;
          font: inherit;
          border-radius: 12px;
        }}
        input, select, textarea {{
          background: #182740;
          border: 1px solid var(--border);
          color: var(--text);
          padding: 11px 12px;
          margin-bottom: 12px;
        }}
        textarea {{ min-height: 88px; resize: vertical; }}
        button {{
          border: 0;
          padding: 11px 14px;
          background: var(--green);
          color: #06251f;
          font-weight: 700;
          cursor: pointer;
        }}
        button.secondary {{
          background: #27405f;
          color: var(--text);
        }}
        .ghost-button {{
          width: auto;
          background: transparent;
          border: 1px solid var(--border);
          color: var(--text);
          padding: 10px 14px;
        }}
        .toolbar {{
          display: grid;
          grid-template-columns: minmax(220px, 1.5fr) repeat(4, minmax(120px, .8fr)) auto;
          gap: 10px;
          align-items: end;
        }}
        .toolbar-actions {{
          display: flex;
          gap: 8px;
          align-items: center;
        }}
        .toolbar-actions a {{
          display: inline-flex;
          justify-content: center;
          align-items: center;
          min-width: 96px;
          padding: 11px 14px;
          border-radius: 12px;
          border: 1px solid var(--border);
          color: var(--text);
        }}
        .table-wrap {{ overflow-x: auto; }}
        table {{ width: 100%; border-collapse: collapse; }}
        th, td {{
          text-align: left;
          padding: 12px 10px;
          border-bottom: 1px solid rgba(144,164,197,.12);
          vertical-align: top;
        }}
        th {{
          color: var(--muted);
          font-size: 12px;
          text-transform: uppercase;
          letter-spacing: .08em;
        }}
        .badge {{
          display: inline-flex;
          align-items: center;
          border-radius: 999px;
          padding: 6px 10px;
          font-size: 12px;
          font-weight: 700;
        }}
        .badge.green {{ background: rgba(30,211,165,.14); color: #c9f9eb; }}
        .badge.blue {{ background: rgba(99,169,255,.14); color: #d9eaff; }}
        .badge.amber {{ background: rgba(243,201,105,.15); color: #ffe7ad; }}
        .badge.red {{ background: rgba(255,125,141,.14); color: #ffd8de; }}
        .inline-form {{
          display: flex;
          gap: 8px;
          align-items: center;
        }}
        .inline-form input, .inline-form select {{
          margin: 0;
          min-width: 0;
        }}
        .inline-form button {{
          width: auto;
        }}
        .helper {{
          font-size: 12px;
          color: var(--muted);
          margin-top: -2px;
        }}
        .pagination {{
          display: flex;
          justify-content: space-between;
          align-items: center;
          gap: 10px;
          margin-top: 14px;
          flex-wrap: wrap;
        }}
        .pagination .pager {{
          display: flex;
          gap: 8px;
          align-items: center;
          flex-wrap: wrap;
        }}
        .pagination a {{
          display: inline-flex;
          justify-content: center;
          align-items: center;
          min-width: 42px;
          padding: 10px 12px;
          border-radius: 12px;
          border: 1px solid var(--border);
          color: var(--text);
          background: transparent;
        }}
        .pagination .current {{
          color: var(--muted);
        }}
        .detail-header {{
          display: flex;
          justify-content: space-between;
          align-items: flex-start;
          gap: 18px;
          margin-bottom: 18px;
        }}
        .detail-header h2 {{
          margin: 0 0 8px;
          font-size: 30px;
        }}
        .detail-actions {{
          display: flex;
          gap: 10px;
          flex-wrap: wrap;
        }}
        .detail-actions a {{
          display: inline-flex;
          justify-content: center;
          align-items: center;
          padding: 11px 14px;
          border-radius: 12px;
          border: 1px solid var(--border);
          color: var(--text);
        }}
        .detail-grid {{
          display: grid;
          grid-template-columns: minmax(0, 1.5fr) minmax(280px, .9fr);
          gap: 18px;
          margin-bottom: 18px;
        }}
        .detail-box {{
          background: var(--panel-alt);
          border: 1px solid rgba(144,164,197,.12);
          border-radius: 16px;
          padding: 16px;
        }}
        .detail-stats {{
          display: grid;
          grid-template-columns: repeat(4, minmax(0, 1fr));
          gap: 12px;
        }}
        .detail-stat {{
          background: rgba(8,17,31,.35);
          border: 1px solid rgba(144,164,197,.12);
          border-radius: 14px;
          padding: 14px;
        }}
        .detail-stat .label {{
          color: var(--muted);
          font-size: 12px;
          margin-bottom: 6px;
        }}
        .detail-stat .value {{
          font-size: 22px;
          font-weight: 700;
        }}
        .kv {{
          display: grid;
          grid-template-columns: 130px 1fr;
          gap: 8px 12px;
        }}
        .kv div {{
          padding: 4px 0;
        }}
        .section-grid {{
          display: grid;
          gap: 18px;
        }}
        .empty-state {{
          padding: 22px;
          border: 1px dashed rgba(144,164,197,.22);
          border-radius: 16px;
          color: var(--muted);
          text-align: center;
        }}
        @media (max-width: 1180px) {{
          .layout {{ grid-template-columns: 1fr; }}
          .stats {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
          .toolbar {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
          .detail-grid {{ grid-template-columns: 1fr; }}
          .detail-stats {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
        }}
        @media (max-width: 760px) {{
          .shell {{ padding: 16px; }}
          .topbar {{ flex-direction: column; }}
          .topbar-actions {{ width: 100%; justify-content: space-between; }}
          .stats {{ grid-template-columns: 1fr; }}
          .toolbar {{ grid-template-columns: 1fr; }}
          .detail-stats {{ grid-template-columns: 1fr; }}
          .kv {{ grid-template-columns: 1fr; }}
          .detail-header {{ flex-direction: column; }}
        }}
      </style>
    </head>
    <body>
      <div class="shell">
        {topbar}
        {flashes}
        {body}
      </div>
    </body>
    </html>
    """
    return HTMLResponse(html)


def _login_page(*, error: str = "", message: str = "") -> HTMLResponse:
    body = """
    <div style="max-width: 500px; margin: 64px auto;">
      <div class="panel">
        <div class="eyebrow">License Server</div>
        <h2 style="margin-top: 8px;">Đăng nhập quản trị</h2>
        <p class="muted">Dùng tài khoản quản trị đã tạo bằng bootstrap để quản lý khách hàng, license, thiết bị và phiên đăng nhập.</p>
        <form method="post" action="/admin/login">
          <label>Tài khoản</label>
          <input type="text" name="username" required>
          <label>Mật khẩu</label>
          <input type="password" name="password" required>
          <button type="submit">Đăng nhập</button>
        </form>
      </div>
    </div>
    """
    return _page("Đăng nhập quản trị", body, message=message, error=error)


def _render_pagination(label: str, pagination: dict, page_builder) -> str:
    if pagination["total_pages"] <= 1:
        return ""
    prev_page = max(1, pagination["page"] - 1)
    next_page = min(pagination["total_pages"], pagination["page"] + 1)
    return f"""
    <div class="pagination">
      <div class="current">{escape(label)}: trang {pagination["page"]}/{pagination["total_pages"]} | tổng {pagination["total_items"]}</div>
      <div class="pager">
        <a href="{escape(page_builder(1))}">Đầu</a>
        <a href="{escape(page_builder(prev_page))}">Trước</a>
        <a href="{escape(page_builder(next_page))}">Sau</a>
        <a href="{escape(page_builder(pagination["total_pages"]))}">Cuối</a>
      </div>
    </div>
    """


def _render_dashboard(
    *,
    current_admin,
    all_users,
    users,
    licenses,
    filters,
    users_pagination,
    licenses_pagination,
    message: str = "",
    error: str = "",
) -> HTMLResponse:
    total_users = len(all_users)
    active_users = len([user for user in all_users if user.is_active])
    active_licenses = len([user for user in all_users if (_latest_license(user) and _latest_license(user).status == "active")])
    expiring_soon = len(
        [
            user for user in all_users
            if (_latest_license(user) and _as_utc(_latest_license(user).expires_at) <= utcnow() + timedelta(days=7))
        ]
    )

    user_rows = []
    for user in users:
        current_license = _latest_license(user)
        user_rows.append(
            f"""
            <tr>
              <td><a href="/admin/users/{escape(user.id)}"><strong>{escape(user.username)}</strong></a></td>
              <td>{_badge("Quản trị" if user.is_admin else "Khách hàng", "blue" if user.is_admin else "amber")}</td>
              <td>{_badge("Đang hoạt động" if user.is_active else "Đã khóa", "green" if user.is_active else "red")}</td>
              <td><code>{escape(current_license.license_code if current_license else "-")}</code></td>
              <td>{escape(current_license.plan_name if current_license else "-")}</td>
              <td>{escape(_format_dt(current_license.expires_at) if current_license else "-")}</td>
              <td>
                <div class="inline-form">
                  <a href="/admin/users/{escape(user.id)}">Chi tiết</a>
                </div>
              </td>
            </tr>
            """
        )

    license_rows = []
    for record in licenses:
        tone = "green" if record.status == "active" else "red"
        license_rows.append(
            f"""
            <tr>
              <td><code>{escape(record.license_code)}</code></td>
              <td><a href="/admin/users/{escape(record.user.id)}">{escape(record.user.username)}</a></td>
              <td>{escape(record.plan_name)}</td>
              <td>{_badge(record.status, tone)}</td>
              <td>{escape(_format_dt(record.expires_at))}</td>
              <td>{escape(f"{record.max_devices} máy / {record.max_concurrent_sessions} phiên")}</td>
              <td>
                <a href="/admin/users/{escape(record.user.id)}">Mở chi tiết</a>
              </td>
            </tr>
            """
        )

    body = f"""
    <div class="hero-card">
      <div class="eyebrow">License control center</div>
      <h2>Quản lý tài khoản, license và chống chia sẻ</h2>
      <p>Theo dõi toàn bộ khách hàng đang hoạt động, lọc nhanh theo hạn dùng, phân loại theo vai trò và nhảy thẳng vào trang chi tiết từng user để khóa tài khoản, thu hồi thiết bị hoặc phiên đăng nhập.</p>
    </div>
    <div class="stats">
      <div class="stat"><div class="label">Tổng số user</div><div class="value">{total_users}</div></div>
      <div class="stat"><div class="label">User đang hoạt động</div><div class="value">{active_users}</div></div>
      <div class="stat"><div class="label">License đang active</div><div class="value">{active_licenses}</div></div>
      <div class="stat"><div class="label">Sắp hết hạn trong 7 ngày</div><div class="value">{expiring_soon}</div></div>
    </div>
    <div class="panel" style="margin-bottom:18px;">
      <div class="eyebrow">Bộ lọc nhanh</div>
      <h3>Tìm kiếm, sắp xếp và phân trang</h3>
      <form method="get" action="/admin" class="toolbar">
        <div>
          <label>Tìm kiếm</label>
          <input type="text" name="search" value="{escape(filters["search"])}" placeholder="username, license code, plan">
        </div>
        <div>
          <label>Trạng thái user</label>
          <select name="account_status">
            {"".join(f'<option value="{value}"{" selected" if filters["account_status"] == value else ""}>{label}</option>' for value, label in (("all", "Tất cả"), ("active", "Đang hoạt động"), ("disabled", "Đã khóa")))}
          </select>
        </div>
        <div>
          <label>Vai trò</label>
          <select name="role">
            {"".join(f'<option value="{value}"{" selected" if filters["role"] == value else ""}>{label}</option>' for value, label in (("all", "Tất cả"), ("admin", "Quản trị"), ("user", "Khách hàng")))}
          </select>
        </div>
        <div>
          <label>Trạng thái license</label>
          <select name="license_status">
            {"".join(f'<option value="{value}"{" selected" if filters["license_status"] == value else ""}>{label}</option>' for value, label in (("all", "Tất cả"), ("active", "Active"), ("disabled", "Disabled"), ("expired", "Expired"), ("none", "Chưa có")))}
          </select>
        </div>
        <div>
          <label>Sắp hết hạn trong</label>
          <select name="expires_in_days">
            {"".join(f'<option value="{value}"{" selected" if filters["expires_in_days"] == value else ""}>{label}</option>' for value, label in (("all", "Tất cả"), ("7", "7 ngày"), ("30", "30 ngày"), ("90", "90 ngày")))}
          </select>
        </div>
        <div>
          <label>Sắp xếp</label>
          <select name="sort">
            {"".join(f'<option value="{value}"{" selected" if filters["sort"] == value else ""}>{label}</option>' for value, label in (("created_at", "Ngày tạo user"), ("username", "Username"), ("expires_at", "Hạn license"), ("plan_name", "Gói dịch vụ")))}
          </select>
        </div>
        <div>
          <label>Thứ tự</label>
          <select name="direction">
            {"".join(f'<option value="{value}"{" selected" if filters["direction"] == value else ""}>{label}</option>' for value, label in (("desc", "Giảm dần"), ("asc", "Tăng dần")))}
          </select>
        </div>
        <div>
          <label>Số dòng / trang</label>
          <select name="page_size">
            {"".join(f'<option value="{value}"{" selected" if str(filters["page_size"]) == str(value) else ""}>{value}</option>' for value in (10, 20, 50, 100))}
          </select>
        </div>
        <div class="toolbar-actions">
          <button type="submit">Áp dụng</button>
          <a href="/admin">Đặt lại</a>
        </div>
      </form>
      <div class="helper">Bộ lọc hiện tại đang hiển thị {len(users)} user và {len(licenses)} license trong trang này.</div>
    </div>
    <div class="layout">
      <div class="stack">
        <div class="panel">
          <div class="eyebrow">Tài khoản mới</div>
          <h3>Tạo user</h3>
          <form method="post" action="/admin/users/create">
            <label>Username</label>
            <input type="text" name="username" required>
            <label>Mật khẩu</label>
            <input type="password" name="password" required>
            <label><input type="checkbox" name="is_admin" value="true" style="width:auto;margin-right:8px;"> Cấp quyền quản trị</label>
            <input type="hidden" name="redirect_to" value="{escape(_build_dashboard_query(filters))}">
            <button type="submit">Tạo user</button>
          </form>
        </div>
        <div class="panel">
          <div class="eyebrow">Cấp license</div>
          <h3>Issue license mới</h3>
          <form method="post" action="/admin/licenses/issue">
            <label>User</label>
            <select name="user_id" required>
              {"".join(f'<option value="{escape(user.id)}">{escape(user.username)}</option>' for user in all_users)}
            </select>
            <label>Gói dịch vụ</label>
            <input type="text" name="plan_name" value="standard" required>
            <label>Số ngày</label>
            <input type="number" name="days" min="1" value="30" required>
            <label>Số máy tối đa</label>
            <input type="number" name="max_devices" min="1" value="1" required>
            <label>Số phiên đồng thời</label>
            <input type="number" name="max_concurrent_sessions" min="1" value="1" required>
            <label>Ghi chú</label>
            <textarea name="notes" placeholder="Ví dụ: gói tháng, không chia sẻ"></textarea>
            <input type="hidden" name="redirect_to" value="{escape(_build_dashboard_query(filters))}">
            <button type="submit">Cấp license</button>
          </form>
        </div>
      </div>
      <div class="stack">
        <div class="panel">
          <div class="eyebrow">Danh sách tài khoản</div>
          <h3>Users</h3>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Username</th>
                  <th>Vai trò</th>
                  <th>Trạng thái</th>
                  <th>License code</th>
                  <th>Gói</th>
                  <th>Hết hạn</th>
                  <th>Thao tác</th>
                </tr>
              </thead>
              <tbody>
                {"".join(user_rows) or '<tr><td colspan="7" class="muted">Không có user nào khớp bộ lọc.</td></tr>'}
              </tbody>
            </table>
          </div>
          {_render_pagination("Users", users_pagination, lambda page: _build_dashboard_query(filters, users_page=page))}
        </div>
        <div class="panel">
          <div class="eyebrow">Danh sách license</div>
          <h3>Licenses</h3>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Code</th>
                  <th>User</th>
                  <th>Gói</th>
                  <th>Trạng thái</th>
                  <th>Hết hạn</th>
                  <th>Giới hạn</th>
                  <th>Chi tiết</th>
                </tr>
              </thead>
              <tbody>
                {"".join(license_rows) or '<tr><td colspan="7" class="muted">Không có license nào khớp bộ lọc.</td></tr>'}
              </tbody>
            </table>
          </div>
          {_render_pagination("Licenses", licenses_pagination, lambda page: _build_dashboard_query(filters, licenses_page=page))}
        </div>
      </div>
    </div>
    """
    return _page("Bảng điều khiển quản trị", body, current_admin=current_admin, message=message, error=error)


def _render_user_detail(*, current_admin, user, devices, sessions, redirect_to: str, message: str = "", error: str = "") -> HTMLResponse:
    licenses = sorted(
        user.licenses,
        key=lambda record: (_as_utc(record.expires_at), _as_utc(record.created_at)),
        reverse=True,
    )
    current_license = licenses[0] if licenses else None
    active_devices = len([device for device in devices if device.is_active and device.revoked_at is None])
    active_sessions = len([session for session in sessions if session.revoked_at is None])

    license_rows = []
    for record in licenses:
        tone = "green" if record.status == "active" else "red"
        license_rows.append(
            f"""
            <tr>
              <td><code>{escape(record.license_code)}</code></td>
              <td>{escape(record.plan_name)}</td>
              <td>{_badge(record.status, tone)}</td>
              <td>{escape(_format_dt(record.expires_at))}</td>
              <td>{escape(f"{record.max_devices} máy / {record.max_concurrent_sessions} phiên")}</td>
              <td>
                <form method="post" action="/admin/licenses/{escape(record.id)}/extend" class="inline-form" style="margin-bottom:8px;">
                  <input type="number" name="extra_days" min="1" value="30">
                  <input type="hidden" name="redirect_to" value="{escape(redirect_to)}">
                  <button type="submit" class="secondary">Gia hạn</button>
                </form>
                <form method="post" action="/admin/licenses/{escape(record.id)}/status" class="inline-form">
                  <select name="status">
                    {"".join(f'<option value="{option}"{" selected" if option == record.status else ""}>{option}</option>' for option in ("active", "disabled", "expired"))}
                  </select>
                  <input type="hidden" name="redirect_to" value="{escape(redirect_to)}">
                  <button type="submit" class="secondary">Cập nhật</button>
                </form>
              </td>
            </tr>
            """
        )

    device_rows = []
    for device in devices:
        status = _badge("Đang hoạt động" if device.is_active and device.revoked_at is None else "Đã thu hồi", "green" if device.is_active and device.revoked_at is None else "red")
        action = "-"
        if device.is_active and device.revoked_at is None:
            action = f"""
            <form method="post" action="/admin/devices/{escape(device.id)}/revoke" class="inline-form">
              <input type="text" name="reason" placeholder="Lý do">
              <input type="hidden" name="redirect_to" value="{escape(redirect_to)}">
              <button type="submit" class="secondary">Thu hồi</button>
            </form>
            """
        device_rows.append(
            f"""
            <tr>
              <td>{escape(device.device_label)}</td>
              <td><code>{escape(device.device_fingerprint)}</code></td>
              <td>{status}</td>
              <td>{escape(_format_dt(device.first_seen_at))}</td>
              <td>{escape(_format_dt(device.last_seen_at))}</td>
              <td>{escape(device.app_version or "-")}</td>
              <td>{action}</td>
            </tr>
            """
        )

    session_rows = []
    for session in sessions:
        status = _badge("Đang hoạt động" if session.revoked_at is None else "Đã thu hồi", "green" if session.revoked_at is None else "red")
        action = "-"
        if session.revoked_at is None:
            action = f"""
            <form method="post" action="/admin/sessions/{escape(session.id)}/revoke" class="inline-form">
              <input type="text" name="reason" placeholder="Lý do">
              <input type="hidden" name="redirect_to" value="{escape(redirect_to)}">
              <button type="submit" class="secondary">Thu hồi</button>
            </form>
            """
        session_rows.append(
            f"""
            <tr>
              <td><code>{escape(session.id)}</code></td>
              <td><code>{escape(session.device_id)}</code></td>
              <td>{status}</td>
              <td>{escape(_format_dt(session.created_at))}</td>
              <td>{escape(_format_dt(session.last_seen_at))}</td>
              <td>{escape(_format_dt(session.refresh_expires_at))}</td>
              <td>{action}</td>
            </tr>
            """
        )

    body = f"""
    <div class="detail-header">
      <div>
        <div class="eyebrow">Chi tiết khách hàng</div>
        <h2>{escape(user.username)}</h2>
        <p class="muted">Trang chi tiết này tập trung vào toàn bộ lịch sử license, thiết bị và phiên đăng nhập của một user để bạn quản lý dễ hơn thay vì xem chung trên dashboard.</p>
      </div>
      <div class="detail-actions">
        <a href="/admin">← Về dashboard</a>
      </div>
    </div>
    <div class="detail-grid">
      <div class="detail-box">
        <h4>Tổng quan nhanh</h4>
        <div class="detail-stats">
          <div class="detail-stat"><div class="label">Trạng thái user</div><div class="value">{_badge("Đang hoạt động" if user.is_active else "Đã khóa", "green" if user.is_active else "red")}</div></div>
          <div class="detail-stat"><div class="label">Vai trò</div><div class="value">{_badge("Quản trị" if user.is_admin else "Khách hàng", "blue" if user.is_admin else "amber")}</div></div>
          <div class="detail-stat"><div class="label">Thiết bị active</div><div class="value">{active_devices}</div></div>
          <div class="detail-stat"><div class="label">Phiên active</div><div class="value">{active_sessions}</div></div>
        </div>
      </div>
      <div class="detail-box">
        <h4>Thông tin tài khoản</h4>
        <div class="kv">
          <div class="muted">Username</div><div>{escape(user.username)}</div>
          <div class="muted">Ngày tạo</div><div>{escape(_format_dt(user.created_at))}</div>
          <div class="muted">License hiện tại</div><div><code>{escape(current_license.license_code if current_license else "-")}</code></div>
          <div class="muted">Gói hiện tại</div><div>{escape(current_license.plan_name if current_license else "-")}</div>
          <div class="muted">Hạn dùng</div><div>{escape(_format_dt(current_license.expires_at) if current_license else "-")}</div>
          <div class="muted">Giới hạn</div><div>{escape(f"{current_license.max_devices} máy / {current_license.max_concurrent_sessions} phiên" if current_license else "-")}</div>
        </div>
      </div>
    </div>
    <div class="layout" style="grid-template-columns: 360px minmax(0, 1fr);">
      <div class="stack">
        <div class="panel">
          <div class="eyebrow">Thao tác tài khoản</div>
          <h3>Bật / khóa user</h3>
          <form method="post" action="/admin/users/{escape(user.id)}/status">
            <label>Trạng thái mới</label>
            <input type="hidden" name="is_active" value={"false" if user.is_active else "true"}>
            <input type="hidden" name="redirect_to" value="{escape(redirect_to)}">
            <button type="submit">{'Khóa tài khoản' if user.is_active else 'Mở lại tài khoản'}</button>
          </form>
        </div>
        <div class="panel">
          <div class="eyebrow">License mới</div>
          <h3>Cấp thêm license cho user này</h3>
          <form method="post" action="/admin/licenses/issue">
            <input type="hidden" name="user_id" value="{escape(user.id)}">
            <input type="hidden" name="redirect_to" value="{escape(redirect_to)}">
            <label>Gói dịch vụ</label>
            <input type="text" name="plan_name" value="standard" required>
            <label>Số ngày</label>
            <input type="number" name="days" min="1" value="30" required>
            <label>Số máy tối đa</label>
            <input type="number" name="max_devices" min="1" value="1" required>
            <label>Số phiên đồng thời</label>
            <input type="number" name="max_concurrent_sessions" min="1" value="1" required>
            <label>Ghi chú</label>
            <textarea name="notes" placeholder="Ví dụ: gia hạn tháng tiếp theo"></textarea>
            <button type="submit">Cấp license mới</button>
          </form>
        </div>
      </div>
      <div class="section-grid">
        <div class="panel">
          <div class="eyebrow">Lịch sử license</div>
          <h3>Toàn bộ licenses</h3>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Code</th>
                  <th>Gói</th>
                  <th>Trạng thái</th>
                  <th>Hết hạn</th>
                  <th>Giới hạn</th>
                  <th>Thao tác</th>
                </tr>
              </thead>
              <tbody>
                {"".join(license_rows) or '<tr><td colspan="6" class="muted">User này chưa có license.</td></tr>'}
              </tbody>
            </table>
          </div>
        </div>
        <div class="panel">
          <div class="eyebrow">Thiết bị đã bind</div>
          <h3>Devices</h3>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Label</th>
                  <th>Fingerprint</th>
                  <th>Trạng thái</th>
                  <th>Lần đầu thấy</th>
                  <th>Lần cuối thấy</th>
                  <th>Phiên bản app</th>
                  <th>Thao tác</th>
                </tr>
              </thead>
              <tbody>
                {"".join(device_rows) or '<tr><td colspan="7" class="muted">User này chưa có thiết bị nào.</td></tr>'}
              </tbody>
            </table>
          </div>
        </div>
        <div class="panel">
          <div class="eyebrow">Phiên đăng nhập</div>
          <h3>Sessions</h3>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Session ID</th>
                  <th>Device ID</th>
                  <th>Trạng thái</th>
                  <th>Tạo lúc</th>
                  <th>Last seen</th>
                  <th>Refresh hết hạn</th>
                  <th>Thao tác</th>
                </tr>
              </thead>
              <tbody>
                {"".join(session_rows) or '<tr><td colspan="7" class="muted">User này chưa có session nào.</td></tr>'}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
    """
    return _page(f"Chi tiết user {user.username}", body, current_admin=current_admin, message=message, error=error)


@router.get("/", response_class=RedirectResponse)
def home() -> RedirectResponse:
    return RedirectResponse("/admin", status_code=303)


@router.get("/admin/login", response_class=HTMLResponse, response_model=None)
def admin_login_page(request: Request, db: Session = Depends(get_db), config: LicenseServerConfig = Depends(get_config)):
    if _load_admin_user(request, db, config) is not None:
        return RedirectResponse("/admin", status_code=303)
    return _login_page(
        message=str(request.query_params.get("message") or ""),
        error=str(request.query_params.get("error") or ""),
    )


@router.post("/admin/login")
async def admin_login(request: Request, db: Session = Depends(get_db), config: LicenseServerConfig = Depends(get_config)) -> RedirectResponse:
    form = await request.form()
    username = str(form.get("username") or "").strip()
    password = str(form.get("password") or "")
    service = AdminService(db, config)
    try:
        user = service.authenticate_admin(username=username, password=password)
    except AuthError as exc:
        return _flash_redirect("/admin/login", error=str(exc))
    expires_at = utcnow() + timedelta(hours=config.admin_session_ttl_hours)
    token = sign_admin_session(
        config,
        user_id=user.id,
        username=user.username,
        is_admin=user.is_admin,
        expires_at=expires_at,
    )
    response = _flash_redirect("/admin", message="Đăng nhập thành công.")
    response.set_cookie(
        key=config.admin_session_cookie_name,
        value=token,
        httponly=True,
        secure=_is_secure_request(request),
        samesite="lax",
        max_age=config.admin_session_ttl_hours * 3600,
    )
    return response


@router.post("/admin/logout")
def admin_logout(config: LicenseServerConfig = Depends(get_config)) -> RedirectResponse:
    response = _flash_redirect("/admin/login", message="Đã đăng xuất.")
    response.delete_cookie(config.admin_session_cookie_name)
    return response


@router.get("/admin", response_class=HTMLResponse, response_model=None)
def admin_dashboard(request: Request, db: Session = Depends(get_db), config: LicenseServerConfig = Depends(get_config)):
    current_admin, redirect = _require_admin_user(request, db, config)
    if redirect is not None:
        redirect.delete_cookie(config.admin_session_cookie_name)
        return redirect

    service = AdminService(db, config)
    all_users = service.list_users()

    filters = {
        "search": str(request.query_params.get("search") or "").strip(),
        "account_status": str(request.query_params.get("account_status") or "all").strip().lower(),
        "role": str(request.query_params.get("role") or "all").strip().lower(),
        "license_status": str(request.query_params.get("license_status") or "all").strip().lower(),
        "expires_in_days": str(request.query_params.get("expires_in_days") or "all").strip().lower(),
        "sort": str(request.query_params.get("sort") or "created_at").strip().lower(),
        "direction": str(request.query_params.get("direction") or "desc").strip().lower(),
        "users_page": max(1, int(str(request.query_params.get("users_page") or "1"))),
        "licenses_page": max(1, int(str(request.query_params.get("licenses_page") or "1"))),
        "page_size": max(1, min(100, int(str(request.query_params.get("page_size") or "10")))),
    }

    search_lower = filters["search"].lower()
    expires_limit = None
    if filters["expires_in_days"] in {"7", "30", "90"}:
        expires_limit = utcnow() + timedelta(days=int(filters["expires_in_days"]))

    filtered_users = []
    for user in all_users:
        current_license = _latest_license(user)
        license_status = current_license.status if current_license else "none"
        if search_lower:
            haystack = " ".join(
                value for value in (
                    user.username,
                    current_license.license_code if current_license else "",
                    current_license.plan_name if current_license else "",
                ) if value
            ).lower()
            if search_lower not in haystack:
                continue
        if filters["account_status"] == "active" and not user.is_active:
            continue
        if filters["account_status"] == "disabled" and user.is_active:
            continue
        if filters["role"] == "admin" and not user.is_admin:
            continue
        if filters["role"] == "user" and user.is_admin:
            continue
        if filters["license_status"] != "all" and license_status != filters["license_status"]:
            continue
        if expires_limit is not None:
            if current_license is None or _as_utc(current_license.expires_at) > expires_limit:
                continue
        filtered_users.append(user)

    reverse = filters["direction"] != "asc"
    if filters["sort"] == "username":
        filtered_users.sort(key=lambda user: user.username.lower(), reverse=reverse)
    elif filters["sort"] == "expires_at":
        filtered_users.sort(
            key=lambda user: (_as_utc(_latest_license(user).expires_at) if _latest_license(user) else utcnow() + timedelta(days=99999)),
            reverse=reverse,
        )
    elif filters["sort"] == "plan_name":
        filtered_users.sort(key=lambda user: (_latest_license(user).plan_name.lower() if _latest_license(user) else ""), reverse=reverse)
    else:
        filtered_users.sort(key=lambda user: user.created_at, reverse=reverse)

    visible_user_ids = {user.id for user in filtered_users}
    filtered_licenses = []
    for record in service.list_licenses():
        if record.user_id not in visible_user_ids:
            continue
        if search_lower:
            haystack = " ".join((record.license_code, record.plan_name, record.user.username)).lower()
            if search_lower not in haystack:
                continue
        filtered_licenses.append(record)

    users_pagination = _pagination(len(filtered_users), filters["users_page"], filters["page_size"])
    licenses_pagination = _pagination(len(filtered_licenses), filters["licenses_page"], filters["page_size"])
    users_page_items = filtered_users[users_pagination["start"]:users_pagination["end"]]
    licenses_page_items = filtered_licenses[licenses_pagination["start"]:licenses_pagination["end"]]

    return _render_dashboard(
        current_admin=current_admin,
        all_users=all_users,
        users=users_page_items,
        licenses=licenses_page_items,
        filters=filters,
        users_pagination=users_pagination,
        licenses_pagination=licenses_pagination,
        message=str(request.query_params.get("message") or ""),
        error=str(request.query_params.get("error") or ""),
    )


@router.get("/admin/users/{user_id}", response_class=HTMLResponse, response_model=None)
def admin_user_detail(user_id: str, request: Request, db: Session = Depends(get_db), config: LicenseServerConfig = Depends(get_config)):
    current_admin, redirect = _require_admin_user(request, db, config)
    if redirect is not None:
        redirect.delete_cookie(config.admin_session_cookie_name)
        return redirect

    service = AdminService(db, config)
    try:
        user = service.license_service.get_user(user_id)
    except LicenseError:
        return _flash_redirect("/admin", error="Không tìm thấy user cần xem.")

    devices = service.list_devices(user_id)
    sessions = service.list_sessions(user_id)
    redirect_to = f"/admin/users/{user_id}"
    return _render_user_detail(
        current_admin=current_admin,
        user=user,
        devices=devices,
        sessions=sessions,
        redirect_to=redirect_to,
        message=str(request.query_params.get("message") or ""),
        error=str(request.query_params.get("error") or ""),
    )


@router.post("/admin/users/create")
async def admin_create_user(request: Request, db: Session = Depends(get_db), config: LicenseServerConfig = Depends(get_config)) -> RedirectResponse:
    current_admin, redirect = _require_admin_user(request, db, config)
    if redirect is not None:
        return redirect
    form = await request.form()
    redirect_to = _safe_redirect_target(form.get("redirect_to"))
    service = AdminService(db, config)
    try:
        service.create_user(
            username=str(form.get("username") or "").strip(),
            password=str(form.get("password") or ""),
            is_admin=str(form.get("is_admin") or "").lower() in ("1", "true", "on", "yes"),
        )
    except (LicenseError, AuthError) as exc:
        return _redirect_with_flash(redirect_to, error=str(exc))
    return _redirect_with_flash(redirect_to, message="Đã tạo user mới.")


@router.post("/admin/users/{user_id}/status")
async def admin_set_user_status(request: Request, user_id: str, db: Session = Depends(get_db), config: LicenseServerConfig = Depends(get_config)) -> RedirectResponse:
    current_admin, redirect = _require_admin_user(request, db, config)
    if redirect is not None:
        return redirect
    form = await request.form()
    redirect_to = _safe_redirect_target(form.get("redirect_to"), f"/admin/users/{user_id}")
    is_active = str(form.get("is_active") or "").lower() in ("1", "true", "on", "yes")
    service = AdminService(db, config)
    try:
        service.set_user_status(user_id=user_id, is_active=is_active)
    except LicenseError as exc:
        return _redirect_with_flash(redirect_to, error=str(exc))
    return _redirect_with_flash(redirect_to, message="Đã cập nhật trạng thái user.")


@router.post("/admin/licenses/issue")
async def admin_issue_license(request: Request, db: Session = Depends(get_db), config: LicenseServerConfig = Depends(get_config)) -> RedirectResponse:
    current_admin, redirect = _require_admin_user(request, db, config)
    if redirect is not None:
        return redirect
    form = await request.form()
    redirect_to = _safe_redirect_target(form.get("redirect_to"))
    service = AdminService(db, config)
    try:
        service.issue_license(
            user_id=str(form.get("user_id") or "").strip(),
            plan_name=str(form.get("plan_name") or "standard").strip() or "standard",
            days=int(str(form.get("days") or "30")),
            max_devices=int(str(form.get("max_devices") or "1")),
            max_concurrent_sessions=int(str(form.get("max_concurrent_sessions") or "1")),
            notes=str(form.get("notes") or "").strip() or None,
        )
    except (LicenseError, ValueError) as exc:
        return _redirect_with_flash(redirect_to, error=str(exc))
    return _redirect_with_flash(redirect_to, message="Đã cấp license mới.")


@router.post("/admin/licenses/{license_id}/extend")
async def admin_extend_license(request: Request, license_id: str, db: Session = Depends(get_db), config: LicenseServerConfig = Depends(get_config)) -> RedirectResponse:
    current_admin, redirect = _require_admin_user(request, db, config)
    if redirect is not None:
        return redirect
    form = await request.form()
    redirect_to = _safe_redirect_target(form.get("redirect_to"))
    service = AdminService(db, config)
    try:
        service.extend_license(license_id=license_id, extra_days=int(str(form.get("extra_days") or "30")))
    except (LicenseError, ValueError) as exc:
        return _redirect_with_flash(redirect_to, error=str(exc))
    return _redirect_with_flash(redirect_to, message="Đã gia hạn license.")


@router.post("/admin/licenses/{license_id}/status")
async def admin_set_license_status(request: Request, license_id: str, db: Session = Depends(get_db), config: LicenseServerConfig = Depends(get_config)) -> RedirectResponse:
    current_admin, redirect = _require_admin_user(request, db, config)
    if redirect is not None:
        return redirect
    form = await request.form()
    redirect_to = _safe_redirect_target(form.get("redirect_to"))
    service = AdminService(db, config)
    try:
        service.set_license_status(license_id=license_id, status=str(form.get("status") or "disabled"))
    except LicenseError as exc:
        return _redirect_with_flash(redirect_to, error=str(exc))
    return _redirect_with_flash(redirect_to, message="Đã cập nhật trạng thái license.")


@router.post("/admin/devices/{device_id}/revoke")
async def admin_revoke_device(request: Request, device_id: str, db: Session = Depends(get_db), config: LicenseServerConfig = Depends(get_config)) -> RedirectResponse:
    current_admin, redirect = _require_admin_user(request, db, config)
    if redirect is not None:
        return redirect
    form = await request.form()
    redirect_to = _safe_redirect_target(form.get("redirect_to"))
    service = AdminService(db, config)
    try:
        service.revoke_device(device_id=device_id, reason=str(form.get("reason") or "").strip() or None)
    except LicenseError as exc:
        return _redirect_with_flash(redirect_to, error=str(exc))
    return _redirect_with_flash(redirect_to, message="Đã thu hồi thiết bị.")


@router.post("/admin/sessions/{session_id}/revoke")
async def admin_revoke_session(request: Request, session_id: str, db: Session = Depends(get_db), config: LicenseServerConfig = Depends(get_config)) -> RedirectResponse:
    current_admin, redirect = _require_admin_user(request, db, config)
    if redirect is not None:
        return redirect
    form = await request.form()
    redirect_to = _safe_redirect_target(form.get("redirect_to"))
    service = AdminService(db, config)
    try:
        service.revoke_session(session_id=session_id, reason=str(form.get("reason") or "").strip() or None)
    except LicenseError as exc:
        return _redirect_with_flash(redirect_to, error=str(exc))
    return _redirect_with_flash(redirect_to, message="Đã thu hồi session.")
