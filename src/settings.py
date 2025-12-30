import asyncio

from textual.app import ComposeResult, ScreenStackError
from textual.binding import Binding
from textual.containers import VerticalScroll, Vertical, Horizontal
from textual.widgets import Input, Markdown, Static, Button, ListView, ListItem, Checkbox, Label
from textual.screen import Screen
from textual.css.query import NoMatches

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

class SettingsNavItem(ListItem):
    def __init__(self, tab_id: str, label: str, **kwargs) -> None:
        super().__init__(Label(label, classes="SettingsNavLabel"), **kwargs)
        self.tab_id = tab_id


class SettingsScreen(Screen):
    CSS_PATH = "settings.tcss"
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
        self._active_tab = "connection"
        self._tabs = {
            "connection": {"label": "Connection", "container_id": "ConnectionTab"},
            "app": {"label": "App", "container_id": "AppTab"},
            "device": {"label": "Device", "container_id": "DeviceTab"},
        }

    def compose(self) -> ComposeResult:
        with Horizontal(id="SettingsLayout"):
            with Vertical(id="SettingsNav"):
                yield Label("Settings", classes="SettingsNavTitle")
                nav_items = [
                    SettingsNavItem(tab_id, tab["label"], classes="SettingsNavItem")
                    for tab_id, tab in self._tabs.items()
                ]
                yield ListView(*nav_items, id="SettingsNavList")
            with Vertical(id="SettingsForm"):
                with Content():
                    with Vertical(id="ConnectionTab", classes="SettingsTab"):
                        yield Markdown("## Connection")
                        yield Static("Transport (bluetooth/serial)", classes="field-label")
                        yield SafeInput(id="TransportInput", classes="spaced-input")
                        yield Static("Companion endpoint or IP", classes="field-label")
                        yield SafeInput(id="EndpointInput", classes="spaced-input")
                        yield Static("Device identifier", classes="field-label")
                        yield SafeInput(id="DeviceInput", classes="spaced-input")
                        yield Static("Channel refresh seconds", classes="field-label")
                        yield SafeInput(id="RefreshSecondsInput", classes="spaced-input")
                    with Vertical(id="AppTab", classes="SettingsTab"):
                        yield Markdown("## Application")
                        yield Static("Theme", classes="field-label")
                        yield SafeInput(id="ThemeInput", classes="spaced-input")
                        yield Static("Log level", classes="field-label")
                        yield SafeInput(id="LogLevelInput", classes="spaced-input")
                        yield Static("Data directory", classes="field-label")
                        yield SafeInput(id="DataLocationInput", classes="spaced-input")
                    with Vertical(id="DeviceTab", classes="SettingsTab"):
                        yield Markdown("## Device")
                        yield Checkbox("Log raw mesh packets to file", id="LogPacketsCheckbox")
                        yield Static("Device Actions", classes="section-label")
                        yield Button("Refresh Contacts & Channels", id="DeviceRefreshButton")
                        yield Button("Reconnect MeshCore", id="DeviceReconnectButton")
                        yield Button("Send Advert", id="DeviceAdvertButton")
                        yield Button("Send Advert Flood", id="DeviceAdvertFloodButton")
                yield Button("Save changes", id="SaveButton")
                yield Button("Go to Channels (1)", id="OpenChannelsButton")
                yield Button("Go to Chats (2)", id="OpenChatsButton")
                yield Static("", id="SaveStatus")
        yield ConnectionStatusFooter()

    def on_mount(self) -> None:
        if not self._config_service:
            self._config_service = getattr(self.app, "config_service", ConfigService())
        self._config = self._config_service.config
        nav = self.query_one("#SettingsNavList", ListView)
        nav.index = list(self._tabs).index(self._active_tab)
        self._show_tab(self._active_tab)
        self._populate_form()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "SaveButton":
            self.action_save_config()
        elif event.button.id == "OpenChannelsButton":
            self.action_open_channels()
        elif event.button.id == "OpenChatsButton":
            self.action_open_chats()
        elif event.button.id == "DeviceRefreshButton":
            self._device_command_refresh()
        elif event.button.id == "DeviceReconnectButton":
            self._device_command_reconnect()
        elif event.button.id == "DeviceAdvertButton":
            self._device_command_advert(flood=False)
        elif event.button.id == "DeviceAdvertFloodButton":
            self._device_command_advert(flood=True)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.list_view.id != "SettingsNavList":
            return
        tab_id = getattr(event.item, "tab_id", None)
        if tab_id:
            self._show_tab(tab_id)

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
        self._config.meshcore.companion.transport = self._clean_value("#TransportInput", fallback="bluetooth")
        self._config.meshcore.companion.endpoint = self._clean_value("#EndpointInput")
        self._config.meshcore.companion.device = self._clean_value("#DeviceInput", fallback="auto")
        refresh_value = self._clean_value(
            "#RefreshSecondsInput",
            fallback=str(self._config.meshcore.companion.channel_refresh_seconds),
        )
        try:
            self._config.meshcore.companion.channel_refresh_seconds = int(refresh_value)
        except ValueError:
            pass
        self._config.app.theme = self._clean_value("#ThemeInput")
        self._config.app.log_level = self._clean_value("#LogLevelInput", fallback="info")
        self._config.app.data_location = self._clean_value(
            "#DataLocationInput",
            fallback=self._config.app.data_location,
        )
        log_packets_checkbox = self.query_one("#LogPacketsCheckbox", Checkbox)
        self._config.meshcore.log_packets = bool(log_packets_checkbox.value)

    def _populate_form(self) -> None:
        self.query_one("#TransportInput", Input).value = self._config.meshcore.companion.transport
        self.query_one("#EndpointInput", Input).value = self._config.meshcore.companion.endpoint
        self.query_one("#DeviceInput", Input).value = self._config.meshcore.companion.device
        self.query_one("#RefreshSecondsInput", Input).value = str(self._config.meshcore.companion.channel_refresh_seconds)
        self.query_one("#ThemeInput", Input).value = self._config.app.theme
        self.query_one("#LogLevelInput", Input).value = self._config.app.log_level
        self.query_one("#DataLocationInput", Input).value = self._config.app.data_location
        self.query_one("#LogPacketsCheckbox", Checkbox).value = self._config.meshcore.log_packets

    def _clean_value(self, selector: str, fallback: str | None = None) -> str:
        widget = self.query_one(selector, Input)
        value = widget.value.strip()
        if not value and fallback is not None:
            return fallback
        return value

    def _show_tab(self, tab_id: str) -> None:
        self._active_tab = tab_id
        for key, info in self._tabs.items():
            try:
                tab = self.query_one(f"#{info['container_id']}")
            except NoMatches:
                continue
            tab.visible = key == tab_id

    def _device_command_refresh(self) -> None:
        service = getattr(self.app, "mesh_service", None)
        if not service:
            self.notify("MeshCore service unavailable.", severity="error")
            return
        self.notify("Refreshing contacts and channels…", severity="information")
        async def _run() -> None:
            try:
                await service.refresh_contacts()
                await service.refresh_channels()
            except Exception as exc:  # pragma: no cover - device specific
                self.notify(f"Device refresh failed: {exc}", severity="error")
                return
            self.notify("Device refresh complete.", severity="information")
        asyncio.create_task(_run())

    def _device_command_reconnect(self) -> None:
        service = getattr(self.app, "mesh_service", None)
        if not service:
            self.notify("MeshCore service unavailable.", severity="error")
            return
        self.notify("Reconnecting MeshCore…", severity="information")
        async def _run() -> None:
            try:
                await service.stop()
                await service.start()
            except Exception as exc:  # pragma: no cover - device specific
                self.notify(f"Reconnect failed: {exc}", severity="error")
                return
            self.notify("MeshCore reconnected.", severity="information")
        asyncio.create_task(_run())

    def _device_command_advert(self, *, flood: bool) -> None:
        service = getattr(self.app, "mesh_service", None)
        if not service:
            self.notify("MeshCore service unavailable.", severity="error")
            return
        async def _run() -> None:
            try:
                await service.send_advert(flood=flood)
            except Exception as exc:  # pragma: no cover - device specific
                self.notify(f"Advert failed: {exc}", severity="error")
                return
            self.notify(
                "Flood advert sent." if flood else "Advert sent.",
                severity="information",
            )
        asyncio.create_task(_run())
