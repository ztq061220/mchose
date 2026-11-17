"""Read the live device cards shown in MCHOSE HUB.

MCHOSE HUB is an Electron app and exposes a local DevTools endpoint on
127.0.0.1:9222.  Its LevelDB cache can contain ``batteryLevel: "breakConnect"``
for a keyboard that is actually connected; the rendered device cards contain
the live percentage shown by the driver.  We read those cards over CDP.
"""

from __future__ import annotations

import json
import re
import time
import urllib.request

_CACHE: list[dict] = []
_CACHE_AT = 0.0
_CACHE_TTL = 1.0
_ENDPOINT = "http://127.0.0.1:9222/json/list"

_JS = r"""
(() => {
  const cards = [...document.querySelectorAll('.device-card-shell')];
  return cards.map((card) => {
    const batt = card.querySelector('.device-card-status-item.battery');
    return {
      key: card.getAttribute('data-home-card-key') || '',
      name: (card.querySelector('.device-card-name')?.innerText || '').trim(),
      battery: (batt?.querySelector('span')?.innerText || '').trim(),
      charging: !!batt?.classList.contains('is-charging'),
      connection: (card.querySelector('.device-card-status-item.connection span')?.innerText || '').trim()
    };
  });
})()
"""


def _parse_percent(text: str) -> int | None:
    match = re.search(r"(\d{1,3})\s*%", text or "")
    if not match:
        return None
    value = int(match.group(1))
    return value if 0 <= value <= 100 else None


def _read_cards() -> list[dict]:
    with urllib.request.urlopen(_ENDPOINT, timeout=1.2) as response:
        pages = json.loads(response.read().decode("utf-8", "replace"))
    page = next(
        (item for item in pages if item.get("type") == "page" and "mchose-renderer" in item.get("url", "")),
        None,
    )
    if not page:
        return []

    import websocket  # websocket-client

    ws = websocket.create_connection(
        page["webSocketDebuggerUrl"], timeout=1.5, suppress_origin=True
    )
    try:
        ws.send(json.dumps({
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {
                "expression": _JS,
                "returnByValue": True,
                "awaitPromise": True,
            },
        }))
        while True:
            message = json.loads(ws.recv())
            if message.get("id") == 1:
                result = message.get("result", {}).get("result", {})
                value = result.get("value", [])
                return value if isinstance(value, list) else []
    finally:
        ws.close()


def get_devices(max_age: float = _CACHE_TTL) -> list[dict]:
    """Return numeric-battery device cards from HUB's live UI, cached briefly."""
    global _CACHE, _CACHE_AT
    now = time.monotonic()
    if _CACHE and now - _CACHE_AT < max_age:
        return _CACHE

    try:
        cards = _read_cards()
    except Exception:
        return _CACHE if _CACHE else []

    devices: list[dict] = []
    for card in cards:
        battery = _parse_percent(card.get("battery", ""))
        if battery is None:
            continue
        name = (card.get("name") or "").strip()
        key = (card.get("key") or "").split("$$$")[0]
        if key.startswith("legacy:"):
            key = key[len("legacy:"):]
        if not name and key:
            name = key
        if not name:
            continue
        devices.append({
            "name": name,
            "key": key,
            "battery": battery,
            "charging": bool(card.get("charging")),
            "connection": (card.get("connection") or "").strip(),
        })

    _CACHE = devices
    _CACHE_AT = now
    return devices
