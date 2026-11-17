"""Read MCHOSE HUB battery data from its Local Storage LevelDB cache."""
from __future__ import annotations

import os
import re
import subprocess
import time

try:
    import ble_battery
except Exception:  # BLE support is optional on non-Windows / minimal installs
    ble_battery = None
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

APP_NAME = "MCHOSE Battery Widget"
STORAGE_DIR = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "MCHOSEHUB" / "Local Storage" / "leveldb"

KEY_RE = re.compile(r"([A-Za-z][A-Za-z0-9 +._()\-]{1,60})\$\$\$\d+")
BATTERY_RE = re.compile(r'"([^"]{2,80})":\{"templateList":\[\{([\s\S]{0,6000}?)"batteryLevel":(\d+)')
DESCRIPTOR_START_RE = re.compile(r'\{"(?:vendorId|productKey)":')
PRODUCT_KEY_RE = re.compile(r'"productKey":"([^"]*)"')
DEVICE_NAME_RE = re.compile(r'"deviceName":"([^"]*)"')
PRODUCT_NAME_RE = re.compile(r'"productName":"([^"]*)"')
BATTERY_LEVEL_RE = re.compile(r'"batteryLevel":(?:(\d+)|"([^"]*)")')
CONNECT_MODE_RE = re.compile(r'"connectMode":(\d+)')
CONNECT_STATUS_RE = re.compile(r'"connectStatus":(\d+)')
CHARGE_STATUS_RE = re.compile(r'"chargeStatus":(\d+)')


@dataclass
class Device:
    type: str
    name: str
    battery: int | None
    charging: bool
    connected: bool | None
    status: str
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Descriptor:
    key: str
    device_name: str
    product_name: str
    battery: int | None
    connect_mode: int | None


_USB_CACHE: dict[tuple[int, int], tuple[float, bool]] = {}
_USB_CACHE_TTL = 20.0


def _usb_ids(key: str) -> tuple[int, int] | None:
    """Extract the decimal VID/PID encoded in a HUB product key."""
    match = re.search(r"\$\$\$(\d+)\$\$\$(\d+)", str(key or ""))
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _usb_present(vid: int, pid: int) -> bool:
    """Check whether the USB receiver/device is currently present in Windows.

    Some 2.4G keyboards (for example the K87 V3 2.4G) have a HUB descriptor
    but no ``connectStatus`` record until HUB refreshes its battery cache.
    Their USB receiver is still visible to Windows, so use that as a safe
    fallback instead of showing every stale keyboard descriptor forever.
    """
    now = time.monotonic()
    cached = _USB_CACHE.get((vid, pid))
    if cached and now - cached[0] < _USB_CACHE_TTL:
        return cached[1]
    present = False
    if os.name == "nt":
        query = (
            "@(Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | "
            f"Where-Object {{ $_.InstanceId -match 'VID_{vid:04X}&PID_{pid:04X}' }}).Count"
        )
        try:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", query],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=4,
                creationflags=creationflags,
            )
            present = result.stdout.strip() not in ("", "0")
        except (OSError, subprocess.SubprocessError):
            present = False
    _USB_CACHE[(vid, pid)] = (now, present)
    return present


def product_name(value: str | None) -> str:
    if not value:
        return ""
    return str(value).split("$$$", 1)[0].strip()


def list_storage_files() -> list[Path]:
    try:
        return sorted(
            [p for p in STORAGE_DIR.iterdir() if p.is_file() and p.suffix.lower() in (".ldb", ".log")],
            key=lambda p: p.stat().st_mtime,
        )
    except OSError:
        return []


def read_texts(files: list[Path]) -> tuple[str, str]:
    latin1_parts: list[str] = []
    utf16_parts: list[str] = []
    for file in files:
        try:
            if file.stat().st_size > 24 * 1024 * 1024:
                continue
            data = file.read_bytes()
        except OSError:
            continue
        latin1_parts.append(data.decode("latin1", errors="replace"))
        utf16_parts.append(data.decode("utf-16le", errors="replace"))
    return "\n".join(latin1_parts), "\n".join(utf16_parts)


def parse_descriptor_text(text: str) -> list[Descriptor]:
    # Descriptor key order differs between device types: some start with
    # vendorId, others with productKey. Anchor on productKey and inspect the
    # following JSON, which contains colorList then batteryLevel/connectMode.
    starts = [m.start() for m in DESCRIPTOR_START_RE.finditer(text)]
    result: list[Descriptor] = []
    for index, start in enumerate(starts):
        end = min(starts[index + 1] if index + 1 < len(starts) else len(text), start + 50_000, len(text))
        chunk = text[start:end]
        key_match = PRODUCT_KEY_RE.search(chunk)
        if not key_match:
            continue
        device_match = DEVICE_NAME_RE.search(chunk)
        product_match = PRODUCT_NAME_RE.search(chunk)
        battery_match = BATTERY_LEVEL_RE.search(chunk)
        connect_match = CONNECT_MODE_RE.search(chunk)
        battery = None
        if battery_match:
            battery = int(battery_match.group(1)) if battery_match.group(1) is not None else None
        result.append(
            Descriptor(
                key=key_match.group(1),
                device_name=device_match.group(1) if device_match else "",
                product_name=product_match.group(1) if product_match else "",
                battery=battery,
                connect_mode=int(connect_match.group(1)) if connect_match else None,
            )
        )
    return result


