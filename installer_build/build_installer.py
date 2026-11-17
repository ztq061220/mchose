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
OUTPUT = RELEASE / "MCHOSE键鼠电量_安装程序_v1.5.7.exe"
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
    RELEASE.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(csc),
        "/nologo",
        "/target:winexe",
        f"/out:{OUTPUT}",
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
    subprocess.run(cmd, check=True, cwd=str(ROOT))
    digest = hashlib.sha256(OUTPUT.read_bytes()).hexdigest()
    print(f"installer: {OUTPUT} ({OUTPUT.stat().st_size / 1024 / 1024:.2f} MiB)")
    print(f"sha256: {digest}")


def main() -> int:
    build_payload()
    compile_installer(find_csc())
    return 0


if __name__ == "__main__":
    sys.exit(main())
