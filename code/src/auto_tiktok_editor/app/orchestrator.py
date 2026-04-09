"""Session orchestration entry points for the Auto TikTok Editor MVP."""

from __future__ import annotations

import logging
import time
from typing import Callable, Dict, List, Optional

from auto_tiktok_editor.app.recorder import PipelineRecorder
from auto_tiktok_editor.app.services import PipelineServices, build_default_services
from auto_tiktok_editor.app.workspace import SessionWorkspace, create_item_workspace, create_session_workspace
from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.domain.models import ItemProcessResult, SessionEvent, SessionResult, SessionSpec, ValidatedSessionItem
from auto_tiktok_editor.domain.validation import SessionValidator
from auto_tiktok_editor.exceptions import EditorError, SessionValidationError


EventCallback = Optional[Callable[[SessionEvent], None]]


class ItemPipelineRunner(object):
    def __init__(
        self,
        config: Optional[PipelineConfig] = None,
        services: Optional[PipelineServices] = None,
        logger: Optional[logging.Logger] = None,
    ):
        self.config = config or PipelineConfig.from_env()
        self.logger = logger or logging.getLogger("auto_tiktok_editor")
        self.services = services or build_default_services(self.config)

    def run(
        self,
        session_id: str,
        validated_item: ValidatedSessionItem,
        session_workspace: SessionWorkspace,
        event_callback: EventCallback = None,
    ) -> ItemProcessResult:
        job_id = self.config.build_job_id()
        workspace = create_item_workspace(session_workspace, validated_item.item_index, validated_item.row_id)
        recorder = PipelineRecorder(job_id, run_label="job", logger=self.logger)
        metadata = {
            "job_id": job_id,
            "session_id": session_id,
            "item_index": validated_item.item_index,
            "row_id": validated_item.row_id,
            "source_url": validated_item.item_spec.source_video_url,
            "status": "running",
            "warnings": [],
            "cookies_file": str(validated_item.validated_job.job_spec.cookies_file) if validated_item.validated_job.job_spec.cookies_file else None,
        }
        final_video_path = None
        final_audio_path = None
        prepared_audio_path = None
        self._emit(
            event_callback,
            SessionEvent(
                event_type="item_started",
                session_id=session_id,
                item_index=validated_item.item_index,
                row_id=validated_item.row_id,
                status="queued",
                message="Item entered the processing queue.",
            ),
        )
        try:
            validated = validated_item.validated_job
            self._transition(recorder, session_id, validated_item, "validating_input", event_callback)
            self._record_warnings(recorder, session_id, validated_item, validated_item.warnings, event_callback)
            metadata["image_type_detected"] = validated.image_info.image_type

            self._transition(recorder, session_id, validated_item, "downloading_source", event_callback)
            source_asset = self.services.downloader.download(
                validated.job_spec.source_video_url,
                workspace.source_dir,
                validated.job_spec.cookies_file,
            )
            metadata["download_strategy_used"] = source_asset.metadata.get("download_strategy")

            self._transition(recorder, session_id, validated_item, "normalizing_media", event_callback)
            source_info = self.services.probe.probe(source_asset.downloaded_path)
            metadata["source_duration"] = source_info.duration_seconds
            working_media = self.services.normalizer.normalize(
                source_asset,
                source_info,
                workspace.normalized_dir / "working_media.mp4",
            )
            self._record_warnings(recorder, session_id, validated_item, working_media.warnings, event_callback)

            self._transition(recorder, session_id, validated_item, "speed_processing", event_callback)
            processed_master = self.services.speed_processor.process(
                working_media,
                workspace.processed_dir / "processed_master.mp4",
            )
            metadata["working_duration_after_speed"] = processed_master.info.duration_seconds

            self._transition(recorder, session_id, validated_item, "extracting_audio", event_callback)
            prepared_audio = self.services.audio_finisher.prepare(
                processed_master,
                workspace.processed_dir / "pre_shuffle_audio.m4a",
            )
            self._record_warnings(recorder, session_id, validated_item, prepared_audio.warnings, event_callback)
            prepared_audio_path = prepared_audio.path if prepared_audio.has_audio else None
            metadata["audio_extracted_before_shuffle"] = True

            self._transition(recorder, session_id, validated_item, "detecting_scenes", event_callback)
            raw_scenes, black_ranges, detection_warnings = self.services.scene_detector.detect(processed_master)
            usable_scenes, dropped_scenes, qualification_warnings = self.services.scene_qualifier.qualify(
                raw_scenes,
                black_ranges,
            )
            self._record_warnings(
                recorder,
                session_id,
                validated_item,
                detection_warnings + qualification_warnings,
                event_callback,
            )
            metadata["scene_detected_count"] = len(raw_scenes)
            metadata["scene_kept_count"] = len(usable_scenes)
            metadata["scene_dropped_count"] = len(dropped_scenes)

            self._transition(recorder, session_id, validated_item, "planning_edit", event_callback)
            edit_plan = self.services.edit_planner.build(usable_scenes, validated.job_spec.shuffle_seed)
            self._record_warnings(recorder, session_id, validated_item, edit_plan.warnings, event_callback)
            metadata["shuffle_seed_used"] = edit_plan.seed
            metadata["scene_drop_reasons"] = [scene.drop_reason for scene in dropped_scenes if scene.drop_reason]

            self._transition(recorder, session_id, validated_item, "rendering_final", event_callback)
            rough_cut = self.services.rough_cut_renderer.render(
                processed_master,
                edit_plan,
                workspace.clips_dir,
                workspace.output_dir / "rough_cut.mp4",
            )
            overlay_spec = self.services.overlay_planner.plan(validated.image_info)
            self._record_warnings(recorder, session_id, validated_item, overlay_spec.warnings, event_callback)
            metadata["overlay_mode_used"] = overlay_spec.mode

            final_audio = self.services.audio_finisher.finish(
                prepared_audio,
                rough_cut.info.duration_seconds,
                workspace.output_dir / "final_audio.m4a",
            )
            self._record_warnings(recorder, session_id, validated_item, final_audio.warnings, event_callback)
            final_audio_path = final_audio.path

            final_video_path = self.services.final_compositor.compose(
                rough_cut,
                final_audio,
                overlay_spec,
                workspace.output_dir / "final_video.mp4",
            )

            self._transition(recorder, session_id, validated_item, "exporting_artifacts", event_callback)
            metadata["status"] = "completed"
            metadata["audio_warnings"] = [
                warning for warning in recorder.warnings if "audio" in warning.lower() or "silent" in warning.lower()
            ]
            metadata["render_warnings"] = list(recorder.warnings)
            metadata["final_output_paths"] = {
                "final_video": str(final_video_path),
                "final_audio": str(final_audio_path),
                "pre_shuffle_audio": str(prepared_audio_path) if prepared_audio_path else None,
            }
            artifacts = self.services.artifact_exporter.export_item(
                workspace,
                final_video_path,
                final_audio_path,
                metadata,
                recorder,
            )
            result = ItemProcessResult(
                item_index=validated_item.item_index,
                row_id=validated_item.row_id,
                job_id=job_id,
                status="completed",
                source_video_url=validated_item.item_spec.source_video_url,
                product_image_path=validated_item.item_spec.product_image,
                output_dir=workspace.root_dir,
                artifacts=artifacts,
                warnings=list(recorder.warnings),
                metadata=metadata,
            )
            self._emit(
                event_callback,
                SessionEvent(
                    event_type="item_completed",
                    session_id=session_id,
                    item_index=validated_item.item_index,
                    row_id=validated_item.row_id,
                    status="completed",
                    message="Item finished successfully.",
                    payload={
                        "output_dir": str(workspace.root_dir),
                        "final_video_path": str(final_video_path),
                    },
                ),
            )
            return result
        except Exception as exc:
            error_message = str(exc) or exc.__class__.__name__
            if not isinstance(exc, EditorError):
                self.logger.exception("Unexpected item pipeline failure for row %s.", validated_item.row_id)
                error_message = "Unexpected error: %s" % error_message
            recorder.error(error_message)
            metadata["status"] = "failed"
            metadata["failure_stage"] = recorder.current_stage
            metadata["error"] = error_message
            metadata["render_warnings"] = list(recorder.warnings)
            artifacts = self.services.artifact_exporter.export_item(
                workspace,
                final_video_path,
                final_audio_path,
                metadata,
                recorder,
            )
            result = ItemProcessResult(
                item_index=validated_item.item_index,
                row_id=validated_item.row_id,
                job_id=job_id,
                status="failed",
                source_video_url=validated_item.item_spec.source_video_url,
                product_image_path=validated_item.item_spec.product_image,
                output_dir=workspace.root_dir,
                artifacts=artifacts,
                warnings=list(recorder.warnings),
                metadata=metadata,
                error=error_message,
            )
            self._emit(
                event_callback,
                SessionEvent(
                    event_type="item_failed",
                    session_id=session_id,
                    item_index=validated_item.item_index,
                    row_id=validated_item.row_id,
                    status="failed",
                    message=error_message,
                    payload={"output_dir": str(workspace.root_dir)},
                ),
            )
            return result

    def _transition(
        self,
        recorder: PipelineRecorder,
        session_id: str,
        validated_item: ValidatedSessionItem,
        stage: str,
        event_callback: EventCallback,
    ) -> None:
        recorder.transition(stage)
        self._emit(
            event_callback,
            SessionEvent(
                event_type="item_stage",
                session_id=session_id,
                item_index=validated_item.item_index,
                row_id=validated_item.row_id,
                stage=stage,
                status=self._status_for_stage(stage),
                message="Entered stage '%s'." % stage,
            ),
        )

    def _record_warnings(
        self,
        recorder: PipelineRecorder,
        session_id: str,
        validated_item: ValidatedSessionItem,
        warnings: List[str],
        event_callback: EventCallback,
    ) -> None:
        for warning in warnings:
            recorder.warning(warning)
            self._emit(
                event_callback,
                SessionEvent(
                    event_type="item_warning",
                    session_id=session_id,
                    item_index=validated_item.item_index,
                    row_id=validated_item.row_id,
                    status=self._status_for_stage(recorder.current_stage),
                    stage=recorder.current_stage,
                    message=warning,
                ),
            )

    def _status_for_stage(self, stage: str) -> str:
        if stage == "validating_input":
            return "validating"
        if stage == "downloading_source":
            return "downloading"
        if stage in ("exporting_artifacts",):
            return "processing"
        return "processing"

    def _emit(self, callback: EventCallback, event: SessionEvent) -> None:
        if callback is not None:
            callback(event)


