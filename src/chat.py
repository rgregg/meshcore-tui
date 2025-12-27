from abc import ABC, abstractmethod
import asyncio
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer, VerticalScroll, Horizontal, HorizontalScroll, Vertical
from textual.widgets import Input, Markdown, Static, Collapsible, LoadingIndicator, ListView, ListItem, Label, Header
from textual.screen import Screen
from data import (
    BaseContainerItem,
    BaseMessage,
    BaseDataProvider,
    FakeDataProvider,
    DataUpdate,
    ChannelMessage,
    UserMessage,
    MeshCoreChatProvider,
)
from dialog import PromptDialog
from footer import ConnectionStatusFooter

# class Content(VerticalScroll, can_focus=False):
#     """Non focusable vertical scroll."""


class ChannelListViewItem(ListItem):
    item: BaseContainerItem
    def __init__(self, item: BaseContainerItem):
        self.log(f"ChannelListViewItem received: {item}")
        super().__init__(Label(item.display_text))
        self.item = item

class MessageListViewItem(ListItem):
    message: BaseMessage
    def __init__(self, message: BaseMessage):
        super().__init__(Label(f"{message.sender}: {message.text}"))
        self.message = message

class BaseChatScreen(Screen):
    """Implements a Screen with a split view"""
    channel_items: list[ChannelListViewItem]
    left_pane_title = "bad_screen_type"
    message_items = list[MessageListViewItem]()
    selected_container: BaseContainerItem | None = None
    _data_provider: BaseDataProvider | None = None

    CSS_PATH = "chat.tcss"
    NAV_BINDINGS = [
        Binding("1", "open_channels", "Channels"),
        Binding("2", "open_chats", "Chats"),
        Binding("s", "open_settings", "Settings"),
    ]
    BINDINGS = NAV_BINDINGS

    def __init__(self) -> None:
        super().__init__()
        self.selected_container = None

    def compose(self) -> ComposeResult:
        self.channel_items = [ChannelListViewItem(c) for c in self.get_data_containers()]
        #yield Header()
        with Horizontal():
            with Vertical(id="LeftPane"):
                yield Label(self.left_pane_title, classes="PaneTitle", id="LeftPaneTitle")
                yield ListView(*self.channel_items,id="LeftPaneListView")
            with Vertical(id="RightPane"):
                yield Label("Loading", classes="PaneTitle", id="RightPaneTitle")
                yield ListView(*self.message_items, id="MessageList")
                yield Input(id="InputTextBox", classes="ChatTextBox", placeholder="Send a message", max_length=160)
                yield LoadingIndicator(id="LoadingIndicator")
        yield ConnectionStatusFooter()

    async def on_mount(self) -> None:
        await self._maybe_select_initial_container()

    @abstractmethod
    def get_data_containers(self) -> list[BaseContainerItem]:
        pass
    
    @abstractmethod
    def get_data_container_by_name(self, name:str) -> BaseContainerItem | None:
        pass

    @abstractmethod
    def get_data_container_items(self, container: BaseContainerItem) -> list[BaseMessage]:
        pass

    @abstractmethod
    def send_message(self, text:str) -> None:
        pass

    async def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        list_item = event.item
        if not hasattr(list_item, 'item'):
            return
        channel = list_item.item
        self.log(f"Select LeftPane: {channel.name}")
        await self.run_action("select_channel('" + channel.name + "')")

    async def action_select_channel(self, name: str) -> None:
        """Selects a new channel and updates the UI"""
        self.log(f"You selected: {name}")
        channel = self.get_data_container_by_name(name)
        if channel is None:
            self.log(f"Channel {name} was not found")
            return
        self.selected_container = channel

        # Update the title box
        label = self.query_one("#RightPaneTitle")
        if label:
            label.content = channel.display_text
            self.log(f"Set title to '{channel.display_text}'")
        else:
            self.log("No label object found in select_channel")
        
        self._focus_container(channel)

        self.set_loader_visible(True)
        self.message_items.clear()
        await self.run_action(f"reload_channel_data('{name}')")

    @property
    def message_listview(self) -> ListView:
        return self.query_one("#MessageList")
    
    @property
    def container_listview(self) -> ListView:
        return self.query_one("#LeftPaneListView")

    @property
    def data_provider(self) -> BaseDataProvider:
        if self._data_provider is None:
            self._data_provider = self.create_data_provider()
        return self._data_provider

    def create_data_provider(self) -> BaseDataProvider:
        if getattr(self.app, "use_fake_data", False):
            return FakeDataProvider(self.on_data_update)
        raise RuntimeError(
            "Real data provider must be supplied when not running with --fake-data"
        )
    
    def set_loader_visible(self, visibile: bool) -> None:
        loader = self.query_one("#LoadingIndicator")
        if loader:
            loader.visible = visibile

    async def action_reload_channel_data(self, name: str) -> None:
        """Updates the list view for the messages with the latest content"""
        self.log(f"Reloading messages for {name}")

        listview = self.message_listview
        listview.clear()
        channel = self.get_data_container_by_name(name)
        if channel is None:
            self.set_loader_visible(False)
            return
        items = [MessageListViewItem(m) for m in self.get_data_container_items(channel)]
        listview.extend(items)
        self.set_loader_visible(False)

    async def on_input_submitted(self, event:Input.Submitted):
        self.log(f"Input received: {event.value}")
        if not self.selected_container:
            self.log("Ignoring message send because no chat/channel is selected")
            return
        self.send_message(event.value)
        event.input.clear()

    def on_data_update(self, event:DataUpdate):
        if event.update_type == "add" and event.item is None:
            # New channel/chat container
            new_channel = ChannelListViewItem(event.container)
            self.container_listview.append(new_channel)
            if self.selected_container is None:
                self._focus_container(event.container)
                asyncio.create_task(self.action_select_channel(event.container.name))
            return

        if event.container == self.selected_container and event.update_type == "add" and event.item:
            new_item = MessageListViewItem(event.item)
            self.message_listview.append(new_item)

    def _focus_container(self, container: BaseContainerItem) -> None:
        """Move the left list selection to the provided container if it exists."""
        listview = self.container_listview
        for idx, child in enumerate(listview.children):
            if getattr(child, "item", None) == container:
                listview.index = idx
                break

    async def _maybe_select_initial_container(self) -> None:
        if self.selected_container is not None:
            return
        containers = self.get_data_containers()
        if not containers:
            return
        first = containers[0]
        self._focus_container(first)
        await self.action_select_channel(first.name)

    def _notify_error(self, message: str) -> None:
        """Show a toast notification for errors."""
        app = getattr(self, "app", None)
        if app:
            app.notify(message, severity="error")
        else:
            self.log(message)

    def action_open_channels(self) -> None:
        self.app.switch_mode("channel")

    def action_open_chats(self) -> None:
        self.app.switch_mode("chat")

    def action_open_settings(self) -> None:
        self.app.switch_mode("settings")