def parse_descriptors(files: list[Path]) -> list[Descriptor]:
    marker = b"listTempAfter"
    json_start = bytes((0x5B, 0x00, 0x7B, 0x00, 0x22, 0x00))
    result: list[Descriptor] = []
    for file in files:
        try:
            if file.stat().st_size > 24 * 1024 * 1024:
                continue
            data = file.read_bytes()
        except OSError:
            continue
        position = 0
        while True:
            position = data.find(marker, position)
            if position < 0:
                break
            start = data.find(json_start, position)
            if start >= 0:
                chunk = data[start:min(len(data), start + 300_000)].decode("utf-16le", errors="replace")
                result.extend(parse_descriptor_text(chunk))
            position += len(marker)
    return result


def parse_battery_records(latin1: str, utf16: str) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for text in (latin1, utf16):
        for match in BATTERY_RE.finditer(text):
            name = product_name(match.group(1))
            body = match.group(2) or ""
            connect = CONNECT_STATUS_RE.search(body)
            charge = CHARGE_STATUS_RE.search(body)
            records[name] = {
                "name": name,
                "battery": max(0, min(100, int(match.group(3)))),
                "connectStatus": int(connect.group(1)) if connect else None,
                "chargeStatus": int(charge.group(1)) if charge else None,
            }
    return records


def find_keyboard_descriptor(descriptors: list[Descriptor]) -> Descriptor | None:
    primary = [d for d in descriptors if re.search(r"(^|\s)(G\d{2}|Ace)\b", f"{d.device_name} {d.product_name}", re.I)]
    secondary = [d for d in descriptors if re.search(r"(^|\s)K(?!7)\d{2}\b", f"{d.device_name} {d.product_name}", re.I)]
    primary.reverse()
    secondary.reverse()
    # Prefer an actual G-series/Ace keyboard over the K87 alias sharing a product id.
    if primary:
        return next((d for d in primary if d.battery is not None), primary[0])
    return next((d for d in secondary if d.battery is not None), secondary[0] if secondary else None)


def find_mouse_descriptor(descriptors: list[Descriptor]) -> Descriptor | None:
    candidates = [d for d in descriptors if re.search(r"(^|\s)(K7|V7|V9)\b", f"{d.device_name} {d.product_name}", re.I)]
    candidates.reverse()
    return next((d for d in candidates if d.battery is not None), candidates[0] if candidates else None)


def descriptor_record(descriptor: Descriptor | None) -> dict[str, Any] | None:
    if descriptor is None:
        return None
    return {
        "name": descriptor.key,
        "battery": descriptor.battery,
        "connectStatus": None if descriptor.connect_mode is None else (0 if descriptor.connect_mode == 0 else 1),
        "chargeStatus": None,
    }


def find_record(records: dict[str, dict[str, Any]], key: str | None) -> dict[str, Any] | None:
    name = product_name(key)
    if not name:
        return None
    if name in records:
        return records[name]
    lower = name.lower()
    for record_name, record in records.items():
        if record_name.lower() == lower:
            return record
    for record_name, record in records.items():
        if record_name.lower() in lower or lower in record_name.lower():
            return record
    return None


TYPE_LABELS = {
    "keyboard": "键盘",
    "mouse": "鼠标",
    "headset": "耳机",
    "gamepad": "手柄",
    "generic": "外设",
}


def infer_type(*names: str) -> str:
    text = " ".join(str(n or "") for n in names).lower()
    if any(token in text for token in ("keyboard", "kbd", "g87", "g98", "k87", "k20", "ace")):
        return "keyboard"
    if any(token in text for token in ("mouse", " k7", "v7", "v9", "r9")):
        return "mouse"
    if any(token in text for token in ("headphone", "headset", "buds", "edifier", "耳机")):
        return "headset"
    if any(token in text for token in ("gamepad", "controller", "手柄")):
        return "gamepad"
    return "generic"


