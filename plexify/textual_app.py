from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Checkbox, Footer, Header, Input, Label, Static

from .paths import PathOverlapError, ensure_non_overlapping_paths
from .ui import format_path
from .ui_controller import (
    ApplyResultState,
    MusicUIConfig,
    MusicUIController,
    PreviewState,
    VideoUIConfig,
    VideoUIController,
)


def _parse_bool(value: bool) -> bool:
    return bool(value)


def _parse_manual_title(value: str) -> tuple[str, int | None]:
    text = value.strip()
    if not text:
        return "", None
    if text.endswith(")") and "(" in text:
        base, _, tail = text.rpartition("(")
        year_text = tail[:-1].strip()
        if year_text.isdigit() and len(year_text) == 4:
            return base.strip(), int(year_text)
    return text, None


@dataclass
class UIWorkflowState:
    workflow: str
    controller: VideoUIController | MusicUIController


class HomeScreen(Screen):
    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="home"):
            yield Static("Plexify UI", id="title")
            yield Static("Choose a workflow to start a review session.", id="subtitle")
            with Horizontal(classes="button-row"):
                yield Button("Video", id="video")
                yield Button("Music", id="music")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "video":
            self.app.push_screen(ConfigScreen("video"))
        elif event.button.id == "music":
            self.app.push_screen(ConfigScreen("music"))


class ConfigScreen(Screen):
    def __init__(self, workflow: str) -> None:
        super().__init__()
        self.workflow = workflow

    def compose(self) -> ComposeResult:
        defaults = self.app.get_defaults(self.workflow)
        yield Header(show_clock=False)
        with Vertical(id="config"):
            yield Static(f"{self.workflow.title()} Configuration", classes="screen-title")
            if self.workflow == "video":
                yield Label("Incoming")
                yield Input(str(defaults[0] or ""), id="path-one")
                yield Label("Library")
                yield Input(str(defaults[1] or ""), id="path-two")
                yield Label("Extensions")
                yield Input(".mkv,.mp4,.avi,.m4v,.mov,.ts", id="extensions")
                yield Label("Min confidence")
                yield Input("0.90", id="min-confidence")
                yield Checkbox("Apply mode", id="apply-mode", value=False)
                yield Checkbox("Copy files", id="copy-mode", value=True)
                yield Checkbox("Use cache", id="use-cache", value=True)
                yield Checkbox("Offline", id="offline", value=False)
            else:
                yield Label("Source")
                yield Input(str(defaults[0] or ""), id="path-one")
                yield Label("Library")
                yield Input(str(defaults[1] or ""), id="path-two")
                yield Label("Extensions")
                yield Input("flac,mp3,m4a", id="extensions")
                yield Checkbox("Apply mode", id="apply-mode", value=False)
                yield Checkbox("Copy files", id="copy-mode", value=True)
                yield Checkbox("Verify with MusicBrainz", id="verify", value=True)
                yield Checkbox("Keep artwork", id="keep-art", value=True)
                yield Checkbox("Keep cue", id="keep-cue", value=False)
                yield Checkbox("Keep log", id="keep-log", value=False)
                yield Checkbox("Offline", id="offline", value=False)
            yield Static("", id="config-error")
            with Horizontal(classes="button-row"):
                yield Button("Back", id="back")
                yield Button("Scan", id="scan", variant="primary")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back":
            self.app.pop_screen()
            return
        if event.button.id != "scan":
            return
        error = self.query_one("#config-error", Static)
        try:
            workflow_state = self.app.build_workflow_state(
                self.workflow,
                path_one=self.query_one("#path-one", Input).value,
                path_two=self.query_one("#path-two", Input).value,
                extensions=self.query_one("#extensions", Input).value,
                min_confidence=self.query_one("#min-confidence", Input).value if self.workflow == "video" else "",
                apply_mode=self.query_one("#apply-mode", Checkbox).value,
                copy_mode=self.query_one("#copy-mode", Checkbox).value,
                use_cache=self.query_one("#use-cache", Checkbox).value if self.workflow == "video" else True,
                verify=self.query_one("#verify", Checkbox).value if self.workflow == "music" else True,
                keep_art=self.query_one("#keep-art", Checkbox).value if self.workflow == "music" else True,
                keep_cue=self.query_one("#keep-cue", Checkbox).value if self.workflow == "music" else False,
                keep_log=self.query_one("#keep-log", Checkbox).value if self.workflow == "music" else False,
                offline=self.query_one("#offline", Checkbox).value,
            )
        except ValueError as exc:
            error.update(str(exc))
            return
        except PathOverlapError as exc:
            error.update(exc.issue.reason)
            return
        error.update("")
        self.app.push_screen(ScanScreen(workflow_state))


