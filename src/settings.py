from textual.app import ComposeResult, ScreenStackError
from textual.binding import Binding
from textual.containers import VerticalScroll, Vertical
from textual.widgets import Input, Markdown, Static, Button
from textual.screen import Screen

from services.config_service import ConfigService
from footer import ConnectionStatusFooter

class Content(VerticalScroll, can_focus=False):
    """Non focusable vertical scroll."""


class SafeInput(Input):
    """Input that ignores selection updates until a screen is available."""

    def _watch_selection(self, selection) -> None:  # type: ignore[override]
        try:
            super()._watch_selection(selection)
        except ScreenStackError:
            # Happens while the screen stack is still preparing.
            return

class SettingsScreen(Screen):
    BINDINGS = [
        Binding("1", "open_channels", "Channels"),
        Binding("2", "open_chats", "Chats"),
        Binding("ctrl+s", "save_config", "Save configuration"),
    ]

    DEFAULT_CSS = """
    SettingsScreen {
        width: 100%;
        height: 1fr;
    }

    #SettingsForm Content {
        padding: 1 2;
    }

    .section-label {
        text-style: bold;
        margin-top: 1;
    }

    .field-label {
        color: $text-muted;
        margin-top: 1;
    }

    .spaced-input {
        margin-bottom: 1;
    }

    #SaveStatus {
        color: $text-success;
        padding-top: 1;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._config_service: ConfigService | None = None
        self._config = None

    def compose(self) -> ComposeResult:
        with Vertical(id="SettingsForm"):
            with Content():
                yield Markdown("# MeshCore-TUI Settings")
                yield Static("User profile", classes="section-label")
                yield Static("Display name", classes="field-label")
                yield SafeInput(
                    id="DisplayNameInput",
                    classes="spaced-input",
                )
                yield Static("Mesh node ID", classes="field-label")
                yield SafeInput(
                    id="NodeIdInput",
                    classes="spaced-input",
                )

                yield Static("Companion connection", classes="section-label")
                yield Static("Transport (bluetooth/usb)", classes="field-label")
                yield SafeInput(
                    id="TransportInput",
                    classes="spaced-input",
                )
                yield Static("Companion endpoint or IP", classes="field-label")
                yield SafeInput(
                    id="EndpointInput",
                    classes="spaced-input",
                )
                yield Static("Device identifier", classes="field-label")
                yield SafeInput(
                    id="DeviceInput",
                    classes="spaced-input",
                )
                yield Static("Channel refresh seconds", classes="field-label")
                yield SafeInput(
                    id="RefreshSecondsInput",
                    classes="spaced-input",
                )

                yield Static("UI", classes="section-label")
                yield Static("Theme", classes="field-label")
                yield SafeInput(
                    id="ThemeInput",
                    classes="spaced-input",
                )
                yield Static("Log level", classes="field-label")
                yield SafeInput(
                    id="LogLevelInput",
                    classes="spaced-input",
                )

                yield Button("Save changes", id="SaveButton")
                yield Button("Go to Channels (1)", id="OpenChannelsButton")
                yield Button("Go to Chats (2)", id="OpenChatsButton")
                yield Static("", id="SaveStatus")
        yield ConnectionStatusFooter()

    def on_mount(self) -> None:
        if not self._config_service:
            self._config_service = getattr(self.app, "config_service", ConfigService())
        self._config = self._config_service.config
        self._populate_form()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "SaveButton":
            self.action_save_config()
        elif event.button.id == "OpenChannelsButton":
            self.action_open_channels()
        elif event.button.id == "OpenChatsButton":
            self.action_open_chats()

    def action_save_config(self) -> None:
        """Persist settings to config/config.yaml."""
        if not self._config_service or not self._config:
            self.log("Configuration service unavailable")
            return
        self._sync_inputs_to_config()
        self._config_service.save(self._config)
        status = self.query_one("#SaveStatus", Static)
        status.update("Configuration saved.")

    def action_open_channels(self) -> None:
        self.app.switch_mode("channel")

    def action_open_chats(self) -> None:
        self.app.switch_mode("chat")

    def _sync_inputs_to_config(self) -> None:
        if not self._config:
            return
        self._config.meshcore.user.display_name = self._clean_value("#DisplayNameInput")
        self._config.meshcore.user.node_id = self._clean_value("#NodeIdInput")
        self._config.meshcore.companion.transport = self._clean_value("#TransportInput", fallback="bluetooth")
        self._config.meshcore.companion.endpoint = self._clean_value("#EndpointInput")
        self._config.meshcore.companion.device = self._clean_value("#DeviceInput", fallback="auto")
        refresh_value = self._clean_value("#RefreshSecondsInput", fallback=str(self._config.meshcore.companion.channel_refresh_seconds))
        try:
            self._config.meshcore.companion.channel_refresh_seconds = int(refresh_value)
        except ValueError:
            pass
        self._config.ui.theme = self._clean_value("#ThemeInput")
        self._config.ui.log_level = self._clean_value("#LogLevelInput", fallback="info")

    def _populate_form(self) -> None:
        self.query_one("#DisplayNameInput", Input).value = self._config.meshcore.user.display_name
        self.query_one("#NodeIdInput", Input).value = self._config.meshcore.user.node_id
        self.query_one("#TransportInput", Input).value = self._config.meshcore.companion.transport
        self.query_one("#EndpointInput", Input).value = self._config.meshcore.companion.endpoint
        self.query_one("#DeviceInput", Input).value = self._config.meshcore.companion.device
        self.query_one("#RefreshSecondsInput", Input).value = str(self._config.meshcore.companion.channel_refresh_seconds)
        self.query_one("#ThemeInput", Input).value = self._config.ui.theme
        self.query_one("#LogLevelInput", Input).value = self._config.ui.log_level

    def _clean_value(self, selector: str, fallback: str | None = None) -> str:
        widget = self.query_one(selector, Input)
        value = widget.value.strip()
        if not value and fallback is not None:
            return fallback
        return value
