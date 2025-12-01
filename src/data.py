from abc import ABC, abstractmethod
from datetime import datetime, timezone

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
    __public_key: str

    def __init__(self, channel_name: str):
        super().__init__(channel_name)
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
    __public_key: str
    __route: str
    __last_seen: datetime

    def __init__(self, display_name: str):
        super().__init__(display_name)


class BaseMessage(ABC):
    """Implements the base class for a message"""
    text: str
    timestamp: datetime
    sender: MeshCoreNode
    draft = False

    def __init__(self, text: str, timestamp: datetime, sender: MeshCoreNode):
        self.text = text
        self.timestamp = timestamp
        self.sender = sender

class ChannelMessage(BaseMessage):
    channel: MeshCoreChannel

    def __init__(self, channel:MeshCoreChannel, text:str, timestamp: datetime, sender: MeshCoreNode):
        super().__init__(text, timestamp, sender)
        self.channel = channel

class UserMessage(BaseMessage):
    pass

class BaseDataProvider(ABC):
    current_user: MeshCoreNode

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

class FakeDataProvider(BaseDataProvider):

    def __init__(self):
        public = MeshCoreChannel("public")
        bot = MeshCoreChannel("#bot")
        self.__channels = [
            public,
            bot,
            MeshCoreChannel("#edm"),
            MeshCoreChannel("#harstine"),
            MeshCoreChannel("FailureToFlood"),
        ]
        
        man = MeshCoreNode("LFPMan")
        self.current_user = man
        woman = MeshCoreNode("LFPWoman")
        kid = MeshCoreNode("LFPKid")
        botuser = MeshCoreNode("BotBot")
        self.__users = [
            man,
            woman,
            kid,
            botuser]
        
        self.__messages = dict()
        self.__messages[public] = [ ChannelMessage(public, "Test 1", datetime.now(timezone.utc), man),
                                      ChannelMessage(public, "Test 2", datetime.now(timezone.utc), woman),
                                      ChannelMessage(public, "Test 3", datetime.now(timezone.utc), kid), ]
        self.__messages[bot] = [   ChannelMessage(bot, "T", datetime.now(timezone.utc), man),
                                      ChannelMessage(bot, "ping pong", datetime.now(timezone.utc), botuser),
                                      ChannelMessage(bot, "path", datetime.now(timezone.utc), kid),
                                      ChannelMessage(bot, "Foo\nBar\nBaz", datetime.now(timezone.utc), botuser),]
        self.__messages[kid] = [ UserMessage("Hello, this is a test", datetime.now(), kid),
                                 UserMessage("Coming through loud and clear", datetime.now(), man)]
        self.__messages[woman] = [ UserMessage("Testing from me to you", datetime.now(), man) ]

    def get_channels(self):
        return self.__channels
    
    def get_messages_for_channel(self, channel):
        return self.__messages.get(channel) or list[ChannelMessage]()
    
    def get_users(self):
        return self.__users
    
    def get_messages_for_user(self, user):
        return self.__messages.get(user) or list[UserMessage]()