class ScanScreen(Screen):
    def __init__(self, workflow_state: UIWorkflowState) -> None:
        super().__init__()
        self.workflow_state = workflow_state

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="scan"):
            yield Static("Preparing review queue...", classes="screen-title")
            yield Static("Scanning and loading candidates in a background worker.", id="scan-status")
        yield Footer()

    def on_mount(self) -> None:
        self.run_scan()

    @work(thread=True)
    def run_scan(self) -> None:
        self.workflow_state.controller.scan()
        self.app.call_from_thread(self._scan_complete)

    def _scan_complete(self) -> None:
        self.app.push_screen(ReviewScreen(self.workflow_state))


class ReviewScreen(Screen):
    BINDINGS = [
        ("j", "next_item", "Next"),
        ("k", "prev_item", "Prev"),
        ("a", "accept", "Accept"),
        ("s", "skip", "Skip"),
        ("p", "preview", "Preview"),
    ]

    def __init__(self, workflow_state: UIWorkflowState) -> None:
        super().__init__()
        self.workflow_state = workflow_state
        self.current_index = 0
        self.current_candidate_index = 0

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="review"):
            with Vertical(id="queue-pane"):
                yield Static("", id="queue")
                with Horizontal(classes="button-row"):
                    yield Button("Prev", id="prev-item")
                    yield Button("Next", id="next-item")
            with Vertical(id="detail-pane"):
                yield Static("", id="details")
                yield Static("", id="candidates")
                yield Input("", id="action-input", placeholder="Search query or manual title (Title or Title (2001))")
                with Horizontal(classes="button-row"):
                    yield Button("Cand -", id="prev-candidate")
                    yield Button("Cand +", id="next-candidate")
                    yield Button("More", id="next-page")
                    yield Button("Accept", id="accept", variant="primary")
                    yield Button("Skip", id="skip")
                    yield Button("Preview", id="preview")
                with Horizontal(classes="button-row"):
                    yield Button("Search", id="search")
                    if self.workflow_state.workflow == "video":
                        yield Button("Switch TV/Movie", id="switch")
                        yield Button("Manual", id="manual")
                        yield Button("Apply To Folder", id="folder")
                        yield Button("Apply To Title", id="title-group")
                    else:
                        yield Button("Filename", id="filename")
                        yield Button("Filename Titles", id="filename-titles")
                        yield Button("Order", id="order")
                        yield Button("Skip Album", id="skip-album")
                        yield Button("Skip Remaining", id="skip-remaining")
                yield Static("", id="review-error")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_view()

    def action_next_item(self) -> None:
        self._move_item(1)

    def action_prev_item(self) -> None:
        self._move_item(-1)

    def action_accept(self) -> None:
        self._accept()

    def action_skip(self) -> None:
        self._skip()

    def action_preview(self) -> None:
        self._preview()

    def _move_item(self, delta: int) -> None:
        length = len(self._items())
        if not length:
            return
        self.current_index = (self.current_index + delta) % length
        self.current_candidate_index = 0
        self.refresh_view()

    def _items(self) -> list[Any]:
        controller = self.workflow_state.controller
        return controller.items if self.workflow_state.workflow == "video" else controller.albums

    def refresh_view(self) -> None:
        queue = self.query_one("#queue", Static)
        details = self.query_one("#details", Static)
        candidates = self.query_one("#candidates", Static)
        items = self._items()
        if not items:
            queue.update("No items discovered.")
            details.update("")
            candidates.update("")
            return
        lines = []
        for index, item in enumerate(items):
            marker = ">" if index == self.current_index else " "
            if self.workflow_state.workflow == "video":
                label = item.item.path.name
                suffix = f" [{item.status_label}]"
            else:
                label = item.album.source.name
                suffix = f" [{item.status_label}]"
            lines.append(f"{marker} {index + 1}. {label}{suffix}")
        queue.update("\n".join(lines))
        current = items[self.current_index]
        if self.workflow_state.workflow == "video":
            detail_lines = [
                f"Path: {format_path(current.item.path)}",
                f"Type: {current.item.media_type}",
                f"Title: {current.item.title}",
                f"Season/Episode: {current.item.season}/{current.item.episode}",
                f"Search: {current.search_query}",
                f"Status: {current.status_label}",
                f"Cache: {current.cache_context}",
                f"Auto-selectable: {current.auto_selectable}",
            ]
            if current.unresolved_reason:
                detail_lines.append(f"Unresolved: {current.unresolved_reason}")
            if current.warning:
                detail_lines.append(f"Warning: {current.warning}")
            candidate_lines = []
            if current.manual_candidate is not None:
                candidate_lines.append(f"Manual: {current.manual_candidate.title}")
            for index, candidate in enumerate(current.candidate_states):
                marker = ">" if index == self.current_candidate_index else " "
                chosen = " [accepted]" if current.selected_candidate_index == index else ""
                candidate_lines.append(f"{marker} {candidate.summary}{chosen}")
            if not candidate_lines:
                candidate_lines.append("No candidates.")
        else:
            detail_lines = [
                f"Album: {current.album.source.name}",
                f"Artist: {current.album.artist}",
                f"Title: {current.album.album}",
                f"Tracks: {len(current.album.tracks)}",
                f"Status: {current.status_label}",
                f"Cached decision: {current.cached_decision or 'none'}",
                f"Decision: {current.decision or 'pending'}",
            ]
            if current.cached_reason:
                detail_lines.append(f"Cached reason: {current.cached_reason}")
            if current.fallback_reason:
                detail_lines.append(f"Fallback: {current.fallback_reason}")
            if current.unresolved_reason:
                detail_lines.append(f"Unresolved: {current.unresolved_reason}")
            if current.warning:
                detail_lines.append(f"Warning: {current.warning}")
            candidate_lines = []
            for index, candidate in enumerate(current.candidate_states):
                marker = ">" if index == self.current_candidate_index else " "
                chosen = " [selected]" if current.selected_candidate_index == index else ""
                candidate_lines.append(
                    f"{marker} {candidate.artist} - {candidate.title} ({candidate.year or 'Unknown'}) [{candidate.score:.2f}]{chosen}"
                )
            if not candidate_lines:
                candidate_lines.append("No candidates.")
        details.update("\n".join(detail_lines))
        candidates.update("\n".join(candidate_lines))

    def _accept(self) -> None:
        controller = self.workflow_state.controller
        if self.workflow_state.workflow == "video":
            controller.accept_candidate(self.current_index, self.current_candidate_index)
        else:
            controller.select_candidate(self.current_index, self.current_candidate_index)
        self.refresh_view()

    def _skip(self) -> None:
        controller = self.workflow_state.controller
        if self.workflow_state.workflow == "video":
            controller.skip_item(self.current_index)
        else:
            controller.skip_album(self.current_index)
        self.refresh_view()

    def _preview(self) -> None:
        preview = self.workflow_state.controller.build_preview()
        self.app.push_screen(PreviewScreen(self.workflow_state, preview))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        error = self.query_one("#review-error", Static)
        error.update("")
        controller = self.workflow_state.controller
        if button_id == "prev-item":
            self._move_item(-1)
        elif button_id == "next-item":
            self._move_item(1)
        elif button_id == "prev-candidate":
            self.current_candidate_index = max(0, self.current_candidate_index - 1)
            self.refresh_view()
        elif button_id == "next-candidate":
            current = self._items()[self.current_index]
            states = current.candidate_states
            if states:
                self.current_candidate_index = min(len(states) - 1, self.current_candidate_index + 1)
            self.refresh_view()
        elif button_id == "next-page" and self.workflow_state.workflow == "video":
            controller.next_page(self.current_index)
            self.current_candidate_index = 0
            self.refresh_view()
        elif button_id == "accept":
            self._accept()
        elif button_id == "skip":
            self._skip()
        elif button_id == "preview":
            self._preview()
        elif button_id == "search" and self.workflow_state.workflow == "video":
            query = self.query_one("#action-input", Input).value
            controller.refine_search(self.current_index, query)
            self.current_candidate_index = 0
            self.refresh_view()
        elif button_id == "switch" and self.workflow_state.workflow == "video":
            current = controller.items[self.current_index]
            target = "movie" if current.item.media_type == "tv" else "tv"
            controller.switch_media_type(self.current_index, target)
            self.current_candidate_index = 0
            self.refresh_view()
        elif button_id == "manual" and self.workflow_state.workflow == "video":
            title, year = _parse_manual_title(self.query_one("#action-input", Input).value)
            if not title:
                error.update("Enter a manual title first.")
                return
            controller.manual_select(self.current_index, title=title, year=year)
            self.refresh_view()
        elif button_id == "folder" and self.workflow_state.workflow == "video":
            controller.apply_choice_to_folder(self.current_index)
            self.refresh_view()
        elif button_id == "title-group" and self.workflow_state.workflow == "video":
            controller.apply_choice_to_title_group(self.current_index)
            self.refresh_view()
        elif button_id == "filename" and self.workflow_state.workflow == "music":
            controller.choose_filename_fallback(self.current_index)
            self.refresh_view()
        elif button_id == "filename-titles" and self.workflow_state.workflow == "music":
            controller.choose_filename_titles_fallback(self.current_index)
            self.refresh_view()
        elif button_id == "order" and self.workflow_state.workflow == "music":
            controller.choose_order_fallback(self.current_index)
            self.refresh_view()
        elif button_id == "skip-album" and self.workflow_state.workflow == "music":
            controller.skip_album(self.current_index)
            self.refresh_view()
        elif button_id == "skip-remaining" and self.workflow_state.workflow == "music":
            controller.skip_remaining(self.current_index)
            self.refresh_view()


