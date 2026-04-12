"""Final artifact writing for per-item metadata/process logs and session summaries."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Dict, List, Optional

from auto_tiktok_editor.app.recorder import PipelineRecorder
from auto_tiktok_editor.app.workspace import ItemWorkspace, SessionWorkspace
from auto_tiktok_editor.domain.models import ItemProcessResult, JobArtifacts, SessionArtifacts


class ArtifactExporter(object):
    def export_item(
        self,
        workspace: ItemWorkspace,
        final_video_path: Optional[Path],
        final_audio_path: Optional[Path],
        metadata: Dict,
        recorder: PipelineRecorder,
    ) -> JobArtifacts:
        return JobArtifacts(
            output_dir=workspace.root_dir,
            final_video_path=final_video_path,
            final_audio_path=final_audio_path,
            video_title_path=None,
            metadata_path=None,
            process_log_path=None,
        )

    def export_session(
        self,
        workspace: SessionWorkspace,
        summary: Dict,
        recorder: PipelineRecorder,
        items: Optional[List[ItemProcessResult]] = None,
    ) -> SessionArtifacts:
        titles_path = workspace.root_dir / "session_titles.txt"
        titles = []
        for item in summary.get("items", []):
            title = str((item or {}).get("source_title") or "").strip()
            if title:
                titles.append(title)
        titles_path.write_text(("\n\n".join(titles) + "\n") if titles else "", encoding="utf-8")
        if items:
            self._materialize_session_deliverables(workspace, items)
        return SessionArtifacts(
            session_dir=workspace.root_dir,
            summary_path=None,
            session_log_path=None,
            titles_path=titles_path,
        )

    def _materialize_session_deliverables(self, workspace: SessionWorkspace, items: List[ItemProcessResult]) -> None:
        ordered_items = sorted(items, key=lambda item: item.item_index)
        deliverable_index = 0
        for item in ordered_items:
            deliverable_path = None  # type: Optional[Path]
            if item.status == "completed" and item.artifacts.final_video_path and item.artifacts.final_video_path.exists():
                deliverable_index += 1
                deliverable_path = workspace.root_dir / ("%03d_final_video.mp4" % deliverable_index)
                if deliverable_path.exists():
                    deliverable_path.unlink()
                shutil.move(str(item.artifacts.final_video_path), str(deliverable_path))
            item.output_dir = workspace.root_dir
            item.artifacts.output_dir = workspace.root_dir
            item.artifacts.final_video_path = deliverable_path
            item.artifacts.final_audio_path = None
            item.artifacts.metadata_path = None
            item.artifacts.process_log_path = None
        if workspace.items_dir.exists():
            shutil.rmtree(workspace.items_dir, ignore_errors=True)
