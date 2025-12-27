from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import argparse
import sys

from textual import getters, work
from textual.app import App, SystemCommand
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
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
    root_logger.setLevel(logging.INFO)
    root_logger.propagate = False
    root_logger.addHandler(file_handler)
    meshcore_logger = logging.getLogger("meshcore")
    meshcore_logger.handlers.clear()
    meshcore_logger.propagate = True
    setattr(configure_logging, "_configured", True)  # type: ignore[attr-defined]


configure_logging()

class MeshCoreTuiApp(App):
    """Main entry point for MeshCore TUI App"""  
    CSS_PATH = "app.tcss"
    TITLE = "MeshCore Companion Terminal Interface"

    def __init__(self, *, use_fake_data: bool = False) -> None:
        super().__init__()
        self.config_service = ConfigService()
        self.use_fake_data = use_fake_data
        self.mesh_service = None if use_fake_data else MeshCoreService(self.config_service)

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
        if self.mesh_service:
            async def _start_meshcore() -> None:
                try:
                    await self.mesh_service.start()
                except Exception as exc:  # pragma: no cover - requires device
                    self.log(f"MeshCore connection failed: {exc}")
                    self.notify(f"MeshCore connection failed: {exc}", severity="error", timeout=10)

            asyncio.create_task(_start_meshcore())

        await self.switch_mode("chat")

    async def on_shutdown(self) -> None:
        if self.mesh_service:
            try:
                await self.mesh_service.stop()
            except Exception as exc:  # pragma: no cover - hardware specific
                self.log(f"MeshCore shutdown failed: {exc}")

    def get_system_commands(self, screen: "Screen") -> list[SystemCommand]:
        commands = list(super().get_system_commands(screen))
        commands.append(
            SystemCommand(
                "Refresh Channels",
                "Fetch the latest channels from the MeshCore companion",
                self._command_refresh_channels,
            )
        )
        commands.append(
            SystemCommand(
                "Reconnect MeshCore",
                "Restart the connection to the MeshCore companion",
                self._command_reconnect_meshcore,
            )
        )
        return commands

    async def _command_refresh_channels(self) -> None:
        service = getattr(self, "mesh_service", None)
        if not service:
            self.notify("MeshCore service unavailable.", severity="error")
            return
        try:
            await service.refresh_channels()
            self.notify("Channel list refreshed.", title="MeshCore", severity="information")
        except Exception as exc:  # pragma: no cover - requires live device
            self.notify(f"Channel refresh failed: {exc}", severity="error")

    async def _command_reconnect_meshcore(self) -> None:
        service = getattr(self, "mesh_service", None)
        if not service:
            self.notify("MeshCore service unavailable.", severity="error")
            return
        await service.stop()
        try:
            await service.start()
            self.notify("MeshCore reconnected.", title="MeshCore", severity="information")
        except Exception as exc:  # pragma: no cover - requires live device
            self.notify(f"MeshCore reconnect failed: {exc}", severity="error", timeout=10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MeshCore TUI")
    parser.add_argument(
        "--fake-data",
        action="store_true",
        help="Use in-memory fake data instead of connecting to MeshCore",
    )
    args = parser.parse_args()
    app = MeshCoreTuiApp(use_fake_data=args.fake_data)
    app.run()
