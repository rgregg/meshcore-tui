from __future__ import annotations

import json
import logging
import sqlite3
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
        *,
        skip_legacy_import: bool = False,
    ) -> None:
        default_path = Path("logs") / "meshcore_state.sqlite3"
        self._path = Path(path or default_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()
        self._legacy_json_path = Path("logs") / "meshcore_state.json"
        self.current_user = current_user or MeshCoreNode("MeshCore Operator")
        self._skip_legacy_import = skip_legacy_import
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
        self._save_current_user()

    def upsert_channel(self, info: MeshCoreChannelInfo) -> MeshCoreChannel:
        """Ensure the provided channel info exists in the store."""
        return self.ensure_channel(info.name, info.index)

    def ensure_channel(self, name: str, index: int | None = None) -> MeshCoreChannel:
        normalized_index = self._normalize_channel_index(name, index)
        existing = self._find_channel_by_index(normalized_index) if normalized_index is not None else None
        if existing:
            if existing.name != name:
                existing.name = name
                self._save_channel(existing)
                self._notify_container_update(existing)
            return existing
        for channel in self._channels:
            if channel.name == name and channel.index is None:
                channel.index = normalized_index
                self._save_channel(channel)
                return channel
        channel = MeshCoreChannel(name, index=normalized_index)
        self._register_container(channel, notify=not self._loading)
        return channel

    def upsert_contact(self, info: MeshCoreContactInfo) -> MeshCoreNode:
        """Ensure the provided contact info exists in the store."""
        return self.ensure_contact(info.display_name, info.public_key)

    def ensure_contact(self, display_name: str, public_key: str | None = None) -> MeshCoreNode:
        node = self._find_contact_by_key(public_key) if public_key else None
        if node:
            updated = False
            if node.name != display_name:
                node.name = display_name
                updated = True
            if public_key and not node.public_key:
                node.public_key = public_key
                updated = True
            if updated:
                self._save_contact(node)
                self._notify_container_update(node)
            return node
        for existing in self._contacts:
            if existing.name == display_name and (not public_key or existing.public_key is None):
                if public_key and existing.public_key is None:
                    existing.public_key = public_key
                    self._save_contact(existing)
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
            self._update_message_repeat(key, hash_value, counts[hash_value])
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
        self._save_message_record(container, message, hash_value)
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

    def _normalize_channel_index(self, name: str, index: int | str | None) -> int | None:
        if isinstance(index, str):
            try:
                index = int(index, 10)
            except ValueError:
                index = None
        if index is None and self._is_public_channel_name(name):
            return 0
        return index

    def _maybe_assign_default_channel_index(self, channel: MeshCoreChannel) -> None:
        if channel.index is None and self._is_public_channel_name(channel.name):
            channel.index = 0

    def _is_public_channel_name(self, name: str | None) -> bool:
        return bool(name and name.strip().lower() == "public")

    def _register_container(self, container: BaseContainerItem, *, notify: bool) -> None:
        if isinstance(container, MeshCoreChannel):
            self._maybe_assign_default_channel_index(container)
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
            if isinstance(container, MeshCoreChannel):
                self._save_channel(container)
            elif isinstance(container, MeshCoreNode):
                self._save_contact(container)

    def _container_key(self, container: BaseContainerItem) -> str:
        key = getattr(container, "_storage_key", None)
        if key:
            return key
        if isinstance(container, MeshCoreChannel):
            ident = container.index if container.index is not None else container.name
            key = f"channel:{ident}"
        elif isinstance(container, MeshCoreNode):
            ident = container.public_key or container.name
            key = f"user:{ident}"
        else:
            key = f"container:{container.name}"
        setattr(container, "_storage_key", key)
        return key

    def _load(self) -> None:
        self._loading = True
        try:
            self._load_from_database()
            if (
                not self._channels
                and not self._contacts
                and not any(self._messages.values())
                and not self._skip_legacy_import
            ):
                self._import_legacy_json()
        finally:
            self._loading = False

    def _hydrate_missing_container(
        self,
        entry: dict,
        container_key: str | None = None,
    ) -> BaseContainerItem | None:
        container: BaseContainerItem | None
        if entry.get("type") == "channel":
            name = entry.get("channel_name", "Channel")
            idx = self._normalize_channel_index(name, entry.get("channel_index"))
            container = MeshCoreChannel(name, index=idx)
        else:
            container = MeshCoreNode(
                entry.get("contact_name", "Contact"),
                public_key=entry.get("contact_public_key"),
            )
        if container_key and container:
            setattr(container, "_storage_key", container_key)
        return container

    def _deserialize_node(self, payload: dict | None) -> MeshCoreNode:
        if not payload:
            return MeshCoreNode("MeshCore Operator")
        return MeshCoreNode(payload.get("display_name", "MeshCore Operator"), public_key=payload.get("public_key"))

    def _parse_timestamp(self, raw: str | None) -> datetime:
        if not raw:
            return self._local_now()
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return self._local_now()
        return self._to_local(parsed)

    def _ensure_schema(self) -> None:
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS channels (
                    container_key TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    channel_index INTEGER
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS contacts (
                    container_key TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    public_key TEXT
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    container_key TEXT NOT NULL,
                    type TEXT NOT NULL,
                    text TEXT,
                    timestamp TEXT,
                    sender_name TEXT,
                    sender_public_key TEXT,
                    receiver_name TEXT,
                    receiver_public_key TEXT,
                    channel_name TEXT,
                    channel_index INTEGER,
                    repeat_count INTEGER NOT NULL DEFAULT 1,
                    message_hash TEXT NOT NULL,
                    path_hops INTEGER,
                UNIQUE(container_key, message_hash)
            )
            """
        )

    def _load_from_database(self) -> None:
        name = self._get_metadata_value("current_user_name")
        public_key = self._get_metadata_value("current_user_public_key")
        if name or public_key:
            self.current_user = MeshCoreNode(name or "MeshCore Operator", public_key=public_key)
        for row in self._conn.execute(
            "SELECT container_key, name, channel_index FROM channels ORDER BY name COLLATE NOCASE"
        ):
            idx = self._normalize_channel_index(row["name"], row["channel_index"])
            channel = MeshCoreChannel(row["name"], index=idx)
            self._maybe_assign_default_channel_index(channel)
            setattr(channel, "_storage_key", row["container_key"])
            self._register_container(channel, notify=False)
        for row in self._conn.execute(
            "SELECT container_key, display_name, public_key FROM contacts ORDER BY display_name COLLATE NOCASE"
        ):
            node = MeshCoreNode(row["display_name"], public_key=row["public_key"])
            setattr(node, "_storage_key", row["container_key"])
            self._register_container(node, notify=False)
        for row in self._conn.execute("SELECT * FROM messages ORDER BY timestamp"):
            container_key = row["container_key"]
            container = self._container_cache.get(container_key)
            if not container:
                container = self._hydrate_missing_container(row, container_key)
                if container:
                    self._register_container(container, notify=False)
            if not container:
                continue
            timestamp = self._parse_timestamp(row["timestamp"])
            sender = MeshCoreNode(row["sender_name"] or "MeshCore Operator", public_key=row["sender_public_key"])
            if row["type"] == "channel" and isinstance(container, MeshCoreChannel):
                message = ChannelMessage(container, row["text"] or "", timestamp, sender)
            elif isinstance(container, MeshCoreNode):
                receiver = MeshCoreNode(
                    row["receiver_name"] or self.current_user.name,
                    public_key=row["receiver_public_key"],
                )
                message = UserMessage(row["text"] or "", timestamp, sender, receiver)
            else:
                continue
            repeat_count = row["repeat_count"] or 1
            setattr(message, "repeat_count", repeat_count)
            path_hops = row["path_hops"]
            if path_hops is not None:
                setattr(message, "path_hops", path_hops)
            self._messages.setdefault(container_key, [])
            self._message_hashes.setdefault(container_key, {})
            self._message_refs.setdefault(container_key, {})
            self._messages[container_key].append(message)
            message_hash = row["message_hash"]
            self._message_hashes[container_key][message_hash] = repeat_count
            self._message_refs[container_key][message_hash] = message

    def _import_legacy_json(self) -> None:
        if not self._legacy_json_path.exists():
            return
        try:
            with self._legacy_json_path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception as exc:  # pragma: no cover - invalid data
            logger.warning("Failed to import legacy chat state: %s", exc)
            return
        logger.info("Importing chat history from legacy JSON file")
        self._load_legacy_payload(data)
        self._persist_full_state_to_db()

    def _load_legacy_payload(self, data: dict) -> None:
        current_user = data.get("current_user")
        if current_user:
            self.current_user = self._deserialize_node(current_user)
        for channel_entry in data.get("channels", []):
            name = channel_entry.get("name", "Channel")
            idx = self._normalize_channel_index(name, channel_entry.get("index"))
            channel = MeshCoreChannel(name, index=idx)
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
                container = self._hydrate_missing_container(message_entry, container_key)
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

    def _persist_full_state_to_db(self) -> None:
        self._save_current_user()
        for channel in self._channels:
            self._save_channel(channel)
        for contact in self._contacts:
            self._save_contact(contact)
        for key, messages in self._messages.items():
            container = self._container_cache.get(key)
            if not container:
                continue
            for message in messages:
                hash_value = self._compute_message_hash(message)
                self._save_message_record(container, message, hash_value)

    def _save_current_user(self) -> None:
        self._set_metadata_value("current_user_name", self.current_user.name)
        self._set_metadata_value("current_user_public_key", self.current_user.public_key)

    def _save_channel(self, channel: MeshCoreChannel) -> None:
        key = self._container_key(channel)
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO channels (container_key, name, channel_index)
                VALUES (?, ?, ?)
                ON CONFLICT(container_key)
                DO UPDATE SET name=excluded.name, channel_index=excluded.channel_index
                """,
                (key, channel.name, channel.index),
            )

    def _save_contact(self, node: MeshCoreNode) -> None:
        key = self._container_key(node)
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO contacts (container_key, display_name, public_key)
                VALUES (?, ?, ?)
                ON CONFLICT(container_key)
                DO UPDATE SET display_name=excluded.display_name, public_key=excluded.public_key
                """,
                (key, node.name, node.public_key),
            )

    def _save_message_record(
        self,
        container: BaseContainerItem,
        message: BaseMessage,
        hash_value: str,
    ) -> None:
        key = self._container_key(container)
        message_type = "channel" if isinstance(container, MeshCoreChannel) else "user"
        sender = getattr(message, "sender", None)
        receiver = getattr(message, "receiver", None)
        path_hops = getattr(message, "path_hops", None)
        channel_name = container.name if isinstance(container, MeshCoreChannel) else None
        channel_index = container.index if isinstance(container, MeshCoreChannel) else None
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO messages (
                    container_key,
                    type,
                    text,
                    timestamp,
                    sender_name,
                    sender_public_key,
                    receiver_name,
                    receiver_public_key,
                    channel_name,
                    channel_index,
                    repeat_count,
                    message_hash,
                    path_hops
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(container_key, message_hash)
                DO UPDATE SET repeat_count=excluded.repeat_count
                """,
                (
                    key,
                    message_type,
                    message.text,
                    message.timestamp.isoformat() if message.timestamp else None,
                    getattr(sender, "name", None),
                    getattr(sender, "public_key", None),
                    getattr(receiver, "name", None),
                    getattr(receiver, "public_key", None),
                    channel_name,
                    channel_index,
                    getattr(message, "repeat_count", 1),
                    hash_value,
                    path_hops,
                ),
            )

    def _update_message_repeat(self, container_key: str, hash_value: str, repeat_count: int) -> None:
        with self._conn:
            self._conn.execute(
                """
                UPDATE messages
                SET repeat_count = ?
                WHERE container_key = ? AND message_hash = ?
                """,
                (repeat_count, container_key, hash_value),
            )

    def _get_metadata_value(self, key: str) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM metadata WHERE key = ?",
            (key,),
        ).fetchone()
        return row["value"] if row else None

    def _set_metadata_value(self, key: str, value: str | None) -> None:
        with self._conn:
            if value is None:
                self._conn.execute("DELETE FROM metadata WHERE key = ?", (key,))
            else:
                self._conn.execute(
                    """
                    INSERT INTO metadata (key, value)
                    VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value
                    """,
                    (key, value),
                )

    def _local_now(self) -> datetime:
        return datetime.now().astimezone()

    def _to_local(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value
        return value.astimezone()

    def _notify(self, update: DataUpdate) -> None:
        for listener in list(self._listeners):
            try:
                listener(update)
            except Exception:  # pragma: no cover - UI level errors
                logger.exception("Data store listener failed")

    def _notify_container_update(self, container: BaseContainerItem) -> None:
        if self._loading:
            return
        self._notify(DataUpdate("update", container, None))

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
        sender, text = self._resolve_sender(payload)
        message = ChannelMessage(
            channel,
            text,
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

    def _resolve_sender(self, payload: dict) -> tuple[MeshCoreNode, str]:
        text = payload.get("text", "") or ""
        contact = payload.get("contact")
        if isinstance(contact, MeshCoreContactInfo):
            return self._store.upsert_contact(contact), text
        prefix = payload.get("sender_prefix") or payload.get("pubkey_prefix")
        if isinstance(prefix, str) and prefix:
            return MeshCoreNode(prefix), text
        if isinstance(text, str) and ":" in text:
            leading, remainder = text.split(":", 1)
            leading = leading.strip()
            if leading:
                return MeshCoreNode(leading), remainder.lstrip()
        return MeshCoreNode("Unknown sender"), text

    def _timestamp_from_payload(self, payload: dict) -> datetime:
        raw = payload.get("timestamp") or payload.get("sender_timestamp")
        if isinstance(raw, (int, float)):
            dt = datetime.fromtimestamp(raw, tz=timezone.utc)
            return dt.astimezone()
        if isinstance(raw, str):
            try:
                dt = datetime.fromisoformat(raw)
            except ValueError:
                pass
            else:
                return dt if dt.tzinfo is None else dt.astimezone()
        return datetime.now(timezone.utc).astimezone()

    def _annotate_message_metadata(self, message: BaseMessage, payload: dict) -> None:
        hops = payload.get("path_len")
        if hops is not None:
            setattr(message, "path_hops", hops)
