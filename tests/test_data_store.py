from __future__ import annotations

from datetime import datetime, timezone

from data import ChannelMessage, MeshCoreNode
from services.data_store import ChatDataStore, MeshCoreStoreBridge
from services.meshcore_service import MeshCoreChannelInfo, MeshCoreContactInfo, MeshCoreSelfInfo


def test_chat_data_store_persists_channels_and_messages(tmp_path) -> None:
    state_path = tmp_path / "state.sqlite3"
    current_user = MeshCoreNode("Operator", public_key="me")
    store = ChatDataStore(path=state_path, current_user=current_user, skip_legacy_import=True)
    channel = store.ensure_channel("general", 1)
    sender = MeshCoreNode("Alice", public_key="alice")
    store.append_message(
        channel,
        ChannelMessage(channel, "Hello world", datetime.now(timezone.utc), sender),
    )

    reloaded = ChatDataStore(path=state_path, current_user=current_user, skip_legacy_import=True)
    persisted_channel = reloaded.get_channel_by_name("general")
    assert persisted_channel is not None
    messages = reloaded.get_messages_for_channel(persisted_channel)
    assert len(messages) == 1
    assert messages[0].text == "Hello world"
    assert messages[0].sender.name == "Alice"


def test_store_bridge_applies_meshcore_updates(tmp_path) -> None:
    class DummyService:
        def __init__(self) -> None:
            self.channel_listeners = []
            self.contact_listeners = []
            self.channel_message_listeners = []
            self.contact_message_listeners = []
            self.self_listeners = []

        def add_channel_listener(self, listener):
            self.channel_listeners.append(listener)

        def add_contact_listener(self, listener):
            self.contact_listeners.append(listener)

        def add_channel_message_listener(self, listener):
            self.channel_message_listeners.append(listener)

        def add_contact_message_listener(self, listener):
            self.contact_message_listeners.append(listener)

        def add_self_listener(self, listener):
            self.self_listeners.append(listener)

    current_user = MeshCoreNode("Operator", public_key="me")
    store = ChatDataStore(path=tmp_path / "state.sqlite3", current_user=current_user, skip_legacy_import=True)
    dummy_service = DummyService()
    MeshCoreStoreBridge(dummy_service, store)
    for listener in dummy_service.self_listeners:
        listener(
            MeshCoreSelfInfo(
                display_name="Operator",
                node_id="me",
            )
        )
    assert store.current_user.name == "Operator"
    assert store.current_user.public_key == "me"

    channel_info = MeshCoreChannelInfo(index=2, name="#ops")
    for listener in dummy_service.channel_listeners:
        listener([channel_info])
    payload = {
        "channel": channel_info,
        "text": "Ping",
        "timestamp": datetime.now(timezone.utc).timestamp(),
        "sender_prefix": "ALICE",
    }
    for listener in dummy_service.channel_message_listeners:
        listener(payload)

    channel = store.get_channel_by_name("#ops")
    assert channel is not None
    channel_messages = store.get_messages_for_channel(channel)
    assert channel_messages
    assert channel_messages[-1].text == "Ping"

    contact_info = MeshCoreContactInfo(public_key="alice", display_name="Alice", raw={})
    contact_payload = {
        "contact": contact_info,
        "text": "Hi there",
        "timestamp": datetime.now(timezone.utc).timestamp(),
    }
    for listener in dummy_service.contact_message_listeners:
        listener(contact_payload)

    contact = store.get_user_by_name("Alice")
    assert contact is not None
    user_messages = store.get_messages_for_user(contact)
    assert user_messages[-1].text == "Hi there"
    assert user_messages[-1].receiver.name == "Operator"
