from abc import ABC, abstractmethod
import asyncio
from datetime import datetime, timedelta
import re
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import (
    ScrollableContainer,
    VerticalScroll,
    Horizontal,
    HorizontalScroll,
    Vertical,
)
from textual.widgets import (
    Input,
    Markdown,
    Static,
    Collapsible,
    LoadingIndicator,
    ListView,
    ListItem,
    Label,
    Header,
)
from textual.css.query import NoMatches
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

MENTION_PATTERN = re.compile(r"@\[[^\]]+\]")
SENDER_STYLE = "bold dodger_blue2"
MENTION_STYLE = "bold medium_spring_green"
TIMESTAMP_STYLE = "italic dim"

# class Content(VerticalScroll, can_focus=False):
#     """Non focusable vertical scroll."""


class ChannelListViewItem(ListItem):
    """List item wrapper for channels that can refresh its label."""

    item: BaseContainerItem

    def __init__(self, item: BaseContainerItem):
        super().__init__()
        self.item = item
        self._label = Label("", classes="ChannelLabel")
        self.refresh_title()

    def compose(self) -> ComposeResult:
        yield self._label

    def refresh_title(self) -> None:
        text = Text(self.item.display_text)
        if getattr(self.item, "unread_count", 0) > 0:
            text.stylize("bold")
        self._label.update(text)

class MessageListViewItem(ListItem):
    """Reusable list item that can refresh its content for different messages."""

    message: BaseMessage

    def __init__(self, message: BaseMessage):
        super().__init__()
        self.message = message
        self._label = Label("", classes="MessageText")
        self.update_message(message)

    def compose(self) -> ComposeResult:
        yield self._label

    def update_message(self, message: BaseMessage) -> None:
        """Refresh the rendered text to reflect the supplied message."""
        self.message = message
        self._label.update(self._format_message_text(message))

    def _format_message_text(self, message: BaseMessage) -> Text:
        sender = getattr(message, "sender", None)
        sender_name = str(sender).strip() if sender else ""
        text = Text()
        if sender_name and sender_name.lower() != "unknown sender":
            text.append(sender_name, style=SENDER_STYLE)
            text.append(": ")
        text += self._highlight_mentions(message.text or "")
        return text

    def _highlight_mentions(self, value: str) -> Text:
        result = Text()
        idx = 0
        for match in MENTION_PATTERN.finditer(value):
            start, end = match.span()
            if start > idx:
                result.append(value[idx:start])
            result.append(match.group(0), style=MENTION_STYLE)
            idx = end
        if idx < len(value):
            result.append(value[idx:])
        return result

class MessageDividerItem(ListItem):
    def __init__(self, label: str):
        text = Text(label, style=TIMESTAMP_STYLE)
        super().__init__(Label(text, classes="MessageDivider"))
        self.can_focus = False

