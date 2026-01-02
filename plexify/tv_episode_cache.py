from __future__ import annotations

from typing import Dict, List

import requests

from .sources import tvmaze


class EpisodeCache:
    def __init__(self) -> None:
        self._episodes_by_show: Dict[int, List[tvmaze.TVMazeEpisode]] = {}

    def get_episodes(self, show_id: int, session: requests.Session) -> list[tvmaze.TVMazeEpisode]:
        if show_id in self._episodes_by_show:
            return self._episodes_by_show[show_id]
        episodes = tvmaze.fetch_episodes(int(show_id), session=session)
        self._episodes_by_show[show_id] = episodes or []
        return self._episodes_by_show[show_id]
