"""Session orchestration entry points for the Auto TikTok Editor MVP."""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import logging
import queue
import time
from typing import Callable, Dict, List, Optional

from auto_tiktok_editor.app.recorder import PipelineRecorder
from auto_tiktok_editor.app.services import PipelineServices, build_default_services
from auto_tiktok_editor.app.workspace import SessionWorkspace, create_item_workspace, create_session_workspace
from auto_tiktok_editor.config import PipelineConfig
from auto_tiktok_editor.domain.models import ItemProcessResult, SessionArtifacts, SessionEvent, SessionItemSpec, SessionResult, SessionSpec, ValidatedSessionItem
from auto_tiktok_editor.domain.validation import SessionValidator
from auto_tiktok_editor.exceptions import EditorError, SessionValidationError


EventCallback = Optional[Callable[[SessionEvent], None]]
LicenseCheckpoint = Optional[Callable[[], None]]


class ItemPipelineRunner(object):
    def __init__(
        self,
        config: Optional[PipelineConfig] = None,
        services: Optional[PipelineServices] = None,
        license_checkpoint: LicenseCheckpoint = None,
        logger: Optional[logging.Logger] = None,
    ):
        self.config = config or PipelineConfig.from_env()
        self.logger = logger or logging.getLogger("auto_tiktok_editor")
        self.services = services or build_default_services(self.config)
        self.license_checkpoint = license_checkpoint

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
            metadata["source_title"] = source_asset.metadata.get("source_title")
            metadata["source_author"] = source_asset.metadata.get("source_author")
            metadata["source_unique_id"] = source_asset.metadata.get("source_unique_id")

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
            overlay_spec = self.services.overlay_planner.plan(
                validated.image_info,
                validated.job_spec.overlay_alpha_ratio,
            )
            self._record_warnings(recorder, session_id, validated_item, overlay_spec.warnings, event_callback)
            metadata["overlay_mode_used"] = overlay_spec.mode
            metadata["overlay_alpha_ratio_used"] = overlay_spec.separator_max_alpha_ratio
            metadata["overlay_fade_ratio_used"] = overlay_spec.separator_fade_ratio

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
        self._checkpoint_license()
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

    def _checkpoint_license(self) -> None:
        if self.license_checkpoint is not None:
            self.license_checkpoint()