class PreviewScreen(Screen):
    def __init__(self, workflow_state: UIWorkflowState, preview: PreviewState) -> None:
        super().__init__()
        self.workflow_state = workflow_state
        self.preview = preview

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="preview"):
            yield Static("Preview", classes="screen-title")
            yield Static("", id="preview-summary")
            yield Static("", id="preview-plans")
            with Horizontal(classes="button-row"):
                yield Button("Back", id="back")
                yield Button("Apply", id="apply", variant="primary")
        yield Footer()

    def on_mount(self) -> None:
        sample = self.preview.plans[:8]
        sample_lines = [f"{format_path(plan.source)} -> {format_path(plan.destination)}" for plan in sample]
        if self.preview.unresolved_items:
            sample_lines.append("")
            sample_lines.append("Unresolved:")
            sample_lines.extend(self.preview.unresolved_items[:5])
        if self.preview.warnings:
            sample_lines.append("")
            sample_lines.extend(f"Warning: {warning}" for warning in self.preview.warnings[:5])
        self.query_one("#preview-summary", Static).update("\n".join(self.preview.summary_lines))
        self.query_one("#preview-plans", Static).update("\n".join(sample_lines) if sample_lines else "No plans generated.")
        self.query_one("#apply", Button).disabled = not self.preview.can_apply

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back":
            self.app.pop_screen()
            return
        if event.button.id == "apply":
            if self.workflow_state.controller.config.mode == "apply":
                self.app.push_screen(ConfirmApplyScreen(self.workflow_state, self.preview))
            else:
                self.app.push_screen(ApplyScreen(self.workflow_state, self.preview))


