from abc import ABC, abstractmethod
from datetime import datetime
import asyncio
import uuid
import logging
from typing import Literal, TYPE_CHECKING

from services.meshcore_service import MeshCoreService
if TYPE_CHECKING:
    from services.data_store import ChatDataStore

logger = logging.getLogger(__name__)

class BaseContainerItem():
    name: str

    """Implements the base class for a chat/channel/container item"""
    def __init__(self, name: str = None):
        self.name = name
        self.unread_count = 0

    @property
    def display_text(self) -> str:
        if self.unread_count > 0:
            return f"{self.name} ({self.unread_count})"
        return self.name

    def increment_unread(self) -> None:
        self.unread_count += 1

    def clear_unread(self) -> None:
        self.unread_count = 0

    def __str__(self):
        return self.display_text

class MeshCoreChannel(BaseContainerItem):
    """Data class for a channel"""

    def __init__(self, channel_name: str, index: int | None = None):
        super().__init__(channel_name)
        self.index = index

class MeshCoreNode(BaseContainerItem):
    __route: str
    __last_seen: datetime

    def __init__(self, display_name: str, public_key: str | None = None):
        super().__init__(display_name)
        self.public_key = public_key

class BaseMessage(ABC):
    """Implements the base class for a message"""
    text: str
    timestamp: datetime
    sender: MeshCoreNode
    channel: MeshCoreChannel
    draft = False

    def __init__(self, text: str, timestamp: datetime, sender: MeshCoreNode):
        self.text = text
        self.timestamp = timestamp or datetime.now().astimezone()
        self.sender = sender
        self.channel = None
        self.public_key = uuid.uuid4()

    def __hash__(self):
        return hash(self.public_key)


class ChannelMessage(BaseMessage):
    def __init__(self, channel:MeshCoreChannel, text:str, timestamp: datetime, sender: MeshCoreNode):
        super().__init__(text, timestamp, sender)
        self.channel = channel

class UserMessage(BaseMessage):
    receiver: MeshCoreNode
    def __init__(self, text: str, timestamp: datetime, sender: MeshCoreNode, receiver: MeshCoreNode):
        super().__init__(text, timestamp, sender)
        self.receiver = receiver


class BaseDataProvider(ABC):
    current_user: MeshCoreNode
    
    def __init__(self, on_update):
        self._on_update = on_update

    @abstractmethod
    def get_channels(self) -> list[MeshCoreChannel]:
        pass

    def get_channel_by_name(self, name: str) -> MeshCoreChannel:
        channels = self.get_channels()
        for c in channels:
            if c.name == name:
                return c
        return None

    @abstractmethod
    def get_messages_for_channel(self, channel: MeshCoreChannel) -> list[ChannelMessage]:
        pass

    @abstractmethod
    def get_users(self) -> list[MeshCoreNode]:
        pass

    def get_user_by_name(self, name: str) -> MeshCoreNode:
        for u in self.get_users():
            if u.name == name:
                return u
        return None

    @abstractmethod
    def get_messages_for_user(self, user: MeshCoreNode) -> list[UserMessage]:
        pass

    @abstractmethod
    def send_message(self, message:BaseMessage) -> bool:
        """Sends a message to a channel or node based on the message type and parameters.
           Returns a value to indiciate if the message was sent or not."""
        
    @abstractmethod
    def remove_container(self, container:BaseContainerItem):
        pass

