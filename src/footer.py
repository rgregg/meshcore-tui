"""Shared footer widgets."""
from __future__ import annotations

import logging

from textual.app import ComposeResult
from textual.widgets import Footer, Static
from textual.css.query import NoMatches
from textual.timer import Timer

logger = logging.getLogger(__name__)


class ConnectionStatusFooter(Footer):
    """Footer that shows MeshCore connection status before key bindings."""

    def __init__(self, *, status_id: str = "ConnectionStatusLabel", **kwargs) -> None:
        super().__init__(**kwargs)
        self._status_id = status_id
        self._last_status_text: str | None = None
        self._status_timer: Timer | None = None
        self._missing_logged = False

    def compose(self) -> ComposeResult:  # type: ignore[override]
        yield Static("MeshCore: starting...", id=self._status_id)
        yield Static("Ctrl+1 Channels • Ctrl+2 Chats", classes="ShortcutHint")
        yield from super().compose()

    def on_mount(self) -> None:
        super().on_mount()
        self._status_timer = self.set_interval(1.0, self._on_status_timer)
        logger.debug("ConnectionStatusFooter mounted; starting status updates.")
        self._update_status()

    def on_unmount(self) -> None:
        if self._status_timer:
            self._status_timer.stop()
            self._status_timer = None
        self._missing_logged = False

    def _on_status_timer(self) -> None:
        self._update_status()

    def _update_status(self) -> None:
        try:
            widget = self.query_one(f"#{self._status_id}", Static)
        except NoMatches:
            if not self._missing_logged:
                logger.debug("Status label missing; skipping update.")
                self._missing_logged = True
            return
        self._missing_logged = False
        app = getattr(self, "app", None)
        if app is None:
            return
        service = getattr(app, "mesh_service", None)
        use_fake = getattr(self.app, "use_fake_data", False)
        if use_fake:
            text = "MeshCore: fake data mode"
            classes = {"-connecting"}
        elif service:
            status = service.status
            message = status.message or ("Connected" if service.is_connected else "Idle")
            progress = ""
            if status.total:
                progress = f" ({status.current}/{status.total})"
            text = f"MeshCore: {message}{progress}"
            classes = {self._class_for_state(status.state, service.is_connected)}
        else:
            text = "MeshCore: unavailable"
            classes = {"-error"}
        if self._last_status_text != text:
            logger.debug("Footer status update: %s", text)
            self._last_status_text = text
        widget.update(text)
        widget.remove_class("-connected")
        widget.remove_class("-connecting")
        widget.remove_class("-error")
        for cls in classes:
            widget.add_class(cls)

    def _class_for_state(self, state: str, connected: bool) -> str:
        if state == "connected" or connected:
            return "-connected"
        if state == "error":
            return "-error"
        return "-connecting"