class SessionOrchestrator(object):
    def __init__(
        self,
        config: Optional[PipelineConfig] = None,
        services: Optional[PipelineServices] = None,
        session_validator: Optional[SessionValidator] = None,
        item_runner: Optional[ItemPipelineRunner] = None,
        license_checkpoint: LicenseCheckpoint = None,
        logger: Optional[logging.Logger] = None,
    ):
        self.config = config or PipelineConfig.from_env()
        self.logger = logger or logging.getLogger("auto_tiktok_editor")
        self.services = services or build_default_services(self.config)
        self.session_validator = session_validator or SessionValidator(self.services.validator, self.config)
        self.license_checkpoint = license_checkpoint
        self.item_runner = item_runner or ItemPipelineRunner(
            self.config,
            self.services,
            license_checkpoint=license_checkpoint,
            logger=self.logger,
        )
        self._last_session_cookies_file = None

    def run(
        self,
        session_spec: SessionSpec,
        event_callback: EventCallback = None,
        rerun_queue: Optional[queue.Queue] = None,
    ) -> SessionResult:
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
            self._checkpoint_license()
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
            self._checkpoint_license()
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
                    message="Processing items in parallel.",
                    payload={
                        "item_count": len(validated_session.items),
                        "max_parallel_items": max(1, int(self.config.max_parallel_session_items)),
                    },
                ),
            )

            items = self._run_items_parallel(
                session_id=session_id,
                validated_session=validated_session,
                session_workspace=session_workspace,
                event_callback=event_callback,
                rerun_queue=rerun_queue,
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
            summary["review_required"] = True
            artifacts = self.services.artifact_exporter.create_review_session_artifacts(session_workspace)
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
                    items=items,
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

    def _run_items_parallel(
        self,
        session_id: str,
        validated_session,
        session_workspace: SessionWorkspace,
        event_callback: EventCallback,
        rerun_queue: Optional[queue.Queue],
    ) -> List[ItemProcessResult]:
        max_workers = max(1, int(self.config.max_parallel_session_items))
        results = [None] * len(validated_session.items)
        running_futures = {}
        running_indexes = set()
        pending_reruns = {}

        def submit(validated_item: ValidatedSessionItem) -> None:
            future = executor.submit(
                self.item_runner.run,
                session_id,
                validated_item,
                session_workspace,
                event_callback,
            )
            running_futures[future] = validated_item
            running_indexes.add(validated_item.item_index)

        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="auto-editor-item") as executor:
            for validated_item in validated_session.items:
                submit(validated_item)
            while running_futures or pending_reruns or self._queue_has_pending_items(rerun_queue):
                self._drain_rerun_requests(
                    rerun_queue=rerun_queue,
                    running_indexes=running_indexes,
                    pending_reruns=pending_reruns,
                    results=results,
                    output_root_dir=validated_session.session_spec.output_root_dir,
                    session_name=validated_session.session_spec.session_name,
                    cookies_file=validated_session.session_spec.cookies_file,
                    submit_callback=submit,
                    event_callback=event_callback,
                    session_id=session_id,
                )
                if not running_futures:
                    if pending_reruns:
                        for item_index in list(pending_reruns.keys()):
                            if item_index in running_indexes:
                                continue
                            results[item_index] = None
                            submit(pending_reruns.pop(item_index))
                        continue
                    time.sleep(0.05)
                    continue
                completed_futures, _ = wait(
                    tuple(running_futures.keys()),
                    timeout=0.10,
                    return_when=FIRST_COMPLETED,
                )
                for future in completed_futures:
                    validated_item = running_futures.pop(future)
                    running_indexes.discard(validated_item.item_index)
                    results[validated_item.item_index] = future.result()
                    if validated_item.item_index in pending_reruns:
                        results[validated_item.item_index] = None
                        submit(pending_reruns.pop(validated_item.item_index))
                    self._emit_session_progress(
                        event_callback,
                        session_id,
                        results,
                        len(validated_session.items),
                    )
        return [item for item in results if item is not None]

    def _drain_rerun_requests(
        self,
        rerun_queue: Optional[queue.Queue],
        running_indexes,
        pending_reruns: Dict[int, ValidatedSessionItem],
        results: List[Optional[ItemProcessResult]],
        output_root_dir,
        session_name: Optional[str],
        cookies_file,
        submit_callback,
        event_callback: EventCallback,
        session_id: str,
    ) -> None:
        if rerun_queue is None:
            return
        while True:
            try:
                rerun_request = rerun_queue.get_nowait()
            except queue.Empty:
                return
            item_index, item_spec = rerun_request
            try:
                validated_item = self._validate_rerun_item(
                    item_spec=item_spec,
                    item_index=item_index,
                    output_root_dir=output_root_dir,
                    session_name=session_name,
                    cookies_file=cookies_file,
                )
            except SessionValidationError as exc:
                self._emit(
                    event_callback,
                    SessionEvent(
                        event_type="item_failed",
                        session_id=session_id,
                        item_index=item_index,
                        row_id=item_spec.row_id,
                        status="failed",
                        message=str(exc),
                    ),
                )
                self._emit_session_progress(event_callback, session_id, results, len(results))
                continue
            if item_index in running_indexes:
                pending_reruns[item_index] = validated_item
                continue
            results[item_index] = None
            submit_callback(validated_item)

    def _validate_rerun_item(
        self,
        item_spec: SessionItemSpec,
        item_index: int,
        output_root_dir,
        session_name: Optional[str],
        cookies_file,
    ) -> ValidatedSessionItem:
        validated_session = self.session_validator.validate(
            SessionSpec(
                items=[item_spec],
                output_root_dir=output_root_dir,
                session_name=session_name,
                cookies_file=cookies_file,
            )
        )
        validated_item = validated_session.items[0]
        validated_item.item_index = item_index
        validated_item.row_id = item_spec.row_id
        return validated_item

    def _emit_session_progress(
        self,
        event_callback: EventCallback,
        session_id: str,
        items: List[Optional[ItemProcessResult]],
        total_items: int,
    ) -> None:
        completed_items = len([item for item in items if item is not None and item.status == "completed"])
        failed_items = len([item for item in items if item is not None and item.status == "failed"])
        processed_items = len([item for item in items if item is not None])
        self._emit(
            event_callback,
            SessionEvent(
                event_type="session_progress",
                session_id=session_id,
                status="running",
                message="Session progress updated.",
                payload={
                    "completed_items": completed_items,
                    "failed_items": failed_items,
                    "processed_items": processed_items,
                    "total_items": total_items,
                },
            ),
        )

    def _queue_has_pending_items(self, rerun_queue: Optional[queue.Queue]) -> bool:
        if rerun_queue is None:
            return False
        try:
            return not rerun_queue.empty()
        except NotImplementedError:
            return False

    def rerun_item_for_review(
        self,
        session_result: SessionResult,
        item_spec: SessionItemSpec,
        item_index: int,
        event_callback: EventCallback = None,
    ) -> SessionResult:
        if session_result.artifacts is None or session_result.artifacts.session_dir is None:
            raise EditorError("Session review workspace is unavailable.")
        session_workspace = SessionWorkspace(
            root_dir=session_result.artifacts.session_dir,
            items_dir=session_result.artifacts.session_dir / "items",
        )
        validated_session = self.session_validator.validate(
            SessionSpec(
                items=[item_spec],
                output_root_dir=session_workspace.root_dir.parent,
                session_name=session_result.summary.get("session_name"),
                cookies_file=None,
            )
        )
        validated_item = validated_session.items[0]
        validated_item.item_index = item_index
        validated_item.row_id = item_spec.row_id
        rerun_result = self.item_runner.run(
            session_result.session_id,
            validated_item,
            session_workspace,
            event_callback=event_callback,
        )
        session_result.items[item_index] = rerun_result
        session_result.status = self._final_status(session_result.items)
        session_result.summary = self._build_summary(
            session_id=session_result.session_id,
            session_name=session_result.summary.get("session_name"),
            status=session_result.status,
            started_at=session_result.summary.get("started_at", self._now()),
            finished_at=self._now(),
            items=session_result.items,
            warnings=session_result.warnings,
        )
        session_result.summary["review_required"] = True
        if session_result.artifacts is not None:
            session_result.artifacts.is_finalized = False
            session_result.artifacts.titles_path = None
        return session_result

    def finalize_reviewed_session(
        self,
        session_result: SessionResult,
        event_callback: EventCallback = None,
    ) -> SessionResult:
        if session_result.artifacts is None or session_result.artifacts.session_dir is None:
            raise EditorError("Session review workspace is unavailable.")
        session_workspace = SessionWorkspace(
            root_dir=session_result.artifacts.session_dir,
            items_dir=session_result.artifacts.session_dir / "items",
        )
        recorder = PipelineRecorder(session_result.session_id, run_label="session_finalize", logger=self.logger)
        session_result.status = self._final_status(session_result.items)
        session_result.summary = self._build_summary(
            session_id=session_result.session_id,
            session_name=session_result.summary.get("session_name"),
            status=session_result.status,
            started_at=session_result.summary.get("started_at", self._now()),
            finished_at=self._now(),
            items=session_result.items,
            warnings=session_result.warnings,
        )
        session_result.summary["review_required"] = False
        artifacts = self.services.artifact_exporter.export_session(
            session_workspace,
            session_result.summary,
            recorder,
            items=session_result.items,
        )
        session_result.artifacts = artifacts
        self._emit(
            event_callback,
            SessionEvent(
                event_type="session_finalized",
                session_id=session_result.session_id,
                status=session_result.status,
                message="Session outputs were approved and saved.",
                payload=session_result.summary,
            ),
        )
        return session_result

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
            "source_title": item.metadata.get("source_title"),
            "product_image_path": str(item.product_image_path) if item.product_image_path else None,
            "output_dir": str(item.output_dir),
            "final_video_path": str(item.artifacts.final_video_path) if item.artifacts.final_video_path else None,
            "final_audio_path": str(item.artifacts.final_audio_path) if item.artifacts.final_audio_path else None,
            "metadata_path": str(item.artifacts.metadata_path) if item.artifacts.metadata_path else None,
            "process_log_path": str(item.artifacts.process_log_path) if item.artifacts.process_log_path else None,
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

    def _checkpoint_license(self) -> None:
        if self.license_checkpoint is not None:
            self.license_checkpoint()