class SessionOrchestrator(object):
    def __init__(
        self,
        config: Optional[PipelineConfig] = None,
        services: Optional[PipelineServices] = None,
        session_validator: Optional[SessionValidator] = None,
        item_runner: Optional[ItemPipelineRunner] = None,
        logger: Optional[logging.Logger] = None,
    ):
        self.config = config or PipelineConfig.from_env()
        self.logger = logger or logging.getLogger("auto_tiktok_editor")
        self.services = services or build_default_services(self.config)
        self.session_validator = session_validator or SessionValidator(self.services.validator, self.config)
        self.item_runner = item_runner or ItemPipelineRunner(self.config, self.services, logger=self.logger)
        self._last_session_cookies_file = None

    def run(self, session_spec: SessionSpec, event_callback: EventCallback = None) -> SessionResult:
        session_id = self.config.build_session_id()
        recorder = PipelineRecorder(session_id, run_label="session", logger=self.logger)
        started_at = self._now()
        items = []  # type: List[ItemProcessResult]
        session_workspace = None  # type: Optional[SessionWorkspace]
        self._last_session_cookies_file = None
        self._emit(
            event_callback,
            SessionEvent(
                event_type="session_started",
                session_id=session_id,
                status="draft",
                message="Session created.",
            ),
        )
        try:
            recorder.transition("validating_session")
            self._emit(
                event_callback,
                SessionEvent(
                    event_type="session_stage",
                    session_id=session_id,
                    status="validating_session",
                    stage="validating_session",
                    message="Validating all session rows.",
                ),
            )
            validated_session = self.session_validator.validate(session_spec)
            self._last_session_cookies_file = validated_session.session_spec.cookies_file
            for warning in validated_session.warnings:
                recorder.warning(warning)
            self._emit(
                event_callback,
                SessionEvent(
                    event_type="session_stage",
                    session_id=session_id,
                    status="ready_to_run",
                    stage="ready_to_run",
                    message="Session validated successfully.",
                    payload={
                        "item_count": len(validated_session.items),
                        "cookies_file": str(validated_session.session_spec.cookies_file) if validated_session.session_spec.cookies_file else None,
                    },
                ),
            )

            session_workspace = create_session_workspace(validated_session.session_spec.output_root_dir, session_id)
            recorder.info("Session workspace created at %s." % session_workspace.root_dir)
            recorder.transition("running")
            self._emit(
                event_callback,
                SessionEvent(
                    event_type="session_stage",
                    session_id=session_id,
                    status="running",
                    stage="running",
                    message="Processing items sequentially.",
                    payload={"item_count": len(validated_session.items)},
                ),
            )

            for validated_item in validated_session.items:
                result = self.item_runner.run(
                    session_id,
                    validated_item,
                    session_workspace,
                    event_callback=event_callback,
                )
                items.append(result)
                self._emit(
                    event_callback,
                    SessionEvent(
                        event_type="session_progress",
                        session_id=session_id,
                        status="running",
                        message="Session progress updated.",
                        payload={
                            "completed_items": len([item for item in items if item.status == "completed"]),
                            "failed_items": len([item for item in items if item.status == "failed"]),
                            "processed_items": len(items),
                            "total_items": len(validated_session.items),
                        },
                    ),
                )

            final_status = self._final_status(items)
            recorder.transition("exporting_summary")
            summary = self._build_summary(
                session_id=session_id,
                session_name=validated_session.session_spec.session_name,
                status=final_status,
                started_at=started_at,
                finished_at=self._now(),
                items=items,
                warnings=list(recorder.warnings),
            )
            artifacts = self.services.artifact_exporter.export_session(
                session_workspace,
                summary,
                recorder,
            )
            self._emit(
                event_callback,
                SessionEvent(
                    event_type="session_completed",
                    session_id=session_id,
                    status=final_status,
                    message="Session completed.",
                    payload=summary,
                ),
            )
            return SessionResult(
                session_id=session_id,
                status=final_status,
                items=items,
                warnings=list(recorder.warnings),
                summary=summary,
                artifacts=artifacts,
            )
        except SessionValidationError as exc:
            recorder.error(str(exc))
            summary = {
                "session_id": session_id,
                "status": "failed_session",
                "started_at": started_at,
                "finished_at": self._now(),
                "row_errors": exc.row_errors,
                "item_count_total": len(session_spec.items),
                "item_count_completed": 0,
                "item_count_failed": 0,
                "items": [],
                "cookies_file": str(session_spec.cookies_file) if session_spec.cookies_file else None,
            }
            self._emit(
                event_callback,
                SessionEvent(
                    event_type="session_validation_failed",
                    session_id=session_id,
                    status="failed_session",
                    message=str(exc),
                    payload={"row_errors": exc.row_errors},
                ),
            )
            return SessionResult(
                session_id=session_id,
                status="failed_session",
                items=[],
                warnings=list(recorder.warnings),
                summary=summary,
                row_errors=exc.row_errors,
            )
        except Exception as exc:
            self.logger.exception("Unexpected session orchestration failure.")
            error_message = str(exc) or exc.__class__.__name__
            recorder.error("Unexpected session error: %s" % error_message)
            summary = self._build_summary(
                session_id=session_id,
                session_name=session_spec.session_name,
                status="failed_session",
                started_at=started_at,
                finished_at=self._now(),
                items=items,
                warnings=list(recorder.warnings),
            )
            artifacts = None
            if session_workspace is not None:
                artifacts = self.services.artifact_exporter.export_session(
                    session_workspace,
                    summary,
                    recorder,
                )
            self._emit(
                event_callback,
                SessionEvent(
                    event_type="session_failed",
                    session_id=session_id,
                    status="failed_session",
                    message=error_message,
                    payload=summary,
                ),
            )
            return SessionResult(
                session_id=session_id,
                status="failed_session",
                items=items,
                warnings=list(recorder.warnings),
                summary=summary,
                artifacts=artifacts,
            )

    def _build_summary(
        self,
        session_id: str,
        session_name: Optional[str],
        status: str,
        started_at: str,
        finished_at: str,
        items: List[ItemProcessResult],
        warnings: List[str],
    ) -> Dict[str, object]:
        completed_items = [item for item in items if item.status == "completed"]
        failed_items = [item for item in items if item.status == "failed"]
        return {
            "session_id": session_id,
            "session_name": session_name,
            "cookies_file": str(self._last_session_cookies_file) if self._last_session_cookies_file else None,
            "status": status,
            "started_at": started_at,
            "finished_at": finished_at,
            "item_count_total": len(items),
            "item_count_completed": len(completed_items),
            "item_count_failed": len(failed_items),
            "warnings": warnings,
            "items": [self._summarize_item(item) for item in items],
        }

    def _summarize_item(self, item: ItemProcessResult) -> Dict[str, object]:
        return {
            "item_index": item.item_index,
            "row_id": item.row_id,
            "job_id": item.job_id,
            "status": item.status,
            "source_video_url": item.source_video_url,
            "product_image_path": str(item.product_image_path) if item.product_image_path else None,
            "output_dir": str(item.output_dir),
            "final_video_path": str(item.artifacts.final_video_path) if item.artifacts.final_video_path else None,
            "final_audio_path": str(item.artifacts.final_audio_path) if item.artifacts.final_audio_path else None,
            "metadata_path": str(item.artifacts.metadata_path),
            "process_log_path": str(item.artifacts.process_log_path),
            "warnings": item.warnings,
            "error": item.error,
        }

    def _final_status(self, items: List[ItemProcessResult]) -> str:
        if any(item.status == "failed" for item in items):
            return "completed_with_partial_failure"
        return "completed_with_success"

    def _now(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S")

    def _emit(self, callback: EventCallback, event: SessionEvent) -> None:
        if callback is not None:
            callback(event)
