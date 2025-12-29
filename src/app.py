from __future__ import annotations

import asyncio
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import argparse
import sys
import contextlib

from textual import getters, work, events
from textual.app import App, SystemCommand
from textual.command import CommandPalette
from textual.binding import Binding
from settings import SettingsScreen
from chat import ChannelChatScreen, UserChatScreen
from dialog import ShutdownDialog
from loading import LoadingScreen
from services.config_service import ConfigService
from services.meshcore_service import MeshCoreService
from services.data_store import ChatDataStore, MeshCoreStoreBridge

LOG_DIR = Path("logs")
LOG_FILE = LOG_DIR / "meshcore-tui.log"


def configure_logging(level: str | None = None) -> None:
    """Write textual/meshcore logs to logs/meshcore-tui.log."""
    if getattr(configure_logging, "_configured", False):  # type: ignore[attr-defined]
        return
    log_level = getattr(logging, (level or "INFO").upper(), logging.INFO)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=5 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    file_handler.setLevel(log_level)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(formatter)
    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
    root_logger.setLevel(log_level)
    root_logger.propagate = False
    root_logger.addHandler(file_handler)
    meshcore_logger = logging.getLogger("meshcore")
    meshcore_logger.handlers.clear()
    meshcore_logger.propagate = True
    setattr(configure_logging, "_configured", True)  # type: ignore[attr-defined]

