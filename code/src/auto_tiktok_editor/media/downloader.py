"""Download public TikTok videos using lazy-down by default, with optional yt-dlp legacy fallback."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
from typing import Dict, List, Optional, Tuple
from urllib.error import URLError
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen

from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.domain.models import SourceAsset
from auto_tiktok_editor.exceptions import DownloadError, ExternalToolError
from auto_tiktok_editor.utils.command import CommandRunner


VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
IGNORED_EXTENSIONS = {".part", ".ytdl", ".json", ".description", ".jpg", ".jpeg", ".png", ".webp", ".m4a", ".mp3", ".wav", ".aac", ".opus"}
TIKTOK_EXTRACTOR_ARGS = "TikTok:app_info=musical_ly/35.1.3/2023501030/0"
TIKTOK_IMPERSONATE_TARGET = "chrome"
BROWSER_FALLBACKS = ("chrome", "edge")
LAZY_DOWN_OUTPUT_PREFIX = "lazy_result"
TIKTOK_SHORTLINK_HOSTS = {"vt.tiktok.com", "vm.tiktok.com"}
LAZY_DOWN_VIDEO_QUALITY_ORDER = {
    "hd_no_watermark": 4,
    "no_watermark": 3,
    "video": 2,
    "play": 1,
}


class SourceDownloader(object):
    def __init__(self, config: PipelineConfig, runner: CommandRunner):
        self.config = config
        self.runner = runner
        self._impersonate_supported = None  # type: Optional[bool]

    def download(self, source_url: str, destination_dir: Path, cookies_file: Optional[Path] = None) -> SourceAsset:
        destination_dir.mkdir(parents=True, exist_ok=True)
        output_template = str(destination_dir / "source.%(ext)s")
        if self.config.download_via_lazy_down_only:
            self._cleanup_destination_dir(destination_dir)
            try:
                downloaded_path, lazy_metadata = self._download_with_lazy_down(source_url, destination_dir)
                return SourceAsset(
                    source_url=source_url,
                    downloaded_path=downloaded_path,
                    extractor_name="lazy-down",
                    metadata={
                        "output_template": output_template,
                        "downloaded_path": str(downloaded_path),
                        "download_strategy": "lazy_down_primary",
                        "cookies_file_ignored": str(cookies_file) if cookies_file else None,
                        "lazy_down_json_path": lazy_metadata.get("json_path"),
                        "lazy_down_media_count": lazy_metadata.get("media_count"),
                        "lazy_down_selected_quality": lazy_metadata.get("selected_quality"),
                        "source_title": lazy_metadata.get("title"),
                        "source_author": lazy_metadata.get("author"),
                        "source_unique_id": lazy_metadata.get("unique_id"),
                    },
                )
            except (DownloadError, ExternalToolError) as exc:
                raise DownloadError(
                    "lazy-down could not obtain a playable TikTok video stream. Details: %s" % str(exc)
                )

        staged_cookies_file = self._stage_cookies_file(cookies_file, destination_dir.parent) if cookies_file is not None else None
        attempts = []  # type: List[Tuple[str, List[str]]]
        for browser in BROWSER_FALLBACKS:
            exported_cookie_path = destination_dir / ("browser_%s_cookies.txt" % browser)
            attempts.append((
                "cookies_%s_fresh" % browser,
                self._build_browser_cookie_command(source_url, output_template, browser, exported_cookie_path),
            ))
            if self._supports_impersonate():
                attempts.append((
                    "cookies_%s_fresh_impersonate" % browser,
                    self._build_browser_cookie_command(
                        source_url,
                        output_template,
                        browser,
                        exported_cookie_path,
                        impersonate=True,
                    ),
                ))
        if staged_cookies_file is not None:
            attempts.append((
                "cookies_file",
                self._build_base_command(source_url, output_template) + ["--cookies", str(staged_cookies_file)],
            ))
            if self._supports_impersonate():
                attempts.append((
                    "cookies_file_impersonate",
                    self._build_base_command(source_url, output_template, impersonate=True) + ["--cookies", str(staged_cookies_file)],
                ))
        attempts.append(("direct", self._build_base_command(source_url, output_template)))
        if self._supports_impersonate():
            attempts.append(("direct_impersonate", self._build_base_command(source_url, output_template, impersonate=True)))

        failure_messages = []
        for strategy_name, command in attempts:
            self._cleanup_destination_dir(destination_dir)
            try:
                completed = self.runner.run(command)
                downloaded_path = self._resolve_downloaded_file(completed.stdout or "", destination_dir)
                return SourceAsset(
                    source_url=source_url,
                    downloaded_path=downloaded_path,
                    extractor_name="yt-dlp",
                    metadata={
                        "output_template": output_template,
                        "downloaded_path": str(downloaded_path),
                        "extractor_args": TIKTOK_EXTRACTOR_ARGS,
                        "download_strategy": strategy_name,
                        "cookies_file": str(cookies_file) if cookies_file else None,
                        "staged_cookies_file": str(staged_cookies_file) if staged_cookies_file else None,
                    },
                )
            except (DownloadError, ExternalToolError) as exc:
                failure_messages.append("%s: %s" % (strategy_name, str(exc)))

        self._cleanup_destination_dir(destination_dir)
        try:
            downloaded_path, lazy_metadata = self._download_with_lazy_down(source_url, destination_dir)
            return SourceAsset(
                source_url=source_url,
                downloaded_path=downloaded_path,
                extractor_name="lazy-down",
                metadata={
                    "output_template": output_template,
                    "downloaded_path": str(downloaded_path),
                    "download_strategy": "lazy_down_fallback",
                    "cookies_file": str(cookies_file) if cookies_file else None,
                    "staged_cookies_file": str(staged_cookies_file) if staged_cookies_file else None,
                    "lazy_down_json_path": lazy_metadata.get("json_path"),
                    "lazy_down_media_count": lazy_metadata.get("media_count"),
                    "lazy_down_selected_quality": lazy_metadata.get("selected_quality"),
                },
            )
        except (DownloadError, ExternalToolError) as exc:
            failure_messages.append("lazy_down_fallback: %s" % str(exc))

        raise DownloadError(self._format_failure_message(failure_messages, cookies_file is not None))

    def _build_base_command(self, source_url: str, output_template: str, impersonate: bool = False) -> List[str]:
        command = [
            self.config.ytdlp_bin,
            "--no-progress",
            "--no-warnings",
            "--user-agent",
            self.config.tiktok_web_user_agent,
            "--print",
            "after_move:filepath",
            "--extractor-args",
            TIKTOK_EXTRACTOR_ARGS,
            "--format",
            "bv*+ba/b",
            "--merge-output-format",
            "mp4",
            "--output",
            output_template,
            source_url,
        ]
        if impersonate:
            command[3:3] = ["--impersonate", TIKTOK_IMPERSONATE_TARGET]
        return command

    def _build_browser_cookie_command(
        self,
        source_url: str,
        output_template: str,
        browser: str,
        exported_cookie_path: Path,
        impersonate: bool = False,
    ) -> List[str]:
        return self._build_base_command(source_url, output_template, impersonate=impersonate) + [
            "--cookies-from-browser",
            browser,
            "--cookies",
            str(exported_cookie_path),
        ]

    def _build_lazy_down_command(self, source_url: str, destination_dir: Path) -> List[str]:
        return [
            self.config.lazy_down_bin,
            source_url,
            "-P",
            str(destination_dir),
            "--all",
            "--quiet",
            "--timeout",
            "90",
            "--retries",
            "1",
            "--write-json",
            "--output-file",
            LAZY_DOWN_OUTPUT_PREFIX,
        ]

    def _stage_cookies_file(self, cookies_file: Path, workspace_dir: Path) -> Path:
        workspace_dir.mkdir(parents=True, exist_ok=True)
        staged_path = workspace_dir / "runtime_cookies.txt"
        shutil.copyfile(str(cookies_file), str(staged_path))
        return staged_path

    def _supports_impersonate(self) -> bool:
        if self._impersonate_supported is not None:
            return self._impersonate_supported
        try:
            completed = self.runner.run([self.config.ytdlp_bin, "--list-impersonate-targets"])
        except ExternalToolError:
            self._impersonate_supported = False
            return False
        target_prefix = TIKTOK_IMPERSONATE_TARGET.lower()
        for line in (completed.stdout or "").splitlines():
            text = line.strip()
            if not text:
                continue
            first_token = text.split()[0].lower()
            if first_token == target_prefix:
                self._impersonate_supported = "(unavailable)" not in text.lower()
                return self._impersonate_supported
        self._impersonate_supported = False
        return False

    def _download_with_lazy_down(self, source_url: str, destination_dir: Path) -> Tuple[Path, Dict[str, object]]:
        self.runner.ensure_tool(self.config.lazy_down_bin)
        lazy_down_source_url = self._normalize_source_url_for_lazy_down(source_url)
        command = self._build_lazy_down_command(lazy_down_source_url, destination_dir)
        completed = self.runner.run(command)
        json_path = self._find_lazy_down_json_path(completed.stdout or "", destination_dir)
        payload = self._read_lazy_down_payload(json_path)
        selected_path, selected_media = self._select_lazy_down_video(payload, destination_dir)
        root_info = payload.get("json") if isinstance(payload.get("json"), dict) else {}
        return selected_path, {
            "json_path": str(json_path),
            "media_count": len(payload.get("medias", [])),
            "selected_quality": selected_media.get("quality"),
            "title": root_info.get("title") or selected_media.get("title"),
            "author": root_info.get("author") or selected_media.get("author"),
            "unique_id": root_info.get("unique_id") or selected_media.get("unique_id"),
        }

    def _normalize_source_url_for_lazy_down(self, source_url: str) -> str:
        parsed = urlparse(source_url)
        host = (parsed.netloc or "").lower()
        if host not in TIKTOK_SHORTLINK_HOSTS:
            return self._strip_tiktok_tracking_query(source_url)
        try:
            request = Request(source_url, headers={"User-Agent": self.config.tiktok_web_user_agent})
            with urlopen(request, timeout=15) as response:
                resolved_url = response.geturl()
        except (URLError, ValueError, OSError):
            return self._strip_tiktok_tracking_query(source_url)
        resolved_parsed = urlparse(resolved_url)
        if resolved_parsed.scheme and resolved_parsed.netloc:
            return self._strip_tiktok_tracking_query(resolved_url)
        return self._strip_tiktok_tracking_query(source_url)

    def _strip_tiktok_tracking_query(self, source_url: str) -> str:
        parsed = urlparse(source_url)
        hostname = (parsed.hostname or "").lower()
        if not hostname.endswith("tiktok.com"):
            return source_url
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))

    def _resolve_downloaded_file(self, command_output: str, destination_dir: Path) -> Path:
        printed_path = self._extract_printed_path(command_output)
        if printed_path is not None and self._is_video_file(printed_path):
            return printed_path

        explicit_mp4 = destination_dir / "source.mp4"
        if explicit_mp4.exists() and explicit_mp4.is_file():
            return explicit_mp4

        video_candidates = []
        ignored_candidates = []
        other_candidates = []
        for path in destination_dir.iterdir():
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            if suffix in VIDEO_EXTENSIONS:
                video_candidates.append(path)
            elif suffix in IGNORED_EXTENSIONS:
                ignored_candidates.append(path)
            else:
                other_candidates.append(path)

        if video_candidates:
            video_candidates.sort(key=lambda item: item.stat().st_mtime, reverse=True)
            return video_candidates[0]

        if ignored_candidates and not other_candidates:
            raise DownloadError(
                "yt-dlp downloaded non-video artifacts only; the TikTok URL may resolve to audio-only media, an image post, or an unsupported asset."
            )

        if other_candidates:
            other_candidates.sort(key=lambda item: item.stat().st_mtime, reverse=True)
            return other_candidates[0]

        raise DownloadError("yt-dlp completed without producing a source file.")

    def _extract_printed_path(self, command_output: str) -> Optional[Path]:
        lines = [line.strip() for line in command_output.splitlines() if line.strip()]
        for line in reversed(lines):
            candidate = Path(line)
            if candidate.exists() and candidate.is_file():
                return candidate.resolve()
        return None

    def _is_video_file(self, path: Path) -> bool:
        return path.suffix.lower() in VIDEO_EXTENSIONS

    def _find_lazy_down_json_path(self, command_output: str, destination_dir: Path) -> Path:
        lines = [line.strip() for line in command_output.splitlines() if line.strip()]
        for line in reversed(lines):
            if not line.startswith("{"):
                continue
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            json_path = payload.get("jsonPath")
            if json_path:
                candidate = Path(str(json_path))
                if candidate.exists() and candidate.is_file():
                    return candidate.resolve()
        candidates = sorted(destination_dir.glob("%s*.json" % LAZY_DOWN_OUTPUT_PREFIX), key=lambda item: item.stat().st_mtime, reverse=True)
        if candidates:
            return candidates[0].resolve()
        raise DownloadError("lazy-down completed without producing a JSON manifest.")

    def _read_lazy_down_payload(self, json_path: Path) -> Dict[str, object]:
        try:
            return json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DownloadError("lazy-down produced an unreadable JSON manifest.") from exc

    def _select_lazy_down_video(self, payload: Dict[str, object], destination_dir: Path) -> Tuple[Path, Dict[str, object]]:
        medias = payload.get("medias")
        if not isinstance(medias, list):
            raise DownloadError("lazy-down manifest did not include any media entries.")
        video_candidates = []
        for media in medias:
            if not isinstance(media, dict):
                continue
            if media.get("type") != "video":
                continue
            local_path_value = media.get("localPath") or media.get("savedPath")
            if not local_path_value:
                continue
            candidate_path = Path(str(local_path_value))
            if not candidate_path.exists() or not candidate_path.is_file():
                continue
            if candidate_path.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            duration_seconds = self._coerce_duration_seconds(
                media.get("duration")
                or media.get("videoDuration")
                or media.get("durationSeconds")
                or media.get("length")
            )
            quality_rank = LAZY_DOWN_VIDEO_QUALITY_ORDER.get(str(media.get("quality") or "").lower(), 0)
            width = int(media.get("width") or 0)
            height = int(media.get("height") or 0)
            filesize = int(media.get("filesize") or 0)
            # Thu tu uu tien: video day du thoi luong > chat luong > do phan giai > kich thuoc file.
            video_candidates.append(((duration_seconds, quality_rank, width * height, filesize), candidate_path.resolve(), media))
        if video_candidates:
            video_candidates.sort(key=lambda item: item[0], reverse=True)
            _score, selected_path, selected_media = video_candidates[0]
            return selected_path, selected_media
        fallback_path = self._resolve_downloaded_file("", destination_dir)
        return fallback_path, {}

    def _coerce_duration_seconds(self, value) -> float:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return 0.0
        if numeric <= 0:
            return 0.0
        # lazy-down co the tra ve duration theo milliseconds trong mot so payload.
        if numeric >= 1000.0:
            return numeric / 1000.0
        return numeric

    def _cleanup_destination_dir(self, destination_dir: Path) -> None:
        for path in destination_dir.iterdir():
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path, ignore_errors=True)

    def _format_failure_message(self, failure_messages: List[str], has_custom_cookies: bool) -> str:
        if not failure_messages:
            return "Unable to download the TikTok source video."
        browser_cookie_issue = any(
            "Could not copy Chrome cookie database" in message or "Failed to decrypt with DPAPI" in message
            for message in failure_messages
        )
        non_video_issue = any("non-video artifacts only" in message for message in failure_messages)
        custom_cookie_issue = any(message.startswith("cookies_file:") for message in failure_messages)
        custom_cookie_forbidden = any(
            message.startswith("cookies_file:") and "HTTP Error 403" in message
            for message in failure_messages
        )
        details = "; ".join(failure_messages)
        if has_custom_cookies and custom_cookie_forbidden:
            return (
                "yt-dlp reached TikTok with the provided cookies file, but TikTok returned HTTP 403. "
                "This usually means the exported session cookies are stale, logged out, or the URL is restricted for that account/region. "
                "Details: %s" % details
            )
        if has_custom_cookies and custom_cookie_issue and non_video_issue:
            return (
                "yt-dlp could not obtain a playable video stream even with the provided cookies file. "
                "Verify that cookies.txt is fresh, exported in Netscape format, and preferably less than 30 minutes old. Details: %s" % details
            )
        if browser_cookie_issue and non_video_issue:
            return (
                "yt-dlp could not obtain a playable video stream. Direct download returned non-video artifacts, "
                "and browser-cookie fallback failed. Close Chrome and Edge completely, retry with a fresh browser session, and keep the browser User-Agent aligned with yt-dlp. Details: %s" % details
            )
        if any(message.startswith("lazy_down_fallback:") for message in failure_messages):
            return (
                "yt-dlp could not obtain a playable TikTok video stream, and lazy-down fallback also failed. "
                "Details: %s" % details
            )
        return "yt-dlp could not obtain a playable video stream. Details: %s" % details
