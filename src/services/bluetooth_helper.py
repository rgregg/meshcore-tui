from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


async def disconnect_bluetooth_device(address: str, adapter: str = "hci0", timeout: float = 5.0) -> bool:
    """Request BlueZ to disconnect the device with the given MAC address using busctl."""
    if not address:
        return False
    normalized = address.upper().replace(":", "_")
    path = f"/org/bluez/{adapter}/dev_{normalized}"
    cmd = [
        "busctl",
        "--system",
        "call",
        "org.bluez",
        path,
        "org.bluez.Device1",
        "Disconnect",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        logger.warning("busctl not available; cannot disconnect bluetooth device.")
        return False
    except Exception as exc:
        logger.warning("Failed to invoke busctl for bluetooth disconnect: %s", exc)
        return False
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("Bluetooth disconnect command timed out for %s", address)
        proc.kill()
        return False
    success = proc.returncode == 0
    if success:
        logger.info("Requested bluetooth disconnect for %s via BlueZ.", address)
    else:
        err_text = (stderr or b"").decode().strip()
        logger.warning("Bluetooth disconnect for %s failed: %s", address, err_text)
    return success
