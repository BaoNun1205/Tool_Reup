"""Validation logic for session inputs and per-item jobs."""

from __future__ import annotations

from pathlib import Path
import time
import re
from urllib.parse import urlparse

from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.domain.models import JobSpec, SessionItemSpec, SessionSpec, ValidatedJob, ValidatedSession, ValidatedSessionItem
from auto_tiktok_editor.exceptions import SessionValidationError, ValidationError
from auto_tiktok_editor.utils.image_probe import probe_image


SAFE_BASENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
SUPPORTED_TIKTOK_SUFFIX = "tiktok.com"
TIKTOK_COOKIE_WEB_SUFFIX = "tiktok.com"
TIKTOK_COOKIE_MEDIA_SUFFIX = "tiktokv.com"
MIN_ACCEPTED_EDGE = 400
RECOMMENDED_EDGE = 800


class InputValidator(object):
    def __init__(self, config=None):
        self.config = config or PipelineConfig.from_env()

    def validate(self, job_spec):
        self._validate_source_url(job_spec.source_video_url)
        image_path = Path(job_spec.product_image).expanduser().resolve()
        if not image_path.exists() or not image_path.is_file():
            raise ValidationError("Product image file does not exist.")
        image_info = probe_image(image_path)
        warnings = []
        if image_info.longest_edge < MIN_ACCEPTED_EDGE:
            raise ValidationError(
                "Product image is too small for MVP rendering; use a larger image."
            )
        if image_info.longest_edge < RECOMMENDED_EDGE:
            warnings.append(
                "Product image is smaller than the recommended 800px long edge; quality may degrade."
            )
        output_dir = Path(job_spec.output_dir).expanduser().resolve()
        basename = self._sanitize_basename(job_spec.output_basename)
        cookies_file = self._validate_cookies_file(job_spec.cookies_file)
        warnings.extend(self._build_cookie_warnings(cookies_file))
        normalized = JobSpec(
            source_video_url=job_spec.source_video_url.strip(),
            product_image=image_path,
            output_dir=output_dir,
            output_basename=basename,
            shuffle_seed=job_spec.shuffle_seed,
            cookies_file=cookies_file,
            overlay_alpha_ratio=self._normalize_overlay_alpha_ratio(job_spec.overlay_alpha_ratio),
        )
        return ValidatedJob(job_spec=normalized, image_info=image_info, warnings=warnings)

    def _validate_source_url(self, value):
        if not value or not value.strip():
            raise ValidationError("Source video URL is required.")
        parsed = urlparse(value.strip())
        if parsed.scheme not in ("http", "https"):
            raise ValidationError("Source video URL must start with http or https.")
        hostname = (parsed.hostname or "").lower()
        if not hostname or not hostname.endswith(SUPPORTED_TIKTOK_SUFFIX):
            raise ValidationError("Only public TikTok URLs are supported in the MVP.")

    def _sanitize_basename(self, value):
        if not value:
            return None
        sanitized = SAFE_BASENAME_RE.sub("_", value.strip()).strip("._")
        if not sanitized:
            raise ValidationError("Output basename contains no valid filename characters.")
        return sanitized

    def _normalize_overlay_alpha_ratio(self, value):
        if value is None:
            return None
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            raise ValidationError("Overlay opacity must be a number.")
        if numeric < 0.05 or numeric > 0.95:
            raise ValidationError("Overlay opacity must stay between 0.05 and 0.95.")
        return numeric

    def _validate_cookies_file(self, value):
        if not value:
            return None
        if self.config.download_via_lazy_down_only:
            return None
        cookies_path = Path(value).expanduser().resolve()
        if not cookies_path.exists() or not cookies_path.is_file():
            raise ValidationError("Cookies file does not exist.")
        return cookies_path

    def _build_cookie_warnings(self, cookies_path):
        if cookies_path is None:
            return []
        if self.config.download_via_lazy_down_only:
            return []
        domains = self._read_cookie_domains(cookies_path)
        if not domains:
            return [
                "Cookies file is empty or unreadable; TikTok download may fail."
            ]
        has_web_domain = any(domain.endswith(TIKTOK_COOKIE_WEB_SUFFIX) for domain in domains)
        has_media_domain = any(domain.endswith(TIKTOK_COOKIE_MEDIA_SUFFIX) for domain in domains)
        warnings = []
        if not has_web_domain:
            warnings.append(
                "Cookies file does not include any tiktok.com domain entries; authenticated TikTok requests may fail."
            )
        if has_web_domain and not has_media_domain:
            warnings.append(
                "Cookies file includes tiktok.com entries but no tiktokv.com/api domain entries. "
                "TikTok pages may still open in the browser, but yt-dlp can fail or return audio-only artifacts. "
                "Try exporting all TikTok cookies instead of site-only cookies."
            )
        freshness_warning = self._build_cookie_freshness_warning(cookies_path)
        if freshness_warning is not None:
            warnings.append(freshness_warning)
        return warnings

    def _build_cookie_freshness_warning(self, cookies_path):
        try:
            age_seconds = max(0.0, time.time() - cookies_path.stat().st_mtime)
        except OSError:
            return None
        if age_seconds <= float(self.config.browser_cookie_freshness_seconds):
            return None
        age_minutes = int(round(age_seconds / 60.0))
        freshness_minutes = int(round(self.config.browser_cookie_freshness_seconds / 60.0))
        return (
            "Cookies file appears older than %s minutes (current file age: %s minutes). "
            "TikTok cookies are more reliable when exported from a fresh browser session within the last %s minutes."
            % (freshness_minutes, age_minutes, freshness_minutes)
        )

    def _read_cookie_domains(self, cookies_path):
        domains = set()
        try:
            with cookies_path.open("r", encoding="utf-8", errors="replace") as handle:
                for raw_line in handle:
                    line = raw_line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split("\t")
                    if not parts:
                        continue
                    domain = parts[0].strip().lower().lstrip(".")
                    if domain:
                        domains.add(domain)
        except OSError:
            return set()
        return domains


