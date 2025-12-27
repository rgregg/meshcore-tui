"""Shared footer widgets."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Footer, Static


class ConnectionStatusFooter(Footer):
    """Footer that shows MeshCore connection status before key bindings."""

    def __init__(self, *, status_id: str = "ConnectionStatusLabel", **kwargs) -> None:
        super().__init__(**kwargs)
        self._status_id = status_id
        self._status_widget: Static | None = None

    def compose(self) -> ComposeResult:  # type: ignore[override]
        yield Static("MeshCore: starting...", id=self._status_id)
        yield from super().compose()

    def on_mount(self) -> None:
        super().on_mount()
        self._status_widget = self.query_one(f"#{self._status_id}", Static)
        self.set_interval(1.0, self._update_status)
        self._update_status()

    def _update_status(self) -> None:
        widget = self._status_widget
        if widget is None:
            return
        service = getattr(self.app, "mesh_service", None)
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
