"""Data models returned by the Facebook Graph API client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class FacebookPage:
    """A Facebook Page managed by the connected user."""

    page_id: str
    name: str
    category: str
    access_token: str
    tasks: tuple[str, ...]
    picture_url: str

    @classmethod
    def from_graph_data(cls, data: Mapping[str, Any]) -> "FacebookPage":
        picture = data.get("picture")
        picture_url = ""
        if isinstance(picture, Mapping):
            picture_data = picture.get("data")
            if isinstance(picture_data, Mapping):
                picture_url = str(picture_data.get("url") or "").strip()

        raw_tasks = data.get("tasks")
        tasks = (
            tuple(str(task).strip() for task in raw_tasks if str(task).strip())
            if isinstance(raw_tasks, (list, tuple))
            else ()
        )
        return cls(
            page_id=str(data.get("id") or "").strip(),
            name=str(data.get("name") or "").strip(),
            category=str(data.get("category") or "").strip(),
            access_token=str(data.get("access_token") or "").strip(),
            tasks=tasks,
            picture_url=picture_url,
        )
