from abc import ABC, abstractmethod
from datetime import datetime, timezone
import asyncio
import uuid
from typing import Any, Literal

from services.meshcore_service import MeshCoreService, MeshCoreChannelInfo, MeshCoreContactInfo

class BaseContainerItem():
    name: str

    """Implements the base class for a chat/channel/container item"""
    def __init__(self, name: str = None):
        self.name = name

    @property
    def display_text(self) -> str:
        return self.name

    def __str__(self):
        return self.display_text

class MeshCoreChannel(BaseContainerItem):
    """Data class for a channel"""
    __unread_count: int

    def __init__(self, channel_name: str, index: int | None = None):
        super().__init__(channel_name)
        self.index = index
        self.__unread_count = 0

    def set_unread_count(self, unread_count: int):
        self.__unread_count = unread_count

    @property
    def display_text(self) -> str:
        if (self.__unread_count > 0):
            return f"{self.name} [{self.__unread_count}]"
        else:
            return self.name

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
        self.timestamp = timestamp or datetime.now(timezone.utc)
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
        self.__messages[public] = [ ChannelMessage(public, "Test 1", datetime.now(timezone.utc), man),
                                      ChannelMessage(public, "Test 2", datetime.now(timezone.utc), woman),
                                      ChannelMessage(public, "Test 3", datetime.now(timezone.utc), kid), ]
        self.__messages[bot] = [   ChannelMessage(bot, "T", datetime.now(timezone.utc), man),
                                      ChannelMessage(bot, "ping pong", datetime.now(timezone.utc), botuser),
                                      ChannelMessage(bot, "path", datetime.now(timezone.utc), kid),
                                      ChannelMessage(bot, "Foo\nBar\nBaz", datetime.now(timezone.utc), botuser),]
        self.__messages[kid] = [ UserMessage("Hello, this is a test", datetime.now(), kid, man),
                                 UserMessage("Coming through loud and clear", datetime.now(), man, kid)]
        self.__messages[woman] = [ UserMessage("Testing from me to you", datetime.now(), man, woman) ]

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
    """Bridges MeshCoreService data into TUI providers."""

    def __init__(
        self,
        on_update,
        service: MeshCoreService,
        mode: Literal["channels", "users"],
    ):
        super().__init__(on_update)
        self._service = service
        self._mode = mode
        self._channel_map: dict[int, MeshCoreChannel] = {}
        self._user_map: dict[str, MeshCoreNode] = {}
        self.__messages: dict[BaseContainerItem, list[BaseMessage]] = {}
        user_cfg = service.config.user
        self.current_user = MeshCoreNode(user_cfg.display_name, public_key=user_cfg.node_id)
        if mode == "channels":
            service.add_channel_listener(self._handle_channels)
            service.add_channel_message_listener(self._handle_channel_message)
        else:
            service.add_contact_listener(self._handle_contacts)
            service.add_contact_message_listener(self._handle_contact_message)

    def get_channels(self):
        return list(self._channel_map.values())

    def get_messages_for_channel(self, channel):
        return self.__messages.get(channel, list[ChannelMessage]())

    def get_users(self):
        return list(self._user_map.values())

    def get_messages_for_user(self, user):
        return self.__messages.get(user, list[UserMessage]())

    def send_message(self, message: BaseMessage) -> bool:
        if isinstance(message, ChannelMessage):
            if message.channel.index is None:
                return False
            if not self._service.is_connected:
                return False
            asyncio.create_task(
                self._service.send_channel_message(message.channel.index, message.text)
            )
            self.__messages.setdefault(message.channel, []).append(message)
            self._on_update(DataUpdate("add", message.channel, message))
            return True
        if isinstance(message, UserMessage):
            if not message.receiver.public_key:
                return False
            if not self._service.is_connected:
                return False
            asyncio.create_task(
                self._service.send_direct_message(message.receiver.public_key, message.text)
            )
            self.__messages.setdefault(message.receiver, []).append(message)
            self._on_update(DataUpdate("add", message.receiver, message))
            return True
        return False

    def remove_container(self, container: BaseContainerItem):
        raise NotImplementedError("Removing MeshCore contacts is not supported yet")

    def _handle_channels(self, channels: list[MeshCoreChannelInfo]) -> None:
        if self._mode != "channels":
            return
        for info in channels:
            channel = self._channel_map.get(info.index)
            if not channel:
                channel = MeshCoreChannel(info.name, index=info.index)
                self._channel_map[info.index] = channel
                self.__messages.setdefault(channel, [])
                self._on_update(DataUpdate("add", channel, None))
            else:
                channel.name = info.name

    def _handle_contacts(self, contacts: list[MeshCoreContactInfo]) -> None:
        if self._mode != "users":
            return
        for info in contacts:
            node = self._user_map.get(info.public_key)
            if not node:
                node = MeshCoreNode(info.display_name, public_key=info.public_key)
                self._user_map[info.public_key] = node
                self.__messages.setdefault(node, [])
                self._on_update(DataUpdate("add", node, None))
            else:
                node.name = info.display_name

    def _handle_channel_message(self, payload: dict[str, Any]) -> None:
        if self._mode != "channels":
            return
        channel = payload.get("channel")
        text = payload.get("text", "")
        if not isinstance(channel, MeshCoreChannelInfo):
            return
        container = self._channel_map.get(channel.index)
        if not container:
            container = MeshCoreChannel(channel.name, index=channel.index)
            self._channel_map[channel.index] = container
            self.__messages.setdefault(container, [])
            self._on_update(DataUpdate("add", container, None))
        contact = payload.get("contact")
        prefix = payload.get("sender_prefix")
        if isinstance(contact, MeshCoreContactInfo):
            sender = MeshCoreNode(contact.display_name, public_key=contact.public_key)
        elif isinstance(prefix, str) and prefix:
            sender = MeshCoreNode(prefix, public_key=prefix)
        else:
            sender = MeshCoreNode("Unknown sender")
        message = ChannelMessage(
            container,
            text,
            datetime.fromtimestamp(
                payload.get("timestamp", datetime.now(timezone.utc).timestamp()),
                tz=timezone.utc,
            ),
            sender,
        )
        self.__messages.setdefault(container, []).append(message)
        self._on_update(DataUpdate("add", container, message))

    def _handle_contact_message(self, payload: dict[str, Any]) -> None:
        if self._mode != "users":
            return
        contact = payload.get("contact")
        if not isinstance(contact, MeshCoreContactInfo):
            return
        node = self._user_map.get(contact.public_key)
        if not node:
            node = MeshCoreNode(contact.display_name, public_key=contact.public_key)
            self._user_map[contact.public_key] = node
            self.__messages.setdefault(node, [])
            self._on_update(DataUpdate("add", node, None))
        message = UserMessage(
            payload.get("text", ""),
            datetime.fromtimestamp(
                payload.get("timestamp", datetime.now(timezone.utc).timestamp()),
                tz=timezone.utc,
            ),
            MeshCoreNode(contact.display_name, public_key=contact.public_key),
            self.current_user,
        )
        self.__messages.setdefault(node, []).append(message)
        self._on_update(DataUpdate("add", node, message))