class FakeDataProvider(BaseDataProvider):
    __messages: dict[BaseContainerItem, list[BaseMessage]] = {}
    __channels: list[MeshCoreChannel] = []
    __users: list[MeshCoreNode] = []

    def __init__(self, on_update):
        super().__init__(on_update)

        public = MeshCoreChannel("public")
        bot = MeshCoreChannel("#bot")
        self.__channels.extend([
            public,
            bot,
            MeshCoreChannel("#edm"),
            MeshCoreChannel("#harstine"),
            MeshCoreChannel("FailureToFlood"),
        ])
        
        man = MeshCoreNode("LFPMan")
        self.current_user = man
        woman = MeshCoreNode("LFPWoman")
        kid = MeshCoreNode("LFPKid")
        botuser = MeshCoreNode("BotBot")
        self.__users.extend([
            man,
            woman,
            kid,
            botuser])
        self.__messages[public] = [
            ChannelMessage(public, "Test 1", datetime.now().astimezone(), man),
            ChannelMessage(public, "Test 2", datetime.now().astimezone(), woman),
            ChannelMessage(public, "Test 3", datetime.now().astimezone(), kid),
        ]
        self.__messages[bot] = [
            ChannelMessage(bot, "T", datetime.now().astimezone(), man),
            ChannelMessage(bot, "ping pong", datetime.now().astimezone(), botuser),
            ChannelMessage(bot, "path", datetime.now().astimezone(), kid),
            ChannelMessage(bot, "Foo\nBar\nBaz", datetime.now().astimezone(), botuser),
        ]
        self.__messages[kid] = [
            UserMessage("Hello, this is a test", datetime.now().astimezone(), kid, man),
            UserMessage("Coming through loud and clear", datetime.now().astimezone(), man, kid),
        ]
        self.__messages[woman] = [
            UserMessage("Testing from me to you", datetime.now().astimezone(), man, woman)
        ]

    def get_channels(self):
        return self.__channels
    
    def get_messages_for_channel(self, channel):
        return self.__messages.get(channel) or list[ChannelMessage]()
    
    def get_users(self):
        return self.__users
    
    def get_messages_for_user(self, user):
        return self.__messages.get(user) or list[UserMessage]()
    
    def send_message(self, message):
        if isinstance(message, ChannelMessage):
            channel = message.channel
            messages = self.__messages.get(channel)
            if messages is None:
                messages = []
                self.__messages[channel] = messages
            messages.append(message)
            self._on_update(DataUpdate("add", channel, message))
        elif isinstance(message, UserMessage):
            user = message.receiver
            messages = self.__messages.get(user)
            if messages is None:
                messages = []
                self.__messages[user] = messages
            messages.append(message)
            self._on_update(DataUpdate("add", user, message))
    
    def remove_container(self, container):
        if isinstance(container, MeshCoreChannel):
            self.__channels.remove(container)
        elif isinstance(container, MeshCoreNode):
            self.__users.remove(container)
        self._on_update(DataUpdate("remove", container, None))

class DataUpdate:
    update_type: str # add, update, remove
    container: BaseContainerItem
    item: BaseMessage

    def __init__(self, update: str, container: BaseContainerItem, item: BaseMessage):
        self.update_type = update
        self.container = container
        self.item = item
        

class MeshCoreChatProvider(BaseDataProvider):
    """Provides chat/channel data backed by ChatDataStore and MeshCore service."""

    def __init__(
        self,
        on_update,
        service: MeshCoreService,
        store: "ChatDataStore",
        mode: Literal["channels", "users"],
    ):
        super().__init__(on_update)
        self._service = service
        self._store = store
        self._mode = mode
        self._store_listener = self._handle_store_update
        self._store.add_listener(self._store_listener)

    def get_channels(self):
        return self._store.get_channels()

    def get_messages_for_channel(self, channel):
        return self._store.get_messages_for_channel(channel)

    def get_users(self):
        return self._store.get_users()

    def get_messages_for_user(self, user):
        return self._store.get_messages_for_user(user)

    @property
    def current_user(self) -> MeshCoreNode:
        return self._store.current_user

    def send_message(self, message: BaseMessage) -> bool:
        if isinstance(message, ChannelMessage):
            if message.channel.index is None:
                logger.error("Cannot send channel message; channel %s has no index", message.channel.name)
                return False
            if not self._service.is_connected:
                logger.error("Cannot send channel message; MeshCore is not connected")
                return False
            logger.info("Sending channel message to %s: %s", message.channel.name, message.text)
            self._store.append_message(message.channel, message)
            asyncio.create_task(
                self._service.send_channel_message(message.channel.index, message.text)
            )
            return True
        if isinstance(message, UserMessage):
            if not message.receiver.public_key:
                logger.error("Cannot send user message; receiver %s lacks public key", message.receiver.name)
                return False
            if not self._service.is_connected:
                logger.error("Cannot send user message; MeshCore is not connected")
                return False
            self._store.append_message(message.receiver, message)
            asyncio.create_task(
                self._service.send_direct_message(message.receiver.public_key, message.text)
            )
            return True
        return False

    def remove_container(self, container: BaseContainerItem):
        raise NotImplementedError("Removing MeshCore contacts is not supported yet")

    def _handle_store_update(self, update: DataUpdate) -> None:
        logger.debug(
            "Data store update received: %s container=%s item_ts=%s",
            update.update_type,
            getattr(update.container, "name", getattr(update.container, "display_name", "unknown")),
            getattr(update.item, "timestamp", None),
        )
        container = update.container
        if self._mode == "channels" and isinstance(container, MeshCoreChannel):
            self._on_update(update)
        elif self._mode == "users" and isinstance(container, MeshCoreNode):
            self._on_update(update)
