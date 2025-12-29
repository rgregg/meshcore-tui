from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from data import (
    BaseContainerItem,
    BaseMessage,
    ChannelMessage,
    DataUpdate,
    MeshCoreChannel,
    MeshCoreNode,
    UserMessage,
)
from services.meshcore_service import (
    MeshCoreChannelInfo,
    MeshCoreContactInfo,
    MeshCoreSelfInfo,
    MeshCoreService,
)

logger = logging.getLogger(__name__)

StateListener = Callable[[DataUpdate], None]


class ChatDataStore:
    """Persists MeshCore chat/channel state and notifies listeners about updates."""

    def __init__(
        self,
        path: Path | str | None = None,
        current_user: MeshCoreNode | None = None,
    ) -> None:
        self._path = Path(path or Path("logs") / "meshcore_state.json")
        self.current_user = current_user or MeshCoreNode("MeshCore Operator")
        self._channels: list[MeshCoreChannel] = []
        self._contacts: list[MeshCoreNode] = []
        self._messages: dict[str, list[BaseMessage]] = {}
        self._message_hashes: dict[str, dict[str, int]] = {}
        self._message_refs: dict[str, dict[str, BaseMessage]] = {}
        self._listeners: list[StateListener] = []
        self._container_cache: dict[str, BaseContainerItem] = {}
        self._loading = False
        self._load()

    def add_listener(self, listener: StateListener) -> None:
        self._listeners.append(listener)

    def remove_listener(self, listener: StateListener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def get_channels(self) -> list[MeshCoreChannel]:
        return list(self._channels)

    def get_channel_by_name(self, name: str) -> MeshCoreChannel | None:
        for channel in self._channels:
            if channel.name == name:
                return channel
        return None

    def get_users(self) -> list[MeshCoreNode]:
        return sorted(self._contacts, key=lambda node: node.name.lower())

    def get_user_by_name(self, name: str) -> MeshCoreNode | None:
        for node in self._contacts:
            if node.name == name:
                return node
        return None

    def get_messages_for_channel(self, channel: MeshCoreChannel) -> list[ChannelMessage]:
        return [
            msg
            for msg in self._messages.get(self._container_key(channel), [])
            if isinstance(msg, ChannelMessage)
        ]

    def get_messages_for_user(self, user: MeshCoreNode) -> list[UserMessage]:
        return [
            msg
            for msg in self._messages.get(self._container_key(user), [])
            if isinstance(msg, UserMessage)
        ]

    def set_current_user(self, node: MeshCoreNode) -> None:
        self.current_user = node
        self._persist()

    def upsert_channel(self, info: MeshCoreChannelInfo) -> MeshCoreChannel:
        """Ensure the provided channel info exists in the store."""
        return self.ensure_channel(info.name, info.index)

    def ensure_channel(self, name: str, index: int | None = None) -> MeshCoreChannel:
        existing = self._find_channel_by_index(index) if index is not None else None
        if existing:
            if existing.name != name:
                existing.name = name
                self._persist()
            return existing
        for channel in self._channels:
            if channel.name == name and channel.index is None:
                channel.index = index
                self._persist()
                return channel
        channel = MeshCoreChannel(name, index=index)
        self._register_container(channel, notify=not self._loading)
        return channel

    def upsert_contact(self, info: MeshCoreContactInfo) -> MeshCoreNode:
        """Ensure the provided contact info exists in the store."""
        return self.ensure_contact(info.display_name, info.public_key)

    def ensure_contact(self, display_name: str, public_key: str | None = None) -> MeshCoreNode:
        node = self._find_contact_by_key(public_key) if public_key else None
        if node:
            if node.name != display_name:
                node.name = display_name
                self._persist()
            return node
        for existing in self._contacts:
            if existing.name == display_name and (not public_key or existing.public_key is None):
                if public_key and existing.public_key is None:
                    existing.public_key = public_key
                    self._persist()
                return existing
        node = MeshCoreNode(display_name, public_key=public_key)
        self._register_container(node, notify=not self._loading)
        return node

    def append_message(self, container: BaseContainerItem, message: BaseMessage) -> None:
        """Store a message for the provided container and notify listeners."""
        key = self._container_key(container)
        if key not in self._container_cache:
            self._register_container(container, notify=False)
        if key not in self._messages:
            self._messages[key] = []
            self._message_hashes[key] = {}
            self._message_refs[key] = {}
        hash_value = self._compute_message_hash(message)
        counts = self._message_hashes.setdefault(key, {})
        refs = self._message_refs.setdefault(key, {})
        if hash_value in counts:
            counts[hash_value] += 1
            ref = refs.get(hash_value)
            if ref:
                setattr(ref, "repeat_count", counts[hash_value])
            if isinstance(message, ChannelMessage):
                logger.info(
                    "Repeater duplicate #%s on %s: %s",
                    counts[hash_value],
                    getattr(message.channel, "name", "channel"),
                    message.text,
                )
            return
        counts[hash_value] = 1
        refs[hash_value] = message
        setattr(message, "repeat_count", 1)
        self._messages[key].append(message)
        self._persist()
        self._notify(DataUpdate("add", container, message))

    def _find_channel_by_index(self, index: int) -> MeshCoreChannel | None:
        for channel in self._channels:
            if channel.index == index:
                return channel
        return None

    def _find_contact_by_key(self, public_key: str | None) -> MeshCoreNode | None:
        if not public_key:
            return None
        for node in self._contacts:
            if node.public_key == public_key:
                return node
        return None

    def _register_container(self, container: BaseContainerItem, *, notify: bool) -> None:
        if isinstance(container, MeshCoreChannel):
            self._channels.append(container)
        elif isinstance(container, MeshCoreNode):
            self._contacts.append(container)
        key = self._container_key(container)
        self._container_cache[key] = container
        self._messages.setdefault(key, [])
        self._message_hashes.setdefault(key, {})
        self._message_refs.setdefault(key, {})
        if notify:
            self._notify(DataUpdate("add", container, None))
        if not self._loading:
            self._persist()

    def _container_key(self, container: BaseContainerItem) -> str:
        if isinstance(container, MeshCoreChannel):
            ident = container.index if container.index is not None else container.name
            return f"channel:{ident}"
        if isinstance(container, MeshCoreNode):
            ident = container.public_key or container.name
            return f"user:{ident}"
        return f"container:{container.name}"

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            with self._path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception as exc:  # pragma: no cover - invalid data
            logger.warning("Failed to load chat data store: %s", exc)
            return
        self._loading = True
        try:
            current_user = data.get("current_user")
            if current_user:
                self.current_user = self._deserialize_node(current_user)
            for channel_entry in data.get("channels", []):
                channel = MeshCoreChannel(
                    channel_entry.get("name", "Channel"),
                    index=channel_entry.get("index"),
                )
                self._register_container(channel, notify=False)
            for contact_entry in data.get("contacts", []):
                node = MeshCoreNode(
                    contact_entry.get("display_name", "Contact"),
                    public_key=contact_entry.get("public_key"),
                )
                self._register_container(node, notify=False)
            for message_entry in data.get("messages", []):
                container_key = message_entry.get("container_key")
                container = self._container_cache.get(container_key)
                if not container:
                    container = self._hydrate_missing_container(message_entry)
                    if container:
                        self._register_container(container, notify=False)
                if not container:
                    continue
                timestamp = self._parse_timestamp(message_entry.get("timestamp"))
                sender = self._deserialize_node(message_entry.get("sender", {}))
                if message_entry.get("type") == "channel" and isinstance(container, MeshCoreChannel):
                    message = ChannelMessage(container, message_entry.get("text", ""), timestamp, sender)
                elif isinstance(container, MeshCoreNode):
                    receiver_data = message_entry.get("receiver")
                    receiver = (
                        self._deserialize_node(receiver_data)
                        if receiver_data
                        else self.current_user
                    )
                    message = UserMessage(message_entry.get("text", ""), timestamp, sender, receiver)
                else:
                    continue
                self._messages.setdefault(container_key, [])
                counts = self._message_hashes.setdefault(container_key, {})
                refs = self._message_refs.setdefault(container_key, {})
                hash_value = self._compute_message_hash(message)
                if hash_value in counts:
                    counts[hash_value] += 1
                    ref = refs.get(hash_value)
                    if ref:
                        setattr(ref, "repeat_count", counts[hash_value])
                    continue
                counts[hash_value] = 1
                refs[hash_value] = message
                setattr(message, "repeat_count", 1)
                self._messages[container_key].append(message)
        finally:
            self._loading = False

    def _hydrate_missing_container(self, entry: dict) -> BaseContainerItem | None:
        if entry.get("type") == "channel":
            return MeshCoreChannel(entry.get("channel_name", "Channel"), index=entry.get("channel_index"))
        return MeshCoreNode(
            entry.get("contact_name", "Contact"),
            public_key=entry.get("contact_public_key"),
        )

    def _deserialize_node(self, payload: dict | None) -> MeshCoreNode:
        if not payload:
            return MeshCoreNode("MeshCore Operator")
        return MeshCoreNode(payload.get("display_name", "MeshCore Operator"), public_key=payload.get("public_key"))

    def _parse_timestamp(self, raw: str | None) -> datetime:
        if not raw:
            return datetime.now(timezone.utc)
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return datetime.now(timezone.utc)

    def _persist(self) -> None:
        if self._loading:
            return
        payload = {
            "current_user": self._serialize_node(self.current_user),
            "channels": [self._serialize_channel(channel) for channel in self._channels],
            "contacts": [self._serialize_node(node) for node in self._contacts],
            "messages": self._serialize_messages(),
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
        except Exception as exc:  # pragma: no cover - filesystem specific
            logger.warning("Failed to persist chat data store: %s", exc)

    def _serialize_channel(self, channel: MeshCoreChannel) -> dict:
        return {"name": channel.name, "index": channel.index}

    def _serialize_node(self, node: MeshCoreNode) -> dict:
        return {"display_name": node.name, "public_key": node.public_key}

    def _serialize_messages(self) -> list[dict]:
        entries = []
        for key, messages in self._messages.items():
            container = self._container_cache.get(key)
            if not container:
                continue
            for message in messages:
                entries.append(self._serialize_message_entry(key, container, message))
        return entries

    def _serialize_message_entry(
        self,
        container_key: str,
        container: BaseContainerItem,
        message: BaseMessage,
    ) -> dict:
        entry = {
            "container_key": container_key,
            "text": message.text,
            "timestamp": message.timestamp.isoformat(),
            "sender": self._serialize_node(message.sender),
        }
        if isinstance(container, MeshCoreChannel):
            entry.update(
                {
                    "type": "channel",
                    "channel_index": container.index,
                    "channel_name": container.name,
                }
            )
        else:
            entry.update(
                {
                    "type": "user",
                    "contact_name": container.name,
                    "contact_public_key": getattr(container, "public_key", None),
                    "receiver": self._serialize_node(getattr(message, "receiver", self.current_user)),
                }
            )
        return entry

    def _notify(self, update: DataUpdate) -> None:
        for listener in list(self._listeners):
            try:
                listener(update)
            except Exception:  # pragma: no cover - UI level errors
                logger.exception("Data store listener failed")

    def _compute_message_hash(self, message: BaseMessage) -> str:
        sender = getattr(message, "sender", None)
        sender_key = ""
        if isinstance(sender, MeshCoreNode):
            sender_key = sender.public_key or sender.name or ""
        timestamp = message.timestamp.isoformat() if message.timestamp else ""
        receiver = getattr(message, "receiver", None)
        receiver_key = ""
        if isinstance(receiver, MeshCoreNode):
            receiver_key = receiver.public_key or receiver.name or ""
        channel = getattr(message, "channel", None)
        channel_key = ""
        if isinstance(channel, MeshCoreChannel):
            channel_key = str(channel.index) if channel.index is not None else channel.name or ""
        return "|".join(
            [
                message.text or "",
                timestamp,
                sender_key,
                receiver_key,
                channel_key,
            ]
        )


class MeshCoreStoreBridge:
    """Connects MeshCoreService updates to the ChatDataStore."""

    def __init__(self, service: MeshCoreService, store: ChatDataStore) -> None:
        self._service = service
        self._store = store
        service.add_self_listener(self._handle_self_info)
        service.add_channel_listener(self._handle_channels)
        service.add_contact_listener(self._handle_contacts)
        service.add_channel_message_listener(self._handle_channel_message)
        service.add_contact_message_listener(self._handle_contact_message)

    def _handle_self_info(self, info: MeshCoreSelfInfo) -> None:
        name = info.display_name or "MeshCore Operator"
        node = MeshCoreNode(name, public_key=info.node_id)
        self._store.set_current_user(node)

    def _handle_channels(self, channels: Iterable[MeshCoreChannelInfo]) -> None:
        for info in channels:
            self._store.upsert_channel(info)

    def _handle_contacts(self, contacts: Iterable[MeshCoreContactInfo]) -> None:
        for info in contacts:
            self._store.upsert_contact(info)

    def _handle_channel_message(self, payload: dict) -> None:
        info = payload.get("channel")
        if isinstance(info, MeshCoreChannelInfo):
            channel = self._store.upsert_channel(info)
        else:
            idx = payload.get("channel_idx")
            name = payload.get("channel_name", f"Channel {idx}")
            channel = self._store.ensure_channel(name, idx)
        sender = self._resolve_sender(payload)
        message = ChannelMessage(
            channel,
            payload.get("text", ""),
            self._timestamp_from_payload(payload),
            sender,
        )
        self._annotate_message_metadata(message, payload)
        self._store.append_message(channel, message)

    def _handle_contact_message(self, payload: dict) -> None:
        info = payload.get("contact")
        if isinstance(info, MeshCoreContactInfo):
            contact = self._store.upsert_contact(info)
            sender = contact
        else:
            prefix = payload.get("sender_prefix") or payload.get("pubkey_prefix") or "Unknown sender"
            sender = MeshCoreNode(prefix)
            contact = sender
        message = UserMessage(
            payload.get("text", ""),
            self._timestamp_from_payload(payload),
            sender,
            self._store.current_user,
        )
        self._annotate_message_metadata(message, payload)
        self._store.append_message(contact, message)

    def _resolve_sender(self, payload: dict) -> MeshCoreNode:
        contact = payload.get("contact")
        if isinstance(contact, MeshCoreContactInfo):
            return self._store.upsert_contact(contact)
        prefix = payload.get("sender_prefix") or payload.get("pubkey_prefix")
        if isinstance(prefix, str) and prefix:
            return MeshCoreNode(prefix)
        text = payload.get("text", "")
        if isinstance(text, str) and ":" in text:
            leading = text.split(":", 1)[0].strip()
            if leading:
                return MeshCoreNode(leading)
        return MeshCoreNode("Unknown sender")

    def _timestamp_from_payload(self, payload: dict) -> datetime:
        raw = payload.get("timestamp") or payload.get("sender_timestamp")
        if isinstance(raw, (int, float)):
            return datetime.fromtimestamp(raw, tz=timezone.utc)
        if isinstance(raw, str):
            try:
                return datetime.fromisoformat(raw)
            except ValueError:
                pass
        return datetime.now(timezone.utc)

    def _annotate_message_metadata(self, message: BaseMessage, payload: dict) -> None:
        hops = payload.get("path_len")
        if hops is not None:
            setattr(message, "path_hops", hops)
