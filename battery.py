"""Read MCHOSE HUB battery data from its Local Storage LevelDB cache."""
from __future__ import annotations

import os
import re
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


def build_device(kind: str, key: str | None, record: dict[str, Any] | None) -> Device:
    fallback = "键盘" if kind == "keyboard" else "鼠标"
    name = product_name(key) or (record or {}).get("name") or fallback
    battery = record.get("battery") if record and isinstance(record.get("battery"), (int, float)) else None
    charging = bool(record and record.get("chargeStatus") == 1)
    connected = None
    if record and record.get("connectStatus") is not None:
        connected = record.get("connectStatus") != 0
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
    note = ""
    if kind == "keyboard" and battery is None:
        note = "有线键盘没有电池，不显示电量属正常；无线键盘请确认 MCHOSE HUB 已读取。"
    return Device(kind, name, battery, charging, connected, status, note)


def get_status() -> dict[str, Any]:
    files = list_storage_files()
    if not files:
        return {
            "ok": False,
            "updatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
            "source": str(STORAGE_DIR),
            "error": "未找到 MCHOSE HUB 本地缓存，请先启动 MCHOSE HUB",
            "devices": [build_device("keyboard", None, None).as_dict(), build_device("mouse", None, None).as_dict()],
            "meta": {"files": 0, "latestFile": None, "records": 0, "descriptors": 0},
        }
    latin1, utf16 = read_texts(files)
    records = parse_battery_records(latin1, utf16)
    descriptors = parse_descriptors(files)
    keyboard_descriptor = find_keyboard_descriptor(descriptors)
    mouse_descriptor = find_mouse_descriptor(descriptors)
    keyboard_key = keyboard_descriptor.key if keyboard_descriptor else None
    mouse_key = mouse_descriptor.key if mouse_descriptor else None
    keyboard_record = find_record(records, keyboard_key) or descriptor_record(keyboard_descriptor)
    mouse_record = find_record(records, mouse_key) or descriptor_record(mouse_descriptor)
    return {
        "ok": True,
        "updatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": str(STORAGE_DIR),
        "devices": [build_device("keyboard", keyboard_key, keyboard_record).as_dict(), build_device("mouse", mouse_key, mouse_record).as_dict()],
        "meta": {
            "files": len(files),
            "latestFile": files[-1].name,
            "records": len(records),
            "descriptors": len(descriptors),
        },
    }


if __name__ == "__main__":
    import json
    print(json.dumps(get_status(), ensure_ascii=False, indent=2))