def collect_mchose_devices(records: dict[str, dict[str, Any]], descriptors: list[Descriptor]) -> list[Device]:
    """Return every unique HUB device, not just the current keyboard/mouse."""
    items: dict[str, tuple[str, str, Descriptor | None, dict[str, Any] | None]] = {}
    for descriptor in descriptors:
        name = product_name(descriptor.key)
        if not name:
            continue
        normalized = name.lower()
        if normalized in items:
            continue
        items[normalized] = (infer_type(descriptor.device_name, descriptor.product_name, name), name, descriptor, None)
    for name, record in records.items():
        normalized = name.lower()
        if normalized in items:
            continue
        items[normalized] = (infer_type(name), name, None, record)

    devices: list[Device] = []
    for normalized, (kind, display_name, descriptor, record_hint) in items.items():
        record = find_record(records, normalized) or record_hint or descriptor_record(descriptor)
        battery_value = record.get("battery") if record else None
        if battery_value is None and kind not in ("keyboard", "mouse"):
            continue
        # A 2.4G keyboard may be present in Windows while HUB has only a
        # descriptor and no connectStatus/battery record yet.  Show it as
        # connected but without a percentage, rather than hiding it.
        if descriptor is not None and battery_value is None and kind == "keyboard":
            ids = _usb_ids(descriptor.key)
            if ids and _usb_present(*ids):
                record = dict(record or descriptor_record(descriptor) or {})
                record["connectStatus"] = 2
                record["_status"] = "已连接 · 未读取电量"
                record["_note"] = "MCHOSE HUB 暂未上报这把键盘的电量；2.4G 接收器已连接。"
        devices.append(build_device(kind, display_name, record))

    return devices


def build_device(kind: str, key: str | None, record: dict[str, Any] | None) -> Device:
    fallback = TYPE_LABELS.get(kind, "外设")
    name = product_name(key) or (record or {}).get("name") or fallback
    battery = record.get("battery") if record and isinstance(record.get("battery"), (int, float)) else None
    charging = bool(record and record.get("chargeStatus") == 1)
    connected = None
    if record and record.get("connectStatus") is not None:
        # MCHOSE HUB uses 2 for the currently active connection and 1 for a
        # cached/passive record.  Treat only 2 as connected so stale entries
        # for powered-off devices are not shown.
        connected = record.get("connectStatus") == 2
    status = "未检测到设备"
    if record:
        if battery is None:
            # A wired keyboard has no battery at all; this is normal, not an
            # error.  Use neutral copy so the UI does not look broken.
            status = "不显示电量" if kind == "keyboard" else "未检测到电量"
        elif charging:
            status = "充电中"
        elif connected is False:
            status = "未连接"
        else:
            status = "已连接"
    elif key:
        status = "不显示电量" if kind == "keyboard" else "未检测到电量"
    note = (record or {}).get("_note", "")
    if not note and kind == "keyboard" and battery is None:
        note = "有线键盘没有电池，不显示电量属正常；无线键盘请确认 MCHOSE HUB 已读取。"
    status = (record or {}).get("_status") or status
    return Device(kind, name, battery, charging, connected, status, note)


def get_status() -> dict[str, Any]:
    files = list_storage_files()
    latin1, utf16 = read_texts(files) if files else ("", "")
    records = parse_battery_records(latin1, utf16) if files else {}
    descriptors = parse_descriptors(files) if files else []
    devices = collect_mchose_devices(records, descriptors)

    ble_devices: list[dict[str, Any]] = []
    if ble_battery is not None:
        try:
            ble_devices = ble_battery.get_ble_devices()
        except Exception:
            ble_devices = []
    existing = {d.name.lower() for d in devices}
    for device in ble_devices:
        if str(device.get("name", "")).lower() not in existing:
            devices.append(Device(
                str(device.get("type") or "generic"),
                str(device.get("name") or "蓝牙外设"),
                device.get("battery"),
                bool(device.get("charging")),
                device.get("connected"),
                str(device.get("status") or "蓝牙 BLE"),
                str(device.get("note") or ""),
            ))
            existing.add(str(device.get("name", "")).lower())

    # Hide stale cache entries: only show devices that the HUB or Windows
    # currently reports as connected.
    detected_count = len(devices)
    devices = [device for device in devices if device.connected is True]

    devices.sort(key=lambda d: (
        {"keyboard": 0, "mouse": 1, "headset": 2, "gamepad": 3}.get(d.type, 4),
        -(d.battery if d.battery is not None else -1),
        d.name.lower(),
    ))

    source = str(STORAGE_DIR) if files else "Bluetooth LE"
    if files and ble_devices:
        source = "MCHOSE HUB + Bluetooth LE"
    elif ble_devices:
        source = "Bluetooth LE"
    error = None
    if not files and not ble_devices:
        error = "未找到 MCHOSE HUB 本地缓存，也未发现标准 BLE 电池设备"
    elif not devices and detected_count:
        error = "已发现设备，但当前都没有连接；连接后会显示电量"
    elif not devices:
        error = "未发现支持电量读取的外设；请启动 MCHOSE HUB 或连接支持标准 BLE 电池服务的设备"

    return {
        "ok": bool(devices),
        "updatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": source,
        "error": error,
        "devices": [device.as_dict() for device in devices],
        "meta": {
            "files": len(files),
            "latestFile": files[-1].name if files else None,
            "records": len(records),
            "descriptors": len(descriptors),
            "ble": len(ble_devices),
        },
    }


if __name__ == "__main__":
    import json
    print(json.dumps(get_status(), ensure_ascii=False, indent=2))
