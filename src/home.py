from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer, VerticalScroll, Vertical, Horizontal, HorizontalScroll
from textual.widgets import Input, Markdown, Static, Collapsible, Footer
from textual.screen import Screen

class Content(VerticalScroll, can_focus=False):
    """Non focusable vertical scroll."""

class HomeScreen(Screen):
    DEFAULT_CSS = """
    PageScreen {
        width: 100%;
        height: 1fr;
        overflow-y: auto;        
    }
    """

    def __init__(self) -> None:
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical():
            with Content():
                yield Markdown("# MeshCore-TUI\n\nTerminal interface for MeshCore companions.")
        yield Footer()
