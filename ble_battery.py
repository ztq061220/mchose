"""Read battery levels from the standard Bluetooth LE Battery Service.

Windows exposes the GATT Battery Service (0x180F) / Battery Level
characteristic (0x2A19) for BLE peripherals.  This module uses the WinRT
Bluetooth APIs through the ``winrt`` package; if those packages are missing or
the machine has no BLE support it simply returns an empty list.
"""
from __future__ import annotations

import asyncio
import re
import threading
import time
from typing import Any

BATTERY_SERVICE = "0000180f-0000-1000-8000-00805f9b34fb"
BATTERY_LEVEL = "00002a19-0000-1000-8000-00805f9b34fb"

_CACHE: tuple[float, list[dict[str, Any]]] | None = None
_CACHE_SECONDS = 30.0
_CACHE_LOCK = threading.Lock()
_SCAN_THREAD: threading.Thread | None = None
_BLE_LOOP = None


def _classify(name: str) -> str:
    lower = name.lower()
    if any(token in lower for token in ("keyboard", "kbd", "g87", "g98", "k87", "k20", "ace")):
        return "keyboard"
    if any(token in lower for token in ("mouse", "k7", "v7", "v9", "r9")):
        return "mouse"
    if any(token in lower for token in ("headphone", "headset", "buds", "edifier", "耳机")):
        return "headset"
    if any(token in lower for token in ("gamepad", "controller", "手柄")):
        return "gamepad"
    return "generic"


def _address_from_id(device_id: str) -> str:
    found = re.search(r"_([0-9a-f]{12})#", device_id.lower())
    if not found:
        return ""
    raw = found.group(1)
    return ":".join(raw[i:i + 2] for i in range(0, 12, 2)).upper()


async def _read_battery(device_id: str, name: str) -> int | None:
    from winrt.windows.devices.bluetooth import BluetoothConnectionStatus, BluetoothLEDevice
    from winrt.windows.storage.streams import DataReader

    device = await BluetoothLEDevice.from_id_async(device_id)
    if device is None:
        return None
    try:
        if device.connection_status != BluetoothConnectionStatus.CONNECTED:
            return None
        services = await device.get_gatt_services_async()
        for service in services.services:
            if str(service.uuid).lower() != BATTERY_SERVICE:
                continue
            characteristics = await service.get_characteristics_async()
            for characteristic in characteristics.characteristics:
                if str(characteristic.uuid).lower() != BATTERY_LEVEL:
                    continue
                result = await characteristic.read_value_async()
                if result.value is None or result.value.length < 1:
                    continue
                value = DataReader.from_buffer(result.value).read_byte()
                return max(0, min(100, int(value)))
    finally:
        try:
            device.close()
        except Exception:
            pass
    return None


async def _scan() -> list[dict[str, Any]]:
    from winrt.windows.devices.enumeration import DeviceInformation

    infos = await DeviceInformation.find_all_async()
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for info in infos:
        device_id = info.id or ""
        if "BTHLE" not in device_id.upper() or BATTERY_SERVICE not in device_id.lower():
            continue
        name = str(info.name or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        try:
            battery = await asyncio.wait_for(_read_battery(device_id, name), timeout=2.5)
        except Exception:
            continue
        if battery is None:
            continue
        found.append({
            "type": _classify(name),
            "name": name,
            "battery": battery,
            "charging": False,
            "connected": True,
            "status": "蓝牙 BLE",
            "note": f"标准 BLE Battery Service · {_address_from_id(device_id)}",
        })
    return found


def _scan_worker() -> None:
    """Keep one WinRT event loop alive and refresh the cache periodically.

    ``asyncio.run`` closes its loop when the coroutine returns; WinRT can
    deliver GATT callbacks just after that and raise "Event loop is closed".
    A persistent loop in a daemon thread avoids that.
    """
    global _CACHE, _BLE_LOOP
    loop = asyncio.new_event_loop()
    _BLE_LOOP = loop
    asyncio.set_event_loop(loop)
    while True:
        try:
            devices = loop.run_until_complete(asyncio.wait_for(_scan(), timeout=12.0))
        except Exception:
            devices = []
        with _CACHE_LOCK:
            _CACHE = (time.monotonic(), devices)
        time.sleep(_CACHE_SECONDS)


def get_ble_devices(max_age: float = _CACHE_SECONDS) -> list[dict[str, Any]]:
    """Return cached BLE devices and keep a background scan alive.

    The WinRT GATT reads can take ~10 s and occasionally stall; never block the
    Qt status thread on them.  A first call returns [] while the scan runs, and
    the next widget refresh picks up the result.
    """
    global _SCAN_THREAD
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE
        if cached is not None and now - cached[0] < max_age:
            return [dict(item) for item in cached[1]]
        if _SCAN_THREAD is None or not _SCAN_THREAD.is_alive():
            _SCAN_THREAD = threading.Thread(target=_scan_worker, name="ble-battery-scan", daemon=True)
            _SCAN_THREAD.start()
        if cached is not None:
            return [dict(item) for item in cached[1]]
    return []
