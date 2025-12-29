from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import LoadingIndicator, Static
from textual.screen import Screen


class LoadingScreen(Screen):
    """Simple splash screen shown while services initialize."""

    CSS_PATH = "loading.tcss"

    def compose(self) -> ComposeResult:
        with Vertical(id="LoadingScreen"):
            yield LoadingIndicator(id="LoadingSpinner")
            yield Static("Connecting to MeshCore…", id="LoadingMessage")
            yield Static("Preparing chat history and radio link.", id="LoadingSubtext")