class ConfirmApplyScreen(Screen):
    def __init__(self, workflow_state: UIWorkflowState, preview: PreviewState) -> None:
        super().__init__()
        self.workflow_state = workflow_state
        self.preview = preview

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="preview"):
            yield Static("Confirm Apply", classes="screen-title")
            yield Static("This will modify files on disk. Continue?", id="confirm-apply-text")
            with Horizontal(classes="button-row"):
                yield Button("Back", id="back")
                yield Button("Apply Now", id="confirm-apply", variant="error")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back":
            self.app.pop_screen()
        elif event.button.id == "confirm-apply":
            self.app.push_screen(ApplyScreen(self.workflow_state, self.preview))


class ApplyScreen(Screen):
    def __init__(self, workflow_state: UIWorkflowState, preview: PreviewState) -> None:
        super().__init__()
        self.workflow_state = workflow_state
        self.preview = preview

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="apply"):
            yield Static("Applying plan...", classes="screen-title")
            yield Static("Running in background worker.", id="apply-status")
        yield Footer()

    def on_mount(self) -> None:
        self.run_apply()

    @work(thread=True)
    def run_apply(self) -> None:
        result = self.workflow_state.controller.apply_preview(self.preview)
        self.app.call_from_thread(self._apply_complete, result)

    def _apply_complete(self, result: ApplyResultState) -> None:
        self.app.push_screen(ResultScreen(result))


