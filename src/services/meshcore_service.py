"""Adapters around the MeshCore SDK."""
from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
from dataclasses import dataclass, field
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
class MeshCoreSelfInfo:
    display_name: str
    node_id: str
    tx_power: int = 0
    max_tx_power: int = 0
    adv_lat: float = 0.0
    adv_lon: float = 0.0
    radio_freq: float = 0.0
    radio_bw: float = 0.0
    radio_sf: int = 0
    radio_cr: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class AckTracker:
    code: str
    count: int = 0


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
        self._contacts_ready = asyncio.Event()
        self._contacts_ready.set()
        self._last_contacts_lastmod = 0
        self._self_info: MeshCoreSelfInfo | None = None
        self._self_listeners: List[Listener] = []
        self._radio_queue: asyncio.Queue[
            tuple[str, Callable[[], Awaitable[Any]], asyncio.Future[Any]] | None
        ] = asyncio.Queue()
        self._radio_worker: asyncio.Task[None] | None = None
        self._ack_trackers: Dict[str, AckTracker] = {}
        self._last_ack_code: str | None = None
        self._log_packets_enabled = bool(self.config.log_packets)

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
            self._running = True
            self._ensure_radio_worker()
            await self._meshcore.commands.send_appstart()
            await self.refresh_contacts(set_idle_status=False)
            await self._meshcore.commands.send_device_query()
            await self.refresh_channels(set_idle_status=False)
            await self._drain_pending_messages()
            await self._meshcore.start_auto_message_fetching()
            self._ready.set()
            self._set_status("Connected", state="connected")
        except Exception as exc:  # pragma: no cover
            self._set_status(f"Connection failed: {exc}", state="error")
            logger.exception("Failed to initialize MeshCore", exc_info=exc)
            self._running = False
            await self._stop_radio_worker()
            await self._force_bluetooth_disconnect()
            raise

    async def stop(self) -> None:
        if not self._running or not self._meshcore:
            return
        self._running = False
        await self._stop_radio_worker()
        await self._meshcore.stop_auto_message_fetching()
        await self._meshcore.connection_manager.disconnect()
        self._set_status("Disconnected", state="disconnected")

    async def _force_bluetooth_disconnect(self) -> None:
        transport = self.config.companion.transport.lower()
        if transport != "bluetooth":
            return
        address = self.config.companion.device
        if not address:
            return
        try:
            from services.bluetooth_helper import disconnect_bluetooth_device
        except Exception as exc:  # pragma: no cover - import issues
            logger.debug("Bluetooth helper unavailable: %s", exc)
            return
        try:
            success = await disconnect_bluetooth_device(address)
            if success:
                logger.info("Forced bluetooth disconnect for %s after failure", address)
        except Exception as exc:  # pragma: no cover - dbus issues
            logger.debug("Bluetooth disconnect helper raised: %s", exc)

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

    def add_self_listener(self, listener: Listener) -> None:
        self._self_listeners.append(listener)
        if self._self_info:
            self._call_listener(listener, self._self_info)

    async def refresh_contacts(self, *, set_idle_status: bool = True) -> None:
        async def _refresh() -> None:
            meshcore = self._meshcore
            if not meshcore or not meshcore.is_connected:
                logger.warning("Skipping contact refresh; MeshCore not connected")
                return
            self._set_status("Refreshing contacts…", state="refreshing_contacts")
            try:
                self._contacts_ready.clear()
                lastmod = self._last_contacts_lastmod
                event = await meshcore.commands.get_contacts(lastmod=lastmod, timeout=30)
                self._log_packet_event(event)
                if event and event.type == EventType.CONTACTS:
                    self._last_contacts_lastmod = event.payload.get("last_mod", lastmod)
                    self._contacts_ready.set()
                await asyncio.wait_for(self._contacts_ready.wait(), timeout=30)
                logger.info("Contact refresh completed")
            except asyncio.TimeoutError:
                logger.warning("Contact refresh timed out")
            except Exception as exc:  # pragma: no cover - hardware specific
                logger.warning("Contact refresh failed: %s", exc)
            finally:
                if set_idle_status:
                    self._set_status(
                        "Connected" if self.is_connected else "Disconnected",
                        state="connected" if self.is_connected else "disconnected",
                    )

        await self._run_radio_task("refresh_contacts", _refresh)

    async def refresh_channels(self, *, set_idle_status: bool = True) -> Sequence[MeshCoreChannelInfo]:
        async def _refresh() -> Sequence[MeshCoreChannelInfo]:
            meshcore = self._meshcore
            if not meshcore or not meshcore.is_connected:
                logger.warning("Skipping channel refresh; MeshCore not connected")
                return []
            self._set_status("Refreshing channels…", 0, 0, state="refreshing_channels")
            try:
                info_event = await meshcore.commands.send_device_query()
                self._log_packet_event(info_event)
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
                    self._log_packet_event(event)
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

        return await self._run_radio_task("refresh_channels", _refresh)

    async def send_direct_message(self, public_key: str, text: str) -> None:
        async def _send() -> None:
            await self._ready.wait()
            if not self._meshcore:
                raise RuntimeError("MeshCore connection unavailable")
            logger.info("Sending direct message to %s: %s", public_key, text)
            result = await self._meshcore.commands.send_msg(public_key, text)
            if result.type != EventType.ERROR:
                self._track_expected_ack(result)
            else:
                logger.error("Direct message send failed: %s", result.payload)

        await self._run_radio_task(f"direct:{public_key[:6]}", _send)

    async def send_channel_message(self, channel_index: int, text: str) -> None:
        async def _send() -> None:
            await self._ready.wait()
            if not self._meshcore:
                raise RuntimeError("MeshCore connection unavailable")
            logger.info("Sending channel message to index %s: %s", channel_index, text)
            await self._meshcore.commands.send_chan_msg(channel_index, text)
            

        await self._run_radio_task(f"channel:{channel_index}", _send)

    async def send_advert(self, *, flood: bool = False) -> None:
        async def _send() -> None:
            await self._ready.wait()
            if not self._meshcore:
                raise RuntimeError("MeshCore connection unavailable")
            logger.info("Sending MeshCore advert (flood=%s)", flood)
            await self._meshcore.commands.send_advert(flood=flood)

        name = "advert:flood" if flood else "advert"
        await self._run_radio_task(name, _send)

    def _wire_event_handlers(self, meshcore: MeshCore) -> None:
        meshcore.subscribe(EventType.CONTACTS, self._handle_contacts)
        meshcore.subscribe(EventType.NEW_CONTACT, self._handle_new_contact)
        meshcore.subscribe(EventType.CONTACT_MSG_RECV, self._handle_contact_message)
        meshcore.subscribe(EventType.CHANNEL_MSG_RECV, self._handle_channel_message)
        meshcore.subscribe(EventType.SELF_INFO, self._handle_self_info)
        meshcore.subscribe(EventType.ACK, self._handle_ack)

    def _log_packet_event(self, event: Event | None) -> None:
        if not self._log_packets_enabled or not event:
            return
        logger.info("Mesh packet %s: %s", event.type, event.payload)

    async def _handle_contacts(self, event: Event) -> None:
        self._log_packet_event(event)
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
        self._contacts_ready.set()

    async def _handle_new_contact(self, event: Event) -> None:
        self._log_packet_event(event)
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
        self._contacts_ready.set()

    async def _handle_contact_message(self, event: Event) -> None:
        self._log_packet_event(event)
        prefix = event.payload.get("pubkey_prefix", "")
        contact = self._find_contact_by_prefix(prefix)
        logger.info(
            "Received contact message from %s (%s): %s",
            prefix,
            contact.display_name if contact else "unknown",
            event.payload.get("text", ""),
        )
        logger.info("Contact message payload: %s", event.payload)
        data = {
            "contact": contact,
            "text": event.payload.get("text", ""),
            "timestamp": event.payload.get("sender_timestamp"),
            "sender_prefix": prefix,
        }
        await self._notify(self._contact_message_listeners, data)

    async def _handle_channel_message(self, event: Event) -> None:
        self._log_packet_event(event)
        logger.debug("Channel message event received from MeshCore: %s", event.payload)
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

    async def _handle_self_info(self, event: Event) -> None:
        self._log_packet_event(event)
        payload = event.payload or {}
        info = MeshCoreSelfInfo(
            display_name=(payload.get("name", "") or "").strip() or "MeshCore Operator",
            node_id=payload.get("public_key", ""),
            tx_power=payload.get("tx_power", 0) or 0,
            max_tx_power=payload.get("max_tx_power", 0) or 0,
            adv_lat=payload.get("adv_lat", 0.0) or 0.0,
            adv_lon=payload.get("adv_lon", 0.0) or 0.0,
            radio_freq=payload.get("radio_freq", 0.0) or 0.0,
            radio_bw=payload.get("radio_bw", 0.0) or 0.0,
            radio_sf=payload.get("radio_sf", 0) or 0,
            radio_cr=payload.get("radio_cr", 0) or 0,
            raw=payload,
        )
        self._self_info = info
        await self._notify(self._self_listeners, info)

    async def _handle_ack(self, event: Event) -> None:
        self._log_packet_event(event)
        payload = event.payload or {}
        code = payload.get("code") or (event.attributes or {}).get("code")
        if not code:
            return
        tracker = self._ack_trackers.get(code)
        if not tracker:
            tracker = AckTracker(code=code, count=0)
            self._ack_trackers[code] = tracker
        tracker.count += 1
        if self._last_ack_code == code:
            self._set_status(f"ACKs received ({tracker.count})", state="connected")
        logger.info("ACK received for code %s (count=%s)", code, tracker.count)

    def _track_expected_ack(self, event: Event) -> None:
        payload = event.payload or {}
        expected = payload.get("expected_ack")
        if isinstance(expected, (bytes, bytearray)):
            code = expected.hex()
        elif isinstance(expected, str):
            code = expected
        else:
            return
        tracker = AckTracker(code=code, count=0)
        self._ack_trackers[code] = tracker
        self._last_ack_code = code
        self._set_status("Awaiting ACKs (0)", state="connected")
        logger.info("Tracking ACKs for code %s", code)

    def _find_contact_by_prefix(self, prefix: str) -> Optional[MeshCoreContactInfo]:
        if not prefix:
            return None
        prefix = prefix.lower()
        for key, contact in self._contacts.items():
            if key.lower().startswith(prefix):
                return contact
        return None

    async def refresh_contacts_and_channels(self) -> None:
        await self.refresh_contacts(set_idle_status=False)
        await self.refresh_channels()

    def _ensure_radio_worker(self) -> None:
        if self._radio_worker:
            return
        self._radio_worker = asyncio.create_task(self._radio_worker_loop())

    async def _stop_radio_worker(self) -> None:
        if not self._radio_worker:
            self._radio_queue = asyncio.Queue()
            return
        await self._radio_queue.put(None)
        with contextlib.suppress(asyncio.CancelledError):
            await self._radio_worker
        self._radio_worker = None
        # Cancel any pending tasks in the old queue.
        while not self._radio_queue.empty():
            item = await self._radio_queue.get()
            if item is None:
                continue
            _, _, future = item
            if not future.done():
                future.cancel()
            self._radio_queue.task_done()
        self._radio_queue = asyncio.Queue()

    def _enqueue_radio_task(
        self,
        name: str,
        factory: Callable[[], Awaitable[Any]],
    ) -> asyncio.Future[Any]:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._radio_queue.put_nowait((name, factory, future))
        logger.info(
            "Queued radio task %s (depth=%s)",
            name,
            self._radio_queue.qsize(),
        )
        return future

    async def _run_radio_task(
        self,
        name: str,
        factory: Callable[[], Awaitable[Any]],
    ) -> Any:
        if not self._radio_worker:
            return await factory()
        future = self._enqueue_radio_task(name, factory)
        return await future

    async def _radio_worker_loop(self) -> None:
        logger.info("Starting MeshCore radio task queue")
        while self._running:
            try:
                item = await self._radio_queue.get()
            except asyncio.CancelledError:
                break
            if item is None:
                break
            name, factory, future = item
            if future.cancelled():
                self._radio_queue.task_done()
                continue
            try:
                result = await factory()
            except Exception as exc:  # pragma: no cover - hardware specific
                logger.warning("Radio task %s failed: %s", name, exc)
                if not future.done():
                    future.set_exception(exc)
            else:
                if not future.done():
                    future.set_result(result)
            finally:
                self._radio_queue.task_done()
        logger.info("Stopping MeshCore radio task queue")

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


__all__ = [
    "MeshCoreService",
    "MeshCoreChannelInfo",
    "MeshCoreContactInfo",
    "MeshCoreSelfInfo",
    "MeshCoreStatus",
]
