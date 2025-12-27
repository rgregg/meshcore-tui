from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from textual import getters, work
from textual.app import App
from textual.command import CommandPalette
from textual.binding import Binding
# from textual.containers import ScrollableContainer, VerticalScroll, Horizontal, HorizontalScroll
# from textual.widgets import Input, Markdown, Static, Collapsible
# from textual.screen import Screen
from settings import SettingsScreen
from chat import ChannelChatScreen, UserChatScreen
from services.config_service import ConfigService
from services.meshcore_service import MeshCoreService

LOG_DIR = Path("logs")
LOG_FILE = LOG_DIR / "meshcore-tui.log"


def configure_logging() -> None:
    """Write textual/meshcore logs to logs/meshcore-tui.log."""
    if getattr(configure_logging, "_configured", False):  # type: ignore[attr-defined]
        return
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(formatter)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)
    setattr(configure_logging, "_configured", True)  # type: ignore[attr-defined]


configure_logging()

class MeshCoreTuiApp(App):
    """Main entry point for MeshCore TUI App"""  
    CSS_PATH = "app.tcss"
    TITLE = "MeshCore Companion Terminal Interface"

    def __init__(self) -> None:
        super().__init__()
        self.config_service = ConfigService()
        self.mesh_service = MeshCoreService(self.config_service)

    MODES = {
        "settings": SettingsScreen,
        "chat": UserChatScreen,
        "channel": ChannelChatScreen,
    }

    DEFAULT_MODE = "settings"
    BINDINGS = [
        Binding(
            "1",
            "app.switch_mode('channel')",
            "Channels",
            tooltip="Show the channels screen"
        ),
        Binding(
            "2",
            "app.switch_mode('chat')",
            "Chats",
            tooltip="Show the chat screen"
        ),
        Binding(
            "s",
            "app.switch_mode('settings')",
            "Settings",
            tooltip="Configure the app settings"
        ),
    ]

    async def on_mount(self) -> None:
        async def _start_meshcore() -> None:
            try:
                await self.mesh_service.start()
            except Exception as exc:  # pragma: no cover - requires device
                self.log(f"MeshCore connection failed: {exc}")

        asyncio.create_task(_start_meshcore())



if __name__ == "__main__":
    app = MeshCoreTuiApp()
    app.run()
