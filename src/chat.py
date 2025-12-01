from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer, VerticalScroll, Horizontal, HorizontalScroll, Vertical
from textual.widgets import Input, Markdown, Static, Collapsible, Footer, LoadingIndicator, ListView, ListItem, Label
from textual.screen import Screen

class Content(VerticalScroll, can_focus=False):
    """Non focusable vertical scroll."""

class ChatScreen(Screen):
    DEFAULT_CSS = """
    PageScreen {
        width: 100%;
        height: 1fr;
        overflow-y: auto;        
    }
    #ChannelsTitle {
        text-align: center;
        border-bottom: panel white;
    }
    #ChannelList {
        width: 25%;
        overflow-y: auto;
        border-right: panel white;
    }
    #MessageList {
        width: 75%;
        overflow-y: auto;
    }
    """

    BINDINGS = [
        Binding("a", "add_channel", "Add ahannel"),
        Binding("d", "delete_channel", "Remove ahannel")
    ]

    def __init__(self) -> None:
        super().__init__()

    def compose(self) -> ComposeResult:
        channels = [
            ListItem(Label("Public")),
            ListItem(Label("#bot")),
            ListItem(Label("#edm"))
        ]

        with Horizontal():
            with Vertical(id="ChannelList"):
                yield Label("Channels", id="ChannelTitle")
                yield ListView(*channels)
            with VerticalScroll(id="MessageList"):
                yield Label("Loading latest messages...", id="ChannelName")
                yield LoadingIndicator(id="LoadingIndicator")
        yield Footer()

    async def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        item = event.item
        channel = item.query_one(Label).content
        self.log(f"Select_channel: {channel}")
        await self.run_action("select_channel('" + channel + "')")

    async def action_add_channel(self) -> None:
        self.log(f"Add channel action invoked")

    async def action_delete_channel(self) -> None:
        list_view = self.query_one(ListView)
        item = list_view.highlighted_child
        if item:
            channel = item.children[0].content
            self.log(f"Remove channel '{channel}' action invoked")

    def action_select_channel(self, channel_name: str) -> None:
        """Selects a new channel and updates the UI"""
        self.log(f"You selected: {channel_name}")
        label = self.query_one("#ChannelName")
        if label:
            label.content = "Channel " + channel_name
        loader = self.query_one("#LoadingIndicator")
        if loader:
            loader.visible = False