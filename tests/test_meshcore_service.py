import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from services.config_service import AppConfig
from services.meshcore_service import MeshCoreService, EventType


class FakeConfigService:
    def __init__(self):
        self.config = AppConfig()


class FakeCommands:
    def __init__(self):
        self.calls = []

    async def get_contacts(self, lastmod=0, timeout=5):
        self.calls.append((lastmod, timeout))
        await asyncio.sleep(0)
        return make_event(EventType.CONTACTS, {"last_mod": lastmod + 1})


class FakeMeshCore:
    def __init__(self):
        self.is_connected = True
        self.commands = FakeCommands()


def make_event(event_type, payload):
    return SimpleNamespace(type=event_type, payload=payload)


def test_refresh_contacts_waits_for_contacts_event():
    async def _run():
        service = MeshCoreService(FakeConfigService())
        fake_mesh = FakeMeshCore()
        service._meshcore = fake_mesh  # type: ignore[attr-defined]

        task = asyncio.create_task(service.refresh_contacts(set_idle_status=False))
        await asyncio.sleep(0)
        await service._handle_contacts(make_event(EventType.CONTACTS, {}))
        await asyncio.wait_for(task, timeout=1)

        assert fake_mesh.commands.calls
        assert service._contacts_ready.is_set()

    asyncio.run(_run())


def test_process_pending_event_routes_channel_messages():
    async def _run():
        service = MeshCoreService(FakeConfigService())
        handler = AsyncMock()
        service._handle_channel_message = handler  # type: ignore[assignment]

        event = make_event(EventType.CHANNEL_MSG_RECV, {"channel_idx": 0})
        await service._process_pending_event(event)

        handler.assert_awaited_once_with(event)

    asyncio.run(_run())
