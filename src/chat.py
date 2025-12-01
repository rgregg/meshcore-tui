from abc import ABC, abstractmethod
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer, VerticalScroll, Horizontal, HorizontalScroll, Vertical
from textual.widgets import Input, Markdown, Static, Collapsible, Footer, LoadingIndicator, ListView, ListItem, Label, Header
from textual.screen import Screen
from data import BaseContainerItem, BaseMessage, BaseDataProvider, FakeDataProvider, DataUpdate, ChannelMessage, UserMessage

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
    selected_container: BaseContainerItem

    CSS_PATH = "chat.tcss"

    def __init__(self) -> None:
        super().__init__()

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
        yield Footer()

    @abstractmethod
    def get_data_containers(self) -> list[BaseContainerItem]:
        pass
    
    @abstractmethod
    def get_data_container_by_name(self, name:str) -> BaseContainerItem:
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
        self.selected_container = channel

        # Update the title box
        label = self.query_one("#RightPaneTitle")
        if label:
            label.content = channel.display_text
            self.log(f"Set title to '{channel.display_text}'")
        else:
            self.log("No label object found in select_channel")
        

        self.set_loader_visible(True)
        self.message_items.clear()
        await self.run_action(f"reload_channel_data('{name}')")

    @property
    def message_listview(self) -> ListView:
        return self.query_one("#MessageList")
    
    @property
    def container_listview(self) -> ListView:
        return self.query_one("#LeftPaneListView")
    
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
        items = [MessageListViewItem(m) for m in self.get_data_container_items(channel)]
        listview.extend(items)
        self.set_loader_visible(False)

    async def on_input_submitted(self, event:Input.Submitted):
        self.log(f"Input received: {event.value}")
        self.send_message(event.value)
        event.input.clear()

    def on_data_update(self, event:DataUpdate):
        if event.container == self.selected_container:
            if event.update_type == "add":
                if event.item is None:
                    # New channel was added
                    new_channel = ChannelListViewItem(event.container)
                    self.container_listview.append(new_channel)
                else:
                    new_item = MessageListViewItem(event.item)
                    self.message_listview.append(new_item)


class ChannelChatScreen(BaseChatScreen):
    left_pane_title = "Channels"
    BINDINGS = [
        Binding("a", "add_channel", "Add channel"),
        Binding("del", "delete_channel", "Remove channel")
    ]
    
    def __init__(self):
        super().__init__()
        self.data_provider = FakeDataProvider(self.on_data_update)

    def send_message(self, text):
        self.data_provider.send_message(ChannelMessage(self.selected_container, text, None, self.data_provider.current_user))
    def get_data_containers(self):
        return self.data_provider.get_channels()
    def get_data_container_items(self, container):
        return self.data_provider.get_messages_for_channel(container)
    def get_data_container_by_name(self, name):
        return self.data_provider.get_channel_by_name(name)
        

class UserChatScreen(BaseChatScreen):
    left_pane_title = "Chats"
    BINDINGS = [
        Binding("n", "add_chat", "New chat"),
        Binding("del", "delete_chat", "Delete chat")
    ]

    def __init__(self):
        super().__init__()
        self.data_provider = FakeDataProvider(self.on_data_update)

    def send_message(self, text):
        sender = self.data_provider.current_user
        receiver = self.selected_container
        self.log(f"Sending message {text} from {sender} to {receiver}")
        self.data_provider.send_message(UserMessage(text, None, sender, receiver))
    def get_data_containers(self):
        return self.data_provider.get_users()
    def get_data_container_items(self, container):
        return self.data_provider.get_messages_for_user(container)
    def get_data_container_by_name(self, name):
        return self.data_provider.get_user_by_name(name)

