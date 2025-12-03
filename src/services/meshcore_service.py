"""Adapters around the MeshCore SDK."""
from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

from meshcore import MeshCore, EventType
from meshcore.events import Event

from services.config_service import ConfigService, MeshcoreConfig

logger = logging.getLogger(__name__)

Listener = Callable[[Any], Optional[Awaitable[None]]]


@dataclass
class MeshCoreChannelInfo:
    index: int
    name: str
    secret: bytes | None = None


@dataclass
class MeshCoreContactInfo:
    public_key: str
    display_name: str
    raw: dict[str, Any]


class MeshCoreService:
    """Manages a MeshCore session and exposes high-level hooks."""

    def __init__(self, config_service: ConfigService) -> None:
        self._config_service = config_service
        self._meshcore: MeshCore | None = None
        self._contacts: Dict[str, MeshCoreContactInfo] = {}
        self._channels: Dict[int, MeshCoreChannelInfo] = {}
        self._contact_listeners: List[Listener] = []
        self._channel_listeners: List[Listener] = []
        self._contact_message_listeners: List[Listener] = []
        self._channel_message_listeners: List[Listener] = []
        self._ready = asyncio.Event()
        self._running = False
        self._channel_refresh_task: asyncio.Task[None] | None = None

    @property
    def config(self) -> MeshcoreConfig:
        return self._config_service.config.meshcore

    @property
    def is_connected(self) -> bool:
        return bool(self._meshcore and self._meshcore.is_connected)

    async def start(self) -> None:
        if self._running:
            return
        transport = self.config.companion.transport.lower()
        if transport == "fake":
            logger.info("MeshCore transport set to fake; skipping connection.")
            return
        try:
            logger.info("Connecting to MeshCore via %s", transport)
            self._meshcore = await self._build_connection()
            self._meshcore.auto_update_contacts = True
            self._wire_event_handlers(self._meshcore)
            await self._meshcore.commands.send_appstart()
            await self._meshcore.ensure_contacts()
            await self._meshcore.commands.send_device_query()
            await self.refresh_channels()
            await self._meshcore.start_auto_message_fetching()
            self._running = True
            self._ready.set()
            self._channel_refresh_task = asyncio.create_task(self._channel_refresh_loop())
        except Exception as exc:  # pragma: no cover
            logger.exception("Failed to initialize MeshCore", exc_info=exc)
            raise

    async def stop(self) -> None:
        if not self._running or not self._meshcore:
            return
        self._running = False
        if self._channel_refresh_task:
            self._channel_refresh_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._channel_refresh_task
            self._channel_refresh_task = None
        await self._meshcore.stop_auto_message_fetching()
        await self._meshcore.connection_manager.disconnect()

    def add_contact_listener(self, listener: Listener) -> None:
        self._contact_listeners.append(listener)
        if self._contacts:
            self._call_listener(listener, list(self._contacts.values()))

    def add_channel_listener(self, listener: Listener) -> None:
        self._channel_listeners.append(listener)
        if self._channels:
            self._call_listener(listener, list(self._channels.values()))

    def add_contact_message_listener(self, listener: Listener) -> None:
        self._contact_message_listeners.append(listener)

    def add_channel_message_listener(self, listener: Listener) -> None:
        self._channel_message_listeners.append(listener)

    async def refresh_channels(self) -> Sequence[MeshCoreChannelInfo]:
        meshcore = self._meshcore
        if not meshcore:
            return []
        info_event = await meshcore.commands.send_device_query()
        max_channels = info_event.payload.get("max_channels", 0)
        channels: Dict[int, MeshCoreChannelInfo] = {}
        for idx in range(max_channels):
            event = await meshcore.commands.get_channel(idx)
            if event.type != EventType.CHANNEL_INFO:
                continue
            channel_name = event.payload.get("channel_name", f"Channel {idx}")
            channels[idx] = MeshCoreChannelInfo(
                index=idx,
                name=channel_name,
                secret=event.payload.get("channel_secret"),
            )
        if channels:
            self._channels = channels
            await self._notify(self._channel_listeners, list(self._channels.values()))
        return list(channels.values())

    async def send_direct_message(self, public_key: str, text: str) -> None:
        await self._ready.wait()
        if not self._meshcore:
            raise RuntimeError("MeshCore connection unavailable")
        await self._meshcore.commands.send_msg(public_key, text)

    async def send_channel_message(self, channel_index: int, text: str) -> None:
        await self._ready.wait()
        if not self._meshcore:
            raise RuntimeError("MeshCore connection unavailable")
        await self._meshcore.commands.send_chan_msg(channel_index, text)

    def _wire_event_handlers(self, meshcore: MeshCore) -> None:
        meshcore.subscribe(EventType.CONTACTS, self._handle_contacts)
        meshcore.subscribe(EventType.NEW_CONTACT, self._handle_new_contact)
        meshcore.subscribe(EventType.CONTACT_MSG_RECV, self._handle_contact_message)
        meshcore.subscribe(EventType.CHANNEL_MSG_RECV, self._handle_channel_message)

    async def _handle_contacts(self, event: Event) -> None:
        contacts = {}
        for public_key, payload in event.payload.items():
            display_name = payload.get("adv_name") or public_key[:8]
            contacts[public_key] = MeshCoreContactInfo(
                public_key=public_key,
                display_name=display_name,
                raw=payload,
            )
        self._contacts.update(contacts)
        await self._notify(self._contact_listeners, list(self._contacts.values()))

    async def _handle_new_contact(self, event: Event) -> None:
        payload = event.payload
        public_key = payload.get("public_key")
        if not public_key:
            return
        info = MeshCoreContactInfo(
            public_key=public_key,
            display_name=payload.get("adv_name") or public_key[:8],
            raw=payload,
        )
        self._contacts[public_key] = info
        await self._notify(self._contact_listeners, list(self._contacts.values()))

    async def _handle_contact_message(self, event: Event) -> None:
        prefix = event.payload.get("pubkey_prefix", "")
        contact = self._find_contact_by_prefix(prefix)
        data = {
            "contact": contact,
            "text": event.payload.get("text", ""),
            "timestamp": event.payload.get("sender_timestamp"),
        }
        await self._notify(self._contact_message_listeners, data)

    async def _handle_channel_message(self, event: Event) -> None:
        idx = event.payload.get("channel_idx")
        channel = self._channels.get(idx)
        data = {
            "channel": channel,
            "text": event.payload.get("text", ""),
            "timestamp": event.payload.get("sender_timestamp"),
        }
        await self._notify(self._channel_message_listeners, data)

    def _find_contact_by_prefix(self, prefix: str) -> Optional[MeshCoreContactInfo]:
        if not prefix:
            return None
        prefix = prefix.lower()
        for key, contact in self._contacts.items():
            if key.lower().startswith(prefix):
                return contact
        return None

    async def _channel_refresh_loop(self) -> None:
        interval = max(5, self.config.companion.channel_refresh_seconds)
        while self._running:
            try:
                await self.refresh_channels()
            except Exception as exc:  # pragma: no cover
                logger.warning("Failed to refresh channels: %s", exc)
            await asyncio.sleep(interval)

    async def _notify(self, listeners: Sequence[Listener], payload: Any) -> None:
        for listener in list(listeners):
            self._call_listener(listener, payload)

    def _call_listener(self, listener: Listener, payload: Any) -> None:
        try:
            result = listener(payload)
            if inspect.isawaitable(result):
                asyncio.create_task(result)  # fire-and-forget for UI callbacks
        except Exception:  # pragma: no cover
            logger.exception("Listener failed")

    async def _build_connection(self) -> MeshCore:
        companion = self.config.companion
        transport = companion.transport.lower()
        if transport == "tcp":
            host, port = self._parse_tcp_endpoint(companion.endpoint)
            return await MeshCore.create_tcp(host, port, auto_reconnect=True)
        if transport == "serial":
            port = companion.device or companion.endpoint
            if not port:
                raise ValueError("Serial transport requires 'device' or 'endpoint'")
            return await MeshCore.create_serial(port)
        if transport == "bluetooth":
            device = companion.device if companion.device and companion.device != "auto" else None
            address = companion.endpoint or None
            return await MeshCore.create_ble(address=address, device=device)
        raise ValueError(f"Unsupported transport: {transport}")

    def _parse_tcp_endpoint(self, endpoint: str) -> tuple[str, int]:
        if not endpoint or ":" not in endpoint:
            raise ValueError("TCP endpoint must be host:port")
        host, port_str = endpoint.split(":", 1)
        return host, int(port_str)


__all__ = ["MeshCoreService", "MeshCoreChannelInfo", "MeshCoreContactInfo"]
