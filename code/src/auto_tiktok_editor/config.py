"""Runtime configuration for the MVP pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import shutil
import sys
import time
import uuid

from auto_tiktok_editor.telegram_settings import load_telegram_runtime_settings


def _runtime_root() -> Path | None:
    if getattr(sys, "frozen", False) or globals().get("__compiled__", False):
        return Path(sys.executable).resolve().parent
    return None


def _resolve_project_root() -> Path:
    configured = os.getenv("AUTO_EDITOR_PROJECT_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()

    runtime_root = _runtime_root()
    if runtime_root is not None:
        for candidate in (runtime_root, *runtime_root.parents):
            if (candidate / "tiktok_profile_manager.sqlite3").exists() or (candidate / "telegram_bots.json").exists():
                return candidate
        return runtime_root

    return Path(__file__).resolve().parents[2]


PROJECT_ROOT = _resolve_project_root()
VENV_SCRIPTS = PROJECT_ROOT / ".venv" / "Scripts"
TELEGRAM_BOT_TOKEN_FILE = PROJECT_ROOT / "telegram_bot_token.txt"
VENDOR_TOOLS = PROJECT_ROOT / "vendor" / "tools"


def _find_runtime_binary(relative_path: str):
    runtime_root = _runtime_root()
    if runtime_root is None:
        return None
    candidate = runtime_root / relative_path
    if candidate.exists():
        return str(candidate)
    return None


def _find_project_binary(executable_name):
    candidate = VENV_SCRIPTS / executable_name
    if candidate.exists():
        return str(candidate)
    vendor_candidate = VENDOR_TOOLS / executable_name
    if vendor_candidate.exists():
        return str(vendor_candidate)
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
            Path("C:/adb/adb.exe"),
            Path("D:/platform-tools/adb.exe"),
            Path("D:/adb/adb.exe"),
        ]
    )
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def _find_scrcpy_binary():
    candidates = [
        Path("D:/scrcpy/scrcpy.exe"),
        Path("D:/scrcpy-win64/scrcpy.exe"),
        Path("D:/tools/scrcpy/scrcpy.exe"),
        Path("C:/scrcpy/scrcpy.exe"),
    ]
    candidates.extend(sorted(Path("D:/").glob("scrcpy-win64-v*/scrcpy.exe"), reverse=True))
    candidates.extend(sorted(Path("C:/").glob("scrcpy-win64-v*/scrcpy.exe"), reverse=True))
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def _resolve_tool(env_var, default_name, project_executable, winget_patterns, executable_name):
    configured = os.getenv(env_var)
    if configured:
        return configured
    runtime_binary = _find_runtime_binary("tools/%s" % executable_name)
    if runtime_binary:
        return runtime_binary
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


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value.strip())
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value.strip())
    except (TypeError, ValueError):
        return default


def _normalize_video_cut_mode(value: str) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"fixed", "scene", "original", "remove_background"} else "fixed"


def _normalize_product_image_crop_ratio(value: str) -> str:
    normalized = str(value or "").strip().lower().replace("x", ":")
    return normalized if normalized in {"1:1", "4:3"} else "1:1"


def _normalize_product_image_motion(value: str) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"still", "zoom"} else "still"


def _normalize_backgroundremover_model(value: str) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"u2net", "u2net_human_seg", "u2netp"} else "u2netp"


def _normalize_background_removal_backend(value: str) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    return normalized if normalized in {"rembg", "backgroundremover"} else "rembg"


def _normalize_rembg_model(value: str) -> str:
    normalized = str(value or "").strip().lower().replace("_", "-")
    allowed = {
        "u2net",
        "u2netp",
        "u2net-human-seg",
        "u2net-cloth-seg",
        "silueta",
        "isnet-general-use",
        "isnet-anime",
        "birefnet-general",
    }
    if normalized == "u2net_human_seg":
        normalized = "u2net-human-seg"
    if normalized == "u2net_cloth_seg":
        normalized = "u2net-cloth-seg"
    return normalized if normalized in allowed else "isnet-general-use"


def _normalize_rembg_providers(value) -> tuple:
    if isinstance(value, (tuple, list)):
        chunks = value
    else:
        chunks = str(value or "").split(",")
    providers = []
    mapping = {
        "directml": "DmlExecutionProvider",
        "dml": "DmlExecutionProvider",
        "dmlexecutionprovider": "DmlExecutionProvider",
        "cpu": "CPUExecutionProvider",
        "cpuexecutionprovider": "CPUExecutionProvider",
        "cuda": "CUDAExecutionProvider",
        "cudaexecutionprovider": "CUDAExecutionProvider",
        "rocm": "ROCMExecutionProvider",
        "rocmexecutionprovider": "ROCMExecutionProvider",
    }
    for chunk in chunks:
        raw = str(chunk or "").strip()
        if not raw:
            continue
        normalized = raw.lower().replace("_", "").replace("-", "")
        provider = mapping.get(normalized, raw)
        if provider not in providers:
            providers.append(provider)
    if not providers:
        providers = ["DmlExecutionProvider", "CPUExecutionProvider"]
    if "CPUExecutionProvider" not in providers:
        providers.append("CPUExecutionProvider")
    return tuple(providers)


def _env_chat_ids(name: str):
    value = os.getenv(name)
    if value is None:
        return ()
    chat_ids = []
    for chunk in value.split(","):
        normalized = chunk.strip()
        if not normalized:
            continue
        try:
            chat_ids.append(int(normalized))
        except ValueError:
            continue
    return tuple(chat_ids)


def _read_telegram_bot_token_file() -> str:
    try:
        if TELEGRAM_BOT_TOKEN_FILE.exists():
            return TELEGRAM_BOT_TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return ""


def _resolve_telegram_bot_token() -> str:
    return os.getenv("AUTO_EDITOR_TELEGRAM_BOT_TOKEN", "").strip() or _read_telegram_bot_token_file()


def _runtime_is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False) or globals().get("__compiled__", False))


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
    realesrgan_bin: str = "realesrgan-ncnn-vulkan"
    background_removal_backend: str = "rembg"
    backgroundremover_bin: str = "backgroundremover"
    backgroundremover_model: str = "u2netp"
    rembg_model: str = "isnet-general-use"
    rembg_providers: tuple = ("DmlExecutionProvider", "CPUExecutionProvider")
    rembg_post_process_mask: bool = False
    rembg_mask_expand_pixels: int = 3
    adb_bin: str = "adb"
    scrcpy_bin: str = "scrcpy"
    tikwm_api_url: str = "https://www.tikwm.com/api/"

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
    # Thong so encode cuoi cung toi uu cho upload TikTok.
    final_video_bitrate: str = "16M"
    final_video_maxrate: str = "20M"
    final_video_bufsize: str = "32M"
    final_audio_bitrate: str = "192k"

    # Tien xu ly truoc khi cat chunk va xao.
    # So dB tang them cho audio truoc limiter va speed-up.
    preprocess_audio_gain_db: float = 4.0
    # Chinh mau video truoc speed-up va truoc khi cat chunk.
    preprocess_video_brightness_adjustment: float = 0.02
    preprocess_video_contrast_adjustment: float = -0.01
    preprocess_video_saturation_adjustment: float = 0.03
    # Lam net nhe video bang FFmpeg CAS trong cung buoc normalize.
    preprocess_video_sharpen_strength: float = 0.12
    # Toc do tang toan cuc ap dung truoc khi tach audio va cat chunk.
    speed_factor: float = 1.2

    # Cat chunk va bao toan muc tieu shuffle.
    # Mode "fixed" cat chunk co dinh; mode "scene" cat theo diem chuyen canh;
    # mode "original" bo qua buoc cat va xao tron, cac buoc con lai van chay.
    # mode "remove_background" bo qua cat/xao tron, xoa nen video va dung anh
    # san pham 1:1 phong cover thanh nen 9:16.
    video_cut_mode: str = "fixed"
    # Do dai chunk co dinh. Neu muon moi doan deu duoi 3 giay thi giu gia tri nay < 3.0.
    fixed_chunk_duration_seconds: float = 2.27
    # Nguong tach canh tu nhien khi video_cut_mode la "scene".
    scene_threshold: float = 0.35
    # Do dai toi thieu cua mot doan nho khi planner phai chia tiep.
    min_scene_duration: float = 0.9
    # Do dai toi da cua mot doan sau khi planner chia nho.
    max_scene_duration: float = 2.95
    # Gioi han thoi gian lap ke hoach shuffle cho tung video.
    edit_planner_timeout_seconds: float = 300.0
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
    split_zoom_factor: float = 1.03
    # Muc zoom ban dau sau khi crop anh san pham theo ti le da chon.
    split_image_scale_factor: float = 1.0
    # Ti le crop anh san pham truoc khi ghep vao panel duoi.
    product_image_crop_ratio: str = "1:1"
    # "still" giu anh dung yen; "zoom" dung hieu ung phong/thu hien tai.
    product_image_motion: str = "still"
    # Muc zoom cao nhat trong chu ky phong/thu.
    split_image_zoom_peak_factor: float = 1.14
    # Chu ky phong/thu cua anh san pham de tao nhip zoom mem va deu.
    split_image_zoom_cycle_seconds: float = 6.0
    # Bien do troi ngang/doc cho hieu ung floating parallax loop.
    split_image_motion_cycle_seconds: float = 5.0
    split_image_horizontal_float_ratio: float = 0.052
    split_image_vertical_float_ratio: float = 0.038
    # Cac thong so trim cu, hien khong ap dung cho crop 1:1.
    split_image_trim_top_ratio: float = 0.10
    split_image_trim_top_pixels: int = 0
    split_image_trim_bottom_pixels: int = 0

    # Can chinh khung video nguon truoc speed va truoc khi cat chunk.
    # So pixel cat o phia tren.
    split_video_trim_top_pixels: int = 200
    # So pixel cat o phia duoi.
    split_video_trim_bottom_pixels: int = 280
    # Ban ratio cua hai thong so crop ben tren.
    # Chu yeu de tuong thich code cu va de test/thu nghiem.
    split_video_trim_top_ratio: float = 0.1042
    split_video_trim_bottom_ratio: float = 0.1458
    # Thong so day/keo video theo ti le kieu cu.
    # Flow normalize hien tai chu yeu dua vao crop theo pixel ben tren.
    split_video_vertical_offset_ratio: float = 0.00
    # Mau nen phia sau anh JPG hoac PNG bi flatten trong phan anh duoi.
    split_image_background_color: str = "#FFF4C2"

    # Lam net anh san pham sau khi crop ve khung 1:1.
    product_image_enhance_enabled: bool = True
    product_image_enhance_required: bool = False
    product_image_enhance_scale: int = 4
    product_image_enhance_model: str = "realesrgan-x4plus"

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

    # Cau hinh Telegram bot polling.
    allow_local_telegram: bool = False
    require_frozen_build: bool = False
    runtime_is_frozen: bool = False
    telegram_bot_token: str = ""
    telegram_poll_timeout_seconds: int = 30
    telegram_poll_interval_seconds: int = 2
    telegram_input_root: Path = PROJECT_ROOT / "output" / "_telegram_inputs"
    telegram_allowed_chat_ids: tuple = ()
    telegram_delivery_chat_id: str = ""
    telegram_send_result_to_telegram: bool = False
    telegram_save_received_video_to_profile: bool = True
    telegram_cleanup_after_job_enabled: bool = False
    telegram_auto_cleanup_enabled: bool = True
    telegram_cleanup_interval_seconds: int = 21600
    telegram_cleanup_max_age_seconds: int = 86400
    # Profile slug dung de dua video Telegram da render vao TikTok Profile Manager.
    # Multi-bot se set gia tri nay bang name trong telegram_bots.json.
    tiktok_profile_slug: str = ""

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
        telegram_runtime_settings = load_telegram_runtime_settings()
        env_allow_local_telegram = _env_flag("AUTO_EDITOR_ALLOW_LOCAL_TELEGRAM", False)
        resolved_telegram_bot_token = _resolve_telegram_bot_token() or telegram_runtime_settings.bot_token
        resolved_telegram_delivery_chat_id = (
            os.getenv("AUTO_EDITOR_TELEGRAM_DELIVERY_CHAT_ID", "").strip()
            or telegram_runtime_settings.delivery_chat_id
        )
        video_cut_mode = _normalize_video_cut_mode(
            os.getenv("AUTO_EDITOR_VIDEO_CUT_MODE", "").strip()
            or telegram_runtime_settings.video_cut_mode
            or "fixed"
        )
        fixed_chunk_duration_seconds = max(
            0.5,
            _env_float(
                "AUTO_EDITOR_FIXED_CHUNK_DURATION_SECONDS",
                telegram_runtime_settings.fixed_chunk_duration_seconds,
            ),
        )
        scene_threshold = max(
            0.01,
            min(
                0.95,
                _env_float("AUTO_EDITOR_SCENE_THRESHOLD", telegram_runtime_settings.scene_threshold),
            ),
        )
        product_image_crop_ratio = _normalize_product_image_crop_ratio(
            os.getenv("AUTO_EDITOR_PRODUCT_IMAGE_CROP_RATIO")
            or telegram_runtime_settings.product_image_crop_ratio
        )
        product_image_motion = _normalize_product_image_motion(
            os.getenv("AUTO_EDITOR_PRODUCT_IMAGE_MOTION")
            or telegram_runtime_settings.product_image_motion
        )
        allow_local_telegram = env_allow_local_telegram or bool(resolved_telegram_bot_token)
        telegram_allowed_chat_ids = _env_chat_ids("AUTO_EDITOR_TELEGRAM_ALLOWED_CHAT_IDS")
        if not telegram_allowed_chat_ids and resolved_telegram_delivery_chat_id:
            try:
                telegram_allowed_chat_ids = (int(resolved_telegram_delivery_chat_id),)
            except ValueError:
                telegram_allowed_chat_ids = ()
        require_frozen_build = _env_flag("AUTO_EDITOR_REQUIRE_FROZEN_BUILD", False)
        runtime_is_frozen = _runtime_is_frozen()
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
            realesrgan_bin=_resolve_tool(
                "AUTO_EDITOR_REALESRGAN_BIN",
                "realesrgan-ncnn-vulkan",
                "realesrgan-ncnn-vulkan.exe",
                [],
                "realesrgan-ncnn-vulkan.exe",
            ),
            backgroundremover_bin=_resolve_tool(
                "AUTO_EDITOR_BACKGROUNDREMOVER_BIN",
                "backgroundremover",
                "backgroundremover.exe",
                [],
                "backgroundremover.exe",
            ),
            backgroundremover_model=_normalize_backgroundremover_model(
                os.getenv("AUTO_EDITOR_BACKGROUNDREMOVER_MODEL", "u2netp")
            ),
            background_removal_backend=_normalize_background_removal_backend(
                os.getenv("AUTO_EDITOR_BACKGROUND_REMOVAL_BACKEND", "rembg")
            ),
            rembg_model=_normalize_rembg_model(os.getenv("AUTO_EDITOR_REMBG_MODEL", "isnet-general-use")),
            rembg_providers=_normalize_rembg_providers(
                os.getenv("AUTO_EDITOR_REMBG_PROVIDERS", "DmlExecutionProvider,CPUExecutionProvider")
            ),
            rembg_post_process_mask=_env_flag("AUTO_EDITOR_REMBG_POST_PROCESS_MASK", False),
            rembg_mask_expand_pixels=max(0, _env_int("AUTO_EDITOR_REMBG_MASK_EXPAND_PIXELS", 3)),
            adb_bin=(
                os.getenv("AUTO_EDITOR_ADB_BIN")
                or _find_runtime_binary("tools/adb.exe")
                or _find_project_binary("adb.exe")
                or shutil.which("adb")
                or _find_android_sdk_adb()
                or _find_winget_binary(["Google.PlatformTools*"], "adb.exe")
                or "adb"
            ),
            scrcpy_bin=(
                os.getenv("AUTO_EDITOR_SCRCPY_BIN")
                or _find_runtime_binary("tools/scrcpy.exe")
                or _find_project_binary("scrcpy.exe")
                or shutil.which("scrcpy")
                or _find_scrcpy_binary()
                or _find_winget_binary(["Genymobile.scrcpy*"], "scrcpy.exe")
                or "scrcpy"
            ),
            download_via_lazy_down_only=_env_flag("AUTO_EDITOR_LAZY_DOWN_ONLY", True),
            tikwm_api_url=os.getenv("AUTO_EDITOR_TIKWM_API_URL", "https://www.tikwm.com/api/").strip()
            or "https://www.tikwm.com/api/",
            product_image_enhance_enabled=_env_flag("AUTO_EDITOR_PRODUCT_IMAGE_ENHANCE", True),
            product_image_enhance_required=_env_flag("AUTO_EDITOR_PRODUCT_IMAGE_ENHANCE_REQUIRED", False),
            product_image_enhance_scale=max(1, _env_int("AUTO_EDITOR_PRODUCT_IMAGE_ENHANCE_SCALE", 4)),
            product_image_enhance_model=os.getenv("AUTO_EDITOR_PRODUCT_IMAGE_ENHANCE_MODEL", "realesrgan-x4plus").strip()
            or "realesrgan-x4plus",
            video_cut_mode=video_cut_mode,
            fixed_chunk_duration_seconds=fixed_chunk_duration_seconds,
            scene_threshold=scene_threshold,
            edit_planner_timeout_seconds=max(1.0, _env_float("AUTO_EDITOR_EDIT_PLANNER_TIMEOUT_SECONDS", 300.0)),
            product_image_crop_ratio=product_image_crop_ratio,
            product_image_motion=product_image_motion,
            default_output_root=output_root,
            max_parallel_session_items=max(1, _env_int("AUTO_EDITOR_MAX_PARALLEL_SESSION_ITEMS", 2)),
            android_device_serial=os.getenv("AUTO_EDITOR_ANDROID_DEVICE_SERIAL", "").strip(),
            android_device_video_dir=os.getenv("AUTO_EDITOR_ANDROID_VIDEO_DIR", "/sdcard/Movies/AutoTikTokEditor").strip()
            or "/sdcard/Movies/AutoTikTokEditor",
            allow_local_telegram=allow_local_telegram,
            require_frozen_build=require_frozen_build,
            runtime_is_frozen=runtime_is_frozen,
            telegram_bot_token=resolved_telegram_bot_token if allow_local_telegram else "",
            telegram_poll_timeout_seconds=max(1, _env_int("AUTO_EDITOR_TELEGRAM_POLL_TIMEOUT_SECONDS", 30)),
            telegram_poll_interval_seconds=max(1, _env_int("AUTO_EDITOR_TELEGRAM_POLL_INTERVAL_SECONDS", 2)),
            telegram_input_root=Path(
                os.getenv("AUTO_EDITOR_TELEGRAM_INPUT_ROOT", str(output_root / "_telegram_inputs"))
            ).expanduser().resolve(),
            telegram_allowed_chat_ids=telegram_allowed_chat_ids if allow_local_telegram else (),
            telegram_delivery_chat_id=resolved_telegram_delivery_chat_id if allow_local_telegram else "",
            telegram_send_result_to_telegram=_env_flag(
                "AUTO_EDITOR_TELEGRAM_SEND_RESULT_TO_TELEGRAM",
                bool(telegram_runtime_settings.send_result_to_telegram),
            ),
            telegram_save_received_video_to_profile=_env_flag(
                "AUTO_EDITOR_TELEGRAM_SAVE_RECEIVED_VIDEO_TO_PROFILE",
                bool(telegram_runtime_settings.save_received_video_to_profile),
            ),
            telegram_cleanup_after_job_enabled=_env_flag("AUTO_EDITOR_TELEGRAM_CLEANUP_AFTER_JOB", False),
            telegram_auto_cleanup_enabled=_env_flag("AUTO_EDITOR_TELEGRAM_AUTO_CLEANUP", True),
            telegram_cleanup_interval_seconds=max(
                60,
                _env_int("AUTO_EDITOR_TELEGRAM_CLEANUP_INTERVAL_SECONDS", 21600),
            ),
            telegram_cleanup_max_age_seconds=max(
                300,
                _env_int("AUTO_EDITOR_TELEGRAM_CLEANUP_MAX_AGE_SECONDS", 86400),
            ),
            tiktok_profile_slug=os.getenv("AUTO_EDITOR_TIKTOK_PROFILE_SLUG", "").strip(),
        )

    def build_job_id(self):
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        return "%s_%s" % (timestamp, uuid.uuid4().hex[:8])

    def build_session_id(self):
        timestamp = time.strftime("session_%Y%m%d_%H%M%S")
        return "%s_%s" % (timestamp, uuid.uuid4().hex[:6])