class ChannelChatScreen(BaseChatScreen):
    left_pane_title = "Channels"
    BINDINGS = BaseChatScreen.NAV_BINDINGS + [
        Binding("a", "add_channel", "Add channel"),
        Binding("d", "delete_channel", "Remove channel")
    ]
    
    def __init__(self):
        super().__init__()

    def send_message(self, text):
        channel = self.selected_container
        if channel is None:
            return
        provider = self.data_provider
        if isinstance(provider, MeshCoreChatProvider):
            service = getattr(self.app, "mesh_service", None)
            if not service or not service.is_connected:
                self._notify_error("MeshCore radio offline. Unable to send channel message.")
                return
            if channel.index is None:
                self._notify_error(f"Channel {channel.name} isn't provisioned on the radio.")
                return
        success = provider.send_message(ChannelMessage(channel, text, None, provider.current_user))
        if not success and isinstance(provider, MeshCoreChatProvider):
            self._notify_error("Failed to send message to MeshCore channel.")
    def get_data_containers(self):
        return self.data_provider.get_channels()
    def get_data_container_items(self, container):
        return self.data_provider.get_messages_for_channel(container)
    def get_data_container_by_name(self, name):
        return self.data_provider.get_channel_by_name(name)

    def action_delete_channel(self):
        if isinstance(self.data_provider, MeshCoreChatProvider):
            self.log("Channel removal not supported for live MeshCore data")
            return
        screen = PromptDialog(f"Are you sure you want to remove channel {self.selected_container.name}?")
        def callback(result):
            if result:
                # Remove the current channel
                self.data_provider.remove_container(self.selected_container)
        self.app.push_screen(screen, callback)

    def create_data_provider(self) -> BaseDataProvider:
        if getattr(self.app, "use_fake_data", False):
            return super().create_data_provider()
        service = getattr(self.app, "mesh_service", None)
        if not service:
            raise RuntimeError("MeshCore service unavailable for channel provider")
        return MeshCoreChatProvider(self.on_data_update, service, "channels")
        

class UserChatScreen(BaseChatScreen):
    left_pane_title = "Chats"
    BINDINGS = BaseChatScreen.NAV_BINDINGS + [
        Binding("a", "add_chat", "New chat"),
        Binding("d", "delete_chat", "Delete chat")
    ]

    def send_message(self, text):
        sender = self.data_provider.current_user
        receiver = self.selected_container
        if receiver is None:
            return
        self.log(f"Sending message {text} from {sender} to {receiver}")
        self.data_provider.send_message(UserMessage(text, None, sender, receiver))
    def get_data_containers(self):
        return self.data_provider.get_users()
    def get_data_container_items(self, container):
        return self.data_provider.get_messages_for_user(container)
    def get_data_container_by_name(self, name):
        return self.data_provider.get_user_by_name(name)
    
    def action_delete_chat(self):
        if isinstance(self.data_provider, MeshCoreChatProvider):
            self.log("Chat removal not supported for live MeshCore data")
            return
        screen = PromptDialog(f"Are you sure you want to remove chat with {self.selected_container.name}?")
        def callback(result):
            if result:
                # Remove the current channel
                self.data_provider.remove_container(self.selected_container)
        self.app.push_screen(screen, callback)

    def create_data_provider(self) -> BaseDataProvider:
        if getattr(self.app, "use_fake_data", False):
            return super().create_data_provider()
        service = getattr(self.app, "mesh_service", None)
        if not service:
            raise RuntimeError("MeshCore service unavailable for chat provider")
        return MeshCoreChatProvider(self.on_data_update, service, "users")