class ResultScreen(Screen):
    def __init__(self, result: ApplyResultState) -> None:
        super().__init__()
        self.result = result

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="result"):
            yield Static("Run Result", classes="screen-title")
            yield Static("", id="result-summary")
            with Horizontal(classes="button-row"):
                yield Button("Home", id="home")
                yield Button("Quit", id="quit")
        yield Footer()

    def on_mount(self) -> None:
        lines = list(self.result.summary_lines)
        if self.result.warnings:
            lines.append("")
            lines.extend(f"Warning: {warning}" for warning in self.result.warnings[:10])
        if self.result.result.errors:
            lines.append("")
            lines.extend(f"Error: {error}" for error in self.result.result.errors[:10])
        self.query_one("#result-summary", Static).update("\n".join(lines))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "home":
            self.app.switch_mode("home")
        elif event.button.id == "quit":
            self.app.exit()


class PlexifyTextualApp(App[None]):
    CSS = """
    #home, #config, #scan, #preview, #result, #apply { padding: 1 2; }
    #review { height: 1fr; }
    #queue-pane { width: 30%; padding: 1; border: solid $accent; }
    #detail-pane { width: 70%; padding: 1; border: solid $panel; }
    .button-row { height: auto; margin-top: 1; }
    .screen-title { content-align: center middle; text-style: bold; margin-bottom: 1; }
    #title { content-align: center middle; text-style: bold; height: 3; }
    #subtitle { content-align: center middle; margin-bottom: 1; }
    """

    MODES = {"home": HomeScreen}

    def on_mount(self) -> None:
        self.switch_mode("home")

    def get_defaults(self, workflow: str) -> tuple[Path | None, Path | None]:
        from . import cli

        return cli._wizard_defaults("video" if workflow == "video" else "music")

    def build_workflow_state(
        self,
        workflow: str,
        *,
        path_one: str,
        path_two: str,
        extensions: str,
        min_confidence: str,
        apply_mode: bool,
        copy_mode: bool,
        use_cache: bool,
        verify: bool,
        keep_art: bool,
        keep_cue: bool,
        keep_log: bool,
        offline: bool,
    ) -> UIWorkflowState:
        source = Path(path_one.strip())
        library = Path(path_two.strip())
        if not source or not str(source).strip():
            raise ValueError("A source/incoming folder is required.")
        if not library or not str(library).strip():
            raise ValueError("A library folder is required.")
        if not source.exists() or not source.is_dir():
            raise ValueError("Source/incoming folder must exist.")
        if library.exists() and library.is_file():
            raise ValueError("Library path must be a folder.")
        ensure_non_overlapping_paths(source, library, label_source="Source", label_library="Library")
        if workflow == "video":
            try:
                confidence = float(min_confidence or "0.90")
            except ValueError as exc:
                raise ValueError("Min confidence must be a number.") from exc
            controller = VideoUIController(
                VideoUIConfig(
                    incoming=source,
                    library=library,
                    mode="apply" if apply_mode else "dry-run",
                    copy_mode=_parse_bool(copy_mode),
                    extensions=extensions,
                    min_confidence=confidence,
                    use_cache=_parse_bool(use_cache),
                    offline=_parse_bool(offline),
                )
            )
        else:
            controller = MusicUIController(
                MusicUIConfig(
                    source=source,
                    library=library,
                    mode="apply" if apply_mode else "dry-run",
                    copy_mode=_parse_bool(copy_mode),
                    extensions=extensions,
                    verify=_parse_bool(verify),
                    keep_art=_parse_bool(keep_art),
                    keep_cue=_parse_bool(keep_cue),
                    keep_log=_parse_bool(keep_log),
                    offline=_parse_bool(offline),
                )
            )
        return UIWorkflowState(workflow=workflow, controller=controller)


def run_textual_ui() -> None:
    PlexifyTextualApp().run()


if __name__ == "__main__":
    run_textual_ui()
