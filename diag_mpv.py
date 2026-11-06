"""Finds out why mpv-2.dll will not load. Put this next to mpv-2.dll and run it."""
import ctypes
import os
import platform
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DLL = os.path.join(HERE, "mpv-2.dll")

print("Windows :", platform.platform())
print("Python  :", struct.calcsize("P") * 8, "bit")

if not os.path.exists(DLL):
    sys.exit("mpv-2.dll not found next to this script")
print("DLL size:", os.path.getsize(DLL) // 1048576, "MB")

with open(DLL, "rb") as f:
    head = f.read(4096)
if head[:2] != b"MZ":
    sys.exit("DLL is NOT a valid Windows file (corrupt download?)")
pe_offset = struct.unpack("<I", head[60:64])[0]
machine = struct.unpack("<H", head[pe_offset + 4:pe_offset + 6])[0]
print("DLL type:", {0x8664: "x64 (64-bit)", 0x14C: "x86 (32-bit)", 0xAA64: "ARM64"}.get(machine, hex(machine)))

if os.name == "nt":
    try:
        # same flags python-mpv uses
        ctypes.CDLL(DLL, 0x1000 | 0x100)
        print("LOAD    : OK - the dll loads fine here")
    except Exception as e:
        print("LOAD    : FAILED ->", repr(e))
        print("winerror:", getattr(e, "winerror", None))

try:
    import pefile
except ImportError:
    print()
    print("To list missing dependencies, run:")
    print(r"  venv\Scripts\python.exe -m pip install pefile")
    print("then run this script again.")
    sys.exit()

pe = pefile.PE(DLL, fast_load=True)
pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]])
deps = sorted({entry.dll.decode().lower() for entry in pe.DIRECTORY_ENTRY_IMPORT})
sysdir = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")

missing = []
for d in deps:
    if d.startswith(("api-ms-", "ext-ms-")):  # virtual API-set names, always resolved by Windows
        continue
    if not (os.path.exists(os.path.join(sysdir, d)) or os.path.exists(os.path.join(HERE, d))):
        missing.append(d)

print()
print("Direct dependencies:", len(deps))
print("Dependencies NOT found:", missing if missing else "none")