class SessionValidator(object):
    def __init__(self, item_validator=None, config=None):
        self.config = config or PipelineConfig.from_env()
        self.item_validator = item_validator or InputValidator(config=self.config)

    def validate(self, session_spec):
        row_errors = {}
        validated_items = []
        warnings = []
        output_root_dir = Path(session_spec.output_root_dir).expanduser().resolve()
        if output_root_dir.exists() and not output_root_dir.is_dir():
            raise SessionValidationError("Output root must be a directory.")
        output_root_dir.mkdir(parents=True, exist_ok=True)
        cookies_file = self._validate_session_cookies_file(session_spec.cookies_file)
        if not session_spec.items:
            raise SessionValidationError("Add at least one item before running the session.")
        if len(session_spec.items) > self.config.max_session_items:
            raise SessionValidationError(
                "Session exceeds the MVP limit of %s items." % self.config.max_session_items
            )
        seen_pairs = {}
        for index, item_spec in enumerate(session_spec.items):
            item_messages = self._basic_row_checks(item_spec)
            if item_messages:
                row_errors[index] = item_messages
                continue
            provisional_job = JobSpec(
                source_video_url=item_spec.source_video_url,
                product_image=item_spec.product_image,
                output_dir=output_root_dir,
                output_basename=item_spec.output_basename,
                shuffle_seed=item_spec.shuffle_seed,
                cookies_file=cookies_file,
                overlay_alpha_ratio=item_spec.overlay_alpha_ratio,
            )
            try:
                validated_job = self.item_validator.validate(provisional_job)
            except ValidationError as exc:
                row_errors[index] = [str(exc)]
                continue
            normalized_item = SessionItemSpec(
                row_id=item_spec.row_id or "row_%03d" % (index + 1),
                source_video_url=validated_job.job_spec.source_video_url,
                product_image=validated_job.job_spec.product_image,
                output_basename=validated_job.job_spec.output_basename,
                shuffle_seed=validated_job.job_spec.shuffle_seed,
                overlay_alpha_ratio=validated_job.job_spec.overlay_alpha_ratio,
            )
            duplicate_key = (
                normalized_item.source_video_url,
                str(normalized_item.product_image),
            )
            if duplicate_key in seen_pairs:
                warnings.append(
                    "Item %s duplicates the same source/image pair as item %s." % (
                        index + 1,
                        seen_pairs[duplicate_key] + 1,
                    )
                )
            else:
                seen_pairs[duplicate_key] = index
            validated_items.append(
                ValidatedSessionItem(
                    item_index=index,
                    row_id=normalized_item.row_id,
                    item_spec=normalized_item,
                    validated_job=validated_job,
                    warnings=list(validated_job.warnings),
                )
            )
        if row_errors:
            raise SessionValidationError(
                "Fix the invalid rows before starting the session.",
                row_errors=row_errors,
            )
        normalized_session = SessionSpec(
            items=[item.item_spec for item in validated_items],
            output_root_dir=output_root_dir,
            session_name=(session_spec.session_name or "").strip() or None,
            cookies_file=cookies_file,
        )
        return ValidatedSession(
            session_spec=normalized_session,
            items=validated_items,
            warnings=warnings,
        )

    def _basic_row_checks(self, item_spec):
        messages = []
        if not item_spec.source_video_url or not item_spec.source_video_url.strip():
            messages.append("TikTok URL is required for this row.")
        if item_spec.product_image is None or not str(item_spec.product_image).strip():
            messages.append("Product image is required for this row.")
        return messages

    def _validate_session_cookies_file(self, value):
        if not value:
            return None
        if self.config.download_via_lazy_down_only:
            return None
        cookies_path = Path(value).expanduser().resolve()
        if not cookies_path.exists() or not cookies_path.is_file():
            raise SessionValidationError("Cookies file does not exist.")
        return cookies_path