class MeshCoreTuiApp(App):
    """Main entry point for MeshCore TUI App"""  
    CSS_PATH = "app.tcss"
    TITLE = "MeshCore Companion Terminal Interface"

    def __init__(self, *, use_fake_data: bool = False) -> None:
        super().__init__()
        self.config_service = ConfigService()
        configure_logging(self.config_service.config.ui.log_level)
        self.use_fake_data = use_fake_data
        self.mesh_service: MeshCoreService | None = None
        self.data_store: ChatDataStore | None = None
        self.store_bridge: MeshCoreStoreBridge | None = None
        self._backend_task: asyncio.Task | None = None

    MODES = {
        "settings": SettingsScreen,
        "chat": UserChatScreen,
        "channel": ChannelChatScreen,
        "loading": LoadingScreen,
    }

    DEFAULT_MODE = "loading"
    BINDINGS = [
        Binding(
            "1",
            "app.switch_mode('channel')",
            "Channels",
            tooltip="Show the channels screen"
        ),
        Binding(
            "ctrl+1",
            "app.switch_mode('channel')",
            "Channels",
            tooltip="Show the channels screen (global)"
        ),
        Binding(
            "2",
            "app.switch_mode('chat')",
            "Chats",
            tooltip="Show the chat screen"
        ),
        Binding(
            "ctrl+2",
            "app.switch_mode('chat')",
            "Chats",
            tooltip="Show the chat screen (global)"
        ),
        Binding(
            "s",
            "app.switch_mode('settings')",
            "Settings",
            tooltip="Configure the app settings"
        ),
    ]

    async def on_load(self, event: events.Load) -> None:
        if self.use_fake_data:
            self._schedule_chat_mode()
            return
        self._backend_task = asyncio.create_task(self._initialize_backend())


    async def action_quit(self) -> None:
        self.log("Quit requested; stopping MeshCore service.")
        shutdown_dialog = ShutdownDialog()
        self.push_screen(shutdown_dialog)
        try:
            if self._backend_task and not self._backend_task.done():
                self._backend_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._backend_task
            if self.mesh_service:
                try:
                    await self.mesh_service.stop()
                except Exception as exc:  # pragma: no cover - hardware specific
                    self.log(f"MeshCore shutdown failed: {exc}")
                else:
                    self.log("MeshCore service stopped.")
        finally:
            shutdown_dialog.dismiss(None)
        await super().action_quit()
        self.log("Textual app shut down, clearing console.")
        self.console.clear()
        self.console.show_cursor()

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
        commands.append(
            SystemCommand(
                "Advertise Presence",
                "Send an advert packet so nearby nodes learn about us",
                self._command_send_advert,
            )
        )
        commands.append(
            SystemCommand(
                "Advertise (Flood)",
                "Flood adverts for quicker discovery at the cost of airtime",
                self._command_send_advert_flood,
            )
        )
        return commands

    async def _command_refresh_channels(self) -> None:
        service = getattr(self, "mesh_service", None)
        if not service:
            self.notify("MeshCore service unavailable.", severity="error")
            return
        try:
            await service.refresh_contacts_and_channels()
            self.notify("Contacts and channels refreshed.", title="MeshCore", severity="information")
        except Exception as exc:  # pragma: no cover - requires live device
            self.notify(f"Channel refresh failed: {exc}", severity="error")

    async def _command_reconnect_meshcore(self) -> None:
        service = getattr(self, "mesh_service", None)
        if not service:
            self.notify("MeshCore service unavailable.", severity="error")
            return
        self.notify("Reconnecting to MeshCore…", severity="information")

        async def _reconnect() -> None:
            try:
                await service.stop()
            except Exception as exc:  # pragma: no cover - hardware specific
                self.log(f"MeshCore stop failed during reconnect: {exc}")
            try:
                await service.start()
            except Exception as exc:  # pragma: no cover - requires live device
                self.log(f"MeshCore reconnect failed: {exc}")
                self.notify(f"MeshCore reconnect failed: {exc}", severity="error", timeout=10)
                return
            self.notify("MeshCore reconnected.", title="MeshCore", severity="information")

        asyncio.create_task(_reconnect())

    async def _command_send_advert(self) -> None:
        await self._send_advert(flood=False)

    async def _command_send_advert_flood(self) -> None:
        await self._send_advert(flood=True)

    async def _send_advert(self, *, flood: bool) -> None:
        service = getattr(self, "mesh_service", None)
        if not service:
            self.notify("MeshCore service unavailable.", severity="error")
            return
        if not service.is_connected:
            self.notify("MeshCore not connected; cannot advertise.", severity="warning")
            return
        try:
            await service.send_advert(flood=flood)
        except Exception as exc:  # pragma: no cover - device specific
            self.notify(f"Advert failed: {exc}", severity="error")
        else:
            message = "Flood advert sent." if flood else "Advert sent."
            self.notify(message, title="MeshCore", severity="information")

    async def _initialize_backend(self) -> None:
        try:
            data_store = await asyncio.to_thread(ChatDataStore)
        except Exception as exc:  # pragma: no cover - disk issues
            self.log(f"Chat data store failed to initialize: {exc}")

            def _notify_failure() -> None:
                self.notify(
                    f"Chat state failed to load: {exc}",
                    severity="error",
                    timeout=10,
                )

            self.call_after_refresh(_notify_failure)
            return
        self.data_store = data_store
        self.mesh_service = MeshCoreService(self.config_service)
        self.store_bridge = MeshCoreStoreBridge(self.mesh_service, self.data_store)

        self._schedule_chat_mode()
        if self.mesh_service:
            asyncio.create_task(self._start_meshcore())

    async def _start_meshcore(self) -> None:
        service = self.mesh_service
        if not service:
            return
        exc_ref: Exception | None = None
        try:
            await service.start()
        except Exception as exc:  # pragma: no cover - requires device
            self.log(f"MeshCore connection failed: {exc}")
            exc_ref = exc

        if exc_ref:
            message = f"MeshCore connection failed: {exc_ref}"

            def _notify() -> None:
                self.notify(message, severity="error", timeout=10)

            self.call_after_refresh(_notify)

    def _schedule_chat_mode(self) -> None:
        async def _switch() -> None:
            await self.switch_mode("chat")
            with contextlib.suppress(KeyError):
                self.remove_mode("loading")

        self.call_after_refresh(lambda: asyncio.create_task(_switch()))


if __name__ == "__main__":
    print("Loading meshcore-tui...\n")
    parser = argparse.ArgumentParser(description="MeshCore TUI")
    parser.add_argument(
        "--fake-data",
        action="store_true",
        help="Use in-memory fake data instead of connecting to MeshCore",
    )
    args = parser.parse_args()
    app = MeshCoreTuiApp(use_fake_data=args.fake_data)
    app.run()
