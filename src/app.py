from __future__ import annotations

try:
    import httpx
except ImportError:
    raise ImportError("Please install httpx with 'pip install httpx' ")

from textual import getters, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer, VerticalScroll, Horizontal, HorizontalScroll
from textual.widgets import Input, Markdown, Static, Collapsible
from textual.screen import Screen
from home import HomeScreen
from chat import ChatScreen

class MeshCoreTuiApp(App):
    """Main entry point for MeshCore TUI App"""  
    CSS_PATH = "app.tcss"
    

    MODES = {
        "home": HomeScreen,
        "chat": ChatScreen,
        "channel": ChatScreen,
    }

    DEFAULT_MODE = "home"
    BINDINGS = [
        Binding(
            "h",
            "app.switch_mode('home')",
            "Home",
            tooltip="Show the home screen"
        ),
        Binding(
            "c",
            "app.switch_mode('chat')",
            "Chats",
            tooltip="Show the chat screen"
        ),
        Binding(
            "h",
            "app.switch_mode('channel')",
            "Channels",
            tooltip="Show the channels screen"
        )
    ]


if __name__ == "__main__":
    app = MeshCoreTuiApp()
    app.run()