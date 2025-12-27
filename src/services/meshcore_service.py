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

from services.config_service import (
    CompanionConnectionConfig,
    ConfigService,
    MeshcoreConfig,
)

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


@dataclass
class MeshCoreStatus:
    message: str
    current: int = 0
    total: int = 0
    state: str = "idle"


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
        self._status = MeshCoreStatus(message="Disconnected", state="disconnected")

    @property
    def config(self) -> MeshcoreConfig:
        return self._config_service.config.meshcore

    @property
    def is_connected(self) -> bool:
        return bool(self._meshcore and self._meshcore.is_connected)

    @property
    def status(self) -> MeshCoreStatus:
        return self._status

    async def start(self) -> None:
        if self._running:
            return
        transport = self.config.companion.transport.lower()
        if transport == "fake":
            logger.info("MeshCore transport set to fake; skipping connection.")
            self._set_status("Fake data mode", state="fake")
            return
        self._set_status("Connecting to MeshCore…", state="connecting")
        try:
            logger.info("Connecting to MeshCore via %s", transport)
            self._meshcore = await self._build_connection()
            self._meshcore.auto_update_contacts = True
            self._wire_event_handlers(self._meshcore)
            await self._meshcore.commands.send_appstart()
            self._set_status("Loading contacts…", state="loading_contacts")
            await self._meshcore.ensure_contacts()
            await self._meshcore.commands.send_device_query()
            self._set_status("Refreshing channels…", state="refreshing_channels")
            await self.refresh_channels(set_idle_status=False)
            await self._drain_pending_messages()
            await self._meshcore.start_auto_message_fetching()
            self._running = True
            self._ready.set()
            self._channel_refresh_task = asyncio.create_task(self._channel_refresh_loop())
            self._set_status("Connected", state="connected")
        except Exception as exc:  # pragma: no cover
            self._set_status(f"Connection failed: {exc}", state="error")
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
        self._set_status("Disconnected", state="disconnected")

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

    async def refresh_channels(self, *, set_idle_status: bool = True) -> Sequence[MeshCoreChannelInfo]:
        meshcore = self._meshcore
        if not meshcore or not meshcore.is_connected:
            logger.warning("Skipping channel refresh; MeshCore not connected")
            return []
        self._set_status("Refreshing channels…", 0, 0, state="refreshing_channels")
        try:
            info_event = await meshcore.commands.send_device_query()
        except Exception as exc:  # pragma: no cover - hardware specific
            logger.warning("MeshCore device query failed: %s", exc)
            if set_idle_status:
                self._set_status(
                    "Connected" if self.is_connected else "Disconnected",
                    state="connected" if self.is_connected else "disconnected",
                )
            return list(self._channels.values())
        max_channels = info_event.payload.get("max_channels", 0)
        if max_channels:
            self._set_status("Refreshing channels…", 0, max_channels, state="refreshing_channels")
        channels: Dict[int, MeshCoreChannelInfo] = {}
        logger.info("Refreshing channels; device reports max_channels=%s", max_channels)
        for idx in range(max_channels):
            try:
                event = await meshcore.commands.get_channel(idx)
            except Exception as exc:  # pragma: no cover - hardware specific
                logger.warning("Failed to fetch channel %s: %s", idx, exc)
                break
            if event.type != EventType.CHANNEL_INFO:
                self._set_status(
                    "Refreshing channels…",
                    min(idx + 1, max_channels),
                    max_channels,
                    state="refreshing_channels",
                )
                continue
            channel_name = event.payload.get("channel_name", f"Channel {idx}")
            logger.info(
                "Fetched channel idx=%s name=%s secret=%s",
                idx,
                channel_name,
                bool(event.payload.get("channel_secret")),
            )
            channels[idx] = MeshCoreChannelInfo(
                index=idx,
                name=channel_name,
                secret=event.payload.get("channel_secret"),
            )
            self._set_status(
                "Refreshing channels…",
                min(idx + 1, max_channels),
                max_channels,
                state="refreshing_channels",
            )
        if channels:
            self._channels = channels
            await self._notify(self._channel_listeners, list(self._channels.values()))
        if set_idle_status:
            if self.is_connected:
                self._set_status("Connected", state="connected")
            else:
                self._set_status("Disconnected", state="disconnected")
        return list(channels.values())

    async def send_direct_message(self, public_key: str, text: str) -> None:
        await self._ready.wait()
        if not self._meshcore:
            raise RuntimeError("MeshCore connection unavailable")
        logger.info("Sending direct message to %s: %s", public_key, text)
        await self._meshcore.commands.send_msg(public_key, text)

    async def send_channel_message(self, channel_index: int, text: str) -> None:
        await self._ready.wait()
        if not self._meshcore:
            raise RuntimeError("MeshCore connection unavailable")
        logger.info("Sending channel message to index %s: %s", channel_index, text)
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
            logger.info("Synced contact %s (%s)", display_name, public_key)
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
        logger.info("Discovered new contact %s (%s)", info.display_name, public_key)
        self._contacts[public_key] = info
        await self._notify(self._contact_listeners, list(self._contacts.values()))

    async def _handle_contact_message(self, event: Event) -> None:
        prefix = event.payload.get("pubkey_prefix", "")
        contact = self._find_contact_by_prefix(prefix)
        logger.info(
            "Received contact message from %s (%s): %s",
            prefix,
            contact.display_name if contact else "unknown",
            event.payload.get("text", ""),
        )
        data = {
            "contact": contact,
            "text": event.payload.get("text", ""),
            "timestamp": event.payload.get("sender_timestamp"),
        }
        await self._notify(self._contact_message_listeners, data)

    async def _handle_channel_message(self, event: Event) -> None:
        idx = event.payload.get("channel_idx")
        channel = self._channels.get(idx)
        prefix = event.payload.get("pubkey_prefix", "")
        contact = self._find_contact_by_prefix(prefix)
        if channel is None:
            name = event.payload.get("channel_name") or f"Channel {idx}"
            channel = MeshCoreChannelInfo(index=idx, name=name)
            self._channels[idx] = channel
            logger.warning("Channel info missing for idx %s; created placeholder '%s'", idx, name)
        logger.info(
            "Received channel message on %s from %s: %s",
            channel.name if channel else idx,
            prefix,
            event.payload.get("text", ""),
        )
        data = {
            "channel": channel,
            "text": event.payload.get("text", ""),
            "timestamp": event.payload.get("sender_timestamp"),
            "contact": contact,
            "sender_prefix": prefix,
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

    async def _drain_pending_messages(self, limit: int = 200) -> None:
        """Fetch pending channel/contact messages so panes show history on connect."""
        meshcore = self._meshcore
        if not meshcore:
            return
        drained = 0
        self._set_status("Syncing messages…", drained, limit, state="syncing")
        while drained < limit:
            try:
                event = await meshcore.commands.get_msg(timeout=2.0)
            except Exception as exc:  # pragma: no cover - hardware specific
                logger.warning("Pending message fetch failed: %s", exc)
                break
            if event.type in (EventType.NO_MORE_MSGS, EventType.ERROR):
                break
            await self._process_pending_event(event)
            drained += 1
            self._set_status("Syncing messages…", drained, limit, state="syncing")
            await asyncio.sleep(0)  # yield to the UI loop
        if drained == limit:
            logger.warning("Drained %s pending messages; stopping to avoid loops", limit)

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
            addresses = self._collect_address_candidates(companion)
            devices = self._collect_device_candidates(companion)
            last_error: Exception | None = None
            for address in addresses or [None]:
                for device in devices or [None]:
                    try:
                        return await MeshCore.create_ble(address=address, device=device)
                    except Exception as exc:  # pragma: no cover - hardware specific
                        last_error = exc
                        logger.warning(
                            "Failed BLE connect attempt (address=%s device=%s): %s",
                            address,
                            device,
                            exc,
                        )
                        await asyncio.sleep(1)
            if last_error:
                raise last_error
            raise ConnectionError("Unable to connect to MeshCore companion via BLE")
        raise ValueError(f"Unsupported transport: {transport}")

    def _parse_tcp_endpoint(self, endpoint: str) -> tuple[str, int]:
        if not endpoint or ":" not in endpoint:
            raise ValueError("TCP endpoint must be host:port")
        host, port_str = endpoint.split(":", 1)
        return host, int(port_str)

    def _collect_address_candidates(self, companion: CompanionConnectionConfig) -> list[str]:
        candidates: list[str] = []
        for value in (companion.endpoint, companion.device):
            value = (value or "").strip()
            if not value or value.lower() == "auto":
                continue
            if self._looks_like_mac(value):
                candidates.append(value)
        return candidates

    def _collect_device_candidates(self, companion: CompanionConnectionConfig) -> list[str]:
        candidates: list[str] = []
        for value in (companion.device, companion.endpoint):
            value = (value or "").strip()
            if not value or value.lower() == "auto":
                continue
            # Always include raw value as device hint; MACs may work as BLE path on some stacks
            candidates.append(value)
        return candidates

    @staticmethod
    def _looks_like_mac(value: str) -> bool:
        value = value.strip()
        if not value:
            return False
        parts = value.split(":")
        if len(parts) not in (6, 8):
            return False
        return all(len(part) in (2, 4) and all(ch in "0123456789ABCDEFabcdef" for ch in part) for part in parts)

    def _set_status(self, message: str, current: int = 0, total: int = 0, state: str | None = None) -> None:
        if total < 0:
            total = 0
        if total and current > total:
            current = total
        self._status = MeshCoreStatus(
            message=message,
            current=current,
            total=total,
            state=state or self._status.state,
        )

    async def _process_pending_event(self, event: Event) -> None:
        """Route drained message events through existing handlers."""
        if event.type == EventType.CONTACT_MSG_RECV:
            await self._handle_contact_message(event)
        elif event.type == EventType.CHANNEL_MSG_RECV:
            await self._handle_channel_message(event)
        elif event.type == EventType.CONTACTS:
            await self._handle_contacts(event)
        elif event.type == EventType.NEW_CONTACT:
            await self._handle_new_contact(event)
        else:
            logger.debug("Unhandled pending event type: %s", event.type)


__all__ = ["MeshCoreService", "MeshCoreChannelInfo", "MeshCoreContactInfo", "MeshCoreStatus"]
