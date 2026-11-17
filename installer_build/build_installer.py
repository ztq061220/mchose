#!/usr/bin/env python3
"""Build the self-contained MCHOSE installer from the PyInstaller output."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist" / "MCHOSEBatteryWidget"
PAYLOAD = ROOT / "installer_build" / "payload.zip"
INSTALLER_CS = ROOT / "installer_build" / "Installer.cs"
RELEASE = ROOT / "release"
OUTPUT = RELEASE / "桌面外设电量_安装程序_v1.6.4.exe"
COMPILE_OUTPUT = RELEASE / "installer_unsigned.exe"
USAGE = ROOT / "使用说明.txt"


def find_csc() -> Path:
    candidates = []
    for env_name in ("WINDIR", "SystemRoot"):
        root = os.environ.get(env_name)
        if root:
            candidates.extend([
                Path(root) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe",
                Path(root) / "Microsoft.NET" / "Framework" / "v4.0.30319" / "csc.exe",
            ])
    for path in candidates:
        if path.is_file():
            return path
    raise SystemExit("找不到 .NET Framework 4.x 的 csc.exe；请在 Windows 上运行。")


def build_payload() -> None:
    if not DIST.is_dir():
        raise SystemExit(f"找不到 {DIST}，请先运行 pyinstaller --noconfirm --clean MCHOSEBatteryWidget.spec")
    if not USAGE.is_file():
        raise SystemExit(f"找不到 {USAGE}")

    # The installer copies the whole folder; include the user guide next to the exe.
    shutil.copy2(USAGE, DIST / USAGE.name)
    PAYLOAD.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(PAYLOAD, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(DIST.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(DIST))
    print(f"payload: {PAYLOAD} ({PAYLOAD.stat().st_size / 1024 / 1024:.2f} MiB)")


def compile_installer(csc: Path) -> None:
    print(f"csc: {csc}")
    RELEASE.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(csc),
        "/nologo",
        "/target:winexe",
        "/codepage:65001",
        f"/out:{COMPILE_OUTPUT}",
        "/r:System.dll",
        "/r:System.Core.dll",
        "/r:System.Windows.Forms.dll",
        "/r:System.Drawing.dll",
        "/r:System.IO.Compression.dll",
        "/r:System.IO.Compression.FileSystem.dll",
        f"/win32icon:{ROOT / 'assets' / 'app.ico'}",
        f"/resource:{PAYLOAD},payload.zip",
        str(INSTALLER_CS),
    ]
    print("running:", subprocess.list2cmdline(cmd))
    result = subprocess.run(
        cmd, cwd=str(ROOT), text=True, errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "csc failed").strip().replace(chr(10), " ")[:1200]
        print(f"::error title=csc failed::{detail}")
        raise SystemExit(result.returncode)
    if COMPILE_OUTPUT != OUTPUT:
        if OUTPUT.exists():
            OUTPUT.unlink()
        COMPILE_OUTPUT.replace(OUTPUT)
    digest = hashlib.sha256(OUTPUT.read_bytes()).hexdigest()
    print(f"installer: {OUTPUT} ({OUTPUT.stat().st_size / 1024 / 1024:.2f} MiB)")
    print(f"sha256: {digest}")


def main() -> int:
    try:
        build_payload()
        compile_installer(find_csc())
    except Exception as exc:
        import traceback
        traceback.print_exc()
        print(f"::error title=installer build::{str(exc)[:1200]}")
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
