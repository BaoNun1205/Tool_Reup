"""Runtime configuration for the MVP pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import shutil
import time
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[2]
VENV_SCRIPTS = PROJECT_ROOT / ".venv" / "Scripts"


def _find_project_binary(executable_name):
    candidate = VENV_SCRIPTS / executable_name
    if candidate.exists():
        return str(candidate)
    return None


def _find_winget_binary(patterns, executable_name):
    local_app_data = os.getenv("LOCALAPPDATA")
    if not local_app_data:
        return None
    base = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
    if not base.exists():
        return None
    matches = []
    for pattern in patterns:
        matches.extend(sorted(base.glob(pattern)))
    for package_dir in reversed(matches):
        candidate = package_dir / executable_name
        if candidate.exists():
            return str(candidate)
        nested_matches = list(package_dir.rglob(executable_name))
        if nested_matches:
            return str(nested_matches[0])
    return None


def _find_android_sdk_adb():
    local_app_data = os.getenv("LOCALAPPDATA")
    user_profile = os.getenv("USERPROFILE")
    candidates = []
    if local_app_data:
        candidates.append(Path(local_app_data) / "Android" / "Sdk" / "platform-tools" / "adb.exe")
    if user_profile:
        candidates.append(Path(user_profile) / "AppData" / "Local" / "Android" / "Sdk" / "platform-tools" / "adb.exe")
    candidates.extend(
        [
            Path("C:/Android/platform-tools/adb.exe"),
            Path("C:/platform-tools/adb.exe"),
            Path("D:/platform-tools/adb.exe"),
            Path("D:/adb/adb.exe"),
        ]
    )
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def _resolve_tool(env_var, default_name, project_executable, winget_patterns, executable_name):
    configured = os.getenv(env_var)
    if configured:
        return configured
    project_binary = _find_project_binary(project_executable)
    if project_binary:
        return project_binary
    discovered = shutil.which(default_name)
    if discovered:
        return discovered
    winget_binary = _find_winget_binary(winget_patterns, executable_name)
    if winget_binary:
        return winget_binary
    return default_name


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off"):
        return False
    return default


@dataclass(frozen=True)
class PipelineConfig:
    """Toan bo thong so runtime cua pipeline.

    Nen uu tien chinh trong file nay khi muon doi hanh vi render, shuffle,
    crop, blur hoac duong dan tool.
    """

    # Duong dan cac tool. Co the override bang bien moi truong neu file .exe
    # nam ngoai venv hoac ngoai PATH.
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"
    ytdlp_bin: str = "yt-dlp"
    lazy_down_bin: str = "lazy-down"
    adb_bin: str = "adb"

    # Neu True thi TikTok se luon tai qua lazy-down.
    download_via_lazy_down_only: bool = True

    # Cau hinh session va output.
    # Thu muc goc de tao thu muc output cho tung session.
    default_output_root: Path = PROJECT_ROOT / "output"
    # So item toi da trong giao dien batch.
    max_session_items: int = 20
    # So item co the render song song trong cung mot session.
    max_parallel_session_items: int = 2
    # Tan suat UI cap nhat progress/log.
    ui_poll_interval_ms: int = 120

    # Cau hinh video dau ra cuoi cung.
    # Kich thuoc canvas doc. Video final luon render ve dung kich thuoc nay.
    target_width: int = 1080
    target_height: int = 1920
    # FPS dich cho normalize va video final.
    target_fps: int = 30
    # Sample rate AAC cho audio tam va audio final.
    target_sample_rate: int = 48000
    # Chat luong H.264. CRF cang thap thi cang net nhung file cang nang.
    video_crf: int = 18

    # Tien xu ly truoc khi cat chunk va xao.
    # So dB tang them cho audio truoc limiter va speed-up.
    preprocess_audio_gain_db: float = 4.0
    # Toc do tang toan cuc ap dung truoc khi tach audio va cat chunk.
    speed_factor: float = 1.2

    # Cat chunk va bao toan muc tieu shuffle.
    # Do dai chunk co dinh thay cho tach canh tu nhien trong flow hien tai.
    # Neu muon moi doan deu duoi 3 giay thi giu gia tri nay < 3.0.
    fixed_chunk_duration_seconds: float = 2.27
    # Nguong tach canh tu nhien kieu cu. Hien tai gan nhu khong con tac dung
    # chinh vi app dang uu tien fixed chunk, nhung van giu lai de tuong thich.
    scene_threshold: float = 0.35
    # Do dai toi thieu cua mot doan nho khi planner phai chia tiep.
    min_scene_duration: float = 0.9
    # Do dai toi da cua mot doan sau khi planner chia nho.
    max_scene_duration: float = 2.95
    # Loc bo cac doan qua den / it gia tri.
    blackdetect_duration: float = 0.4
    blackdetect_threshold: float = 0.98

    # Nhom thong so overlay kieu cu dang floating-card.
    # Hien khong phai layout chinh nua, nhung van giu lai de tuong thich.
    overlay_margin: int = 48
    png_overlay_width_ratio: float = 0.28
    jpg_overlay_width_ratio: float = 0.26
    overlay_max_height_ratio: float = 0.26

    # Nhom thong so layout split chinh.
    # Chieu cao phan anh ro o ben duoi. Tang len se hien nhieu anh hon.
    split_bottom_panel_ratio: float = 0.3921
    # Chieu cao vung mo mem giao nhau giua video va anh.
    # Tang len de vung overlap dai hon.
    split_separator_height_ratio: float = 0.2521
    # Rut gon rieng dai fade cua vung mo de overlap gon hon ma van giu
    # cung huong mo dan tu duoi len tren.
    split_separator_fade_trim_pixels: int = 100
    # Do day alpha toi da trong vung mo.
    # Gia tri cang thap thi vung overlap cang trong.
    split_separator_max_alpha_ratio: float = 0.40
    # Phan tram chieu cao danh cho dai fade. Phan con lai o ben duoi se giu
    # muc mo dam hon truoc khi fade dan len 0 o phia tren.
    split_separator_fade_ratio: float = 0.50
    # Muc zoom ap dung cho video normalize.
    split_zoom_factor: float = 1.0
    # Sau khi crop anh san pham ve 1:1, se scale theo ti le nay truoc khi
    # cat mep tren/duoi va dat sat day.
    split_image_scale_factor: float = 1.1
    # Muc zoom lon nhat cua anh san pham trong split layout.
    split_image_zoom_peak_factor: float = 1.2
    # Chu ky phong/thu cua anh san pham de tao nhiep zoom mem va deu.
    split_image_zoom_cycle_seconds: float = 6.0
    # Cat bo mep tren cua anh san pham truoc khi de len video.
    split_image_trim_top_ratio: float = 0.10
    # Cat chinh xac theo pixel o mep tren cua anh trong split layout.
    split_image_trim_top_pixels: int = 50
    # Cat chinh xac theo pixel o mep duoi cua anh trong split layout.
    split_image_trim_bottom_pixels: int = 50

    # Can chinh khung video nguon truoc speed va truoc khi cat chunk.
    # So pixel cat o phia tren.
    split_video_trim_top_pixels: int = 120
    # So pixel cat o phia duoi.
    split_video_trim_bottom_pixels: int = 360
    # Ban ratio cua hai thong so crop ben tren.
    # Chu yeu de tuong thich code cu va de test/thu nghiem.
    split_video_trim_top_ratio: float = 0.0625
    split_video_trim_bottom_ratio: float = 0.1875
    # Thong so day/keo video theo ti le kieu cu.
    # Flow normalize hien tai chu yeu dua vao crop theo pixel ben tren.
    split_video_vertical_offset_ratio: float = 0.00
    # Mau nen phia sau anh JPG hoac PNG bi flatten trong phan anh duoi.
    split_image_background_color: str = "#FFF4C2"

    # Gui sang dien thoai.
    # Serial adb co dinh neu muon khoa 1 thiet bi cu the.
    # De trong thi dung thiet bi dang duoc chon / dang ket noi.
    android_device_serial: str = ""
    # Thu muc dich tren Android khi bam "Send To Phone".
    android_device_video_dir: str = "/sdcard/Movies/AutoTikTokEditor"

    # Kiem tra cookie cua flow yt-dlp kieu cu.
    # Hien tai gan nhu khong quan trong khi lazy-down-only dang bat, nhung
    # van giu lai vi code fallback cu van ho tro.
    browser_cookie_freshness_seconds: int = 1800
    # User-Agent trinh duyet dung cho resolve shortlink TikTok va flow cu.
    tiktok_web_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/135.0.0.0 Safari/537.36"
    )

    # Hoan thien audio.
    # Muc loudness muc tieu cho audio da xu ly va audio final.
    audio_target_lufs: float = -14.0
    # Muc tran true peak cho limiter.
    audio_true_peak: float = -1.0
    # Fade rat ngan o dau/cuoi tung clip de giam click/pop.
    temp_audio_fade_seconds: float = 0.04

    @classmethod
    def from_env(cls):
        configured_output_root = os.getenv("AUTO_EDITOR_OUTPUT_ROOT")
        output_root = Path(configured_output_root).expanduser().resolve() if configured_output_root else PROJECT_ROOT / "output"
        return cls(
            ffmpeg_bin=_resolve_tool(
                "AUTO_EDITOR_FFMPEG_BIN",
                "ffmpeg",
                "ffmpeg.exe",
                ["Gyan.FFmpeg*", "yt-dlp.FFmpeg*"],
                "ffmpeg.exe",
            ),
            ffprobe_bin=_resolve_tool(
                "AUTO_EDITOR_FFPROBE_BIN",
                "ffprobe",
                "ffprobe.exe",
                ["Gyan.FFmpeg*", "yt-dlp.FFmpeg*"],
                "ffprobe.exe",
            ),
            ytdlp_bin=_resolve_tool(
                "AUTO_EDITOR_YTDLP_BIN",
                "yt-dlp",
                "yt-dlp.exe",
                ["yt-dlp.yt-dlp*"],
                "yt-dlp.exe",
            ),
            lazy_down_bin=_resolve_tool(
                "AUTO_EDITOR_LAZY_DOWN_BIN",
                "lazy-down",
                "lazy-down.cmd",
                [],
                "lazy-down.cmd",
            ),
            adb_bin=(
                os.getenv("AUTO_EDITOR_ADB_BIN")
                or _find_project_binary("adb.exe")
                or shutil.which("adb")
                or _find_android_sdk_adb()
                or _find_winget_binary(["Google.PlatformTools*"], "adb.exe")
                or "adb"
            ),
            download_via_lazy_down_only=_env_flag("AUTO_EDITOR_LAZY_DOWN_ONLY", True),
            default_output_root=output_root,
            android_device_serial=os.getenv("AUTO_EDITOR_ANDROID_DEVICE_SERIAL", "").strip(),
            android_device_video_dir=os.getenv("AUTO_EDITOR_ANDROID_VIDEO_DIR", "/sdcard/Movies/AutoTikTokEditor").strip()
            or "/sdcard/Movies/AutoTikTokEditor",
        )

    def build_job_id(self):
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        return "%s_%s" % (timestamp, uuid.uuid4().hex[:8])

    def build_session_id(self):
        timestamp = time.strftime("session_%Y%m%d_%H%M%S")
        return "%s_%s" % (timestamp, uuid.uuid4().hex[:6])