class BaseChatScreen(Screen):
    """Implements a Screen with a split view"""
    channel_items: list[ChannelListViewItem]
    left_pane_title = "bad_screen_type"
    message_items = list[MessageListViewItem]()
    selected_container: BaseContainerItem | None = None
    _data_provider: BaseDataProvider | None = None

    CSS_PATH = "chat.tcss"
    MESSAGE_GAP = timedelta(minutes=15)
    MAX_MESSAGES = 50
    NAV_BINDINGS = [
        Binding("1", "open_channels", "Channels"),
        Binding("2", "open_chats", "Chats"),
        Binding("s", "open_settings", "Settings"),
    ]
    BINDINGS = NAV_BINDINGS + [
        Binding("i", "show_message_info", "Message info"),
        Binding("ctrl+r", "reply_message", "Reply"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.selected_container = None
        self._pending_data_updates: list[DataUpdate] = []
        self._message_widget_cache: dict[BaseMessage, MessageListViewItem] = {}
        self._load_generation = 0

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
        self._flush_pending_data_updates()

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
        if channel == self.selected_container:
            return
        self.log(f"Select LeftPane: {channel.name}")
        await self.action_select_channel(channel.name)

    async def action_select_channel(self, name: str) -> None:
        """Selects a new channel and updates the UI"""
        self.log(f"You selected: {name}")
        channel = self.get_data_container_by_name(name)
        if channel is None:
            self.log(f"Channel {name} was not found")
            return
        if self.selected_container == channel:
            return
        self.selected_container = channel
        self._clear_unread(channel)

        # Update the title box
        label = self.query_one("#RightPaneTitle")
        if label:
            label.content = channel.display_text
            self.log(f"Set title to '{channel.display_text}'")
        else:
            self.log("No label object found in select_channel")
        
        self._focus_container(channel)

        self.set_loader_visible(True)
        await self.action_reload_channel_data(name)

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

        self._load_generation += 1
        load_generation = self._load_generation
        listview = self.message_listview
        listview.clear()
        channel = self.get_data_container_by_name(name)
        if channel is None:
            self.set_loader_visible(False)
            return
        try:
            messages = await asyncio.to_thread(self._fetch_messages_for_container, channel)
        except Exception as exc:  # pragma: no cover - defensive
            self.log(f"Failed to load messages for {name}: {exc}")
            self.set_loader_visible(False)
            self._notify_error(f"Failed to load messages for {name}.")
            return
        if load_generation != self._load_generation or self.selected_container != channel:
            self.log(f"Stale load result for {name}; ignoring")
            return
        items = self._build_message_items(messages)
        listview.extend(items)
        self._scroll_messages_to_end()
        self.set_loader_visible(False)

    async def on_input_submitted(self, event:Input.Submitted):
        self.log(f"Input received: {event.value}")
        if not self.selected_container:
            self.log("Ignoring message send because no chat/channel is selected")
            return
        self.send_message(event.value)
        event.input.clear()

    def on_data_update(self, event:DataUpdate):
        if not self._ui_ready():
            self.log("UI not ready; queueing data update")
            self._pending_data_updates.append(event)
            return

        self.log(
            f"UI data update: type={event.update_type} container={getattr(event.container, 'name', getattr(event.container, 'display_name', 'unknown'))}"
        )
        self._apply_data_update(event)

    def _apply_data_update(self, event: DataUpdate) -> None:
        if event.update_type == "update" and event.item is None:
            self._refresh_container_label(event.container)
            return

        if event.update_type == "add" and event.item is None:
            # New channel/chat container
            new_channel = ChannelListViewItem(event.container)
            self.container_listview.append(new_channel)
            new_channel.refresh_title()
            if self.selected_container is None:
                self._focus_container(event.container)
                asyncio.create_task(self.action_select_channel(event.container.name))
            return

        if event.update_type == "add" and event.item:
            if event.container == self.selected_container:
                self._append_message_with_divider(event.item)
                self._scroll_messages_to_end()
                self._clear_unread(event.container)
            else:
                self._increment_unread(event.container)
            return

    def _refresh_container_label(self, container: BaseContainerItem) -> None:
        for child in self.container_listview.children:
            if getattr(child, "item", None) == container and hasattr(child, "refresh_title"):
                child.refresh_title()
                break

    def _increment_unread(self, container: BaseContainerItem) -> None:
        counter = getattr(container, "unread_count", None)
        if counter is None:
            return
        if hasattr(container, "increment_unread"):
            container.increment_unread()
        else:
            container.unread_count += 1
        self._refresh_container_label(container)

    def _clear_unread(self, container: BaseContainerItem) -> None:
        counter = getattr(container, "unread_count", None)
        if not counter:
            return
        if hasattr(container, "clear_unread"):
            container.clear_unread()
        else:
            container.unread_count = 0
        self._refresh_container_label(container)

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

    def _flush_pending_data_updates(self) -> None:
        if not self._pending_data_updates or not self._ui_ready():
            return
        pending = list(self._pending_data_updates)
        self._pending_data_updates.clear()
        for update in pending:
            self._apply_data_update(update)

    def _ui_ready(self) -> bool:
        try:
            self.query_one("#LeftPaneListView")
            self.query_one("#MessageList")
            return True
        except NoMatches:
            return False

    def _notify_error(self, message: str) -> None:
        """Show a toast notification for errors."""
        app = getattr(self, "app", None)
        if app:
            app.notify(message, severity="error")
        else:
            self.log(message)

    def _build_message_items(self, messages: list[BaseMessage]) -> list[ListItem]:
        items: list[ListItem] = []
        prev_ts: datetime | None = None
        for message in messages:
            ts = getattr(message, "timestamp", None)
            if (
                prev_ts
                and ts
                and ts - prev_ts > self.MESSAGE_GAP
            ):
                items.append(MessageDividerItem(self._format_divider_label(ts)))
            items.append(self._get_message_widget(message))
            if ts:
                prev_ts = ts
        return items

    def _append_message_with_divider(self, message: BaseMessage) -> None:
        listview = self.message_listview
        prev_ts = self._last_message_timestamp()
        ts = getattr(message, "timestamp", None)
        if prev_ts and ts and ts - prev_ts > self.MESSAGE_GAP:
            listview.append(MessageDividerItem(self._format_divider_label(ts)))
        listview.append(self._get_message_widget(message))
        self._trim_message_list()

    def action_show_message_info(self) -> None:
        message = self._selected_message()
        if not message:
            self._notify_error("Unable to read message details.")
            return
        sender = getattr(message, "sender", None)
        receiver = getattr(message, "receiver", None)
        channel = getattr(message, "channel", None)
        lines = [
            f"Text: {message.text}",
            f"Timestamp: {message.timestamp.isoformat() if message.timestamp else 'unknown'}",
        ]
        if sender:
            lines.append(f"Sender: {sender.name} ({getattr(sender, 'public_key', 'n/a')})")
        if receiver:
            lines.append(f"Receiver: {receiver.name} ({getattr(receiver, 'public_key', 'n/a')})")
        if channel:
            lines.append(f"Channel: {channel.name} (idx={channel.index})")
        hops = getattr(message, "path_hops", None)
        if hops is not None:
            lines.append(f"Path hops: {hops}")
        repeats = getattr(message, "repeat_count", None)
        if repeats:
            lines.append(f"Repeats heard: {repeats}")
        app = getattr(self, "app", None)
        if app:
            app.notify("\n".join(lines), title="Message info", severity="information", timeout=10)
        else:
            self.log("\n".join(lines))

    def action_reply_message(self) -> None:
        message = self._selected_message()
        if not message:
            self._notify_error("Select a message to reply to.")
            return
        sender = getattr(message, "sender", None)
        sender_name = getattr(sender, "name", None)
        if not sender_name:
            self._notify_error("Message has no sender info to reply to.")
            return
        mention = f"@[{sender_name}] "
        input_box = self.query_one("#InputTextBox", Input)
        existing = input_box.value or ""
        if existing.startswith(mention):
            input_box.cursor_position = len(input_box.value)
            input_box.focus()
            return
        if existing:
            input_box.value = f"{mention} {existing}"
        else:
            input_box.value = f"{mention} "
        input_box.cursor_position = len(input_box.value)
        input_box.focus()

    def _scroll_messages_to_end(self) -> None:
        listview = self.message_listview
        try:
            listview.scroll_end(animate=False)
        except Exception:
            # scroll_end may not exist on older Textual; fall back to forcing last index
            if listview.children:
                listview.index = len(listview.children) - 1

    def _last_message_timestamp(self) -> datetime | None:
        listview = self.message_listview
        for child in reversed(listview.children):
            message = getattr(child, "message", None)
            if message and getattr(message, "timestamp", None):
                return message.timestamp
        return None

    def action_open_channels(self) -> None:
        self.app.switch_mode("channel")

    def action_open_chats(self) -> None:
        self.app.switch_mode("chat")

    def action_open_settings(self) -> None:
        self.app.switch_mode("settings")

    def _selected_message(self) -> BaseMessage | None:
        listview = self.message_listview
        if not listview.children:
            return None
        idx = getattr(listview, "index", -1)
        if idx is None or idx < 0 or idx >= len(listview.children):
            return None
        child = listview.children[idx]
        return getattr(child, "message", None)

    def _format_divider_label(self, timestamp: datetime) -> str:
        return timestamp.strftime("%Y-%m-%d %H:%M")

    def _get_message_widget(self, message: BaseMessage) -> MessageListViewItem:
        widget = self._message_widget_cache.get(message)
        if widget is None:
            widget = MessageListViewItem(message)
            self._message_widget_cache[message] = widget
        else:
            widget.update_message(message)
        return widget

    def _fetch_messages_for_container(self, container: BaseContainerItem) -> list[BaseMessage]:
        """Blocking message fetch limited to MAX_MESSAGES; run via asyncio.to_thread."""
        messages = self.get_data_container_items(container)
        return self._limit_messages(messages)

    def _limit_messages(self, messages: list[BaseMessage]) -> list[BaseMessage]:
        if self.MAX_MESSAGES <= 0 or len(messages) <= self.MAX_MESSAGES:
            return messages
        return messages[-self.MAX_MESSAGES :]

    def _trim_message_list(self) -> None:
        if self.MAX_MESSAGES <= 0:
            return
        listview = self.message_listview
        message_children = [child for child in listview.children if hasattr(child, "message")]
        while len(message_children) > self.MAX_MESSAGES:
            oldest = message_children.pop(0)
            oldest.remove()
            self._remove_leading_dividers()

    def _remove_leading_dividers(self) -> None:
        listview = self.message_listview
        while listview.children and not hasattr(listview.children[0], "message"):
            listview.children[0].remove()


class ChannelChatScreen(BaseChatScreen):
    left_pane_title = "Channels"
    BINDINGS = BaseChatScreen.BINDINGS + [
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
        store = getattr(self.app, "data_store", None)
        if not service or not store:
            raise RuntimeError("MeshCore service or data store unavailable for channel provider")
        return MeshCoreChatProvider(self.on_data_update, service, store, "channels")
        

class UserChatScreen(BaseChatScreen):
    left_pane_title = "Chats"
    BINDINGS = BaseChatScreen.BINDINGS + [
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
        store = getattr(self.app, "data_store", None)
        if not service or not store:
            raise RuntimeError("MeshCore service or data store unavailable for chat provider")
        return MeshCoreChatProvider(self.on_data_update, service, store, "users")
