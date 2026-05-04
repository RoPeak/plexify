from pathlib import Path

from plexify.infer import InferredItem
from plexify.ui_controller import VideoReviewItem, VideoUIConfig, VideoUIController


def test_refine_search_keeps_original_inferred_item(monkeypatch, tmp_path: Path) -> None:
    controller = VideoUIController(VideoUIConfig(incoming=tmp_path, library=tmp_path))
    original = InferredItem(path=tmp_path / "Big Hero 6" / "A1_t00.mkv", media_type="movie", title="Big Hero 6")
    state = VideoReviewItem(item=original, search_query="big hero 6", cache_key="movie|big-hero-6")
    controller.items = [state]
    seen: dict[str, str] = {}

    def fake_load(item_state, *, lookup_item=None, **_kwargs):
        seen["lookup_title"] = lookup_item.title if lookup_item is not None else item_state.item.title

    monkeypatch.setattr(controller, "_load_video_candidates", fake_load)

    controller.refine_search(0, "Big Hero")

    assert controller.items[0].item.title == "Big Hero 6"
    assert controller.items[0].lookup_title == "Big Hero"
    assert seen["lookup_title"] == "Big Hero"
