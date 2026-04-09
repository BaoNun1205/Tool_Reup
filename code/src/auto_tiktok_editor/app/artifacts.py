"""Final artifact writing for per-item metadata/process logs and session summaries."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

from auto_tiktok_editor.app.recorder import PipelineRecorder
from auto_tiktok_editor.app.workspace import ItemWorkspace, SessionWorkspace
from auto_tiktok_editor.domain.models import JobArtifacts, SessionArtifacts


class ArtifactExporter(object):
    def export_item(
        self,
        workspace: ItemWorkspace,
        final_video_path: Optional[Path],
        final_audio_path: Optional[Path],
        metadata: Dict,
        recorder: PipelineRecorder,
    ) -> JobArtifacts:
        metadata_path = workspace.root_dir / "job_metadata.json"
        process_log_path = workspace.root_dir / "process_log.txt"
        metadata_path.write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        process_log_path.write_text(recorder.to_log_text(), encoding="utf-8")
        return JobArtifacts(
            output_dir=workspace.root_dir,
            final_video_path=final_video_path,
            final_audio_path=final_audio_path,
            metadata_path=metadata_path,
            process_log_path=process_log_path,
        )

    def export_session(
        self,
        workspace: SessionWorkspace,
        summary: Dict,
        recorder: PipelineRecorder,
    ) -> SessionArtifacts:
        summary_path = workspace.root_dir / "session_summary.json"
        session_log_path = workspace.root_dir / "session_log.txt"
        summary_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        session_log_path.write_text(recorder.to_log_text(), encoding="utf-8")
        return SessionArtifacts(
            session_dir=workspace.root_dir,
            summary_path=summary_path,
            session_log_path=session_log_path,
        )
